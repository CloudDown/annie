"""Tests métadonnées (labels, synonymes, ambiguïté) hors réseau."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from annie.config import AnnieConfig, reload_config
from annie.mal import (
    MalAnime,
    franchise_to_releases,
    nyaa_queries_for,
)


def _anime(**kwargs) -> MalAnime:
    base = dict(
        mal_id=1,
        title="Tengen Toppa Gurren Lagann",
        title_english="Gurren Lagann",
        title_japanese="天元突破グレンラガン",
        type="TV",
        episodes=27,
        aired_from="2007-04-01",
        synonyms=("Gurren Lagann", "TTGL"),
        anilist_id=2001,
    )
    base.update(kwargs)
    return MalAnime(**base)


class MetadataUnitTests(unittest.TestCase):
    def tearDown(self) -> None:
        reload_config()

    def test_nyaa_queries_include_synonyms(self) -> None:
        queries = nyaa_queries_for(_anime(), user_query="gurren")
        self.assertIn("TTGL", queries)
        self.assertIn("Gurren Lagann", queries)

    def test_season_label_enriched(self) -> None:
        s2 = _anime(
            mal_id=2,
            title="Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season",
            title_english="Re:Zero Season 2",
            episodes=25,
            aired_from="2020-07-08",
            via_relation="Sequel",
        )
        s1 = _anime(
            mal_id=1,
            title="Re:Zero kara Hajimeru Isekai Seikatsu",
            title_english="Re:Zero",
            episodes=25,
            aired_from="2016-04-04",
            via_relation="Root",
        )
        releases = franchise_to_releases([s1, s2], root_id=1, user_query="re zero")
        seasons = [r for r in releases if r.season is not None]
        self.assertEqual(len(seasons), 2)
        self.assertIn("2016", seasons[0].label)
        self.assertIn("25 ep", seasons[0].label)
        self.assertTrue(seasons[0].label.startswith("Season 01"))

    def test_oshi_no_ko_not_confused_with_hoshi(self) -> None:
        poron = _anime(
            mal_id=1,
            title="Hoshi no Ko Poron",
            title_english=None,
            synonyms=(),
            aired_from="1974-01-04",
            episodes=260,
            anilist_id=11213,
        )
        oshi = _anime(
            mal_id=2,
            title="[Oshi no Ko]",
            title_english="Oshi No Ko",
            synonyms=(),
            aired_from="2023-04-12",
            episodes=11,
            anilist_id=150672,
        )
        from annie.mal import pick_candidate

        picked = pick_candidate([poron, oshi], "oshi no ko")
        self.assertEqual(picked.mal_id, 2)

    def test_prefer_base_season_over_sequel_title(self) -> None:
        from annie.mal import pick_candidate

        s1 = _anime(
            mal_id=1,
            title="Noragami",
            title_english="Noragami",
            episodes=12,
            aired_from="2014-01-05",
            synonyms=(),
        )
        s2 = _anime(
            mal_id=2,
            title="Noragami Aragoto",
            title_english="Noragami Aragoto",
            episodes=13,
            aired_from="2015-10-03",
            synonyms=(),
            anilist_id=2002,
        )
        self.assertEqual(pick_candidate([s2, s1], "noragami").mal_id, 1)

    def test_ona_series_becomes_season(self) -> None:
        ona = _anime(
            mal_id=3,
            title="Cyberpunk: Edgerunners",
            title_english="Cyberpunk: Edgerunners",
            type="ONA",
            episodes=10,
            aired_from="2022-09-13",
            synonyms=(),
            via_relation="Root",
        )
        releases = franchise_to_releases([ona], root_id=3, user_query="cyberpunk")
        seasons = [r for r in releases if r.season]
        self.assertEqual(len(seasons), 1)
        self.assertEqual(seasons[0].episode_count, 10)

    def test_railgun_excludes_index(self) -> None:
        railgun = _anime(
            mal_id=10,
            title="Toaru Kagaku no Railgun",
            title_english="A Certain Scientific Railgun",
            episodes=24,
            aired_from="2009-10-03",
            via_relation="Root",
            synonyms=(),
        )
        index_seq = _anime(
            mal_id=11,
            title="Toaru Majutsu no Index",
            title_english="A Certain Magical Index",
            episodes=24,
            aired_from="2008-10-04",
            via_relation="Sequel",
            synonyms=(),
            anilist_id=3001,
        )
        releases = franchise_to_releases(
            [railgun, index_seq], root_id=10, user_query="railgun"
        )
        labels = " ".join(r.label for r in releases if r.season)
        self.assertIn("Railgun", labels)
        self.assertNotIn("Index", labels)

        root = _anime(
            mal_id=9253,
            title="Steins;Gate",
            title_english="Steins;Gate",
            episodes=24,
            aired_from="2011-04-06",
            via_relation="Root",
            synonyms=(),
        )
        zero = _anime(
            mal_id=30484,
            title="Steins;Gate 0",
            title_english="Steins;Gate 0",
            episodes=23,
            aired_from="2018-04-12",
            via_relation="Sequel",
            synonyms=(),
        )
        chaos = _anime(
            mal_id=34599,
            title="ChäoS;Child",
            title_english="Chaos;Child",
            episodes=13,
            aired_from="2017-01-11",
            via_relation="Sequel",
            synonyms=(),
        )
        releases = franchise_to_releases(
            [root, zero, chaos], root_id=9253, user_query="steins gate"
        )
        labels = [r.label for r in releases if r.season]
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("Steins;Gate" in label for label in labels))
        self.assertFalse(any("Chaos" in label for label in labels))

        fma03 = _anime(
            mal_id=121,
            title="Fullmetal Alchemist",
            title_english="Fullmetal Alchemist",
            episodes=51,
            aired_from="2003-10-04",
            via_relation="Alternative Version",
            synonyms=(),
        )
        brotherhood = _anime(
            mal_id=5114,
            title="Fullmetal Alchemist: Brotherhood",
            title_english="Fullmetal Alchemist: Brotherhood",
            episodes=64,
            aired_from="2009-04-05",
            via_relation="Root",
            synonyms=(),
        )
        releases = franchise_to_releases(
            [brotherhood, fma03],
            root_id=5114,
            user_query="fullmetal alchemist brotherhood",
        )
        seasons = [r for r in releases if r.season]
        self.assertEqual(len(seasons), 1)
        self.assertEqual(seasons[0].mal_id, 5114)

    def test_metadata_provider_default_anilist(self) -> None:
        toml = """
[metadata]
provider = "anilist"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.metadata.provider, "anilist")
        self.assertTrue(cfg.metadata.fallback_mal)


if __name__ == "__main__":
    unittest.main()
