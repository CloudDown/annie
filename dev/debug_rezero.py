#!/usr/bin/env python3
"""Diagnostic Re:Zero — tests offline + option live MAL/Nyaa."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_offline_tests() -> int:
    print("=== Re:Zero offline (fixture) ===")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_catalog.ReZeroCatalogFixtureTests",
            "-v",
        ],
        cwd=ROOT,
    )
    return result.returncode


def run_live(season: int | None) -> int:
    print("=== Re:Zero live (MAL + Nyaa) ===")
    from annie.cli import gather_catalog
    from annie.config import AnnieConfig
    from annie.types import MediaKind

    cfg = AnnieConfig.load()
    kwargs: dict = {}
    if season is not None:
        kwargs["target_season"] = season
        print(f"Cible: saison {season}")

    catalog, meta = gather_catalog("re zero", cfg, **kwargs)
    chosen = meta.get("chosen")
    if chosen is not None:
        print(f"MAL: {chosen.title_english or chosen.title} ({chosen.mal_id})")

    tv = [s for s in catalog if s.kind == MediaKind.EPISODE and s.season]
    tv.sort(key=lambda section: section.season or 0)
    for section in tv:
        eps = sorted(section.episodes)
        expected = section.expected_episodes or "?"
        print(
            f"  S{section.season:02d}: {len(eps)}/{expected} ep"
            + (f"  [{eps[0]}..{eps[-1]}]" if eps else "")
        )

    if season is not None:
        target = next((s for s in tv if s.season == season), None)
        if target is None:
            print(f"ERREUR: saison {season} absente du catalogue")
            return 1
        if not target.episodes:
            print(f"ERREUR: saison {season} sans épisodes")
            return 1
        offset = target.absolute_episode_offset
        if offset != 66 and season == 4:
            print(f"AVERTISSEMENT: offset S4 = {offset} (attendu 66)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Interroger MAL/Nyaa (réseau requis)",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Limiter au scope d'une saison (ex. 4)",
    )
    args = parser.parse_args()

    code = run_offline_tests()
    if code != 0:
        return code
    if args.live:
        print()
        return run_live(args.season)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
