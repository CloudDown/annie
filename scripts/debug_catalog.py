#!/usr/bin/env python3
"""Construit et affiche un catalogue offline (fixture) ou via Nyaa/MAL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import print  # noqa: E402

from annie.catalog import build_catalog, build_catalog_from_releases
from annie.config import AnnieConfig
from annie.types import MediaKind
from tests.helpers import entries_from_fixture, load_fixture, mal_release


def print_catalog(sections) -> None:
    for section in sections:
        if section.kind != MediaKind.EPISODE:
            continue
        eps = sorted(section.episodes)
        expected = section.expected_episodes or "?"
        print(
            f"\n== {section.label} (S{section.season or '?'}) "
            f"{len(eps)}/{expected} épisodes =="
        )
        if eps:
            shown = eps[:12]
            suffix = "…" if len(eps) > 12 else ""
            print(f"  nums: {shown}{suffix}")
        for ep in eps[:5]:
            item = section.episodes[ep]
            print(f"  E{ep:02d}: {item.entry.title[:90]}")


def run_fixture(name: str) -> int:
    fixture = load_fixture(name)
    entries = entries_from_fixture(fixture)
    releases = [
        mal_release(
            mal_id=row["mal_id"],
            season=row["season"],
            episode_count=row["episode_count"],
            label=row["label"],
            queries=[fixture["query"]],
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
    print(f"Fixture: {name} ({len(entries)} entrées simulées)")
    print_catalog(sections)

    issues: list[str] = []
    by_season = {s.season: s for s in sections if s.kind == MediaKind.EPISODE}
    for season_str, rules in fixture.get("expectations", {}).items():
        season = int(season_str)
        section = by_season.get(season)
        if section is None:
            issues.append(f"S{season:02d} absente")
            continue
        for ep in rules.get("must_have", []):
            if ep not in section.episodes:
                issues.append(f"S{season:02d} E{ep:02d} manquant")
        for ep in rules.get("must_not_have", []):
            if ep in section.episodes:
                issues.append(f"S{season:02d} E{ep:02d} pollution")

    if issues:
        print("\nISSUES:")
        for issue in issues:
            print(f"  ! {issue}")
        return 1

    print("\nOK — toutes les attentes fixture satisfaites")
    return 0


def run_live(query: str, *, use_mal: bool) -> int:
    if use_mal:
        from annie.cli import gather_catalog

        config = AnnieConfig.load()
        sections, _ = gather_catalog(query, config)
        print(f"Catalogue MAL+Nyaa: {query}")
    else:
        from annie.nyaa import search

        entries = search(query)
        sections = build_catalog(entries, query)
        print(f"Catalogue Nyaa seul: {query} ({len(entries)} résultats)")

    print_catalog(sections)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Debug construction catalogue")
    parser.add_argument("query", nargs="?", help="Requête anime (réseau)")
    parser.add_argument(
        "--fixture",
        default="catalog_re_zero.json",
        help="Fixture JSON dans tests/fixtures/ (offline, défaut: catalog_re_zero.json)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Utiliser uniquement la fixture (pas de réseau)",
    )
    parser.add_argument(
        "--mal",
        action="store_true",
        help="Avec --query: passer par gather_catalog (MAL+Nyaa)",
    )
    parser.add_argument("--json", action="store_true", help="Résumé JSON")
    args = parser.parse_args(argv)

    if args.offline or not args.query:
        code = run_fixture(args.fixture)
        if args.json:
            print(json.dumps({"fixture": args.fixture, "ok": code == 0}))
        return code

    return run_live(args.query, use_mal=args.mal)


if __name__ == "__main__":
    raise SystemExit(main())
