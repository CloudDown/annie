"""Configuration Annie (~/.config/annie/config.toml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

CONFIG_DIR = Path.home() / ".config" / "annie"
CONFIG_FILE = CONFIG_DIR / "config.toml"
_config_cache: "AnnieConfig | None" = None


@dataclass
class AnnieConfig:
    player: str = "auto"
    category: str = "0_0"
    filter_code: str = "0"
    skip_recap_movies: bool = False
    preferred_groups: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "AnnieConfig":
        global _config_cache
        if _config_cache is not None:
            return replace(_config_cache)
        data: dict = {}
        if CONFIG_FILE.is_file():
            data = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        _config_cache = cls(
            player=os.environ.get("ANNIE_PLAYER", data.get("player", "auto")),
            category=data.get("category", "0_0"),
            filter_code=str(data.get("filter", data.get("filter_code", "0"))),
            skip_recap_movies=bool(data.get("skip_recap_movies", False)),
            preferred_groups=list(data.get("preferred_groups", [])),
        )
        return replace(_config_cache)

    def resolved_player(self, override: str | None = None) -> str | None:
        if override and override != "auto":
            return override
        if self.player != "auto":
            return self.player
        return None
