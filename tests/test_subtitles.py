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
    build_query,
    download,
    language_for,
    parse_api_results,
    search_params,
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
