"""fzf preview helper (invoked as python -m annie.preview <key>)."""

from __future__ import annotations

import sys

from annie.ui import preview_key


def main() -> None:
    preview_key(sys.argv[1] if len(sys.argv) > 1 else "")


if __name__ == "__main__":
    main()
