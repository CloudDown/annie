"""AniList GraphQL (franchise → MalAnime / MalRelease)."""

from __future__ import annotations

import time
import urllib.error
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from annie.cache import read_json, read_json_stale, write_json
from annie.catalog import is_recap_movie
from annie.mal import (
    FRANCHISE_EXPAND_RELATIONS,
    MalAnime,
    SKIP_MAL_TYPES,
    _fallback_token,
    _score_candidate,
)
from annie.net import TokenBucket, fetch_json_post
from annie.parsing import normalize
from annie.paths import cache_dir

ANILIST_URL = "https://graphql.anilist.co"
USER_AGENT = "Annie/0.5 (+https://github.com/CloudDown/annie)"
DISK_CACHE_DIR = cache_dir() / "anilist"
DISK_CACHE_TTL = 7 * 24 * 3600

# AniList relationType → libellés proches MAL (franchise_to_releases).
_RELATION_TO_MAL = {
    "SEQUEL": "Sequel",
    "PREQUEL": "Prequel",
    "SIDE_STORY": "Side story",
    "SPIN_OFF": "Spin-off",
    "PARENT": "Parent story",
    "ALTERNATIVE": "Alternative Version",
    "SUMMARY": "Summary",
    "ADAPTATION": "Adaptation",
    "COMPILATION": "Summary",
    "SOURCE": "Adaptation",
    "CHARACTER": "Character",
    "OTHER": "Other",
    "CONTAINS": "Side story",
}

_FORMAT_TO_MAL = {
    "TV": "TV",
    "TV_SHORT": "TV",
    "MOVIE": "Movie",
    "OVA": "OVA",
    "ONA": "ONA",
    "SPECIAL": "Special",
    "MUSIC": "Music",
    "PV": "PV",
    "CM": "CM",
}

_MEDIA_FIELDS = """
id
idMal
title { romaji english native }
synonyms
format
episodes
startDate { year month day }
relations {
  edges {
    relationType(version: 2)
    node {
      id
      idMal
      title { romaji english native }
      format
      type
    }
  }
}
"""

_SEARCH_QUERY = f"""
query ($search: String, $page: Int, $perPage: Int) {{
  Page(page: $page, perPage: $perPage) {{
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
      {_MEDIA_FIELDS}
    }}
  }}
}}
"""

