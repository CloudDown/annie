"""Client de recherche Nyaa.si."""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

NYAA_BASE = "https://nyaa.si"
USER_AGENT = "Annie/0.2 (+https://github.com/local/annie)"
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


def search(
    query: str,
    *,
    category: str = "0_0",
    filter_code: str = "0",
    sort: str = "seeders",
    order: str = "desc",
) -> list[NyaaEntry]:
    params = urllib.parse.urlencode(
        {
            "f": filter_code,
            "c": category,
            "q": query,
            "s": sort,
            "o": order,
        }
    )
    url = f"{NYAA_BASE}/?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", errors="replace")

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
