"""Tests unitaires parsing / catalogue (sans réseau)."""

from __future__ import annotations

import unittest

from annie.catalog import (
    _episode_belongs_to_release,
    _gap_search_queries,
    franchise_absolute_offsets,
    is_spinoff,
    normalize_section_episodes,
    parse_batch_episode_range,
)
from annie.parsing import match_episode_filename, parse_title
from annie.types import MalRelease, MediaKind, MediaSection, ResultItem
from annie.nyaa import NyaaEntry


def _entry(title: str) -> NyaaEntry:
    return NyaaEntry(
        title=title,
        magnet="magnet:?xt=urn:btih:0",
        size="100 MiB",
        date="2024-01-01",
        seeders=10,
        leechers=1,
        downloads=100,
        trusted=True,
    )


def _item(title: str, **parsed_kw) -> ResultItem:
    parsed = parse_title(title)
    if parsed_kw:
        from dataclasses import replace

        parsed = replace(parsed, **parsed_kw)
    return ResultItem(entry=_entry(title), parsed=parsed, score=100.0)


class ParseTitleTests(unittest.TestCase):
    def test_single_episode_not_batch(self) -> None:
        p = parse_title("[Erai-raws] Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season - 05 [1080p]")
        self.assertEqual(p.season, 2)
        self.assertEqual(p.episode, 5)

    def test_dash_episode_defaults_season_one(self) -> None:
        p = parse_title("[HorribleSubs] Re Zero kara Hajimeru Isekai Seikatsu - 15 [1080p].mkv")
        self.assertEqual(p.season, 1)
        self.assertEqual(p.episode, 15)

    def test_absolute_episode_no_default_season(self) -> None:
        p = parse_title("[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 42 (720p).mkv")
        self.assertIsNone(p.season)
        self.assertEqual(p.episode, 42)

    def test_spinoff_petit(self) -> None:
        title = "[Pn8] Re:PETIT -Starting Life in Another World From PETIT- S01E14"
        self.assertTrue(is_spinoff(title))


class BatchRangeTests(unittest.TestCase):
    def test_second_season_batch(self) -> None:
        title = "[Erai-raws] Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season 1~13 [1080p]"
        season, eps = parse_batch_episode_range(title)
        self.assertEqual(season, 2)
        self.assertEqual(eps, list(range(1, 14)))

    def test_alias_pipe_ignored(self) -> None:
        title = (
            "[LostYears] Re: ZERO - S03E08 (CR WEB-DL) | "
            "Re: Zero Kara Hajimeru Isekai Seikatsu Season 3 - 08"
        )
        season, eps = parse_batch_episode_range(title)
        self.assertEqual(eps, [])


class EpisodeBelongsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s1 = MalRelease(
            mal_id=1,
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            episode_count=25,
            nyaa_queries=["re zero"],
            sort_key=(1, "season 01"),
        )

    def test_rejects_wrong_season(self) -> None:
        item = _item(
            "[Erai-raws] Re:Zero 2nd Season 1~13 [1080p]",
            kind=MediaKind.EPISODE,
            season=2,
            episode=1,
        )
        self.assertFalse(_episode_belongs_to_release(item, self.s1))

    def test_accepts_season_one(self) -> None:
        item = _item("[HorribleSubs] Re Zero - 15 [1080p].mkv")
        self.assertTrue(_episode_belongs_to_release(item, self.s1))

    def test_rejects_absolute_high_episode(self) -> None:
        item = _item("[SubsPlease] Re Zero - 42 (720p).mkv")
        self.assertFalse(_episode_belongs_to_release(item, self.s1))

    def test_s2_accepts_franchise_absolute(self) -> None:
        s2 = MalRelease(
            mal_id=2,
            label="Season 02",
            kind=MediaKind.EPISODE,
            season=2,
            episode_count=25,
            nyaa_queries=["re zero"],
            sort_key=(2, "season 02"),
        )
        item = _item("[SubsPlease] Re Zero - 42 (720p).mkv")
        self.assertTrue(
            _episode_belongs_to_release(item, s2, absolute_offset=25)
        )


class FranchiseOffsetTests(unittest.TestCase):
    def test_cumulative_offsets(self) -> None:
        releases = [
            MalRelease(
                mal_id=1,
                label="S1",
                kind=MediaKind.EPISODE,
                season=1,
                episode_count=25,
                nyaa_queries=[],
                sort_key=(1, "s1"),
            ),
            MalRelease(
                mal_id=2,
                label="S2",
                kind=MediaKind.EPISODE,
                season=2,
                episode_count=25,
                nyaa_queries=[],
                sort_key=(2, "s2"),
            ),
            MalRelease(
                mal_id=3,
                label="S3",
                kind=MediaKind.EPISODE,
                season=3,
                episode_count=16,
                nyaa_queries=[],
                sort_key=(3, "s3"),
            ),
        ]
        offsets = franchise_absolute_offsets(releases)
        self.assertEqual(offsets[1], 0)
        self.assertEqual(offsets[2], 25)
        self.assertEqual(offsets[3], 50)


class NormalizeSectionTests(unittest.TestCase):
    def test_absolute_to_relative_with_offset(self) -> None:
        section = MediaSection(
            key="season:02",
            label="Season 02",
            kind=MediaKind.EPISODE,
            season=2,
        )
        section.episodes[42] = _item("[SubsPlease] Re Zero - 42 (720p).mkv")
        normalize_section_episodes(section, 25, absolute_offset=25)
        self.assertIn(17, section.episodes)
        self.assertNotIn(42, section.episodes)


class EpisodeFilenameTests(unittest.TestCase):
    def test_subsplease_batch_filename(self) -> None:
        name = (
            "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 08 "
            "(1080p) [ABCD1234].mkv"
        )
        self.assertTrue(match_episode_filename(name, 8, season=1))

    def test_dash_episode_before_crc(self) -> None:
        name = "[Erai-raws] Re Zero - 08 [1080p][ABC12345].mkv"
        self.assertTrue(match_episode_filename(name, 8, season=1))

    def test_rejects_other_episode(self) -> None:
        name = "[SubsPlease] Re Zero - 09 (1080p) [ABCD1234].mkv"
        self.assertFalse(match_episode_filename(name, 8, season=1))


class GapQueryTests(unittest.TestCase):
    def test_includes_absolute_numbers(self) -> None:
        release = MalRelease(
            mal_id=2,
            label="Season 02",
            kind=MediaKind.EPISODE,
            season=2,
            episode_count=25,
            nyaa_queries=["Re:Zero kara Hajimeru Isekai Seikatsu"],
            sort_key=(2, "season 02"),
        )
        queries = _gap_search_queries(release, [17], absolute_offset=25)
        joined = " ".join(queries).lower()
        self.assertIn("s02e17", joined)
        self.assertIn("42", joined)


if __name__ == "__main__":
    unittest.main()
