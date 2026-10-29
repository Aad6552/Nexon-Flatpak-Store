# Nexon Flatpak Store

A small, native application store for Flathub that works on every Linux
distribution. Browse and search the Flathub catalogue, install applications,
open them, remove them again, and keep everything up to date — without tying
yourself to one distribution's software centre.

Nexon Flatpak Store is written in Python with GTK 4 and libadwaita, ships as a
Flatpak, and talks to the public Flathub API plus your own `flatpak` command
line. It is prepared to be submitted to Flathub itself.

![Icon](data/icons/hicolor/256x256/apps/io.github.aad6552.NexonFlatpakStore.png)

## Features

* **Discover** — browse Flathub's recently updated, popular and trending apps,
  or any category from Audio & Video to Utilities, with endless "Load more".
* **Search** — instant, debounced search across the whole Flathub catalogue.
* **App details** — description, screenshots, verified-publisher badge, licence,
  latest version and release notes before you install anything.
* **Install, open, remove** — one click each, with progress and readable error
  messages from flatpak.
* **Updates** — a dedicated tab listing every pending update, with "Update" per
  app and "Update all" in one go.
* **Sandbox aware** — when running as a Flatpak it drives the host's `flatpak`
  through `flatpak-spawn`, so it manages your real system rather than a sandbox.
* **No bundled runtimes or web views** — a few thousand lines of Python on top
  of what the GNOME runtime already provides.

## Requirements

* Linux (any distribution) with Flatpak support.
* `flatpak-builder` to build the application, which `install.sh` installs for you.
* An internet connection (Flathub API, icons and screenshots).
* For running from source only: Python 3.9+, PyGObject, GTK 4.10+ and
  libadwaita 1.2+ (normally `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`).

Flatpak applications are installed per user by default, so no root password is
needed to install apps through the store.

## Install

```bash
git clone https://github.com/Aad6552/Nexon-Flatpak-Store.git
cd Nexon-Flatpak-Store
./install.sh
```

`install.sh` detects your distribution's package manager (`apt`, `dnf`, `yum`,
`pacman`, `zypper`, `apk`, `xbps-install`, `emerge`, `eopkg`, `nix-env`),
installs Flatpak and flatpak-builder if needed, adds the Flathub remote, builds
the Flatpak and installs it for your user. The first build downloads the GNOME
runtime and SDK (roughly 1 GB), so give it a few minutes.

Options:

| Option | Effect |
| --- | --- |
| `--run` | Start the store as soon as it is installed |
| `--system` | Install for all users instead of just you (uses sudo) |
| `--no-deps` | Skip installing Flatpak/flatpak-builder automatically |
| `-h`, `--help` | Show usage |

Start it afterwards from your application menu ("Nexon Flatpak Store") or with:

```bash
flatpak run io.github.aad6552.NexonFlatpakStore
```

Uninstall with:

```bash
flatpak uninstall io.github.aad6552.NexonFlatpakStore
```

### Manual Flatpak build

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50
flatpak-builder --user --install --force-clean \
  --state-dir=.flatpak-builder/state \
  .flatpak-builder/build io.github.aad6552.NexonFlatpakStore.json
```

### Build a single-file bundle

```bash
flatpak-builder --repo=repo --force-clean build-dir io.github.aad6552.NexonFlatpakStore.json
flatpak build-bundle repo nexon-flatpak-store.flatpak io.github.aad6552.NexonFlatpakStore
flatpak install --user nexon-flatpak-store.flatpak
```

## Run from source

`run.sh` picks the best available option on its own:

```bash
./run.sh            # source if GTK4 is installed, else the Flatpak, else build it
./run.sh --source   # force running the Python sources in this checkout
./run.sh --flatpak  # force running the installed Flatpak
./run.sh --build    # build with flatpak-builder and run it in one step
```

## Usage

| Where | What you can do |
| --- | --- |
| **Discover** | Pick a collection or category in the drop-down, scroll, press *Load more*. Type in the search box to search all of Flathub. Click any card for details. |
| **Details** | Read the description, screenshots and details, then *Install*, or *Open* / *Update* / *Remove* if it is already installed. |
| **Installed** | Every installed application with its version, update and remove buttons. |
| **Updates** | Pending updates for all installed applications, plus *Update all*. |
| **Menu** | Refresh everything, refresh Flathub metadata (`flatpak update --appstream`), update all, About, Quit. |

Keyboard shortcuts: <kbd>Ctrl</kbd>+<kbd>F</kbd> search,
<kbd>Ctrl</kbd>+<kbd>R</kbd> refresh, <kbd>Ctrl</kbd>+<kbd>Q</kbd> quit.

## How it works

### Flathub

The store only uses Flathub's public, key-less API:

| Endpoint | Used for |
| --- | --- |
| `POST /api/v2/search` | searching the catalogue |
| `GET /api/v2/appstream/{app-id}` | description, screenshots, releases, verification |
| `GET /api/v2/collection/{recently-updated,popular,trending}` | browse pages |
| `GET /api/v2/collection/category/{Category}` | category browse pages |
| `https://dl.flathub.org/repo/appstream/x86_64/icons/...` | icons of installed apps |

Everything is fetched on background threads; the UI updates on the GTK main
loop. Icons and screenshots are cached in `~/.cache/nexon-flatpak-store`
(`~/.var/app/io.github.aad6552.NexonFlatpakStore/cache` when sandboxed).

