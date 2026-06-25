"""Tests matching fichiers torrent (batch SubsPlease, etc.)."""

from __future__ import annotations

import unittest

try:
    import libtorrent as lt  # noqa: F401

    HAS_LT = True
except ImportError:
    HAS_LT = False

from annie.parsing import match_episode_filename
from tests.helpers import load_fixture

if HAS_LT:
    from annie.stream import pick_file


@unittest.skipUnless(HAS_LT, "libtorrent requis pour pick_file")
class PickFileTests(unittest.TestCase):
    def test_picks_subsplease_batch_episode(self) -> None:
        files = [
            (0, "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 07 (1080p) [ABCD1234].mkv", 500_000_000),
            (1, "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 08 (1080p) [ABCD1234].mkv", 500_000_000),
            (2, "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 09 (1080p) [ABCD1234].mkv", 500_000_000),
        ]
        picked = pick_file(files, None, None, episode=8, season=1)
        self.assertEqual(picked[0], 1)

    def test_fails_clearly_on_missing_episode(self) -> None:
        files = [
            (0, "[SubsPlease] Re Zero - 07 (1080p) [ABCD1234].mkv", 100),
        ]
        with self.assertRaises(SystemExit):
            pick_file(files, None, None, episode=8, season=1)


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
