#!/usr/bin/env bash
#
# Run Nexon Flatpak Store.
#
#   ./run.sh            run from source when GTK4 is available, otherwise use
#                       the installed Flatpak or build one and run it
#   ./run.sh --source   always run the Python source from this checkout
#   ./run.sh --flatpak  always run the installed Flatpak
#   ./run.sh --build    build the Flatpak with flatpak-builder and run it
set -euo pipefail

APP_NAME="Nexon Flatpak Store"
APP_ID="io.github.aad6552.NexonFlatpakStore"

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
MANIFEST="$REPO_ROOT/$APP_ID.json"
BUILD_DIR="$REPO_ROOT/.flatpak-builder/build"
STATE_DIR="$REPO_ROOT/.flatpak-builder/state"

MODE="auto"
for arg in "$@"; do
    case "$arg" in
        --source) MODE="source" ;;
        --flatpak) MODE="flatpak" ;;
        --build) MODE="build" ;;
        -h | --help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Use ./run.sh --help for usage." >&2
            exit 2
            ;;
    esac
done

if [ "$(uname -s)" != "Linux" ]; then
    echo "$APP_NAME runs on Linux only." >&2
    exit 1
fi

if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
    echo "Warning: no graphical session detected (WAYLAND_DISPLAY and DISPLAY are unset)." >&2
fi

python_has_gtk() {
    command -v python3 >/dev/null 2>&1 || return 1
    python3 - <<'PY' >/dev/null 2>&1 || return 1
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: F401
PY
}

app_installed() {
    command -v flatpak >/dev/null 2>&1 || return 1
    flatpak info "$APP_ID" >/dev/null 2>&1
}

run_source() {
    if ! python_has_gtk; then
        cat >&2 <<EOF
Running from source needs Python 3 with PyGObject, GTK 4 and libadwaita.
Install them (for example 'python3-gi gir1.2-gtk-4.0 gir1.2-adw-1' on Debian
and Ubuntu) or use ./run.sh --build to run the Flatpak build.
EOF
        exit 1
    fi
    exec "$REPO_ROOT/bin/nexon-flatpak-store"
}

run_flatpak() {
    if ! app_installed; then
        echo "$APP_NAME is not installed as a Flatpak yet." >&2
        echo "Install it with ./install.sh, or run the source with ./run.sh --source." >&2
        exit 1
    fi
    exec flatpak run "$APP_ID"
}

run_build() {
    if ! command -v flatpak-builder >/dev/null 2>&1; then
        echo "flatpak-builder is needed to build and run the Flatpak." >&2
        echo "Install flatpak-builder, or run the source with ./run.sh --source." >&2
        exit 1
    fi
    echo "Building $APP_NAME. The first run downloads the GNOME runtime and SDK."
    exec flatpak-builder --user --force-clean --run \
        --install-deps-from=flathub --state-dir="$STATE_DIR" \
        "$BUILD_DIR" "$MANIFEST"
}

case "$MODE" in
    source) run_source ;;
    flatpak) run_flatpak ;;
    build) run_build ;;
    auto)
        if python_has_gtk; then
            run_source
        elif app_installed; then
            run_flatpak
        elif command -v flatpak-builder >/dev/null 2>&1; then
            run_build
        else
            cat >&2 <<EOF
$APP_NAME cannot be started yet.

  * Install it as a Flatpak:          ./install.sh
  * Run it from source (needs GTK4):  ./run.sh --source

Flatpak, flatpak-builder and GTK4 were not found on this system.
EOF
            exit 1
        fi
        ;;
esac
