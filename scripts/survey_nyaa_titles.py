#!/usr/bin/env python3
"""Collecte des titres Nyaa à grande échelle et synthèse de patterns pour le parsing.

Récupère des milliers de releases (top MAL + requêtes), parse chaque titre,
agrège les motifs récurrents et produit un JSON + rapport Markdown pour analyse
(manuelle ou IA) avant d'améliorer annie/parsing.py et annie/catalog.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie.catalog import (
    is_franchise_multi_season_batch,
    parse_batch_episode_range,
)
from annie.mal import TopAnimeEntry, fetch_top_anime, nyaa_queries_for, search_anime
from annie.media import AnnieConfig
from annie.nyaa import NyaaEntry, search
from annie.parsing import minimal_label, parse_title
from annie.types import MediaKind

RESULTS_DIR = ROOT / "scripts" / "results"

# Signaux structurels (comptage pour le rapport patterns)
TITLE_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("s01e05", re.compile(r"\bS0?\d{1,2}E\d{1,3}\b", re.I)),
    ("s01_only", re.compile(r"\bS0?(\d{1,2})\b", re.I)),
    ("season_word", re.compile(r"\bseasons?\s*\d", re.I)),
    ("seasons_span", re.compile(r"\bseasons?\s*\d+\s*[-–—~]\s*\d+", re.I)),
    ("ep_dash", re.compile(r"[-–—]\s*\d{1,3}\b")),
    ("ep_tilde", re.compile(r"\d{1,3}\s*~\s*\d{1,3}")),
    ("ep_range_paren", re.compile(r"\(\s*\d{1,3}\s*[-–—~]\s*\d{1,3}\s*\)")),
    ("batch_word", re.compile(r"\bbatch\b", re.I)),
    ("complete", re.compile(r"\b(?:complete|full)\s+(?:series|collection)?\b", re.I)),
    ("movie", re.compile(r"\bmovie\b", re.I)),
    ("ova", re.compile(r"\bova\b", re.I)),
    ("special", re.compile(r"\bspecial\b", re.I)),
    ("dual_audio", re.compile(r"\bdual[\s._-]*audio\b", re.I)),
    ("multi_sub", re.compile(r"\bmultiple\s+subtitle", re.I)),
    ("alias_pipe", re.compile(r"\s\|\s")),
    ("bracket_hash", re.compile(r"\[[A-Fa-f0-9]{8}\]")),
    ("resolution", re.compile(r"\b(?:1080|720|2160|4k|8k)p\b", re.I)),
    ("arc_name", re.compile(r"\b(?:arc|cour)\s*\d", re.I)),
    ("ordinal_season", re.compile(r"\b\d(?:st|nd|rd|th)\s+season\b", re.I)),
)


@dataclass
class SurveyRow:
    title: str
    source_query: str
    seeders: int
    trusted: bool
    kind: str
    season: int | None
    episode: int | None
    arc: str | None
    release_group: str | None
    resolution: str | None
    label: str
    batch_season: int | None
    batch_ep_count: int
    batch_ep_first: int | None
    batch_ep_last: int | None
    franchise_multi_season: bool
    flags: list[str]
    signals: list[str]


def _nyaa_query_for_top(entry: TopAnimeEntry) -> str:
    candidates = search_anime(entry.title_english or entry.title)
    if candidates:
        base = candidates[0]
        queries = nyaa_queries_for(base, user_query=entry.title_english or entry.title)
        if queries:
            return queries[0]
    return entry.title_english or entry.title


def _analyze_entry(entry: NyaaEntry, source_query: str) -> SurveyRow:
    parsed = parse_title(entry.title)
    batch_season, batch_eps = parse_batch_episode_range(entry.title)
    flags: list[str] = []

    if parsed.kind == MediaKind.BATCH and not batch_eps:
        flags.append("batch_kind_empty_range")
    if batch_eps and parsed.kind != MediaKind.BATCH:
        flags.append("batch_range_non_batch_kind")
    if is_franchise_multi_season_batch(entry.title):
        flags.append("franchise_multi_season")
    if batch_eps and len(batch_eps) <= 3 and is_franchise_multi_season_batch(entry.title):
        flags.append("suspect_short_range_on_multi_season")
    if parsed.season is None and parsed.episode is not None and parsed.kind == MediaKind.EPISODE:
        flags.append("episode_without_season")
    if parsed.kind == MediaKind.UNKNOWN:
        flags.append("unknown_kind")

    signals = [name for name, pattern in TITLE_SIGNAL_PATTERNS if pattern.search(entry.title)]

    return SurveyRow(
        title=entry.title,
        source_query=source_query,
        seeders=entry.seeders,
        trusted=entry.trusted,
        kind=parsed.kind.value,
        season=parsed.season,
        episode=parsed.episode,
        arc=parsed.arc,
        release_group=parsed.release_group,
        resolution=parsed.resolution,
        label=minimal_label(parsed),
        batch_season=batch_season,
        batch_ep_count=len(batch_eps),
        batch_ep_first=batch_eps[0] if batch_eps else None,
        batch_ep_last=batch_eps[-1] if batch_eps else None,
        franchise_multi_season=is_franchise_multi_season_batch(entry.title),
        flags=flags,
        signals=signals,
    )


def _collect_for_query(
    query: str,
    *,
    pages: int,
    category: str,
    filter_code: str,
) -> list[NyaaEntry]:
    return search(query, category=category, filter_code=filter_code, pages=pages)


def _dedupe_entries(
    pairs: list[tuple[str, NyaaEntry]],
) -> list[tuple[str, NyaaEntry]]:
    seen: set[str] = set()
    out: list[tuple[str, NyaaEntry]] = []
    for query, entry in pairs:
        key = entry.title.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append((query, entry))
    return out


def _fetch_all(
    queries: list[str],
    *,
    pages: int,
    category: str,
    filter_code: str,
    workers: int,
) -> list[tuple[str, NyaaEntry]]:
    pairs: list[tuple[str, NyaaEntry]] = []
    workers = max(1, min(workers, len(queries) or 1))

    def _one(q: str) -> list[tuple[str, NyaaEntry]]:
        try:
            entries = _collect_for_query(
                q, pages=pages, category=category, filter_code=filter_code
            )
            return [(q, e) for e in entries]
        except Exception as exc:
            print(f"  ! {q}: {exc}", file=sys.stderr)
            return []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, q): q for q in queries}
        for future in as_completed(futures):
            pairs.extend(future.result())

    return _dedupe_entries(pairs)


def _aggregate(rows: list[SurveyRow]) -> dict:
    kind_counts = Counter(row.kind for row in rows)
    flag_counts = Counter(flag for row in rows for flag in row.flags)
    signal_counts = Counter(sig for row in rows for sig in row.signals)
    group_counts = Counter(
        row.release_group or "(none)" for row in rows if row.release_group
    )

    batch_sizes = Counter()
    for row in rows:
        if row.batch_ep_count:
            if row.batch_ep_count <= 3:
                batch_sizes["1-3"] += 1
            elif row.batch_ep_count <= 13:
                batch_sizes["4-13"] += 1
            elif row.batch_ep_count <= 26:
                batch_sizes["14-26"] += 1
            else:
                batch_sizes["27+"] += 1

    anomalies: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for flag in row.flags:
            if len(anomalies[flag]) < 8:
                anomalies[flag].append(row.title)

    signal_samples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for sig in row.signals[:3]:
            if len(signal_samples[sig]) < 5:
                signal_samples[sig].append(row.title)

    return {
        "total_titles": len(rows),
        "kind_distribution": dict(kind_counts.most_common()),
        "flag_counts": dict(flag_counts.most_common()),
        "signal_counts": dict(signal_counts.most_common()),
        "top_release_groups": dict(group_counts.most_common(25)),
        "batch_episode_bucket_counts": dict(batch_sizes),
        "anomaly_samples": dict(anomalies),
        "signal_samples": dict(signal_samples),
    }


def _render_report(
    meta: dict,
    summary: dict,
    rows: list[SurveyRow],
) -> str:
    lines = [
        "# Nyaa title survey — patterns parsing",
        "",
        f"- Généré : {meta['generated_at']}",
        f"- Titres uniques : {summary['total_titles']}",
        f"- Requêtes : {meta['query_count']}",
        f"- Pages Nyaa / requête : {meta['pages']}",
        "",
        "## Distribution `kind` (parse_title)",
        "",
    ]
    for kind, count in summary["kind_distribution"].items():
        pct = 100.0 * count / max(summary["total_titles"], 1)
        lines.append(f"- `{kind}` : {count} ({pct:.1f}%)")

    lines.extend(["", "## Signaux structurels (regex)", ""])
    for sig, count in summary["signal_counts"].items():
        lines.append(f"- `{sig}` : {count}")
        for sample in summary.get("signal_samples", {}).get(sig, [])[:2]:
            lines.append(f"  - `{sample[:100]}`")

    lines.extend(["", "## Anomalies / flags", ""])
    if not summary["flag_counts"]:
        lines.append("- (aucune)")
    for flag, count in summary["flag_counts"].items():
        lines.append(f"- **{flag}** : {count}")
        for sample in summary.get("anomaly_samples", {}).get(flag, []):
            lines.append(f"  - `{sample[:110]}`")

    lines.extend(["", "## Top release groups", ""])
    for group, count in summary["top_release_groups"].items():
        lines.append(f"- `{group}` : {count}")

    lines.extend(["", "## Tailles de batch détectées", ""])
    for bucket, count in summary.get("batch_episode_bucket_counts", {}).items():
        lines.append(f"- {bucket} épisodes : {count}")

    lines.extend(
        [
            "",
            "## Pistes pour améliorer le parsing",
            "",
            "1. **`batch_kind_empty_range`** — kind=BATCH mais `parse_batch_episode_range` vide "
            "(pack franchise « Seasons 1-2 », Complete Series, etc.).",
            "2. **`suspect_short_range_on_multi_season`** — plage 1-2 lue sur un titre "
            "multi-saisons (faux positif type Code Geass).",
            "3. **`batch_range_non_batch_kind`** — plage d'épisodes détectée mais kind≠batch.",
            "4. **`episode_without_season`** — épisode seul sans saison (heuristique S1?).",
            "5. Comparer les **signal_counts** avec les regex actuelles dans "
            "`annie/parsing.py` et `annie/catalog.py`.",
            "",
            "## Échantillon aléatoire (10 titres)",
            "",
        ]
    )
    step = max(len(rows) // 10, 1)
    for row in rows[::step][:10]:
        lines.append(f"- `[{row.kind}]` {row.title[:120]}")

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collecte titres Nyaa + rapport patterns pour le parsing"
    )
    parser.add_argument(
        "queries",
        nargs="*",
        help="Requêtes Nyaa explicites (sinon --top ou --from-validate-queries)",
    )
    parser.add_argument(
        "--top",
        type=int,
        metavar="N",
        help="Top N anime MAL → une recherche Nyaa par titre",
    )
    parser.add_argument(
        "--from-validate-queries",
        action="store_true",
        help="Utilise la liste QUERIES de validate_franchise.py",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limiter le nombre de requêtes (après --top / validate)",
    )
    parser.add_argument("--pages", type=int, default=2, help="Pages Nyaa par requête")
    parser.add_argument("--workers", type=int, default=4, help="Recherches parallèles")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON de sortie (défaut: scripts/results/nyaa_survey_<ts>.json)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Rapport Markdown (défaut: même nom que --output en .md)",
    )
    parser.add_argument("--json", action="store_true", help="Afficher le JSON sur stdout")
    args = parser.parse_args(argv)

    config = AnnieConfig.load()
    queries: list[str] = list(args.queries)

    if args.from_validate_queries:
        from validate_franchise import QUERIES  # noqa: WPS433

        queries.extend(QUERIES)

    if args.top:
        top_entries = fetch_top_anime(args.top)
        for entry in top_entries:
            queries.append(_nyaa_query_for_top(entry))

    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
    if args.limit > 0:
        queries = queries[: args.limit]

    if not queries:
        parser.error("aucune requête — passe des titres, --top N ou --from-validate-queries")

    t0 = time.monotonic()
    print(f"Collecte Nyaa : {len(queries)} requêtes × {args.pages} pages…")
    pairs = _fetch_all(
        queries,
        pages=args.pages,
        category=config.category,
        filter_code=config.filter_code,
        workers=args.workers,
    )
    rows = [_analyze_entry(entry, q) for q, entry in pairs]
    elapsed = round(time.monotonic() - t0, 1)
    print(f"  {len(rows)} titres uniques en {elapsed}s")

    summary = _aggregate(rows)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_json = args.output or (RESULTS_DIR / f"nyaa_survey_{stamp}.json")
    out_md = args.report or out_json.with_suffix(".md")

    payload = {
        "meta": {
            "generated_at": generated_at,
            "query_count": len(queries),
            "queries": queries,
            "pages": args.pages,
            "elapsed_s": elapsed,
        },
        "summary": summary,
        "rows": [asdict(row) for row in rows],
    }

    out_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    out_md.write_text(
        _render_report(payload["meta"], summary, rows), encoding="utf-8"
    )

    print(f"JSON   → {out_json}")
    print(f"Report → {out_md}")
    print(
        f"Flags  : {summary['flag_counts'] or '(aucun)'}",
    )

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
