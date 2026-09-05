"""Tests commandes slash du prompt."""

from __future__ import annotations

import unittest

from annie.tui import strip_ansi
from annie.ui import BANNER_HINT, parse_slash_command


class SlashCommandTests(unittest.TestCase):
    def test_slash_help(self) -> None:
        self.assertEqual(parse_slash_command("/help"), "help")
        self.assertEqual(parse_slash_command("/HELP"), "help")
        self.assertEqual(parse_slash_command("/?"), "help")

    def test_slash_settings(self) -> None:
        self.assertEqual(parse_slash_command("/settings"), "settings")
        self.assertEqual(parse_slash_command("/config"), "settings")

    def test_slash_quit(self) -> None:
        self.assertEqual(parse_slash_command("/quit"), "quit")
        self.assertEqual(parse_slash_command("/q"), "quit")

    def test_bare_compat(self) -> None:
        self.assertEqual(parse_slash_command("help"), "help")
        self.assertEqual(parse_slash_command("settings"), "settings")
        self.assertEqual(parse_slash_command("quit"), "quit")

    def test_search_query_not_command(self) -> None:
        self.assertIsNone(parse_slash_command("frieren"))
        self.assertIsNone(parse_slash_command("help me"))
        self.assertIsNone(parse_slash_command("quit smoking"))

    def test_unknown_slash(self) -> None:
        self.assertEqual(parse_slash_command("/nope"), "unknown")
        self.assertEqual(parse_slash_command("/"), "unknown")

    def test_banner_hint_one_line(self) -> None:
        plain = strip_ansi(BANNER_HINT)
        self.assertNotIn("\n", plain)
        self.assertIn("/help", plain)
        self.assertIn("/settings", plain)
        self.assertIn("/quit", plain)


if __name__ == "__main__":
    unittest.main()
