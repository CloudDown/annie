"""Scoring, matching et évaluation qualité des releases."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from annie.nyaa import NyaaEntry
from annie.parsing import (
    CODEC_SCORES,
    FINAL_SEASON_RE,
    PREFERRED_GROUPS,
    RESOLUTION_SCORES,
    SOURCE_SCORES,
    is_manga,
    parse_title,
    target_match_score,
    title_marks_season,
)
from annie.season_coherence import assess_season_coherence, format_coherence_issue
from annie.types import MediaKind, MediaSection, ParsedTitle, ResultItem, WatchTarget


def _merged_preferred_groups() -> dict[str, int]:
    from annie.config import AnnieConfig

    groups = dict(PREFERRED_GROUPS)
    cfg = AnnieConfig.load_cached().catalog
    for name in cfg.preferred_groups:
        groups[name.lower()] = cfg.preferred_group_bonus
    return groups


def torrent_quality_score(title: str, release_group: str | None) -> int:
    """Score qualité torrent (résolution, source, codec, groupe)."""
    score = 0
    for pattern, points in RESOLUTION_SCORES:
        if pattern.search(title):
            score += points
            break
    for pattern, points in SOURCE_SCORES:
        if pattern.search(title):
            score += points
            break
    for pattern, points in CODEC_SCORES:
        if pattern.search(title):
            score += points
            break
    if release_group:
        score += _merged_preferred_groups().get(release_group.lower(), 0)
    pref = _preferred_resolution()
    if pref and pref != "auto":
        from annie.parsing import resolution_tag

        tag = resolution_tag(title)
        if tag == pref:
            score += 20
        elif tag:
            score -= 8
    if re.search(r"\brepack\b", title, re.I):
        score -= 5
    if re.search(r"\bdual[\s-]?audio\b", title, re.I):
        score += 2
    return score


def _preferred_resolution() -> str:
    from annie.config import AnnieConfig

    raw = getattr(AnnieConfig.load_cached().catalog, "preferred_resolution", "auto")
    value = str(raw or "auto").lower()
    if value not in {"auto", "720p", "1080p", "2160p"}:
        return "auto"
    return value


def _catalog_thresholds() -> tuple[int, int, int, int]:
    from annie.config import AnnieConfig

    cfg = AnnieConfig.load_cached().catalog
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


def _filter_entry(
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
    filtered = _filter_entry(entry, target, match_queries=match_queries)
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
        filtered = _filter_entry(entry, target, match_queries=queries)
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


@dataclass(frozen=True)
class EpisodeAssessment:
    season: int | None
    episode: int
    seeders: int
    quality: int
    resolution: str | None
    release_group: str | None
    title: str
    flags: tuple[str, ...] = ()

    @property
    def strict_ok(self) -> bool:
        blocked = {
            "dead",
            "low_seeders",
            "low_quality",
            "directors_cut",
            "suspect_source",
            "season_unmarked",
        }
        return not blocked.intersection(self.flags)

    @property
    def relaxed_ok(self) -> bool:
        return "dead" not in self.flags


@dataclass
class SeasonQualityReport:
    label: str
    season: int | None
    expected: int | None
    found: int
    episodes: list[EpisodeAssessment] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    coherence_outliers: list[str] = field(default_factory=list)

    @property
    def strict_ok(self) -> bool:
        return not self.issues and all(ep.strict_ok for ep in self.episodes)

    @property
    def relaxed_ok(self) -> bool:
        return all(ep.relaxed_ok for ep in self.episodes)


@dataclass
class CatalogQualityReport:
    seasons: list[SeasonQualityReport]
    issues: list[str] = field(default_factory=list)

    @property
    def strict_ok(self) -> bool:
        return not self.issues and all(season.strict_ok for season in self.seasons)

    @property
    def relaxed_ok(self) -> bool:
        return not any(
            issue.startswith("season") or issue.startswith("seasons")
            for issue in self.issues
        ) and all(season.relaxed_ok for season in self.seasons)


def assess_episode_item(
    item: ResultItem, *, season: int | None = None
) -> EpisodeAssessment:
    episode = item.parsed.episode
    if episode is None:
        raise ValueError("episode required")

    flags = variant_flags(item.entry.title)
    seeders = item.entry.seeders
    quality = item.parsed.quality
    min_strict, min_relaxed, qual_strict, qual_relaxed = _catalog_thresholds()

    if seeders < min_relaxed:
        flags.append("dead")
    elif seeders < min_strict:
        flags.append("low_seeders")

    if quality < qual_relaxed:
        flags.append("low_quality")
    elif quality < qual_strict:
        flags.append("sd_quality")

    # S≥2 sans marqueur de saison dans le titre → risque de pollution S1.
    effective_season = season if season is not None else item.parsed.season
    if (
        effective_season is not None
        and effective_season >= 2
        and not title_marks_season(item.entry.title, effective_season)
        and item.parsed.source_episode is None
        and not FINAL_SEASON_RE.search(item.entry.title)
    ):
        flags.append("season_unmarked")

    return EpisodeAssessment(
        season=season if season is not None else item.parsed.season,
        episode=episode,
        seeders=seeders,
        quality=quality,
        resolution=item.parsed.resolution,
        release_group=item.parsed.release_group,
        title=item.entry.title,
        flags=tuple(dict.fromkeys(flags)),
    )


def _format_episode_issue(assessment: EpisodeAssessment) -> str:
    min_strict, _, _, _ = _catalog_thresholds()
    parts: list[str] = []
    if "dead" in assessment.flags or "low_seeders" in assessment.flags:
        parts.append(f"{assessment.seeders}S (min {min_strict})")
    if "low_quality" in assessment.flags or "sd_quality" in assessment.flags:
        res = assessment.resolution or "?"
        parts.append(f"quality {res}/{assessment.quality}")
    for flag in ("directors_cut", "new_edition", "suspect_source", "season_unmarked"):
        if flag in assessment.flags:
            parts.append(flag.replace("_", " "))
    detail = ", ".join(parts) if parts else "ok"
    season = assessment.season or 0
    return f"S{season:02d}E{assessment.episode:02d}: {detail}"


def assess_tv_catalog(
    mal_tv: list[tuple[str, int | None]],
    nyaa_tv: list[MediaSection],
    *,
    coverage_relaxed: float | None = None,
) -> CatalogQualityReport:
    """Couverture MAL + seeders/qualité par épisode catalogue."""
    if coverage_relaxed is None:
        from annie.config import AnnieConfig

        coverage_relaxed = AnnieConfig.load_cached().catalog.coverage_relaxed
    issues: list[str] = []
    mal_by_season = {index + 1: count for index, (_, count) in enumerate(mal_tv)}
    season_reports: list[SeasonQualityReport] = []

    if len(nyaa_tv) != len(mal_tv):
        issues.append(f"seasons: MAL={len(mal_tv)} Nyaa={len(nyaa_tv)}")

    for section in sorted(nyaa_tv, key=lambda s: s.season or 0):
        season = section.season or 0
        expected = mal_by_season.get(season) or section.expected_episodes
        found = len(section.episodes)
        report = SeasonQualityReport(
            label=section.label,
            season=section.season,
            expected=expected,
            found=found,
        )

        if expected and found < expected:
            issues.append(f"{section.label}: {found}/{expected} episodes")
            if found < max(1, int(expected * coverage_relaxed)):
                report.issues.append(f"{section.label}: couverture {found}/{expected}")

        for episode in sorted(section.episodes):
            item = section.episodes[episode]
            assessment = assess_episode_item(item, season=section.season)
            report.episodes.append(assessment)
            if not assessment.strict_ok:
                report.issues.append(_format_episode_issue(assessment))
            elif not assessment.relaxed_ok:
                report.issues.append(_format_episode_issue(assessment))

        coherence = assess_season_coherence(section)
        if coherence.inconsistent:
            for outlier in coherence.outliers:
                issue = format_coherence_issue(outlier, season=section.season)
                report.coherence_outliers.append(issue)
                report.issues.append(issue)

        season_reports.append(report)

    for season, expected in mal_by_season.items():
        if not any(s.season == season for s in nyaa_tv):
            issues.append(f"season {season:02d} missing from Nyaa catalog")

    return CatalogQualityReport(seasons=season_reports, issues=issues)
