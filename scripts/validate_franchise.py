#!/usr/bin/env python3
"""Valide saisons/épisodes MAL + couverture Nyaa sans lancer mpv."""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

print = partial(print, flush=True)

from annie.cli import gather_catalog
from annie.mal import collect_franchise, franchise_to_releases, pick_candidate, search_anime
from annie.media import AnnieConfig, MediaKind

QUERIES = [
    "frieren",
    "danmachi",
    "made in abyss",
    "chainsaw man",
    "jujutsu kaisen",
    "one punch man",
    "mob psycho 100",
    "re zero",
    "konosuba",
    "tensura",
    "attack on titan",
    "steins gate",
    "vinland saga",
    "spy x family",
    "boku no hero academia",
    "demon slayer",
    "fullmetal alchemist brotherhood",
    "hunter x hunter",
    "bleach",
    "naruto",
    "cowboy bebop",
    "evangelion",
    "code geass",
    "overlord",
    "toradora",
    "clannad",
    "soul eater",
    "blue lock",
    "bocchi the rock",
    "oshi no ko",
]


def mal_seasons(query: str) -> tuple[str, list[tuple[str, int | None]]] | None:
    candidates = search_anime(query)
    chosen = pick_candidate(candidates, query) if candidates else None
    if chosen is None:
        return None
    franchise = collect_franchise(chosen.mal_id)
    releases = franchise_to_releases(
        franchise,
        root_id=chosen.mal_id,
        user_query=query,
    )
    tv = [(r.label, r.episode_count) for r in releases if r.kind == MediaKind.EPISODE]
    title = chosen.title_english or chosen.title
    return title, tv


def validate_one(query: str, config: AnnieConfig) -> dict:
    t0 = time.monotonic()
    result: dict = {"query": query, "ok": True, "issues": []}

    mal = mal_seasons(query)
    if mal is None:
        result["ok"] = False
        result["issues"].append("MAL: aucun résultat")
        result["elapsed"] = time.monotonic() - t0
        return result

    title, mal_tv = mal
    result["title"] = title
    result["mal_seasons"] = len(mal_tv)
    result["mal_detail"] = mal_tv

    try:
        catalog, _ = gather_catalog(query, config)
    except Exception as exc:
        result["ok"] = False
        result["issues"].append(f"catalog: {exc}")
        result["elapsed"] = time.monotonic() - t0
        return result

    nyaa_tv = [s for s in catalog if s.kind == MediaKind.EPISODE and s.season is not None]
    nyaa_tv.sort(key=lambda s: s.season or 0)
    result["nyaa_seasons"] = len(nyaa_tv)
    result["nyaa_detail"] = []

    mal_by_season = {i + 1: ep for i, (_, ep) in enumerate(mal_tv)}

    if len(nyaa_tv) != len(mal_tv):
        result["ok"] = False
        result["issues"].append(
            f"saisons: MAL={len(mal_tv)} Nyaa={len(nyaa_tv)}"
        )

    for section in nyaa_tv:
        season = section.season or 0
        found = len(section.episodes)
        expected = mal_by_season.get(season) or section.expected_episodes
        row = (section.label, found, expected)
        result["nyaa_detail"].append(row)
        if expected and found < expected:
            result["ok"] = False
            result["issues"].append(f"{section.label}: {found}/{expected} épisodes")

    for season, expected in mal_by_season.items():
        if not any(s.season == season for s in nyaa_tv):
            result["ok"] = False
            result["issues"].append(f"saison {season:02d} absente du catalogue Nyaa")

    result["elapsed"] = time.monotonic() - t0
    return result


def main() -> int:
    queries = QUERIES[:30]
    if len(sys.argv) > 1:
        queries = sys.argv[1:]

    config = AnnieConfig()
    results: list[dict] = []
    workers = min(2, len(queries))

    print(f"Validation de {len(queries)} anime (workers={workers})…\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(validate_one, q, config): q for q in queries}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            status = "OK" if result["ok"] else "FAIL"
            title = result.get("title", "?")
            mal_n = result.get("mal_seasons", "?")
            nyaa_n = result.get("nyaa_seasons", "?")
            elapsed = result.get("elapsed", 0)
            print(f"[{status}] {result['query']:28} {title[:40]:40} MAL={mal_n} Nyaa={nyaa_n} ({elapsed:.0f}s)")
            for issue in result.get("issues", []):
                print(f"       ↳ {issue}")

    results.sort(key=lambda r: r["query"])
    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    print(f"\n{'=' * 60}")
    print(f"Résultat: {ok}/{len(results)} OK, {fail} échecs")

    if fail:
        print("\nDétail des échecs:")
        for r in results:
            if r["ok"]:
                continue
            print(f"\n  {r['query']} — {r.get('title', '?')}")
            for label, found, expected in r.get("nyaa_detail", []):
                print(f"    {label}: {found}/{expected or '?'}")
            for issue in r["issues"]:
                print(f"    ! {issue}")

    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
