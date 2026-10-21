"""Background service: applies settings to the mouse and turns diverted buttons into actions.

Runs as the logged-in user (systemd user unit). It waits for the mouse, applies the
config, and applies it again whenever the mouse reconnects, the config file changes, or
(with the GNOME Shell extension) the focused app changes to one with its own settings.
"""
import logging
import math
import os
import signal
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from . import actions, config, hidpp
from .shell import Shell
from .uinput import VirtualInput, VirtualTouchpad

log = logging.getLogger("mx-masterd")

DIVERT, DIVERT_VALID, RAW_XY, RAW_XY_VALID = 0x01, 0x02, 0x10, 0x20
WHEEL_MODES = {"free": 1, "ratchet": 2, "smartshift": 2}
SEARCH_INTERVAL = 2
CONFIG_CHECK_MS = 500
SWIPE_START = 60  # sensor counts before the virtual fingers go down; less is a click
RING_DEADZONE = 90  # sensor counts from the centre before a ring item is picked
BATTERY_INTERVAL = 300
PACKAGE_DIR = Path(__file__).resolve().parent.parent


class Button:
    """Press/move tracking for one diverted button."""

    def __init__(self, name, settings):
        self.name = name
        self.click = settings.get("click", "default")
        self.ring = self.click == actions.RING
        self.directions = {d: settings.get(d) for d in config.DIRECTIONS}
        self.gestures = not self.ring and any(actions.does_something(a) for a in self.directions.values())
        self.swipe = self.gestures and actions.SWIPE in self.directions.values()
        self.held = False
        self.dx = self.dy = 0
        self.done = False  # this hold already became a gesture, so release is not a click

    @property
    def diverted(self):
        return self.gestures or actions.is_set(self.click)

    @property
    def needs_movement(self):
        return self.gestures or self.ring

    def direction(self):
        if abs(self.dx) >= abs(self.dy):
            return "right" if self.dx > 0 else "left"
        return "down" if self.dy > 0 else "up"


