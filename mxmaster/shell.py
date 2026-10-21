"""The daemon's side of the link to the GNOME Shell extension (session bus).

The extension draws the actions ring at the pointer and reports which app has focus.
Everything here is optional: without the extension, the ring and per-app settings
simply do nothing.
"""
import logging

from gi.repository import Gio, GLib

log = logging.getLogger("mx-masterd")

NAME = "dev.faruhaan.MxMaster.Shell"
PATH = "/dev/faruhaan/MxMaster/Shell"
IFACE = "dev.faruhaan.MxMaster.Shell1"


class Shell:
    def __init__(self, on_focus, on_ring_chosen):
        self.on_focus = on_focus
        self.on_ring_chosen = on_ring_chosen
        self.available = False
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.bus.signal_subscribe(NAME, IFACE, None, PATH, None, Gio.DBusSignalFlags.NONE, self._signal)
        Gio.bus_watch_name_on_connection(self.bus, NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._appeared, self._vanished)

    def _appeared(self, *_):
        self.available = True
        log.info("GNOME Shell extension connected")
        self.bus.call(NAME, PATH, IFACE, "GetFocusedApp", None, GLib.VariantType("(s)"),
                      Gio.DBusCallFlags.NONE, 1000, None, self._got_focus)

    def _vanished(self, *_):
        if self.available:
            log.info("GNOME Shell extension went away")
        self.available = False
        self.on_focus("")

    def _got_focus(self, bus, result):
        try:
            self.on_focus(bus.call_finish(result).unpack()[0])
        except GLib.Error as e:
            log.debug("GetFocusedApp failed: %s", e.message)

    def _signal(self, _bus, _sender, _path, _iface, signal, params):
        if signal == "FocusChanged":
            self.on_focus(params.unpack()[0])
        elif signal == "RingChosen":
            self.on_ring_chosen(params.unpack()[0])

    def _call(self, method, params=None):
        if not self.available:
            return False
        self.bus.call(NAME, PATH, IFACE, method, params, None, Gio.DBusCallFlags.NONE, 1000, None, None)
        return True

    def show_ring(self, items):
        """items: (label, icon name) per slot, clockwise from the top; ("", "") for an empty slot."""
        return self._call("ShowRing", GLib.Variant("(a(ss))", (items,)))

    def highlight_ring(self, index):
        self._call("HighlightRing", GLib.Variant("(i)", (index,)))

    def release_ring(self, index):
        """index >= 0 closes the ring on that item; -1 keeps it open to pick with the pointer."""
        self._call("ReleaseRing", GLib.Variant("(i)", (index,)))

    def hide_ring(self):
        self._call("HideRing")
