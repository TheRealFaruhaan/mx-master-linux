// MX Master Linux: the parts of mx-master-linux that only the shell can do on Wayland.
//
// Owns dev.faruhaan.MxMaster.Shell on the session bus for mx-masterd:
//   ShowRing(items)       draw the actions ring around the pointer
//   HighlightRing(index)  the daemon picks the item from the mouse movement
//   ReleaseRing(index)    close on that item, or (-1) stay open to pick with the pointer
//   HideRing()
//   GetFocusedApp() / FocusChanged(app)  the focused app's desktop id, for per-app settings
//   RingChosen(index)     an item picked with the pointer (-1: closed without picking)
// It also keeps the AI assistant window centred and above other windows.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'dev.faruhaan.MxMaster.Shell';
const OBJECT_PATH = '/dev/faruhaan/MxMaster/Shell';
const ASSISTANT_ID = 'dev.faruhaan.MxMaster.Assistant';

const IFACE = `
<node>
  <interface name="dev.faruhaan.MxMaster.Shell1">
    <method name="ShowRing"><arg type="a(ss)" direction="in" name="items"/></method>
    <method name="HighlightRing"><arg type="i" direction="in" name="index"/></method>
    <method name="ReleaseRing"><arg type="i" direction="in" name="index"/></method>
    <method name="HideRing"/>
    <method name="GetFocusedApp"><arg type="s" direction="out" name="app"/></method>
    <signal name="FocusChanged"><arg type="s" name="app"/></signal>
    <signal name="RingChosen"><arg type="i" name="index"/></signal>
  </interface>
</node>`;

const RADIUS = 112;      // centre of the ring to the centre of an item
const SLOT = 56;         // item circle size
const LABEL_GAP = 10;
const EDGE = 190;        // keep the ring and its labels this far inside the screen
const SHOW_MS = 140;
const HIDE_MS = 120;

