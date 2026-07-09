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
