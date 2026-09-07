#!/usr/bin/env python3
"""Smoke catalogue offline : Re:Zero, Tanya S2, film Konosuba."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import print  # noqa: E402

from annie.catalog import (
    _movie_belongs_to_release,
    build_catalog_from_releases,
    is_movie_noise,
)
from annie.types import MediaKind
from tests.helpers import (
    catalog_from_fixture,
    mal_release,
    movie_release,
    nyaa_entry,
    result_item,
)


def _ok(name: str) -> None:
    print(f"OK  {name}")


def _fail(name: str, detail: str) -> None:
    print(f"FAIL  {name} — {detail}")


def check_rezero() -> bool:
    name = "Re:Zero saisons"
    sections, _ = catalog_from_fixture("catalog_re_zero.json")
    seasons = {s.season for s in sections if s.kind == MediaKind.EPISODE}
    missing = {1, 2, 3, 4} - seasons
    if missing:
        _fail(name, f"saisons absentes : {sorted(missing)}")
        return False
    _ok(name)
    return True


def check_tanya_s2() -> bool:
    name = "Tanya S2 (pas de S1)"
    s2 = mal_release(
        mal_id=2,
        season=2,
        episode_count=12,
        label="Youjo Senki II",
        queries=["youjo senki", "tanya the evil"],
        absolute_episode_offset=0,
    )
    entries = [
        nyaa_entry("[SubsPlease] Youjo Senki - 01 (1080p) [DEAD].mkv", seeders=500),
        nyaa_entry("[SubsPlease] Youjo Senki II - 01 (1080p).mkv", seeders=40),
        nyaa_entry("[Erai-raws] Youjo Senki II - 02 [1080p].mkv", seeders=35),
    ]

    def fake_search(query: str, **kwargs):
        return entries

    sections = build_catalog_from_releases(
        [s2], search=fake_search, category="1_2", filter_code="0"
    )
    if len(sections) != 1 or sections[0].season != 2:
        _fail(name, f"sections={[(s.season, s.label) for s in sections]}")
        return False
    ep1 = sections[0].episodes.get(1)
    if ep1 is None or "II" not in ep1.entry.title or "DEAD" in ep1.entry.title:
        _fail(name, f"E01 = {ep1.entry.title if ep1 else None}")
        return False
    _ok(name)
    return True


def check_konosuba_movie() -> bool:
    name = "Konosuba film"
    pack = "[Cerberus] Konosuba S1 + S2 + OVA + Kurenai Densetsu Movie [BD]"
    title = "[EMBER] KONOSUBA Legend of Crimson - Movie (2019) [BDRip]"
    release = movie_release(
        queries=["KonoSuba Legend of Crimson", "Kurenai Densetsu"],
    )
    if not is_movie_noise(pack):
        _fail(name, "pack saison accepté comme film")
        return False
    if _movie_belongs_to_release(result_item(pack, score=10.0), release):
        _fail(name, "pack saison rattaché au film")
        return False
    if not _movie_belongs_to_release(result_item(title, score=10.0), release):
        _fail(name, "film standalone rejeté")
        return False
    _ok(name)
    return True


def main() -> int:
    checks = (check_rezero, check_tanya_s2, check_konosuba_movie)
    failed = sum(0 if check() else 1 for check in checks)
    if failed:
        print(f"\n{failed} échec(s)")
        return 1
    print("\nOK — smoke catalogue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
