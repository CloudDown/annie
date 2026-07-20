#!/usr/bin/env python3
"""Compare structure AllAnime vs franchise AniList/MAL (+ audit Nyaa movies)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie import allanime, metadata as meta
from annie.cli import gather_catalog
from annie.config import AnnieConfig
from annie.mal import franchise_to_releases, pick_candidate
from annie.types import MediaKind

OUT = ROOT / "scripts" / "results" / "structure_compare.json"

QUERIES = [
    "konosuba",
    "violet evergarden",
    "demon slayer",
    "sword art online",
    "fate zero",
    "jujutsu kaisen",
    "spy x family",
    "rascal does not dream of bunny girl senpai",
    "evangelion",
    "made in abyss",
    "tanya the evil",
    "overlord",
    "code geass",
    "madoka magica",
    "re zero",
]


def _summarize_releases(releases) -> dict:
    tv = [
        {
            "season": r.season,
            "eps": r.episode_count,
            "label": r.label[:70],
        }
        for r in releases
        if r.kind == MediaKind.EPISODE
    ]
    movies = [{"label": r.label[:70], "eps": r.episode_count} for r in releases if r.kind == MediaKind.MOVIE]
    extras = [
        {"kind": r.kind.name, "label": r.label[:60]}
        for r in releases
        if r.kind not in {MediaKind.EPISODE, MediaKind.MOVIE}
    ]
    return {
        "tv": tv,
        "movies": movies,
        "extras": extras,
        "n_tv": len(tv),
        "n_movies": len(movies),
        "n_extras": len(extras),
    }


def compare_one(query: str, config: AnnieConfig) -> dict:
    t0 = time.time()
    row: dict = {"query": query}
    try:
        cands = meta.search_anime(query, config=config)
        chosen = pick_candidate(cands, query) if cands else None
        if not chosen:
            row["ok"] = False
            row["error"] = "no_pick"
            return row
        row["pick"] = chosen.title_english or chosen.title

        # AllAnime structure
        aa = allanime.releases_for_query(query, chosen=chosen)
        row["allanime"] = _summarize_releases(aa)

        # Franchise graph (AniList/MAL)
        fr = meta.collect_franchise(chosen, config=config)
        mal = franchise_to_releases(
            fr, skip_recap=False, root_id=chosen.mal_id, user_query=query
        )
        row["franchise"] = _summarize_releases(mal)

        # Catalog live avec structure allanime (défaut)
        cfg_aa = config
        catalog, _ = gather_catalog(
            query, cfg_aa, confirm_anime=False, fill_gaps=False
        )
        movies = [s for s in catalog if s.kind == MediaKind.MOVIE]
        seasons = [s for s in catalog if s.kind == MediaKind.EPISODE]
        row["catalog_aa"] = {
            "seasons": [
                {
                    "s": s.season,
                    "eps": len(s.episodes),
                    "expected": s.expected_episodes,
                    "label": s.label[:50],
                }
                for s in seasons
            ],
            "movies": [
                {
                    "label": s.label[:50],
                    "n": len(s.singles),
                    "title": (s.singles[0].entry.title[:70] if s.singles else ""),
                }
                for s in movies
            ],
        }

        # Flags qualité structure
        flags: list[str] = []
        aa_seasons = {r.season for r in aa if r.kind == MediaKind.EPISODE}
        mal_seasons = {r.season for r in mal if r.kind == MediaKind.EPISODE}
        if aa_seasons and mal_seasons and aa_seasons != mal_seasons:
            flags.append(f"season_diff aa={sorted(aa_seasons)} mal={sorted(mal_seasons)}")
        if row["allanime"]["n_movies"] and not row["catalog_aa"]["movies"]:
            flags.append("movies_meta_but_empty_catalog")
        for m in row["catalog_aa"]["movies"]:
            if m["n"] != 1:
                flags.append(f"movie_n={m['n']}:{m['label']}")
        row["flags"] = flags
        row["ok"] = True
    except Exception as exc:  # noqa: BLE001
        row["ok"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["dt"] = round(time.time() - t0, 1)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()

    queries = list(args.queries) if args.queries else list(QUERIES)
    if args.limit > 0:
        queries = queries[: args.limit]

    config = AnnieConfig.load()
    config.metadata.structure = "allanime"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    print(f"Compare structure — {len(queries)} anime\n")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(compare_one, q, config): q for q in queries}
        done = 0
        for fut in as_completed(futs):
            done += 1
            row = fut.result()
            rows.append(row)
            status = "OK" if row.get("ok") else "FAIL"
            aa = row.get("allanime") or {}
            fr = row.get("franchise") or {}
            print(
                f"[{done}/{len(queries)}] {status} {row['query']!r}  "
                f"AA tv={aa.get('n_tv')} mov={aa.get('n_movies')} | "
                f"MAL tv={fr.get('n_tv')} mov={fr.get('n_movies')}  "
                f"{row.get('dt')}s"
            )
            if aa.get("tv"):
                print(
                    "    AA seasons:",
                    ", ".join(
                        f"S{t['season']}({t['eps']})" for t in aa["tv"] if t.get("season")
                    ),
                )
            if aa.get("movies"):
                print(
                    "    AA movies:",
                    "; ".join(m["label"] for m in aa["movies"][:3]),
                )
            cat = row.get("catalog_aa") or {}
            if cat.get("movies"):
                for m in cat["movies"]:
                    print(f"    cat movie: {m['label']} → {m['title'][:60]}")
            for flag in row.get("flags") or []:
                print(f"    !! {flag}")
            if row.get("error"):
                print(f"    !! {row['error']}")

    rows.sort(key=lambda r: queries.index(r["query"]) if r["query"] in queries else 999)
    summary = {
        "total": len(rows),
        "ok": sum(1 for r in rows if r.get("ok")),
        "rows": rows,
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"\n→ {OUT}")
    return 0 if summary["ok"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
