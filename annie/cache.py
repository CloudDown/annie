"""Cache disque léger (JSON + TTL)."""

from __future__ import annotations

import json
import time
from pathlib import Path


def read_json(path: Path, *, ttl: float) -> dict | list | None:
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_json_stale(path: Path) -> dict | list | None:
    """Lit un JSON même si le TTL est dépassé (fallback hors-ligne)."""
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: dict | list) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


class ApiDiskCache:
    """Cache TTL mémoire + JSON disque (MAL / AniList)."""

    def __init__(self, disk_dir: Path, *, ttl: float, memory: dict | None = None):
        self.disk_dir = disk_dir
        self.ttl = ttl
        self._memory = memory if memory is not None else {}

    def disk_path(self, key: str) -> Path:
        safe = key.strip("/").replace("/", "_").replace(" ", "_")
        return self.disk_dir / f"{safe}.json"

    def get(self, key: str, *, ttl: float | None = None) -> dict | None:
        hit = self._memory.get(key)
        if hit is not None:
            return hit
        effective_ttl = self.ttl if ttl is None else ttl
        disk = read_json(self.disk_path(key), ttl=effective_ttl)
        if disk is not None:
            self._memory[key] = disk
            return disk
        return None

    def get_stale(self, key: str) -> dict | None:
        stale = read_json_stale(self.disk_path(key))
        if isinstance(stale, dict):
            self._memory[key] = stale
            return stale
        return None

    def store(self, key: str, payload: dict) -> dict:
        self._memory[key] = payload
        write_json(self.disk_path(key), payload)
        return payload
