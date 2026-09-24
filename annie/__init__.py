"""Annie — Nyaa, tri, stream torrent."""

from __future__ import annotations

import sys

__version__ = "0.5.0"


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


_configure_stdio()
