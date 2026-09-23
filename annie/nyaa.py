"""Client de recherche Nyaa.si."""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from annie.cache import read_json, write_json
from annie.net import TokenBucket, fetch_text
from annie.paths import cache_dir

NYAA_BASE = "https://nyaa.si"
USER_AGENT = "Annie/0.5 (+https://github.com/CloudDown/annie)"
NYAA_SEARCH_PAGES = 2
NYAA_FAST_PAGES = NYAA_SEARCH_PAGES
DISK_CACHE_DIR = cache_dir() / "nyaa"
DISK_CACHE_TTL = 45 * 60

# Moins que ça, une plage (01-03) n'est pas un pack de saison.
MIN_SEASON_SPAN = 8

_search_cache: dict[tuple[str, ...], tuple[float, list["NyaaEntry"]]] = {}
_inflight: dict[tuple[str, ...], Future] = {}
_inflight_lock = threading.Lock()
_nyaa_limiter: TokenBucket | None = None

ROW_RE = re.compile(
    r'<tr class="(?:default|success|danger|warning)">(.*?)</tr>',
    re.S,
)
TITLE_RE = re.compile(
    r'<a href="/view/\d+" title="([^"]+)">([^<]+)</a>',
)
MAGNET_RE = re.compile(r'href="(magnet:[^"]+)"')
SIZE_RE = re.compile(r'<td class="text-center">([^<]+)</td>')
NUMERIC_CELL_RE = re.compile(r'<td class="text-center">\s*(\d+)\s*</td>')


def _nyaa_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load_cached().nyaa


def _disk_cache_ttl() -> int:
    return _nyaa_cfg().cache_ttl


def _get_limiter() -> TokenBucket:
    global _nyaa_limiter
    if _nyaa_limiter is None:
        cfg = _nyaa_cfg()
        _nyaa_limiter = TokenBucket(rate=cfg.rate, burst=cfg.rate_burst)
    return _nyaa_limiter


@dataclass(frozen=True)
class NyaaEntry:
    title: str
    magnet: str
    size: str
    date: str
    seeders: int
    leechers: int
    downloads: int
    trusted: bool


def _disk_cache_path(cache_key: tuple[str, ...]) -> Path:
    safe = re.sub(r"[^\w\-.]+", "_", "-".join(cache_key)).strip("_")[:120]
    return DISK_CACHE_DIR / f"{safe}.json"


def _cache_key(
    query: str,
    category: str,
    filter_code: str,
    pages: int,
    *,
    exhaustive: bool,
    sort: str,
    order: str,
) -> tuple[str, ...]:
    mode = "x" if exhaustive else "a"
    return (query, category, filter_code, f"{mode}{pages}", sort, order)


def _entries_to_json(entries: list[NyaaEntry]) -> list[dict]:
    return [
        {
            "title": entry.title,
            "magnet": entry.magnet,
            "size": entry.size,
            "date": entry.date,
            "seeders": entry.seeders,
            "leechers": entry.leechers,
            "downloads": entry.downloads,
            "trusted": entry.trusted,
        }
        for entry in entries
    ]


def _entries_from_json(payload: list[dict]) -> list[NyaaEntry]:
    return [NyaaEntry(**item) for item in payload]


def _cached_entries(cache_key: tuple[str, ...]) -> list[NyaaEntry] | None:
    ttl = _disk_cache_ttl()
    cached = _search_cache.get(cache_key)
    if cached is not None:
        stored_at, entries = cached
        if time.monotonic() - stored_at <= ttl:
            return entries
        del _search_cache[cache_key]
    disk_cached = read_json(_disk_cache_path(cache_key), ttl=ttl)
    if disk_cached is not None:
        entries = _entries_from_json(disk_cached)
        _search_cache[cache_key] = (time.monotonic(), entries)
        return entries
    return None


