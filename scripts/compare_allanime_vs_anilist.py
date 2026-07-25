#!/usr/bin/env python3
"""Comparaison longue : AllAnime-only vs AniList/MAL (pick + structure).

Ne touche pas Nyaa — mesure uniquement la qualité métadonnées.
Verdict : peut-on remplacer AniList complètement par AllAnime ?
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie import allanime, metadata as meta
from annie.config import AnnieConfig
from annie.mal import (
    franchise_to_releases,
    pick_candidate,
    _fuzzy_title_ratio,
)
from annie.parsing import normalize
from _metadata_compare import summarize_releases
from validate_franchise import QUERIES as BIG_QUERIES

OUT = ROOT / "scripts" / "results" / "allanime_vs_anilist.json"

# Attentes manuelles (saison TV « main » connues) pour juger les trous.
EXPECT_MIN_TV = {
    "re zero": 3,
    "konosuba": 3,
    "overlord": 4,
    "spy x family": 2,
    "jujutsu kaisen": 2,
    "demon slayer": 3,
    "sword art online": 2,
    "attack on titan": 3,
    "one punch man": 2,
    "vinland saga": 2,
    "mushoku tensei": 2,
    "danmachi": 4,
    "fate zero": 1,
    "steins gate": 1,
    "violet evergarden": 1,
    "made in abyss": 2,
    "code geass": 2,
    "evangelion": 1,
    "bleach": 1,
    "naruto": 1,
    "one piece": 1,
}


def _summ(releases) -> dict:
    out = summarize_releases(releases)
    out["seasons"] = sorted(
        {entry["season"] for entry in out["tv"] if entry["season"] is not None}
    )
    return out


def _titles_close(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na, nb = normalize(a), normalize(b)
    if na == nb or na in nb or nb in na:
        return True
    return _fuzzy_title_ratio(a, b) >= 0.72


def _allanime_only_releases(query: str):
    """Pick + structure 100 % AllAnime (aucun AniList/MAL)."""
    shows = allanime.search_shows(query, limit=40)
    if not shows:
        return None, [], "no_search"
    # Meilleur show vs query seule (comportement type ani-cli top hit).
    ranked = sorted(
        (
            (
                allanime._score_show_against_chosen(
                    s, chosen=None, user_query=query
                ),
                s,
            )
            for s in shows
        ),
        key=lambda row: row[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 15:
        # Fallback : premier résultat API
        best = shows[0]
    else:
        best = ranked[0][1]
    kept = allanime.filter_franchise_shows(
        shows, chosen=None, user_query=query
    )
    if not kept:
        kept = [best]
    releases = allanime.shows_to_releases(kept, user_query=query, chosen=None)
    return best, releases, None


def _anilist_path(query: str, config: AnnieConfig):
    cands = meta.search_anime(query, limit=8, config=config)
    if not cands:
        return None, [], "no_search"
    chosen = pick_candidate(cands, query)
    if not chosen:
        return None, [], "no_pick"
    fr = meta.collect_franchise(chosen, config=config)
    releases = franchise_to_releases(
        fr, skip_recap=False, root_id=chosen.mal_id, user_query=query
    )
    return chosen, releases, None


def compare_one(query: str, config: AnnieConfig) -> dict:
    t0 = time.time()
    row: dict = {"query": query}
    flags: list[str] = []

    # --- AniList / MAL ---
    try:
        al_chosen, al_rels, al_err = _anilist_path(query, config)
    except Exception as exc:  # noqa: BLE001
        al_chosen, al_rels, al_err = None, [], f"{type(exc).__name__}: {exc}"
    row["anilist"] = {
        "error": al_err,
        "pick": (al_chosen.title_english or al_chosen.title) if al_chosen else None,
        "type": al_chosen.type if al_chosen else None,
        **_summ(al_rels),
    }

    # --- AllAnime only ---
    try:
        aa_best, aa_rels, aa_err = _allanime_only_releases(query)
    except Exception as exc:  # noqa: BLE001
        aa_best, aa_rels, aa_err = None, [], f"{type(exc).__name__}: {exc}"
    row["allanime"] = {
        "error": aa_err,
        "pick": aa_best.name if aa_best else None,
        "eps": aa_best.episode_count if aa_best else None,
        **_summ(aa_rels),
    }

    al, aa = row["anilist"], row["allanime"]

    # --- Flags ---
    if al_err:
        flags.append(f"al_err:{al_err}")
    if aa_err:
        flags.append(f"aa_err:{aa_err}")
    if not al_err and not aa_err:
        if al["pick"] and aa["pick"] and not _titles_close(al["pick"], aa["pick"]):
            flags.append("pick_mismatch")
        if aa["n_tv"] + aa["n_movies"] == 0:
            flags.append("aa_empty")
        if al["n_tv"] + al["n_movies"] == 0:
            flags.append("al_empty")
        # Couverture saisons
        expect = EXPECT_MIN_TV.get(query)
        if expect:
            if aa["n_tv"] < expect:
                flags.append(f"aa_missing_seasons:{aa['n_tv']}<{expect}")
            if al["n_tv"] < expect:
                flags.append(f"al_missing_seasons:{al['n_tv']}<{expect}")
        if al["n_tv"] >= 2 and aa["n_tv"] <= 1 and al["n_tv"] > aa["n_tv"]:
            flags.append("aa_thin_vs_al")
        if aa["n_tv"] >= al["n_tv"] + 2:
            flags.append("aa_fatter_tv")
        if al["n_movies"] >= 1 and aa["n_movies"] == 0:
            flags.append("aa_miss_movies")
        if aa["n_movies"] >= 1 and al["n_movies"] == 0:
            flags.append("aa_has_movies_al_none")
        # Pollution spinoff dans le pick AA
        if aa["pick"] and re.search(
            r"\b(?:alternative|gaiden|recap|camrip)\b", aa["pick"], re.I
        ):
            flags.append("aa_pick_spinoffish")

    # Winner heuristique par query
    winner = "tie"
    if aa_err and not al_err:
        winner = "anilist"
    elif al_err and not aa_err:
        winner = "allanime"
    elif not aa_err and not al_err:
        aa_pen = sum(
            1
            for f in flags
            if f.startswith(
                (
                    "aa_missing",
                    "aa_thin",
                    "aa_miss_movies",
                    "aa_empty",
                    "pick_mismatch",
                    "aa_pick_spinoff",
                )
            )
        )
        al_pen = sum(
            1
            for f in flags
            if f.startswith(("al_missing", "al_empty"))
        )
        # Bonus AA si films mieux présents sans être thin
        if "aa_has_movies_al_none" in flags:
            aa_pen = max(0, aa_pen - 1)
        if aa_pen < al_pen:
            winner = "allanime"
        elif al_pen < aa_pen:
            winner = "anilist"
        elif aa["n_tv"] > al["n_tv"] and "aa_thin_vs_al" not in flags:
            winner = "allanime"
        elif al["n_tv"] > aa["n_tv"]:
            winner = "anilist"

    row["flags"] = flags
    row["winner"] = winner
    row["ok"] = not aa_err or not al_err
    row["dt"] = round(time.time() - t0, 1)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()

    queries = list(args.queries) if args.queries else list(BIG_QUERIES)
    if args.limit > 0:
        queries = queries[: args.limit]

    config = AnnieConfig.load()
    # Force AniList pour le bras de comparaison
    config.metadata.provider = "anilist"
    config.metadata.structure = "franchise"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    print(f"AllAnime-only vs AniList/MAL — {len(queries)} queries\n")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(compare_one, q, config): q for q in queries}
        done = 0
        for fut in as_completed(futs):
            done += 1
            row = fut.result()
            rows.append(row)
            al, aa = row["anilist"], row["allanime"]
            print(
                f"[{done}/{len(queries)}] {row['winner'].upper():8}  "
                f"{row['query']!r}\n"
                f"    AL pick={al.get('pick')!r}  tv={al.get('n_tv')} mov={al.get('n_movies')}\n"
                f"    AA pick={aa.get('pick')!r}  tv={aa.get('n_tv')} mov={aa.get('n_movies')}  "
                f"{row.get('dt')}s"
            )
            if row.get("flags"):
                print(f"    !! {', '.join(row['flags'])}")

    rows.sort(key=lambda r: queries.index(r["query"]) if r["query"] in queries else 999)

    wins = {"allanime": 0, "anilist": 0, "tie": 0}
    for r in rows:
        wins[r.get("winner", "tie")] = wins.get(r.get("winner", "tie"), 0) + 1

    flag_counts: dict[str, int] = {}
    for r in rows:
        for f in r.get("flags") or []:
            key = f.split(":")[0]
            flag_counts[key] = flag_counts.get(key, 0) + 1

    aa_only_ok = sum(1 for r in rows if not r["allanime"].get("error"))
    al_only_ok = sum(1 for r in rows if not r["anilist"].get("error"))
    pick_mismatch = sum(1 for r in rows if "pick_mismatch" in (r.get("flags") or []))
    aa_thin = sum(1 for r in rows if "aa_thin_vs_al" in (r.get("flags") or []))
    aa_miss_movies = sum(
        1 for r in rows if "aa_miss_movies" in (r.get("flags") or [])
    )

    # Verdict
    n = max(1, len(rows))
    aa_win_rate = wins["allanime"] / n
    thin_rate = aa_thin / n
    mismatch_rate = pick_mismatch / n

    if thin_rate > 0.25 or mismatch_rate > 0.35:
        verdict = "NO"
        verdict_detail = (
            "AllAnime seul n'est pas assez fiable comme remplacement complet : "
            f"saisons trop pauvres dans {aa_thin}/{n} cas, "
            f"mauvais pick dans {pick_mismatch}/{n} cas."
        )
    elif aa_win_rate >= 0.45 and thin_rate <= 0.15:
        verdict = "YES_WITH_CAVEATS"
        verdict_detail = (
            "AllAnime peut servir de provider principal, mais garde un fallback "
            "AniList/MAL pour les franchises multi-saisons incomplètes et les picks ambigus."
        )
    else:
        verdict = "HYBRID"
        verdict_detail = (
            "Ni AllAnime ni AniList ne gagne clairement partout. "
            "Le meilleur setup reste hybride : pick AniList + structure AllAnime "
            "(ou AllAnime avec fallback franchise)."
        )

    summary = {
        "total": len(rows),
        "wins": wins,
        "aa_search_ok": aa_only_ok,
        "al_search_ok": al_only_ok,
        "pick_mismatch": pick_mismatch,
        "aa_thin_vs_al": aa_thin,
        "aa_miss_movies": aa_miss_movies,
        "flag_counts": dict(sorted(flag_counts.items(), key=lambda x: -x[1])),
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "rows": rows,
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    print("\n======== RÉSUMÉ ========")
    print(f"Total queries     : {len(rows)}")
    print(f"Wins AllAnime     : {wins['allanime']}")
    print(f"Wins AniList      : {wins['anilist']}")
    print(f"Ties              : {wins['tie']}")
    print(f"AA search OK      : {aa_only_ok}/{len(rows)}")
    print(f"AL search OK      : {al_only_ok}/{len(rows)}")
    print(f"Pick mismatch     : {pick_mismatch}")
    print(f"AA thin vs AL     : {aa_thin}")
    print(f"AA miss movies    : {aa_miss_movies}")
    print(f"Top flags         : {summary['flag_counts']}")
    print(f"\nVERDICT: {verdict}")
    print(verdict_detail)
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
