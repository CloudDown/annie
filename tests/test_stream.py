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

from annie.parsing import match_episode_filename
from tests.helpers import load_fixture

if HAS_LT:
    from annie.stream import pick_file

from annie.stream import _buffer_peer_state, _peer_wait_deadlines
from annie.settings import BufferSettings


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
        self.assertIn("métadonnées", hint)

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
        buf = BufferSettings(no_peers_sec=45.0, absolute_sec=90.0)
        no_peers, absolute = _peer_wait_deadlines(
            buf, 100.0, listed_seeders=20
        )
        self.assertGreater(no_peers, 145.0)
        self.assertGreater(absolute, 190.0)

    def test_no_bonus_without_listed_seeders(self) -> None:
        buf = BufferSettings(no_peers_sec=45.0, absolute_sec=90.0)
        no_peers, absolute = _peer_wait_deadlines(buf, 100.0)
        self.assertEqual(no_peers, 145.0)
        self.assertEqual(absolute, 190.0)


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
        ]
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
            pick_file(files, None, None, episode=8, season=1)
        self.assertIn("no file matches episode 8", stderr.getvalue())


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
