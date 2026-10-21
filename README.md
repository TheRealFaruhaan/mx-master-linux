# mx-master-linux

Button remapping and thumb-button gestures for the Logitech MX Master on Linux.
Logitech's own software does not run on Linux, so this fills in the parts people miss most.

Hold the thumb button and move the mouse, and the workspaces slide along with your hand,
the same way a three-finger touchpad swipe does. Swipe up for the overview and the app grid.

<p align="center"><img src="data/dev.faruhaan.MxMaster.svg" width="96" alt=""></p>

## What it does

- **Gestures that follow your hand**: hold a button and move. Workspace switching and the
  overview follow the movement and settle when you let go, like on a touchpad.
- **One-shot gestures**: any direction can run an action instead (a key shortcut, volume, media
  keys, screenshot, DPI cycle, …). It runs once, after you have moved a set distance.
- **Remap buttons**: thumb, back, forward, middle click and the wheel-mode button can each
  run an action on click, plus a different one for each direction.
- **Custom shortcuts**: record any key combination for a button or gesture.
- **Actions ring**: set a button to open a ring of eight actions around the pointer. Hold the
  button, move toward an action and let go, or click once and pick one with the pointer.
- **Per-app settings**: give an app its own button and gesture actions on top of your global ones.
  They switch as you change windows.
- **AI assistant window** (off unless you turn it on): ChatGPT, Gemini, Claude or any address in
  a small window that opens from a button, gesture or the ring. Closing it only hides it, so the
  conversation is still there until you log out. Sign-ins are kept.
- **Pointer and wheel**: sensor DPI, and SmartShift / always ratchet / always free-spin with
  the SmartShift threshold.
- Each setting has its own reset button, and changes apply as soon as you make them.
- Settings are applied again after the mouse sleeps, wakes or reconnects.

## Requirements

- An MX Master that speaks HID++ 2.0 (tested on the **MX Master 2S over Bluetooth**)
- Python 3.10 or newer
- GTK 4 and libadwaita for Python (for the settings app): `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`
  (already present on Ubuntu Desktop)
- Gestures that follow your hand need a desktop with touchpad gestures, such as GNOME on Wayland or KDE Plasma
- The actions ring and per-app settings need GNOME Shell 46 or newer (a small extension is installed with the app)
- For the AI assistant window: `sudo apt install gir1.2-webkit-6.0`

No other libraries are needed; the daemon talks to the mouse and to `/dev/uinput` directly.

## Install

```sh
git clone https://github.com/TheRealFaruhaan/mx-master-linux.git
cd mx-master-linux
./install.sh              # installs for your user, starts the service, adds the app to the app grid
./install.sh --uninstall  # removes it (your settings stay in ~/.config/mx-master-linux)
```

Or download `mx-master-linux-<version>.tar.gz` from the
[latest release](https://github.com/TheRealFaruhaan/mx-master-linux/releases/latest), unpack it,
and run `./install.sh` inside.

Run `install.sh` as your normal user. It asks for `sudo` once to install a udev rule
(`/etc/udev/rules.d/70-mx-master.rules`) that gives the logged-in user access to the mouse
and to `/dev/uinput`. Everything else goes into your home folder:

| | |
| --- | --- |
| `~/.local/share/mx-master-linux` | the program |
| `~/.local/bin/mx-master`, `mx-masterd` | the settings app and the daemon |
| `~/.config/systemd/user/mx-masterd.service` | starts the daemon with your session |
| `~/.local/share/gnome-shell/extensions/mx-master-linux@faruhaan.dev` | the GNOME Shell extension |
| `~/.config/mx-master-linux/config.json` | your settings |
| `~/.local/share/mx-master-linux/assistant` | the assistant window's sign-ins and cache |

On GNOME, log out and back in once after the first install so the shell loads the extension.

## How it works

`mx-masterd` runs as you, as a systemd user service. It talks to the mouse over HID++ 2.0
through hidraw and asks the mouse to send the buttons you have remapped to it instead of to the
system ("diverting"). While a diverted button is held, the mouse reports movement to the daemon
instead of moving the pointer.

- For one-shot actions, the daemon presses keys on a virtual keyboard.
- For gestures that follow your hand, it puts three (or four) fingers down on a virtual
  touchpad and moves them with the mouse. The desktop then runs its own swipe.

The GNOME Shell extension owns `dev.faruhaan.MxMaster.Shell` on the session bus. It draws the
actions ring for the daemon, which picks the item from the mouse movement, and it reports which
app has focus so the daemon can switch to that app's settings.

The settings app only edits the config file. The daemon sees the change and applies it, and
writes the mouse's status (battery, DPI range) to `$XDG_RUNTIME_DIR/mx-master-linux/state.json`
for the app to show. When the daemon stops, it gives every button back to the mouse.

Logs: `journalctl --user -u mx-masterd -f`. Run `mx-masterd -v` in a terminal to see every gesture.

## Limits

- You can't carry a window to another workspace by dragging it and swiping. GNOME ignores both
  touchpad swipes and the workspace shortcuts while a window is being dragged.
- The side thumb wheel on the 2S has no HID++ control, so it keeps scrolling sideways.
- The built-in actions use GNOME's default shortcuts. On other desktops, use custom shortcuts.
- The actions ring and per-app settings are GNOME-only for now.
- Some sign-in pages may refuse to work inside the assistant window; "Open in your browser" is
  there for those.
- Only one mouse at a time, connected over Bluetooth. Unifying and Bolt receivers are not supported yet.

## Developing

Run straight from the folder without installing: `python3 -m mxmaster.daemon -v`, `python3 -m mxmaster`
and `python3 -m mxmaster.assistant`.
`tools/probe.py` lists what a connected mouse supports, and `tools/gesture_test.py` shows the raw
thumb-button movement.

## License

MIT, see [LICENSE](LICENSE). Not affiliated with or endorsed by Logitech. Logitech and MX Master
are trademarks of Logitech.
