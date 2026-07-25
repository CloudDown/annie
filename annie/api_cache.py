"""Cache mémoire + disque JSON partagé (MAL Jikan, AniList GraphQL)."""

from __future__ import annotations

from pathlib import Path

from annie.cache import read_json, read_json_stale, write_json


class ApiDiskCache:
    """Cache TTL en mémoire avec persistance JSON."""

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