class Ring {
    constructor(onChosen) {
        this._onChosen = onChosen;
        this._slots = [];
        this._index = -1;
        this._grab = null;
        this._open = false;

        this._holder = new St.Widget({reactive: false, visible: false});
        this._holder.connect('button-release-event', () => {
            this._choose(-1);
            return Clutter.EVENT_STOP;
        });
        this._holder.connect('key-press-event', (_actor, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape)
                this._choose(-1);
            return Clutter.EVENT_STOP;
        });
        this._ring = new St.Widget({style_class: 'mxm-ring'});
        this._holder.add_child(this._ring);
        Main.layoutManager.uiGroup.add_child(this._holder);
    }

    show(items) {
        this._clearSlots();
        this._index = -1;

        const [px, py] = global.get_pointer();
        const monitor = Main.layoutManager.monitors.find(m =>
            px >= m.x && px < m.x + m.width && py >= m.y && py < m.y + m.height) ??
            Main.layoutManager.primaryMonitor;
        this._holder.set_position(monitor.x, monitor.y);
        this._holder.set_size(monitor.width, monitor.height);
        const cx = Math.min(Math.max(px - monitor.x, EDGE), monitor.width - EDGE);
        const cy = Math.min(Math.max(py - monitor.y, EDGE * 0.7), monitor.height - EDGE * 0.7);
        this._ring.set_position(cx, cy);

        // On screen (but transparent) first, so the labels have their style when measured
        Main.layoutManager.uiGroup.set_child_above_sibling(this._holder, null);
        this._holder.remove_all_transitions();
        this._holder.opacity = 0;
        this._holder.show();

        const close = new St.Icon({
            style_class: 'mxm-ring-centre', icon_name: 'window-close-symbolic', icon_size: 14,
        });
        this._ring.add_child(close);
        close.set_position(-14, -14);

        items.forEach(([label, icon], i) => {
            if (!label) {
                this._slots.push(null);
                return;
            }
            const angle = i * 2 * Math.PI / items.length;
            const x = Math.round(RADIUS * Math.sin(angle));
            const y = Math.round(-RADIUS * Math.cos(angle));

            const circle = new St.Button({
                style_class: 'mxm-ring-slot',
                child: new St.Icon({icon_name: icon, icon_size: 24}),
                reactive: false,
                can_focus: false,
            });
            circle.set_size(SLOT, SLOT);
            circle.set_position(x - SLOT / 2, y - SLOT / 2);
            circle.set_pivot_point(0.5, 0.5);
            circle.connect('enter-event', () => this.highlight(i));
            circle.connect('leave-event', () => this.highlight(-1));
            circle.connect('button-release-event', () => {
                this._choose(i);
                return Clutter.EVENT_STOP;
            });

            const text = new St.Label({style_class: 'mxm-ring-label', text: label});
            this._ring.add_child(circle);
            this._ring.add_child(text);
            const [, width] = text.get_preferred_width(-1);
            const [, height] = text.get_preferred_height(-1);
            const side = Math.sin(angle);
            let lx, ly;
            if (side > 0.3) {
                lx = x + SLOT / 2 + LABEL_GAP;
                ly = y - height / 2;
            } else if (side < -0.3) {
                lx = x - SLOT / 2 - LABEL_GAP - width;
                ly = y - height / 2;
            } else {
                lx = x - width / 2;
                ly = Math.cos(angle) > 0 ? y - SLOT / 2 - LABEL_GAP - height : y + SLOT / 2 + LABEL_GAP;
            }
            text.set_position(Math.round(lx), Math.round(ly));
            this._slots.push({circle, text});
        });

        this._ring.set_scale(0.85, 0.85);
        this._holder.ease({opacity: 255, duration: SHOW_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._ring.ease({scale_x: 1, scale_y: 1, duration: SHOW_MS, mode: Clutter.AnimationMode.EASE_OUT_BACK});
        this._open = true;
    }

    highlight(index) {
        if (!this._open || index === this._index)
            return;
        const old = this._slots[this._index];
        if (old) {
            old.circle.remove_style_pseudo_class('highlight');
            old.text.remove_style_pseudo_class('highlight');
            old.circle.ease({scale_x: 1, scale_y: 1, duration: 90, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
        this._index = this._slots[index] ? index : -1;
        const slot = this._slots[this._index];
        if (slot) {
            slot.circle.add_style_pseudo_class('highlight');
            slot.text.add_style_pseudo_class('highlight');
            slot.circle.ease({scale_x: 1.14, scale_y: 1.14, duration: 90, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
    }

    release(index) {
        if (!this._open)
            return;
        if (index >= 0) {
            this.highlight(index);
            this.hide();
            return;
        }
        // Let go without picking: stay open, and pick with the pointer instead
        this._holder.reactive = true;
        for (const slot of this._slots) {
            if (slot)
                slot.circle.reactive = true;
        }
        this._grab = Main.pushModal(this._holder);
        this._holder.grab_key_focus();
    }

    _choose(index) {
        if (!this._open)
            return;
        this.hide();
        this._onChosen(index);
    }

    hide() {
        if (!this._open)
            return;
        this._open = false;
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        this._holder.reactive = false;
        this._holder.ease({
            opacity: 0,
            duration: HIDE_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                if (!this._open) {
                    this._holder.hide();
                    this._clearSlots();
                }
            },
        });
    }

    _clearSlots() {
        this._ring.destroy_all_children();
        this._slots = [];
        this._index = -1;
    }

    destroy() {
        if (this._grab)
            Main.popModal(this._grab);
        this._holder.destroy();
    }
}

class Service {
    constructor() {
        this._ring = new Ring(index => this._emit('RingChosen', new GLib.Variant('(i)', [index])));
        this._tracker = Shell.WindowTracker.get_default();
        this._focusId = this._tracker.connect('notify::focus-app', () =>
            this._emit('FocusChanged', new GLib.Variant('(s)', [this.GetFocusedApp()])));
        this._windowId = global.display.connect('window-created', (_display, window) =>
            this._placeAssistant(window));

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE, null, null);
    }

    _emit(signal, params) {
        this._dbus.emit_signal(signal, params);
    }

    ShowRing(items) {
        this._ring.show(items);
    }

    HighlightRing(index) {
        this._ring.highlight(index);
    }

    ReleaseRing(index) {
        this._ring.release(index);
    }

    HideRing() {
        this._ring.hide();
    }

    GetFocusedApp() {
        const id = this._tracker.focus_app?.get_id() ?? '';
        return id.startsWith('window:') ? '' : id.replace(/\.desktop$/, '');
    }

    _placeAssistant(window) {
        if (window.get_gtk_application_id() !== ASSISTANT_ID)
            return;
        const actor = window.get_compositor_private();
        const place = () => {
            window.make_above();
            const area = window.get_work_area_current_monitor();
            const frame = window.get_frame_rect();
            window.move_frame(true,
                area.x + Math.round((area.width - frame.width) / 2),
                area.y + Math.round((area.height - frame.height) / 2));
        };
        if (actor) {
            const id = actor.connect('first-frame', () => {
                actor.disconnect(id);
                place();
            });
        } else {
            GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                place();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    destroy() {
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._tracker.disconnect(this._focusId);
        global.display.disconnect(this._windowId);
        this._ring.destroy();
    }
}

export default class MxMasterExtension extends Extension {
    enable() {
        this._service = new Service();
    }

    disable() {
        this._service?.destroy();
        this._service = null;
    }
}
