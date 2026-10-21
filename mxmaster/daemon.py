"""Background service: applies settings to the mouse and turns diverted buttons into actions.

Runs as the logged-in user (systemd user unit). It waits for the mouse, applies the
config, and applies it again whenever the mouse reconnects or the config file changes.
"""
import logging
import os
import signal
import sys
import time

from . import actions, config, hidpp
from .uinput import VirtualInput, VirtualTouchpad

log = logging.getLogger("mx-masterd")

DIVERT, DIVERT_VALID, RAW_XY, RAW_XY_VALID = 0x01, 0x02, 0x10, 0x20
WHEEL_MODES = {"free": 1, "ratchet": 2, "smartshift": 2}
SEARCH_INTERVAL = 2.0
SWIPE_START = 60  # sensor counts before the virtual fingers go down; less is a click
BATTERY_INTERVAL = 300


class Button:
    """Press/move tracking for one diverted button."""

    def __init__(self, name, settings):
        self.name = name
        self.click = settings.get("click", "default")
        self.directions = {d: settings.get(d) for d in config.DIRECTIONS}
        self.gestures = any(actions.does_something(a) for a in self.directions.values())
        self.swipe = actions.SWIPE in self.directions.values()
        self.held = False
        self.dx = self.dy = 0
        self.done = False  # this hold already became a gesture, so release is not a click

    @property
    def diverted(self):
        return self.gestures or actions.is_set(self.click)

    def direction(self):
        if abs(self.dx) >= abs(self.dy):
            return "right" if self.dx > 0 else "left"
        return "down" if self.dy > 0 else "up"


class Daemon:
    def __init__(self):
        self.config = config.load()
        self.config_mtime = config.mtime()
        self.device = None
        self.buttons = {}
        self.dpi_list = []
        self.state = {"connected": False, "pid": os.getpid()}
        self.battery_at = 0.0
        self.output = VirtualInput()
        self.touchpad = None  # created when a button first needs it
        self.dpi = 1000
        self.running = True

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
                return True
            except (OSError, hidpp.HidppError) as e:
                log.debug("Skipping %s: %s", path, e)
        return False

    def disconnect(self):
        if self.touchpad:
            self.touchpad.end()
        if self.device:
            try:
                self.device.close()
            except OSError:
                pass
            self.device = None
            log.info("Mouse disconnected, waiting for it")
        self.publish(connected=False)

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

        available = self.controls()
        self.buttons = {}
        for name, settings in cfg.get("buttons", {}).items():
            cid = config.BUTTONS.get(name)
            if cid is None or cid not in available:
                continue
            button = Button(name, settings or {})
            flags = DIVERT_VALID | RAW_XY_VALID
            if button.diverted:
                flags |= DIVERT
                if button.gestures:
                    if not available[cid] & 0x01:
                        log.warning("%s cannot report movement; gestures on it are ignored", name)
                        button.gestures = False
                    else:
                        flags |= RAW_XY
                if button.swipe and not self.touchpad:
                    self.touchpad = VirtualTouchpad()
                self.buttons[cid] = button
            dev.request(hidpp.REPROG_CONTROLS_V4, 3, cid.to_bytes(2, "big") + bytes([flags, 0, 0]))
        log.info("Applied settings; diverted: %s", ", ".join(b.name for b in self.buttons.values()) or "none")
        self.publish(
            connected=True,
            name=dev.name(),
            dpi=self.dpi,
            dpi_default=dev.default_dpi() if dpi_range else None,
            dpi_range=dpi_range,
            wheel=bool(dev.feature(hidpp.SMART_SHIFT)),
            buttons=[n for n, cid in config.BUTTONS.items() if cid in available],
            gesture_buttons=[n for n, cid in config.BUTTONS.items() if available.get(cid, 0) & 0x01],
        )
        self.update_battery()

    def publish(self, **changes):
        if not changes.get("connected", True):
            self.state = {"connected": False, "pid": os.getpid()}
        self.state.update(changes)
        try:
            config.write_state(self.state)
        except OSError as e:
            log.warning("Could not write the status file: %s", e)

    def update_battery(self):
        self.battery_at = time.monotonic()
        if self.device.feature(hidpp.BATTERY_STATUS):
            percent, charging = self.device.battery()
            self.publish(battery=percent, charging=charging)

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

    # --- events -----------------------------------------------------------

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
                if not button.gestures and button.click == "default":
                    self.run(button, "default", down=True)  # held as long as the real button
            elif button.held and not down:
                button.held = False
                if self.touchpad and self.touchpad.touching:
                    self.touchpad.end()
                elif not button.gestures and button.click == "default":
                    self.run(button, "default", down=False)
                elif not button.done:
                    self.run(button, button.click)

    def on_move(self, dx, dy):
        for button in self.buttons.values():
            if button.held and button.gestures:
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
        elif keys := actions.keys_for(action):
            self.output.press(keys)
        else:
            log.warning("Unknown action %r on %s %s", action, button.name, direction)

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

    def reload_if_changed(self):
        mtime = config.mtime()
        if mtime == self.config_mtime:
            return
        self.config_mtime = mtime
        try:
            self.config = config.load()
        except ValueError as e:
            log.error("Config file is not valid JSON, keeping the old settings: %s", e)
            return
        log.info("Config changed")
        if self.device:
            self.apply()

    def run_forever(self):
        last_search = 0.0
        self.publish(connected=False)
        while self.running:
            self.reload_if_changed()
            if not self.device:
                if time.monotonic() - last_search >= SEARCH_INTERVAL:
                    last_search = time.monotonic()
                    try:
                        self.connect()
                    except (OSError, hidpp.HidppError) as e:
                        log.warning("Could not set up the mouse: %s", e)
                        self.disconnect()
                if not self.device:
                    time.sleep(0.5)
                continue
            try:
                while self.device.pending_events:
                    self.handle(self.device.pending_events.pop(0))
                data = self.device.read_report(1.0)
                if data:
                    self.handle(data)
                if time.monotonic() - self.battery_at > BATTERY_INTERVAL:
                    self.update_battery()
            except OSError:
                self.disconnect()
            except hidpp.HidppError as e:
                log.warning("Mouse did not answer (%s); reconnecting", e)
                self.disconnect()

    def shutdown(self):
        """Give the buttons back to the mouse so they work normally without us."""
        if self.device:
            for cid in self.buttons:
                try:
                    self.device.request(hidpp.REPROG_CONTROLS_V4, 3, cid.to_bytes(2, "big") + bytes([DIVERT_VALID | RAW_XY_VALID, 0, 0]))
                except (OSError, hidpp.HidppError):
                    pass
            self.device.close()
        self.publish(connected=False, pid=None)
        if self.touchpad:
            self.touchpad.end()
            self.touchpad.close()
        self.output.close()


def main():
    logging.basicConfig(level=logging.DEBUG if "-v" in sys.argv else logging.INFO, format="%(message)s")
    try:
        daemon = Daemon()
    except PermissionError as e:
        sys.exit(f"No access to {e.filename}. Is the udev rule installed? See README.")

    def stop(*_):
        daemon.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        daemon.run_forever()
    finally:
        daemon.shutdown()
        log.info("Stopped; buttons are back to normal")


if __name__ == "__main__":
    main()
