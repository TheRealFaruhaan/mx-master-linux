"""The settings app. It edits the config file and shows what the daemon reports; the
daemon notices the file change and applies it, so every change takes effect at once."""
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, actions, config  # noqa: E402
from .keycodes import CODES  # noqa: E402

APP_ID = "dev.faruhaan.MxMaster"
WEBSITE = "https://github.com/TheRealFaruhaan/mx-master-linux"
SERVICE = "mx-masterd.service"
SAVE_DELAY_MS = 300

BUTTON_HINTS = {
    "thumb": "The wide button under your thumb",
    "back": "Lower side button",
    "forward": "Upper side button",
    "middle": "Pressing the scroll wheel",
    "wheel-mode": "The small button behind the scroll wheel",
}
DIRECTION_LABELS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}
WHEEL_MODES = [("smartshift", "SmartShift", "Ratchet, and spins freely when you flick it"),
               ("ratchet", "Always ratchet", "Clicks with every step"),
               ("free", "Always free-spin", "Spins without resistance")]
CUSTOM = "custom"
SOURCE_ICONS = Path(__file__).resolve().parent.parent / "data"  # when run from the source folder

MODIFIER_KEYS = [(Gdk.ModifierType.CONTROL_MASK, "KEY_LEFTCTRL"), (Gdk.ModifierType.SHIFT_MASK, "KEY_LEFTSHIFT"),
                 (Gdk.ModifierType.ALT_MASK, "KEY_LEFTALT"), (Gdk.ModifierType.SUPER_MASK, "KEY_LEFTMETA")]
CODE_NAMES = {}
for _name, _code in CODES.items():
    if _name.startswith("KEY_"):
        CODE_NAMES.setdefault(_code, _name)
MODIFIER_CODES = {CODES[n] for n in ("KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT",
                                     "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA")}


def action_choices(direction=None):
    """(value, label) pairs for an action dropdown. Directions get the swipe; clicks get Default."""
    if direction:
        choices = [(None, "Nothing"), (actions.SWIPE, actions.label(actions.SWIPE, direction))]
    else:
        choices = [("default", "Default"), ("none", "Do nothing")]
    choices += [(key, label) for key, (label, _) in actions.PRESETS.items()]
    choices += list(actions.SPECIAL.items())
    return choices


class ShortcutDialog(Adw.Dialog):
    """Records one key chord, e.g. Ctrl+Shift+T."""

    def __init__(self, on_done):
        super().__init__(title="Custom Shortcut", content_width=380)
        self.on_done = on_done
        self.keys = None
        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.set_button = Gtk.Button(label="Set", sensitive=False, css_classes=["suggested-action"])
        self.set_button.connect("clicked", self.on_set)
        header.pack_start(cancel)
        header.pack_end(self.set_button)
        view.add_top_bar(header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=24, margin_bottom=30, margin_start=24, margin_end=24)
        body.append(Gtk.Image(icon_name="input-keyboard-symbolic", pixel_size=64, css_classes=["dim-label"]))
        body.append(Gtk.Label(label="Press the keys you want this to send", css_classes=["title-4"]))
        self.preview = Gtk.Label(label="Waiting for keys…", css_classes=["dim-label", "title-2"])
        body.append(self.preview)
        view.set_content(body)
        self.set_child(view)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("closed", lambda *_: self.on_done(self.keys if self.done else None))
        self.done = False

    def on_key(self, _ctrl, _keyval, keycode, state):
        code = keycode - 8  # XKB keycodes are evdev codes + 8
        name = CODE_NAMES.get(code)
        if not name:
            return True
        keys = [k for mask, k in MODIFIER_KEYS if state & mask]
        if code in MODIFIER_CODES:
            if name not in keys:
                keys.append(name)
            self.preview.set_label(actions.key_label(keys) + " + …")
            return True
        keys.append(name)
        self.keys = keys
        self.preview.set_label(actions.key_label(keys))
        self.preview.remove_css_class("dim-label")
        self.set_button.set_sensitive(True)
        return True

    def on_set(self, *_):
        self.done = True
        self.close()


