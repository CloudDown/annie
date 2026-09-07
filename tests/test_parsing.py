"""Tests unitaires parsing / catalogue (sans réseau)."""

from __future__ import annotations

import unittest

from annie.catalog import (
    _episode_belongs_to_release,
    _gap_search_queries,
    franchise_absolute_offsets,
    is_franchise_multi_season_batch,
    is_spinoff,
    normalize_section_episodes,
    parse_batch_episode_range,
)
from annie.parsing import match_episode_filename, parse_title, series_match_score
from annie.mal import _title_shortcuts
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
        p = parse_title(
            "[Erai-raws] Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season - 05 [1080p]"
        )
        self.assertEqual(p.season, 2)
        self.assertEqual(p.episode, 5)

    def test_dash_episode_defaults_season_one(self) -> None:
        p = parse_title(
            "[HorribleSubs] Re Zero kara Hajimeru Isekai Seikatsu - 15 [1080p].mkv"
        )
        self.assertEqual(p.season, 1)
        self.assertEqual(p.episode, 15)

    def test_absolute_episode_no_default_season(self) -> None:
        p = parse_title(
            "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 42 (720p).mkv"
        )
        self.assertIsNone(p.season)
        self.assertEqual(p.episode, 42)

    def test_spinoff_petit(self) -> None:
        title = "[Pn8] Re:PETIT -Starting Life in Another World From PETIT- S01E14"
        self.assertTrue(is_spinoff(title))

    def test_ordinal_dash_season(self) -> None:
        p = parse_title(
            "[AsukaRaws] Re Zero kara Hajimeru Isekai Seikatsu 4th - 08 (74) (WEB-DL 1280x720)"
        )
        self.assertEqual(p.season, 4)
        self.assertEqual(p.episode, 8)

    def test_season_pack_s02_web(self) -> None:
        p = parse_title(
            "[Ironclad] Sousou no Frieren - S02 [WEB.1080p.AV1] | Frieren: Beyond Journey's End"
        )
        self.assertEqual(p.kind, MediaKind.BATCH)
        self.assertEqual(p.season, 2)

    def test_season_pack_ordinal(self) -> None:
        p = parse_title(
            "[DB] Sousou no Frieren 2nd Season | Frieren: Beyond Journey's End Season 2 [1080p]"
        )
        self.assertEqual(p.kind, MediaKind.BATCH)
        self.assertEqual(p.season, 2)

    def test_season_pack_remux(self) -> None:
        p = parse_title(
            "[PMR] Frieren Beyond Journey's End (BD Remux 1080p AVC FLAC AAC) [Dual Audio]"
        )
        self.assertEqual(p.kind, MediaKind.BATCH)

    def test_soundtrack_non_anime(self) -> None:
        p = parse_title(
            "[ZAIA] Frieren Beyond Journey's End Original Soundtrack (TVアニメ)"
        )
        self.assertEqual(p.kind, MediaKind.UNKNOWN)

    def test_dot_notation_episode(self) -> None:
        p = parse_title(
            "Fullmetal.Alchemist.Brotherhood.53.v2.1080p.BluRay.Dual-Audio.FLAC2.0.Hi10P.x264-JySzE.mkv"
        )
        self.assertEqual(p.kind, MediaKind.EPISODE)
        self.assertEqual(p.episode, 53)

    def test_year_pack_batch(self) -> None:
        p = parse_title("[Commie] JoJo's Bizarre Adventure (2012)")
        self.assertEqual(p.kind, MediaKind.BATCH)
        self.assertEqual(p.season, 1)

    def test_manga_chapter_year(self) -> None:
        p = parse_title("Chainsaw Man 232 (2026)")
        self.assertEqual(p.kind, MediaKind.MANGA)

    def test_se_batch_range(self) -> None:
        p = parse_title("Pokemon Horizons The Series S01E112-E123 1080p")
        self.assertEqual(p.kind, MediaKind.BATCH)
        self.assertEqual(p.season, 1)

    def test_one_piece_high_episode(self) -> None:
        p = parse_title(
            "[SubsPlease] One Piece - 1168 (1080p) [0A043BA1].mkv"
        )
        self.assertEqual(p.kind, MediaKind.EPISODE)
        self.assertEqual(p.episode, 1168)

    def test_clannad_after_story_episode(self) -> None:
        p = parse_title(
            "[XedO-SpeedSubs] Clannad After Story 03 (X264) [9484CEC5].mkv"
        )
        self.assertEqual(p.kind, MediaKind.EPISODE)
        self.assertEqual(p.episode, 3)

    def test_clannad_after_story_batch(self) -> None:
        p = parse_title(
            "[inFIN] Clannad: After Story (1080p 10-bit h.264 | 5.1ch AAC | Finnish sub)"
        )
        self.assertEqual(p.kind, MediaKind.BATCH)


