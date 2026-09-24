"""Tests navigation fzf ←/→ (vues stables au retour)."""

from __future__ import annotations

import unittest
from unittest import mock

from annie.types import MediaKind, MediaSection
from annie.ui import _bucket_section, _group_sections, pick_section
from tests.helpers import result_item


def _section(
    *,
    key: str,
    label: str,
    kind: MediaKind,
    season: int | None = None,
    episodes: int = 0,
) -> MediaSection:
    section = MediaSection(
        key=key,
        label=label,
        kind=kind,
        season=season,
        expected_episodes=episodes or None,
        mal_id=abs(hash(key)) % 10000,
    )
    for ep in range(1, episodes + 1):
        section.episodes[ep] = result_item(
            f"[T] {label} - {ep:02d} [1080p]",
            score=50.0,
            season=season,
            episode=ep,
            kind=MediaKind.EPISODE,
        )
    if kind != MediaKind.EPISODE:
        section.singles.append(
            result_item(
                f"[T] {label} [1080p]",
                score=50.0,
                kind=kind,
                season=None,
                episode=None,
            )
        )
    return section


class FzfNavRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = [
            _section(
                key="s1",
                label="Season 01",
                kind=MediaKind.EPISODE,
                season=1,
                episodes=2,
            ),
            _section(
                key="s2",
                label="Season 02",
                kind=MediaKind.EPISODE,
                season=2,
                episodes=2,
            ),
            _section(key="m1", label="Movie 1", kind=MediaKind.MOVIE),
            _section(key="o1", label="OVA", kind=MediaKind.OVA),
        ]

    def test_buckets(self) -> None:
        groups = _group_sections(self.catalog)
        self.assertEqual(len(groups["season"]), 2)
        self.assertEqual(len(groups["movie"]), 1)
        self.assertEqual(len(groups["other"]), 1)
        self.assertEqual(_bucket_section(self.catalog[0]), "season")

    def test_resume_from_stays_in_same_group(self) -> None:
        """Retour ← depuis S01 : fzf sections = Seasons seulement, pas Movies."""
        season_01 = self.catalog[0]
        captured: dict[str, list] = {}

        def fake_flat(sections, *, back_label="search"):
            captured["sections"] = list(sections)
            captured["back_label"] = back_label
            return sections[0]

        with mock.patch("annie.ui._pick_section_flat", side_effect=fake_flat):
            with mock.patch("annie.ui.pick_group") as pick_group:
                picked = pick_section(
                    self.catalog,
                    force_interactive=True,
                    resume_from=season_01,
                )
        pick_group.assert_not_called()
        self.assertIs(picked, season_01)
        keys = [section.key for section in captured["sections"]]
        self.assertEqual(keys, ["s1", "s2"])
        self.assertEqual(captured["back_label"], "group")

    def test_left_from_sections_returns_to_group_picker(self) -> None:
        calls = {"flat": 0, "group": 0}

        def flat_then_back(sections, *, back_label="search"):
            calls["flat"] += 1
            if calls["flat"] == 1:
                return None  # ← utilisateur
            return sections[0]

        def pick_group(groups):
            calls["group"] += 1
            if calls["group"] == 1:
                return "season"
            if calls["group"] == 2:
                return "movie"
            return None

        with mock.patch("annie.ui._pick_section_flat", side_effect=flat_then_back):
            with mock.patch("annie.ui.pick_group", side_effect=pick_group):
                picked = pick_section(self.catalog)

        self.assertEqual(picked.key, "m1")
        self.assertEqual(calls["group"], 2)
        # Seasons: flat affiché puis ← ; Movies: 1 entrée → auto-pick sans flat.
        self.assertEqual(calls["flat"], 1)


if __name__ == "__main__":
    unittest.main()
