#!/usr/bin/env python3
"""Analyse cohérence intra-saison (fixture offline ou catalogue live)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import print  # noqa: E402

from annie.catalog import build_catalog_from_releases
from annie.season_coherence import assess_season_coherence, format_coherence_issue
from annie.types import MediaKind
from tests.helpers import entries_from_fixture, load_fixture, mal_release


def run_fixture(name: str) -> int:
    if not name.endswith(".json"):
        name = f"{name}.json"
    fixture = load_fixture(name)
    entries = entries_from_fixture(fixture)
    releases = [
        mal_release(
            mal_id=row["mal_id"],
            season=row["season"],
            episode_count=row["episode_count"],
            label=row["label"],
            queries=[fixture.get("query", "anime")],
        )
        for row in fixture["releases"]
    ]

    def fake_search(query: str, **kwargs):
        return entries

    sections = build_catalog_from_releases(
        releases,
        search=fake_search,
        category="1_2",
        filter_code="0",
    )

    print(f"Fixture: {name}")
    issues = 0
    for section in sections:
        if section.kind != MediaKind.EPISODE or not section.episodes:
            continue
        report = assess_season_coherence(section)
        print(
            f"\n== {section.label} — {report.episode_count} ep. "
            f"magnet={report.magnet_coverage:.0%} group={report.group_coverage:.0%} =="
        )
        if not report.inconsistent:
            print("  OK — cohérent")
            continue
        for outlier in report.outliers:
            issue = format_coherence_issue(outlier, season=section.season)
            print(f"  ! {issue}")
            issues += 1

    if issues:
        print(f"\n{issues} anomalie(s)")
        return 1
    print("\nOK — aucune anomalie")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cohérence magnet/groupe par saison")
    parser.add_argument(
        "--fixture",
        default="catalog_coherence_mixed.json",
        help="Fixture dans tests/fixtures/",
    )
    args = parser.parse_args()
    return run_fixture(args.fixture)


if __name__ == "__main__":
    raise SystemExit(main())