_MEDIA_QUERY = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
    {_MEDIA_FIELDS}
  }}
}}
"""

_response_cache: dict[str, dict] = {}


_anilist_limiter = TokenBucket(rate=0.9, burst=3)


def _meta_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load().metadata


def _cache_ttl() -> int:
    return _meta_cfg().cache_ttl


def _disk_path(key: str) -> Path:
    safe = key.strip("/").replace("/", "_").replace(" ", "_")
    return DISK_CACHE_DIR / f"{safe}.json"


def _cached(key: str) -> dict | None:
    hit = _response_cache.get(key)
    if hit is not None:
        return hit
    disk = read_json(_disk_path(key), ttl=_cache_ttl())
    if disk is not None:
        _response_cache[key] = disk
        return disk
    return None


def _store(key: str, payload: dict) -> dict:
    _response_cache[key] = payload
    write_json(_disk_path(key), payload)
    return payload


def _graphql(query: str, variables: dict, *, cache_key: str) -> dict:
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    last_error: Exception | None = None
    for attempt in range(4):
        _anilist_limiter.acquire()
        try:
            payload = fetch_json_post(
                ANILIST_URL,
                body={"query": query, "variables": variables},
                user_agent=USER_AGENT,
                timeout=25,
            )
            if payload.get("errors"):
                message = payload["errors"][0].get("message", "AniList error")
                raise RuntimeError(f"AniList: {message}")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise RuntimeError("AniList: empty response")
            return _store(cache_key, data)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < 3:
                time.sleep(3.0 * (attempt + 1))
                continue
            stale = read_json_stale(_disk_path(cache_key))
            if isinstance(stale, dict):
                _response_cache[cache_key] = stale
                return stale
            raise RuntimeError(f"AniList API error ({exc.code})") from exc
        except urllib.error.URLError as exc:
            stale = read_json_stale(_disk_path(cache_key))
            if isinstance(stale, dict):
                _response_cache[cache_key] = stale
                return stale
            raise RuntimeError(f"AniList unreachable: {exc.reason}") from exc
        except RuntimeError as exc:
            last_error = exc
            stale = read_json_stale(_disk_path(cache_key))
            if isinstance(stale, dict):
                _response_cache[cache_key] = stale
                return stale
            if attempt < 3:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
    if last_error:
        raise RuntimeError("AniList API error") from last_error
    raise RuntimeError("AniList API error")


def _format_mal(fmt: str | None) -> str:
    return _FORMAT_TO_MAL.get((fmt or "").upper(), "Unknown")


def _aired_from(start: dict | None) -> str | None:
    if not start or not start.get("year"):
        return None
    year = int(start["year"])
    month = int(start.get("month") or 1)
    day = int(start.get("day") or 1)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _stable_id(anilist_id: int, id_mal: int | None) -> int:
    """Préfère idMal ; sinon -anilist_id pour éviter collisions MAL."""
    if id_mal:
        return int(id_mal)
    return -int(anilist_id)


def _parse_media(
    data: dict, *, is_recap: bool = False, via_relation: str = "Root"
) -> MalAnime:
    title = data.get("title") or {}
    romaji = str(title.get("romaji") or "").strip()
    english = (title.get("english") or None) and str(title["english"]).strip()
    native = (title.get("native") or None) and str(title["native"]).strip()
    synonyms = tuple(
        str(s).strip()
        for s in (data.get("synonyms") or [])
        if isinstance(s, str) and s.strip()
    )
    anilist_id = int(data["id"])
    id_mal = data.get("idMal")
    mal_type = _format_mal(data.get("format"))
    display = romaji or english or native or f"anilist:{anilist_id}"
    return MalAnime(
        mal_id=_stable_id(anilist_id, int(id_mal) if id_mal else None),
        title=display,
        title_english=english or None,
        title_japanese=native or None,
        type=mal_type,
        episodes=data.get("episodes") or None,
        aired_from=_aired_from(data.get("startDate")),
        is_recap=is_recap or is_recap_movie(display),
        via_relation=via_relation,
        synonyms=synonyms,
        anilist_id=anilist_id,
    )


def _fetch_media(anilist_id: int) -> dict:
    data = _graphql(
        _MEDIA_QUERY,
        {"id": anilist_id},
        cache_key=f"media_{anilist_id}",
    )
    media = data.get("Media")
    if not isinstance(media, dict):
        raise RuntimeError(f"AniList: media {anilist_id} introuvable")
    return media


def _media_cached(anilist_id: int) -> bool:
    return _cached(f"media_{anilist_id}") is not None


def relation_nyaa_hints(data: dict) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    title = data.get("title") or {}
    for value in (
        title.get("english"),
        title.get("romaji"),
        title.get("native"),
        *(data.get("synonyms") or []),
    ):
        if value and str(value) not in seen:
            seen.add(str(value))
            queries.append(str(value))
    for edge in (data.get("relations") or {}).get("edges") or []:
        node = edge.get("node") or {}
        if (node.get("type") or "ANIME") != "ANIME":
            continue
        ntitle = node.get("title") or {}
        for value in (ntitle.get("english"), ntitle.get("romaji"), ntitle.get("native")):
            if value and str(value) not in seen:
                seen.add(str(value))
                queries.append(str(value))
    return queries


def _ingest_node(
    data: dict,
    *,
    from_recap: bool,
    via_relation: str,
    seen: dict[int, MalAnime],
    queue: list[tuple[int, bool, str]],
    queued: set[int],
) -> None:
    anilist_id = int(data["id"])
    if anilist_id in seen:
        return

    rel_mal = via_relation
    anime = _parse_media(
        data,
        is_recap=from_recap or is_recap_movie(
            (data.get("title") or {}).get("romaji") or ""
        ),
        via_relation=rel_mal,
    )
    if anime.type in SKIP_MAL_TYPES:
        return
    seen[anilist_id] = anime

    for edge in (data.get("relations") or {}).get("edges") or []:
        rel_type = str(edge.get("relationType") or "")
        mal_rel = _RELATION_TO_MAL.get(rel_type, rel_type.title())
        if mal_rel not in FRANCHISE_EXPAND_RELATIONS:
            continue
        node = edge.get("node") or {}
        if (node.get("type") or "ANIME") != "ANIME":
            continue
        child_id = int(node["id"])
        child_recap = from_recap or mal_rel == "Summary"
        if child_id not in seen and child_id not in queued:
            queue.append((child_id, child_recap, mal_rel))
            queued.add(child_id)


def collect_franchise(
    root_anilist_id: int,
    *,
    max_nodes: int = 32,
    on_root: Callable[[dict], None] | None = None,
    pool: ThreadPoolExecutor | None = None,
) -> list[MalAnime]:
    seen: dict[int, MalAnime] = {}
    queue: list[tuple[int, bool, str]] = []
    queued: set[int] = set()

    try:
        root_data = _fetch_media(root_anilist_id)
        if on_root is not None:
            on_root(root_data)
        _ingest_node(
            root_data,
            from_recap=False,
            via_relation="Root",
            seen=seen,
            queue=queue,
            queued=queued,
        )
    except Exception:
        return []

    def _run_batch(
        batch: list[tuple[int, bool, str]], executor: ThreadPoolExecutor
    ) -> None:
        if not batch:
            return
        cached: list[tuple[dict, bool, str]] = []
        pending: list[tuple[int, bool, str]] = []
        for anilist_id, from_recap, via_relation in batch:
            if _media_cached(anilist_id):
                try:
                    cached.append((_fetch_media(anilist_id), from_recap, via_relation))
                except Exception:
                    pending.append((anilist_id, from_recap, via_relation))
            else:
                pending.append((anilist_id, from_recap, via_relation))

        for data, from_recap, via_relation in cached:
            _ingest_node(
                data,
                from_recap=from_recap,
                via_relation=via_relation,
                seen=seen,
                queue=queue,
                queued=queued,
            )

        if not pending:
            return

        futures: dict[Future[dict], tuple[int, bool, str]] = {
            executor.submit(_fetch_media, anilist_id): (
                anilist_id,
                from_recap,
                via_relation,
            )
            for anilist_id, from_recap, via_relation in pending
        }
        for future in as_completed(futures):
            anilist_id, from_recap, via_relation = futures[future]
            try:
                data = future.result()
            except Exception:
                continue
            _ingest_node(
                data,
                from_recap=from_recap,
                via_relation=via_relation,
                seen=seen,
                queue=queue,
                queued=queued,
            )

    def _drain(executor: ThreadPoolExecutor) -> None:
        while queue and len(seen) < max_nodes:
            batch: list[tuple[int, bool, str]] = []
            while queue and len(seen) + len(batch) < max_nodes:
                anilist_id, from_recap, via_relation = queue.pop(0)
                if anilist_id in seen:
                    continue
                batch.append((anilist_id, from_recap, via_relation))
            _run_batch(batch, executor)

    workers = max(2, min(8, _meta_cfg().parallel))
    if pool is None:
        with ThreadPoolExecutor(max_workers=workers) as local:
            _drain(local)
    else:
        _drain(pool)

    return list(seen.values())


def _search_once(query: str, *, limit: int = 8) -> list[MalAnime]:
    data = _graphql(
        _SEARCH_QUERY,
        {"search": query, "page": 1, "perPage": limit},
        cache_key=f"search_{normalize(query)}_{limit}",
    )
    page = data.get("Page") or {}
    results: list[MalAnime] = []
    for item in page.get("media") or []:
        anime = _parse_media(item)
        if anime.type in SKIP_MAL_TYPES:
            continue
        results.append(anime)
    return results


def search_anime(query: str, *, limit: int = 8) -> list[MalAnime]:
    cleaned = normalize(query).strip() or query.strip()
    merged: dict[int, MalAnime] = {}

    try:
        for anime in _search_once(cleaned, limit=limit):
            key = anime.anilist_id or anime.mal_id
            merged.setdefault(key, anime)
    except RuntimeError:
        pass

    best_score = (
        max((_score_candidate(anime, query) for anime in merged.values()), default=0)
        if merged
        else 0
    )
    fallback = _fallback_token(cleaned)
    if fallback and fallback != cleaned and (not merged or best_score < 200):
        try:
            for anime in _search_once(fallback, limit=limit):
                key = anime.anilist_id or anime.mal_id
                merged.setdefault(key, anime)
        except RuntimeError:
            pass

    ranked = sorted(
        merged.values(),
        key=lambda anime: _score_candidate(anime, query),
        reverse=True,
    )
    return ranked[:limit]