def add_reset(row, is_default, reset):
    """A small undo button on the row; greyed out while the setting is at its default.
    Returns a function to call after the value changes."""
    button = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Reset to default",
                        valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])

    def update(*_):
        changed = not is_default()
        button.set_sensitive(changed)
        button.set_tooltip_text("Reset to default" if changed else "Already the default")

    def clicked(*_):
        reset()
        update()

    button.connect("clicked", clicked)
    row.add_suffix(button)
    update()
    return update


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(a - b) < 1e-6
    return a == b


class ActionRow(Adw.ComboRow):
    """A dropdown of actions for one button click or direction."""

    def __init__(self, window, title, value, on_change, default, direction=None):
        super().__init__(title=title, enable_search=True, use_subtitle=True)
        self.window = window
        self.on_change = on_change
        self.default = default
        self.choices = action_choices(direction)
        self.custom = None  # the chord shown as an extra entry, if any
        self.updating = False
        self.set_value(value)
        self.connect("notify::selected", self.on_selected)
        self.update_reset = add_reset(self, lambda: self.value == self.default, self.reset)

    def reset(self):
        self.set_value(self.default)
        self.on_change(self.default)

    def changed(self, value):
        self.value = value
        self.on_change(value)
        self.update_reset()

    def rebuild(self):
        entries = list(self.choices)
        if self.custom:
            entries.append(({"keys": self.custom}, actions.key_label(self.custom)))
        entries.append((CUSTOM, "Custom shortcut…"))
        self.entries = entries
        self.updating = True
        self.set_model(Gtk.StringList.new([label for _, label in entries]))
        self.updating = False

    def set_value(self, value):
        self.value = value
        if isinstance(value, dict):
            self.custom = value.get("keys")
        self.rebuild()
        index = next((i for i, (v, _) in enumerate(self.entries) if v == value), 0)
        self.updating = True
        self.set_selected(index)
        self.updating = False

    def on_selected(self, *_):
        if self.updating:
            return
        value = self.entries[self.get_selected()][0]
        if value == CUSTOM:
            dialog = ShortcutDialog(self.on_shortcut)
            dialog.present(self.window)
            return
        self.changed(value)

    def on_shortcut(self, keys):
        if keys:
            value = {"keys": keys}
            self.set_value(value)
            self.changed(value)
        else:
            self.set_value(self.value)  # cancelled: back to what it was


