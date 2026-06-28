"""Configuration Annie (~/.config/annie/config.toml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from annie.user_config import CONFIG_DIR, CONFIG_FILE, ensure_user_config

_config_cache: "AnnieConfig | None" = None


def _load_config_data() -> dict:
    ensure_user_config()
    if not CONFIG_FILE.is_file():
        return {}
    return tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))


@dataclass
class AnnieConfig:
    player: str = "auto"
    category: str = "0_0"
    filter_code: str = "0"
    skip_recap_movies: bool = False
    preferred_groups: list[str] = field(default_factory=list)
    subtitles_enabled: bool = True
    default_sub_lang: str = ""
    opensubtitles_api_key: str = ""
    opensubtitles_username: str = ""
    opensubtitles_password: str = ""

    @classmethod
    def load(cls) -> "AnnieConfig":
        global _config_cache
        if _config_cache is not None:
            return replace(_config_cache)
        data = _load_config_data()
        _config_cache = cls(
            player=os.environ.get("ANNIE_PLAYER", data.get("player", "auto")),
            category=data.get("category", "0_0"),
            filter_code=str(data.get("filter", data.get("filter_code", "0"))),
            skip_recap_movies=bool(data.get("skip_recap_movies", False)),
            preferred_groups=list(data.get("preferred_groups", [])),
            subtitles_enabled=bool(data.get("subtitles_enabled", True)),
            default_sub_lang=str(data.get("default_sub_lang", "")).strip(),
            opensubtitles_api_key=str(data.get("opensubtitles_api_key", "")).strip(),
            opensubtitles_username=str(data.get("opensubtitles_username", "")).strip(),
            opensubtitles_password=str(data.get("opensubtitles_password", "")).strip(),
        )
        return replace(_config_cache)

    def resolved_player(self, override: str | None = None) -> str | None:
        if override and override != "auto":
            return override
        if self.player != "auto":
            return self.player
        return None
