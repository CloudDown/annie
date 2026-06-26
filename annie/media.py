"""Titres, tri, catalogue Nyaa — façade de compatibilité."""

from __future__ import annotations

from annie.catalog import (
    FRANCHISE_SEARCH_PAGES,
    MAX_FRANCHISE_QUERIES,
    PRIMARY_SEARCH_PAGES,
    build_catalog,
    build_catalog_from_releases,
    fill_catalog_gaps,
    fill_section_gaps,
    is_recap_movie,
    is_spinoff,
    parse_batch_episode_range,
    resolve_catalog_target,
    scope_releases_for_target,
)
from annie.config import CONFIG_DIR, CONFIG_FILE, AnnieConfig
from annie.parsing import (
    is_manga,
    match_episode_filename,
    minimal_label,
    minimal_filename,
    normalize,
    parse_title,
)
from annie.scoring import pick_best, rank_entry, target_match_score
from annie.types import (
    MalRelease,
    MediaKind,
    MediaSection,
    ParsedTitle,
    ResultItem,
    WatchTarget,
)

__all__ = [
    "AnnieConfig",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "FRANCHISE_SEARCH_PAGES",
    "MAX_FRANCHISE_QUERIES",
    "MalRelease",
    "MediaKind",
    "MediaSection",
    "PRIMARY_SEARCH_PAGES",
    "ParsedTitle",
    "ResultItem",
    "WatchTarget",
    "build_catalog",
    "build_catalog_from_releases",
    "fill_catalog_gaps",
    "fill_section_gaps",
    "is_manga",
    "is_recap_movie",
    "is_spinoff",
    "match_episode_filename",
    "minimal_label",
    "minimal_filename",
    "normalize",
    "parse_batch_episode_range",
    "parse_title",
    "pick_best",
    "rank_entry",
    "resolve_catalog_target",
    "scope_releases_for_target",
    "target_match_score",
]
