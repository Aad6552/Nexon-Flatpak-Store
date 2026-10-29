"""Smoke test for the application detail page.

The detail page is pure GTK code, so this test swaps ``gi`` for a tiny fake
before importing the module. The fake widgets deliberately have no ``__len__``,
exactly like GTK4 widgets, so a regression such as ``len(box)`` raises here
instead of blanking the page at runtime.
"""

import sys
import types
import unittest
from pathlib import Path


class FakeWidget:
    """Stand-in for a GTK widget: records children and ignores setters."""

    def __init__(self, *args, **kwargs):
        self.children = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def ignore(*args, **kwargs):
            return None

        return ignore

    def append(self, child):
        self.children.append(child)

    def get_first_child(self):
        return None

    def remove(self, child):
        pass


class AutoNamespace(FakeWidget):
    """Attribute access returns more namespaces; calling one builds a widget."""

    def __call__(self, *args, **kwargs):
        return FakeWidget()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return AutoNamespace()


class FakeApplicationWindow(FakeWidget):
    """Base class for the fake Adw.ApplicationWindow."""


def install_fake_gi():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args, **kwargs: None
    repository = types.ModuleType("gi.repository")
    for name in ("Gtk", "Adw", "Gdk", "GdkPixbuf", "Gio", "GLib", "Pango"):
        setattr(repository, name, AutoNamespace())
    repository.Adw.ApplicationWindow = FakeApplicationWindow
    gi.repository = repository
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository


install_fake_gi()

try:
    from nexon_store import window
except ImportError:  # running from the repository root without PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from nexon_store import window


class DetailPageTest(unittest.TestCase):
    def make_window(self):
        instance = window.StoreWindow.__new__(window.StoreWindow)
        instance.detail_box = FakeWidget()
        instance.installed = {}
        instance.update_index = {}
        instance._icon_image = lambda path, size: FakeWidget()
        return instance

    def test_fake_widgets_behave_like_gtk(self):
        # Without this the regression test below would be toothless.
        with self.assertRaises(TypeError):
            len(FakeWidget())

    def test_detail_page_renders_badges(self):
        instance = self.make_window()
        instance._render_detail(
            {
                "app_id": "org.gnome.Dictionary",
                "name": "Dictionary",
                "summary": "Look up words",
                "verified": True,
                "license": "GPL-3.0-or-later",
                "description": "A dictionary.",
                "urls": {"homepage": "https://example.org"},
                "categories": ["Utility"],
                "version": "1.0",
                "released": "29 Oct 2026",
                "release_notes": "",
                "screenshot_paths": [],
            }
        )
        self.assertTrue(instance.detail_box.children)

    def test_detail_page_renders_with_minimal_info(self):
        instance = self.make_window()
        instance._render_detail({"app_id": "org.example.App"})
        self.assertTrue(instance.detail_box.children)

    def test_screenshot_fits_the_viewport(self):
        # 922x572 is a typical Flathub screenshot: it used to be drawn smaller
        # than its slot, so the page showed a sliver of it.
        self.assertEqual(window.StoreWindow._fit_size(922, 572), (677, 420))
        self.assertEqual(window.StoreWindow._fit_size(410, 666), (259, 420))
        self.assertEqual(window.StoreWindow._fit_size(0, 0), (0, 0))

    def test_a_broken_section_does_not_hide_the_rest(self):
        instance = self.make_window()

        def boom(_info):
            raise RuntimeError("boom")

        instance._detail_header = boom
        instance._render_detail({"app_id": "org.example.App", "version": "1.0"})
        self.assertTrue(instance.detail_box.children)

    def test_detail_page_for_installed_app(self):
        instance = self.make_window()
        instance.installed = {"org.example.App": object()}
        instance.update_index = {"org.example.App": {"installation": "user"}}
        instance._render_detail({"app_id": "org.example.App", "name": "App"})
        self.assertTrue(instance.detail_box.children)


if __name__ == "__main__":
    unittest.main()
