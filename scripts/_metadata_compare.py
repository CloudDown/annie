"""Helpers partagés scripts compare structure / AllAnime."""

from __future__ import annotations

from annie.types import MediaKind


def summarize_releases(releases) -> dict:
    tv = [
        {
            "season": r.season,
            "eps": r.episode_count,
            "label": r.label[:70],
        }
        for r in releases
        if r.kind == MediaKind.EPISODE
    ]
    movies = [
        {"label": r.label[:70], "eps": r.episode_count}
        for r in releases
        if r.kind == MediaKind.MOVIE
    ]
    extras = [
        {"kind": r.kind.name, "label": r.label[:60]}
        for r in releases
        if r.kind not in {MediaKind.EPISODE, MediaKind.MOVIE}
    ]
    return {
        "tv": tv,
        "movies": movies,
        "extras": extras,
        "n_tv": len(tv),
        "n_movies": len(movies),
        "n_extras": len(extras),
    }
