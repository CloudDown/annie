"""MyAnimeList via Jikan API (franchise → Nyaa queries)."""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from annie.cache import read_json, write_json
from annie.media import MediaKind, MalRelease, is_recap_movie, normalize
from annie.net import fetch_json

JIKAN_BASE = "https://api.jikan.moe/v4"
USER_AGENT = "Annie/0.5 (+https://github.com/CloudDown/annie)"
MAL_PARALLEL = 10
DISK_CACHE_DIR = Path.home() / ".cache" / "annie" / "jikan"
DISK_CACHE_TTL = 7 * 24 * 3600

SKIP_MAL_TYPES = frozenset({"Music", "PV", "CM", "Unknown"})
WATCHABLE_TYPES = frozenset({"TV", "Movie", "OVA", "Special", "ONA", "TV Special"})
MAIN_TV_RELATIONS = frozenset({"Root", "Sequel", "Prequel", "Parent story"})
FRANCHISE_EXPAND_RELATIONS = frozenset(
    {
        "Sequel",
        "Prequel",
        "Side story",
        "Spin-off",
        "Parent story",
        "Alternative setting",
        "Summary",
        "Adaptation",
        "Full story",
    }
)
SPLIT_COUR_RE = re.compile(r"\b(?:part\s*2|2(?:nd)?\s*cour|second\s*cour|cour\s*2)\b", re.I)
SPINOFF_MARKERS_RE = re.compile(
    r"\b(nikki|diaries|picture drama|mini anime|chibi|an explosion on|after story)\b",
    re.I,
)

_response_cache: dict[str, dict] = {}


class _TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                time.sleep((1.0 - self._tokens) / self._rate)


_jikan_limiter = _TokenBucket(rate=4.0, burst=5)


@dataclass(frozen=True)
class MalAnime:
    mal_id: int
    title: str
    title_english: str | None
    title_japanese: str | None
    type: str
    episodes: int | None
    aired_from: str | None
    is_recap: bool = False
    via_relation: str = "Root"


@dataclass(frozen=True)
class TopAnimeEntry:
    mal_id: int
    rank: int
    title: str
    title_english: str | None
    anime_type: str


def fetch_top_anime(limit: int = 100, *, cache_path: Path | None = None) -> list[TopAnimeEntry]:
    """Top anime MAL via Jikan (/top/anime), 25 entrées par page."""
    if limit < 1:
        return []

    cache_file = cache_path or (DISK_CACHE_DIR / f"top_anime_{limit}.json")
    entries: list[TopAnimeEntry] = []
    if cache_file.exists():
        cached = read_json(cache_file, ttl=7 * 24 * 3600)
        if cached and isinstance(cached.get("entries"), list):
            for row in cached["entries"]:
                entries.append(
                    TopAnimeEntry(
                        mal_id=int(row["mal_id"]),
                        rank=int(row["rank"]),
                        title=str(row["title"]),
                        title_english=row.get("title_english"),
                        anime_type=str(row.get("anime_type") or "Unknown"),
                    )
                )

    if len(entries) >= limit:
        return entries[:limit]

    page = max(1, len(entries) // 25 + 1)
    max_pages = (limit + 24) // 25 + 2

    def _persist() -> None:
        write_json(
            cache_file,
            {
                "entries": [
                    {
                        "mal_id": entry.mal_id,
                        "rank": entry.rank,
                        "title": entry.title,
                        "title_english": entry.title_english,
                        "anime_type": entry.anime_type,
                    }
                    for entry in entries
                ]
            },
        )

    while len(entries) < limit and page <= max_pages:
        payload: dict | None = None
        for attempt in range(20):
            try:
                payload = _get(f"/top/anime?page={page}")
                break
            except RuntimeError as exc:
                if "504" not in str(exc) and "429" not in str(exc):
                    raise
                time.sleep(min(60.0, 3.0 * (attempt + 1)))
        if payload is None:
            _persist()
            break

        batch = payload.get("data") or []
        if not batch:
            break
        for item in batch:
            entries.append(
                TopAnimeEntry(
                    mal_id=int(item["mal_id"]),
                    rank=int(item.get("rank") or len(entries) + 1),
                    title=str(item.get("title") or ""),
                    title_english=item.get("title_english"),
                    anime_type=str(item.get("type") or "Unknown"),
                )
            )
            if len(entries) >= limit:
                break

        _persist()
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_next_page"):
            break
        page += 1
        time.sleep(1.0)

    return entries[:limit]

def _disk_cache_path(path: str) -> Path:
    safe = path.strip("/").replace("/", "_")
    return DISK_CACHE_DIR / f"{safe}.json"


def _cached_payload(path: str) -> dict | None:
    cached = _response_cache.get(path)
    if cached is not None:
        return cached
    disk_cached = read_json(_disk_cache_path(path), ttl=DISK_CACHE_TTL)
    if disk_cached is not None:
        _response_cache[path] = disk_cached
        return disk_cached
    return None


def _get(path: str, *, retries: int = 4) -> dict:
    cached = _cached_payload(path)
    if cached is not None:
        return cached

    last_error: Exception | None = None
    url = f"{JIKAN_BASE}{path}"
    for attempt in range(retries):
        _jikan_limiter.acquire()
        try:
            payload = fetch_json(url, user_agent=USER_AGENT, timeout=25)
            _response_cache[path] = payload
            write_json(_disk_cache_path(path), payload)
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"MAL API error ({exc.code})") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"MAL API unreachable: {exc.reason}") from exc
    if last_error:
        raise RuntimeError("MAL API error") from last_error
    raise RuntimeError("MAL API error")


