"""Cohérence intra-saison (magnet, release group, seeders)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from annie.types import MediaSection, ResultItem


@dataclass(frozen=True)
class EpisodeOutlier:
    episode: int
    reason: str  # magnet | release_group | seeders
    dominant_magnet: str | None
    dominant_group: str | None


@dataclass
class SeasonCoherenceReport:
    season: int | None
    episode_count: int
    dominant_magnet: str | None
    dominant_group: str | None
    magnet_coverage: float
    group_coverage: float
    outliers: list[EpisodeOutlier] = field(default_factory=list)

    @property
    def inconsistent(self) -> bool:
        return bool(self.outliers)


def _dominant(counter: Counter[str]) -> tuple[str | None, float]:
    if not counter:
        return None, 0.0
    total = sum(counter.values())
    if total <= 0:
        return None, 0.0
    value, count = counter.most_common(1)[0]
    return value, count / total


def assess_season_coherence(
    section: MediaSection,
    *,
    coherence_min_share: float = 0.60,
    seeders_ratio_threshold: float = 0.50,
) -> SeasonCoherenceReport:
    """Compare les épisodes d'une saison et signale les anomalies."""
    episodes = section.episodes
    count = len(episodes)
    if count == 0:
        return SeasonCoherenceReport(
            season=section.season,
            episode_count=0,
            dominant_magnet=None,
            dominant_group=None,
            magnet_coverage=0.0,
            group_coverage=0.0,
        )

    magnet_counter: Counter[str] = Counter()
    group_counter: Counter[str] = Counter()
    by_magnet: dict[str, list[ResultItem]] = {}

    for item in episodes.values():
        magnet_counter[item.entry.magnet] += 1
        group = (item.parsed.release_group or "").lower()
        if group:
            group_counter[group] += 1
        by_magnet.setdefault(item.entry.magnet, []).append(item)

    dominant_magnet, magnet_coverage = _dominant(magnet_counter)
    dominant_group, group_coverage = _dominant(group_counter)

    outliers: list[EpisodeOutlier] = []
    median_seeders = 0.0
    if dominant_magnet and dominant_magnet in by_magnet:
        median_seeders = float(
            median(i.entry.seeders for i in by_magnet[dominant_magnet])
        )

    for ep, item in sorted(episodes.items()):
        magnet = item.entry.magnet
        group = (item.parsed.release_group or "").lower()
        is_magnet_outlier = (
            dominant_magnet
            and magnet != dominant_magnet
            and magnet_coverage >= coherence_min_share
        )

        if is_magnet_outlier:
            outliers.append(
                EpisodeOutlier(
                    episode=ep,
                    reason="magnet",
                    dominant_magnet=dominant_magnet,
                    dominant_group=dominant_group,
                )
            )
            continue

        if (
            dominant_group
            and group
            and group != dominant_group
            and group_coverage >= coherence_min_share
        ):
            outliers.append(
                EpisodeOutlier(
                    episode=ep,
                    reason="release_group",
                    dominant_magnet=dominant_magnet,
                    dominant_group=dominant_group,
                )
            )
            continue

        if (
            dominant_magnet
            and magnet == dominant_magnet
            and median_seeders > 0
            and item.entry.seeders < median_seeders * seeders_ratio_threshold
        ):
            outliers.append(
                EpisodeOutlier(
                    episode=ep,
                    reason="seeders",
                    dominant_magnet=dominant_magnet,
                    dominant_group=dominant_group,
                )
            )

    return SeasonCoherenceReport(
        season=section.season,
        episode_count=count,
        dominant_magnet=dominant_magnet,
        dominant_group=dominant_group,
        magnet_coverage=magnet_coverage,
        group_coverage=group_coverage,
        outliers=outliers,
    )


def format_coherence_issue(outlier: EpisodeOutlier, *, season: int | None) -> str:
    season_label = f"S{season or 0:02d}"
    if outlier.reason == "magnet":
        return f"{season_label}E{outlier.episode:02d}: outlier magnet"
    if outlier.reason == "release_group":
        return (
            f"{season_label}E{outlier.episode:02d}: outlier release group "
            f"(dominant: {outlier.dominant_group})"
        )
    return f"{season_label}E{outlier.episode:02d}: outlier seeders"
