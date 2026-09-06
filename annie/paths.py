"""User paths (XDG on Linux, ~/.config and ~/.cache on macOS)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


def find_program(name: str) -> str | None:
    raw = name.strip().strip('"')
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_file():
        return str(candidate.resolve())

    return shutil.which(raw)


def config_dir() -> Path:
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "annie"
    return Path.home() / ".config" / "annie"


def cache_dir() -> Path:
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "annie"
    return Path.home() / ".cache" / "annie"


def venv_python(project_root: Path) -> Path | None:
    candidate = project_root / ".venv" / "bin" / "python3"
    if not candidate.is_file():
        candidate = project_root / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def mpv_ipc_path(ipc_dir: Path) -> Path:
    token = f"annie-{os.getpid()}-{int(time.time() * 1000)}"
    path = ipc_dir / f"{token}.sock"
    if path.exists():
        path.unlink()
    return path


def ipc_ready(ipc_path: Path) -> bool:
    return ipc_path.is_socket()


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def path_exists(path: Path) -> bool:
    return path.exists()


def path_open(path: Path, mode: str = "rb"):
    return path.open(mode)
