#!/usr/bin/env python3
"""Audit méta rapide : pick + saisons TV pour 100 queries (sans Nyaa)."""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from validate_franchise import QUERIES
from annie import metadata as meta
from annie.mal import franchise_to_releases, is_ambiguous_pick, pick_candidate
from annie.types import MediaKind

OUT = ROOT / "scripts/results/meta_audit_100.json"


def audit_one(query: str) -> dict:
    t1 = time.time()
    cands = meta.search_anime(query)
    chosen = pick_candidate(cands, query) if cands else None
    if not chosen:
        return {"query": query, "ok": False, "error": "no_pick", "dt": round(time.time() - t1, 1)}
    amb = is_ambiguous_pick(cands, query)
    fr = meta.collect_franchise(chosen)
    rels = franchise_to_releases(fr, root_id=chosen.mal_id, user_query=query)
    tv = [
        {"label": r.label, "episodes": r.episode_count, "mal_id": r.mal_id}
        for r in rels
        if r.kind == MediaKind.EPISODE and r.season is not None
    ]
    title = chosen.title_english or chosen.title
    flags: list[str] = []
    if amb:
        flags.append("ambiguous")
    root_tokens = {
        t for t in (title or "").lower().replace(":", " ").split() if len(t) >= 4
    }
    qt = {t for t in query.lower().split() if len(t) >= 3}
    for season in tv:
        short = (
            season["label"].split(" · ")[-1].lower()
            if " · " in season["label"]
            else season["label"].lower()
        )
        st = {t for t in short.replace(":", " ").split() if len(t) >= 4}
        if root_tokens and st and not (root_tokens & st):
            if qt and not (qt & st):
                flags.append(f"divergent:{season['label'][:48]}")
        if (season["episodes"] or 0) > 200:
            flags.append(f"huge:{season['label'][:40]}")
    if len(tv) >= 6:
        flags.append(f"many_seasons:{len(tv)}")
    if chosen.type == "Movie" and "movie" not in query.lower():
        flags.append("picked_movie")
    return {
        "query": query,
        "ok": True,
        "title": title,
        "type": chosen.type,
        "anilist_id": chosen.anilist_id,
        "mal_id": chosen.mal_id,
        "seasons": tv,
        "flags": flags,
        "ambiguous": amb,
        "dt": round(time.time() - t1, 1),
    }


def main() -> None:
    queries = QUERIES[:100]
    rows: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(audit_one, q): q for q in queries}
        done = 0
        for fut in as_completed(futures):
            q = futures[fut]
            done += 1
            try:
                row = fut.result()
            except Exception as exc:
                row = {"query": q, "ok": False, "error": str(exc)}
            rows.append(row)
            flags = ",".join(row.get("flags") or [])
            status = (
                "FAIL"
                if not row.get("ok")
                else ("WARN" if row.get("flags") else "OK")
            )
            title = (row.get("title") or row.get("error") or "")[:40]
            n = len(row.get("seasons") or [])
            print(
                f"[{done:03d}/{len(queries)}] {status:4} {q[:28]:28} → {title:40} "
                f"S={n} {flags}",
                flush=True,
            )
            if done % 10 == 0:
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(
                    json.dumps(
                        {"elapsed": round(time.time() - t0, 1), "rows": rows},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

    rows.sort(key=lambda r: queries.index(r["query"]) if r["query"] in queries else 999)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"elapsed": round(time.time() - t0, 1), "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    warns = [r for r in rows if r.get("flags")]
    fails = [r for r in rows if not r.get("ok")]
    print(
        f"\nDone {len(rows)} in {time.time() - t0:.0f}s — fails={len(fails)} warns={len(warns)}",
        flush=True,
    )
    for r in fails + warns:
        print(
            f"  ! {r['query']}: {r.get('error') or r.get('title')} | {r.get('flags')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