class Daemon:
    def __init__(self):
        self.config = config.load()
        self.config_mtime = config.mtime()
        self.device = None
        self.device_watch = 0
        self.available = {}  # control id -> flags2, for the connected mouse
        self.buttons = {}
        self.dpi_list = []
        self.dpi = 1000
        self.state = {"connected": False, "pid": os.getpid()}
        self.output = VirtualInput()
        self.touchpad = None  # created when a button first needs it
        self.focused_app = ""
        self.ring_button = None  # the button holding the ring open
        self.ring_items = []
        self.ring_index = -1
        self.loop = GLib.MainLoop()
        self.shell = Shell(self.on_focus, self.on_ring_chosen)

    # --- device lifecycle -------------------------------------------------

    def connect(self):
        for path, name in hidpp.find_devices():
            try:
                dev = hidpp.Device(path)
                dev.protocol()
                if not dev.feature(hidpp.REPROG_CONTROLS_V4):
                    dev.close()
                    continue
                dev.feature(hidpp.WIRELESS_STATUS)  # so reconnect events can be recognised
                self.device = dev
                log.info("Connected to %s (%s)", dev.name(), path)
                self.apply()
                self.device_watch = GLib.io_add_watch(dev.fd, GLib.PRIORITY_HIGH,
                                                      GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_readable)
                return True
            except (OSError, hidpp.HidppError) as e:
                log.debug("Skipping %s: %s", path, e)
                self.device = None
        return False

    def disconnect(self):
        self.end_gestures()
        if self.device_watch:
            GLib.source_remove(self.device_watch)
            self.device_watch = 0
        if self.device:
            try:
                self.device.close()
            except OSError:
                pass
            self.device = None
            log.info("Mouse disconnected, waiting for it")
        self.publish(connected=False)

    def end_gestures(self):
        if self.touchpad:
            self.touchpad.end()
        if self.ring_button:
            self.ring_button = None
            self.shell.hide_ring()

    def apply(self):
        """Push the config to the mouse. Diverts do not survive a reconnect, so this runs again then."""
        dev, cfg = self.device, self.config
        dpi_range = None
        if dev.feature(hidpp.ADJUSTABLE_DPI):
            dpi_range = dev.dpi_range()
            if cfg.get("dpi"):
                dev.set_dpi(int(cfg["dpi"]))
            self.dpi = dev.dpi() or 1000
            self.dpi_list = self.cycle_steps(dpi_range)
        wheel = cfg.get("wheel") or {}
        if wheel.get("mode") in WHEEL_MODES and dev.feature(hidpp.SMART_SHIFT):
            threshold = 0xFF if wheel["mode"] == "ratchet" else max(1, min(254, int(wheel.get("threshold", 16))))
            dev.request(hidpp.SMART_SHIFT, 1, (WHEEL_MODES[wheel["mode"]], threshold, 0))
        self.available = self.controls()
        self.apply_buttons()
        self.publish(
            connected=True,
            name=dev.name(),
            dpi=self.dpi,
            dpi_default=dev.default_dpi() if dpi_range else None,
            dpi_range=dpi_range,
            wheel=bool(dev.feature(hidpp.SMART_SHIFT)),
            buttons=[n for n, cid in config.BUTTONS.items() if cid in self.available],
            gesture_buttons=[n for n, cid in config.BUTTONS.items() if self.available.get(cid, 0) & 0x01],
        )
        self.update_battery()

    def apply_buttons(self):
        """Divert the buttons that do something for the focused app, and give the rest back."""
        self.end_gestures()
        app = self.focused_app if self.focused_app in (self.config.get("apps") or {}) else None
        self.buttons = {}
        for name, cid in config.BUTTONS.items():
            if cid not in self.available:
                continue
            button = Button(name, config.button_settings(self.config, name, app))
            flags = DIVERT_VALID | RAW_XY_VALID
            if button.diverted:
                flags |= DIVERT
                if button.needs_movement:
                    if not self.available[cid] & 0x01:
                        log.warning("%s cannot report movement; its gestures are ignored", name)
                        button.gestures = button.swipe = button.ring = False
                    else:
                        flags |= RAW_XY
                if button.swipe and not self.touchpad:
                    self.touchpad = VirtualTouchpad()
                self.buttons[cid] = button
            self.device.request(hidpp.REPROG_CONTROLS_V4, 3, cid.to_bytes(2, "big") + bytes([flags, 0, 0]))
        log.info("Applied settings%s; diverted: %s", f" for {app}" if app else "",
                 ", ".join(b.name for b in self.buttons.values()) or "none")

    def controls(self):
        """Control id -> flags2 (bit 0: can report raw movement) for divertable controls."""
        found = {}
        count = self.device.request(hidpp.REPROG_CONTROLS_V4, 0)[0]
        for i in range(count):
            r = self.device.request(hidpp.REPROG_CONTROLS_V4, 1, (i,))
            if r[4] & 0x20:
                found[int.from_bytes(r[0:2], "big")] = r[8]
        return found

    @staticmethod
    def cycle_steps(dpi_range):
        """The DPI values "Cycle DPI" steps through."""
        if not dpi_range:
            return []
        if isinstance(dpi_range, tuple):
            low, high, step = dpi_range
            return [d for d in (400, 800, 1000, 1600, 2400, 3200, 4000) if low <= d <= high and (d - low) % step == 0]
        return list(dpi_range)

    def publish(self, **changes):
        if not changes.get("connected", True):
            self.state = {"connected": False, "pid": os.getpid()}
        self.state.update(changes)
        self.state["shell"] = self.shell.available
        self.state["focused_app"] = self.focused_app
        try:
            config.write_state(self.state)
        except OSError as e:
            log.warning("Could not write the status file: %s", e)

    def update_battery(self):
        if self.device and self.device.feature(hidpp.BATTERY_STATUS):
            percent, charging = self.device.battery()
            self.publish(battery=percent, charging=charging)

    # --- events -----------------------------------------------------------

    def on_readable(self, _fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self.disconnect()
            return GLib.SOURCE_REMOVE
        try:
            while self.device:
                while self.device.pending_events:
                    self.handle(self.device.pending_events.pop(0))
                data = self.device.read_report(0)
                if data is None:
                    break
                if data:
                    self.handle(data)
        except OSError:
            self.disconnect()
            return GLib.SOURCE_REMOVE
        except hidpp.HidppError as e:
            log.warning("Mouse did not answer (%s); reconnecting", e)
            self.disconnect()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE if self.device else GLib.SOURCE_REMOVE

    def handle(self, data):
        dev = self.device
        if data[3] & 0x0F:  # a reply to someone else's request (the settings app)
            return
        feature, function = data[2], data[3] >> 4
        if feature == dev.features.get(hidpp.REPROG_CONTROLS_V4):
            if function == 0:
                pressed = {int.from_bytes(data[i:i + 2], "big") for i in range(4, 12, 2)} - {0}
                self.on_buttons(pressed)
            elif function == 1:
                dx = int.from_bytes(data[4:6], "big", signed=True)
                dy = int.from_bytes(data[6:8], "big", signed=True)
                self.on_move(dx, dy)
        elif feature == dev.features.get(hidpp.BATTERY_STATUS) and function == 0:
            self.publish(battery=data[4], charging=data[6] in (1, 2, 3))
        elif feature == dev.features.get(hidpp.WIRELESS_STATUS) and function == 0 and data[4] == 1:
            log.info("Mouse woke up or reconnected; applying settings again")
            self.apply()

    def on_buttons(self, pressed):
        for cid, button in self.buttons.items():
            down = cid in pressed
            if down and not button.held:
                button.held, button.dx, button.dy, button.done = True, 0, 0, False
                if button.ring:
                    self.open_ring(button)
                elif not button.gestures and button.click == "default":
                    self.run(button, "default", down=True)  # held as long as the real button
            elif button.held and not down:
                button.held = False
                if button.ring:
                    self.release_ring(button)
                elif self.touchpad and self.touchpad.touching:
                    self.touchpad.end()
                elif not button.gestures and button.click == "default":
                    self.run(button, "default", down=False)
                elif not button.done:
                    self.run(button, button.click)

    def on_move(self, dx, dy):
        for button in self.buttons.values():
            if not button.held:
                continue
            if button.ring:
                self.move_ring(button, dx, dy)
            elif button.gestures:
                self.on_gesture_move(button, dx, dy)

    def on_gesture_move(self, button, dx, dy):
        """Swipe directions follow the hand; other actions run once past the threshold."""
        mm = 25.4 / self.dpi
        swipe = self.config.get("swipe") or {}
        speed = float(swipe.get("speed", config.SWIPE_BASELINE))
        sx, sy = mm * speed, mm * float(swipe.get("vertical_speed", speed))
        if self.touchpad and self.touchpad.touching:
            self.touchpad.move(dx * sx, dy * sy)
            return
        if button.done:
            return
        button.dx += dx
        button.dy += dy
        distance = max(abs(button.dx), abs(button.dy))
        direction = button.direction()
        action = button.directions.get(direction)
        if action == actions.SWIPE and distance >= SWIPE_START:
            button.done = True
            self.touchpad.begin(int(swipe.get("fingers", 3)))
            # Moving the whole distance at once also keeps it from looking like a tap
            self.touchpad.move(button.dx * sx, button.dy * sy)
        elif action != actions.SWIPE and distance >= self.config.get("gesture_threshold", 150):
            button.done = True
            self.run(button, action, direction)

    # --- actions ring -----------------------------------------------------

    def open_ring(self, button):
        items = list(self.config.get("ring") or [])[:actions.RING_SLOTS]
        items += [None] * (actions.RING_SLOTS - len(items))
        self.ring_items = items
        self.ring_index = -1
        shown = [(actions.label(a), actions.icon(a)) if actions.does_something(a) else ("", "") for a in items]
        if self.shell.show_ring(shown):
            self.ring_button = button
        else:
            log.warning("The actions ring needs the GNOME Shell extension, which is not running")

    def move_ring(self, button, dx, dy):
        if self.ring_button is not button:
            return
        button.dx += dx
        button.dy += dy
        index = -1
        if math.hypot(button.dx, button.dy) >= RING_DEADZONE:
            # 0 is straight up, counting clockwise; screen y grows downwards
            angle = math.degrees(math.atan2(button.dx, -button.dy)) % 360
            index = round(angle / (360 / actions.RING_SLOTS)) % actions.RING_SLOTS
            if not actions.does_something(self.ring_items[index]):
                index = -1
        if index != self.ring_index:
            self.ring_index = index
            self.shell.highlight_ring(index)

    def release_ring(self, button):
        if self.ring_button is not button:
            return
        index = self.ring_index
        self.shell.release_ring(index)
        if index >= 0:
            self.ring_button = None
            self.run(button, self.ring_items[index], f"ring {actions.RING_POSITIONS[index].lower()}")
        # Otherwise the ring stays open and the extension reports a pointer click

    def on_ring_chosen(self, index):
        button, self.ring_button = self.ring_button, None
        if button and 0 <= index < len(self.ring_items):
            self.run(button, self.ring_items[index], f"ring {actions.RING_POSITIONS[index].lower()}")

    # --- per-app settings ---------------------------------------------------

    def on_focus(self, app):
        app = app.removesuffix(".desktop")
        apps = self.config.get("apps") or {}
        before = self.focused_app if self.focused_app in apps else None
        self.focused_app = app
        after = app if app in apps else None
        if self.device and before != after:
            try:
                self.apply_buttons()
            except (OSError, hidpp.HidppError) as e:
                log.warning("Could not switch settings for %s: %s", app, e)
        self.publish()

    # --- running actions ------------------------------------------------------

    def run(self, button, action, direction="click", down=None):
        if action == "default":
            if down is not False:
                log.debug("%s %s -> default", button.name, direction)
            native = actions.NATIVE.get(button.name)
            if native and down is not None:
                self.output.hold(native, down)
            elif native:
                self.output.press(native)
            return
        if action in (None, "none"):
            return
        log.debug("%s %s -> %s", button.name, direction, actions.label(action, direction))
        if action == "smartshift-toggle":
            self.toggle_smartshift()
        elif action == "dpi-cycle":
            self.cycle_dpi()
        elif action == "web-search":
            self.open_uri(actions.WEB_SEARCH_URL)
        elif action == "assistant":
            self.toggle_assistant()
        elif keys := actions.keys_for(action):
            self.output.press(keys)
        else:
            log.warning("Unknown action %r on %s %s", action, button.name, direction)

    def open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as e:
            log.warning("Could not open %s: %s", uri, e.message)

    def toggle_assistant(self):
        if not (self.config.get("assistant") or {}).get("enabled"):
            log.info("The AI assistant is turned off in the settings")
            return
        # The assistant is a single-instance app: running it again shows or hides its window
        subprocess.Popen([sys.executable, "-m", "mxmaster.assistant"], cwd=PACKAGE_DIR,
                         stdin=subprocess.DEVNULL, start_new_session=True)

    def toggle_smartshift(self):
        if self.device.feature(hidpp.SMART_SHIFT):
            mode = self.device.request(hidpp.SMART_SHIFT, 0)[0]
            self.device.request(hidpp.SMART_SHIFT, 1, (1 if mode == 2 else 2, 0, 0))

    def cycle_dpi(self):
        if not self.dpi_list:
            return
        dpi = next((d for d in self.dpi_list if d > self.device.dpi()), self.dpi_list[0])
        self.device.set_dpi(dpi)
        self.dpi = dpi
        self.publish(dpi=dpi)
        log.info("DPI %d", dpi)

    # --- main loop --------------------------------------------------------

    def check_config(self):
        mtime = config.mtime()
        if mtime != self.config_mtime:
            self.config_mtime = mtime
            try:
                self.config = config.load()
            except ValueError as e:
                log.error("Config file is not valid JSON, keeping the old settings: %s", e)
                return GLib.SOURCE_CONTINUE
            log.info("Config changed")
            if self.device:
                try:
                    self.apply()
                except (OSError, hidpp.HidppError) as e:
                    log.warning("Could not apply the new settings: %s", e)
                    self.disconnect()
        return GLib.SOURCE_CONTINUE

    def search(self):
        if not self.device:
            try:
                self.connect()
            except (OSError, hidpp.HidppError) as e:
                log.warning("Could not set up the mouse: %s", e)
                self.disconnect()
        return GLib.SOURCE_CONTINUE

    def battery_tick(self):
        try:
            self.update_battery()
        except (OSError, hidpp.HidppError):
            pass
        return GLib.SOURCE_CONTINUE

    def run_forever(self):
        self.publish(connected=False)
        self.search()
        GLib.timeout_add_seconds(SEARCH_INTERVAL, self.search)
        GLib.timeout_add(CONFIG_CHECK_MS, self.check_config)
        GLib.timeout_add_seconds(BATTERY_INTERVAL, self.battery_tick)
        self.loop.run()

    def stop(self):
        self.loop.quit()

    def shutdown(self):
        """Give the buttons back to the mouse so they work normally without us."""
        self.end_gestures()
        if self.device:
            for cid in self.buttons:
                try:
                    self.device.request(hidpp.REPROG_CONTROLS_V4, 3, cid.to_bytes(2, "big") + bytes([DIVERT_VALID | RAW_XY_VALID, 0, 0]))
                except (OSError, hidpp.HidppError):
                    pass
            self.device.close()
        self.publish(connected=False, pid=None)
        if self.touchpad:
            self.touchpad.close()
        self.output.close()


def main():
    logging.basicConfig(level=logging.DEBUG if "-v" in sys.argv else logging.INFO, format="%(message)s")
    try:
        daemon = Daemon()
    except PermissionError as e:
        sys.exit(f"No access to {e.filename}. Is the udev rule installed? See README.")

    try:
        from gi.repository import GLibUnix
        add_signal = GLibUnix.signal_add
    except ImportError:  # GLib older than 2.80
        add_signal = GLib.unix_signal_add
    for signum in (signal.SIGTERM, signal.SIGINT):
        add_signal(GLib.PRIORITY_HIGH, signum, lambda: daemon.stop() or GLib.SOURCE_REMOVE)
    try:
        daemon.run_forever()
    finally:
        daemon.shutdown()
        log.info("Stopped; buttons are back to normal")


if __name__ == "__main__":
    main()
