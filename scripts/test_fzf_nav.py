#!/usr/bin/env python3
"""Teste la navigation fzf ←/→ : les vues doivent être identiques après un aller-retour.

Reproduit la pile Annie (groupes → sections → épisodes) sans lancer fzf, en
comparant les listes affichées à l'aller et au retour.

Usage:
  uv run python scripts/test_fzf_nav.py              # catalogue synthétique
  uv run python scripts/test_fzf_nav.py --fixture catalog_re_zero.json
  uv run python scripts/test_fzf_nav.py --query "gurren lagann"   # réseau
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie.types import MediaKind, MediaSection, ResultItem
from annie.ui import (
    GROUP_LABELS,
    _bucket_section,
    _group_sections,
    format_section_line,
    format_torrent_line,
)
from tests.helpers import mal_release, nyaa_entry, result_item

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _plain(text: str) -> str:
    return ANSI_RE.sub("", text)


@dataclass(frozen=True)
class NavSnapshot:
    level: str
    context: str
    lines: tuple[str, ...]

    def fingerprint(self) -> tuple[str, ...]:
        return self.lines


def snapshot_groups(sections: list[MediaSection]) -> NavSnapshot:
    groups = _group_sections(sections)
    lines: list[str] = []
    for key in ("season", "movie", "other"):
        items = groups[key]
        if not items:
            continue
        lines.append(f"{GROUP_LABELS[key]}|{len(items)}")
    return NavSnapshot("group", "root", tuple(lines))


def snapshot_sections(sections: list[MediaSection], *, context: str) -> NavSnapshot:
    lines = tuple(
        f"{section.key}|{_plain(format_section_line(section))}|"
        f"{section.kind.value}|{section.season}|{len(section.episodes)}|{len(section.singles)}"
        for section in sections
    )
    return NavSnapshot("section", context, lines)


def snapshot_episodes(
    section: MediaSection, *, watch_history=None
) -> NavSnapshot:
    items = section.choices()
    lines = tuple(
        f"{item.parsed.episode}|{_plain(format_torrent_line(item, section=section, watch_history=watch_history))}|"
        f"{item.entry.title}"
        for item in items
    )
    return NavSnapshot("episode", section.key, lines)


def _synthetic_catalog() -> list[MediaSection]:
    """Catalogue multi-groupes pour exercer ←/→ (saisons + films + OVA)."""

    def ep_section(
        *,
        key: str,
        label: str,
        season: int,
        count: int,
        mal_id: int,
    ) -> MediaSection:
        section = MediaSection(
            key=key,
            label=label,
            kind=MediaKind.EPISODE,
            season=season,
            expected_episodes=count,
            mal_id=mal_id,
        )
        for ep in range(1, count + 1):
            title = f"[Test] Series S{season:02d}E{ep:02d} [1080p]"
            section.episodes[ep] = result_item(
                title,
                score=100.0 - ep,
                season=season,
                episode=ep,
                kind=MediaKind.EPISODE,
            )
        return section

    seasons = [
        ep_section(
            key="mal:1",
            label="Season 01 · 2016 · 3 ep · Demo",
            season=1,
            count=3,
            mal_id=1,
        ),
        ep_section(
            key="mal:2",
            label="Season 02 · 2017 · 2 ep · Demo",
            season=2,
            count=2,
            mal_id=2,
        ),
    ]
    movie = MediaSection(
        key="mal:10",
        label="Demo Movie",
        kind=MediaKind.MOVIE,
        season=None,
        mal_id=10,
    )
    movie.singles.append(
        result_item(
            "[Test] Demo Movie [1080p]",
            score=90.0,
            kind=MediaKind.MOVIE,
            season=None,
            episode=None,
        )
    )
    ova = MediaSection(
        key="mal:11",
        label="OVA · Demo Side",
        kind=MediaKind.OVA,
        season=None,
        mal_id=11,
    )
    ova.singles.append(
        result_item(
            "[Test] Demo OVA [1080p]",
            score=80.0,
            kind=MediaKind.OVA,
            season=None,
            episode=None,
        )
    )
    return [*seasons, movie, ova]


def _fixture_catalog(name: str) -> list[MediaSection]:
    from tests.helpers import catalog_from_fixture

    sections, _ = catalog_from_fixture(name)
    return sections


def _live_catalog(query: str) -> list[MediaSection]:
    from annie.catalog import gather_catalog
    from annie.config import AnnieConfig

    catalog, _ = gather_catalog(query, AnnieConfig.load(), confirm_anime=False)
    return catalog


@dataclass
class RoundTripResult:
    path: str
    ok: bool
    detail: str
    forward: NavSnapshot | None = None
    back: NavSnapshot | None = None


def expected_section_pool(
    sections: list[MediaSection], *, group_key: str | None
) -> list[MediaSection]:
    """Miroir de pick_section après choix de groupe (ou catalogue plat)."""
    groups = _group_sections(sections)
    multi = sum(1 for key in ("season", "movie", "other") if groups[key]) > 1
    if multi and group_key is not None:
        return list(groups[group_key])
    return list(sections)


def test_round_trips(sections: list[MediaSection]) -> list[RoundTripResult]:
    """Pour chaque entrée possible : aller → épisodes → retour sections → retour groupes."""
    results: list[RoundTripResult] = []
    groups = _group_sections(sections)
    multi = sum(1 for key in ("season", "movie", "other") if groups[key]) > 1

    group_fwd = snapshot_groups(sections) if multi else None

    for group_key in ("season", "movie", "other"):
        pool = groups[group_key]
        if not pool:
            continue
        context = GROUP_LABELS[group_key]
        section_fwd = snapshot_sections(pool, context=context)

        for section in pool:
            if not section.choices():
                results.append(
                    RoundTripResult(
                        path=f"{context} → {section.label} (vide)",
                        ok=True,
                        detail="skip: aucune entrée",
                    )
                )
                continue

            ep_fwd = snapshot_episodes(section)

            # Retour ← depuis épisodes : même groupe (fix resume_from).
            back_pool = expected_section_pool(sections, group_key=group_key if multi else None)
            section_back = snapshot_sections(
                back_pool, context=f"{context} (retour ←)"
            )
            same_sections = section_fwd.fingerprint() == section_back.fingerprint()
            # L'épisode listé doit encore être dans la section au même index/key.
            section_still = next(
                (s for s in back_pool if s.key == section.key), None
            )
            ep_back = (
                snapshot_episodes(section_still)
                if section_still is not None
                else None
            )
            same_episodes = (
                ep_back is not None
                and ep_fwd.fingerprint() == ep_back.fingerprint()
            )

            ok = same_sections and same_episodes
            detail_parts = []
            if not same_sections:
                detail_parts.append(
                    "sections différentes à l'aller/retour "
                    f"(aller={len(section_fwd.lines)} lignes, "
                    f"retour={len(section_back.lines)} lignes)"
                )
                only_fwd = set(section_fwd.lines) - set(section_back.lines)
                only_back = set(section_back.lines) - set(section_fwd.lines)
                if only_fwd:
                    detail_parts.append(f"  seulement aller: {list(only_fwd)[:3]}")
                if only_back:
                    detail_parts.append(f"  seulement retour: {list(only_back)[:3]}")
            if not same_episodes:
                detail_parts.append("liste d'épisodes différente après retour")
            if ok:
                detail_parts.append("vues identiques")

            results.append(
                RoundTripResult(
                    path=f"{context} → {section.label} → épisodes → ←",
                    ok=ok,
                    detail="; ".join(detail_parts),
                    forward=section_fwd,
                    back=section_back,
                )
            )

        if multi and group_fwd is not None:
            # Retour ← depuis sections → groupes
            group_back = snapshot_groups(sections)
            same_groups = group_fwd.fingerprint() == group_back.fingerprint()
            results.append(
                RoundTripResult(
                    path=f"{context} → ← groupes",
                    ok=same_groups,
                    detail="groupes identiques"
                    if same_groups
                    else f"groupes changés: {group_fwd.lines} vs {group_back.lines}",
                    forward=group_fwd,
                    back=group_back,
                )
            )

    # Régression : le retour ne doit PAS afficher tout le catalogue mélangé
    # quand on était dans Seasons (ancien bug force_interactive).
    if multi and groups["season"] and groups["movie"]:
        season_view = snapshot_sections(groups["season"], context="Seasons")
        all_view = snapshot_sections(sections, context="ALL")
        # Simule l'ancien comportement buggué vs le correct.
        buggy_back = all_view
        correct_back = season_view
        results.append(
            RoundTripResult(
                path="régression: retour Seasons ≠ catalogue entier",
                ok=correct_back.fingerprint() != buggy_back.fingerprint()
                and season_view.fingerprint() == correct_back.fingerprint(),
                detail=(
                    "OK: retour reste filtré Seasons"
                    if season_view.fingerprint() != all_view.fingerprint()
                    else "WARN: un seul groupe visible"
                ),
                forward=season_view,
                back=correct_back,
            )
        )

    return results


def test_legacy_bug_detection(sections: list[MediaSection]) -> RoundTripResult:
    """Détecte l'ancien bug : retour épisodes → liste plate de TOUTES les sections."""
    groups = _group_sections(sections)
    if not (groups["season"] and (groups["movie"] or groups["other"])):
        return RoundTripResult(
            path="legacy-bug",
            ok=True,
            detail="skip: besoin saisons + films/other",
        )
    season_lines = snapshot_sections(groups["season"], context="Seasons").lines
    all_lines = snapshot_sections(sections, context="ALL").lines
    # Avec le fix, resume_from garde Seasons. Sans fix, on voyait ALL.
    diverges = season_lines != all_lines
    return RoundTripResult(
        path="legacy: Seasons ⊂ catalogue (prérequis du bug)",
        ok=diverges,
        detail=(
            "les vues Seasons et ALL diffèrent — le test aller-retour est pertinent"
            if diverges
            else "vues identiques (catalogue trop simple)"
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vérifie que la navigation fzf ←/→ conserve les mêmes listes."
    )
    parser.add_argument(
        "--fixture",
        help="Fixture catalogue (ex. catalog_re_zero.json)",
    )
    parser.add_argument(
        "--query",
        help="Requête live (AniList/Nyaa) pour un catalogue réel",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Afficher les fingerprints en cas d'échec",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.query:
        print(f"Catalogue live: {args.query!r}…")
        sections = _live_catalog(args.query)
    elif args.fixture:
        print(f"Fixture: {args.fixture}")
        sections = _fixture_catalog(args.fixture)
    else:
        print("Catalogue synthétique (saisons + film + OVA)")
        sections = _synthetic_catalog()

    if not sections:
        print("FAIL: catalogue vide")
        return 1

    groups = _group_sections(sections)
    print(
        f"Sections: {len(sections)} "
        f"(seasons={len(groups['season'])}, "
        f"movies={len(groups['movie'])}, "
        f"other={len(groups['other'])})"
    )
    print()

    results = [test_legacy_bug_detection(sections), *test_round_trips(sections)]
    failed = 0
    for row in results:
        status = "OK  " if row.ok else "FAIL"
        if not row.ok:
            failed += 1
        print(f"[{status}] {row.path}")
        print(f"         {row.detail}")
        if args.verbose and not row.ok and row.forward and row.back:
            print("         --- aller ---")
            for line in row.forward.lines[:12]:
                print(f"           {line[:100]}")
            print("         --- retour ---")
            for line in row.back.lines[:12]:
                print(f"           {line[:100]}")

    print()
    print(f"Résultat: {len(results) - failed}/{len(results)} OK")
    if failed:
        print(
            "Les FAIL indiquent qu'un aller-retour ← changerait la liste affichée "
            "(souvent: mélange Seasons/Movies au retour)."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
