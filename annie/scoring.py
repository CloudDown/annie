"""Scoring et matching des releases."""

from __future__ import annotations

import math
import re
from pathlib import Path

from annie.nyaa import NyaaEntry
from annie.parsing import (
    is_manga,
    parse_title,
    target_match_score,
)
from annie.types import MediaKind, ParsedTitle, WatchTarget

def rank_entry(
    entry: NyaaEntry,
    target: WatchTarget,
    *,
    match_queries: list[str] | None = None,
) -> tuple[float, ParsedTitle] | None:
    if is_manga(entry.title):
        return None
    parsed = parse_title(entry.title)
    if parsed.kind == MediaKind.MANGA:
        return None
    title_score = target_match_score(parsed, target, match_queries=match_queries)
    if title_score is None:
        return None

    seed_score = math.log1p(entry.seeders) * 20
    quality = parsed.quality
    trusted_bonus = 8 if entry.trusted else 0
    batch_penalty = -30 if target.episode is not None and parsed.kind == MediaKind.BATCH else 0
    repack_penalty = -8 if parsed.is_repack else 0
    unknown_penalty = -40 if parsed.kind == MediaKind.UNKNOWN else 0

    total = title_score * 1000 + seed_score + quality + trusted_bonus + batch_penalty + repack_penalty + unknown_penalty
    return total, parsed


def pick_best(entries: list[NyaaEntry], target: WatchTarget) -> tuple[NyaaEntry, ParsedTitle] | None:
    ranked: list[tuple[float, NyaaEntry, ParsedTitle]] = []
    for entry in entries:
        result = rank_entry(entry, target)
        if result is None:
            continue
        score, parsed = result
        ranked.append((score, entry, parsed))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, best, parsed = ranked[0]
    return best, parsed

