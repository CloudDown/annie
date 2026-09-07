"""Orchestration catalogue : métadonnées → Nyaa → sections."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from annie.catalog import (
    build_catalog,
    build_catalog_from_releases,
    fill_catalog_gaps,
    scope_releases_for_target,
)
from annie.nyaa import search
from annie.parsing import kind_from_options, parse_inline_target
from annie.types import MalRelease, MediaKind, MediaSection


def format_catalog_status(catalog: list[MediaSection], options: dict) -> str:
    """One line: source + seasons/movies, or why the scope missed."""
    source = options.get("catalog_source") or "nyaa"
    labels = {
        "allanime": "AllAnime",
        "franchise": "AniList/MAL",
        "nyaa": "Nyaa",
    }
    parts = [labels.get(source, str(source))]
    title = options.get("picked_title")
    if title:
        parts.append(str(title))

    tv = [s.season for s in catalog if s.kind == MediaKind.EPISODE and s.season]
    movies = sum(1 for s in catalog if s.kind == MediaKind.MOVIE)
    if tv:
        parts.append(f"{len(tv)} season{'s' if len(tv) != 1 else ''}")
    if movies:
        parts.append(f"{movies} movie{'s' if movies != 1 else ''}")

    if options.get("scope_missed"):
        wanted = options.get("target_season")
        avail = options.get("available_seasons") or []
        avail_txt = ", ".join(f"S{s}" for s in avail) if avail else "none"
        if wanted is not None:
            parts.append(f"S{wanted} missing (have {avail_txt})")
        else:
            parts.append("requested type not found")
    elif options.get("catalog_fallback"):
        parts.append("Nyaa fallback")
    return " · ".join(parts)


def _annotate_source(options: dict, source: str, **extra) -> dict:
    options["catalog_source"] = source
    options.update(extra)
    return options


def _warm_nyaa(query: str, *, category: str, filter_code: str) -> None:
    if not query:
        return
    try:
        search(query, category=category, filter_code=filter_code)
    except Exception:
        pass


def _available_seasons(releases: list[MalRelease]) -> list[int]:
    return sorted(
        {
            release.season
            for release in releases
            if release.kind == MediaKind.EPISODE and release.season is not None
        }
    )


def gather_catalog(
    raw_query: str, config, **overrides
) -> tuple[list[MediaSection], dict]:
    """AllAnime/MAL → Nyaa → sections. options contient catalog_source / scope."""
    from annie import metadata as meta
    from annie.ui import C, pick_anime_candidate, stylize, tui_available

    query, options = parse_inline_target(raw_query)
    category = overrides.get("category") or config.category
    filter_code = overrides.get("filter_code") or config.filter_code
    fill_gaps = overrides.get("fill_gaps", config.catalog.fill_gaps_on_search)
    target_season = overrides.get("target_season", options.get("season"))
    target_kind = overrides.get("target_kind", kind_from_options(options))
    confirm_anime = overrides.get("confirm_anime")
    if confirm_anime is None:
        confirm_anime = bool(sys.stdin.isatty() and tui_available())
    preselected = overrides.get("preselected")
    preselected_release: MalRelease | None = overrides.get("preselected_release")
    options["target_season"] = target_season

    def _finish(
        catalog: list[MediaSection], source: str, **extra
    ) -> tuple[list[MediaSection], dict]:
        return catalog, _annotate_source(options, source, **extra)

    if preselected_release is not None and meta.metadata_enabled(config):
        try:
            with ThreadPoolExecutor(max_workers=config.ui.mal_pool_workers) as pool:
                for warm_q in preselected_release.nyaa_queries[:12]:
                    if warm_q:
                        pool.submit(
                            _warm_nyaa,
                            warm_q,
                            category=category,
                            filter_code=filter_code,
                        )
                catalog = build_catalog_from_releases(
                    [preselected_release],
                    search=search,
                    category=category,
                    filter_code=filter_code,
                    skip_recap_movies=config.skip_recap_movies,
                    pool=pool,
                    fill_gaps=False,
                )
                if catalog:
                    if fill_gaps:
                        fill_catalog_gaps(
                            catalog,
                            search=search,
                            category=category,
                            filter_code=filter_code,
                            skip_recap_movies=config.skip_recap_movies,
                            pool=pool,
                        )
                    return _finish(
                        catalog,
                        "allanime",
                        picked_title=preselected_release.label,
                    )
        except Exception as exc:
            print(
                stylize(
                    f"annie: AllAnime catalog failed ({exc}), falling back",
                    C.MUTED,
                ),
                file=sys.stderr,
                flush=True,
            )
            options["catalog_fallback"] = True

    if meta.metadata_enabled(config):
        try:
            with ThreadPoolExecutor(max_workers=config.ui.mal_pool_workers) as pool:
                if preselected is not None:
                    chosen = preselected
                    pool.submit(
                        _warm_nyaa, query, category=category, filter_code=filter_code
                    )
                else:
                    candidates_future = pool.submit(
                        meta.search_anime, query, config=config
                    )
                    pool.submit(
                        _warm_nyaa, query, category=category, filter_code=filter_code
                    )

                    candidates = candidates_future.result()
                    chosen = None
                    if candidates:
                        if (
                            confirm_anime
                            and config.metadata.confirm_ambiguous
                            and meta.is_ambiguous_pick(candidates, query)
                        ):
                            chosen = pick_anime_candidate(candidates, query)
                        if chosen is None:
                            chosen = meta.pick_candidate(candidates, query)
                if chosen is not None:
                    options["mal_titles"] = tuple(
                        title
                        for title in (
                            chosen.title_english,
                            chosen.title,
                            chosen.title_japanese,
                            *chosen.synonyms[:4],
                        )
                        if title
                    )
                    options["picked_title"] = (
                        chosen.title_english or chosen.title or ""
                    )
                    for warm_q in dict.fromkeys(
                        [
                            chosen.title_english or "",
                            chosen.title,
                            chosen.title_japanese or "",
                            *chosen.synonyms[:6],
                        ]
                    ):
                        if warm_q:
                            pool.submit(
                                _warm_nyaa,
                                warm_q,
                                category=category,
                                filter_code=filter_code,
                            )

                    from_anilist = chosen.anilist_id is not None

                    def on_root(root_data: dict) -> None:
                        for hint in meta.relation_nyaa_hints(
                            root_data, from_anilist=from_anilist
                        ):
                            pool.submit(
                                _warm_nyaa,
                                hint,
                                category=category,
                                filter_code=filter_code,
                            )

                    releases = pool.submit(
                        meta.releases_for_anime,
                        chosen,
                        query=query,
                        skip_recap=config.skip_recap_movies,
                        on_root=on_root,
                        pool=pool,
                        config=config,
                    ).result()
                    if releases:
                        options["available_seasons"] = _available_seasons(releases)
                        structure = getattr(config.metadata, "structure", "")
                        source = (
                            "allanime" if structure == "allanime" else "franchise"
                        )
                        if target_season is not None or target_kind is not None:
                            scoped = scope_releases_for_target(
                                releases,
                                season=target_season,
                                kind=target_kind,
                            )
                            if not scoped:
                                options["scope_missed"] = True
                                return [], _annotate_source(options, source)
                    if releases:
                        catalog = build_catalog_from_releases(
                            releases,
                            search=search,
                            category=category,
                            filter_code=filter_code,
                            skip_recap_movies=config.skip_recap_movies,
                            pool=pool,
                            fill_gaps=False,
                        )
                        if catalog:
                            if fill_gaps:
                                fill_catalog_gaps(
                                    catalog,
                                    search=search,
                                    category=category,
                                    filter_code=filter_code,
                                    skip_recap_movies=config.skip_recap_movies,
                                    pool=pool,
                                )
                            return _finish(catalog, source)
        except Exception as exc:
            print(
                stylize(
                    f"annie: metadata unavailable ({exc}), Nyaa fallback",
                    C.MUTED,
                ),
                file=sys.stderr,
                flush=True,
            )
            options["catalog_fallback"] = True

    entries = search(query, category=category, filter_code=filter_code)
    if not entries:
        return [], _annotate_source(options, "nyaa")

    kind = kind_from_options(options)
    catalog = build_catalog(
        entries,
        query,
        season=options.get("season"),
        episode=options.get("episode"),
        kind=kind if options.get("season") or options.get("episode") or kind else None,
        skip_recap_movies=config.skip_recap_movies,
    )
    return _finish(catalog, "nyaa", catalog_fallback=options.get("catalog_fallback"))
