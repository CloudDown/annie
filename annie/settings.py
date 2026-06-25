"""Réglages utilisateur Annie (streaming, lecture)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

CONFIG_DIR = Path.home() / ".config" / "annie"
SETTINGS_FILE = CONFIG_DIR / "settings.toml"
_settings_cache: AnnieSettings | None = None

DEFAULT_SETTINGS_TOML = """\
# Réglages Annie — ~/.config/annie/settings.toml

[streaming]
# Partager l'épisode en cours pendant la lecture (pièces déjà téléchargées).
seed_while_watching = true
"""


@dataclass
class AnnieSettings:
    seed_while_watching: bool = True

    @classmethod
    def load(cls) -> AnnieSettings:
        global _settings_cache
        if _settings_cache is not None:
            return replace(_settings_cache)

        if not SETTINGS_FILE.is_file():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(DEFAULT_SETTINGS_TOML, encoding="utf-8")

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
