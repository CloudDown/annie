"""Chemins utilisateur multi-plateformes (Linux, Debian, Windows, macOS)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _windows_roaming() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    return Path.home() / "AppData" / "Roaming"


def _windows_local() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local)
    return Path.home() / "AppData" / "Local"


def config_dir() -> Path:
    """Répertoire de configuration (XDG sur Unix, %APPDATA% sur Windows)."""
    if sys.platform == "win32":
        return _windows_roaming() / "annie"
    if sys.platform == "darwin":
        return Path.home() / ".config" / "annie"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "annie"
    return Path.home() / ".config" / "annie"


def cache_dir() -> Path:
    """Répertoire de cache (XDG sur Unix, %LOCALAPPDATA% sur Windows)."""
    if sys.platform == "win32":
        return _windows_local() / "annie" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / ".cache" / "annie"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "annie"
    return Path.home() / ".cache" / "annie"


def venv_python(project_root: Path) -> Path | None:
    """Interpréteur Python du venv local (uv sync), si présent."""
    if sys.platform == "win32":
        candidate = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = project_root / ".venv" / "bin" / "python3"
        if not candidate.is_file():
            candidate = project_root / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def mpv_ipc_path(ipc_dir: Path) -> Path:
    """Chemin socket/pipe pour le IPC mpv."""
    token = f"annie-{os.getpid()}-{int(__import__('time').time() * 1000)}"
    if sys.platform == "win32":
        return Path(rf"\\.\pipe\{token}")
    path = ipc_dir / f"{token}.sock"
    if path.exists():
        path.unlink()
    return path


def ipc_ready(ipc_path: Path) -> bool:
    """True si le socket/pipe IPC mpv est prêt."""
    if sys.platform == "win32":
        try:
            with open(ipc_path, "rb", buffering=0):
                return True
        except OSError:
            return False
    return ipc_path.is_socket()