### Flatpak

The store never manipulates Flatpak's files directly. It calls:

```text
flatpak list --app --columns=application,name,version,branch,installation,origin,size
flatpak --installation=<name> remote-ls --updates --columns=application,branch,version
flatpak --installation=<name> install -y --noninteractive flathub <app-id>
flatpak --installation=<name> uninstall -y --noninteractive <app-id>
flatpak --installation=<name> update -y --noninteractive [<app-id>]
flatpak run <app-id>
```

Because the store itself is sandboxed, these run through
`flatpak-spawn --host` when `/.flatpak-info` exists. Installs go to the user
installation; updates and removals use the installation the app already lives in
(`user` or `system`), so nothing is duplicated and no password is needed for
user installs.

### Permissions

| Permission | Why |
| --- | --- |
| `--share=network` | Flathub API, icons and screenshots |
| `--talk-name=org.freedesktop.Flatpak` | drive the host `flatpak` CLI through `flatpak-spawn --host`, which is the only way a sandboxed app can manage host installations |
| `--share=ipc`, `--socket=wayland`, `--socket=fallback-x11`, `--device=dri` | standard GTK 4 display access |

There is no filesystem access beyond the default, and no background service.

## Project structure

```text
.
├── bin/nexon-flatpak-store                 # portable launcher (source + installed)
├── data/
│   ├── io.github.aad6552.NexonFlatpakStore.desktop
│   ├── io.github.aad6552.NexonFlatpakStore.metainfo.xml
│   └── icons/hicolor/{64,128,256}x*/apps/  # application icon
├── flathub/io.github.aad6552.NexonFlatpakStore.json  # tagged-source manifest for Flathub
├── io.github.aad6552.NexonFlatpakStore.json          # local manifest (source dir)
├── install.sh                              # cross-distribution installer
├── run.sh                                  # source / Flatpak / build runner
├── src/nexon_store/
│   ├── application.py                      # Adw.Application, actions, About
│   ├── window.py                           # GTK4 + libadwaita interface
│   ├── api.py                              # Flathub API client (stdlib only)
│   ├── flatpak.py                          # flatpak CLI wrapper (stdlib only)
│   └── paths.py                            # cache locations
└── tests/test_core.py                      # headless unit tests
```

## Development

```bash
python3 -m unittest discover -s tests -v   # parsing, command building, API mapping
./run.sh --source                          # iterate without rebuilding the Flatpak
```

`api.py` and `flatpak.py` deliberately import nothing but the standard library,
so they can be tested without GTK or a network connection.

## Publishing on Flathub

This repository is structured for Flathub (app ID `io.github.aad6552.NexonFlatpakStore`
matches the GitHub account `Aad6552`, AppStream metadata and icons are included,
and the store's dependencies all come from the GNOME runtime). It is **not yet
published** on Flathub. Before opening a submission pull request:

1. Add at least one screenshot to
   `data/io.github.aad6552.NexonFlatpakStore.metainfo.xml` (hosted URLs are
   simplest, for example `raw.githubusercontent.com` links to PNGs in this repo).
   Flathub requires screenshots for the app page.
2. Tag the release and push the tag, which the submission manifest references:
   ```bash
   git tag -a v1.0.0 -m "Nexon Flatpak Store 1.0.0"
   git push origin v1.0.0
   ```
3. Validate locally:
   ```bash
   flatpak-builder-lint manifest flathub/io.github.aad6552.NexonFlatpakStore.json
   flatpak run --command=appstreamcli org.gnome.Sdk//50 \
     validate --no-net data/io.github.aad6552.NexonFlatpakStore.metainfo.xml
   ```
4. Submit to Flathub: fork `flathub/flathub`, create a branch named
   `io.github.aad6552.NexonFlatpakStore`, copy in
   `flathub/io.github.aad6552.NexonFlatpakStore.json` (named
   `io.github.aad6552.NexonFlatpakStore.json`), and open a pull request.
5. In the pull request, explain the `--talk-name=org.freedesktop.Flatpak`
   permission: the application is a Flatpak manager, so it must invoke the host
   `flatpak` command through `flatpak-spawn` instead of touching host files.

The `flathub/` manifest builds from the `v1.0.0` git tag, while the manifest in
the repository root builds from the working directory for local development.

## Troubleshooting

* **"The flatpak command was not found"** — Flatpak is not installed, or (inside
  the sandbox) the `org.freedesktop.Flatpak` permission was removed. Install
  Flatpak or reinstall the app with `./install.sh`.
* **Installing fails with authorisation errors** — system-wide installations
  need polkit approval that a sandboxed app cannot show. The store installs to
  your user account on purpose; for system installs use
  `flatpak install --system` in a terminal.
* **No application appears in the menu after building** — log out and back in,
  or run `flatpak run io.github.aad6552.NexonFlatpakStore`.
* **Icons or descriptions stay empty** — check the network permission with
  Flatseal, and make sure `flathub.org` is reachable.
* **Updates list is empty but `flatpak update` finds some** — press the refresh
  button; the list comes from `flatpak remote-ls --updates`, which needs the
  remote metadata to be current (`flatpak update --appstream`).

## License

MIT — see [LICENSE](LICENSE).

Nexon Flatpak Store is not affiliated with Flathub or the Flatpak project.
