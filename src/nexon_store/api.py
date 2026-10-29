"""Small, dependency-free client for the public Flathub API.

Everything here uses the Python standard library only, so the module can be
imported and tested without GTK, libadwaita or an internet connection.

Endpoints used (all public, no API key required):

* ``POST /api/v2/search``            - full text search
* ``GET  /api/v2/appstream/<id>``    - AppStream metadata for one application
* ``GET  /api/v2/collection/<name>`` - curated lists such as ``popular``
* ``GET  /api/v2/collection/category/<name>`` - category listings
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_BASE = "https://flathub.org/api/v2"
USER_AGENT = "NexonFlatpakStore/1.0 (+https://github.com/Aad6552/Nexon-Flatpak-Store)"
DEFAULT_TIMEOUT = 30
MAX_SCREENSHOT_WIDTH = 1400

#: Curated Flathub collections shown in the browse view.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("recently-updated", "Recently updated"),
    ("popular", "Popular"),
    ("trending", "Trending"),
)

#: Flathub main categories (freedesktop menu spec names).
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("AudioVideo", "Audio & Video"),
    ("Development", "Developer tools"),
    ("Education", "Education"),
    ("Game", "Games"),
    ("Graphics", "Graphics"),
    ("Network", "Networking"),
    ("Office", "Office"),
    ("Science", "Science"),
    ("Settings", "Settings"),
    ("System", "System"),
    ("Utility", "Utilities"),
)


class FlathubError(Exception):
    """Raised when the Flathub API cannot be queried."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AppPage:
    """One page of Flathub results."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    page: int = 1
    has_more: bool = False


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _request(url: str, payload: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise FlathubError(f"Flathub returned HTTP {exc.code} for this request.", status=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise FlathubError("Could not reach Flathub. Check your internet connection.") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FlathubError("Flathub returned an unexpected response.") from exc


def download(url: str, destination: Path | str, timeout: int = 60) -> Path:
    """Download ``url`` into ``destination`` atomically and return the path."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise FlathubError(f"Could not download {url}.") from exc

    temporary = destination.with_name(destination.name + ".part")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
