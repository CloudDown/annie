"""Helpers construction MalRelease (MAL / AllAnime)."""

from __future__ import annotations

from annie.types import MalRelease, MediaKind


def tv_release(
    *,
    mal_id: int,
    label: str,
    season: int,
    episode_count: int | None,
    nyaa_queries: list[str],
    sort_key: tuple[int | float, str],
    absolute_episode_offset: int = 0,
) -> MalRelease:
    return MalRelease(
        mal_id=mal_id,
        label=label,
        kind=MediaKind.EPISODE,
        season=season,
        episode_count=episode_count,
        nyaa_queries=nyaa_queries,
        sort_key=sort_key,
        absolute_episode_offset=absolute_episode_offset,
    )


def movie_release(
    *,
    mal_id: int,
    label: str,
    nyaa_queries: list[str],
    sort_key: tuple[int | float, str],
    episode_count: int | None = 1,
) -> MalRelease:
    return MalRelease(
        mal_id=mal_id,
        label=label,
        kind=MediaKind.MOVIE,
        season=None,
        episode_count=episode_count,
        nyaa_queries=nyaa_queries,
        sort_key=sort_key,
    )


def extra_release(
    *,
    mal_id: int,
    label: str,
    kind: MediaKind,
    nyaa_queries: list[str],
    sort_key: tuple[int | float, str],
    episode_count: int | None = None,
) -> MalRelease:
    return MalRelease(
        mal_id=mal_id,
        label=label,
        kind=kind,
        season=None,
        episode_count=episode_count,
        nyaa_queries=nyaa_queries,
        sort_key=sort_key,
    )