class MovieVsPackTests(unittest.TestCase):
    def test_franchise_pack_with_movie_is_batch(self) -> None:
        titles = [
            "[Cerberus] Konosuba S1 + S2 + OVA + Kurenai Densetsu Movie [BD]",
            "[Tenrai] KonoSuba S1+S2+OVAs+Movie [BD][1080p]",
            "[Anime Time] Konosuba S01+02+OVA+Movie [Dual Audio]",
            "KonoSuba - INTEGRALE S01 / S02 / Film",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(parse_title(title).kind, MediaKind.BATCH)

    def test_standalone_movie_stays_movie(self) -> None:
        titles = [
            "[EMBER] KONOSUBA Legend of Crimson - Movie (2019) [BDRip]",
            "[Group] Demon Slayer - Mugen Train Movie [1080p]",
            "[Erai-raws] Evangelion 1.0 You Are (Not) Alone - Movie [1080p].mkv",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(parse_title(title).kind, MediaKind.MOVIE)


class BatchRangeTests(unittest.TestCase):
    def test_second_season_batch(self) -> None:
        title = (
            "[Erai-raws] Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season 1~13 [1080p]"
        )
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

    def test_season_dash_episode_not_batch_range(self) -> None:
        """« Season 3 - 04 » = S03E04, pas épisodes 3–4 ni pack multi-saisons."""
        title = (
            "[Chihiro] Re Zero kara Hajimeru Isekai Seikatsu Season 3 - 04 "
            "[1080p HEVC AAC][38AEFA74].mkv"
        )
        season, eps = parse_batch_episode_range(title)
        self.assertEqual(season, 3)
        self.assertEqual(eps, [])
        self.assertFalse(is_franchise_multi_season_batch(title))

    def test_seasons_plural_span_still_multi(self) -> None:
        title = "[Anime Time] Attack On Titan (Complete) (Seasons 1-4) [BD]"
        self.assertTrue(is_franchise_multi_season_batch(title))
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
        self.assertTrue(_episode_belongs_to_release(item, s2, absolute_offset=25))

    def test_s2_rejects_seasonless_s1_without_offset(self) -> None:
        """AllAnime S2 seul (offset 0) : ne pas prendre Youjo Senki - 01 (S1)."""
        s2 = MalRelease(
            mal_id=2,
            label="Youjo Senki II",
            kind=MediaKind.EPISODE,
            season=2,
            episode_count=12,
            nyaa_queries=["youjo senki", "tanya"],
            sort_key=(2, "youjo senki ii"),
        )
        s1_ep = _item("[SubsPlease] Youjo Senki - 01 (1080p) [ABC].mkv")
        self.assertFalse(_episode_belongs_to_release(s1_ep, s2, absolute_offset=0))

    def test_s2_accepts_roman_marked_title(self) -> None:
        s2 = MalRelease(
            mal_id=2,
            label="Youjo Senki II",
            kind=MediaKind.EPISODE,
            season=2,
            episode_count=12,
            nyaa_queries=["youjo senki"],
            sort_key=(2, "youjo senki ii"),
        )
        s2_ep = _item("[SubsPlease] Youjo Senki II - 01 (1080p).mkv")
        self.assertTrue(_episode_belongs_to_release(s2_ep, s2, absolute_offset=0))


class RomanSeasonParseTests(unittest.TestCase):
    def test_youjo_senki_ii_episode(self) -> None:
        from annie.parsing import parse_title, title_marks_season

        parsed = parse_title("[SubsPlease] Youjo Senki II - 01 (1080p).mkv")
        self.assertEqual(parsed.season, 2)
        self.assertEqual(parsed.episode, 1)
        self.assertTrue(title_marks_season(parsed.raw, 2))
        self.assertFalse(
            title_marks_season("[SubsPlease] Youjo Senki - 01 (1080p).mkv", 2)
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

    def test_erai_end_suffix_matches_episode(self) -> None:
        name = (
            "[Erai-raws] Youjo Senki - 12 END [1080p][Multiple Subtitle].mkv"
        )
        self.assertTrue(match_episode_filename(name, 12, season=1))
        self.assertFalse(match_episode_filename(name, 2, season=1))

    def test_dash_episode_digit_boundaries(self) -> None:
        self.assertFalse(
            match_episode_filename("[G] Show - 13 [1080p].mkv", 3, season=1)
        )
        self.assertFalse(
            match_episode_filename("[G] Show - 12 [1080p].mkv", 1, season=1)
        )
        self.assertTrue(
            match_episode_filename("[G] Show - 03 [1080p].mkv", 3, season=1)
        )


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


class SeriesMatchTests(unittest.TestCase):
    def test_series_match_ignores_nyaa_alias(self) -> None:
        title = (
            "[Group] Other Anime - S02E02 (1080p) "
            "| (Subtitle 2nd Quest Edition)"
        )
        parsed = parse_title(title)
        self.assertLess(series_match_score(parsed, "hero quest"), 0)

    def test_title_shortcuts_skips_long_titles(self) -> None:
        self.assertEqual(_title_shortcuts("Attack on Titan"), [])
        self.assertEqual(_title_shortcuts("Cowboy Bebop"), ["Cowboy"])


class FinalSeasonBelongsTests(unittest.TestCase):
    def test_final_season_rejected_on_non_max_release(self) -> None:
        title = "[Group] Example Series - Final Season - 26 [1080p]"
        release = MalRelease(
            mal_id=2,
            label="Season 02",
            kind=MediaKind.EPISODE,
            season=2,
            episode_count=12,
            nyaa_queries=["example series"],
            sort_key=(2, "season 02"),
        )
        item = _item(title)
        self.assertFalse(
            _episode_belongs_to_release(
                item, release, absolute_offset=25, max_tv_season=4
            )
        )

    def test_final_season_accepted_on_max_release(self) -> None:
        title = "[Group] Example Series - Final Season - 05 [1080p]"
        release = MalRelease(
            mal_id=4,
            label="Season 04",
            kind=MediaKind.EPISODE,
            season=4,
            episode_count=28,
            nyaa_queries=["example series"],
            sort_key=(4, "season 04"),
        )
        item = _item(title)
        self.assertTrue(
            _episode_belongs_to_release(item, release, max_tv_season=4)
        )


if __name__ == "__main__":
    unittest.main()
