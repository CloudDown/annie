"""Tests historique de visionnage + détection fin de lecture."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from annie.stream import PLAY_COMPLETED, PLAY_INCOMPLETE, _normalize_playback_code
from annie.types import MediaKind, MediaSection
from annie.ui import format_torrent_line
from annie.watch_history import WatchHistory, watch_key
from tests.helpers import result_item


class WatchHistoryTests(unittest.TestCase):
    def test_mark_and_is_watched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.toml"
            with patch("annie.watch_history.HISTORY_FILE", history_path):
                history = WatchHistory()
                section = MediaSection(
                    key="s1",
                    label="Season 01",
                    kind=MediaKind.EPISODE,
                    season=1,
                    mal_id=42,
                )
                item = result_item("[G] Anime - 01 [1080p]")
                history.mark_item(section, item)
                self.assertTrue(
                    history.is_watched(
                        mal_id=42, section_key="s1", season=1, episode=1
                    )
                )
                reloaded = WatchHistory.load()
                self.assertTrue(
                    reloaded.is_watched(
                        mal_id=42, section_key="s1", season=1, episode=1
                    )
                )

    def test_mark_movie_without_episode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.toml"
            with patch("annie.watch_history.HISTORY_FILE", history_path):
                history = WatchHistory()
                section = MediaSection(
                    key="movie",
                    label="Legend of Crimson",
                    kind=MediaKind.MOVIE,
                    season=None,
                    mal_id=99,
                )
                item = result_item(
                    "[EMBER] KONOSUBA Legend of Crimson - Movie (2019)",
                    episode=None,
                    kind=MediaKind.MOVIE,
                )
                history.mark_item(section, item)
                self.assertTrue(
                    history.is_watched(
                        mal_id=99, section_key="movie", season=None, episode=None
                    )
                )
                self.assertEqual(
                    watch_key(
                        mal_id=99, section_key="movie", season=None, episode=None
                    ),
                    "mal:99:movie",
                )


class WatchedDotTests(unittest.TestCase):
    def test_dot_on_right_in_red(self) -> None:
        section = MediaSection(
            key="s1",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            mal_id=1,
        )
        item = result_item("[G] Anime - 03 [1080p]")
        history = WatchHistory(
            entries={watch_key(mal_id=1, section_key="s1", season=1, episode=3): "x"}
        )
        line = format_torrent_line(item, section=section, watch_history=history)
        self.assertTrue(line.rstrip().endswith("●") or "●" in line)
        # Point après le numéro, pas avant.
        plain = line.replace("\033[0m", "")
        self.assertRegex(plain, r"03.*●")

    def test_seeders_and_resolution_suffix(self) -> None:
        item = result_item("[G] Anime - 03 [1080p]")
        line = format_torrent_line(item)
        plain = line.replace("\033[0m", "")
        self.assertIn("50S", plain)
        self.assertIn("1080p", plain)


class PlaybackCompletionTests(unittest.TestCase):
    def test_saw_near_end_counts_as_completed(self) -> None:
        code = _normalize_playback_code(
            0, ipc_path=None, ipc_was_ready=True, saw_near_end=True
        )
        self.assertEqual(code, PLAY_COMPLETED)

    def test_early_quit_incomplete_without_near_end(self) -> None:
        code = _normalize_playback_code(
            0,
            ipc_path=Path("/tmp/annie-missing.sock"),
            ipc_was_ready=True,
            saw_near_end=False,
        )
        self.assertEqual(code, PLAY_INCOMPLETE)


class SameMagnetBingeTests(unittest.TestCase):
    def test_same_magnet_chain_stops_on_different_magnet(self) -> None:
        from dataclasses import replace

        from annie.cli import _binge_chain, _same_magnet_binge_chain
        from annie.types import MediaKind, MediaSection
        from tests.helpers import nyaa_entry, result_item

        magnet_a = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        magnet_b = "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        ep1 = replace(
            result_item("[G] Anime - 01 [1080p]"),
            entry=nyaa_entry("[G] Anime - 01 [1080p]", magnet=magnet_a),
        )
        ep2 = replace(
            result_item("[G] Anime - 02 [1080p]"),
            entry=nyaa_entry("[G] Anime - 02 [1080p]", magnet=magnet_a),
        )
        ep3 = replace(
            result_item("[G] Anime - 03 [1080p]"),
            entry=nyaa_entry("[G] Anime - 03 [1080p]", magnet=magnet_b),
        )
        section = MediaSection(
            key="s1",
            label="Season 01",
            kind=MediaKind.EPISODE,
            season=1,
            mal_id=1,
            episodes={1: ep1, 2: ep2, 3: ep3},
        )
        chain = _same_magnet_binge_chain(section, ep1)
        self.assertEqual([it.parsed.episode for it in chain], [2])
        # Chaîne binge complète : enchaîne aussi l'épisode sur un autre magnet.
        self.assertEqual(
            [it.parsed.episode for it in _binge_chain(section, ep1)], [2, 3]
        )

    def test_mpv_keep_open_flag(self) -> None:
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from annie.player import player_command

        mpv = MagicMock()
        mpv.gpu_api = "vulkan"
        mpv.hwdec = "auto"
        mpv.vo = "gpu"
        mpv.force_window = False
        mpv.really_quiet = True
        mpv.cache_secs = 120
        mpv.extra_args = []
        settings = MagicMock()
        settings.player.mpv = mpv
        with patch("annie.player.find_program", return_value="mpv"), patch(
            "annie.player._settings", return_value=settings
        ):
            cmd_yes = player_command(
                "mpv", Path("/tmp/a.mkv"), keep_open=True
            )
            cmd_no = player_command(
                "mpv", Path("/tmp/a.mkv"), keep_open=False
            )
        self.assertIn("--keep-open=yes", cmd_yes)
        self.assertIn("--keep-open=no", cmd_no)


if __name__ == "__main__":
    unittest.main()
