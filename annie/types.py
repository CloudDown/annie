"""Types partagés catalogue Annie."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from annie.nyaa import NyaaEntry


class MediaKind(str, Enum):
    EPISODE = "episode"
    MOVIE = "movie"
    OVA = "ova"
    SPECIAL = "special"
    BATCH = "batch"
    MANGA = "manga"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedTitle:
    raw: str
    release_group: str | None
    series: str
    display_name: str
    kind: MediaKind
    season: int | None
    episode: int | None
    arc: str | None
    quality: int
    resolution: str | None
    is_repack: bool
    # Numéro d'épisode d'origine (avant remap saison/absolu) — sert à
    # retrouver le fichier dans le torrent (ex. S4E01 affiché, fichier « - 67 »).
    source_episode: int | None = None


@dataclass(frozen=True)
class WatchTarget:
    query: str
    season: int | None = None
    episode: int | None = None
    kind: MediaKind | None = None


@dataclass(frozen=True)
class ResultItem:
    entry: NyaaEntry
    parsed: ParsedTitle
    score: float


@dataclass
class MediaSection:
    key: str
    label: str
    kind: MediaKind
    season: int | None
    arc: str | None = None
    batch_recommended: bool = False
    expected_episodes: int | None = None
    mal_id: int | None = None
    absolute_episode_offset: int = 0
    nyaa_queries: list[str] = field(default_factory=list)
    episodes: dict[int, ResultItem] = field(default_factory=dict)
    singles: list[ResultItem] = field(default_factory=list)

    @property
    def has_episodes(self) -> bool:
        return bool(self.episodes)

    def choices(self) -> list[ResultItem]:
        if self.has_episodes:
            return [self.episodes[n] for n in sorted(self.episodes)]
        return sorted(self.singles, key=lambda item: item.score, reverse=True)


@dataclass(frozen=True)
class MalRelease:
    mal_id: int
    label: str
    kind: MediaKind
    season: int | None
    episode_count: int | None
    nyaa_queries: list[str]
    sort_key: tuple[int, str]
    absolute_episode_offset: int = 0
