"""Tests catalogue offline (régressions Re:Zero, scope, resolve)."""

from __future__ import annotations

import unittest

from annie.catalog import (
    build_catalog,
    build_catalog_from_releases,
    is_spinoff,
    parse_batch_episode_range,
    resolve_catalog_target,
    scope_releases_for_target,
)
from annie.types import MediaKind
from tests.helpers import entries_from_fixture, load_fixture, mal_release, result_item


class ReZeroCatalogFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("catalog_re_zero.json")
        cls.entries = entries_from_fixture(cls.fixture)
        cls.releases = [
            mal_release(
                mal_id=row["mal_id"],
                season=row["season"],
                episode_count=row["episode_count"],
                label=row["label"],
            )
            for row in cls.fixture["releases"]
        ]

    def test_no_spinoff_in_catalog(self) -> None:
        for title in self.fixture["entries"]:
            if any(tag in title for tag in self.fixture["reject_spinoffs"]):
                self.assertTrue(is_spinoff(title), msg=title)

    def test_s2_batch_not_parsed_as_s1_range(self) -> None:
        title = "[Erai-raws] Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season 1~13 [1080p]"
        season, eps = parse_batch_episode_range(title)
        self.assertEqual(season, 2)
        self.assertEqual(eps, list(range(1, 14)))

    def test_build_catalog_from_releases_seasons(self) -> None:
        def fake_search(query: str, **kwargs):
            return self.entries

        sections = build_catalog_from_releases(
            self.releases,
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        by_season = {s.season: s for s in sections if s.kind == MediaKind.EPISODE}
        self.assertIn(1, by_season)
        self.assertIn(2, by_season)
        self.assertIn(3, by_season)

        for season_str, rules in self.fixture["expectations"].items():
            season = int(season_str)
            section = by_season[season]
            for ep in rules["must_have"]:
                self.assertIn(ep, section.episodes, msg=f"S{season:02d} E{ep}")
            for ep in rules.get("must_not_have", []):
                self.assertNotIn(ep, section.episodes, msg=f"S{season:02d} pollue E{ep}")

    def test_s1_ep15_is_not_from_s2_release(self) -> None:
        def fake_search(query: str, **kwargs):
            return self.entries

        sections = build_catalog_from_releases(
            self.releases,
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        s1 = next(s for s in sections if s.season == 1)
        ep15 = s1.episodes.get(15)
        self.assertIsNotNone(ep15)
        self.assertNotIn("2nd Season", ep15.entry.title)


class NyaaQueriesForTests(unittest.TestCase):
    def test_season_variants_not_truncated(self) -> None:
        from annie.mal import MalAnime, nyaa_queries_for

        anime = MalAnime(
            mal_id=1,
            title="Re:ZERO -Starting Life in Another World-",
            title_english="Re:ZERO -Starting Life in Another World-",
            title_japanese="",
            type="TV",
            episodes=25,
            aired_from="2016",
            is_recap=False,
            via_relation="Root",
        )
        queries = nyaa_queries_for(anime, user_query="re zero", season=1)
        self.assertIn("re zero S01", queries)
        self.assertIn("re zero Season 01", queries)
        self.assertIn("re zero batch", queries)
        self.assertNotIn("Re S01", queries)
        self.assertNotIn("Re Season 01", queries)


class ScopeReleasesTests(unittest.TestCase):
    def test_filters_to_target_season(self) -> None:
        releases = [
            mal_release(mal_id=1, season=1, episode_count=25),
            mal_release(mal_id=2, season=2, episode_count=25),
        ]
        scoped = scope_releases_for_target(releases, season=2)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0].season, 2)


class ResolveCatalogTargetTests(unittest.TestCase):
    def test_finds_episode_in_section(self) -> None:
        section = mal_release(mal_id=1, season=1, episode_count=25)
        from annie.types import MediaSection

        media = MediaSection(
            key="mal:1",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
        )
        media.episodes[8] = result_item(
            "[SubsPlease] Re Zero - 08 (1080p).mkv",
            kind=MediaKind.BATCH,
            season=1,
            episode=8,
        )
        item = resolve_catalog_target([media], season=1, episode=8)
        self.assertIsNotNone(item)
        self.assertEqual(item.parsed.episode, 8)


class BuildCatalogSmokeTests(unittest.TestCase):
    def test_groups_episodes_by_season(self) -> None:
        entries = entries_from_fixture(load_fixture("catalog_re_zero.json"))
        catalog = build_catalog(entries, "re zero")
        episode_sections = [s for s in catalog if s.kind == MediaKind.EPISODE and s.season]
        self.assertGreaterEqual(len(episode_sections), 2)


if __name__ == "__main__":
    unittest.main()