def scale_row(title, subtitle, low, high, step, value, on_change, fmt, default, marks=()):
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
    scale.set_value(value)
    scale.set_hexpand(True)
    scale.set_size_request(260, -1)
    scale.set_valign(Gtk.Align.CENTER)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.LEFT)
    scale.set_format_value_func(lambda _s, v: fmt(v))
    for at, label in marks:
        scale.add_mark(at, Gtk.PositionType.BOTTOM, label)
    snapped = lambda: round(round(scale.get_value() / step) * step, 6)  # noqa: E731
    row.add_suffix(scale)
    update = add_reset(row, lambda: same(snapped(), default), lambda: scale.set_value(default))

    def changed(*_):
        on_change(snapped())
        update()

    scale.connect("value-changed", changed)
    row.scale = scale
    return row


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MX Master Settings", default_width=760, default_height=820)
        self.set_size_request(360, 480)
        self.config = config.load()
        self.state = None
        self.save_source = 0

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.RAISED)
        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        menu = Gio.Menu()
        menu.append("About MX Master Settings", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main Menu", primary=True))
        view.add_top_bar(header)
        self.banner = Adw.Banner(title="The background service isn't running, so changes won't take effect",
                                 button_label="Start")
        self.banner.connect("button-clicked", self.start_service)
        view.add_top_bar(self.banner)
        bottom = Adw.ViewSwitcherBar(stack=self.stack)
        view.add_bottom_bar(bottom)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        breakpoint.add_setter(switcher, "visible", False)
        breakpoint.add_setter(bottom, "reveal", True)
        self.add_breakpoint(breakpoint)

        self.toasts.set_child(view)
        view.set_content(self.stack)
        self.set_content(self.toasts)
        self.build_pages()
        self.refresh_state()
        GLib.timeout_add_seconds(2, self.refresh_state)
        self.connect("map", lambda *_: GLib.idle_add(lambda: self.set_focus(None)))

    # --- pages ---------------------------------------------------------------

    def build_pages(self):
        for name in ("buttons", "gestures", "pointer"):
            child = self.stack.get_child_by_name(name)
            if child:
                self.stack.remove(child)
        self.stack.add_titled_with_icon(self.buttons_page(), "buttons", "Buttons", "input-mouse-symbolic")
        self.stack.add_titled_with_icon(self.gestures_page(), "gestures", "Gestures", "input-touchpad-symbolic")
        self.stack.add_titled_with_icon(self.pointer_page(), "pointer", "Pointer", "find-location-symbolic")

    def device_group(self):
        group = Adw.PreferencesGroup()
        self.device_row = Adw.ActionRow(title="Looking for the mouse…", subtitle=" ")
        self.device_icon = Gtk.Image(icon_name=APP_ID, pixel_size=48)
        self.device_row.add_prefix(self.device_icon)
        self.battery_label = Gtk.Label(css_classes=["dim-label", "numeric"])
        self.battery_icon = Gtk.Image()
        self.device_row.add_suffix(self.battery_label)
        self.device_row.add_suffix(self.battery_icon)
        group.add(self.device_row)
        return group

    def buttons_page(self):
        page = Adw.PreferencesPage()
        page.add(self.device_group())
        state = self.state or {}
        available = state.get("buttons") or list(config.BUTTONS)
        movable = state.get("gesture_buttons") or list(config.BUTTONS)
        for name in config.BUTTONS:
            if name not in available:
                continue
            settings = self.config["buttons"].setdefault(name, {"click": "default"})
            defaults = config.DEFAULTS["buttons"].get(name, {})
            group = Adw.PreferencesGroup(title=config.BUTTON_LABELS[name], description=BUTTON_HINTS.get(name))
            group.add(ActionRow(self, "Click", settings.get("click", "default"),
                                lambda v, n=name: self.set_button(n, "click", v),
                                defaults.get("click", "default")))
            if name in movable:
                expander = Adw.ExpanderRow(title="Hold and move",
                                           subtitle=self.gesture_summary(settings),
                                           expanded=name == "thumb")
                for direction in config.DIRECTIONS:
                    expander.add_row(ActionRow(self, DIRECTION_LABELS[direction], settings.get(direction),
                                               lambda v, n=name, d=direction, e=expander: self.set_button(n, d, v, e),
                                               defaults.get(direction), direction))
                group.add(expander)
            page.add(group)
        return page

    def gesture_summary(self, settings):
        swipes = sum(settings.get(d) == actions.SWIPE for d in config.DIRECTIONS)
        shots = sum(actions.does_something(settings.get(d)) and settings.get(d) != actions.SWIPE
                    for d in config.DIRECTIONS)
        parts = []
        if swipes:
            parts.append(f"{swipes} follow your hand")
        if shots:
            parts.append(f"{shots} run an action")
        return ", ".join(parts) or "Off"

    def gestures_page(self):
        page = Adw.PreferencesPage()
        swipe = self.config["swipe"]
        defaults = config.DEFAULTS["swipe"]
        group = Adw.PreferencesGroup(
            title="Following gestures",
            description="Directions set to “following hand” drive the desktop's own touchpad swipe, "
                        "so workspaces and the overview move with the mouse. "
                        "1.0× is the speed of a real touchpad swipe.")
        baseline = config.SWIPE_BASELINE
        speed_fmt = lambda v: f"{v:.1f}×"  # noqa: E731
        for key, title in (("speed", "Left and right speed"), ("vertical_speed", "Up and down speed")):
            group.add(scale_row(title, f"Default {defaults[key]:.1f}×", 0.5, 4.0, 0.1, swipe.get(key, defaults[key]),
                                lambda v, k=key: self.set_value(("swipe", k), v), speed_fmt, defaults[key],
                                marks=[(baseline, "Touchpad")]))
        fingers = Adw.ComboRow(title="Fingers", subtitle="GNOME uses three fingers; KDE Plasma uses four",
                               model=Gtk.StringList.new(["Three", "Four"]))
        fingers.set_selected(1 if swipe.get("fingers", 3) == 4 else 0)
        fingers_value = lambda: 4 if fingers.get_selected() else 3  # noqa: E731
        update_fingers = add_reset(fingers, lambda: fingers_value() == defaults["fingers"],
                                   lambda: fingers.set_selected(1 if defaults["fingers"] == 4 else 0))
        fingers.connect("notify::selected",
                        lambda *_: (self.set_value(("swipe", "fingers"), fingers_value()), update_fingers()))
        group.add(fingers)
        page.add(group)

        group = Adw.PreferencesGroup(
            title="One-shot gestures",
            description="Any other action on a direction runs once, when you have moved far enough.")
        dpi = (self.state or {}).get("dpi") or 1000
        group.add(scale_row("Distance", "How far to move before the action runs", 50, 600, 10,
                            self.config.get("gesture_threshold", 150),
                            lambda v: self.set_value(("gesture_threshold",), int(v)),
                            lambda v: f"{v / dpi * 25.4:.0f} mm", config.DEFAULTS["gesture_threshold"]))
        page.add(group)

        group = Adw.PreferencesGroup()
        reset = Adw.ButtonRow(title="Reset Gesture Settings")
        reset.connect("activated", self.reset_gestures)
        group.add(reset)
        page.add(group)
        return page

    def pointer_page(self):
        page = Adw.PreferencesPage()
        state = self.state or {}
        group = Adw.PreferencesGroup(title="Pointer")
        dpi_range = state.get("dpi_range")
        low, high, step = dpi_range if isinstance(dpi_range, list) and len(dpi_range) == 3 else (200, 4000, 50)
        dpi = self.config.get("dpi") or state.get("dpi") or 1000
        row = Adw.SpinRow.new_with_range(low, high, step)
        row.set_title("Sensor DPI")
        row.set_subtitle("Higher moves the pointer further for the same hand movement")
        row.set_value(dpi)
        default_dpi = state.get("dpi_default") or 1000
        update_dpi = add_reset(row, lambda: int(row.get_value()) == default_dpi, lambda: row.set_value(default_dpi))
        row.connect("notify::value", lambda r, *_: (self.set_value(("dpi",), int(r.get_value())), update_dpi()))
        group.add(row)
        page.add(group)

        if state.get("wheel", True):
            wheel = self.config["wheel"]
            group = Adw.PreferencesGroup(title="Scroll wheel")
            mode = Adw.ComboRow(title="Wheel mode", model=Gtk.StringList.new([m[1] for m in WHEEL_MODES]))
            keys = [m[0] for m in WHEEL_MODES]
            mode.set_selected(keys.index(wheel.get("mode", "smartshift")) if wheel.get("mode") in keys else 0)
            mode.set_subtitle(WHEEL_MODES[mode.get_selected()][2])
            wheel_defaults = config.DEFAULTS["wheel"]
            threshold = scale_row("SmartShift threshold", "How hard a flick switches to free-spin",
                                  1, 50, 1, wheel.get("threshold", 16),
                                  lambda v: self.set_value(("wheel", "threshold"), int(v)), lambda v: f"{v:.0f}",
                                  wheel_defaults["threshold"])
            threshold.set_visible(mode.get_selected() == 0)
            update_mode = add_reset(mode, lambda: keys[mode.get_selected()] == wheel_defaults["mode"],
                               lambda: mode.set_selected(keys.index(wheel_defaults["mode"])))

            def on_mode(row, *_):
                row.set_subtitle(WHEEL_MODES[row.get_selected()][2])
                threshold.set_visible(row.get_selected() == 0)
                self.set_value(("wheel", "mode"), keys[row.get_selected()])
                update_mode()

            mode.connect("notify::selected", on_mode)
            group.add(mode)
            group.add(threshold)
            page.add(group)
        return page

    # --- changes -------------------------------------------------------------

    def set_button(self, name, slot, value, expander=None):
        settings = self.config["buttons"].setdefault(name, {})
        if value is None:
            settings.pop(slot, None)
        else:
            settings[slot] = value
        if expander:
            expander.set_subtitle(self.gesture_summary(settings))
        self.save_soon()

    def set_value(self, path, value):
        target = self.config
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = value
        self.save_soon()

    def reset_gestures(self, *_):
        defaults = config.defaults()
        self.config["swipe"] = defaults["swipe"]
        self.config["gesture_threshold"] = defaults["gesture_threshold"]
        self.save_soon()
        page = self.stack.get_visible_child_name()
        self.build_pages()
        self.stack.set_visible_child_name(page)
        self.update_device()
        self.toasts.add_toast(Adw.Toast(title="Gesture settings reset"))

    def save_soon(self):
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(SAVE_DELAY_MS, self.save)

    def save(self):
        self.save_source = 0
        try:
            config.save(self.config)
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=f"Could not save: {e.strerror}"))
        return GLib.SOURCE_REMOVE

    # --- daemon state ----------------------------------------------------------

    def daemon_running(self, state):
        pid = (state or {}).get("pid")
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def refresh_state(self):
        state = config.read_state()
        running = self.daemon_running(state)
        self.banner.set_revealed(not running)
        if not running:
            state = None
        first = self.state is None and state and state.get("connected")
        self.state = state
        if first:
            # Now we know which buttons this mouse has and its DPI range
            page = self.stack.get_visible_child_name()
            self.build_pages()
            self.stack.set_visible_child_name(page)
        self.update_device()
        return GLib.SOURCE_CONTINUE

    def update_device(self):
        state = self.state or {}
        if state.get("connected"):
            self.device_row.set_title(state.get("name", "Mouse"))
            self.device_row.set_subtitle("Connected")
            self.device_icon.set_opacity(1)
        else:
            self.device_row.set_title("Mouse not connected" if self.state else "Service not running")
            self.device_row.set_subtitle("Settings are saved and apply when it connects")
            self.device_icon.set_opacity(0.4)
        battery = state.get("battery") if state.get("connected") else None
        self.battery_label.set_visible(battery is not None)
        self.battery_icon.set_visible(battery is not None)
        if battery is not None:
            level = min(100, max(0, round(battery / 10) * 10))
            charging = "-charging" if state.get("charging") else ""
            self.battery_icon.set_from_icon_name(f"battery-level-{level}{charging}-symbolic")
            self.battery_label.set_label(f"{battery}%")

    def start_service(self, *_):
        try:
            proc = Gio.Subprocess.new(["systemctl", "--user", "start", SERVICE],
                                      Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error:
            self.toasts.add_toast(Adw.Toast(title="Could not run systemctl"))
            return

        def done(p, result):
            _, _, err = p.communicate_utf8_finish(result)
            if p.get_successful():
                self.toasts.add_toast(Adw.Toast(title="Service started"))
                GLib.timeout_add(800, lambda: self.refresh_state() and False)
            else:
                self.toasts.add_toast(Adw.Toast(title="The service isn't installed. Run ./install.sh", timeout=6))

        proc.communicate_utf8_async(None, None, done)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        if SOURCE_ICONS.is_dir():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(SOURCE_ICONS))
        Gtk.Window.set_default_icon_name(APP_ID)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self.show_about)
        self.add_action(about)

    def show_about(self, *_):
        Adw.AboutDialog(
            application_name="MX Master Settings",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Hussain Faruhaan",
            comments="Button remapping and touchpad-style gestures for Logitech MX Master mice.\n"
                     "Not affiliated with Logitech.",
            license_type=Gtk.License.MIT_X11,
            website=WEBSITE,
            issue_url=WEBSITE + "/issues",
        ).present(self.get_active_window())

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()


def main():
    sys.exit(App().run(sys.argv))
