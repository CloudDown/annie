"""Tests offline AllAnime → MalRelease (structure ani-cli)."""

from __future__ import annotations

import unittest

from annie.allanime import (
    AllAnimeShow,
    filter_franchise_shows,
    shows_to_releases,
    _franchise_stem,
    _infer_kind,
    _infer_season,
)
from annie.types import MediaKind


def _show(name: str, sub: int) -> AllAnimeShow:
    return AllAnimeShow(show_id=name[:12], name=name, sub_episodes=sub)


class AllAnimeInferTests(unittest.TestCase):
    def test_season_from_trailing_number(self) -> None:
        self.assertEqual(
            _infer_season("Kono Subarashii Sekai ni Shukufuku wo! 2"),
            2,
        )
        self.assertEqual(
            _infer_season("Kono Subarashii Sekai ni Shukufuku wo! 3"),
            3,
        )
        self.assertIsNone(
            _infer_season("Kono Subarashii Sekai ni Shukufuku wo!")
        )

    def test_movie_kind(self) -> None:
        movie = _show(
            "Kono Subarashii Sekai ni Shukufuku wo! Movie: Kurenai Densetsu",
            1,
        )
        self.assertEqual(_infer_kind(movie), MediaKind.MOVIE)

    def test_ova_bonus(self) -> None:
        ova = _show(
            "Kono Subarashii Sekai ni Shukufuku wo! 3: Bonus Stage",
            2,
        )
        self.assertEqual(_infer_kind(ova), MediaKind.OVA)


class AllAnimeShowReleaseTests(unittest.TestCase):
    def test_show_to_release_movie(self) -> None:
        from annie.allanime import show_to_release

        show = _show(
            "Kono Subarashii Sekai ni Shukufuku wo! Movie: Kurenai Densetsu",
            1,
        )
        release = show_to_release(show, user_query="konosuba")
        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.kind, MediaKind.MOVIE)
        self.assertIn("Kurenai", release.label)
        self.assertTrue(release.nyaa_queries)

    def test_show_to_release_season(self) -> None:
        from annie.allanime import show_to_release

        show = _show("Kono Subarashii Sekai ni Shukufuku wo! 2", 10)
        release = show_to_release(show, user_query="konosuba")
        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.kind, MediaKind.EPISODE)
        self.assertEqual(release.season, 2)
        self.assertEqual(release.episode_count, 10)

    def test_rank_shows_for_picker_prefers_query(self) -> None:
        from annie.allanime import rank_shows_for_picker

        shows = [
            _show("Kono Subarashii Sekai ni Bakuen wo!", 12),
            _show("Kono Subarashii Sekai ni Shukufuku wo!", 10),
            _show(
                "Kono Subarashii Sekai ni Shukufuku wo! Movie: Kurenai Densetsu",
                1,
            ),
        ]
        ranked = rank_shows_for_picker(
            shows, user_query="Kono Subarashii Sekai ni Shukufuku wo!"
        )
        self.assertGreaterEqual(len(ranked), 2)
        self.assertIn("Shukufuku", ranked[0].name)


class AllAnimeStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shows = [
            _show("Kono Subarashii Sekai ni Shukufuku wo! 3: Bonus Stage", 2),
            _show("Kono Subarashii Sekai ni Shukufuku wo! 3", 11),
            _show("Kono Subarashii Sekai ni Bakuen wo!", 12),
            _show("Kono Subarashii Sekai ni Shukufuku wo! 2", 10),
            _show("Kono Subarashii Sekai ni Shukufuku wo!", 10),
            _show(
                "Kono Subarashii Sekai ni Shukufuku wo! Movie: Kurenai Densetsu",
                1,
            ),
        ]

    def test_releases_have_seasons_and_movie(self) -> None:
        from annie.mal import MalAnime

        chosen = MalAnime(
            mal_id=21202,
            title="Kono Subarashii Sekai ni Shukufuku wo!",
            title_english="KONOSUBA -God's blessing on this wonderful world!",
            title_japanese=None,
            type="TV",
            episodes=10,
            aired_from="2016",
            synonyms=("Konosuba", "KonoSuba"),
        )
        kept = filter_franchise_shows(
            self.shows, chosen=chosen, user_query="konosuba"
        )
        releases = shows_to_releases(
            kept, user_query="konosuba", chosen=chosen
        )
        seasons = {
            r.season: r.episode_count
            for r in releases
            if r.kind == MediaKind.EPISODE
        }
        self.assertIn(1, seasons)
        self.assertEqual(seasons[1], 10)
        self.assertEqual(seasons.get(2), 10)
        self.assertEqual(seasons.get(3), 11)
        movies = [r for r in releases if r.kind == MediaKind.MOVIE]
        self.assertTrue(movies)
        self.assertIn("Kurenai", movies[0].label)
        # Spinoff Bakuen exclu
        self.assertFalse(
            any("Bakuen" in (r.label or "") for r in releases)
        )

    def test_stem_shared(self) -> None:
        a = _franchise_stem("Kono Subarashii Sekai ni Shukufuku wo! 2")
        b = _franchise_stem("Kono Subarashii Sekai ni Shukufuku wo!")
        self.assertGreaterEqual(len(set(a) & set(b)), 3)


if __name__ == "__main__":
    unittest.main()
