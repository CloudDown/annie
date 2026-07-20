"""Historique de visionnage (~/.config/annie/history.toml)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from annie import toml_util
from annie.paths import config_dir, ensure_directory
from annie.types import MediaSection, ResultItem

HISTORY_FILE = config_dir() / "history.toml"


def watch_key(
    *,
    mal_id: int | None,
    section_key: str,
    season: int | None,
    episode: int,
) -> str:
    season_num = season or 1
    if mal_id:
        return f"mal:{mal_id}:{season_num}:{episode}"
    return f"{section_key}:{season_num}:{episode}"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class WatchHistory:
    entries: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> WatchHistory:
        data = toml_util.read_toml(HISTORY_FILE)
        table = toml_util.table(data, "watched")
        entries = {
            str(key): str(value)
            for key, value in table.items()
            if value is not None
        }
        return cls(entries=entries)

    def is_watched(
        self,
        *,
        mal_id: int | None,
        section_key: str,
        season: int | None,
        episode: int | None,
    ) -> bool:
        if episode is None:
            return False
        key = watch_key(
            mal_id=mal_id, section_key=section_key, season=season, episode=episode
        )
        return key in self.entries

    def mark_item(self, section: MediaSection, item: ResultItem) -> None:
        episode = item.parsed.episode
        if episode is None:
            return
        key = watch_key(
            mal_id=section.mal_id,
            section_key=section.key,
            season=item.parsed.season or section.season,
            episode=episode,
        )
        self.entries[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save()

    def save(self) -> None:
        ensure_directory(HISTORY_FILE.parent)
        lines = [
            "# Annie — historique de visionnage\n",
            "# Clés : mal:<id>:<saison>:<épisode> ou <section>:<saison>:<épisode>\n",
            "[watched]\n",
        ]
        for key in sorted(self.entries):
            lines.append(f"{_toml_string(key)} = {_toml_string(self.entries[key])}\n")
        HISTORY_FILE.write_text("".join(lines), encoding="utf-8")
        try:
            HISTORY_FILE.chmod(0o600)
        except OSError:
            pass
