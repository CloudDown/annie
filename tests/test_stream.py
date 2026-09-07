"""Tests matching fichiers torrent (batch SubsPlease, etc.)."""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace

try:
    import libtorrent as lt  # noqa: F401

    HAS_LT = True
except ImportError:
    HAS_LT = False

from annie.buffer import (
    _buffer_peer_state,
    _buffer_start_mode,
    _peer_wait_deadlines,
)
from annie.config import BufferConfig
from annie.parsing import match_episode_filename
from tests.helpers import load_fixture

if HAS_LT:
    from annie.stream import pick_file

from annie.stream import (
    BINGE_PREFETCH_PROGRESS,
    BINGE_SWITCH_PROGRESS,
)


class BufferPeerStateTests(unittest.TestCase):
    def test_active_when_downloading(self) -> None:
        status = SimpleNamespace(
            download_rate=1024,
            upload_rate=0,
            num_peers=2,
            num_seeds=0,
            state=SimpleNamespace(name="downloading"),
        )
        active, hint = _buffer_peer_state(status)
        self.assertTrue(active)
        self.assertIn("2 peers", hint)

    def test_metadata_state(self) -> None:
        status = SimpleNamespace(
            download_rate=0,
            upload_rate=0,
            num_peers=0,
            num_seeds=0,
            state=SimpleNamespace(name="downloading_metadata"),
        )
        active, hint = _buffer_peer_state(status)
        self.assertFalse(active)
        self.assertIn("metadata", hint)

    def test_listed_seeders_hint(self) -> None:
        status = SimpleNamespace(
            download_rate=0,
            upload_rate=0,
            num_peers=0,
            num_seeds=0,
            state=SimpleNamespace(name="downloading"),
        )
        _active, hint = _buffer_peer_state(status, listed_seeders=42)
        self.assertIn("42S Nyaa", hint)

    def test_swarm_seeds_hint(self) -> None:
        status = SimpleNamespace(
            download_rate=0,
            upload_rate=0,
            num_peers=0,
            num_seeds=5,
            state=SimpleNamespace(name="downloading"),
        )
        _active, hint = _buffer_peer_state(status)
        self.assertIn("5 seeds", hint)


class PeerWaitDeadlineTests(unittest.TestCase):
    def test_bonus_with_listed_seeders(self) -> None:
        buf = BufferConfig(no_peers_sec=45.0, absolute_sec=90.0)
        no_peers, absolute = _peer_wait_deadlines(
            buf, 100.0, listed_seeders=20
        )
        self.assertGreater(no_peers, 145.0)
        self.assertGreater(absolute, 190.0)

    def test_no_bonus_without_listed_seeders(self) -> None:
        buf = BufferConfig(no_peers_sec=45.0, absolute_sec=90.0)
        no_peers, absolute = _peer_wait_deadlines(buf, 100.0)
        self.assertEqual(no_peers, 145.0)
        self.assertEqual(absolute, 190.0)


class BufferStartModeTests(unittest.TestCase):
    def test_ready_beats_quick(self) -> None:
        self.assertEqual(
            _buffer_start_mode(
                startable=True,
                can_start=True,
                contiguous=80 * 1024 * 1024,
                target_bytes=80 * 1024 * 1024,
                soft_timeout=True,
                hard_timeout=False,
                seeding=False,
            ),
            "ready",
        )

    def test_quick_after_soft_timeout_without_full_buffer(self) -> None:
        self.assertEqual(
            _buffer_start_mode(
                startable=True,
                can_start=True,
                contiguous=20 * 1024 * 1024,
                target_bytes=80 * 1024 * 1024,
                soft_timeout=True,
                hard_timeout=False,
                seeding=False,
            ),
            "quick",
        )

    def test_keeps_waiting_before_timeout(self) -> None:
        self.assertIsNone(
            _buffer_start_mode(
                startable=True,
                can_start=True,
                contiguous=20 * 1024 * 1024,
                target_bytes=80 * 1024 * 1024,
                soft_timeout=False,
                hard_timeout=False,
                seeding=False,
            )
        )

    def test_forced_on_hard_timeout(self) -> None:
        self.assertEqual(
            _buffer_start_mode(
                startable=True,
                can_start=True,
                contiguous=10 * 1024 * 1024,
                target_bytes=80 * 1024 * 1024,
                soft_timeout=True,
                hard_timeout=True,
                seeding=False,
            ),
            "quick",
        )
        self.assertEqual(
            _buffer_start_mode(
                startable=False,
                can_start=True,
                contiguous=10 * 1024 * 1024,
                target_bytes=80 * 1024 * 1024,
                soft_timeout=True,
                hard_timeout=True,
                seeding=False,
            ),
            "timeout",
        )


class BufferDefaultTests(unittest.TestCase):
    def test_buffer_defaults_are_conservative(self) -> None:
        buf = BufferConfig()
        self.assertGreaterEqual(buf.mkv_start_mib, 80)
        self.assertGreaterEqual(buf.mkv_head_mib, buf.mkv_start_mib)
        self.assertGreaterEqual(buf.stream_margin_mib, 64)
        self.assertGreaterEqual(buf.max_wait_sec, 25.0)