def _store_entries(
    cache_key: tuple[str, ...], entries: list[NyaaEntry]
) -> list[NyaaEntry]:
    _search_cache[cache_key] = (time.monotonic(), entries)
    write_json(_disk_cache_path(cache_key), _entries_to_json(entries))
    return entries


def _parse_page(page: str) -> list[NyaaEntry]:
    entries: list[NyaaEntry] = []
    for row in ROW_RE.findall(page):
        title_match = TITLE_RE.search(row)
        magnet_match = MAGNET_RE.search(row)
        if not title_match or not magnet_match:
            continue

        title = html.unescape(title_match.group(1))
        magnet = html.unescape(magnet_match.group(1))

        size_cells = SIZE_RE.findall(row)
        size = size_cells[0] if size_cells else "?"

        date_match = re.search(
            r'<td class="text-center" data-timestamp="\d+">([^<]+)</td>',
            row,
        )
        date = date_match.group(1).strip() if date_match else "?"

        numbers = [int(value) for value in NUMERIC_CELL_RE.findall(row)]
        if len(numbers) < 3:
            continue

        seeders, leechers, downloads = numbers[-3:]
        trusted = 'class="success"' in row or "trusted" in row.lower()

        entries.append(
            NyaaEntry(
                title=title,
                magnet=magnet,
                size=size,
                date=date,
                seeders=seeders,
                leechers=leechers,
                downloads=downloads,
                trusted=trusted,
            )
        )
    return entries


def _catalog_search_limits() -> tuple[int, float]:
    from annie.config import AnnieConfig

    catalog = AnnieConfig.load_cached().catalog
    return catalog.min_seeders_relaxed, catalog.season_batch_min_coverage


def _season_page_ready(
    entries: list[NyaaEntry],
    *,
    min_seeders: int | None = None,
    min_coverage: float | None = None,
) -> bool:
    """Page 1 assez fournie : pack de saison, ou épisodes qui couvrent la saison."""
    if min_seeders is None or min_coverage is None:
        configured_seeders, configured_coverage = _catalog_search_limits()
        if min_seeders is None:
            min_seeders = configured_seeders
        if min_coverage is None:
            min_coverage = configured_coverage
    from annie.catalog import parse_batch_episode_range
    from annie.parsing import parse_title
    from annie.types import MediaKind

    by_season: dict[int, set[int]] = {}
    for entry in entries:
        if entry.seeders < min_seeders:
            continue
        _season, episodes = parse_batch_episode_range(entry.title)
        if len(episodes) >= MIN_SEASON_SPAN:
            return True
        parsed = parse_title(entry.title)
        if parsed.kind != MediaKind.EPISODE or parsed.episode is None:
            continue
        season = parsed.season or 1
        by_season.setdefault(season, set()).add(parsed.episode)
    for episodes in by_season.values():
        if not episodes:
            continue
        highest = max(episodes)
        if highest < MIN_SEASON_SPAN:
            continue
        covered = sum(1 for episode in episodes if 1 <= episode <= highest)
        if covered / highest >= min_coverage:
            return True
    return False


def _claim_inflight(cache_key: tuple[str, ...]) -> tuple[bool, Future]:
    with _inflight_lock:
        existing = _inflight.get(cache_key)
        if existing is not None:
            return False, existing
        future: Future = Future()
        _inflight[cache_key] = future
        return True, future


def _release_inflight(cache_key: tuple[str, ...], future: Future) -> None:
    with _inflight_lock:
        if _inflight.get(cache_key) is future:
            del _inflight[cache_key]


