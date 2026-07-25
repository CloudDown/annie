#!/usr/bin/env python3
"""Audit live : sections Movies + packs Season+Movie pour une liste d'anime."""

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

from annie.catalog import gather_catalog
from annie.config import AnnieConfig
from annie.parsing import is_franchise_pack_with_movie, parse_title
from annie.types import MediaKind

OUT = ROOT / "scripts" / "results" / "movie_audit.json"

# Franchises avec films connus / packs fréquents sur Nyaa.
QUERIES = [
    "konosuba",
    "violet evergarden",
    "demon slayer",
    "your name",
    "weathering with you",
    "suzume",
    "a silent voice",
    "evangelion",
    "made in abyss",
    "re zero",
    "fate zero",
    "sword art online",
    "spy x family",
    "jujutsu kaisen",
    "chainsaw man",
    "rascal does not dream of bunny girl senpai",
    "madoka magica",
    "code geass",
    "tanya the evil",
    "overlord",
]

PACK_HINT = re.compile(
    r"(?:S\d{1,2}|Season).{0,40}(?:\+|and).{0,20}(?:[Mm]ovie|[Ff]ilm)|"
    r"(?:[Mm]ovie|[Ff]ilm).{0,40}(?:\+|and).{0,20}(?:S\d{1,2}|Season)",
    re.I,
)


def _short(title: str, n: int = 90) -> str:
    title = " ".join(title.split())
    return title if len(title) <= n else title[: n - 1] + "…"


def audit_one(query: str, config: AnnieConfig) -> dict:
    t0 = time.time()
    try:
        catalog, _ = gather_catalog(
            query,
            config,
            confirm_anime=False,
            fill_gaps=False,
        )
    except Exception as exc:  # noqa: BLE001 — audit réseau
        return {
            "query": query,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "dt": round(time.time() - t0, 1),
        }

    movies = [s for s in catalog if s.kind == MediaKind.MOVIE]
    seasons = [s for s in catalog if s.kind == MediaKind.EPISODE]
    issues: list[str] = []
    movie_rows: list[dict] = []

    for section in movies:
        n = len(section.singles)
        titles = [_short(i.entry.title) for i in section.singles]
        kinds = [parse_title(i.entry.title).kind.name for i in section.singles]
        row = {
            "label": section.label,
            "n": n,
            "titles": titles,
            "kinds": kinds,
        }
        movie_rows.append(row)
        if n == 0:
            issues.append(f"empty:{section.label[:50]}")
        elif n > 1:
            issues.append(f"multi:{section.label[:40]} n={n}")
        for item in section.singles:
            title = item.entry.title
            if is_franchise_pack_with_movie(title) or PACK_HINT.search(title):
                issues.append(f"pack_in_movies:{_short(title, 70)}")
            if parse_title(title).kind == MediaKind.BATCH:
                issues.append(f"batch_kind:{_short(title, 70)}")
            # Ancres du label absentes du torrent → mauvais film.
            from annie.catalog import (
                _movie_label_anchors,
                _title_has_movie_anchors,
            )
            from annie.types import MalRelease

            fake = MalRelease(
                mal_id=0,
                label=section.label,
                kind=MediaKind.MOVIE,
                season=None,
                episode_count=1,
                nyaa_queries=[section.label],
                sort_key=(15, "x"),
            )
            anchors = _movie_label_anchors(section.label, fake)
            if anchors and not _title_has_movie_anchors(title, anchors):
                issues.append(
                    f"anchor_miss:{section.label[:35]} ← {_short(title, 55)}"
                )

    packs_in_seasons = 0
    for section in seasons:
        for item in section.episodes.values():
            title = item.entry.title
            if is_franchise_pack_with_movie(title) or PACK_HINT.search(title):
                packs_in_seasons += 1
                break  # un pack par saison suffit

    hard = [
        i
        for i in issues
        if i.startswith(
            ("multi:", "pack_in_movies:", "batch_kind:", "anchor_miss:")
        )
    ]
    soft_empty = bool(issues) and all(i.startswith("empty:") for i in issues)

    return {
        "query": query,
        "ok": not hard and not soft_empty,
        "soft_ok": soft_empty and not hard,
        "movies": movie_rows,
        "movie_sections": len(movies),
        "season_sections": len(seasons),
        "packs_in_seasons": packs_in_seasons,
        "issues": issues,
        "dt": round(time.time() - t0, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Limiter le nombre d'anime")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("queries", nargs="*", help="Queries custom (sinon liste défaut)")
    args = parser.parse_args()

    queries = list(args.queries) if args.queries else list(QUERIES)
    if args.limit > 0:
        queries = queries[: args.limit]

    config = AnnieConfig.load()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    t0 = time.time()
    print(f"Audit Movies — {len(queries)} anime, workers={args.workers}\n")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(audit_one, q, config): q for q in queries}
        done = 0
        for fut in as_completed(futures):
            done += 1
            row = fut.result()
            rows.append(row)
            status = "OK" if row.get("ok") else ("SOFT" if row.get("soft_ok") else "FAIL")
            issues = ", ".join(row.get("issues") or []) or "—"
            n_mov = row.get("movie_sections", 0)
            packs = row.get("packs_in_seasons", 0)
            print(
                f"[{done}/{len(queries)}] {status}  {row['query']!r}  "
                f"movies={n_mov} packs_in_S={packs}  {row.get('dt')}s"
            )
            if row.get("movies"):
                for m in row["movies"]:
                    titles = m["titles"] or ["(vide)"]
                    print(f"    · {m['label']}: {titles[0]}")
            if row.get("issues") and status != "OK":
                print(f"    !! {issues}")
            if row.get("error"):
                print(f"    !! {row['error']}")

    rows.sort(key=lambda r: queries.index(r["query"]) if r["query"] in queries else 999)
    fails = [r for r in rows if not r.get("ok") and not r.get("soft_ok")]
    softs = [r for r in rows if r.get("soft_ok")]
    summary = {
        "total": len(rows),
        "ok": sum(1 for r in rows if r.get("ok")),
        "soft": len(softs),
        "fail": len(fails),
        "elapsed_s": round(time.time() - t0, 1),
        "rows": rows,
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(
        f"\n→ {OUT}\n"
        f"OK={summary['ok']}  soft_empty={summary['soft']}  "
        f"FAIL={summary['fail']}  ({summary['elapsed_s']}s)"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
