"""Recherche Nyaa : une requête en vol, page 2 seulement si la page 1 est maigre."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from annie.nyaa import NyaaEntry, _season_page_ready, search


def _html(rows: list[tuple[str, int, str]]) -> str:
    parts: list[str] = []
    for title, seeders, magnet in rows:
        parts.append(
            "<tr class=\"success\">"
            f'<a href="/view/1" title="{title}">{title}</a>'
            f'<a href="{magnet}">magnet</a>'
            '<td class="text-center">1.2 GiB</td>'
            '<td class="text-center" data-timestamp="1">2024-01-01</td>'
            f'<td class="text-center">{seeders}</td>'
            '<td class="text-center">1</td>'
            '<td class="text-center">9</td>'
            "</tr>"
        )
    return "<table>" + "".join(parts) + "</table>"


def _magnet(n: int) -> str:
    return "magnet:?xt=urn:btih:" + f"{n:040x}"


def _entry(title: str, seeders: int, n: int) -> NyaaEntry:
    return NyaaEntry(
        title=title,
        magnet=_magnet(n),
        size="1 GiB",
        date="today",
        seeders=seeders,
        leechers=1,
        downloads=1,
        trusted=True,
    )


class SeasonPageReadyTests(unittest.TestCase):
    def test_season_batch_with_enough_seeders(self) -> None:
        entries = [
            _entry("[SubsPlease] Frieren - S01 (01-28) [1080p].mkv", 40, 1)
        ]
        self.assertTrue(_season_page_ready(entries, min_seeders=3, min_coverage=0.85))

    def test_short_or_unseeded_batch_is_not_enough(self) -> None:
        short = [_entry("[Group] Show (01-03) [1080p]", 40, 1)]
        quiet = [_entry("[Group] Show (01-12) [1080p]", 1, 2)]
        self.assertFalse(_season_page_ready(short, min_seeders=3, min_coverage=0.85))
        self.assertFalse(_season_page_ready(quiet, min_seeders=3, min_coverage=0.85))

    def test_singles_cover_a_season(self) -> None:
        full = [
            _entry(f"[Group] Show - {episode:02d} [1080p]", 10, episode)
            for episode in range(1, 13)
        ]
        sparse = [
            _entry("[Group] Show - 01 [1080p]", 10, 1),
            _entry("[Group] Show - 12 [1080p]", 10, 2),
        ]
        self.assertTrue(_season_page_ready(full, min_seeders=3, min_coverage=0.85))
        self.assertFalse(_season_page_ready(sparse, min_seeders=3, min_coverage=0.85))


class SearchFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cache = mock.patch(
            "annie.nyaa.DISK_CACHE_DIR", Path(self._tmp.name)
        )
        self._cache.start()
        self._limits = mock.patch(
            "annie.nyaa._catalog_search_limits", return_value=(3, 0.85)
        )
        self._limits.start()
        from annie import nyaa

        nyaa._search_cache.clear()
        nyaa._inflight.clear()

    def tearDown(self) -> None:
        from annie import nyaa

        nyaa._search_cache.clear()
        nyaa._inflight.clear()
        self._limits.stop()
        self._cache.stop()
        self._tmp.cleanup()

    def _patch_fetch(self, pages: dict[int, str], calls: list[str]):
        def fake(url: str, user_agent: str = "", timeout: float = 30) -> str:
            calls.append(url)
            if "p=2" in url:
                return pages.get(2, "")
            return pages.get(1, "")

        return mock.patch("annie.nyaa.fetch_text", side_effect=fake)

    def test_page_two_skipped_when_page_one_has_a_season_batch(self) -> None:
        calls: list[str] = []
        pages = {
            1: _html(
                [("[SubsPlease] Frieren - S01 (01-28) [1080p].mkv", 40, _magnet(1))]
            ),
            2: _html([("[Other] Frieren - 02 [1080p]", 2, _magnet(2))]),
        }
        with self._patch_fetch(pages, calls):
            found = search("Frieren", pages=2, exhaustive=False, retries=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(found), 1)
        self.assertIn("01-28", found[0].title)

    def test_page_two_kept_when_page_one_is_thin(self) -> None:
        calls: list[str] = []
        pages = {
            1: _html([("[Group] Show - 01 [1080p]", 4, _magnet(1))]),
            2: _html([("[Group] Show - 02 [1080p]", 3, _magnet(2))]),
        }
        with self._patch_fetch(pages, calls):
            found = search("Show", pages=2, exhaustive=False, retries=1)
        self.assertEqual(len(calls), 2)
        self.assertEqual({item.title for item in found}, {
            "[Group] Show - 01 [1080p]",
            "[Group] Show - 02 [1080p]",
        })

    def test_explicit_page_count_is_not_cut_short(self) -> None:
        calls: list[str] = []
        pages = {
            1: _html(
                [("[SubsPlease] Frieren - S01 (01-28) [1080p].mkv", 40, _magnet(1))]
            ),
            2: _html([("[Other] Frieren - 03 [1080p]", 5, _magnet(2))]),
        }
        with self._patch_fetch(pages, calls):
            found = search("Frieren", pages=2, retries=1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(found), 2)

    def test_concurrent_searches_share_one_fetch(self) -> None:
        calls: list[str] = []
        started = threading.Event()
        release = threading.Event()
        page = _html([("[Group] Show - 01 [1080p]", 4, _magnet(1))])

        def fake(url: str, user_agent: str = "", timeout: float = 30) -> str:
            calls.append(url)
            started.set()
            self.assertTrue(release.wait(2))
            return page

        results: list[list[NyaaEntry]] = []

        def run() -> None:
            results.append(search("Show", pages=1, retries=1))

        with mock.patch("annie.nyaa.fetch_text", side_effect=fake):
            first = threading.Thread(target=run)
            second = threading.Thread(target=run)
            first.start()
            self.assertTrue(started.wait(2))
            second.start()
            release.set()
            first.join(2)
            second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].title, results[1][0].title)


if __name__ == "__main__":
    unittest.main()
