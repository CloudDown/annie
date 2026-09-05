"""Tests TUI : fuzzy, rendu, parsing expect — sans TTY."""

from __future__ import annotations

import unittest

from annie.tui import clip_visible, filter_rows, fuzzy_score, parse_expect, strip_ansi


class FuzzyScoreTests(unittest.TestCase):
    def test_empty_query_matches_all(self) -> None:
        self.assertEqual(fuzzy_score("", "Season 02"), 0)

    def test_subsequence(self) -> None:
        self.assertIsNotNone(fuzzy_score("s2", "Season 02"))
        self.assertIsNone(fuzzy_score("zzz", "Season 02"))

    def test_consecutive_beats_scattered(self) -> None:
        tight = fuzzy_score("re", "rezero")
        loose = fuzzy_score("re", "rxxxxx e")
        self.assertIsNotNone(tight)
        self.assertIsNotNone(loose)
        self.assertGreater(tight or 0, loose or 0)

    def test_case_insensitive(self) -> None:
        self.assertIsNotNone(fuzzy_score("Frieren", "frieren s2"))


class FilterRowsTests(unittest.TestCase):
    def test_keeps_order_without_query(self) -> None:
        rows = [
            ("a", "Season 01", "", 1),
            ("b", "Season 02", "", 2),
            ("c", "Movie", "", 3),
        ]
        out = filter_rows(rows, "")
        self.assertEqual([row[1][3] for row in out], [1, 2, 3])

    def test_filters_and_ranks(self) -> None:
        rows = [
            ("a", "Season 01", "", 1),
            ("b", "Season 02", "", 2),
            ("c", "Movie", "", 3),
        ]
        out = filter_rows(rows, "s02")
        values = [row[1][3] for row in out]
        self.assertEqual(values, [2])


class RenderHelpersTests(unittest.TestCase):
    def test_strip_ansi(self) -> None:
        self.assertEqual(strip_ansi("\033[1;32mhi\033[0m"), "hi")

    def test_clip_keeps_short(self) -> None:
        self.assertEqual(clip_visible("abc", 10), "abc")

    def test_parse_expect(self) -> None:
        self.assertEqual(parse_expect("left,enter"), {"left", "enter"})


if __name__ == "__main__":
    unittest.main()