def _fetch_anime_full(mal_id: int) -> dict:
    return _get(f"/anime/{mal_id}/full")["data"]


def _mal_path(mal_id: int) -> str:
    return f"/anime/{mal_id}/full"


def _mal_cached(mal_id: int) -> bool:
    return _cached_payload(_mal_path(mal_id)) is not None


def relation_nyaa_hints(data: dict) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for field in ("title_english", "title", "title_japanese"):
        value = data.get(field)
        if value and value not in seen:
            seen.add(value)
            queries.append(str(value))
    for relation in data.get("relations", []):
        for entry in relation.get("entry", []):
            if entry.get("type") != "anime":
                continue
            name = entry.get("name")
            if name and name not in seen:
                seen.add(name)
                queries.append(str(name))
    return queries


def _ingest_franchise_node(
    data: dict,
    *,
    from_recap: bool,
    via_relation: str,
    seen: dict[int, MalAnime],
    recap_ids: set[int],
    queue: list[tuple[int, bool, str]],
    queued: set[int],
) -> None:
    mal_id = int(data["mal_id"])
    if mal_id in seen:
        return

    is_recap = from_recap or is_recap_movie(data.get("title") or "")
    anime = _parse_anime(data, is_recap=is_recap, via_relation=via_relation)
    if anime.type in SKIP_MAL_TYPES:
        return
    seen[mal_id] = anime
    if is_recap:
        recap_ids.add(mal_id)

    for relation in data.get("relations", []):
        rel_name = relation.get("relation", "")
        if rel_name not in FRANCHISE_EXPAND_RELATIONS:
            continue
        child_recap = from_recap or rel_name == "Summary"
        for entry in relation.get("entry", []):
            if entry.get("type") != "anime":
                continue
            child_id = int(entry["mal_id"])
            if child_id not in seen and child_id not in queued:
                queue.append((child_id, child_recap, rel_name))
                queued.add(child_id)


def _parse_anime(data: dict, *, is_recap: bool = False, via_relation: str = "Root") -> MalAnime:
    aired = data.get("aired") or {}
    return MalAnime(
        mal_id=int(data["mal_id"]),
        title=str(data.get("title") or ""),
        title_english=data.get("title_english") or None,
        title_japanese=data.get("title_japanese") or None,
        type=str(data.get("type") or "Unknown"),
        episodes=data.get("episodes") or None,
        aired_from=(aired.get("from") or "")[:10] or None,
        is_recap=is_recap,
        via_relation=via_relation,
    )


def search_anime(query: str, *, limit: int = 8) -> list[MalAnime]:
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    payload = _get(f"/anime?{params}")
    results: list[MalAnime] = []
    for item in payload.get("data", []):
        anime = _parse_anime(item)
        if anime.type in SKIP_MAL_TYPES:
            continue
        results.append(anime)
    return results


def fetch_anime_full(mal_id: int) -> MalAnime:
    return _parse_anime(_fetch_anime_full(mal_id))


def _score_candidate(anime: MalAnime, query: str) -> int:
    tokens = [token for token in normalize(query).split() if len(token) > 1]
    title = anime.title_english or anime.title or ""
    haystacks = [normalize(anime.title), normalize(anime.title_english or ""), normalize(anime.title_japanese or "")]
    score = 0
    for token in tokens:
        if any(token in hay for hay in haystacks):
            score += 120
    if normalize(query) in haystacks[0] or normalize(query) in haystacks[1]:
        score += 200
    if anime.type == "TV":
        score += 40
    if anime.episodes:
        score += min(anime.episodes, 30)
    if SPINOFF_MARKERS_RE.search(title) or SPINOFF_MARKERS_RE.search(anime.title):
        score -= 400
    if re.search(
        r"\bseason\s*[2-9]|\b[2-9](?:nd|rd|th)\s*season|\bpart\s*[2-9]\b"
        r"|\b(?:ii|iii|iv|v)\b|(?:season\s+)?[2-9]\s*$",
        title,
        re.I,
    ):
        score -= 100
    return score


