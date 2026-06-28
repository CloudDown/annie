#!/usr/bin/env python3
"""Valide saisons/épisodes MAL + couverture Nyaa sans lancer mpv."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie.cli import gather_catalog
from annie.scoring import assess_tv_catalog
from annie.mal import (
    TopAnimeEntry,
    collect_franchise,
    fetch_top_anime,
    franchise_to_releases,
    nyaa_queries_for,
    pick_candidate,
    search_anime,
)
from annie.media import AnnieConfig, MediaKind

RESULTS_DIR = ROOT / "scripts" / "results"
COVERAGE_RELAXED = 0.85

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
    "sword art online",
    "fairy tail",
    "black clover",
    "fire force",
    "violet evergarden",
    "mushoku tensei",
    "classroom of the elite",
    "horimiya",
    "darling in the franxx",
    "fate zero",
    "bakemonogatari",
    "gintama",
    "one piece",
    "haikyuu",
    "kuroko no basket",
    "slam dunk",
    "your lie in april",
    "angel beats",
    "anohana",
    "steins gate 0",
    "parasyte",
    "tokyo ghoul",
    "death note",
    "psycho pass",
    "golden kamuy",
    "dr stone",
    "black lagoon",
    "trigun stampede",
    "gurren lagann",
    "kill la kill",
    "quintessential quintuplets",
    "komi cant communicate",
    "rascal does not dream of bunny girl senpai",
    "hyouka",
    "grand blue dreaming",
    "nisekoi",
    "yuru camp",
    "non non biyori",
    "k-on",
    "lucky star",
    "madoka magica",
    "sailor moon",
    "cardcaptor sakura",
    "little busters",
    "ranking of kings",
    "to your eternity",
    "ascendance of a bookworm",
    "rising of the shield hero",
    "log horizon",
    "no game no life",
    "a certain scientific railgun",
    "erased",
    "noragami",
    "blue exorcist",
    "d gray man",
    "hellsing ultimate",
    "march comes in like a lion",
    "world trigger",
    "dandadan",
    "kaiju no 8",
    "sakamoto days",
    "hell's paradise",
    "wind breaker",
    "undead unluck",
    "solo leveling",
    "love live",
    "zombieland saga",
    "beastars",
    "86",
    "cyberpunk edgerunners",
    "odd taxi",
    "terror in resonance",
    "banana fish",
]


@dataclass(frozen=True)
class ValidateTarget:
    query: str
    mal_id: int | None = None
    rank: int | None = None
    anime_type: str | None = None


def _dedupe_targets(targets: list[ValidateTarget]) -> list[ValidateTarget]:
    seen: set[int | str] = set()
    unique: list[ValidateTarget] = []
    for target in targets:
        key: int | str = target.mal_id if target.mal_id is not None else target.query
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique


def _query_for_top(entry: TopAnimeEntry) -> str:
    return (entry.title_english or entry.title).strip()


def _pick_nyaa_query(candidates: list[str]) -> str:
    cleaned = [query.strip() for query in candidates if len(query.strip()) >= 3]
    if not cleaned:
        return ""
    return min(
        cleaned,
        key=lambda text: (
            10 if len(text.split()) == 1 else 0,
            5 if len(text) > 48 else 0,
            len(text.split()),
            len(text),
        ),
    )


def _franchise_search_query(
    franchise,
    mal_id: int,
    *,
    user_query: str = "",
) -> str:
    stripped = user_query.strip()
    if stripped and len(stripped.split()) <= 4 and len(stripped) <= 40:
        return stripped

    tv_nodes = sorted(
        [node for node in franchise if node.type == "TV" and not node.is_recap],
        key=lambda node: node.aired_from or "9999",
    )
    base = (
        tv_nodes[0]
        if tv_nodes
        else next(
            (node for node in franchise if node.mal_id == mal_id),
            franchise[0],
        )
    )
    candidates = nyaa_queries_for(base, user_query=stripped)
    picked = _pick_nyaa_query(candidates)
    if picked:
        return picked
    return base.title_english or base.title


def _mal_from_id(mal_id: int, fallback_query: str) -> tuple[str, list, str, str] | None:
    franchise = collect_franchise(mal_id)
    if not franchise:
        return None
    root = next((node for node in franchise if node.mal_id == mal_id), franchise[0])
    search_query = _franchise_search_query(franchise, mal_id, user_query=fallback_query)
    releases = franchise_to_releases(franchise, root_id=mal_id, user_query=search_query)
    tv = [(r.label, r.episode_count) for r in releases if r.kind == MediaKind.EPISODE]
    title = root.title_english or root.title
    return title, tv, root.type, search_query


def _mal_tv_from_id(
    mal_id: int, query: str
) -> tuple[str, list[tuple[str, int | None]], str, str] | None:
    data = _mal_from_id(mal_id, query)
    if data is None:
        return None
    title, tv, anime_type, search_query = data
    return title, tv, anime_type, search_query


def _mal_tv_from_query(
    query: str,
) -> tuple[str, list[tuple[str, int | None]], str, str] | None:
    candidates = search_anime(query)
    chosen = pick_candidate(candidates, query) if candidates else None
    if chosen is None:
        return None
    return _mal_tv_from_id(chosen.mal_id, query)


def _score_tv(
    mal_tv: list[tuple[str, int | None]],
    nyaa_tv: list,
) -> tuple[bool, bool, bool, bool, list[str], list[tuple[str, int, int | None]], dict]:
    report = assess_tv_catalog(mal_tv, nyaa_tv, coverage_relaxed=COVERAGE_RELAXED)
    issues = list(report.issues)
    for season in report.seasons:
        issues.extend(season.issues)

    if len(mal_tv) >= 2:
        nyaa_by_season = {s.season: s for s in nyaa_tv if s.season is not None}
        for index, (label, expected) in enumerate(mal_tv):
            if not expected or index == 0:
                continue
            season_num = index + 1
            section = nyaa_by_season.get(season_num)
            found = len(section.episodes) if section else 0
            if found > 0:
                continue
            prev_expected = mal_tv[index - 1][1]
            prev_section = nyaa_by_season.get(index)
            prev_found = len(prev_section.episodes) if prev_section else 0
            if prev_expected and prev_found >= max(1, int(prev_expected * COVERAGE_RELAXED)):
                issues.append(
                    f"{label}: 0/{expected} — saison manquante alors que S{index:02d} "
                    "est couverte (pack multi-saisons non rattaché ?)"
                )

    nyaa_detail = [(s.label, s.found, s.expected) for s in report.seasons]
    mal_by_season = {index + 1: count for index, (_, count) in enumerate(mal_tv)}

    coverage_strict = True
    coverage_relaxed = True
    for season in report.seasons:
        expected = mal_by_season.get(season.season or 0) or season.expected
        if expected and season.found < expected:
            coverage_strict = False
            if season.found < max(1, int(expected * COVERAGE_RELAXED)):
                coverage_relaxed = False
    if len(nyaa_tv) != len(mal_tv):
        coverage_strict = False
        coverage_relaxed = False

    quality_strict = all(ep.strict_ok for s in report.seasons for ep in s.episodes)
    quality_relaxed = all(ep.relaxed_ok for s in report.seasons for ep in s.episodes)

    strict_ok = coverage_strict and quality_strict
    relaxed_ok = coverage_relaxed and quality_relaxed

    stats = {
        "episodes_checked": sum(len(s.episodes) for s in report.seasons),
        "low_seed_episodes": sum(
            1
            for s in report.seasons
            for ep in s.episodes
            if "low_seeders" in ep.flags or "dead" in ep.flags
        ),
        "low_quality_episodes": sum(
            1
            for s in report.seasons
            for ep in s.episodes
            if "low_quality" in ep.flags or "sd_quality" in ep.flags
        ),
        "variant_episodes": sum(
            1
            for s in report.seasons
            for ep in s.episodes
            if any(
                f in ep.flags
                for f in ("directors_cut", "new_edition", "suspect_source")
            )
        ),
        "coherence_outliers": sum(
            len(s.coherence_outliers) for s in report.seasons
        ),
    }

    return (
        strict_ok,
        relaxed_ok,
        quality_strict,
        quality_relaxed,
        issues,
        nyaa_detail,
        stats,
    )


def _gather_with_retry(
    query: str, config: AnnieConfig, *, attempts: int = 3
) -> tuple[list, dict]:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            catalog, options = gather_catalog(query, config)
            if catalog:
                return catalog, options
        except Exception as exc:
            last_exc = exc
        if attempt < attempts - 1:
            time.sleep(2.0 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    return [], {}


def validate_target(target: ValidateTarget, config: AnnieConfig) -> dict:
    t0 = time.monotonic()
    result: dict = {
        "query": target.query,
        "mal_id": target.mal_id,
        "rank": target.rank,
        "anime_type": target.anime_type,
        "issues": [],
        "strict_ok": False,
        "relaxed_ok": False,
        "quality_strict_ok": False,
        "quality_relaxed_ok": False,
        "catalog_ok": False,
    }

    if target.mal_id is not None:
        mal = _mal_tv_from_id(target.mal_id, target.query)
    else:
        mal = _mal_tv_from_query(target.query)

    if mal is None:
        result["issues"].append("MAL: aucun résultat")
        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result

    title, mal_tv, anime_type, search_query = mal
    result["title"] = title
    result["search_query"] = search_query
    result["anime_type"] = anime_type
    result["mal_seasons"] = len(mal_tv)
    result["mal_detail"] = mal_tv

    try:
        catalog, _ = _gather_with_retry(search_query, config)
    except Exception as exc:
        result["issues"].append(f"catalog: {exc}")
        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result

    if not catalog:
        result["issues"].append("catalog: vide")
        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result

    result["catalog_ok"] = True

    if anime_type == "Movie" or (not mal_tv and anime_type != "TV"):
        movies = [s for s in catalog if s.kind == MediaKind.MOVIE]
        has_movie = bool(movies) and any(s.singles for s in movies)
        result["nyaa_seasons"] = 0
        result["nyaa_detail"] = []
        if has_movie or catalog:
            result["strict_ok"] = True
            result["relaxed_ok"] = True
        else:
            result["issues"].append("film: aucune release trouvée")
        result["elapsed"] = round(time.monotonic() - t0, 1)
        return result

    nyaa_tv = [
        s for s in catalog if s.kind == MediaKind.EPISODE and s.season is not None
    ]
    nyaa_tv.sort(key=lambda s: s.season or 0)
    result["nyaa_seasons"] = len(nyaa_tv)

    (
        strict_ok,
        relaxed_ok,
        quality_strict,
        quality_relaxed,
        issues,
        nyaa_detail,
        stats,
    ) = _score_tv(mal_tv, nyaa_tv)
    result["strict_ok"] = strict_ok
    result["relaxed_ok"] = relaxed_ok
    result["quality_strict_ok"] = quality_strict
    result["quality_relaxed_ok"] = quality_relaxed
    result["issues"].extend(issues)
    result["nyaa_detail"] = nyaa_detail
    result["quality_stats"] = stats
    result["elapsed"] = round(time.monotonic() - t0, 1)
    return result


def _targets_from_args(args: argparse.Namespace) -> list[ValidateTarget]:
    if args.queries:
        return _dedupe_targets([ValidateTarget(query=q) for q in args.queries])

    if args.top:
        print(f"Chargement du top {args.top} MAL via Jikan…")
        entries = fetch_top_anime(args.top)
        print(f"  {len(entries)} entrées récupérées")
        return _dedupe_targets(
            [
                ValidateTarget(
                    query=_query_for_top(entry),
                    mal_id=entry.mal_id,
                    rank=entry.rank,
                    anime_type=entry.anime_type,
                )
                for entry in entries
            ]
        )

    return _dedupe_targets(
        [ValidateTarget(query=q) for q in QUERIES[: max(1, args.limit)]]
    )


def _load_resume(path: Path) -> dict[int | str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    done: dict[int | str, dict] = {}
    for row in data.get("results", []):
        key = row.get("mal_id") or row.get("query")
        if key is not None:
            done[key] = row
    return done


def _save_results(path: Path, results: list[dict], *, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **meta,
        "results": sorted(
            results, key=lambda r: (r.get("rank") or 99999, r.get("query", ""))
        ),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vérifie que le catalogue Annie correspond aux saisons MAL."
    )
    parser.add_argument("queries", nargs="*", help="Requêtes à tester")
    parser.add_argument(
        "--top",
        type=int,
        metavar="N",
        help="Utiliser le top N anime MAL (ex: --top 1000)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Liste intégrée: nombre max d'anime (défaut: 100)",
    )
    parser.add_argument(
        "--workers", type=int, default=2, help="Parallélisme (défaut: 2)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Fichier JSON de sortie (défaut: scripts/results/…)",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Reprendre depuis --output"
    )
    parser.add_argument("--json", action="store_true", help="Résumé JSON sur stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    targets = _targets_from_args(args)

    default_name = f"top{args.top}" if args.top else f"list{len(targets)}"
    output_path = args.output or (RESULTS_DIR / f"validate_{default_name}.json")

    done: dict[int | str, dict] = _load_resume(output_path) if args.resume else {}
    pending = [
        target for target in targets if (target.mal_id or target.query) not in done
    ]

    config = AnnieConfig()
    results: list[dict] = list(done.values())
    workers = max(1, min(args.workers, len(pending) or 1))

    print(
        f"Validation {len(pending)} anime "
        f"(total={len(targets)}, déjà faits={len(done)}, workers={workers})…\n"
    )

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(validate_target, target, config): target for target in pending
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1

            status = (
                "STRICT"
                if result.get("strict_ok")
                else (
                    "RELAXED"
                    if result.get("relaxed_ok")
                    else ("QUALITY" if result.get("quality_relaxed_ok") else "FAIL")
                )
            )
            title = result.get("title", "?")
            rank = result.get("rank")
            rank_s = f"#{rank:4d} " if rank else ""
            print(
                f"[{status:7}] {rank_s}{result['query'][:32]:32} "
                f"{str(title)[:36]:36} "
                f"MAL={result.get('mal_seasons', '?')} "
                f"Nyaa={result.get('nyaa_seasons', '?')} "
                f"({result.get('elapsed', 0):.0f}s)"
            )
            for issue in result.get("issues", [])[:3]:
                print(f"         ↳ {issue}")
            if len(result.get("issues", [])) > 3:
                print(f"         ↳ … +{len(result['issues']) - 3} autres")

            if completed % 10 == 0 or completed == len(pending):
                _save_results(
                    output_path,
                    results,
                    meta={
                        "total": len(targets),
                        "completed": len(results),
                        "source": f"top_{args.top}" if args.top else "list",
                    },
                )

    strict_ok = sum(1 for r in results if r.get("strict_ok"))
    relaxed_ok = sum(1 for r in results if r.get("relaxed_ok"))
    catalog_ok = sum(1 for r in results if r.get("catalog_ok"))
    quality_strict_ok = sum(1 for r in results if r.get("quality_strict_ok"))
    quality_relaxed_ok = sum(1 for r in results if r.get("quality_relaxed_ok"))
    fail = len(results) - relaxed_ok

    summary = {
        "total": len(results),
        "catalog_ok": catalog_ok,
        "strict_ok": strict_ok,
        "relaxed_ok": relaxed_ok,
        "quality_strict_ok": quality_strict_ok,
        "quality_relaxed_ok": quality_relaxed_ok,
        "fail": fail,
        "output": str(output_path),
        "results": results,
    }

    _save_results(
        output_path,
        results,
        meta={
            "total": len(results),
            "catalog_ok": catalog_ok,
            "strict_ok": strict_ok,
            "relaxed_ok": relaxed_ok,
            "quality_strict_ok": quality_strict_ok,
            "quality_relaxed_ok": quality_relaxed_ok,
            "fail": fail,
            "source": f"top_{args.top}" if args.top else "list",
        },
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if fail else 0

    print(f"\n{'=' * 60}")
    print(f"Catalogue non vide : {catalog_ok}/{len(results)}")
    print(f"Strict (100% ep.)  : {strict_ok}/{len(results)}")
    print(f"Relaxed (≥85% ep.) : {relaxed_ok}/{len(results)}")
    print(f"Qualité stricte    : {quality_strict_ok}/{len(results)}")
    print(f"Qualité relaxée    : {quality_relaxed_ok}/{len(results)}")
    print(f"Échecs             : {fail}/{len(results)}")
    print(f"Rapport            : {output_path}")

    if fail:
        print("\nÉchecs (relaxed) — extrait:")
        shown = 0
        for row in sorted(results, key=lambda r: r.get("rank") or 99999):
            if row.get("relaxed_ok"):
                continue
            rank = row.get("rank")
            rank_s = f"#{rank:4d} " if rank is not None else "      "
            print(f"  {rank_s}{row.get('query')} — {row.get('title', '?')}")
            for issue in row.get("issues", [])[:2]:
                print(f"       ! {issue}")
            shown += 1
            if shown >= 25:
                print("  …")
                break

    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
