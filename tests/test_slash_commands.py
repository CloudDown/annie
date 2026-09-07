"""Tests commandes prompt + barre raccourcis Omarchy."""

from __future__ import annotations

import unittest

from annie.tui import strip_ansi
from annie.ui import BANNER_HINT, HELP, keychip, parse_prompt_command, shortcut_line


class PromptCommandTests(unittest.TestCase):
    def test_commands(self) -> None:
        self.assertEqual(parse_prompt_command("help"), "help")
        self.assertEqual(parse_prompt_command("settings"), "settings")
        self.assertEqual(parse_prompt_command("quit"), "quit")
        self.assertEqual(parse_prompt_command("q"), "quit")

    def test_optional_slash_still_works(self) -> None:
        self.assertEqual(parse_prompt_command("/help"), "help")

    def test_search_not_command(self) -> None:
        self.assertIsNone(parse_prompt_command("frieren"))
        self.assertIsNone(parse_prompt_command("help me"))

    def test_banner_one_line_chips(self) -> None:
        plain = strip_ansi(BANNER_HINT)
        self.assertNotIn("\n", plain)
        self.assertIn("help", plain)
        self.assertIn("settings", plain)
        self.assertNotIn("quit", plain)
        self.assertNotIn("TUI help", plain)
        self.assertNotIn("/help", plain)
        self.assertIn("\033[7m", BANNER_HINT)

    def test_help_one_line_chips(self) -> None:
        plain = strip_ansi(HELP)
        self.assertNotIn("\n", plain)
        self.assertIn("enter", plain)
        self.assertIn("\033[7m", HELP)
        self.assertEqual(HELP, BANNER_HINT)

    def test_keychip(self) -> None:
        chip = keychip("Esc")
        self.assertIn("\033[7m", chip)
        self.assertEqual(strip_ansi(chip).strip(), "Esc")

    def test_shortcut_line(self) -> None:
        line = shortcut_line([("a", "toggle"), ("i", "info")])
        plain = strip_ansi(line)
        self.assertIn("a", plain)
        self.assertIn("toggle", plain)
        self.assertIn("i", plain)


if __name__ == "__main__":
    unittest.main()
