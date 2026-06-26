"""Tests sous-titres OpenSubtitles API (offline)."""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from annie.subtitles import (
    SubtitleCandidate,
    SubtitleQuery,
    SubtitlesError,
    _extract_subtitle,
    _pick_best,
    _subtitle_basename,
    build_query,
    download,
    language_for,
    parse_api_results,
    search_params,
    subtitle_title_variants,
)
from tests.helpers import FIXTURES_DIR, result_item


class SubtitleLanguageTests(unittest.TestCase):
    def test_language_for_known_codes(self) -> None:
        self.assertEqual(language_for("en").code, "en")
        self.assertEqual(language_for("fr").label, "Français")
        self.assertIsNone(language_for("ja"))


class BuildQueryTests(unittest.TestCase):
    def test_prefers_series_title(self) -> None:
        item = result_item(
            "[SubsPlease] Re Zero - 08 (1080p).mkv",
            season=1,
            episode=8,
        )
        query = build_query(item, series_title="re zero")
        self.assertEqual(query.title, "re zero")
        self.assertEqual(query.season, 1)
        self.assertEqual(query.episode, 8)
        self.assertEqual(query.kind, "tv")
        self.assertIn("Re Zero", query.extra_titles)


class SubtitleTitleVariantTests(unittest.TestCase):
    def test_re_zero_romanized_title(self) -> None:
        variants = subtitle_title_variants("Re Zero kara Hajimeru Isekai Seikatsu")
        self.assertIn("Re Zero kara Hajimeru Isekai Seikatsu", variants)
        self.assertIn("Re Zero", variants)
        self.assertIn("Re:Zero", variants)

    def test_user_query_re_zero(self) -> None:
        variants = subtitle_title_variants("re zero")
        self.assertIn("re zero", variants)
        self.assertIn("re:zero", variants)

    def test_mal_colon_title(self) -> None:
        variants = subtitle_title_variants("Re:Zero kara Hajimeru Isekai Seikatsu")
        self.assertIn("Re:Zero", variants)

    def test_frieren_no_tail(self) -> None:
        variants = subtitle_title_variants("Sousou no Frieren")
        self.assertIn("Frieren", variants)


class SubtitleFilenameTests(unittest.TestCase):
    def test_basename_includes_lang_season_episode(self) -> None:
        query = SubtitleQuery(title="Re Zero", season=1, episode=8)
        self.assertEqual(_subtitle_basename(query, "fr"), "re-zero-s01-e08-fr")

    def test_fetch_best_uses_lang_in_filename(self) -> None:
        query = SubtitleQuery(title="Re Zero", season=1, episode=8)
        fake_zip = io.BytesIO()
        with zipfile.ZipFile(fake_zip, "w") as archive:
            archive.writestr("ep.srt", "1\n00:00:00,000 --> 00:00:01,000\nOK\n")
        cache_dir = Path("/tmp/annie-sub-name-test")

        with (
            patch("annie.subtitles._require_api_key", return_value="test-key"),
            patch("annie.subtitles._auth_token", return_value=None),
            patch("annie.subtitles._read_cache", return_value=None),
            patch("annie.subtitles._write_cache"),
            patch(
                "annie.subtitles.search",
                return_value=[SubtitleCandidate(file_id=99, release="x", downloads=1)],
            ),
            patch(
                "annie.subtitles._fetch_download_link",
                return_value="https://example.com/sub.zip",
            ),
            patch("annie.subtitles.fetch_bytes", return_value=fake_zip.getvalue()),
            patch("annie.subtitles.CACHE_DIR", cache_dir),
        ):
            from annie.subtitles import fetch_best

            path = fetch_best(query, "fr")
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "re-zero-s01-e08-fr.srt")
        path.unlink(missing_ok=True)
        if cache_dir.is_dir():
            for leftover in cache_dir.glob("99.srt"):
                leftover.unlink(missing_ok=True)


class SearchFallbackTests(unittest.TestCase):
    def test_search_tries_title_variants(self) -> None:
        query = SubtitleQuery(
            title="Re Zero kara Hajimeru Isekai Seikatsu", season=1, episode=8
        )
        lang = language_for("fr")
        self.assertIsNotNone(lang)

        def fake_request(method, path, *, api_key, token=None, body=None, params=None):
            self.assertEqual(method, "GET")
            if params and params.get("query") == "Re:Zero":
                return json.loads(
                    (FIXTURES_DIR / "opensubtitles_api_search.json").read_text(
                        encoding="utf-8"
                    )
                )
            return {"data": []}

        with (
            patch("annie.subtitles._require_api_key", return_value="test-key"),
            patch("annie.subtitles._auth_token", return_value=None),
            patch("annie.subtitles._api_request", side_effect=fake_request),
        ):
            from annie.subtitles import search

            results = search(query, lang)
        self.assertEqual(len(results), 2)


class SearchParamsTests(unittest.TestCase):
    def test_tv_params(self) -> None:
        query = SubtitleQuery(title="Re Zero", season=1, episode=8)
        lang = language_for("fr")
        self.assertIsNotNone(lang)
        params = search_params(query, lang)
        self.assertEqual(params["query"], "Re Zero")
        self.assertEqual(params["languages"], "fr")
        self.assertEqual(params["season_number"], 1)
        self.assertEqual(params["episode_number"], 8)


class ParseApiResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(
            (FIXTURES_DIR / "opensubtitles_api_search.json").read_text(encoding="utf-8")
        )

    def test_extracts_candidates(self) -> None:
        candidates = parse_api_results(self.payload)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].file_id, 7654844)
        self.assertEqual(candidates[0].downloads, 1234)

    def test_pick_best_prefers_downloads(self) -> None:
        candidates = parse_api_results(self.payload)
        best = _pick_best(candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.file_id, 7654844)


class ExtractSubtitleTests(unittest.TestCase):
    def test_plain_srt(self) -> None:
        data = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        content, suffix = _extract_subtitle(data)
        self.assertEqual(suffix, ".srt")
        self.assertIn(b"Hello", content)

    def test_zip_srt(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("show.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        content, suffix = _extract_subtitle(buffer.getvalue())
        self.assertEqual(suffix, ".srt")
        self.assertIn(b"Hi", content)


class DownloadTests(unittest.TestCase):
    def test_download_writes_file(self) -> None:
        candidate = SubtitleCandidate(file_id=99, release="test", downloads=1)
        fake_zip = io.BytesIO()
        with zipfile.ZipFile(fake_zip, "w") as archive:
            archive.writestr("ep.srt", "1\n00:00:00,000 --> 00:00:01,000\nOK\n")

        with (
            patch("annie.subtitles._require_api_key", return_value="test-key"),
            patch("annie.subtitles._auth_token", return_value=None),
            patch(
                "annie.subtitles._fetch_download_link",
                return_value="https://example.com/sub.zip",
            ),
            patch("annie.subtitles.fetch_bytes", return_value=fake_zip.getvalue()),
        ):
            path = download(candidate, Path("/tmp/annie-sub-test"))
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".srt")
        path.unlink(missing_ok=True)

    def test_missing_api_key_raises(self) -> None:
        with patch("annie.subtitles._resolve_api_key", return_value=""):
            with self.assertRaises(SubtitlesError):
                download(
                    SubtitleCandidate(file_id=1, release="x"),
                    Path("/tmp"),
                )


if __name__ == "__main__":
    unittest.main()
