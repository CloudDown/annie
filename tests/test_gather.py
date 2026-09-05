"""Tests statut catalogue et orchestration gather."""

from __future__ import annotations

import unittest

from annie.gather import format_catalog_status
from annie.types import MediaKind, MediaSection


def _section(*, season: int | None, kind: MediaKind = MediaKind.EPISODE) -> MediaSection:
    return MediaSection(
        key=f"s{season or 'm'}",
        label=f"Season {season}" if season else "Movie",
        kind=kind,
        season=season,
    )


class CatalogStatusTests(unittest.TestCase):
    def test_franchise_with_seasons(self) -> None:
        catalog = [_section(season=1), _section(season=2)]
        line = format_catalog_status(
            catalog,
            {"catalog_source": "franchise", "picked_title": "Re:Zero"},
        )
        self.assertIn("AniList/MAL", line)
        self.assertIn("Re:Zero", line)
        self.assertIn("2 seasons", line)

    def test_scope_missed_names_available(self) -> None:
        line = format_catalog_status(
            [],
            {
                "catalog_source": "franchise",
                "scope_missed": True,
                "target_season": 9,
                "available_seasons": [1, 2, 3],
            },
        )
        self.assertIn("S9 missing", line)
        self.assertIn("S1", line)
        self.assertIn("S3", line)

    def test_nyaa_fallback(self) -> None:
        line = format_catalog_status(
            [_section(season=1)],
            {"catalog_source": "nyaa", "catalog_fallback": True},
        )
        self.assertIn("Nyaa", line)
        self.assertIn("Nyaa fallback", line)


if __name__ == "__main__":
    unittest.main()
