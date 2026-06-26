"""Tests sous-titres pilotés par fixtures JSON (offline + recherche mockée)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from annie.subtitles import (
    SubtitleQuery,
    build_query,
    language_for,
    probe_search,
    search,
    subtitle_title_variants,
)
from tests.helpers import FIXTURES_DIR, load_fixture, result_item


def _query_from_case(case: dict) -> SubtitleQuery:
    item = result_item(
        case["nyaa_title"],
        season=case.get("season"),
        episode=case.get("episode"),
    )
    return build_query(item, series_title=case.get("series_title"))


class SubtitleFixtureVariantTests(unittest.TestCase):
    def test_all_variant_expectations(self) -> None:
        for case in load_fixture("subtitle_queries.json"):
            with self.subTest(case=case["id"]):
                query = _query_from_case(case)
                variants = subtitle_title_variants(query.title, extra=query.extra_titles)
                lowered = {value.casefold() for value in variants}
                for expected in case.get("variants_contain", []):
                    self.assertIn(
                        expected.casefold(),
                        lowered,
                        msg=f"{case['id']}: variante manquante {expected!r} dans {variants}",
                    )


class SubtitleFixtureSearchTests(unittest.TestCase):
    def test_search_uses_first_matching_variant(self) -> None:
        case = next(
            item for item in load_fixture("subtitle_queries.json") if item["id"] == "re-zero-s01e08-subsplease"
        )
        query = _query_from_case(case)
        lang = language_for("fr")
        self.assertIsNotNone(lang)

        payload = json.loads(
            (FIXTURES_DIR / "opensubtitles_api_search.json").read_text(encoding="utf-8")
        )

        def fake_probe(subtitle_query, subtitle_lang, *, api_key=None):
            rows: list[tuple[str, list]] = []
            for title in subtitle_title_variants(
                subtitle_query.title, extra=subtitle_query.extra_titles
            ):
                if title.casefold() == "re:zero":
                    from annie.subtitles import parse_api_results

                    rows.append((title, parse_api_results(payload)))
                else:
                    rows.append((title, []))
            return rows

        with (
            patch("annie.subtitles._require_api_key", return_value="test-key"),
            patch("annie.subtitles.probe_search", side_effect=fake_probe),
        ):
            results = search(query, lang)
        self.assertEqual(len(results), 2)


class SubtitleFixtureLiveTests(unittest.TestCase):
    @unittest.skipUnless(
        __import__("os").environ.get("ANNIE_SUBTITLES_LIVE") == "1",
        "définir ANNIE_SUBTITLES_LIVE=1 pour les tests réseau OpenSubtitles",
    )
    def test_fixture_min_hits_live(self) -> None:
        for case in load_fixture("subtitle_queries.json"):
            query = _query_from_case(case)
            for lang_code, minimum in case.get("min_hits", {}).items():
                with self.subTest(case=case["id"], lang=lang_code):
                    lang = language_for(lang_code)
                    self.assertIsNotNone(lang)
                    hits = search(query, lang)
                    self.assertGreaterEqual(
                        len(hits),
                        minimum,
                        msg=f"{case['id']} [{lang_code}]: {len(hits)} < {minimum}",
                    )


if __name__ == "__main__":
    unittest.main()