def pick_candidate(candidates: list[MalAnime], query: str) -> MalAnime | None:
    if not candidates:
        return None
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (_score_candidate(item[1], query), -item[0]),
        reverse=True,
    )
    return ranked[0][1]


def _is_split_cour_continuation(_prev: MalAnime, nxt: MalAnime) -> bool:
    nxt_title = normalize(nxt.title_english or nxt.title)
    return bool(SPLIT_COUR_RE.search(nxt_title))


def _merge_split_cours_tv(tv: list[MalAnime]) -> list[tuple[MalAnime, MalAnime | None]]:
    out: list[tuple[MalAnime, MalAnime | None]] = []
    i = 0
    while i < len(tv):
        cur = tv[i]
        if i + 1 < len(tv):
            nxt = tv[i + 1]
            if _is_split_cour_continuation(cur, nxt):
                total = (cur.episodes or 0) + (nxt.episodes or 0)
                merged = MalAnime(
                    mal_id=cur.mal_id,
                    title=cur.title,
                    title_english=cur.title_english,
                    title_japanese=cur.title_japanese,
                    type=cur.type,
                    episodes=total or None,
                    aired_from=cur.aired_from,
                    is_recap=cur.is_recap,
                    via_relation=cur.via_relation,
                )
                out.append((merged, nxt))
                i += 2
                continue
        out.append((cur, None))
        i += 1
    return out


def collect_franchise(
    root_id: int,
    *,
    max_nodes: int = 32,
    on_root: Callable[[dict], None] | None = None,
    pool: ThreadPoolExecutor | None = None,
) -> list[MalAnime]:
    seen: dict[int, MalAnime] = {}
    recap_ids: set[int] = set()
    queue: list[tuple[int, bool, str]] = []
    queued: set[int] = set()

    try:
        root_data = _fetch_anime_full(root_id)
        if on_root is not None:
            on_root(root_data)
        _ingest_franchise_node(
            root_data,
            from_recap=False,
            via_relation="Root",
            seen=seen,
            recap_ids=recap_ids,
            queue=queue,
            queued=queued,
        )
    except Exception:
        return []

    def _run_batch(batch: list[tuple[int, bool, str]], executor: ThreadPoolExecutor) -> None:
        if not batch:
            return
        cached: list[tuple[dict, bool, str]] = []
        pending: list[tuple[int, bool, str]] = []
        for mal_id, from_recap, via_relation in batch:
            if _mal_cached(mal_id):
                try:
                    cached.append((_fetch_anime_full(mal_id), from_recap, via_relation))
                except Exception:
                    pending.append((mal_id, from_recap, via_relation))
            else:
                pending.append((mal_id, from_recap, via_relation))

        for data, from_recap, via_relation in cached:
            _ingest_franchise_node(
                data,
                from_recap=from_recap,
                via_relation=via_relation,
                seen=seen,
                recap_ids=recap_ids,
                queue=queue,
                queued=queued,
            )

        if not pending:
            return

        futures: dict[Future[dict], tuple[int, bool, str]] = {
            executor.submit(_fetch_anime_full, mal_id): (mal_id, from_recap, via_relation)
            for mal_id, from_recap, via_relation in pending
        }
        for future in as_completed(futures):
            mal_id, from_recap, via_relation = futures[future]
            try:
                data = future.result()
            except Exception:
                continue
            _ingest_franchise_node(
                data,
                from_recap=from_recap,
                via_relation=via_relation,
                seen=seen,
                recap_ids=recap_ids,
                queue=queue,
                queued=queued,
            )

    def _drain_queue(executor: ThreadPoolExecutor) -> None:
        while queue and len(seen) < max_nodes:
            batch: list[tuple[int, bool, str]] = []
            while queue and len(seen) + len(batch) < max_nodes:
                mal_id, from_recap, via_relation = queue.pop(0)
                if mal_id in seen:
                    continue
                batch.append((mal_id, from_recap, via_relation))
            _run_batch(batch, executor)

    if pool is None:
        with ThreadPoolExecutor(max_workers=MAL_PARALLEL) as local_pool:
            _drain_queue(local_pool)
    else:
        _drain_queue(pool)

    return list(seen.values())


