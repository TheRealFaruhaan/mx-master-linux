#!/usr/bin/env bash
# Installs mx-master-linux for the current user. Run again to update; --uninstall removes it.
# Only the udev rule needs root, so sudo is asked for once, and only when the rule changed.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
share="${XDG_DATA_HOME:-$HOME/.local/share}"
bin="$HOME/.local/bin"
app="$share/mx-master-linux"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
service="mx-masterd.service"
desktop="$share/applications/dev.faruhaan.MxMaster.desktop"
icon="$share/icons/hicolor/scalable/apps/dev.faruhaan.MxMaster.svg"
rule_src="$here/data/70-mx-master.rules"
rule="/etc/udev/rules.d/70-mx-master.rules"
ext_uuid="mx-master-linux@faruhaan.dev"
ext_dir="$share/gnome-shell/extensions/$ext_uuid"

# Adds or removes the extension in GNOME's list of enabled extensions
gnome_extension() {
    python3 - "$1" "$ext_uuid" <<'PY' 2>/dev/null || true
import sys
from gi.repository import Gio
action, uuid = sys.argv[1:]
schema = Gio.SettingsSchemaSource.get_default().lookup("org.gnome.shell", True)
if schema is None:
    sys.exit()
settings = Gio.Settings.new("org.gnome.shell")
enabled = [u for u in settings.get_strv("enabled-extensions") if u != uuid]
disabled = [u for u in settings.get_strv("disabled-extensions") if u != uuid]
if action == "enable":
    enabled.append(uuid)
settings.set_strv("enabled-extensions", enabled)
settings.set_strv("disabled-extensions", disabled)
Gio.Settings.sync()
PY
}

reload_udev() {
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw --subsystem-match=misc
}

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now "$service" 2>/dev/null || true
    rm -f "$units/$service"
    systemctl --user daemon-reload 2>/dev/null || true
    gdbus call --session --dest dev.faruhaan.MxMaster.Assistant --object-path /dev/faruhaan/MxMaster/Assistant \
        --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1 || true
    gnome_extension disable
    rm -rf "$app" "$ext_dir"
    rm -f "$bin/mx-master" "$bin/mx-masterd" "$desktop" "$icon"
    update-desktop-database "$share/applications" 2>/dev/null || true
    if [[ -e "$rule" ]]; then
        echo "Removing the udev rule (needs sudo): $rule"
        sudo rm -f "$rule" && reload_udev
    fi
    echo "mx-master-linux removed. Your settings stay in ~/.config/mx-master-linux,"
    echo "and the assistant's sign-ins in $share/mx-master-linux (delete them with: rm -r $share/mx-master-linux)."
    exit 0
fi

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user, not with sudo. It asks for sudo itself when needed." >&2
    exit 1
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    echo "mx-master-linux needs Python 3.10 or newer." >&2
    exit 1
}
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null || {
    echo "The settings app needs GTK 4 and libadwaita for Python:" >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
    exit 1
}

if ! cmp -s "$rule_src" "$rule"; then
    echo "Installing the udev rule that lets your user talk to the mouse (needs sudo): $rule"
    sudo install -m644 "$rule_src" "$rule"
    reload_udev
fi

rm -rf "$app"
mkdir -p "$app" "$bin" "$units" "$(dirname "$desktop")" "$(dirname "$icon")"
cp -r "$here/mxmaster" "$app/"
find "$app" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$bin/mx-master" <<LAUNCH
#!/bin/sh
cd "$app" && exec python3 -m mxmaster "\$@"
LAUNCH
cat > "$bin/mx-masterd" <<LAUNCH
#!/bin/sh
cd "$app" && exec python3 -m mxmaster.daemon "\$@"
LAUNCH
chmod +x "$bin/mx-master" "$bin/mx-masterd"

sed "s|@BIN@|$bin|" "$here/data/$service" > "$units/$service"
sed "s|@BIN@|$bin|" "$here/data/dev.faruhaan.MxMaster.desktop" > "$desktop"
cp "$here/data/dev.faruhaan.MxMaster.svg" "$icon"
update-desktop-database "$share/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$share/icons/hicolor" 2>/dev/null || true

new_extension=0
if command -v gnome-shell >/dev/null 2>&1; then
    [[ -d "$ext_dir" ]] || new_extension=1
    rm -rf "$ext_dir"
    mkdir -p "$ext_dir"
    cp "$here/shell-extension/metadata.json" "$here/shell-extension/extension.js" \
       "$here/shell-extension/stylesheet.css" "$ext_dir/"
    gnome_extension enable
fi

systemctl --user daemon-reload
systemctl --user enable "$service" >/dev/null 2>&1
systemctl --user restart "$service"

echo "mx-master-linux installed. The daemon is running; open the settings from the app grid, or run: mx-master"
if (( new_extension )); then
    echo "Log out and back in once to start the GNOME Shell extension (for the actions ring and per-app settings)."
elif command -v gnome-shell >/dev/null 2>&1; then
    echo "The GNOME Shell extension was updated; log out and back in to load the new version."
fi
python3 -c 'import gi; gi.require_version("WebKit", "6.0")' 2>/dev/null ||
    echo "For the optional AI assistant window, also run: sudo apt install gir1.2-webkit-6.0"
