"""Tests cohérence intra-saison et priorité batch."""

from __future__ import annotations

import unittest

from annie.catalog import (
    apply_coherent_season_picks,
    build_catalog_from_releases,
)
from annie.scoring import rank_entry
from annie.scoring import assess_tv_catalog
from annie.season_coherence import assess_season_coherence
from annie.types import MediaKind, MediaSection, ResultItem, WatchTarget
from tests.helpers import entries_from_fixture, load_fixture, mal_release, nyaa_entry


def _sections_from_fixture(
    fixture: dict, *, coherent_picks: bool | None = True
) -> list:
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

    return build_catalog_from_releases(
        releases,
        search=fake_search,
        category="1_2",
        filter_code="0",
        coherent_picks=coherent_picks,
    )


class SeasonCoherenceDetectionTests(unittest.TestCase):
    def test_uniform_batch_no_outliers(self) -> None:
        fixture = load_fixture("catalog_coherence_uniform.json")
        sections = _sections_from_fixture(fixture)
        s1 = next(s for s in sections if s.season == 1)
        report = assess_season_coherence(s1)
        self.assertFalse(report.inconsistent)
        self.assertEqual(report.magnet_coverage, 1.0)

    def test_detects_magnet_outlier_before_coherent_picks(self) -> None:
        section = MediaSection(
            key="season:01",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=8,
        )
        pack_magnet = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        for episode in range(1, 8):
            title = f"[SubsPlease] Anime - {episode:02d} (1080p).mkv"
            rank = rank_entry(
                nyaa_entry(title, magnet=pack_magnet), WatchTarget(query="anime")
            )
            assert rank is not None
            section.episodes[episode] = ResultItem(
                entry=nyaa_entry(title, magnet=pack_magnet),
                parsed=rank[1],
                score=rank[0],
            )
        evil_title = "[EvilRips] Anime - 08 (1080p).mkv"
        evil_magnet = "magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc"
        evil_rank = rank_entry(
            nyaa_entry(evil_title, seeders=200, magnet=evil_magnet),
            WatchTarget(query="anime"),
        )
        assert evil_rank is not None
        section.episodes[8] = ResultItem(
            entry=nyaa_entry(evil_title, seeders=200, magnet=evil_magnet),
            parsed=evil_rank[1],
            score=evil_rank[0],
        )

        report = assess_season_coherence(section)
        self.assertTrue(report.inconsistent)
        self.assertEqual(
            [o.episode for o in report.outliers if o.reason == "magnet"],
            [8],
        )

    def test_detects_group_outlier_alert_only(self) -> None:
        section = MediaSection(
            key="season:01",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=10,
        )
        for episode in range(1, 8):
            title = f"[SubsPlease] Anime - {episode:02d} (1080p).mkv"
            magnet = f"magnet:?xt=urn:btih:{episode:040x}"
            rank = rank_entry(nyaa_entry(title, magnet=magnet), WatchTarget(query="anime"))
            assert rank is not None
            section.episodes[episode] = ResultItem(
                entry=nyaa_entry(title, magnet=magnet),
                parsed=rank[1],
                score=rank[0],
            )
        for episode in range(8, 11):
            title = f"[HorribleSubs] Anime - {episode:02d} (1080p).mkv"
            magnet = f"magnet:?xt=urn:btih:{episode + 100:040x}"
            rank = rank_entry(nyaa_entry(title, magnet=magnet), WatchTarget(query="anime"))
            assert rank is not None
            section.episodes[episode] = ResultItem(
                entry=nyaa_entry(title, magnet=magnet),
                parsed=rank[1],
                score=rank[0],
            )

        report = assess_season_coherence(section, coherence_min_share=0.60)
        group_outliers = [o for o in report.outliers if o.reason == "release_group"]
        self.assertEqual([o.episode for o in group_outliers], [8, 9, 10])
        magnet_outliers = [o for o in report.outliers if o.reason == "magnet"]
        self.assertEqual(magnet_outliers, [])