def _fetch_search(
    query: str,
    *,
    category: str,
    filter_code: str,
    sort: str,
    order: str,
    pages: int,
    retries: int,
    timeout: float,
    exhaustive: bool,
) -> tuple[list[NyaaEntry], bool]:
    """Retourne (entrées, toutes les pages demandées ont été lues)."""
    merged: list[NyaaEntry] = []
    seen_magnets: set[str] = set()
    complete = True

    for page in range(1, pages + 1):
        page_params: dict[str, str] = {
            "f": filter_code,
            "c": category,
            "q": query,
            "s": sort,
            "o": order,
        }
        if page > 1:
            page_params["p"] = str(page)
        url = f"{NYAA_BASE}/?{urllib.parse.urlencode(page_params)}"

        last_error: Exception | None = None
        page_entries: list[NyaaEntry] = []
        for attempt in range(retries):
            _get_limiter().acquire()
            try:
                html_page = fetch_text(url, user_agent=USER_AGENT, timeout=timeout)
                page_entries = _parse_page(html_page)
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 429 and attempt < retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if page == 1:
                    raise
                page_entries = []
                break
            except urllib.error.URLError:
                raise
        else:
            if page == 1 and last_error:
                raise last_error
            complete = False
            break

        if not page_entries:
            # Dernière page vide = plus de résultats. Une erreur HTTP ne l'est pas.
            if page < pages or last_error is not None:
                complete = False
            break

        for entry in page_entries:
            if entry.magnet in seen_magnets:
                continue
            seen_magnets.add(entry.magnet)
            merged.append(entry)

        if (
            page == 1
            and pages > 1
            and not exhaustive
            and _season_page_ready(merged)
        ):
            complete = False
            break

    return merged, complete


def _remember(
    cache_key: tuple[str, ...],
    entries: list[NyaaEntry],
    *,
    query: str,
    category: str,
    filter_code: str,
    pages: int,
    exhaustive: bool,
    sort: str,
    order: str,
    complete: bool,
) -> list[NyaaEntry]:
    stored = _store_entries(cache_key, entries)
    if not complete:
        return stored
    other = _cache_key(
        query,
        category,
        filter_code,
        pages,
        exhaustive=not exhaustive,
        sort=sort,
        order=order,
    )
    if other != cache_key:
        _store_entries(other, entries)
    return stored


def search(
    query: str,
    *,
    category: str = "0_0",
    filter_code: str = "0",
    sort: str | None = None,
    order: str | None = None,
    pages: int | None = None,
    retries: int | None = None,
    exhaustive: bool | None = None,
) -> list[NyaaEntry]:
    """Recherche Nyaa. ``exhaustive`` force toutes les pages.

    Sans ``pages`` (préchauffage, catalogue), la page 2 est sautée quand la
    page 1 contient déjà un pack de saison crédible. ``pages`` explicite
    (``annie search``) garde le nombre demandé, sauf ``exhaustive=False``.
    """
    cfg = _nyaa_cfg()
    sort = sort if sort is not None else cfg.sort
    order = order if order is not None else cfg.order
    if exhaustive is None:
        exhaustive = pages is not None
    pages = pages if pages is not None else cfg.search_pages
    retries = retries if retries is not None else cfg.retries
    pages = max(1, pages)
    cache_key = _cache_key(
        query,
        category,
        filter_code,
        pages,
        exhaustive=exhaustive,
        sort=sort,
        order=order,
    )
    cached = _cached_entries(cache_key)
    if cached is not None:
        return cached

    owner, future = _claim_inflight(cache_key)
    if not owner:
        return future.result()

    try:
        cached = _cached_entries(cache_key)
        if cached is not None:
            future.set_result(cached)
            return cached
        entries, complete = _fetch_search(
            query,
            category=category,
            filter_code=filter_code,
            sort=sort,
            order=order,
            pages=pages,
            retries=retries,
            timeout=cfg.timeout,
            exhaustive=exhaustive,
        )
        stored = _remember(
            cache_key,
            entries,
            query=query,
            category=category,
            filter_code=filter_code,
            pages=pages,
            exhaustive=exhaustive,
            sort=sort,
            order=order,
            complete=complete,
        )
        future.set_result(stored)
        return stored
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
        raise
    finally:
        _release_inflight(cache_key, future)
