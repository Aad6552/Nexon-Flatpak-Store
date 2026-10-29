"""GtkApplication subclass for Nexon Flatpak Store."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from . import APP_ID, APP_ISSUES, APP_NAME, APP_WEBSITE, __version__
from .window import StoreWindow


class StoreApplication(Adw.Application):
    """The application object: owns the window and the global actions."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        GLib.set_application_name(APP_NAME)
        GLib.set_prgname("nexon-flatpak-store")

    # -- GtkApplication ------------------------------------------------- #
    def do_startup(self) -> None:  # noqa: N802 - GObject virtual method
        Adw.Application.do_startup(self)
        self._add_action("about", self._on_about)
        self._add_action("quit", lambda *_: self.quit())
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.refresh", ["<Control>r"])

    def do_activate(self) -> None:  # noqa: N802 - GObject virtual method
        window = self.props.active_window
        if window is None:
            window = StoreWindow(application=self)
        window.present()

    # -- helpers -------------------------------------------------------- #
    def _add_action(self, name: str, callback: Any) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def _on_about(self, *_args: Any) -> None:
        about = Adw.AboutWindow(
            transient_for=self.props.active_window,
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="Aad6552",
            developers=["Aad6552"],
            website=APP_WEBSITE,
            issue_url=APP_ISSUES,
            license_type=Gtk.License.MIT_X11,
            copyright="© 2026 Aad6552",
            comments=(
                "Browse Flathub, then install, update and remove applications "
                "on any Linux distribution."
            ),
        )
        about.present()
