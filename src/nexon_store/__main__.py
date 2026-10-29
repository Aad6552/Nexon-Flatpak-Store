"""Entry point for ``python3 -m nexon_store``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from .application import StoreApplication

    application = StoreApplication()
    return application.run(argv if argv is not None else [sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
