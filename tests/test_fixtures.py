"""Tests parsing pilotés par fixtures JSON."""

from __future__ import annotations

import unittest

from annie.catalog import parse_batch_episode_range
from annie.parsing import parse_title
from annie.types import MediaKind
from tests.helpers import load_fixture


class FixtureParseTitleTests(unittest.TestCase):
    def test_all_parse_titles_fixture(self) -> None:
        for case in load_fixture("parse_titles.json"):
            with self.subTest(case=case["id"]):
                parsed = parse_title(case["title"])
                expect = case["expect"]
                if "season" in expect:
                    self.assertEqual(parsed.season, expect["season"])
                if "episode" in expect:
                    self.assertEqual(parsed.episode, expect["episode"])
                if "kind" in expect:
                    self.assertEqual(parsed.kind, MediaKind(expect["kind"]))
                if "batch_eps" in expect:
                    season, eps = parse_batch_episode_range(case["title"])
                    self.assertEqual(eps, expect["batch_eps"])
                    if expect["batch_eps"] and season is not None:
                        self.assertEqual(season, expect.get("season"))


if __name__ == "__main__":
    unittest.main()
