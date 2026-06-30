"""Chemins utilisateur multi-plateformes (Linux, Debian, Windows, macOS)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_WINDOWS_PROGRAM_DIRS: dict[str, tuple[str, ...]] = {
    "mpv": (
        r"C:\Program Files\mpv",
        r"C:\Program Files\MPV Player",
        r"C:\mpv",
    ),
    "vlc": (
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
    ),
    "ffplay": (
        r"C:\Program Files\ffmpeg\bin",
        r"C:\ffmpeg\bin",
    ),
}


def find_program(name: str) -> str | None:
    """Résout un exécutable (mpv, vlc, ffplay) ou un chemin absolu."""
    raw = name.strip().strip('"')
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_file():
        return str(candidate.resolve())

    found = shutil.which(raw)
    if found:
        return found

    if sys.platform != "win32":
        return None

    stem = Path(raw).stem.lower()
    exe_name = raw if raw.lower().endswith(".exe") else f"{stem}.exe"
    program_dirs = _WINDOWS_PROGRAM_DIRS.get(stem, ())
    for directory in program_dirs:
        path = Path(directory) / exe_name
        if path.is_file():
            return str(path.resolve())

    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if winget.is_dir():
            for match in winget.rglob(exe_name):
                if match.is_file():
                    return str(match.resolve())

    program_files = os.environ.get("ProgramFiles")
    if program_files:
        for match in Path(program_files).rglob(exe_name):
            if match.is_file() and match.name.lower() == exe_name.lower():
                return str(match.resolve())

    return None


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
