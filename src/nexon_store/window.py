"""Main window of Nexon Flatpak Store.

GTK4 + libadwaita.  All network and flatpak work happens on worker threads and
is handed back to the GTK main loop through ``GLib.idle_add``.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import APP_NAME, api, flatpak, paths

#: Browse selectors: ("collection"|"category", API value, label)
DISCOVER_OPTIONS: tuple[tuple[str, str, str], ...] = tuple(
    ("collection", value, label) for value, label in api.SECTIONS
) + tuple(("category", value, label) for value, label in api.CATEGORIES)


def clear_container(container: Any) -> None:
    """Remove every child of a Gtk.Box."""
    child = container.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        container.remove(child)
        child = next_child


class StoreWindow(Adw.ApplicationWindow):
    """The single window of the application."""

    def __init__(self, application: Any) -> None:
        super().__init__(application=application, title=APP_NAME)
        self.set_default_size(1160, 800)

        self.installed: dict[str, flatpak.Installation] = {}
        self.update_index: dict[str, dict[str, str]] = {}
        self.discover_state: dict[str, Any] = {
            "kind": "collection",
            "value": api.SECTIONS[0][0],
            "page": 0,
            "query": "",
        }
        self.detail_app_id: str | None = None
        self._detail_info: dict[str, Any] | None = None
        self._busy_count = 0
        self._operation_running = False
        self._search_timeout = 0

        self._install_actions()
        self._build_ui()
        self.refresh_installed()
        self._reload_discover(reset=True)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(root)
        self.set_content(self.toast_overlay)

        header = Adw.HeaderBar()

        self.back_button = Gtk.Button(
            icon_name="go-previous-symbolic", tooltip_text="Back", visible=False
        )
        self.back_button.add_css_class("flat")
        self.back_button.connect("clicked", self._on_back)
        header.pack_start(self.back_button)

        self.main_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True
        )
        self.main_stack.add_titled(self._build_discover_page(), "discover", "Discover")
        self.main_stack.add_titled(self._build_installed_page(), "installed", "Installed")
        self.main_stack.add_titled(self._build_updates_page(), "updates", "Updates")

        self.switcher = Gtk.StackSwitcher(stack=self.main_stack)
        header.set_title_widget(self.switcher)

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh_button.add_css_class("flat")
        refresh_button.connect("clicked", lambda *_: self.refresh_all())
        header.pack_end(refresh_button)
        header.pack_end(self._build_menu_button())

        self.root_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True
        )
        self.root_stack.add_named(self.main_stack, "main")
        self.root_stack.add_named(self._build_detail_page(), "detail")

        root.append(header)
        root.append(self.root_stack)
        root.append(self._build_status_bar())

    def _build_menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Refresh everything", "win.refresh")
        menu.append("Refresh Flathub metadata", "win.refresh-metadata")
        menu.append("Update all applications", "win.update-all")
        menu.append(f"About {APP_NAME}", "app.about")
        menu.append("Quit", "app.quit")
        button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"
        )
        button.add_css_class("flat")
        return button

    def _build_status_bar(self) -> Gtk.Revealer:
        self.status_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP, reveal_child=False
        )
        bar = Gtk.Box(
            spacing=10, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12
        )
        self.status_spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(
            label="", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END
        )
        self.status_label.add_css_class("dim-label")
        bar.append(self.status_spinner)
        bar.append(self.status_label)
        self.status_revealer.set_child(bar)
        return self.status_revealer

    def _build_discover_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        toolbar = Gtk.Box(
            spacing=12, margin_top=12, margin_bottom=6, margin_start=18, margin_end=18
        )
        self.search_entry = Gtk.SearchEntry(
            hexpand=True, placeholder_text="Search Flathub"
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.section_dropdown = Gtk.DropDown.new_from_strings(
            [label for _kind, _value, label in DISCOVER_OPTIONS]
        )
        self.section_dropdown.set_tooltip_text("Browse a Flathub collection or category")
        self.section_dropdown.connect("notify::selected", self._on_section_changed)
        toolbar.append(self.search_entry)
        toolbar.append(self.section_dropdown)
        page.append(toolbar)

        self.discover_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_start=12,
            margin_end=12,
            margin_bottom=18,
        )
        self.discover_flow = Gtk.FlowBox()
        self.discover_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.discover_flow.set_homogeneous(True)
        self.discover_flow.set_valign(Gtk.Align.START)
        self.discover_flow.set_min_children_per_line(2)
        self.discover_flow.set_max_children_per_line(6)
        self.discover_flow.set_row_spacing(6)
        self.discover_flow.set_column_spacing(6)
        content.append(self.discover_flow)

        self.load_more_button = Gtk.Button(
            label="Load more", halign=Gtk.Align.CENTER, margin_top=12, visible=False
        )
        self.load_more_button.connect("clicked", lambda *_: self._reload_discover(reset=False))
        content.append(self.load_more_button)

        scroller = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=content
        )
        self.discover_stack.add_named(scroller, "content")

        self.discover_spinner = Gtk.Spinner()
        spinner_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
        )
        spinner_box.append(self.discover_spinner)
        loading_label = Gtk.Label(label="Loading Flathub…")
        loading_label.add_css_class("dim-label")
        spinner_box.append(loading_label)
        self.discover_stack.add_named(spinner_box, "loading")

        self.discover_message = Gtk.Label(
            label="", wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50
        )
        retry_button = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
        retry_button.connect("clicked", lambda *_: self._reload_discover(reset=True))
        message_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
        )
        message_icon = Gtk.Image.new_from_icon_name("system-search-symbolic")
        message_icon.set_pixel_size(64)
        message_icon.add_css_class("dim-label")
        message_box.append(message_icon)
        message_box.append(self.discover_message)
        message_box.append(retry_button)
        self.discover_stack.add_named(message_box, "message")

        page.append(self.discover_stack)
        return page

    def _build_installed_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        heading = Gtk.Box(
            spacing=12, margin_top=12, margin_bottom=6, margin_start=18, margin_end=18
        )
        title = Gtk.Label(label="Installed applications", xalign=0, hexpand=True)
        title.add_css_class("heading")
        self.installed_count = Gtk.Label(label="", xalign=1)
        self.installed_count.add_css_class("dim-label")
        heading.append(title)
        heading.append(self.installed_count)
        page.append(heading)

        self.installed_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True
        )
        self.installed_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.installed_list.add_css_class("boxed-list")
        self.installed_list.set_valign(Gtk.Align.START)
        inner = Gtk.Box(margin_start=18, margin_end=18, margin_bottom=18)
        inner.append(self.installed_list)
        self.installed_stack.add_named(
            Gtk.ScrolledWindow(
                vexpand=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                child=Adw.Clamp(maximum_size=900, child=inner),
            ),
            "content",
        )

        self.installed_spinner = Gtk.Spinner()
        self.installed_stack.add_named(
            self._centered_box(self.installed_spinner, "Reading installed applications…"),
            "loading",
        )

        self.installed_message = Gtk.Label(
            label="", wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50
        )
        self.installed_stack.add_named(
            self._centered_box(None, "", self.installed_message), "message"
        )
        page.append(self.installed_stack)
        return page

    def _build_updates_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        heading = Gtk.Box(
            spacing=12, margin_top=12, margin_bottom=6, margin_start=18, margin_end=18
        )
        title = Gtk.Label(label="Updates", xalign=0, hexpand=True)
        title.add_css_class("heading")
        self.update_all_button = Gtk.Button(label="Update all", sensitive=False)
        self.update_all_button.add_css_class("suggested-action")
        self.update_all_button.connect("clicked", lambda *_: self._update_all())
        heading.append(title)
        heading.append(self.update_all_button)
        page.append(heading)

        self.updates_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True
        )
        self.updates_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.updates_list.add_css_class("boxed-list")
        self.updates_list.set_valign(Gtk.Align.START)
        inner = Gtk.Box(margin_start=18, margin_end=18, margin_bottom=18)
        inner.append(self.updates_list)
        self.updates_stack.add_named(
            Gtk.ScrolledWindow(
                vexpand=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                child=Adw.Clamp(maximum_size=900, child=inner),
            ),
            "content",
        )

        self.updates_spinner = Gtk.Spinner()
        self.updates_stack.add_named(
            self._centered_box(self.updates_spinner, "Checking for updates…"), "loading"
        )

        self.updates_message = Gtk.Label(
            label="", wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50
        )
        self.updates_stack.add_named(
            self._centered_box(None, "", self.updates_message), "message"
        )
        page.append(self.updates_stack)
        return page

    def _build_detail_page(self) -> Gtk.Widget:
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp = Adw.Clamp(maximum_size=960, child=self.detail_box)
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=18,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        outer.append(clamp)
        return Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=outer
        )

    @staticmethod
    def _centered_box(
        spinner: Gtk.Spinner | None, label_text: str, label: Gtk.Label | None = None
    ) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
        )
        if spinner is not None:
            box.append(spinner)
        if label is None:
            label = Gtk.Label(label=label_text, wrap=True, max_width_chars=50)
            label.add_css_class("dim-label")
        box.append(label)
        return box

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _install_actions(self) -> None:
        actions: tuple[tuple[str, Callable[..., None]], ...] = (
            ("refresh", lambda *_: self.refresh_all()),
            ("refresh-metadata", lambda *_: self.refresh_metadata()),
            ("update-all", lambda *_: self._update_all()),
            ("search", lambda *_: self._focus_search()),
        )
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _focus_search(self) -> None:
        self.root_stack.set_visible_child_name("main")
        self.back_button.set_visible(False)
        self.main_stack.set_visible_child_name("discover")
        self.search_entry.grab_focus()

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    def _show_error(self, heading: str, error: BaseException) -> None:
        detail = ""
        if isinstance(error, flatpak.FlatpakError) and error.output:
            detail = flatpak.summarize(error.output)
        body = str(error).strip() or "An unexpected error occurred."
        if detail and detail.strip() != body:
            body = f"{body}\n\n{detail}"
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present()

    def _confirm(self, heading: str, body: str, label: str, callback: Callable[[], None]) -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("confirm", label)
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog: Adw.MessageDialog, response: str) -> None:
            if response == "confirm":
                callback()

        dialog.connect("response", on_response)
        dialog.present()

    # ------------------------------------------------------------------ #
    # Background work plumbing
    # ------------------------------------------------------------------ #
    def _start_busy(self, text: str, show_status: bool = True) -> None:
        self._busy_count += 1
        if show_status:
            self.status_label.set_text(text)
            self.status_spinner.start()
            self.status_revealer.set_reveal_child(True)

    def _stop_busy(self) -> None:
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.status_spinner.stop()
            self.status_revealer.set_reveal_child(False)
            self.status_label.set_text("")

    def _run_task(
        self,
        description: str,
        work: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        show_status: bool = True,
    ) -> None:
        self._start_busy(description, show_status=show_status)

        def runner() -> None:
            try:
                result = work()
            except BaseException as error:  # noqa: BLE001 - reported to the user
                GLib.idle_add(self._task_failed, description, error, on_error)
            else:
                GLib.idle_add(self._task_succeeded, result, on_success)

        threading.Thread(target=runner, daemon=True).start()

    def _task_succeeded(
        self, result: Any, on_success: Callable[[Any], None] | None
    ) -> bool:
        self._stop_busy()
        if on_success is not None:
            try:
                on_success(result)
            except Exception as error:  # noqa: BLE001 - a UI bug must not blank the window
                self._show_error("Something went wrong while showing this page", error)
        return GLib.SOURCE_REMOVE

    def _task_failed(
        self,
        description: str,
        error: BaseException,
        on_error: Callable[[BaseException], None] | None,
    ) -> bool:
        self._stop_busy()
        if on_error is not None:
            try:
                on_error(error)
            except Exception:  # noqa: BLE001 - report the original error instead
                self._show_error(description, error)
        else:
            self._show_error(description, error)
        return GLib.SOURCE_REMOVE

    def _run_operation(
        self,
        description: str,
        work: Callable[[], Any],
        success_message: str | None = None,
        on_success: Callable[[Any], None] | None = None,
    ) -> None:
        if self._operation_running:
            self._toast("Another operation is already in progress.")
            return
        self._operation_running = True

        def finished(result: Any) -> None:
            self._operation_running = False
            if success_message:
                self._toast(success_message)
            if on_success is not None:
                on_success(result)
            self.refresh_installed()

        def failed(error: BaseException) -> None:
            self._operation_running = False
            self._show_error(description, error)

        self._run_task(
            description, work, on_success=finished, on_error=failed, show_status=True
        )

    # ------------------------------------------------------------------ #
    # Images and icons
    # ------------------------------------------------------------------ #
    def _cache_icon(self, app_id: str, url: str | None) -> str | None:
        """Download an icon into the cache (worker thread) and return its path."""
        if not url or not app_id:
            return None
        path = paths.icon_cache_path(app_id)
        try:
            if path.exists() and path.stat().st_size > 0:
                return str(path)
            api.download(url, path)
        except (api.FlathubError, OSError):
            return None
        return str(path)

    def _icon_image(self, path: str | None, size: int) -> Gtk.Image:
        if path and os.path.exists(path):
            image = Gtk.Image.new_from_file(path)
            image.set_pixel_size(size)
            return image
        image = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        image.set_pixel_size(size)
        image.add_css_class("dim-label")
        return image

    #: Largest size a screenshot is drawn at on the detail page.
    SCREENSHOT_MAX_WIDTH = 760
    SCREENSHOT_MAX_HEIGHT = 420

    @classmethod
    def _fit_size(cls, width: int, height: int) -> tuple[int, int]:
        """Scale ``width`` x ``height`` down so it fits the screenshot viewport."""
        if width <= 0 or height <= 0:
            return (0, 0)
        scale = min(cls.SCREENSHOT_MAX_WIDTH / width, cls.SCREENSHOT_MAX_HEIGHT / height)
        return (max(1, round(width * scale)), max(1, round(height * scale)))

    @classmethod
    def _screenshot_picture(cls, path: str) -> Gtk.Widget | None:
        """Draw one downloaded screenshot at a readable size with rounded corners."""
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error:
            return None
        target_width, target_height = cls._fit_size(
            pixbuf.get_width(), pixbuf.get_height()
        )
        if not target_width or not target_height:
            return None
        frame = Gtk.Box(overflow=Gtk.Overflow.HIDDEN)
        frame.add_css_class("card")
        frame.set_size_request(target_width, target_height)
        frame.append(
            Gtk.Picture(
                paintable=Gdk.Texture.new_for_pixbuf(pixbuf),
                content_fit=Gtk.ContentFit.CONTAIN,
                can_shrink=True,
            )
        )
        return frame

    # ------------------------------------------------------------------ #
    # Installed / updates state
    # ------------------------------------------------------------------ #
    def refresh_all(self) -> None:
        self.refresh_installed()
        self._reload_discover(reset=True)

    def refresh_metadata(self) -> None:
        self._run_operation(
            "Refreshing Flathub metadata…",
            lambda: flatpak.refresh_appstream(),
            success_message="Flathub metadata refreshed.",
        )

    def refresh_installed(self) -> None:
        self.installed_spinner.start()
        self.updates_spinner.start()
        if not self.installed:
            self.installed_stack.set_visible_child_name("loading")
            self.updates_stack.set_visible_child_name("loading")

        def work() -> tuple[dict[str, flatpak.Installation], dict[str, dict[str, str]]]:
            installed = flatpak.list_installed()
            try:
                pending = flatpak.updates()
            except flatpak.FlatpakError:
                pending = {}
            for app_id in installed:
                self._cache_icon(app_id, api.appstream_icon_url(app_id))
            return installed, pending

        self._run_task(
            "Reading installed applications…",
            work,
            on_success=self._apply_installed_state,
            on_error=self._installed_failed,
            show_status=False,
        )

    def _apply_installed_state(
        self, state: tuple[dict[str, flatpak.Installation], dict[str, dict[str, str]]]
    ) -> None:
        self.installed_spinner.stop()
        self.updates_spinner.stop()
        self.installed, self.update_index = state
        self._render_installed()
        self._render_updates()
        if (
            self._detail_info is not None
            and self.root_stack.get_visible_child_name() == "detail"
        ):
            self._render_detail(self._detail_info)

    def _installed_failed(self, error: BaseException) -> None:
        self.installed_spinner.stop()
        self.updates_spinner.stop()
        message = str(error) or "Could not read the installed applications."
        self.installed_message.set_text(message)
        self.updates_message.set_text(message)
        self.installed_stack.set_visible_child_name("message")
        self.updates_stack.set_visible_child_name("message")
        self.installed_count.set_text("")
        self.update_all_button.set_sensitive(False)

    def _render_installed(self) -> None:
        clear_container(self.installed_list)
        if not self.installed:
            self.installed_count.set_text("")
            self.installed_message.set_text(
                "No applications are installed yet. Open the Discover tab to find something."
            )
            self.installed_stack.set_visible_child_name("message")
            return

        self.installed_stack.set_visible_child_name("content")
        self.installed_count.set_text(
            f"{len(self.installed)} application{'s' if len(self.installed) != 1 else ''}"
        )

        for app_id in sorted(
            self.installed, key=lambda key: self.installed[key].name.casefold()
        ):
            record = self.installed[app_id]
            subtitle = f"{app_id} · {record.version or 'unknown version'}"
            if record.installation:
                subtitle += f" · {record.installation}"
            row = Adw.ActionRow(title=record.name, subtitle=subtitle)
            row.set_activatable(False)
            row.add_prefix(self._icon_image(self._cached_icon_path(app_id), 32))

            if app_id in self.update_index:
                update_button = Gtk.Button(label="Update", valign=Gtk.Align.CENTER)
                update_button.connect(
                    "clicked", lambda _button, app=app_id: self._update_app(app)
                )
                row.add_suffix(update_button)

            remove_button = Gtk.Button(
                icon_name="user-trash-symbolic",
                tooltip_text=f"Remove {record.name}",
                valign=Gtk.Align.CENTER,
            )
            remove_button.add_css_class("flat")
            remove_button.connect(
                "clicked", lambda _button, app=app_id: self._confirm_uninstall(app)
            )
            row.add_suffix(remove_button)
            self.installed_list.append(row)

    def _render_updates(self) -> None:
        clear_container(self.updates_list)
        if not self.update_index:
            self.update_all_button.set_sensitive(False)
            self.updates_message.set_text(
                "Everything is up to date. New updates will appear here."
            )
            self.updates_stack.set_visible_child_name("message")
            return

        self.updates_stack.set_visible_child_name("content")
        self.update_all_button.set_sensitive(True)

        for app_id in sorted(self.update_index):
            info = self.update_index[app_id]
            record = self.installed.get(app_id)
            name = record.name if record else app_id
            subtitle = app_id
            if info.get("version"):
                subtitle += f" · new version {info['version']}"
            if record and record.version:
                subtitle += f" (installed {record.version})"

            row = Adw.ActionRow(title=name, subtitle=subtitle)
            row.set_activatable(False)
            icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
            icon.set_pixel_size(24)
            row.add_prefix(icon)

            update_button = Gtk.Button(label="Update", valign=Gtk.Align.CENTER)
            update_button.add_css_class("suggested-action")
            update_button.connect(
                "clicked", lambda _button, app=app_id: self._update_app(app)
            )
            row.add_suffix(update_button)
            self.updates_list.append(row)

    @staticmethod
    def _cached_icon_path(app_id: str) -> str | None:
        path = paths.icon_cache_path(app_id)
        return str(path) if path.exists() and path.stat().st_size > 0 else None

    # ------------------------------------------------------------------ #
    # Browse
    # ------------------------------------------------------------------ #
    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self._search_timeout:
            GLib.source_remove(self._search_timeout)
        self._search_timeout = GLib.timeout_add(400, self._apply_search)

    def _apply_search(self) -> bool:
        self._search_timeout = 0
        query = self.search_entry.get_text().strip()
        if not query:
            index = max(self.section_dropdown.get_selected(), 0)
            kind, value, _label = DISCOVER_OPTIONS[index]
            self.discover_state = {
                "kind": kind,
                "value": value,
                "page": 0,
                "query": "",
            }
        else:
            self.discover_state = {
                "kind": "search",
                "value": "",
                "page": 0,
                "query": query,
            }
        self._reload_discover(reset=True)
        return GLib.SOURCE_REMOVE

    def _on_section_changed(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        if self.search_entry.get_text().strip():
            return
        index = dropdown.get_selected()
        if index < 0 or index >= len(DISCOVER_OPTIONS):
            return
        kind, value, _label = DISCOVER_OPTIONS[index]
        self.discover_state = {"kind": kind, "value": value, "page": 0, "query": ""}
        self._reload_discover(reset=True)

    def _reload_discover(self, reset: bool) -> None:
        state = dict(self.discover_state)
        page = 1 if reset else int(state.get("page", 0)) + 1
        kind = state["kind"]
        value = state["value"]
        query = state["query"]

        if reset:
            clear_container(self.discover_flow)
            self.load_more_button.set_visible(False)
            self.discover_spinner.start()
            self.discover_stack.set_visible_child_name("loading")

        def work() -> api.AppPage:
            if kind == "search":
                result = api.search(query, page=page)
            elif kind == "category":
                result = api.category(value, page=page)
            else:
                result = api.collection(value, page=page)
            for entry in result.entries:
                entry["icon_path"] = self._cache_icon(entry["app_id"], entry.get("icon"))
            return result

        def done(result: api.AppPage) -> None:
            self.discover_spinner.stop()
            self.discover_state["page"] = page
            if reset and not result.entries:
                self.discover_message.set_text(
                    "No applications match your search."
                    if kind == "search"
                    else "Nothing to show here right now."
                )
                self.discover_stack.set_visible_child_name("message")
                return
            self.discover_stack.set_visible_child_name("content")
            for entry in result.entries:
                self.discover_flow.insert(self._app_card(entry), -1)
            self.load_more_button.set_visible(result.has_more)

        def failed(error: BaseException) -> None:
            self.discover_spinner.stop()
            if reset:
                self.discover_message.set_text(str(error))
                self.discover_stack.set_visible_child_name("message")
            else:
                self._show_error("Could not load more applications", error)

        self._run_task(
            "Loading Flathub…", work, on_success=done, on_error=failed, show_status=False
        )

    def _app_card(self, entry: dict[str, Any]) -> Gtk.FlowBoxChild:
        button = Gtk.Button()
        button.add_css_class("flat")
        tooltip = entry.get("summary") or entry["name"]
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda _button, app=entry["app_id"]: self._open_detail(app))

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            width_request=176,
            margin_top=10,
            margin_bottom=10,
            margin_start=10,
            margin_end=10,
        )
        box.append(self._icon_image(entry.get("icon_path"), 96))

        name = Gtk.Label(
            label=entry["name"],
            xalign=0.5,
            justify=Gtk.Justification.CENTER,
            wrap=True,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=22,
        )
        name.add_css_class("heading")
        box.append(name)

        summary = Gtk.Label(
            label=entry.get("summary") or "",
            xalign=0.5,
            justify=Gtk.Justification.CENTER,
            wrap=True,
            lines=2,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=26,
        )
        summary.add_css_class("dim-label")
        summary.add_css_class("caption")
        box.append(summary)

        if entry["app_id"] in self.installed:
            badge = Gtk.Label(label="Installed")
            badge.add_css_class("caption")
            badge.add_css_class("accent")
            box.append(badge)

        button.set_child(box)
        child = Gtk.FlowBoxChild()
        child.set_child(button)
        return child

    # ------------------------------------------------------------------ #
    # Detail page
    # ------------------------------------------------------------------ #
    def _open_detail(self, app_id: str) -> None:
        self.detail_app_id = app_id
        self._detail_info = None
        self.back_button.set_visible(True)
        self.switcher.set_visible(False)
        self.root_stack.set_visible_child_name("detail")
        clear_container(self.detail_box)
        spinner = Gtk.Spinner()
        spinner.start()
        self.detail_box.append(self._centered_box(spinner, f"Loading {app_id}…"))

        def work() -> dict[str, Any]:
            info = api.app_info(app_id)
            info["icon_path"] = self._cache_icon(app_id, info.get("icon"))
            screenshot_paths: list[str] = []
            for index, url in enumerate(info.get("screenshots", [])[:4]):
                path = paths.screenshot_cache_path(app_id, index)
                try:
                    if not (path.exists() and path.stat().st_size > 0):
                        api.download(url, path)
                except (api.FlathubError, OSError):
                    continue
                screenshot_paths.append(str(path))
            info["screenshot_paths"] = screenshot_paths
            return info

        def done(info: dict[str, Any]) -> None:
            self._detail_info = info
            self._render_detail(info)

        self._run_task(
            f"Loading {app_id}…", work, on_success=done, on_error=self._detail_failed
        )

    def _detail_failed(self, error: BaseException) -> None:
        clear_container(self.detail_box)
        message = Gtk.Label(
            label=str(error) or "Could not load this application.",
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=50,
        )
        retry = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
        if self.detail_app_id:
            retry.connect(
                "clicked", lambda *_: self._open_detail(self.detail_app_id or "")
            )
        box = self._centered_box(None, "", message)
        box.append(retry)
        self.detail_box.append(box)

    def _on_back(self, *_args: Any) -> None:
        self.root_stack.set_visible_child_name("main")
        self.back_button.set_visible(False)
        self.switcher.set_visible(True)
        self.detail_app_id = None
        self._detail_info = None

    def _render_detail(self, info: dict[str, Any]) -> None:
        """Build the detail page one section at a time.

        Each section is independent, so a widget that fails to build shows an
        inline message instead of leaving the page empty and the app without a
        description, version, screenshots or install button.
        """
        clear_container(self.detail_box)
        builders: tuple[tuple[str, Callable[[dict[str, Any]], Any]], ...] = (
            ("the application header", self._detail_header),
            ("the action buttons", self._detail_actions),
            ("screenshots", self._detail_screenshots),
            ("the application details", self._detail_facts),
            ("the description", self._detail_description),
            ("the release notes", self._detail_release_notes),
        )
        for label, build in builders:
            try:
                widget = build(info)
            except Exception as error:  # noqa: BLE001 - one bad section must not hide the rest
                widget = self._detail_error(label, error)
            if widget is not None:
                self.detail_box.append(widget)

    @staticmethod
    def _detail_error(section: str, error: BaseException) -> Gtk.Widget:
        return Gtk.Label(
            label=f"Could not show {section} ({type(error).__name__}: {error}).",
            xalign=0,
            wrap=True,
            selectable=True,
        )

    def _detail_header(self, info: dict[str, Any]) -> Gtk.Widget:
        app_id = info.get("app_id") or ""
        header = Gtk.Box(spacing=20)
        header.append(self._icon_image(info.get("icon_path"), 128))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        title = Gtk.Label(label=info.get("name") or app_id, xalign=0, wrap=True)
        title.add_css_class("title-1")
        text.append(title)

        developer = str(info.get("developer") or "").strip()
        if developer:
            developer_label = Gtk.Label(label=developer, xalign=0)
            developer_label.add_css_class("dim-label")
            text.append(developer_label)

        summary = str(info.get("summary") or "").strip()
        if summary:
            text.append(
                Gtk.Label(label=summary, xalign=0, wrap=True, max_width_chars=70)
            )

        badges = Gtk.Box(spacing=8, margin_top=4)
        badge_count = 0
        if info.get("verified"):
            verified = Gtk.Label(label="Verified by Flathub")
            verified.add_css_class("caption")
            verified.add_css_class("accent")
            badges.append(verified)
            badge_count += 1
        if info.get("license"):
            license_label = Gtk.Label(label=str(info["license"]))
            license_label.add_css_class("caption")
            license_label.add_css_class("dim-label")
            badges.append(license_label)
            badge_count += 1
        if badge_count:
            text.append(badges)

        header.append(text)
        return header

    def _detail_actions(self, info: dict[str, Any]) -> Gtk.Widget:
        app_id = info.get("app_id") or ""
        actions = Gtk.Box(spacing=10, margin_top=10, halign=Gtk.Align.START)
        if app_id in self.installed:
            open_button = Gtk.Button(label="Open")
            open_button.add_css_class("suggested-action")
            open_button.connect("clicked", lambda *_: self._launch_app(app_id))
            actions.append(open_button)
            if app_id in self.update_index:
                update_button = Gtk.Button(label="Update")
                update_button.connect("clicked", lambda *_: self._update_app(app_id))
                actions.append(update_button)
            remove_button = Gtk.Button(label="Remove")
            remove_button.add_css_class("destructive-action")
            remove_button.connect(
                "clicked", lambda *_: self._confirm_uninstall(app_id)
            )
            actions.append(remove_button)
        else:
            install_button = Gtk.Button(label="Install")
            install_button.add_css_class("suggested-action")
            install_button.connect("clicked", lambda *_: self._install_app(app_id))
            actions.append(install_button)
        return actions

    def _detail_screenshots(self, info: dict[str, Any]) -> Gtk.Widget | None:
        paths = [
            path
            for path in info.get("screenshot_paths") or []
            if isinstance(path, str) and os.path.exists(path)
        ]
        if not paths:
            return None

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label="Screenshots", xalign=0)
        title.add_css_class("title-4")
        section.append(title)

        strip = Gtk.Box(spacing=12)
        strip.set_valign(Gtk.Align.START)
        drawn = 0
        for path in paths[:6]:
            picture = self._screenshot_picture(path)
            if picture is not None:
                strip.append(picture)
                drawn += 1
        if not drawn:
            return None

        section.append(
            Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                vscrollbar_policy=Gtk.PolicyType.NEVER,
                propagate_natural_height=True,
                hexpand=True,
                child=strip,
            )
        )
        return section

    @staticmethod
    def _detail_facts(info: dict[str, Any]) -> Gtk.Widget:
        app_id = info.get("app_id") or ""
        rows: list[tuple[str, str]] = [("Application ID", app_id)]
        if info.get("version"):
            rows.append(("Latest version", str(info["version"])))
        if info.get("released"):
            rows.append(("Last updated", str(info["released"])))
        if info.get("categories"):
            categories = ", ".join(str(name) for name in info["categories"])
            rows.append(("Categories", categories))
        if info.get("developer"):
            rows.append(("Developer", str(info["developer"])))
        urls = info.get("urls") or {}
        if urls.get("homepage"):
            rows.append(("Homepage", str(urls["homepage"])))

        grid = Gtk.Grid(column_spacing=20, row_spacing=8)
        for row_index, (key, value) in enumerate(rows):
            key_label = Gtk.Label(label=key, xalign=0, valign=Gtk.Align.START)
            key_label.add_css_class("dim-label")
            value_label = Gtk.Label(
                label=value, xalign=0, wrap=True, selectable=True, hexpand=True
            )
            grid.attach(key_label, 0, row_index, 1, 1)
            grid.attach(value_label, 1, row_index, 1, 1)
        return grid

    @staticmethod
    def _detail_text_section(
        title_text: str, body: str, dim: bool = False
    ) -> Gtk.Widget | None:
        body = (body or "").strip()
        if not body:
            return None
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=title_text, xalign=0)
        title.add_css_class("title-4")
        section.append(title)
        label = Gtk.Label(
            label=body, xalign=0, wrap=True, selectable=True, max_width_chars=100
        )
        if dim:
            label.add_css_class("dim-label")
        section.append(label)
        return section

    def _detail_description(self, info: dict[str, Any]) -> Gtk.Widget | None:
        return self._detail_text_section("About", str(info.get("description") or ""))

    def _detail_release_notes(self, info: dict[str, Any]) -> Gtk.Widget | None:
        return self._detail_text_section(
            "What's new", str(info.get("release_notes") or ""), dim=True
        )

    # ------------------------------------------------------------------ #
    # Operations
    # ------------------------------------------------------------------ #
    def _install_app(self, app_id: str) -> None:
        def work() -> str:
            # Plain ``flatpak install flathub <app>``: no installation is selected,
            # so flatpak uses its default one.
            flatpak.ensure_flathub()
            return flatpak.install(app_id)

        self._run_operation(
            f"Installing {app_id}…", work, success_message=f"{app_id} was installed."
        )

    def _confirm_uninstall(self, app_id: str) -> None:
        record = self.installed.get(app_id)
        name = record.name if record else app_id
        self._confirm(
            f"Remove {name}?",
            f"{app_id} will be uninstalled. Application data is kept unless you remove it with flatpak yourself.",
            "Remove",
            lambda: self._uninstall_app(app_id),
        )

    def _uninstall_app(self, app_id: str) -> None:
        record = self.installed.get(app_id)
        installation = record.installation if record else ""

        self._run_operation(
            f"Removing {app_id}…",
            lambda: flatpak.uninstall(app_id, installation=installation),
            success_message=f"{app_id} was removed.",
        )

    def _update_app(self, app_id: str) -> None:
        info = self.update_index.get(app_id, {})
        installation = info.get("installation") or "user"

        self._run_operation(
            f"Updating {app_id}…",
            lambda: flatpak.update(app_id, installation=installation),
            success_message=f"{app_id} was updated.",
        )

    def _update_all(self) -> None:
        pending = dict(self.update_index)
        if not pending:
            self._toast("Everything is already up to date.")
            return

        def work() -> list[str]:
            failures: list[str] = []
            for app_id, info in pending.items():
                try:
                    flatpak.update(
                        app_id, installation=info.get("installation") or "user"
                    )
                except flatpak.FlatpakError as error:
                    failures.append(f"{app_id}: {error}")
            return failures

        def done(failures: list[str]) -> None:
            if failures:
                error = flatpak.FlatpakError(
                    "Some applications could not be updated.", "\n".join(failures)
                )
                self._show_error("Updates incomplete", error)
            else:
                self._toast(f"{len(pending)} application(s) updated.")

        self._run_operation("Updating applications…", work, on_success=done)

    def _launch_app(self, app_id: str) -> None:
        try:
            flatpak.launch(app_id)
        except flatpak.FlatpakError as error:
            self._show_error(f"Could not launch {app_id}", error)
        else:
            self._toast(f"Launching {app_id}…")
