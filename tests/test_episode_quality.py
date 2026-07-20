"""Tests qualité / seeders des épisodes catalogue."""

from __future__ import annotations

import unittest

from annie.catalog import build_catalog_from_releases
from annie.scoring import rank_entry
from annie.scoring import (
    MIN_SEEDERS_STRICT,
    assess_episode_item,
    assess_tv_catalog,
)
from annie.types import MediaKind, ResultItem, WatchTarget
from tests.helpers import entries_from_fixture, load_fixture, mal_release, nyaa_entry


class AssessEpisodeTests(unittest.TestCase):
    def test_flags_low_seeders_and_directors_cut(self) -> None:
        entry = nyaa_entry(
            "Re.ZERO.Directors.Cut.S01E08.MULTi.1080p",
            seeders=5,
        )
        rank = rank_entry(entry, WatchTarget(query="re zero"))
        self.assertIsNotNone(rank)
        item = ResultItem(entry=entry, parsed=rank[1], score=rank[0])
        assessment = assess_episode_item(item, season=1)
        self.assertIn("low_seeders", assessment.flags)
        self.assertIn("directors_cut", assessment.flags)
        self.assertFalse(assessment.strict_ok)

    def test_dead_torrent_flagged(self) -> None:
        entry = nyaa_entry("Re.ZERO.Directors.Cut.S01E08.MULTi.1080p", seeders=1)
        rank = rank_entry(entry, WatchTarget(query="re zero"))
        item = ResultItem(entry=entry, parsed=rank[1], score=rank[0])
        assessment = assess_episode_item(item, season=1)
        self.assertIn("dead", assessment.flags)
        self.assertFalse(assessment.relaxed_ok)

    def test_season_unmarked_flagged_on_s2(self) -> None:
        entry = nyaa_entry("[SubsPlease] Youjo Senki - 01 (1080p).mkv", seeders=50)
        rank = rank_entry(entry, WatchTarget(query="youjo senki"))
        self.assertIsNotNone(rank)
        item = ResultItem(entry=entry, parsed=rank[1], score=rank[0])
        assessment = assess_episode_item(item, season=2)
        self.assertIn("season_unmarked", assessment.flags)
        self.assertFalse(assessment.strict_ok)

    def test_batch_1080p_is_strict_ok(self) -> None:
        entry = nyaa_entry(
            "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu (01-25) (1080p) [Batch]",
            seeders=102,
        )
        rank = rank_entry(entry, WatchTarget(query="re zero"))
        self.assertIsNotNone(rank)
        item = ResultItem(entry=entry, parsed=rank[1], score=rank[0])
        from annie.catalog import item_for_episode

        ep8 = item_for_episode(item, 8)
        assessment = assess_episode_item(ep8, season=1)
        self.assertGreaterEqual(assessment.seeders, MIN_SEEDERS_STRICT)
        self.assertGreaterEqual(assessment.quality, 38)
        self.assertTrue(assessment.strict_ok)


class CatalogEpisodePickTests(unittest.TestCase):
    def test_batch_beats_directors_cut_for_same_episode(self) -> None:
        fixture = load_fixture("catalog_quality_re_zero.json")
        releases = [
            mal_release(
                mal_id=row["mal_id"],
                season=row["season"],
                episode_count=row["episode_count"],
                label=row["label"],
            )
            for row in fixture["releases"]
        ]

        def fake_search(query: str, **kwargs):
            return entries_from_fixture(fixture)

        sections = build_catalog_from_releases(
            releases,
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        s1 = next(s for s in sections if s.season == 1)
        ep8 = s1.episodes[8]
        self.assertIn("SubsPlease", ep8.entry.title)
        self.assertGreaterEqual(ep8.entry.seeders, 10)
        self.assertNotIn("Director", ep8.entry.title)


class CatalogQualityFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture("catalog_quality_re_zero.json")
        cls.releases = [
            mal_release(
                mal_id=row["mal_id"],
                season=row["season"],
                episode_count=row["episode_count"],
                label=row["label"],
            )
            for row in cls.fixture["releases"]
        ]

    def test_quality_expectations_met(self) -> None:
        def fake_search(query: str, **kwargs):
            return entries_from_fixture(self.fixture)

        sections = build_catalog_from_releases(
            self.releases,
            search=fake_search,
            category="1_2",
            filter_code="0",
        )
        mal_tv = [(r.label, r.episode_count) for r in self.releases]
        nyaa_tv = [s for s in sections if s.kind == MediaKind.EPISODE]
        report = assess_tv_catalog(mal_tv, nyaa_tv)

        for season_str, episodes in self.fixture.get(
            "quality_expectations", {}
        ).items():
            season = int(season_str)
            section = next(s for s in report.seasons if s.season == season)
            for ep_str, rules in episodes.items():
                episode = int(ep_str)
                assessment = next(a for a in section.episodes if a.episode == episode)
                self.assertGreaterEqual(
                    assessment.seeders,
                    rules["min_seeders"],
                    msg=f"S{season:02d}E{episode:02d}",
                )
                self.assertGreaterEqual(
                    assessment.quality,
                    rules["min_quality"],
                    msg=f"S{season:02d}E{episode:02d}",
                )
                for banned in rules.get("must_not_contain", []):
                    self.assertNotIn(
                        banned,
                        assessment.title,
                        msg=f"S{season:02d}E{episode:02d}",
                    )
                self.assertTrue(assessment.strict_ok, msg=assessment.flags)


class AssessTvCatalogTests(unittest.TestCase):
    def test_detects_low_seed_in_report(self) -> None:
        from annie.types import MediaSection

        section = MediaSection(
            key="mal:1",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=1,
        )
        low = nyaa_entry("[Group] Anime - 01 [480p].mkv", seeders=1)
        rank = rank_entry(low, WatchTarget(query="anime"))
        self.assertIsNotNone(rank)
        section.episodes[1] = ResultItem(entry=low, parsed=rank[1], score=rank[0])

        report = assess_tv_catalog([("Season 01", 1)], [section])
        self.assertFalse(report.strict_ok)
        self.assertTrue(any("S01E01" in issue for issue in report.seasons[0].issues))


if __name__ == "__main__":
    unittest.main()
