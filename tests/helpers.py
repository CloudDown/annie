"""Factories et chargement de fixtures pour les tests Annie."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from annie.nyaa import NyaaEntry
from annie.parsing import parse_title
from annie.types import MalRelease, MediaKind, ResultItem

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def nyaa_entry(
    title: str, *, seeders: int = 50, magnet: str | None = None
) -> NyaaEntry:
    return NyaaEntry(
        title=title,
        magnet=magnet or f"magnet:?xt=urn:btih:{abs(hash(title)) % 10**16:016x}",
        size="500 MiB",
        date="2024-01-01",
        seeders=seeders,
        leechers=1,
        downloads=100,
        trusted=True,
    )


def result_item(title: str, *, score: float = 100.0, **parsed_kw: Any) -> ResultItem:
    parsed = parse_title(title)
    if parsed_kw:
        parsed = replace(parsed, **parsed_kw)
    return ResultItem(entry=nyaa_entry(title), parsed=parsed, score=score)


def mal_release(
    *,
    mal_id: int,
    season: int,
    episode_count: int,
    label: str | None = None,
    queries: list[str] | None = None,
) -> MalRelease:
    label = label or f"Season {season:02d}"
    return MalRelease(
        mal_id=mal_id,
        label=label,
        kind=MediaKind.EPISODE,
        season=season,
        episode_count=episode_count,
        nyaa_queries=queries or ["re zero"],
        sort_key=(season, label.lower()),
    )


def entries_from_fixture(fixture: dict) -> list[NyaaEntry]:
    entries: list[NyaaEntry] = []
    for row in fixture.get("entries", []):
        if isinstance(row, str):
            entries.append(nyaa_entry(row))
            continue
        entries.append(
            nyaa_entry(
                row["title"],
                seeders=row.get("seeders", 50),
                magnet=row.get("magnet"),
            )
        )
    return entries
