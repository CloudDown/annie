"""Tests scoring / pick_best sans réseau."""

from __future__ import annotations

import unittest

from annie.scoring import pick_best, rank_entry
from annie.types import MediaKind, WatchTarget
from tests.helpers import nyaa_entry


class RankEntryTests(unittest.TestCase):
    def test_higher_seeders_boost_same_episode(self) -> None:
        target = WatchTarget(query="re zero", season=1, episode=8, kind=MediaKind.EPISODE)
        title = "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 08 (1080p).mkv"
        low = nyaa_entry(title, seeders=10)
        high = nyaa_entry(title, seeders=200)
        low_score = rank_entry(low, target)
        high_score = rank_entry(high, target)
        self.assertIsNotNone(low_score)
        self.assertIsNotNone(high_score)
        self.assertGreater(high_score[0], low_score[0])

    def test_rejects_manga(self) -> None:
        target = WatchTarget(query="re zero", season=1, episode=1)
        entry = nyaa_entry("[Group] Re Zero Manga Chapter 01")
        self.assertIsNone(rank_entry(entry, target))


class PickBestTests(unittest.TestCase):
    def test_picks_highest_ranked(self) -> None:
        target = WatchTarget(query="re zero", season=2, episode=5, kind=MediaKind.EPISODE)
        entries = [
            nyaa_entry("[Erai-raws] Re:Zero 2nd Season - 04 [1080p]", seeders=10),
            nyaa_entry("[Erai-raws] Re:Zero 2nd Season - 05 [1080p]", seeders=10),
            nyaa_entry("[Erai-raws] Re:Zero 2nd Season - 06 [1080p]", seeders=10),
        ]
        picked = pick_best(entries, target)
        self.assertIsNotNone(picked)
        self.assertEqual(picked[1].episode, 5)


if __name__ == "__main__":
    unittest.main()
