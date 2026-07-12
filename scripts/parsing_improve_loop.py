#!/usr/bin/env python3
"""Boucle parsing : top MAL par paliers jusqu'à 2000, 100 % parsing puis escalade.

Pour chaque palier :
  1. Récupère les anime MAL + releases Nyaa (comme validate_franchise / survey)
  2. Parse chaque titre Nyaa et mesure les flags bloquants
  3. Tant que parsing < 100 % → exporte les anomalies et sort (code 2) pour correction
  4. À 100 % parsing → valide le catalogue (relaxed) puis passe au palier suivant

État persistant : scripts/results/parsing_loop_state.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie.config import AnnieConfig
from annie.mal import TopAnimeEntry, fetch_top_anime
from annie.parsing import is_non_anime_extra

from survey_nyaa_titles import (  # noqa: E402
    SurveyRow,
    _aggregate,
    _analyze_entry,
    _fetch_all,
    _nyaa_query_for_top,
)
from validate_franchise import (  # noqa: E402
    ValidateTarget,
    _mal_tv_from_id,
    _query_for_top,
    validate_target,
)

RESULTS_DIR = ROOT / "scripts" / "results"
STATE_PATH = RESULTS_DIR / "parsing_loop_state.json"
FIXTURES_PENDING = ROOT / "tests" / "fixtures" / "parse_titles_pending.json"

# Palier MAL : on monte jusqu'au top 2000
TIERS = (10, 40, 100, 200, 500, 1000, 2000)

# Flags considérés comme échec parsing (hors episode_without_season = heuristique S1)
BLOCKING_FLAGS = frozenset(
    {
        "unknown_kind",
        "batch_range_non_batch_kind",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "tiers_completed": [],
            "current_tier": TIERS[0],
            "updated_at": None,
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    state["updated_at"] = _utc_now()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def targets_for_top(n: int) -> list[ValidateTarget]:
    print(f"Chargement top {n} MAL…")
    entries = fetch_top_anime(n)
    print(f"  {len(entries)} entrées")
    return [
        ValidateTarget(
            query=_query_for_top(entry),
            mal_id=entry.mal_id,
            rank=entry.rank,
            anime_type=entry.anime_type,
        )
        for entry in entries
    ]


def nyaa_queries_for_targets(targets: list[ValidateTarget]) -> list[str]:
    """Requêtes Nyaa alignées sur validate_franchise (franchise search_query)."""
    queries: list[str] = []
    for target in targets:
        if target.mal_id is not None:
            mal = _mal_tv_from_id(target.mal_id, target.query)
            if mal is not None:
                _, _, _, search_query = mal
                if search_query.strip():
                    queries.append(search_query.strip())
                    continue
        queries.append(target.query.strip())
    return list(dict.fromkeys(q for q in queries if q))


def nyaa_queries_for_top_entries(entries: list[TopAnimeEntry]) -> list[str]:
    return list(
        dict.fromkeys(
            _nyaa_query_for_top(entry).strip()
            for entry in entries
            if _nyaa_query_for_top(entry).strip()
        )
    )


def _row_blocks_parsing(row: SurveyRow) -> bool:
    if is_non_anime_extra(row.title) or "non_anime_extra" in row.flags:
        return False
    if row.kind == "manga":
        return False
    return any(f in BLOCKING_FLAGS for f in row.flags)


def parsing_metrics(rows: list[SurveyRow]) -> dict:
    relevant = [
        r
        for r in rows
        if not is_non_anime_extra(r.title)
        and "non_anime_extra" not in r.flags
        and r.kind != "manga"
    ]
    total = len(relevant)
    blocked_rows = [r for r in relevant if _row_blocks_parsing(r)]
    unknown = sum(1 for r in relevant if "unknown_kind" in r.flags)
    by_flag: dict[str, int] = {}
    for row in relevant:
        for flag in row.flags:
            if flag in BLOCKING_FLAGS:
                by_flag[flag] = by_flag.get(flag, 0) + 1

    pct_ok = 100.0 * (total - len(blocked_rows)) / total if total else 100.0
    return {
        "total_titles": total,
        "blocked": len(blocked_rows),
        "unknown_kind": unknown,
        "parsing_ok_pct": round(pct_ok, 2),
        "parsing_100": len(blocked_rows) == 0 and total > 0,
        "flag_counts": by_flag,
    }


def export_pending(rows: list[SurveyRow], tier: int) -> Path:
    blocked = [r for r in rows if _row_blocks_parsing(r)]
    blocked.sort(key=lambda r: (r.flags, -r.seeders))

    samples: list[dict] = []
    seen: set[str] = set()
    for row in blocked:
        if row.title in seen:
            continue
        seen.add(row.title)
        samples.append(
            {
                "title": row.title,
                "flags": row.flags,
                "signals": row.signals,
                "kind": row.kind,
                "season": row.season,
                "episode": row.episode,
                "source_query": row.source_query,
            }
        )
        if len(samples) >= 200:
            break

    out = RESULTS_DIR / f"parse_pending_top{tier}.json"
    payload = {
        "tier": tier,
        "generated_at": _utc_now(),
        "blocked_count": len(blocked),
        "samples": samples,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    FIXTURES_PENDING.parent.mkdir(parents=True, exist_ok=True)
    fixture_rows = [
        {"title": s["title"], "note": ",".join(s["flags"])}
        for s in samples[:50]
    ]
    FIXTURES_PENDING.write_text(
        json.dumps(fixture_rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out


def run_unit_tests() -> bool:
    print("\nTests unitaires…")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        print("  OK")
        return True
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    return False


def validate_tier(
    targets: list[ValidateTarget],
    config: AnnieConfig,
    *,
    workers: int,
    output_path: Path,
) -> dict:
    results: list[dict] = []
    workers = max(1, min(workers, len(targets) or 1))
    print(f"\nValidation catalogue ({len(targets)} anime, workers={workers})…")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(validate_target, target, config): target for target in targets
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            status = "OK" if result.get("relaxed_ok") else "FAIL"
            rank = result.get("rank")
            rank_s = f"#{rank:4d} " if rank else ""
            print(
                f"  [{status:4}] {rank_s}{str(result.get('title', '?'))[:40]:40} "
                f"({result.get('elapsed', 0):.0f}s)"
            )

    relaxed_ok = sum(1 for r in results if r.get("relaxed_ok"))
    strict_ok = sum(1 for r in results if r.get("strict_ok"))
    payload = {
        "tier": len(targets),
        "generated_at": _utc_now(),
        "total": len(results),
        "strict_ok": strict_ok,
        "relaxed_ok": relaxed_ok,
        "fail": len(results) - relaxed_ok,
        "results": sorted(
            results, key=lambda r: (r.get("rank") or 99999, r.get("query", ""))
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"  Validation : strict {strict_ok}/{len(results)}, "
        f"relaxed {relaxed_ok}/{len(results)} → {output_path}"
    )
    return payload


def run_tier(
    tier: int,
    *,
    pages: int,
    workers: int,
    skip_validate: bool,
    run_tests: bool,
) -> int:
    """Retourne 0 si palier terminé (parsing 100 %), 2 si parsing à corriger, 1 si erreur."""
    config = AnnieConfig.load()
    targets = targets_for_top(tier)
    queries = nyaa_queries_for_targets(targets)
    if not queries:
        print("Aucune requête Nyaa — abandon")
        return 1

    print(f"\n{'=' * 60}")
    print(f"PALIER top {tier} — {len(queries)} requêtes Nyaa × {pages} pages")
    print(f"{'=' * 60}")

    t0 = time.monotonic()
    pairs = _fetch_all(
        queries,
        pages=pages,
        category=config.category,
        filter_code=config.filter_code,
        workers=workers,
    )
    rows = [_analyze_entry(entry, q) for q, entry in pairs]
    elapsed = round(time.monotonic() - t0, 1)
    print(f"  {len(rows)} titres uniques en {elapsed}s")

    metrics = parsing_metrics(rows)
    summary = _aggregate(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    survey_path = RESULTS_DIR / f"parsing_loop_top{tier}_{stamp}.json"
    survey_path.write_text(
        json.dumps(
            {
                "meta": {
                    "tier": tier,
                    "generated_at": _utc_now(),
                    "queries": queries,
                    "pages": pages,
                    "elapsed_s": elapsed,
                },
                "metrics": metrics,
                "summary": summary,
                "rows": [asdict(r) for r in rows],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nParsing : {metrics['parsing_ok_pct']}% OK "
          f"({metrics['total_titles'] - metrics['blocked']}/{metrics['total_titles']})")
    if metrics["flag_counts"]:
        for flag, count in sorted(metrics["flag_counts"].items(), key=lambda x: -x[1]):
            print(f"  {flag}: {count}")
    print(f"  Rapport → {survey_path}")

    if not metrics["parsing_100"]:
        pending = export_pending(rows, tier)
        print(f"\n⚠ Parsing incomplet ({metrics['blocked']} titres bloquants)")
        print(f"  Anomalies → {pending}")
        print(f"  Fixtures  → {FIXTURES_PENDING}")
        print("\nCorrige annie/parsing.py puis relance ce script.")
        if summary.get("anomaly_samples"):
            print("\nÉchantillon unknown_kind :")
            for sample in summary["anomaly_samples"].get("unknown_kind", [])[:8]:
                print(f"  • {sample[:110]}")
        return 2

    print("\n✓ Parsing 100 % sur ce palier")

    if run_tests and not run_unit_tests():
        return 1

    if not skip_validate:
        validate_path = RESULTS_DIR / f"parsing_loop_validate_top{tier}.json"
        validate_tier(targets, config, workers=workers, output_path=validate_path)

    state = load_state()
    if tier not in state.get("tiers_completed", []):
        state.setdefault("tiers_completed", []).append(tier)
    next_idx = TIERS.index(tier) + 1 if tier in TIERS else len(TIERS)
    state["current_tier"] = TIERS[next_idx] if next_idx < len(TIERS) else tier
    state["last_completed_tier"] = tier
    save_state(state)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Boucle parsing MAL → Nyaa jusqu'au top 2000"
    )
    parser.add_argument(
        "--tier",
        type=int,
        metavar="N",
        help=f"Palier MAL explicite (défaut: état ou {TIERS[0]})",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Enchaîner les paliers jusqu'à 2000 ou échec parsing",
    )
    parser.add_argument("--pages", type=int, default=2, help="Pages Nyaa / requête")
    parser.add_argument("--workers", type=int, default=4, help="Parallélisme Nyaa")
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Ne pas lancer validate_franchise après parsing 100 %",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="Ne pas lancer unittest après parsing 100 %",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Réinitialiser l'état (repartir du palier 10)",
    )
    parser.add_argument("--json", action="store_true", help="État JSON sur stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.reset:
        save_state(
            {
                "tiers_completed": [],
                "current_tier": TIERS[0],
                "updated_at": _utc_now(),
            }
        )
        print(f"État réinitialisé → palier {TIERS[0]}")

    state = load_state()

    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False))

    tiers_to_run: list[int]
    if args.tier:
        tiers_to_run = [args.tier]
    elif args.loop:
        start = state.get("current_tier", TIERS[0])
        if start not in TIERS:
            start = TIERS[0]
        idx = TIERS.index(start)
        tiers_to_run = list(TIERS[idx:])
    else:
        tiers_to_run = [state.get("current_tier", TIERS[0])]

    for tier in tiers_to_run:
        if tier > 2000:
            break
        code = run_tier(
            tier,
            pages=args.pages,
            workers=args.workers,
            skip_validate=args.skip_validate,
            run_tests=not args.no_tests,
        )
        if code != 0:
            return code
        if tier >= 2000:
            print("\n🎉 Top 2000 MAL — parsing 100 % sur tous les paliers terminés")
            break
        if not args.loop:
            next_tier = TIERS[TIERS.index(tier) + 1] if tier in TIERS else None
            if next_tier:
                print(f"\nPalier {tier} OK — prochain : top {next_tier} (relance avec --loop)")
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
