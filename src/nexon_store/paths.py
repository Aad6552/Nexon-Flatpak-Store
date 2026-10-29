"""Filesystem helpers (no GTK imports so they stay unit-testable)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_CACHE_DIR_NAME = "nexon-flatpak-store"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_component(value: str) -> str:
    """Make a string safe to use as a single path component."""
    cleaned = _UNSAFE.sub("_", value).lstrip(".")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "__")
    return cleaned or "_"


def _xdg_base(env_var: str, fallback: str) -> Path:
    value = os.environ.get(env_var, "").strip()
    if value:
        return Path(value).expanduser()
    return Path.home() / fallback


def cache_dir(*parts: str) -> Path:
    """Return (and create) a directory below the user's cache directory."""
    path = _xdg_base("XDG_CACHE_HOME", ".cache") / _CACHE_DIR_NAME
    for part in parts:
        path = path / _safe_component(part)
    path.mkdir(parents=True, exist_ok=True)
    return path


def icon_cache_path(app_id: str) -> Path:
    return cache_dir("icons") / f"{_safe_component(app_id)}.png"


def screenshot_cache_path(app_id: str, index: int) -> Path:
    return cache_dir("screenshots") / f"{_safe_component(app_id)}-{int(index)}.png"
