"""Façade métadonnées : AniList/MAL + structure AllAnime (ani-cli)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from annie import allanime, anilist
from annie.config import AnnieConfig
from annie.mal import (
    MalAnime,
    collect_franchise as mal_collect_franchise,
    franchise_to_releases,
    is_ambiguous_pick,
    pick_candidate,
    relation_nyaa_hints as mal_relation_hints,
    search_anime as mal_search_anime,
)
from annie.types import MalRelease

__all__ = [
    "MalAnime",
    "collect_franchise",
    "franchise_to_releases",
    "is_ambiguous_pick",
    "metadata_enabled",
    "pick_candidate",
    "relation_nyaa_hints",
    "releases_for_anime",
    "search_anime",
]


def metadata_enabled(config: AnnieConfig | None = None) -> bool:
    cfg = config or AnnieConfig.load()
    return cfg.metadata.enabled


def search_anime(query: str, *, limit: int = 8, config: AnnieConfig | None = None) -> list[MalAnime]:
    cfg = config or AnnieConfig.load()
    provider = cfg.metadata.provider
    errors: list[Exception] = []

    if provider == "anilist":
        try:
            results = anilist.search_anime(query, limit=limit)
            if results:
                return results
        except Exception as exc:
            errors.append(exc)
        if cfg.metadata.fallback_mal and cfg.mal.enabled:
            try:
                return mal_search_anime(query, limit=limit)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[-1]
        return []

    try:
        return mal_search_anime(query, limit=limit)
    except Exception as exc:
        if cfg.metadata.fallback_anilist:
            try:
                return anilist.search_anime(query, limit=limit)
            except Exception:
                raise exc from None
        raise


def collect_franchise(
    chosen: MalAnime,
    *,
    on_root: Callable[[dict], None] | None = None,
    pool: ThreadPoolExecutor | None = None,
    config: AnnieConfig | None = None,
) -> list[MalAnime]:
    cfg = config or AnnieConfig.load()

    if chosen.anilist_id is not None:
        try:
            franchise = anilist.collect_franchise(
                chosen.anilist_id, on_root=on_root, pool=pool
            )
            if franchise:
                return franchise
        except Exception:
            if not (cfg.metadata.fallback_mal and cfg.mal.enabled and chosen.mal_id > 0):
                raise

    if chosen.mal_id > 0 and cfg.mal.enabled:
        return mal_collect_franchise(chosen.mal_id, on_root=on_root, pool=pool)
    return []


def relation_nyaa_hints(root_data: dict, *, from_anilist: bool) -> list[str]:
    if from_anilist:
        return anilist.relation_nyaa_hints(root_data)
    return mal_relation_hints(root_data)


def _franchise_releases(
    chosen: MalAnime,
    *,
    query: str,
    skip_recap: bool,
    on_root: Callable[[dict], None] | None,
    pool: ThreadPoolExecutor | None,
    config: AnnieConfig,
) -> list[MalRelease]:
    franchise = collect_franchise(
        chosen, on_root=on_root, pool=pool, config=config
    )
    if not franchise:
        return []
    return franchise_to_releases(
        franchise,
        skip_recap=skip_recap,
        root_id=chosen.mal_id,
        user_query=query,
    )


def releases_for_anime(
    chosen: MalAnime,
    *,
    query: str,
    skip_recap: bool,
    on_root: Callable[[dict], None] | None = None,
    pool: ThreadPoolExecutor | None = None,
    config: AnnieConfig | None = None,
) -> list[MalRelease]:
    cfg = config or AnnieConfig.load()

    franchise_rels = _franchise_releases(
        chosen,
        query=query,
        skip_recap=skip_recap,
        on_root=on_root,
        pool=pool,
        config=cfg,
    )

    # Structure AllAnime (shows discrets S1/S2/Movie) — même source qu'ani-cli.
    if cfg.metadata.structure == "allanime":
        try:
            aa_rels = allanime.releases_for_query(
                query,
                chosen=chosen,
                skip_recap=skip_recap,
            )
            if aa_rels:
                from annie.types import MediaKind

                aa_tv = sum(1 for r in aa_rels if r.kind == MediaKind.EPISODE)
                fr_tv = sum(
                    1 for r in franchise_rels if r.kind == MediaKind.EPISODE
                )
                # AllAnime parfois incomplet (Overlord S1 seul) → garder le graphe.
                if fr_tv >= 2 and aa_tv <= 1 and fr_tv > aa_tv:
                    return franchise_rels
                return aa_rels
        except Exception:
            pass

    return franchise_rels