_BREAK_TAGS = re.compile(r"(?i)<br\s*/?>")
_PARAGRAPH_END = re.compile(r"(?i)</p\s*>")
_LIST_ITEM = re.compile(r"(?i)<li[^>]*>")
_LIST_END = re.compile(r"(?i)</(?:ul|ol)\s*>")
_ANY_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_text(value: str | None) -> str:
    """Turn the HTML-ish markup used by AppStream descriptions into plain text."""
    if not value:
        return ""
    text = value.replace("\r\n", "\n")
    text = _BREAK_TAGS.sub("\n", text)
    text = _PARAGRAPH_END.sub("\n\n", text)
    text = _LIST_ITEM.sub("\n• ", text)
    text = _LIST_END.sub("\n", text)
    text = _ANY_TAG.sub("", text)
    text = html.unescape(text)
    text = _SPACES.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def format_date(timestamp: Any) -> str:
    """Format a Flathub release timestamp (epoch seconds) as ``29 Oct 2026``."""
    try:
        seconds = int(timestamp)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%d %b %Y")


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def _category_names(categories: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(categories, (list, tuple)):
        return names
    for category in categories:
        if isinstance(category, dict):
            label = category.get("name") or category.get("id")
        else:
            label = category
        if isinstance(label, str) and label.strip():
            names.append(label.strip())
    return names


def _normalize_hit(hit: dict[str, Any]) -> dict[str, Any]:
    app_id = (hit.get("app_id") or "").strip()
    return {
        "app_id": app_id,
        "name": (hit.get("name") or app_id).strip(),
        "summary": (hit.get("summary") or "").strip(),
        "icon": hit.get("icon") or "",
        "developer": (hit.get("developer_name") or "").strip(),
        "verified": bool(hit.get("verification_verified")),
        "categories": _category_names(hit.get("main_categories")),
    }


def _page_from(data: Any, page: int) -> AppPage:
    if not isinstance(data, dict):
        return AppPage(entries=[], page=page, has_more=False)
    entries = [
        entry
        for entry in (_normalize_hit(hit) for hit in data.get("hits") or [] if isinstance(hit, dict))
        if entry["app_id"]
    ]
    try:
        total_pages = int(data.get("totalPages") or 1)
    except (TypeError, ValueError):
        total_pages = 1
    return AppPage(entries=entries, page=page, has_more=page < total_pages)


def _pick_screenshot_source(screenshot: dict[str, Any]) -> str:
    best: tuple[int, str] | None = None
    for size in screenshot.get("sizes") or []:
        if not isinstance(size, dict):
            continue
        source = size.get("src")
        if not source:
            continue
        try:
            width = int(size.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        if width > MAX_SCREENSHOT_WIDTH:
            continue
        if best is None or width > best[0]:
            best = (width, source)
    if best is not None:
        return best[1]
    sizes = screenshot.get("sizes") or []
    if sizes and isinstance(sizes[0], dict):
        return sizes[0].get("src") or ""
    return ""


def _latest_release(releases: Any) -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    latest_stamp = -1
    for release in releases or []:
        if not isinstance(release, dict):
            continue
        try:
            stamp = int(release.get("timestamp") or 0)
        except (TypeError, ValueError):
            stamp = 0
        if latest is None or stamp > latest_stamp:
            latest, latest_stamp = release, stamp
    return latest or {}


def normalize_app(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw ``/appstream/<id>`` payload into what the UI needs."""
    metadata = raw.get("metadata") or {}
    release = _latest_release(raw.get("releases"))
    screenshots = []
    for screenshot in raw.get("screenshots") or []:
        if isinstance(screenshot, dict):
            source = _pick_screenshot_source(screenshot)
            if source:
                screenshots.append(source)

    icon = raw.get("icon") or ""
    if not icon:
        for candidate in raw.get("icons") or []:
            if isinstance(candidate, str) and candidate:
                icon = candidate
                break

    return {
        "app_id": raw.get("id") or "",
        "name": (raw.get("name") or "").strip(),
        "summary": (raw.get("summary") or "").strip(),
        "developer": (raw.get("developer_name") or "").strip(),
        "description": html_to_text(raw.get("description")),
        "icon": icon,
        "screenshots": screenshots,
        "categories": _category_names(raw.get("categories")),
        "license": (raw.get("project_license") or "").strip(),
        "version": (release.get("version") or "").strip(),
        "released": format_date(release.get("timestamp")),
        "release_notes": html_to_text(release.get("description")),
        "verified": bool(metadata.get("flathub::verification::verified")),
        "urls": raw.get("urls") or {},
        "is_free_license": bool(raw.get("is_free_license")),
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def search(query: str, page: int = 1, per_page: int = 24) -> AppPage:
    """Search Flathub for ``query``."""
    query = query.strip()
    if not query:
        return AppPage(entries=[], page=page, has_more=False)
    data = _request(
        f"{API_BASE}/search",
        payload={"query": query, "page": page, "per_page": per_page},
    )
    return _page_from(data, page)


def collection(name: str, page: int = 1, per_page: int = 24) -> AppPage:
    """Fetch a curated Flathub collection such as ``recently-updated``."""
    url = f"{API_BASE}/collection/{urllib.parse.quote(name)}?page={page}&per_page={per_page}"
    return _page_from(_request(url), page)


def category(name: str, page: int = 1, per_page: int = 24) -> AppPage:
    """Fetch one Flathub category such as ``Game``."""
    return collection(f"category/{urllib.parse.quote(name)}", page=page, per_page=per_page)


def appstream_icon_url(app_id: str, size: int = 128) -> str:
    """Deterministic Flathub AppStream icon URL for an application ID."""
    return f"https://dl.flathub.org/repo/appstream/x86_64/icons/{size}x{size}/{app_id}.png"


def app_info(app_id: str) -> dict[str, Any]:
    """Fetch detailed AppStream metadata for ``app_id``."""
    url = f"{API_BASE}/appstream/{urllib.parse.quote(app_id)}"
    try:
        data = _request(url)
    except FlathubError as exc:
        if exc.status == 404:
            raise FlathubError(f"{app_id} is not available on Flathub.", status=404) from exc
        raise
    if not isinstance(data, dict):
        raise FlathubError(f"Flathub returned no metadata for {app_id}.")
    return normalize_app(data)
