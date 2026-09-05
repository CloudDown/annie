"""Tests TUI : fuzzy, rendu, parsing expect — sans TTY."""

from __future__ import annotations

import unittest

from annie.tui import (
    chrome,
    clip_visible,
    cycle_choice,
    filter_rows,
    fuzzy_score,
    layout,
    mask_secret,
    parse_expect,
    screen_title,
    select_row,
    strip_ansi,
)


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

    def test_strip_truecolor(self) -> None:
        self.assertEqual(strip_ansi("\033[38;2;122;162;247mhi\033[0m"), "hi")

    def test_clip_keeps_short(self) -> None:
        self.assertEqual(clip_visible("abc", 10), "abc")

    def test_parse_expect(self) -> None:
        self.assertEqual(parse_expect("left,enter"), {"left", "enter"})

    def test_mask_secret(self) -> None:
        self.assertEqual(mask_secret(""), "—")
        self.assertEqual(mask_secret("abcd1234"), "••••1234")

    def test_cycle_choice(self) -> None:
        self.assertEqual(cycle_choice("720p", ("auto", "720p", "1080p")), "1080p")
        self.assertEqual(cycle_choice("1080p", ("auto", "720p", "1080p")), "auto")


class ChromeTests(unittest.TestCase):
    def test_no_box_drawing(self) -> None:
        frame = chrome(
            title="saison",
            body=["Season 01", "Season 02"],
            footer="/  ↑↓  enter",
            preview=["Season 02", "episode · 12 ep"],
            cols=80,
            rows=24,
            meta="2/2",
        )
        plain = strip_ansi(frame)
        self.assertIn("annie", plain)
        self.assertIn("saison", plain)
        self.assertNotIn("╭", plain)
        self.assertNotIn("│", plain)
        self.assertNotIn("╰", plain)

    def test_fits_rows(self) -> None:
        frame = chrome(
            title="saison",
            body=["a"] * 40,
            footer="x",
            preview=["p"] * 12,
            cols=80,
            rows=16,
        )
        self.assertLessEqual(len(frame.splitlines()), 16)

    def test_layout_counts(self) -> None:
        body_h, preview_h, spacer = layout(24, 8)
        self.assertGreaterEqual(body_h, 3)
        self.assertEqual(4 + body_h + spacer + (1 if preview_h else 0) + preview_h, 24)

    def test_screen_title_strips_brand(self) -> None:
        self.assertEqual(screen_title("annie  ·  réglages"), "réglages")
        self.assertEqual(screen_title("saison> "), "saison")

    def test_select_row_marker(self) -> None:
        selected = select_row("Season 02", 40, selected=True)
        idle = select_row("Season 01", 40, selected=False)
        self.assertTrue(strip_ansi(selected).startswith("▏"))
        self.assertTrue(strip_ansi(idle).startswith("  "))
        self.assertEqual(len(strip_ansi(selected)), 40)
        self.assertEqual(len(strip_ansi(idle)), 40)


if __name__ == "__main__":
    unittest.main()
