"""Tests matching fichiers torrent (batch SubsPlease, etc.)."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import libtorrent as lt  # noqa: F401

    HAS_LT = True
except ImportError:
    HAS_LT = False

from annie.buffer import (
    MKV_FRONTIER_PIECES,
    WIDE_FRONTIER_PIECES,
    _buffer_peer_state,
    _buffer_start_mode,
    _enforce_sequential_frontier,
    _frontier_window,
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
    _active_download_target,
    _load_dht_state,
    _save_dht_state,
    _session_settings,
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

    def test_yyq_bracket_episode_picks_s2e07(self) -> None:
        # Régression : batch YYQ « [Youjo Senki S2][07][…] » sans tiret avant l'épisode.
        files = [
            (
                i,
                f"[YYQSUB][Youjo Senki S2][{ep:02d}][TVRIP][1080P][AVC_AAC][JPSC].mp4",
                100,
            )
            for i, ep in enumerate(range(1, 13), start=0)
        ]
        picked = pick_file(files, None, None, episode=7, season=2)
        self.assertEqual(picked[0], 6)
        self.assertIn("[07]", picked[1])

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


class _PieceFiles:
    def __init__(self, size: int) -> None:
        self._size = size

    def file_offset(self, _index: int) -> int:
        return 0

    def file_size(self, _index: int) -> int:
        return self._size


class _PieceInfo:
    def __init__(self, pieces: int, piece_len: int = 1024) -> None:
        self._len = piece_len
        self._size = pieces * piece_len

    def files(self) -> _PieceFiles:
        return _PieceFiles(self._size)

    def piece_length(self) -> int:
        return self._len


class _PieceHandle:
    def __init__(self, pieces: int) -> None:
        self._info = _PieceInfo(pieces)
        self.priorities: dict[int, int] = {}

    def torrent_file(self) -> _PieceInfo:
        return self._info

    def have_piece(self, _piece: int) -> bool:
        return False

    def piece_priority(self, piece: int, priority: int) -> None:
        self.priorities[piece] = priority

    def set_piece_deadline(self, _piece: int, _deadline: int) -> None:
        return None


class FrontierWindowTests(unittest.TestCase):
    def test_narrow_until_the_download_is_fast(self) -> None:
        self.assertEqual(_frontier_window(), MKV_FRONTIER_PIECES)
        self.assertEqual(
            _frontier_window(download_rate=1024 * 1024 - 1), MKV_FRONTIER_PIECES
        )
        self.assertEqual(
            _frontier_window(download_rate=1024 * 1024), WIDE_FRONTIER_PIECES
        )

    def test_urgent_adds_a_small_extra_window(self) -> None:
        self.assertEqual(_frontier_window(urgent=True), MKV_FRONTIER_PIECES + 32)
        self.assertEqual(
            _frontier_window(urgent=True, download_rate=2 * 1024 * 1024),
            WIDE_FRONTIER_PIECES + 32,
        )

    def test_lead_past_the_margin_widens_the_window(self) -> None:
        margin = 64 * 1024 * 1024
        with mock.patch("annie.buffer._stream_margin_bytes", return_value=margin):
            self.assertEqual(
                _frontier_window(lead_bytes=margin - 1), MKV_FRONTIER_PIECES
            )
            self.assertEqual(_frontier_window(lead_bytes=margin), WIDE_FRONTIER_PIECES)

    def test_priorities_stop_at_the_window(self) -> None:
        narrow = _PieceHandle(400)
        _enforce_sequential_frontier(narrow, 0, download_rate=0)
        self.assertEqual(narrow.priorities[MKV_FRONTIER_PIECES - 1], 7)
        self.assertEqual(narrow.priorities[MKV_FRONTIER_PIECES], 0)
        self.assertEqual(narrow.priorities[399], 0)

        wide = _PieceHandle(400)
        _enforce_sequential_frontier(wide, 0, download_rate=2 * 1024 * 1024)
        self.assertEqual(wide.priorities[WIDE_FRONTIER_PIECES - 1], 7)
        self.assertEqual(wide.priorities[WIDE_FRONTIER_PIECES], 0)
        self.assertTrue(
            all(
                priority == 0
                for piece, priority in wide.priorities.items()
                if piece >= WIDE_FRONTIER_PIECES
            )
        )


class ActiveDownloadTests(unittest.TestCase):
    def test_prefetch_opens_a_second_slot(self) -> None:
        self.assertEqual(_active_download_target(1, 1), 1)
        self.assertEqual(_active_download_target(1, 2), 2)
        self.assertEqual(_active_download_target(4, 2), 4)
        self.assertEqual(_active_download_target(0, 1), 1)


class SessionSettingsTests(unittest.TestCase):
    def test_trackers_and_listen_port(self) -> None:
        from annie.config import AnnieConfig

        torrent = AnnieConfig.load().torrent
        settings = _session_settings(seed_while_watching=False)
        self.assertTrue(settings["announce_to_all_trackers"])
        self.assertTrue(settings["announce_to_all_tiers"])
        self.assertEqual(settings["connection_speed"], max(1, torrent.connection_speed))
        if torrent.listen_port > 0:
            self.assertIn(str(torrent.listen_port), settings["listen_interfaces"])
        else:
            self.assertNotIn("listen_interfaces", settings)


class DhtStateTests(unittest.TestCase):
    def test_roundtrip_and_empty_state_keeps_the_file(self) -> None:
        import libtorrent as lt

        class FakeSession:
            def __init__(self, data: dict | None = None) -> None:
                self.data = data if data is not None else {}
                self.loaded = None

            def save_state(self, _flags: int) -> dict:
                return self.data

            def load_state(self, data: dict) -> None:
                self.loaded = data

        payload = {b"dht": {b"n": b"abc"}}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with mock.patch("annie.stream.CACHE_DIR", cache):
                _save_dht_state(FakeSession({}))
                self.assertFalse((cache / "session" / "dht_state").exists())
                _save_dht_state(FakeSession(payload))
                path = cache / "session" / "dht_state"
                self.assertTrue(path.is_file())
                sentinel = path.read_bytes()
                _save_dht_state(FakeSession({}))
                self.assertEqual(path.read_bytes(), sentinel)
                reader = FakeSession()
                _load_dht_state(reader)
        self.assertEqual(reader.loaded, lt.bdecode(sentinel))


if __name__ == "__main__":
    unittest.main()
