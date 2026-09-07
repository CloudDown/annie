"""Tests du bloc dashboard de lecture."""

from __future__ import annotations

import unittest
from io import StringIO
from unittest.mock import patch

from annie.ui import (
    _shorten_sub_detail,
    format_buffer_lines,
    print_playback_header,
)


class PlaybackDashboardTests(unittest.TestCase):
    def test_buffer_and_file_bars(self) -> None:
        text = format_buffer_lines(
            contiguous=10 * 1024 * 1024,
            ready=50 * 1024 * 1024,
            file_size=1000 * 1024 * 1024,
            target_bytes=80 * 1024 * 1024,
            peer_hint="14 peers",
            download_kib=1538,
            player="mpv",
            seed=True,
            filename="episode.mkv",
        )
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("buffer", lines[0])
        self.assertIn("file", lines[1])
        self.assertIn("mpv", lines[2])
        self.assertIn("seed", lines[2])
        self.assertNotIn("contig", text)

    def test_shorten_api_key_message(self) -> None:
        long = (
            "OpenSubtitles key missing — type settings · "
            "https://www.opensubtitles.com/en/consumers"
        )
        short = _shorten_sub_detail(long)
        self.assertIn("settings", short)
        self.assertIn("opensubtitles.com", short)
        self.assertNotIn("config.toml", short)

    def test_header_prints_title_rule_and_subs(self) -> None:
        buf = StringIO()
        with patch("sys.stdout", buf):
            print_playback_header(
                "Youjo Senki S02E08 [1080p]",
                sub_status=("warn", "subtitles", "OpenSubtitles API key missing"),
            )
        out = buf.getvalue()
        self.assertIn("◆ Youjo Senki S02E08 [1080p]", out)
        self.assertIn("─", out)
        self.assertIn("subs", out)


if __name__ == "__main__":
    unittest.main()
