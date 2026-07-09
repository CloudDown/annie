"""Compat tests — réexporte l'évaluation qualité depuis annie.scoring."""

from __future__ import annotations

from annie.scoring import (  # noqa: F401
    MIN_QUALITY_RELAXED,
    MIN_QUALITY_STRICT,
    MIN_SEEDERS_RELAXED,
    MIN_SEEDERS_STRICT,
    CatalogQualityReport,
    EpisodeAssessment,
    SeasonQualityReport,
    assess_episode_item,
    assess_tv_catalog,
    catalog_episode_rank,
    catalog_episode_score,
)
