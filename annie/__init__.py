"""Annie — Nyaa, tri, stream torrent."""

from __future__ import annotations

import os
import sys

__version__ = "0.5.0"


def _configure_stdio() -> None:
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


_configure_stdio()
