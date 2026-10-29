"""Thin, testable wrapper around the ``flatpak`` command line interface.

The application is itself distributed as a Flatpak, so it has to drive the
host's ``flatpak`` binary from inside a sandbox.  When ``/.flatpak-info``
exists we go through ``flatpak-spawn --host`` (requires the
``--talk-name=org.freedesktop.Flatpak`` permission); otherwise the CLI is used
directly.  Nothing in this module imports GTK.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

FLATHUB_REMOTE = "flathub"
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
DEFAULT_TIMEOUT = 900
LIST_COLUMNS = "application,name,version,branch,installation,origin,size"


class FlatpakError(Exception):
    """Raised when a flatpak command fails."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


@dataclass(frozen=True)
class Installation:
    """One application installed in one flatpak installation."""

    app_id: str
    name: str
    version: str = ""
    branch: str = ""
    installation: str = ""
    origin: str = ""
    size: str = ""


def in_sandbox() -> bool:
    """Return True when running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info")


def _use_host_spawn() -> bool:
    return in_sandbox() and shutil.which("flatpak-spawn") is not None


def flatpak_available() -> bool:
    """Return True when the flatpak CLI can be reached."""
    if shutil.which("flatpak") is not None:
        return True
    return _use_host_spawn()


def _command(*args: str) -> list[str]:
    prefix = ["flatpak-spawn", "--host"] if _use_host_spawn() else []
    return [*prefix, *args]


def run(*args: str, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a command (on the host when sandboxed) and capture its output."""
    try:
        return subprocess.run(
            _command(*args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FlatpakError(
            "The flatpak command was not found. Install Flatpak and try again."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FlatpakError(f"Timed out while running: {' '.join(args)}") from exc


def _output(process: subprocess.CompletedProcess[str]) -> str:
    return f"{process.stdout or ''}{process.stderr or ''}"


def summarize(output: str, limit: int = 12) -> str:
    """Keep the last few non-empty lines of CLI output for error messages."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _checked(*args: str, timeout: int = DEFAULT_TIMEOUT, action: str) -> str:
    process = run(*args, timeout=timeout)
    output = _output(process)
    if process.returncode != 0:
        message = summarize(output) or f"{action} failed with status {process.returncode}."
        raise FlatpakError(message, output)
    return output


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_list_output(text: str) -> dict[str, Installation]:
    """Parse ``flatpak list --app --columns=...`` tab separated output."""
    installed: dict[str, Installation] = {}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) < 2 or not fields[0]:
            continue
        app_id = fields[0]
        padded = fields + [""] * (7 - len(fields))
        installed[app_id] = Installation(
            app_id=app_id,
            name=padded[1] or app_id,
            version=padded[2],
            branch=padded[3],
            installation=padded[4],
            origin=padded[5],
            size=padded[6],
        )
    return installed


def parse_updates_output(text: str, installation: str = "") -> dict[str, dict[str, str]]:
    """Parse ``flatpak remote-ls --updates --columns=...`` output."""
    updates: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split("\t")]
        if not fields or not fields[0]:
            continue
        padded = fields + [""] * (3 - len(fields))
        updates[fields[0]] = {
            "app_id": fields[0],
            "branch": padded[1],
            "version": padded[2],
            "installation": installation,
        }
    return updates


def parse_remotes_output(text: str) -> dict[str, str]:
    remotes: dict[str, str] = {}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) >= 1 and fields[0]:
            remotes[fields[0]] = fields[1] if len(fields) > 1 else ""
    return remotes


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def list_installed() -> dict[str, Installation]:
    """Return every installed application keyed by application ID."""
    output = _checked(
        "flatpak", "list", "--app", f"--columns={LIST_COLUMNS}", action="Listing applications"
    )
    return parse_list_output(output)


def installations() -> list[str]:
    """Return the names of the flatpak installations in use (e.g. ``user``)."""
    process = run("flatpak", "list", "--columns=installation")
    names: list[str] = []
    for line in _output(process).splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


def updates() -> dict[str, dict[str, str]]:
    """Return applications with a pending update, keyed by application ID."""
    result: dict[str, dict[str, str]] = {}
    for installation in installations() or ["user"]:
        process = run(
            "flatpak",
            f"--installation={installation}",
            "remote-ls",
            "--updates",
            "--columns=application,branch,version",
            timeout=180,
        )
        if process.returncode != 0:
            continue
        result.update(parse_updates_output(process.stdout, installation))
    return result


def remotes(installation: str = "user") -> dict[str, str]:
    process = run("flatpak", f"--installation={installation}", "remotes", "--columns=name,url")
    if process.returncode != 0:
        return {}
    return parse_remotes_output(process.stdout)


def ensure_flathub(installation: str = "user") -> bool:
    """Add the Flathub remote if it is missing. Returns True when it was added."""
    if FLATHUB_REMOTE in remotes(installation):
        return False
    _checked(
        "flatpak",
        f"--installation={installation}",
        "remote-add",
        "--if-not-exists",
        FLATHUB_REMOTE,
        FLATHUB_REPO,
        action="Adding the Flathub remote",
    )
    return True


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
def install(app_id: str, installation: str = "user", remote: str = FLATHUB_REMOTE) -> str:
    """Install ``app_id`` from ``remote`` into ``installation``."""
    return _checked(
        "flatpak",
        f"--installation={installation}",
        "install",
        "-y",
        "--noninteractive",
        remote,
        app_id,
        timeout=3600,
        action=f"Installing {app_id}",
    )


def uninstall(app_id: str, installation: str = "user", delete_data: bool = False) -> str:
    """Remove ``app_id`` (optionally wiping its data) from ``installation``."""
    args = [
        "flatpak",
        f"--installation={installation}",
        "uninstall",
        "-y",
        "--noninteractive",
    ]
    if delete_data:
        args.append("--delete-data")
    args.append(app_id)
    return _checked(*args, timeout=1800, action=f"Removing {app_id}")


def update(app_id: str | None = None, installation: str = "user") -> str:
    """Update one application, or every application in ``installation``."""
    args = [
        "flatpak",
        f"--installation={installation}",
        "update",
        "-y",
        "--noninteractive",
    ]
    if app_id:
        args.append(app_id)
    return _checked(*args, timeout=3600, action="Updating applications")


def refresh_appstream() -> str:
    """Ask flatpak to download fresh AppStream metadata from all remotes."""
    return _checked(
        "flatpak", "update", "--appstream", timeout=900, action="Refreshing Flathub metadata"
    )


def launch(app_id: str) -> None:
    """Launch an installed application in the background."""
    try:
        subprocess.Popen(
            _command("flatpak", "run", app_id),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise FlatpakError(f"Could not launch {app_id}: {exc}") from exc
