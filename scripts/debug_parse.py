#!/usr/bin/env python3
"""Affiche le parsing d'un ou plusieurs titres Nyaa."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import FIXTURES, print  # noqa: E402

from annie.catalog import parse_batch_episode_range
from annie.parsing import minimal_label, parse_title


def show_title(title: str) -> None:
    parsed = parse_title(title)
    batch_season, batch_eps = parse_batch_episode_range(title)
    print(f"TITLE: {title}")
    print(f"  kind     = {parsed.kind.value}")
    print(f"  season   = {parsed.season}")
    print(f"  episode  = {parsed.episode}")
    print(f"  arc      = {parsed.arc}")
    print(f"  group    = {parsed.release_group}")
    print(f"  quality  = {parsed.resolution or parsed.quality}")
    print(f"  label    = {minimal_label(parsed)}")
    if batch_eps:
        print(f"  batch    = S{batch_season:02d} E{batch_eps[0]:02d}–E{batch_eps[-1]:02d} ({len(batch_eps)} ep)")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Debug parse_title / batch range")
    parser.add_argument("titles", nargs="*", help="Titres Nyaa à parser")
    parser.add_argument(
        "--fixture",
        choices=["parse_titles"],
        help="Charger les cas depuis tests/fixtures/",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args(argv)

    titles: list[str] = list(args.titles)
    if args.fixture:
        data = json.loads((FIXTURES / f"{args.fixture}.json").read_text(encoding="utf-8"))
        titles.extend(row["title"] for row in data)

    if not titles:
        parser.print_help()
        return 1

    if args.json:
        rows = []
        for title in titles:
            p = parse_title(title)
            season, eps = parse_batch_episode_range(title)
            rows.append(
                {
                    "title": title,
                    "kind": p.kind.value,
                    "season": p.season,
                    "episode": p.episode,
                    "batch_season": season,
                    "batch_eps": eps,
                    "label": minimal_label(p),
                }
            )
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    for title in titles:
        show_title(title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
