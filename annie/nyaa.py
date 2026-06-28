"""Client de recherche Nyaa.si."""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from annie.cache import read_json, write_json
from annie.net import fetch_text

NYAA_BASE = "https://nyaa.si"
USER_AGENT = "Annie/0.5 (+https://github.com/CloudDown/annie)"
NYAA_PARALLEL = 10
NYAA_SEARCH_PAGES = 2
NYAA_FAST_PAGES = NYAA_SEARCH_PAGES
DISK_CACHE_DIR = Path.home() / ".cache" / "annie" / "nyaa"
DISK_CACHE_TTL = 45 * 60

_search_cache: dict[tuple[str, str, str, str], tuple[float, list["NyaaEntry"]]] = {}
_nyaa_limiter: _TokenBucket | None = None

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


def _nyaa_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load().nyaa


def _disk_cache_ttl() -> int:
    return _nyaa_cfg().cache_ttl


def _get_limiter() -> _TokenBucket:
    global _nyaa_limiter
    if _nyaa_limiter is None:
        cfg = _nyaa_cfg()
        _nyaa_limiter = _TokenBucket(rate=cfg.rate, burst=cfg.rate_burst)
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
    query, category, filter_code, pages = cache_key
    safe = re.sub(
        r"[^\w\-.]+", "_", f"{query}-{category}-{filter_code}-p{pages}"
    ).strip("_")[:120]
    return DISK_CACHE_DIR / f"{safe}.json"


def _cache_key(
    query: str, category: str, filter_code: str, pages: int
) -> tuple[str, str, str, str]:
    return (query, category, filter_code, str(pages))


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


def search(
    query: str,
    *,
    category: str = "0_0",
    filter_code: str = "0",
    sort: str | None = None,
    order: str | None = None,
    pages: int | None = None,
    retries: int | None = None,
) -> list[NyaaEntry]:
    cfg = _nyaa_cfg()
    sort = sort if sort is not None else cfg.sort
    order = order if order is not None else cfg.order
    pages = pages if pages is not None else cfg.search_pages
    retries = retries if retries is not None else cfg.retries
    pages = max(1, pages)
    cache_key = _cache_key(query, category, filter_code, pages)
    cached = _cached_entries(cache_key)
    if cached is not None:
        return cached

    last_error: Exception | None = None
    merged: list[NyaaEntry] = []
    seen_magnets: set[str] = set()

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

        for attempt in range(retries):
            _get_limiter().acquire()
            try:
                html_page = fetch_text(url, user_agent=USER_AGENT, timeout=cfg.timeout)
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
            break

        if not page_entries:
            break

        for entry in page_entries:
            if entry.magnet in seen_magnets:
                continue
            seen_magnets.add(entry.magnet)
            merged.append(entry)

    return _store_entries(cache_key, merged)


def prefetch(
    queries: list[str],
    *,
    category: str = "0_0",
    filter_code: str = "0",
    pool=None,
) -> None:
    """Précharge des requêtes Nyaa uniques (no-op si déjà en cache)."""
    cfg = _nyaa_cfg()
    pages = cfg.search_pages
    parallel = cfg.parallel
    unique = [
        q
        for q in dict.fromkeys(queries)
        if q
        and _cached_entries(_cache_key(q, category, filter_code, pages))
        is None
    ]
    if not unique:
        return

    if pool is None:
        with ThreadPoolExecutor(max_workers=parallel) as local_pool:
            futures = [
                local_pool.submit(
                    search,
                    q,
                    category=category,
                    filter_code=filter_code,
                    pages=pages,
                )
                for q in unique
            ]
            wait(futures)
        return

    futures = [
        pool.submit(
            search, q, category=category, filter_code=filter_code, pages=pages
        )
        for q in unique
    ]
    wait(futures)
