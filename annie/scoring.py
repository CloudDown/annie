"""Scoring, matching et évaluation qualité des releases."""

from __future__ import annotations

import re

from annie.nyaa import NyaaEntry
from annie.parsing import (
    is_manga,
    parse_title,
    target_match_score,
)
from annie.types import MediaKind, ParsedTitle, ResultItem, WatchTarget

MIN_SEEDERS_STRICT = 10
MIN_SEEDERS_RELAXED = 3
MIN_QUALITY_STRICT = 26
MIN_QUALITY_RELAXED = 12


def _catalog_thresholds() -> tuple[int, int, int, int]:
    from annie.config import AnnieConfig

    cfg = AnnieConfig.load().catalog
    return (
        cfg.min_seeders_strict,
        cfg.min_seeders_relaxed,
        cfg.min_quality_strict,
        cfg.min_quality_relaxed,
    )

_VARIANT_RULES: tuple[tuple[re.Pattern[str], str, int], ...] = (
    (re.compile(r"director'?s?[\s._-]*cut", re.I), "directors_cut", 2),
    (re.compile(r"\bnew\s+edition\b", re.I), "new_edition", 1),
    (re.compile(r"\b(?:unofficial|cam|hdcam)\b", re.I), "suspect_source", 2),
)


def variant_tier(title: str) -> int:
    """0 = release standard, plus haut = variante moins désirable."""
    tier = 0
    for pattern, _, value in _VARIANT_RULES:
        if pattern.search(title):
            tier = max(tier, value)
    return tier


def variant_flags(title: str) -> list[str]:
    return [name for pattern, name, _ in _VARIANT_RULES if pattern.search(title)]


def catalog_episode_pick_rank(item: ResultItem) -> tuple:
    """Tri catalogue / pick épisode : vivant → seeders → qualité → variante → confiance → match."""
    _, min_relaxed, _, _ = _catalog_thresholds()
    seeders = item.entry.seeders
    batch_pack = (
        1
        if parse_title(item.entry.title).kind == MediaKind.BATCH
        else 0
    )
    return (
        seeders >= min_relaxed,
        batch_pack,
        seeders,
        item.parsed.quality,
        -variant_tier(item.entry.title),
        1 if item.entry.trusted else 0,
        int(item.score),
    )


def filter_entry(
    entry: NyaaEntry,
    target: WatchTarget,
    *,
    match_queries: list[str] | None = None,
) -> tuple[int, ParsedTitle] | None:
    """Filtres durs + score de pertinence titre (sans seeders)."""
    if is_manga(entry.title):
        return None
    parsed = parse_title(entry.title)
    if parsed.kind == MediaKind.MANGA:
        return None

    match_score = target_match_score(parsed, target, match_queries=match_queries)
    if match_score is None:
        return None

    if parsed.is_repack:
        match_score -= 1
    if parsed.kind == MediaKind.UNKNOWN:
        match_score -= 2

    return match_score, parsed


def rank_entry(
    entry: NyaaEntry,
    target: WatchTarget,
    *,
    match_queries: list[str] | None = None,
) -> tuple[float, ParsedTitle] | None:
    """Retourne (match_score, parsed) si le torrent passe les filtres."""
    filtered = filter_entry(entry, target, match_queries=match_queries)
    if filtered is None:
        return None
    match_score, parsed = filtered
    return float(match_score), parsed


def _search_pick_rank(item: ResultItem) -> tuple:
    """Recherche sans épisode précis : pertinence titre puis seeders."""
    return (
        int(item.score),
        item.entry.seeders,
        item.parsed.quality,
        -variant_tier(item.entry.title),
        1 if item.entry.trusted else 0,
    )


def pick_best(
    entries: list[NyaaEntry],
    target: WatchTarget,
    *,
    match_queries: list[str] | None = None,
) -> tuple[NyaaEntry, ParsedTitle] | None:
    queries = match_queries
    candidates: list[ResultItem] = []
    for entry in entries:
        filtered = filter_entry(entry, target, match_queries=queries)
        if filtered is None:
            continue
        match_score, parsed = filtered
        candidates.append(
            ResultItem(entry=entry, parsed=parsed, score=float(match_score))
        )

    if not candidates:
        return None

    if target.episode is not None:
        candidates.sort(key=catalog_episode_pick_rank, reverse=True)
    else:
        candidates.sort(key=_search_pick_rank, reverse=True)

    best = candidates[0]
    return best.entry, best.parsed
