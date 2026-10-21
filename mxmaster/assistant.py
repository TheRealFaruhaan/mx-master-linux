"""The AI assistant window: ChatGPT, Gemini, Claude or any address in a small web view.

A single-instance app. Running it again shows or hides the window. Closing the window
only hides it, so the page (and the conversation in it) stays as it was until you log out.
Sign-ins and cookies are kept on disk in config.ASSISTANT_DATA.
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
try:
    gi.require_version("WebKit", "6.0")
except ValueError:
    sys.exit("The assistant needs WebKitGTK: sudo apt install gir1.2-webkit-6.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, WebKit  # noqa: E402

from . import config  # noqa: E402

APP_ID = "dev.faruhaan.MxMaster.Assistant"
WIDTH, HEIGHT = 520, 780


class AssistantWindow(Adw.ApplicationWindow):
    def __init__(self, app, url):
        super().__init__(application=app, title="Assistant", default_width=WIDTH, default_height=HEIGHT)
        self.home = url
        data = config.ASSISTANT_DATA
        session = WebKit.NetworkSession.new(str(data / "data"), str(data / "cache"))
        session.get_cookie_manager().set_persistent_storage(str(data / "cookies.sqlite"),
                                                            WebKit.CookiePersistentStorage.SQLITE)
        self.web = WebKit.WebView(network_session=session)
        settings = self.web.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_javascript_can_open_windows_automatically(True)
        self.web.connect("create", self.on_create)
        self.web.connect("decide-policy", self.on_policy)
        self.web.connect("notify::title", lambda w, *_: self.set_title(w.get_title() or "Assistant"))

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        back.connect("clicked", lambda *_: self.web.go_back())
        self.web.bind_property("can-go-back", back, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(back)
        reload = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload")
        reload.connect("clicked", lambda *_: self.web.reload())
        header.pack_start(reload)
        home = Gtk.Button(icon_name="go-home-symbolic", tooltip_text="New chat")
        home.connect("clicked", lambda *_: self.web.load_uri(self.home))
        header.pack_start(home)
        browser = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open in your browser")
        browser.connect("clicked", self.open_in_browser)
        header.pack_end(browser)
        view.add_top_bar(header)
        self.progress = Gtk.ProgressBar(css_classes=["osd"], visible=False)
        self.web.connect("notify::estimated-load-progress", self.on_progress)
        overlay = Gtk.Overlay(child=self.web)
        overlay.add_overlay(self.progress)
        self.progress.set_valign(Gtk.Align.START)
        view.set_content(overlay)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("close-request", self.on_close)
        self.web.load_uri(url)

    def on_progress(self, web, *_):
        value = web.get_estimated_load_progress()
        self.progress.set_fraction(value)
        self.progress.set_visible(value < 1)

    def on_policy(self, _web, decision, kind):
        """Links that leave the service open in the normal browser."""
        if kind != WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if uri and not self.same_site(uri) and not self.sign_in(uri):
            Gio.AppInfo.launch_default_for_uri(uri, None)
            decision.ignore()
            return True
        return False

    def on_create(self, view, _action):
        """Sign-in pop-ups (Google, Apple, Microsoft) need a real child view to report back to the page."""
        popup = WebKit.WebView(related_view=view)
        window = Gtk.Window(transient_for=self, modal=True, default_width=480, default_height=640,
                            title="Sign in", child=popup)
        popup.connect("ready-to-show", lambda *_: window.present())
        popup.connect("close", lambda *_: window.destroy())
        popup.connect("notify::title", lambda w, *_: window.set_title(w.get_title() or "Sign in"))
        return popup

    def same_site(self, uri):
        home = GLib.Uri.parse(self.home, GLib.UriFlags.NONE).get_host() or ""
        host = GLib.Uri.parse(uri, GLib.UriFlags.NONE).get_host() or ""
        base = ".".join(home.split(".")[-2:])
        return host == base or host.endswith("." + base)

    @staticmethod
    def sign_in(uri):
        host = GLib.Uri.parse(uri, GLib.UriFlags.NONE).get_host() or ""
        return any(host.endswith(d) for d in ("accounts.google.com", "appleid.apple.com",
                                              "login.microsoftonline.com", "login.live.com",
                                              "auth.openai.com", "auth0.openai.com"))

    def open_in_browser(self, *_):
        Gio.AppInfo.launch_default_for_uri(self.web.get_uri() or self.home, None)
        self.set_visible(False)

    def on_key(self, _ctrl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.web.reload()
            return True
        return False

    def on_close(self, *_):
        self.set_visible(False)  # keep the page alive until the session ends
        return True


class Assistant(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

    def do_activate(self):
        cfg = config.load()
        if not (cfg.get("assistant") or {}).get("enabled"):
            print("The AI assistant is turned off in MX Master Settings", file=sys.stderr)
            if not self.window:
                self.quit()
            return
        url = config.assistant_url(cfg)
        if not url:
            print("No address set for the assistant", file=sys.stderr)
            return
        if self.window and self.window.home != url:
            self.window.destroy()
            self.window = None
        if not self.window:
            self.hold()  # stay running while the window is hidden
            self.window = AssistantWindow(self, url)
            self.window.present()
        elif self.window.is_visible() and self.window.is_active():
            self.window.set_visible(False)
        else:
            self.window.present()


def main():
    config.ASSISTANT_DATA.mkdir(parents=True, exist_ok=True)
    sys.exit(Assistant().run(sys.argv))


if __name__ == "__main__":
    main()
