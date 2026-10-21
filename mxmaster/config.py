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
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "mx-master-linux"
ASSISTANT_DATA = DATA_DIR / "assistant"  # the assistant window's sign-ins and cache

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
    # Actions ring: one action (or null) per slot, clockwise from the top.
    # A button opens it when its click is set to "ring".
    "ring": ["play-pause", "next-track", "volume-up", "brightness-up",
             "screenshot", "lock", "web-search", "previous-track"],
    # Per-app overrides, by desktop file id without ".desktop", e.g.
    # {"org.mozilla.firefox": {"name": "Firefox", "buttons": {"forward": {"up": "copy"}}}}.
    # A slot that is not listed uses the global setting; "none" switches it off for that app.
    "apps": {},
    # A small web window for an AI chat, opened with the "assistant" action. Off unless turned on.
    "assistant": {"enabled": False, "provider": "chatgpt", "url": ""},
}

ASSISTANT_PROVIDERS = {
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "gemini": ("Gemini", "https://gemini.google.com/app"),
    "claude": ("Claude", "https://claude.ai/new"),
    "custom": ("Custom address", ""),
}


def _merge(base, over):
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and key not in ("buttons", "apps"):
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


def button_settings(cfg, name, app=None):
    """A button's settings for the focused app: the global ones with that app's overrides on top."""
    settings = dict(cfg.get("buttons", {}).get(name) or {})
    overrides = ((cfg.get("apps") or {}).get(app) or {}).get("buttons", {}).get(name) or {}
    settings.update({slot: value for slot, value in overrides.items() if value is not None})
    return settings


def assistant_url(cfg):
    assistant = cfg.get("assistant") or {}
    provider = assistant.get("provider", "chatgpt")
    if provider == "custom":
        return assistant.get("url") or ""
    return ASSISTANT_PROVIDERS.get(provider, ASSISTANT_PROVIDERS["chatgpt"])[1]


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