def _mal_kind(mal_type: str) -> MediaKind:
    mapping = {
        "TV": MediaKind.EPISODE,
        "Movie": MediaKind.MOVIE,
        "OVA": MediaKind.OVA,
        "Special": MediaKind.SPECIAL,
        "ONA": MediaKind.SPECIAL,
        "TV Special": MediaKind.SPECIAL,
    }
    return mapping.get(mal_type, MediaKind.UNKNOWN)


def _short_movie_label(title: str) -> str:
    match = re.search(r"\bMovie\s*(\d+)\b", title, re.I)
    if match:
        return f"Movie {match.group(1)}"
    if ":" in title:
        return title.split(":", 1)[1].strip()
    return title


def _title_shortcuts(title: str) -> list[str]:
    shortcuts: list[str] = []
    cleaned = title.strip()
    if not cleaned:
        return shortcuts
    if ":" in cleaned:
        head = cleaned.split(":", 1)[0].strip()
        if len(head) >= 3:
            shortcuts.append(head)
    first = cleaned.split()[0]
    if len(first) >= 3 and first not in shortcuts:
        shortcuts.append(first)
    return shortcuts


def nyaa_queries_for(
    anime: MalAnime,
    *,
    user_query: str = "",
    season: int | None = None,
) -> list[str]:
    queries: list[str] = []
    if user_query.strip():
        queries.append(user_query.strip())

    for value in (anime.title_english, anime.title, anime.title_japanese):
        if not value:
            continue
        if value not in queries:
            queries.append(value)
        for short in _title_shortcuts(value):
            if short not in queries:
                queries.append(short)

    if season is not None:
        base = queries[0] if queries else ""
        short = base.split(":", 1)[0].strip() if base else base
        for variant in (f"{short} S{season:02d}", f"{short} Season {season:02d}"):
            if variant.strip() and variant not in queries:
                queries.append(variant)

    return queries[:5]


def franchise_to_releases(
    franchise: list[MalAnime],
    *,
    skip_recap: bool = False,
    root_id: int | None = None,
    user_query: str = "",
) -> list[MalRelease]:
    entries = [anime for anime in franchise if anime.type in WATCHABLE_TYPES]
    if skip_recap:
        entries = [anime for anime in entries if not anime.is_recap]

    root = next((anime for anime in entries if anime.mal_id == root_id), None)
    if root is None and entries:
        root = next((anime for anime in entries if anime.type == "TV"), entries[0])
    root_query = (root.title_english or root.title) if root else ""

    tv = sorted(
        [
            anime
            for anime in entries
            if anime.type == "TV"
            and anime.via_relation in MAIN_TV_RELATIONS
            and not anime.is_recap
            and anime.episodes
        ],
        key=lambda anime: anime.aired_from or "9999",
    )
    tv_seasons = _merge_split_cours_tv(tv)
    movies = sorted(
        [anime for anime in entries if anime.type == "Movie"],
        key=lambda anime: anime.aired_from or "9999",
    )
    extras = sorted(
        [anime for anime in entries if anime.type not in {"TV", "Movie"}],
        key=lambda anime: anime.aired_from or "9999",
    )

    releases: list[MalRelease] = []
    for index, (anime, part2) in enumerate(tv_seasons, start=1):
        queries: list[str] = []
        for value in (
            *nyaa_queries_for(anime, user_query=user_query, season=index),
            *(nyaa_queries_for(part2, user_query=user_query, season=index) if part2 else ()),
            root_query,
        ):
            if value and value not in queries:
                queries.append(value)
        releases.append(
            MalRelease(
                mal_id=anime.mal_id,
                label=f"Season {index:02d}",
                kind=MediaKind.EPISODE,
                season=index,
                episode_count=anime.episodes,
                nyaa_queries=queries,
                sort_key=(index * 10, anime.title.lower()),
            )
        )

    for index, anime in enumerate(movies, start=1):
        releases.append(
            MalRelease(
                mal_id=anime.mal_id,
                label=_short_movie_label(anime.title_english or anime.title),
                kind=MediaKind.MOVIE,
                season=None,
                episode_count=anime.episodes,
                nyaa_queries=nyaa_queries_for(anime, user_query=user_query),
                sort_key=(15 + index, anime.title.lower()),
            )
        )

    for anime in extras:
        kind = _mal_kind(anime.type)
        label = anime.title_english or anime.title
        if kind == MediaKind.OVA:
            label = f"OVA · {label}"
        releases.append(
            MalRelease(
                mal_id=anime.mal_id,
                label=label,
                kind=kind,
                season=None,
                episode_count=anime.episodes,
                nyaa_queries=nyaa_queries_for(anime, user_query=user_query),
                sort_key=(25, anime.title.lower()),
            )
        )

    releases.sort(key=lambda release: release.sort_key)
    return releases