class BingePrefetchTests(unittest.TestCase):
    def test_prefetch_starts_early_for_smooth_handoff(self) -> None:
        self.assertEqual(BINGE_PREFETCH_PROGRESS, 0.30)
        self.assertGreater(BINGE_SWITCH_PROGRESS, BINGE_PREFETCH_PROGRESS)


@unittest.skipUnless(HAS_LT, "libtorrent requis pour pick_file")
class PickFileTests(unittest.TestCase):
    def test_picks_subsplease_batch_episode(self) -> None:
        files = [
            (
                0,
                "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 07 (1080p) [ABCD1234].mkv",
                500_000_000,
            ),
            (
                1,
                "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 08 (1080p) [ABCD1234].mkv",
                500_000_000,
            ),
            (
                2,
                "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 09 (1080p) [ABCD1234].mkv",
                500_000_000,
            ),
        ]
        picked = pick_file(files, None, None, episode=8, season=1)
        self.assertEqual(picked[0], 1)

    def test_fails_clearly_on_missing_episode(self) -> None:
        files = [
            (0, "[SubsPlease] Re Zero - 07 (1080p) [ABCD1234].mkv", 100),
            (1, "[SubsPlease] Re Zero - 09 (1080p) [ABCD1234].mkv", 100),
        ]
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
            pick_file(files, None, None, episode=8, season=1)
        self.assertIn("no file matches episode 8", stderr.getvalue())

    def test_single_file_fallback_when_numbering_differs(self) -> None:
        # Épisode affiché S4E01 mais fichier en numérotation absolue « - 67 » :
        # le torrent ne contient qu'un fichier, on le lit.
        files = [
            (
                0,
                "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 67 (1080p) [ABCD1234].mkv",
                100,
            ),
        ]
        picked = pick_file(files, None, None, episode=1, season=4)
        self.assertEqual(picked[0], 0)

    def test_source_episode_matches_absolute_numbering_in_batch(self) -> None:
        # Batch en numérotation absolue : S4E02 affiché → fichier « - 68 ».
        files = [
            (
                0,
                "[Batch] Re Zero kara Hajimeru Isekai Seikatsu - 67 (1080p).mkv",
                100,
            ),
            (
                1,
                "[Batch] Re Zero kara Hajimeru Isekai Seikatsu - 68 (1080p).mkv",
                100,
            ),
        ]
        picked = pick_file(
            files, None, None, episode=2, season=4, source_episode=68
        )
        self.assertEqual(picked[0], 1)

    def test_tanya_end_suffix_picks_episode_12_not_3(self) -> None:
        # Régression : batch Erai « - 12 END » + source_episode contaminé → E03.
        files = [
            (
                i,
                f"[Erai-raws] Youjo Senki - 01 ~ 12 [1080p]/"
                f"[Erai-raws] Youjo Senki - {ep}{' END' if ep == 12 else ''} "
                f"[1080p][Multiple Subtitle].mkv",
                100,
            )
            for i, ep in enumerate(
                [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], start=0
            )
        ]
        picked = pick_file(
            files, None, None, episode=12, season=1, source_episode=None
        )
        self.assertEqual(picked[0], 11)
        self.assertIn("12 END", picked[1])
        # Même avec un source_episode erroné, E12 doit gagner s'il matche.
        picked_safe = pick_file(
            files, None, None, episode=12, season=1, source_episode=3
        )
        self.assertEqual(picked_safe[0], 11)

    def test_disambiguates_franchise_batch_by_series(self) -> None:
        files = [
            (
                10,
                "[Sokudo] DanMachi Sword Oratoria - S01E02 [1080p BD][AV1][dual audio].mkv",
                100,
            ),
            (
                85,
                "[Sokudo] Dungeon ni Deai wo Motomeru no wa Machigatteiru Darou ka - S01E02 [1080p BD][AV1][dual audio].mkv",
                100,
            ),
        ]
        main_queries = [
            "Dungeon ni Deai wo Motomeru no wa Machigatteiru Darou ka",
            "danmachi",
            "Is It Wrong to Try to Pick Up Girls in a Dungeon",
        ]
        picked = pick_file(
            files, None, None, episode=2, season=1, match_queries=main_queries
        )
        self.assertEqual(picked[0], 85)

        ora_queries = ["DanMachi Sword Oratoria", "Sword Oratoria"]
        picked_ora = pick_file(
            files, None, None, episode=2, season=1, match_queries=ora_queries
        )
        self.assertEqual(picked_ora[0], 10)

    def test_ambiguous_franchise_batch_without_queries_still_fails(self) -> None:
        files = [
            (
                10,
                "[Sokudo] DanMachi Sword Oratoria - S01E02 [1080p BD][AV1].mkv",
                100,
            ),
            (
                85,
                "[Sokudo] Dungeon ni Deai wo Motomeru no wa Machigatteiru Darou ka - S01E02 [1080p BD][AV1].mkv",
                100,
            ),
        ]
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
            pick_file(files, None, None, episode=2, season=1)
        self.assertIn("multiple files match", stderr.getvalue())


class FixtureFilenameTests(unittest.TestCase):
    def test_fixture_cases(self) -> None:
        for case in load_fixture("match_filenames.json"):
            with self.subTest(case=case["id"]):
                result = match_episode_filename(
                    case["path"],
                    case["episode"],
                    season=case.get("season"),
                )
                self.assertEqual(result, case["match"], msg=case["path"])


if __name__ == "__main__":
    unittest.main()
