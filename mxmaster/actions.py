"""Named actions a button or gesture can run."""

# Key chords. The workspace and overview ones are GNOME's default shortcuts.
PRESETS = {
    "overview": ("Activities overview", ["KEY_LEFTMETA"]),
    "app-grid": ("Show apps", ["KEY_LEFTMETA", "KEY_A"]),
    "workspace-left": ("Previous workspace", ["KEY_LEFTMETA", "KEY_PAGEUP"]),
    "workspace-right": ("Next workspace", ["KEY_LEFTMETA", "KEY_PAGEDOWN"]),
    "move-window-left": ("Move window to previous workspace", ["KEY_LEFTMETA", "KEY_LEFTSHIFT", "KEY_PAGEUP"]),
    "move-window-right": ("Move window to next workspace", ["KEY_LEFTMETA", "KEY_LEFTSHIFT", "KEY_PAGEDOWN"]),
    "switch-windows": ("Switch windows", ["KEY_LEFTALT", "KEY_TAB"]),
    "close-window": ("Close window", ["KEY_LEFTALT", "KEY_F4"]),
    "maximize": ("Maximize window", ["KEY_LEFTMETA", "KEY_UP"]),
    "minimize": ("Hide window", ["KEY_LEFTMETA", "KEY_H"]),
    "browser-back": ("Back", ["KEY_LEFTALT", "KEY_LEFT"]),
    "browser-forward": ("Forward", ["KEY_LEFTALT", "KEY_RIGHT"]),
    "copy": ("Copy", ["KEY_LEFTCTRL", "KEY_C"]),
    "paste": ("Paste", ["KEY_LEFTCTRL", "KEY_V"]),
    "undo": ("Undo", ["KEY_LEFTCTRL", "KEY_Z"]),
    "brightness-up": ("Brightness up", ["KEY_BRIGHTNESSUP"]),
    "brightness-down": ("Brightness down", ["KEY_BRIGHTNESSDOWN"]),
    "volume-up": ("Volume up", ["KEY_VOLUMEUP"]),
    "volume-down": ("Volume down", ["KEY_VOLUMEDOWN"]),
    "mute": ("Mute", ["KEY_MUTE"]),
    "play-pause": ("Play / pause", ["KEY_PLAYPAUSE"]),
    "next-track": ("Next track", ["KEY_NEXTSONG"]),
    "previous-track": ("Previous track", ["KEY_PREVIOUSSONG"]),
    "screenshot": ("Screenshot", ["KEY_SYSRQ"]),
    "lock": ("Lock screen", ["KEY_LEFTMETA", "KEY_L"]),
}

# What a button does on its own, replayed when we divert it but its click is "default"
NATIVE = {
    "back": ["BTN_SIDE"],
    "forward": ["BTN_EXTRA"],
    "middle": ["BTN_MIDDLE"],
}

# Actions the daemon handles itself rather than as key chords
SPECIAL = {
    "smartshift-toggle": "Toggle free-spin / ratchet",
    "dpi-cycle": "Cycle DPI",
    "web-search": "Web search",
    "assistant": "AI assistant",
}

# Only for a button's click: opens the actions ring
RING = "ring"
RING_LABEL = "Actions ring"
RING_SLOTS = 8  # clockwise from the top
RING_POSITIONS = ("Top", "Top right", "Right", "Bottom right", "Bottom", "Bottom left", "Left", "Top left")

WEB_SEARCH_URL = "https://www.google.com/"

# Symbolic icons for the actions ring
ICONS = {
    "overview": "view-app-grid-symbolic",
    "app-grid": "view-app-grid-symbolic",
    "workspace-left": "go-previous-symbolic",
    "workspace-right": "go-next-symbolic",
    "move-window-left": "go-first-symbolic",
    "move-window-right": "go-last-symbolic",
    "switch-windows": "focus-windows-symbolic",
    "close-window": "window-close-symbolic",
    "maximize": "window-maximize-symbolic",
    "minimize": "window-minimize-symbolic",
    "browser-back": "go-previous-symbolic",
    "browser-forward": "go-next-symbolic",
    "copy": "edit-copy-symbolic",
    "paste": "edit-paste-symbolic",
    "undo": "edit-undo-symbolic",
    "brightness-up": "display-brightness-symbolic",
    "brightness-down": "display-brightness-symbolic",
    "volume-up": "audio-volume-high-symbolic",
    "volume-down": "audio-volume-low-symbolic",
    "mute": "audio-volume-muted-symbolic",
    "play-pause": "media-playback-start-symbolic",
    "next-track": "media-skip-forward-symbolic",
    "previous-track": "media-skip-backward-symbolic",
    "screenshot": "camera-photo-symbolic",
    "lock": "system-lock-screen-symbolic",
    "smartshift-toggle": "input-mouse-symbolic",
    "dpi-cycle": "input-mouse-symbolic",
    "web-search": "web-browser-symbolic",
    "assistant": "starred-symbolic",
}


def icon(action):
    if isinstance(action, dict):
        return "input-keyboard-symbolic"
    return ICONS.get(action, "applications-system-symbolic")

# Gesture-only: drive the desktop's own touchpad swipe, which follows the movement
SWIPE = "swipe"
SWIPE_LABELS = {
    "left": "Workspaces, following hand",
    "right": "Workspaces, following hand",
    "up": "Overview, following hand",
    "down": "Leave overview, following hand",
}


def is_set(action):
    return action not in (None, "default")


def does_something(action):
    return action not in (None, "default", "none")


def keys_for(action):
    if isinstance(action, dict):
        return action.get("keys") or None
    if action in PRESETS:
        return PRESETS[action][1]
    return None


def label(action, direction=None):
    if action == RING:
        return RING_LABEL
    if action == SWIPE:
        return SWIPE_LABELS.get(direction, "Touchpad swipe (follows hand)")
    if action in (None, "default"):
        return "Default"
    if action == "none":
        return "Do nothing"
    if isinstance(action, dict):
        return key_label(action.get("keys", []))
    if action in PRESETS:
        return PRESETS[action][0]
    return SPECIAL.get(action, action)


KEY_NAMES = {
    "LEFTCTRL": "Ctrl", "RIGHTCTRL": "Ctrl", "LEFTSHIFT": "Shift", "RIGHTSHIFT": "Shift",
    "LEFTALT": "Alt", "RIGHTALT": "AltGr", "LEFTMETA": "Super", "RIGHTMETA": "Super",
    "PAGEUP": "Page Up", "PAGEDOWN": "Page Down", "SYSRQ": "Print", "ESC": "Esc",
    "BACKSPACE": "Backspace", "CAPSLOCK": "Caps Lock", "VOLUMEUP": "Volume Up",
    "VOLUMEDOWN": "Volume Down", "PLAYPAUSE": "Play/Pause", "NEXTSONG": "Next Track",
    "PREVIOUSSONG": "Previous Track",
}


def key_label(keys):
    names = []
    for key in keys:
        name = key.removeprefix("KEY_").removeprefix("BTN_")
        names.append(KEY_NAMES.get(name, name if len(name) <= 3 else name.title()))
    return " + ".join(names)
