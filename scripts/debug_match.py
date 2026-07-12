#!/usr/bin/env python3
"""Teste le matching épisode ↔ nom de fichier dans un torrent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import FIXTURES, print  # noqa: E402

from annie.parsing import match_episode_filename


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Debug match_episode_filename (batch SubsPlease, SxxExx, etc.)"
    )
    parser.add_argument("path", nargs="?", help="Chemin ou nom de fichier")
    parser.add_argument("-e", "--episode", type=int, help="Numéro d'épisode")
    parser.add_argument("-s", "--season", type=int, help="Saison")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Exécuter tous les cas tests/fixtures/match_filenames.json",
    )
    parser.add_argument("--failures-only", action="store_true")
    args = parser.parse_args(argv)

    if args.fixture:
        cases = json.loads(
            (FIXTURES / "match_filenames.json").read_text(encoding="utf-8")
        )
        ok = fail = 0
        for case in cases:
            result = match_episode_filename(
                case["path"],
                case["episode"],
                season=case.get("season"),
            )
            expected = case["match"]
            passed = result == expected
            if passed:
                ok += 1
            else:
                fail += 1
            if args.failures_only and passed:
                continue
            status = "OK" if passed else "FAIL"
            print(
                f"[{status}] {case['id']:24} ep={case['episode']} "
                f"want={expected} got={result}"
            )
            if not passed:
                print(f"         {case['path']}")
        print(f"\n{ok}/{ok + fail} OK")
        return 1 if fail else 0

    if not args.path or args.episode is None:
        parser.print_help()
        return 1

    result = match_episode_filename(args.path, args.episode, season=args.season)
    print(f"match = {result}")
    print(f"path  = {args.path}")
    print(f"target= S{args.season or '?'}E{args.episode:02d}")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