class CoherentSeasonPickTests(unittest.TestCase):
    def test_prefer_batch_over_high_seed_single(self) -> None:
        fixture = load_fixture("catalog_coherence_mixed.json")
        sections = _sections_from_fixture(fixture, coherent_picks=True)
        s1 = next(s for s in sections if s.season == 1)
        ep8 = s1.episodes[8]
        self.assertIn("SubsPlease", ep8.entry.title)
        self.assertNotIn("EvilRips", ep8.entry.title)
        report = assess_season_coherence(s1)
        self.assertFalse(any(o.reason == "magnet" for o in report.outliers))

    def test_outlier_steal_reverted_to_batch(self) -> None:
        fixture = load_fixture("catalog_coherence_outlier_steal.json")
        sections = _sections_from_fixture(fixture, coherent_picks=True)
        s1 = next(s for s in sections if s.season == 1)
        expected_magnet = fixture["coherence_after"]["ep8_magnet"]
        self.assertEqual(s1.episodes[8].entry.magnet, expected_magnet)
        self.assertIn("SubsPlease", s1.episodes[8].entry.title)

    def test_singles_fill_gaps_only(self) -> None:
        fixture = load_fixture("catalog_coherence_gap.json")
        sections = _sections_from_fixture(fixture, coherent_picks=True)
        s1 = next(s for s in sections if s.season == 1)
        after = fixture["coherence_after"]
        self.assertEqual(s1.episodes[13].entry.magnet, after["ep13_magnet"])
        self.assertEqual(s1.episodes[14].entry.magnet, after["ep14_magnet"])

    def test_prefer_batch_false_skips_coherent_fill(self) -> None:
        section = MediaSection(
            key="season:01",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=25,
        )
        batch_magnet = "magnet:?xt=urn:btih:dddddddddddddddddddddddddddddddddddddddd"
        batch_title = "[SubsPlease] Anime (01-25) (1080p) [Batch]"
        batch_item = ResultItem(
            entry=nyaa_entry(batch_title, seeders=90, magnet=batch_magnet),
            parsed=rank_entry(
                nyaa_entry(batch_title, seeders=90, magnet=batch_magnet),
                WatchTarget(query="anime"),
            )[1],
            score=100.0,
        )
        section.singles.append(batch_item)
        evil_title = "[EvilRips] Anime - 08 (1080p).mkv"
        evil_magnet = "magnet:?xt=urn:btih:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        evil_rank = rank_entry(
            nyaa_entry(evil_title, seeders=500, magnet=evil_magnet),
            WatchTarget(query="anime"),
        )
        assert evil_rank is not None
        section.episodes[8] = ResultItem(
            entry=nyaa_entry(evil_title, seeders=500, magnet=evil_magnet),
            parsed=evil_rank[1],
            score=evil_rank[0],
        )

        apply_coherent_season_picks(section, expected=25, prefer_batch=False)
        self.assertEqual(section.episodes[8].entry.magnet, evil_magnet)

        apply_coherent_season_picks(section, expected=25, prefer_batch=True)
        self.assertEqual(section.episodes[8].entry.magnet, batch_magnet)


class ApplyCoherentDirectTests(unittest.TestCase):
    def test_apply_replaces_magnet_outliers(self) -> None:
        section = MediaSection(
            key="season:01",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=25,
        )
        batch_magnet = "magnet:?xt=urn:btih:dddddddddddddddddddddddddddddddddddddddd"
        batch_title = "[SubsPlease] Anime (01-25) (1080p) [Batch]"
        batch_entry = nyaa_entry(batch_title, seeders=90, magnet=batch_magnet)
        batch_rank = rank_entry(batch_entry, WatchTarget(query="anime"))
        assert batch_rank is not None
        batch_item = ResultItem(entry=batch_entry, parsed=batch_rank[1], score=batch_rank[0])
        section.singles.append(batch_item)

        stolen = nyaa_entry(
            "[EvilRips] Anime - 08 (1080p).mkv",
            seeders=500,
            magnet="magnet:?xt=urn:btih:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        )
        stolen_rank = rank_entry(stolen, WatchTarget(query="anime"))
        assert stolen_rank is not None
        section.episodes[8] = ResultItem(
            entry=stolen, parsed=stolen_rank[1], score=stolen_rank[0]
        )

        apply_coherent_season_picks(section, expected=25)
        self.assertEqual(section.episodes[8].entry.magnet, batch_magnet)
        self.assertEqual(
            section.episodes[8].parsed.episode,
            8,
        )


class AssessTvCatalogCoherenceTests(unittest.TestCase):
    def test_reports_magnet_outlier_in_season_issues(self) -> None:
        from annie.types import MediaSection

        section = MediaSection(
            key="season:01",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            expected_episodes=8,
        )
        pack_magnet = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        for episode in range(1, 8):
            title = f"[SubsPlease] Anime - {episode:02d} (1080p).mkv"
            rank = rank_entry(
                nyaa_entry(title, magnet=pack_magnet), WatchTarget(query="anime")
            )
            assert rank is not None
            section.episodes[episode] = ResultItem(
                entry=nyaa_entry(title, magnet=pack_magnet),
                parsed=rank[1],
                score=rank[0],
            )
        evil_title = "[EvilRips] Anime - 08 (1080p).mkv"
        evil_magnet = "magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc"
        evil_rank = rank_entry(
            nyaa_entry(evil_title, seeders=200, magnet=evil_magnet),
            WatchTarget(query="anime"),
        )
        assert evil_rank is not None
        section.episodes[8] = ResultItem(
            entry=nyaa_entry(evil_title, seeders=200, magnet=evil_magnet),
            parsed=evil_rank[1],
            score=evil_rank[0],
        )

        report = assess_tv_catalog([("Season 01", 8)], [section])
        self.assertTrue(report.seasons[0].coherence_outliers)
        self.assertTrue(
            any("outlier magnet" in issue for issue in report.seasons[0].issues)
        )


if __name__ == "__main__":
    unittest.main()
