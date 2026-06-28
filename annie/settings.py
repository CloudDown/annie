"""Réglages utilisateur Annie (streaming, lecture)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from annie.user_config import SETTINGS_FILE, ensure_user_config

_settings_cache: AnnieSettings | None = None


@dataclass
class AnnieSettings:
    seed_while_watching: bool = True

    @classmethod
    def load(cls) -> AnnieSettings:
        global _settings_cache
        if _settings_cache is not None:
            return replace(_settings_cache)

        ensure_user_config()

        data: dict = {}
        if SETTINGS_FILE.is_file():
            data = tomllib.loads(SETTINGS_FILE.read_text(encoding="utf-8"))

        streaming = data.get("streaming", data)
        seed_while_watching = bool(streaming.get("seed_while_watching", True))

        env = os.environ.get("ANNIE_SEED_WHILE_WATCHING", "").strip().lower()
        if env in {"0", "false", "no", "off"}:
            seed_while_watching = False
        elif env in {"1", "true", "yes", "on"}:
            seed_while_watching = True

        _settings_cache = cls(seed_while_watching=seed_while_watching)
        return replace(_settings_cache)
