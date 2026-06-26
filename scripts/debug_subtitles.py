#!/usr/bin/env python3
"""Debug recherche sous-titres OpenSubtitles (variantes + API)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import FIXTURES, print  # noqa: E402

from annie.parsing import parse_title
from annie.subtitles import (
    SubtitlesError,
    SubtitleQuery,
    build_query,
    language_for,
    probe_search,
    search,
    subtitle_title_variants,
)
from tests.helpers import result_item


def _query_from_case(case: dict) -> SubtitleQuery:
    item = result_item(
        case["nyaa_title"],
        season=case.get("season"),
        episode=case.get("episode"),
    )
    return build_query(item, series_title=case.get("series_title"))


def _print_variants(query: SubtitleQuery) -> None:
    variants = subtitle_title_variants(query.title, extra=query.extra_titles)
    print(f"primary : {query.title}")
    if query.extra_titles:
        print(f"extra   : {', '.join(query.extra_titles)}")
    print(f"target  : S{query.season or '?'}E{query.episode:02d}" if query.episode is not None else f"target  : S{query.season or '?'}E?")
    print("variantes:")
    for index, title in enumerate(variants, start=1):
        print(f"  {index:2}. {title}")


def _print_probe(query: SubtitleQuery, lang_code: str) -> int:
    lang = language_for(lang_code)
    if lang is None:
        print(f"langue inconnue: {lang_code}", file=sys.stderr)
        return 1
    try:
        probed = probe_search(query, lang)
    except SubtitlesError as exc:
        print(f"erreur: {exc}", file=sys.stderr)
        return 1

    total = 0
    print(f"\nprobe [{lang.label}]:")
    for title, candidates in probed:
        count = len(candidates)
        total = max(total, count)
        status = "HIT" if count else "miss"
        best = candidates[0] if candidates else None
        detail = f" → {best.release} ({best.downloads} dl)" if best else ""
        print(f"  [{status:4}] {count:3}  {title}{detail}")

    merged = search(query, lang)
    print(f"\nsearch() final: {len(merged)} sous-titre(s)")
    if merged:
        best = max(merged, key=lambda item: (item.downloads, item.file_id))
        print(f"  best: {best.release} ({best.downloads} dl, file_id={best.file_id})")
    return 0 if merged else 1


def _run_fixture(*, live: bool, failures_only: bool, lang_filter: str | None) -> int:
    cases = json.loads((FIXTURES / "subtitle_queries.json").read_text(encoding="utf-8"))
    ok = fail = 0

    for case in cases:
        query = _query_from_case(case)
        variants = subtitle_title_variants(query.title, extra=query.extra_titles)
        lowered = {value.casefold() for value in variants}
        variant_ok = all(
            expected.casefold() in lowered for expected in case.get("variants_contain", [])
        )

        if not live:
            passed = variant_ok
            if passed:
                ok += 1
            else:
                fail += 1
            if failures_only and passed:
                continue
            status = "OK" if passed else "FAIL"
            print(f"[{status}] {case['id']:32} variants={len(variants)}")
            if not passed:
                missing = [
                    value
                    for value in case.get("variants_contain", [])
                    if value.casefold() not in lowered
                ]
                print(f"         manquant: {', '.join(missing)}")
            continue

        langs = case.get("langs", ["fr"])
        if lang_filter:
            langs = [code for code in langs if code == lang_filter]
        case_ok = variant_ok
        for lang_code in langs:
            minimum = case.get("min_hits", {}).get(lang_code, 1)
            lang = language_for(lang_code)
            if lang is None:
                case_ok = False
                continue
            try:
                hits = len(search(query, lang))
            except SubtitlesError as exc:
                hits = 0
                print(f"[ERR ] {case['id']} [{lang_code}] {exc}")
            if hits < minimum:
                case_ok = False
                print(
                    f"[FAIL] {case['id']:32} [{lang_code}] "
                    f"hits={hits} want>={minimum} title={query.title!r}"
                )
        if case_ok:
            ok += 1
            if not failures_only:
                print(f"[OK  ] {case['id']:32} ({', '.join(langs)})")
        else:
            fail += 1

    print(f"\n{ok}/{ok + fail} OK")
    return 1 if fail else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Debug sous-titres OpenSubtitles (variantes offline, probe API live)"
    )
    parser.add_argument("query", nargs="?", help="Titre utilisateur ou requête (ex. re zero)")
    parser.add_argument(
        "--nyaa",
        help="Titre Nyaa complet pour build_query (ex. '[SubsPlease] Re Zero - 08 ...')",
    )
    parser.add_argument("-s", "--season", type=int, help="Saison")
    parser.add_argument("-e", "--episode", type=int, help="Épisode")
    parser.add_argument("-l", "--lang", default="fr", help="Code langue (fr, en, …)")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Exécuter tests/fixtures/subtitle_queries.json (offline: variantes)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Avec --fixture : interroger l'API OpenSubtitles (clé requise)",
    )
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON (probe d'une requête unique)",
    )
    args = parser.parse_args(argv)

    if args.fixture:
        return _run_fixture(
            live=args.live,
            failures_only=args.failures_only,
            lang_filter=args.lang if args.live else None,
        )

    if args.nyaa:
        parsed = parse_title(args.nyaa)
        season = args.season if args.season is not None else parsed.season
        episode = args.episode if args.episode is not None else parsed.episode
        item = result_item(args.nyaa, season=season, episode=episode)
        subtitle_query = build_query(item, series_title=args.query)
    elif args.query:
        subtitle_query = SubtitleQuery(
            title=args.query,
            season=args.season,
            episode=args.episode,
            kind="tv",
        )
    else:
        parser.print_help()
        return 1

    if args.json:
        lang = language_for(args.lang)
        if lang is None:
            return 1
        try:
            probed = probe_search(subtitle_query, lang)
        except SubtitlesError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1
        payload = {
            "query": subtitle_query.title,
            "season": subtitle_query.season,
            "episode": subtitle_query.episode,
            "lang": args.lang,
            "variants": [
                {
                    "title": title,
                    "hits": len(candidates),
                    "best": {
                        "release": candidates[0].release,
                        "downloads": candidates[0].downloads,
                        "file_id": candidates[0].file_id,
                    }
                    if candidates
                    else None,
                }
                for title, candidates in probed
            ],
            "final_hits": len(search(subtitle_query, lang)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["final_hits"] else 1

    _print_variants(subtitle_query)
    return _print_probe(subtitle_query, args.lang)


if __name__ == "__main__":
    raise SystemExit(main())
