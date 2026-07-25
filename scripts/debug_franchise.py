#!/usr/bin/env python3
"""Rapport détaillé pour un seul anime (MAL vs catalogue Annie)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import print  # noqa: E402

from annie.catalog import gather_catalog
from annie.config import AnnieConfig
from annie import metadata as meta
from annie.mal import franchise_to_releases, pick_candidate
from annie.types import MediaKind


def diagnose(query: str, config: AnnieConfig) -> dict:
    t0 = time.monotonic()
    report: dict = {"query": query, "issues": []}

    candidates = meta.search_anime(query)
    chosen = pick_candidate(candidates, query) if candidates else None
    if chosen is None:
        report["issues"].append("metadata: aucun candidat")
        report["ok"] = False
        return report

    report["mal_title"] = chosen.title_english or chosen.title
    report["mal_id"] = chosen.mal_id
    report["anilist_id"] = chosen.anilist_id

    franchise = meta.collect_franchise(chosen)
    releases = franchise_to_releases(franchise, root_id=chosen.mal_id, user_query=query)
    tv = [r for r in releases if r.kind == MediaKind.EPISODE]
    report["mal_seasons"] = [
        {"label": r.label, "season": r.season, "episodes": r.episode_count} for r in tv
    ]

    catalog, options = gather_catalog(query, config, confirm_anime=False)
    report["inline_options"] = options
    nyaa_tv = [
        s for s in catalog if s.kind == MediaKind.EPISODE and s.season is not None
    ]
    nyaa_tv.sort(key=lambda s: s.season or 0)
    report["nyaa_seasons"] = []
    for section in nyaa_tv:
        eps = sorted(section.episodes)
        missing = []
        if section.expected_episodes:
            missing = [
                ep
                for ep in range(1, section.expected_episodes + 1)
                if ep not in section.episodes
            ]
        report["nyaa_seasons"].append(
            {
                "label": section.label,
                "season": section.season,
                "found": len(eps),
                "expected": section.expected_episodes,
                "offset": section.absolute_episode_offset,
                "missing": missing[:20],
                "sample_titles": [
                    section.episodes[ep].entry.title[:100] for ep in eps[:3]
                ],
            }
        )
        if section.expected_episodes and len(eps) < section.expected_episodes:
            report["issues"].append(
                f"{section.label}: {len(eps)}/{section.expected_episodes}"
            )

    report["ok"] = not report["issues"]
    report["elapsed_s"] = round(time.monotonic() - t0, 1)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic détaillé MAL ↔ catalogue pour un anime"
    )
    parser.add_argument("query", help="Ex: re zero, frieren")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = diagnose(args.query, AnnieConfig.load())

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("ok") else 1

    print(f"Query: {report['query']}")
    print(f"MAL:   {report.get('mal_title')} (id={report.get('mal_id')})")
    print(f"Time:  {report.get('elapsed_s')}s\n")

    print("MAL saisons:")
    for row in report.get("mal_seasons", []):
        print(f"  S{row['season']:02d}  {row['episodes']} ep  {row['label']}")

    print("\nCatalogue Annie:")
    for row in report.get("nyaa_seasons", []):
        miss = row["missing"]
        miss_hint = (
            f"  manque: {miss[:8]}{'…' if len(miss) > 8 else ''}" if miss else ""
        )
        print(
            f"  S{row['season']:02d}  {row['found']}/{row['expected']} ep  "
            f"offset={row['offset']}{miss_hint}"
        )
        for title in row.get("sample_titles", []):
            print(f"       · {title}")

    if report["issues"]:
        print("\nIssues:")
        for issue in report["issues"]:
            print(f"  ! {issue}")
        return 1

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
