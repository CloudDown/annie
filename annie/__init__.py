"""Annie — Nyaa, tri, stream torrent."""

from __future__ import annotations

import os
import sys

__version__ = "0.5.0"


def _enable_windows_ansi() -> None:
    """Active les séquences ANSI sur la console Windows classique (conhost).

    Windows Terminal les gère nativement, mais cmd.exe/PowerShell dans conhost
    (défaut Windows 10) affiche du garbage sans ENABLE_VIRTUAL_TERMINAL_PROCESSING.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        for std_handle in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(std_handle)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _configure_stdio() -> None:
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
    _enable_windows_ansi()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


_configure_stdio()
