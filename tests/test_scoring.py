"""Tests scoring / pick_best sans réseau."""

from __future__ import annotations

import unittest

from annie.scoring import catalog_episode_pick_rank, pick_best, rank_entry
from annie.types import MediaKind, ResultItem, WatchTarget
from tests.helpers import nyaa_entry


class RankEntryTests(unittest.TestCase):
    def test_higher_seeders_boost_same_episode(self) -> None:
        target = WatchTarget(
            query="re zero", season=1, episode=8, kind=MediaKind.EPISODE
        )
        title = "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 08 (1080p).mkv"
        low = nyaa_entry(title, seeders=10)
        high = nyaa_entry(title, seeders=200)
        picked = pick_best([low, high], target)
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0].seeders, 200)

    def test_rejects_manga(self) -> None:
        target = WatchTarget(query="re zero", season=1, episode=1)
        entry = nyaa_entry("[Group] Re Zero Manga Chapter 01")
        self.assertIsNone(rank_entry(entry, target))


class CatalogEpisodeScoreTests(unittest.TestCase):
    def test_batch_beats_directors_cut(self) -> None:
        batch = nyaa_entry(
            "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu (01-25) (1080p) [Batch]",
            seeders=102,
        )
        batch_rank = rank_entry(
            batch, WatchTarget(query="re zero"), match_queries=["re zero"]
        )
        self.assertIsNotNone(batch_rank)
        batch_item = ResultItem(entry=batch, parsed=batch_rank[1], score=batch_rank[0])

        dc = nyaa_entry(
            "Re.ZERO.Starting.Life.in.Another.World.Directors.Cut.S01E08.MULTi.1080p",
            seeders=1,
        )
        dc_rank = rank_entry(
            dc, WatchTarget(query="re zero"), match_queries=["re zero"]
        )
        self.assertIsNotNone(dc_rank)
        dc_item = ResultItem(entry=dc, parsed=dc_rank[1], score=dc_rank[0])

        self.assertGreater(
            catalog_episode_pick_rank(batch_item),
            catalog_episode_pick_rank(dc_item),
        )


class PickBestTests(unittest.TestCase):
    def test_picks_highest_ranked(self) -> None:
        target = WatchTarget(
            query="re zero", season=2, episode=5, kind=MediaKind.EPISODE
        )
        entries = [
            nyaa_entry("[Erai-raws] Re:Zero 2nd Season - 04 [1080p]", seeders=10),
            nyaa_entry("[Erai-raws] Re:Zero 2nd Season - 05 [1080p]", seeders=10),
            nyaa_entry("[Erai-raws] Re:Zero 2nd Season - 06 [1080p]", seeders=10),
        ]
        picked = pick_best(entries, target)
        self.assertIsNotNone(picked)
        self.assertEqual(picked[1].episode, 5)


class PreferredResolutionTests(unittest.TestCase):
    def test_bonus_for_preferred_res(self) -> None:
        from unittest.mock import Mock, patch

        from annie.scoring import torrent_quality_score

        catalog = Mock(
            preferred_resolution="1080p",
            preferred_groups=[],
            preferred_group_bonus=10,
        )
        with patch(
            "annie.config.AnnieConfig.load_cached",
            return_value=Mock(catalog=catalog),
        ):
            hi = torrent_quality_score("[G] Show - 01 [1080p]", None)
            lo = torrent_quality_score("[G] Show - 01 [720p]", None)
        self.assertGreater(hi, lo)


if __name__ == "__main__":
    unittest.main()
