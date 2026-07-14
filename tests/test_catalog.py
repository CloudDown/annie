"""Tests catalogue offline (régressions Re:Zero, scope, resolve)."""

from __future__ import annotations

import unittest
from dataclasses import replace

from annie.catalog import (
    _batch_coverage,
    _batch_episodes_for_release,
    _fill_missing_episodes,
    build_catalog,
    item_for_episode,
    build_catalog_from_releases,
    is_franchise_multi_season_batch,
    is_spinoff,
    parse_batch_episode_range,
    resolve_catalog_target,
    scope_releases_for_target,
)
from annie.types import MediaKind, MediaSection
from tests.helpers import entries_from_fixture, load_fixture, mal_release, nyaa_entry, result_item


class ReZeroCatalogFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("catalog_re_zero.json")
        cls.entries = entries_from_fixture(cls.fixture)
        cursor = 0
        cls.releases = []
        for row in cls.fixture["releases"]:
            cls.releases.append(
                mal_release(
                    mal_id=row["mal_id"],
                    season=row["season"],
                    episode_count=row["episode_count"],
                    label=row["label"],
                    absolute_episode_offset=cursor,
                )
            )
            cursor += row["episode_count"]

    def test_no_spinoff_in_catalog(self) -> None:
        for title in self.fixture["entries"]:
            if any(tag in title for tag in self.fixture["reject_spinoffs"]):
                self.assertTrue(is_spinoff(title), msg=title)

    def test_s2_batch_not_parsed_as_s1_range(self) -> None:
        title = (
            "[Erai-raws] Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season 1~13 [1080p]"
        )
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
        self.assertIn(4, by_season)

        for season_str, rules in self.fixture["expectations"].items():
            season = int(season_str)
            section = by_season[season]
            for ep in rules["must_have"]:
                self.assertIn(ep, section.episodes, msg=f"S{season:02d} E{ep}")
            for ep in rules.get("must_not_have", []):
                self.assertNotIn(
                    ep, section.episodes, msg=f"S{season:02d} pollue E{ep}"
                )

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

    def test_s4_absolute_episodes_from_subsplease(self) -> None:
        def fake_search(query: str, **kwargs):
            return self.entries

        sections = build_catalog_from_releases(
            self.releases,
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        s4 = next(s for s in sections if s.season == 4)
        ep1 = s4.episodes.get(1)
        ep11 = s4.episodes.get(11)
        self.assertIsNotNone(ep1)
        self.assertIsNotNone(ep11)
        self.assertIn("- 67", ep1.entry.title)
        self.assertIn("- 77", ep11.entry.title)

    def test_s4_scoped_build_keeps_absolute_offset(self) -> None:
        scoped = scope_releases_for_target(self.releases, season=4)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0].absolute_episode_offset, 66)

        def fake_search(query: str, **kwargs):
            return self.entries

        sections = build_catalog_from_releases(
            scoped,
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertEqual(section.season, 4)
        self.assertIn(1, section.episodes)
        self.assertIn(11, section.episodes)
        self.assertNotIn(12, section.episodes)
        self.assertNotIn(25, section.episodes)


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

    def test_season_4_includes_ordinal_variant(self) -> None:
        from annie.mal import MalAnime, nyaa_queries_for

        anime = MalAnime(
            mal_id=4,
            title="Re:ZERO kara Hajimeru Isekai Seikatsu 4th Season",
            title_english="Re:ZERO kara Hajimeru Isekai Seikatsu 4th Season",
            title_japanese="",
            type="TV",
            episodes=19,
            aired_from="2026",
            is_recap=False,
            via_relation="Sequel",
        )
        queries = nyaa_queries_for(anime, user_query="re zero", season=4)
        self.assertIn("re zero 4th Season", queries)
        self.assertIn("re zero S04", queries)


class ScopeReleasesTests(unittest.TestCase):
    def test_filters_to_target_season(self) -> None:
        releases = [
            mal_release(mal_id=1, season=1, episode_count=25),
            mal_release(mal_id=2, season=2, episode_count=25),
        ]
        scoped = scope_releases_for_target(releases, season=2)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0].season, 2)

    def test_target_season_excludes_movies(self) -> None:
        from annie.types import MalRelease

        releases = [
            mal_release(
                mal_id=4,
                season=4,
                episode_count=19,
                absolute_episode_offset=66,
            ),
            MalRelease(
                mal_id=99,
                label="Memory Snow",
                kind=MediaKind.MOVIE,
                season=None,
                episode_count=1,
                nyaa_queries=["re zero movie"],
                sort_key=(99, "movie"),
            ),
        ]
        scoped = scope_releases_for_target(releases, season=4)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0].kind, MediaKind.EPISODE)
        self.assertEqual(scoped[0].absolute_episode_offset, 66)


class ResolveCatalogTargetTests(unittest.TestCase):
    def test_finds_episode_in_section(self) -> None:
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
        episode_sections = [
            s for s in catalog if s.kind == MediaKind.EPISODE and s.season
        ]
        self.assertGreaterEqual(len(episode_sections), 2)


class CodeGeassCatalogFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("catalog_code_geass.json")
        cls.entries = entries_from_fixture(cls.fixture)
        cls.releases = [
            mal_release(
                mal_id=row["mal_id"],
                season=row["season"],
                episode_count=row["episode_count"],
                label=row["label"],
                queries=["code geass"],
            )
            for row in cls.fixture["releases"]
        ]

    def test_seasons_span_not_parsed_as_episode_range(self) -> None:
        title = self.fixture["entries"][0]["title"]
        self.assertTrue(is_franchise_multi_season_batch(title))
        season, eps = parse_batch_episode_range(title)
        self.assertNotEqual(eps, [1, 2])

    def test_build_catalog_from_releases_s2_from_complete_pack(self) -> None:
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

        for season_str, rules in self.fixture["expectations"].items():
            season = int(season_str)
            section = by_season[season]
            self.assertGreaterEqual(
                len(section.episodes), rules["min_episodes"], msg=f"S{season}"
            )
            for ep in rules["must_have"]:
                self.assertIn(ep, section.episodes, msg=f"S{season:02d} E{ep}")
            for ep in rules.get("must_not_have", []):
                self.assertNotIn(
                    ep, section.episodes, msg=f"S{season:02d} pollue E{ep}"
                )

        s2_ep1 = by_season[2].episodes[1]
        self.assertIn("Judas", s2_ep1.entry.title)
        self.assertEqual(
            by_season[1].episodes[1].entry.magnet, s2_ep1.entry.magnet
        )


class FranchiseBatchPickTests(unittest.TestCase):
    BATCH = "[Batch] Black Clover Season 1-4 Complete [1080p]"
    SINGLE = "[SubsPlease] Black Clover - 05 (1080p) [ABCD1234].mkv"

    @classmethod
    def setUpClass(cls) -> None:
        cls.release = mal_release(
            mal_id=1,
            season=1,
            episode_count=51,
            label="Season 01",
            queries=["black clover"],
        )
        cls.batch_magnet = "magnet:?xt=urn:btih:blackcloverbatch"
        cls.single_magnet = "magnet:?xt=urn:btih:blackcloversingle"

    def test_season_span_parsed_as_franchise_not_full_episode_list(self) -> None:
        season, episodes = parse_batch_episode_range(self.BATCH)
        self.assertTrue(is_franchise_multi_season_batch(self.BATCH))
        self.assertLess(len(episodes), self.release.episode_count)

    def test_franchise_batch_maps_all_season_episodes(self) -> None:
        batch = replace(
            result_item(self.BATCH, score=80.0),
            entry=nyaa_entry(self.BATCH, seeders=300, magnet=self.batch_magnet),
        )
        pairs = _batch_episodes_for_release(batch, self.release)
        self.assertEqual(len(pairs), 51)
        self.assertEqual(pairs[4], (5, 5))

    def test_franchise_batch_coverage_is_full_season(self) -> None:
        batch = replace(
            result_item(self.BATCH),
            entry=nyaa_entry(self.BATCH, seeders=300, magnet=self.batch_magnet),
        )
        covered, coverage = _batch_coverage(batch, self.release.episode_count)
        self.assertEqual(len(covered), 51)
        self.assertGreaterEqual(coverage, 0.85)

    def test_catalog_prefers_batch_over_low_seed_singles(self) -> None:
        entries = [
            nyaa_entry(self.BATCH, seeders=300, magnet=self.batch_magnet),
            nyaa_entry(self.SINGLE, seeders=1, magnet=self.single_magnet),
        ]

        def fake_search(query: str, **kwargs):
            return entries

        sections = build_catalog_from_releases(
            [self.release],
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        self.assertEqual(len(sections), 1)
        section = sections[0]
        ep5 = section.episodes.get(5)
        self.assertIsNotNone(ep5)
        self.assertEqual(ep5.entry.seeders, 300)
        self.assertEqual(ep5.entry.magnet, self.batch_magnet)

    def test_gap_fill_does_not_replace_high_seed_batch(self) -> None:
        batch = replace(
            result_item(self.BATCH, score=50.0),
            entry=nyaa_entry(self.BATCH, seeders=300, magnet=self.batch_magnet),
        )
        section = MediaSection(
            key="mal:1",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=51,
            mal_id=1,
            nyaa_queries=["black clover"],
        )
        section.episodes[5] = item_for_episode(batch, 5)

        def fake_search(query: str, **kwargs):
            return [nyaa_entry(self.SINGLE, seeders=1, magnet=self.single_magnet)]

        _fill_missing_episodes(
            self.release,
            section,
            search=fake_search,
            category="1_2",
            filter_code="0",
            skip_recap_movies=False,
            pool=None,
        )
        self.assertEqual(section.episodes[5].entry.magnet, self.batch_magnet)
        self.assertEqual(section.episodes[5].entry.seeders, 300)


class BatchSourceEpisodeTests(unittest.TestCase):
    def test_item_for_episode_does_not_leak_sibling_episode_as_source(self) -> None:
        # apply_coherent_season_picks peut repasser un item déjà à E03 ;
        # E12 ne doit pas hériter de source_episode=3.
        title = "[Erai-raws] Youjo Senki - 01 ~ 12 [1080p][Multiple Subtitle]"
        batch = result_item(title, score=100.0)
        batch = replace(
            batch,
            parsed=replace(
                batch.parsed,
                kind=MediaKind.BATCH,
                season=1,
                episode=3,
                source_episode=None,
            ),
        )
        ep12 = item_for_episode(batch, 12)
        self.assertEqual(ep12.parsed.episode, 12)
        self.assertIsNone(ep12.parsed.source_episode)


if __name__ == "__main__":
    unittest.main()
