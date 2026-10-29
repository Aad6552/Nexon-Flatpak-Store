#!/usr/bin/env bash
#
# Nexon Flatpak Store installer.
#
# Installs Flatpak and flatpak-builder when they are missing, makes sure the
# Flathub remote exists, then builds this checkout as a Flatpak and installs it.
# Uses whichever package manager the distribution provides.
set -euo pipefail

APP_NAME="Nexon Flatpak Store"
APP_ID="io.github.aad6552.NexonFlatpakStore"
FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
MANIFEST="$REPO_ROOT/$APP_ID.json"
BUILD_DIR="$REPO_ROOT/.flatpak-builder/build"
STATE_DIR="$REPO_ROOT/.flatpak-builder/state"

RUN_AFTER=0
SYSTEM_SCOPE=0
SKIP_DEPS=0

usage() {
    cat <<EOF
Usage: ./install.sh [options]

Builds and installs $APP_NAME on this computer.

Options:
  --run        Start the store once it is installed
  --system     Install for every user (needs root/sudo) instead of just you
  --no-deps    Do not try to install Flatpak/flatpak-builder automatically
  -y, --yes    Accepted for convenience; this script never asks questions
  -h, --help   Show this help

Uninstall again with:
  flatpak uninstall $APP_ID
EOF
}

for arg in "$@"; do
    case "$arg" in
        --run) RUN_AFTER=1 ;;
        --system) SYSTEM_SCOPE=1 ;;
        --no-deps) SKIP_DEPS=1 ;;
        -y | --yes) ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$(uname -s)" != "Linux" ]; then
    echo "$APP_NAME runs on Linux only." >&2
    exit 1
fi

if [ ! -d "$REPO_ROOT/src/nexon_store" ] || [ ! -f "$MANIFEST" ]; then
    echo "Please run install.sh from a clone of the $APP_NAME repository." >&2
    exit 1
fi

have() {
    command -v "$1" >/dev/null 2>&1
}

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif have sudo; then
        sudo "$@"
    else
        echo "Administrator rights are required for this step, but sudo was not found:" >&2
        echo "  $*" >&2
        return 1
    fi
}

install_dependencies() {
    if have flatpak && have flatpak-builder; then
        echo "Flatpak and flatpak-builder are already installed."
        return 0
    fi

    echo "Installing Flatpak and flatpak-builder for this distribution..."
    if have apt-get; then
        as_root apt-get update
        as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y flatpak flatpak-builder
    elif have dnf; then
        as_root dnf install -y flatpak flatpak-builder
    elif have yum; then
        as_root yum install -y flatpak flatpak-builder
    elif have pacman; then
        as_root pacman -S --needed --noconfirm flatpak flatpak-builder
    elif have zypper; then
        as_root zypper --non-interactive install flatpak flatpak-builder
    elif have apk; then
        as_root apk add flatpak flatpak-builder
    elif have xbps-install; then
        as_root xbps-install -Sy flatpak flatpak-builder
    elif have emerge; then
        as_root emerge --noreplace sys-apps/flatpak dev-util/flatpak-builder
    elif have eopkg; then
        as_root eopkg install flatpak flatpak-builder
    elif have nix-env; then
        nix-env -iA nixpkgs.flatpak nixpkgs.flatpak-builder
    else
        cat >&2 <<EOF
Could not detect a supported package manager.

Install Flatpak and flatpak-builder with your distribution's tools, then run:
  ./install.sh --no-deps
EOF
        return 1
    fi
}

setup_flathub() {
    echo "Making sure the Flathub remote is configured..."
    flatpak remote-add --user --if-not-exists flathub "$FLATHUB_REPO"
    if [ "$SYSTEM_SCOPE" -eq 1 ]; then
        as_root flatpak remote-add --system --if-not-exists flathub "$FLATHUB_REPO"
    fi
}

build_and_install() {
    echo "Building $APP_NAME. The first run downloads the GNOME runtime and SDK."
    if [ "$SYSTEM_SCOPE" -eq 1 ]; then
        as_root flatpak-builder --system --force-clean --install \
            --install-deps-from=flathub --state-dir="$STATE_DIR" \
            "$BUILD_DIR" "$MANIFEST"
    else
        flatpak-builder --user --force-clean --install \
            --install-deps-from=flathub --state-dir="$STATE_DIR" \
            "$BUILD_DIR" "$MANIFEST"
    fi
}

if [ "$SKIP_DEPS" -eq 0 ]; then
    if ! install_dependencies; then
        exit 1
    fi
fi

if ! have flatpak; then
    echo "Flatpak is not available. Install it and run this script again." >&2
    exit 1
fi

if ! have flatpak-builder; then
    echo "flatpak-builder is required to build the application from source." >&2
    echo "Most distributions package it as 'flatpak-builder'. You can also run" >&2
    echo "the app straight from the checkout with ./run.sh if GTK4 is available." >&2
    exit 1
fi

setup_flathub
build_and_install

cat <<EOF

$APP_NAME is installed.

  Launch:     flatpak run $APP_ID
  Menu entry: "$APP_NAME"
  Uninstall:  flatpak uninstall $APP_ID

EOF

if [ "$RUN_AFTER" -eq 1 ]; then
    echo "Starting $APP_NAME..."
    exec flatpak run "$APP_ID"
fi
