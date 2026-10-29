"""Headless unit tests for the non-GUI parts of Nexon Flatpak Store.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from nexon_store import api, flatpak, paths  # noqa: E402


class FlatpakParsingTests(unittest.TestCase):
    def test_parses_typical_list_output(self) -> None:
        text = (
            "org.mozilla.firefox\tFirefox\t128.0\tstable\tuser\tflathub\t120.4 MB\n"
            "org.gnome.Builder\tBuilder\t46.0\tstable\tsystem\tflathub\t540.1 MB\n"
        )
        installed = flatpak.parse_list_output(text)

        self.assertEqual(set(installed), {"org.mozilla.firefox", "org.gnome.Builder"})
        firefox = installed["org.mozilla.firefox"]
        self.assertEqual(firefox.name, "Firefox")
        self.assertEqual(firefox.version, "128.0")
        self.assertEqual(firefox.installation, "user")
        self.assertEqual(firefox.origin, "flathub")
        self.assertEqual(firefox.size, "120.4 MB")

    def test_ignores_blank_and_truncated_rows(self) -> None:
        self.assertEqual(flatpak.parse_list_output("\n\t\n"), {})
        self.assertEqual(flatpak.parse_list_output("org.example.App\n"), {})

    def test_tolerates_missing_columns(self) -> None:
        installed = flatpak.parse_list_output("org.example.App\tExample App")
        self.assertEqual(installed["org.example.App"].name, "Example App")
        self.assertEqual(installed["org.example.App"].version, "")

    def test_updates_parsing_keeps_installation(self) -> None:
        text = "org.mozilla.firefox\tstable\t129.0\norg.gnome.Builder\tstable\t47.0\n"
        updates = flatpak.parse_updates_output(text, installation="system")

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates["org.mozilla.firefox"]["version"], "129.0")
        self.assertEqual(updates["org.mozilla.firefox"]["installation"], "system")

    def test_remotes_parsing(self) -> None:
        remotes = flatpak.parse_remotes_output(
            "flathub\thttps://dl.flathub.org/repo/\nflatpak\t\n"
        )
        self.assertEqual(remotes["flathub"], "https://dl.flathub.org/repo/")
        self.assertEqual(remotes["flatpak"], "")


class FlatpakCommandTests(unittest.TestCase):
    def test_direct_command_when_not_sandboxed(self) -> None:
        with mock.patch.object(flatpak, "_use_host_spawn", return_value=False):
            self.assertEqual(flatpak._command("flatpak", "list"), ["flatpak", "list"])

    def test_host_spawn_when_sandboxed(self) -> None:
        with mock.patch.object(flatpak, "_use_host_spawn", return_value=True):
            self.assertEqual(
                flatpak._command("flatpak", "list"),
                ["flatpak-spawn", "--host", "flatpak", "list"],
            )

    def test_install_targets_requested_installation_and_remote(self) -> None:
        with mock.patch.object(flatpak, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            flatpak.install("org.mozilla.firefox", installation="system")
            args = run.call_args.args

        self.assertEqual(args[0], "flatpak")
        self.assertIn("--installation=system", args)
        self.assertIn("-y", args)
        self.assertIn("flathub", args)
        self.assertEqual(args[-1], "org.mozilla.firefox")

    def test_uninstall_can_delete_data(self) -> None:
        with mock.patch.object(flatpak, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            flatpak.uninstall("org.mozilla.firefox", delete_data=True)
            args = run.call_args.args

        self.assertIn("--delete-data", args)
        self.assertEqual(args[-1], "org.mozilla.firefox")

    def test_failure_raises_with_output_tail(self) -> None:
        output = "\n".join(f"line {index}" for index in range(30))
        with mock.patch.object(flatpak, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr=output)
            with self.assertRaises(flatpak.FlatpakError) as context:
                flatpak.uninstall("org.mozilla.firefox")

        self.assertIn("line 29", str(context.exception))
        self.assertEqual(context.exception.output, output)

    def test_summarize_keeps_last_lines(self) -> None:
        text = "\n".join(f"line {index}" for index in range(20))
        summary = flatpak.summarize(text, limit=3)
        self.assertEqual(summary.splitlines(), ["line 17", "line 18", "line 19"])


class HtmlTests(unittest.TestCase):
    def test_converts_paragraphs_and_lists(self) -> None:
        markup = "<p>Fast &amp; private</p><ul><li>One</li><li>Two</li></ul>"
        text = api.html_to_text(markup)

        self.assertIn("Fast & private", text)
        self.assertIn("• One", text)
        self.assertNotIn("<", text)

    def test_handles_break_tags(self) -> None:
        self.assertEqual(api.html_to_text("a<br/>b"), "a\nb")

    def test_handles_empty_input(self) -> None:
        self.assertEqual(api.html_to_text(None), "")
        self.assertEqual(api.html_to_text(""), "")

    def test_format_date(self) -> None:
        self.assertRegex(api.format_date("1761782400"), r"^\d{2} [A-Z][a-z]{2} \d{4}$")
        self.assertEqual(api.format_date("0"), "")
        self.assertEqual(api.format_date(None), "")
        self.assertEqual(api.format_date("nonsense"), "")


class SearchTests(unittest.TestCase):
    PAYLOAD = {
        "hits": [
            {
                "app_id": "org.mozilla.firefox",
                "name": "Firefox",
                "summary": " Fast, Private & Safe Web Browser ",
                "icon": "https://example.invalid/icon.png",
                "developer_name": "Mozilla",
                "verification_verified": True,
                "main_categories": ["Network"],
            },
            {"name": "Broken entry without an app id"},
        ],
        "page": 1,
        "totalPages": 2,
    }

    def test_normalizes_hits_and_reports_more_pages(self) -> None:
        with mock.patch.object(api, "_request", return_value=self.PAYLOAD) as request:
            page = api.search("firefox", page=1)

        self.assertEqual(request.call_args.kwargs["payload"]["query"], "firefox")
        self.assertEqual(len(page.entries), 1)

        entry = page.entries[0]
        self.assertEqual(entry["app_id"], "org.mozilla.firefox")
        self.assertEqual(entry["summary"], "Fast, Private & Safe Web Browser")
        self.assertTrue(entry["verified"])
        self.assertEqual(entry["categories"], ["Network"])
        self.assertTrue(page.has_more)

    def test_last_page_has_no_more(self) -> None:
        payload = {"hits": [], "totalPages": 3}
        with mock.patch.object(api, "_request", return_value=payload):
            self.assertFalse(api.search("x", page=3).has_more)

    def test_missing_total_pages_means_no_more(self) -> None:
        with mock.patch.object(api, "_request", return_value={"hits": []}):
            self.assertFalse(api.search("x").has_more)

    def test_blank_query_never_hits_the_network(self) -> None:
        with mock.patch.object(api, "_request", side_effect=AssertionError("request sent")):
            page = api.search("   ")

        self.assertEqual(page.entries, [])

    def test_category_uses_category_collection_endpoint(self) -> None:
        with mock.patch.object(api, "_request", return_value={"hits": []}) as request:
            api.category("Game")
        self.assertIn("/collection/category/Game", request.call_args.args[0])


class AppInfoTests(unittest.TestCase):
    RAW = {
        "id": "org.mozilla.firefox",
        "name": "Firefox",
        "summary": "Fast, Private & Safe Web Browser",
        "developer_name": "Mozilla",
        "description": "<p>Firefox is a browser.</p><ul><li>Private</li></ul>",
        "icon": "https://example.invalid/firefox.png",
        "project_license": "MPL-2.0",
        "categories": ["Network", "WebBrowser"],
        "is_free_license": True,
        "metadata": {"flathub::verification::verified": True},
        "releases": [
            {"version": "128.0", "timestamp": "1700000000"},
            {"version": "129.0", "timestamp": "1761782400", "description": "<p>New stuff</p>"},
        ],
        "screenshots": [
            {
                "sizes": [
                    {"src": "https://example.invalid/small.png", "width": "624", "height": "351"},
                    {"src": "https://example.invalid/large.png", "width": "1248", "height": "702"},
                    {"src": "https://example.invalid/huge.png", "width": "4000", "height": "2250"},
                ]
            }
        ],
    }

    def test_normalizes_appstream_metadata(self) -> None:
        info = api.normalize_app(self.RAW)

        self.assertEqual(info["app_id"], "org.mozilla.firefox")
        self.assertEqual(info["version"], "129.0")
        self.assertEqual(info["license"], "MPL-2.0")
        self.assertTrue(info["verified"])
        self.assertEqual(info["screenshots"], ["https://example.invalid/large.png"])
        self.assertIn("Firefox is a browser.", info["description"])
        self.assertNotIn("<p>", info["description"])
        self.assertEqual(info["release_notes"], "New stuff")
        self.assertEqual(info["categories"], ["Network", "WebBrowser"])

    def test_missing_screenshots_are_ignored(self) -> None:
        raw = dict(self.RAW)
        raw.pop("screenshots")
        self.assertEqual(api.normalize_app(raw)["screenshots"], [])

    def test_missing_app_is_reported_clearly(self) -> None:
        missing = api.FlathubError("Flathub returned HTTP 404", status=404)
        with mock.patch.object(api, "_request", side_effect=missing):
            with self.assertRaises(api.FlathubError) as context:
                api.app_info("org.example.Missing")

        self.assertIn("not available on Flathub", str(context.exception))

    def test_network_errors_are_wrapped(self) -> None:
        with mock.patch.object(
            api, "_request", side_effect=api.FlathubError("Could not reach Flathub.")
        ):
            with self.assertRaises(api.FlathubError):
                api.app_info("org.mozilla.firefox")

    def test_appstream_icon_url(self) -> None:
        self.assertEqual(
            api.appstream_icon_url("org.mozilla.firefox"),
            "https://dl.flathub.org/repo/appstream/x86_64/icons/128x128/"
            "org.mozilla.firefox.png",
        )


class CachePathTests(unittest.TestCase):
    def test_icon_cache_path_is_sanitised(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": "/tmp/nexon-test-cache"}):
            path = paths.icon_cache_path("org.mozilla../firefox")

        self.assertTrue(str(path).startswith("/tmp/nexon-test-cache"))
        self.assertNotIn("..", path.name)
        self.assertNotIn("/", path.name)

    def test_screenshot_cache_path_stays_inside_cache(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": "/tmp/nexon-test-cache"}):
            path = paths.screenshot_cache_path("org.example.App", 2)

        self.assertEqual(path.name, "org.example.App-2.png")


if __name__ == "__main__":
    unittest.main()
