"""Settings: what each button and gesture does, plus DPI, the scroll wheel and swipe speeds.

Stored as JSON in ~/.config/mx-master-linux/config.json. Missing keys fall back to DEFAULTS.

Every button has a "click" action and optional "up"/"down"/"left"/"right" actions for
moving the mouse while holding it. An action is a preset name (see actions.PRESETS),
"default" (what the button does without this app), "none", or {"keys": [...]} for a key
chord. The gesture-only action "swipe" acts as a multi-finger touchpad swipe while held,
so workspace switching and the overview follow the hand. Any other action on a direction
runs once, when the movement passes gesture_threshold.
"""
import copy
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mx-master-linux"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Button name -> HID++ control id
BUTTONS = {
    "thumb": 0x00C3,
    "back": 0x0053,
    "forward": 0x0056,
    "middle": 0x0052,
    "wheel-mode": 0x00C4,
}
BUTTON_LABELS = {
    "thumb": "Thumb button",
    "back": "Back button",
    "forward": "Forward button",
    "middle": "Middle click",
    "wheel-mode": "Wheel mode button",
}
DIRECTIONS = ("up", "down", "left", "right")
SWIPE_BASELINE = 1.0  # the speed a real touchpad swipe moves at

DEFAULTS = {
    "dpi": None,  # None leaves the mouse as it is
    "wheel": {"mode": "smartshift", "threshold": 16},  # smartshift | ratchet | free
    "gesture_threshold": 150,  # sensor counts before a hold-and-move runs a one-shot action
    "swipe": {"fingers": 3, "speed": 1.5, "vertical_speed": 2.0},
    "buttons": {
        "thumb": {"click": "overview", "up": "swipe", "down": "swipe", "left": "swipe", "right": "swipe"},
        "back": {"click": "default"},
        "forward": {"click": "default"},
        "middle": {"click": "default"},
        "wheel-mode": {"click": "default"},
    },
}


def _merge(base, over):
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and key != "buttons":
            _merge(base[key], value)
        elif key == "buttons" and isinstance(value, dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def load():
    config = defaults()
    try:
        return _merge(config, json.loads(CONFIG_FILE.read_text()))
    except FileNotFoundError:
        return config


def save(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    tmp.replace(CONFIG_FILE)


def defaults():
    return copy.deepcopy(DEFAULTS)


def mtime():
    try:
        return CONFIG_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


STATE_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "mx-master-linux" / "state.json"


def write_state(state):
    """What the daemon knows about the mouse, for the settings app to show."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None
