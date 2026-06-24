"""Titres, tri, catalogue Nyaa."""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from annie.nyaa import NYAA_PARALLEL, NyaaEntry

MAX_FRANCHISE_QUERIES = 20

CONFIG_DIR = Path.home() / ".config" / "annie"
CONFIG_FILE = CONFIG_DIR / "config.toml"
_config_cache: AnnieConfig | None = None


@dataclass
class AnnieConfig:
    player: str = "auto"
    category: str = "0_0"
    filter_code: str = "0"
    skip_recap_movies: bool = False
    preferred_groups: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> AnnieConfig:
        global _config_cache
        if _config_cache is not None:
            return replace(_config_cache)
        data: dict = {}
        if CONFIG_FILE.is_file():
            data = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        _config_cache = cls(
            player=os.environ.get("ANNIE_PLAYER", data.get("player", "auto")),
            category=data.get("category", "0_0"),
            filter_code=str(data.get("filter", data.get("filter_code", "0"))),
            skip_recap_movies=bool(data.get("skip_recap_movies", False)),
            preferred_groups=list(data.get("preferred_groups", [])),
        )
        return replace(_config_cache)

    def resolved_player(self, override: str | None = None) -> str | None:
        if override and override != "auto":
            return override
        if self.player != "auto":
            return self.player
        return None


class MediaKind(str, Enum):
    EPISODE = "episode"
    MOVIE = "movie"
    OVA = "ova"
    SPECIAL = "special"
    BATCH = "batch"
    MANGA = "manga"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedTitle:
    raw: str
    release_group: str | None
    series: str
    display_name: str
    kind: MediaKind
    season: int | None
    episode: int | None
    arc: str | None
    quality: int
    resolution: str | None
    is_repack: bool


@dataclass(frozen=True)
class WatchTarget:
    query: str
    season: int | None = None
    episode: int | None = None
    kind: MediaKind | None = None


@dataclass(frozen=True)
class ResultItem:
    entry: NyaaEntry
    parsed: ParsedTitle
    score: float


@dataclass
class MediaSection:
    key: str
    label: str
    kind: MediaKind
    season: int | None
    arc: str | None = None
    batch_recommended: bool = False
    expected_episodes: int | None = None
    mal_id: int | None = None
    episodes: dict[int, ResultItem] = field(default_factory=dict)
    singles: list[ResultItem] = field(default_factory=list)

    @property
    def has_episodes(self) -> bool:
        return bool(self.episodes)

    def choices(self) -> list[ResultItem]:
        if self.has_episodes:
            return [self.episodes[n] for n in sorted(self.episodes)]
        return sorted(self.singles, key=lambda item: item.score, reverse=True)


@dataclass(frozen=True)
class MalRelease:
    mal_id: int
    label: str
    kind: MediaKind
    season: int | None
    episode_count: int | None
    nyaa_queries: list[str]
    sort_key: tuple[int, str]

import re


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

import re

RESOLUTION_SCORES = (
    (re.compile(r"\b2160p\b|\b4k\b", re.I), 45),
    (re.compile(r"\b1080p\b", re.I), 38),
    (re.compile(r"\b720p\b", re.I), 26),
    (re.compile(r"\b480p\b", re.I), 12),
)
SOURCE_SCORES = (
    (re.compile(r"\bbluray\b|\bbd\b|\bblu-?ray\b", re.I), 24),
    (re.compile(r"\bweb-?dl\b", re.I), 20),
    (re.compile(r"\bwebrip\b", re.I), 14),
    (re.compile(r"\bhdtv\b", re.I), 6),
)
CODEC_SCORES = (
    (re.compile(r"\bhevc\b|\bx265\b|\bh\.265\b", re.I), 8),
    (re.compile(r"\bavc\b|\bx264\b|\bh\.264\b", re.I), 10),
)
PREFERRED_GROUPS = {
    "subsplease": 14,
    "erai-raws": 12,
    "toonhub": 9,
    "sam": 8,
    "judas": 7,
    "ember": 6,
}


def resolution_tag(title: str) -> str | None:
    for pattern, _ in RESOLUTION_SCORES:
        match = pattern.search(title)
        if match:
            return match.group(0).lower().replace("4k", "2160p")
    return None


def quality_score(title: str, release_group: str | None) -> int:
    score = 0
    for pattern, points in RESOLUTION_SCORES:
        if pattern.search(title):
            score += points
            break
    for pattern, points in SOURCE_SCORES:
        if pattern.search(title):
            score += points
            break
    for pattern, points in CODEC_SCORES:
        if pattern.search(title):
            score += points
            break
    if release_group:
        score += PREFERRED_GROUPS.get(release_group.lower(), 0)
    if re.search(r"\brepack\b", title, re.I):
        score -= 5
    if re.search(r"\bdual[\s-]?audio\b", title, re.I):
        score += 2
    return score

import re

MANGA_KEYWORDS = (
    re.compile(r"\bmanga\b", re.I),
    re.compile(r"\bmanhwa\b", re.I),
    re.compile(r"\bmanhua\b", re.I),
    re.compile(r"\bcomic\b", re.I),
    re.compile(r"\bdoujin(?:shi)?\b", re.I),
    re.compile(r"\bchapter\b", re.I),
    re.compile(r"\bch\.?\s*\d+\b", re.I),
    re.compile(r"\bvolume\b", re.I),
    re.compile(r"\bvol\.?\s*\d+\b", re.I),
)
MANGA_VOLUME_RANGE_RE = re.compile(
    r"\bv(?:ol(?:ume)?\.?\s*)?(?P<a>\d{1,2})\s*[-–—]\s*v?(?P<b>\d{1,2})\b",
    re.I,
)
MANGA_DIGITAL_RE = re.compile(
    r"\((?:digital|web)\)|\bdigital\s+(?:comic|release|edition)\b",
    re.I,
)
MANGA_EXT_RE = re.compile(r"\.(?:cbz|cbr|pdf|zip)(?:\s|\]|$)", re.I)
MANGA_SCAN_GROUPS = frozenset(
    {
        "danke-empire",
        "lucaz",
        "1r0n",
        "nif",
        "stick",
        "rascal",
        "philia",
    }
)


def is_manga(title: str, release_group: str | None = None) -> bool:
    if MANGA_EXT_RE.search(title):
        return True
    if any(pattern.search(title) for pattern in MANGA_KEYWORDS):
        return True
    if MANGA_DIGITAL_RE.search(title) and not re.search(
        r"\b(?:mkv|mp4|avi|webm|m4v|mov|ts)\b",
        title,
        re.I,
    ):
        return True
    volume_match = MANGA_VOLUME_RANGE_RE.search(title)
    if volume_match and MANGA_DIGITAL_RE.search(title):
        return True
    if volume_match and not re.search(r"\bS\d{1,2}E\d{1,3}\b", title, re.I):
        if not re.search(r"[-–—]\s*\d{1,2}(?:v\d+)?(?:\s|\[|\(|$)", title):
            return True
    if release_group and release_group.lower() in MANGA_SCAN_GROUPS and MANGA_DIGITAL_RE.search(title):
        return True
    return False

ARC_SEASON_ALIASES: dict[str, int] = {
    "retsujitsu no ougonkyou": 2,
    "the golden city of the scorching sun": 2,
    "golden city of the scorching sun": 2,
}


def arc_to_season(arc: str | None) -> int | None:
    if not arc:
        return None
    return ARC_SEASON_ALIASES.get(normalize(arc))

import re


RELEASE_GROUP_RE = re.compile(r"^\[([^\]]+)\]\s*")
TECH_BRACKET_RE = re.compile(
    r"\[(?:1080p|720p|480p|2160p|4K|HEVC|H\.264|H\.265|x264|x265|AAC|AC3|"
    r"Multi-?Sub(?:s)?|Batch|Weekly|REPACK|v\d+)\]",
    re.I,
)
MOVIE_PATTERNS = (
    re.compile(r"\bgekijouban\b", re.I),
    re.compile(r"\bthe\s+movie\b", re.I),
    re.compile(r"\bmovie(?:\s*\d+)?\b", re.I),
    re.compile(r"\bfilm\b", re.I),
    re.compile(r"\b(?:movie|film)\s*:", re.I),
    re.compile(r"\bdawn\s+of\s+the\s+deep\s+soul\b", re.I),
    re.compile(r"\bfukaki\s+tamashii\s+no\s+reimei\b", re.I),
    re.compile(r"\btabidachi\s+no\s+yoake\b", re.I),
    re.compile(r"\bhourou\s+suru\s+tasogare\b", re.I),
    re.compile(r"\bjourney'?s\s+dawn\b", re.I),
    re.compile(r"\bwandering\s+twilight\b", re.I),
)
MANGA_VOLUME_IN_TITLE_RE = re.compile(
    r"\bv(?:ol(?:ume)?\.?\s*)?\d{1,2}\s*[-–—]\s*v?\d{1,2}\b",
    re.I,
)
OVA_PATTERNS = (
    re.compile(r"\bova\b", re.I),
    re.compile(r"\boad\b", re.I),
)
SPECIAL_PATTERNS = (
    re.compile(r"\bspecial\b", re.I),
    re.compile(r"\btv\s+special\b", re.I),
    re.compile(r"\b(?:^|\s)sp(?:\s|$|\d)", re.I),
)
BATCH_PATTERNS = (
    re.compile(r"\bbatch\b", re.I),
    re.compile(r"\bcomplete\b", re.I),
    re.compile(r"\b\d{1,3}\s*-\s*\d{1,3}\b"),
    re.compile(r"\b\d{1,3}\s*[~]\s*\d{1,3}\b"),
    re.compile(r"\bS\d{1,2}E\d{1,3}\s*-\s*S?\d{1,2}E\d{1,3}\b", re.I),
)
VIDEO_EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|webm|m4v|mov)\b", re.I)
HASH_BRACKET_RE = re.compile(r"\[[0-9A-Fa-f]{6,}\]")
SEASON_EP_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b", re.I)
ORDINAL_SEASON_RE = re.compile(r"(?P<season>\d)(?:st|nd|rd|th)\s+Season", re.I)
SEASON_WORD_RE = re.compile(r"\bSeason\s*(?P<season>\d+)\b", re.I)
SEASON_SHORT_RE = re.compile(r"(?<![A-Za-z0-9])S(?P<season>\d{1,2})(?!E\d)", re.I)
PART_RE = re.compile(r"\bPart\s*(?P<season>\d+)\b", re.I)
COUR_RE = re.compile(r"\bCour\s*(?P<season>\d+)\b", re.I)
EP_DASH_RE = re.compile(
    r"[-–—]\s*(?P<episode>\d{1,3})(?:v\d+)?(?:\s|\[|\(|$)",
    re.I,
)
EP_WORD_RE = re.compile(r"\b(?:Episode|EP)\s*(?P<episode>\d{1,3})\b", re.I)
ARC_EP_RE = re.compile(
    r"^(?P<series>.+?)\s+[-–—]\s+(?P<arc>.+?)\s+[-–—]\s+(?P<episode>\d{1,3})(?:v\d+)?(?:\s|\[|\(|$)",
    re.I,
)


def strip_release_group(title: str) -> tuple[str | None, str]:
    match = RELEASE_GROUP_RE.match(title)
    if not match:
        return None, title
    return match.group(1), title[match.end() :].strip()




def matches_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def parse_season(body: str) -> int | None:
    for pattern in (
        ORDINAL_SEASON_RE,
        SEASON_WORD_RE,
        PART_RE,
        COUR_RE,
        SEASON_SHORT_RE,
    ):
        match = pattern.search(body)
        if match:
            return int(match.group("season"))
    return None


def parse_episode(body: str) -> int | None:
    if MANGA_VOLUME_IN_TITLE_RE.search(body):
        return None
    match = EP_DASH_RE.search(body) or EP_WORD_RE.search(body)
    if match:
        return int(match.group("episode"))
    return None


def extract_arc(body: str) -> tuple[str | None, str | None, int | None]:
    match = ARC_EP_RE.match(body)
    if not match:
        return None, None, None
    arc = match.group("arc").strip()
    if matches_any(MOVIE_PATTERNS, arc) or matches_any(BATCH_PATTERNS, arc):
        return None, None, None
    return match.group("series").strip(), arc, int(match.group("episode"))


def extract_display_name(body: str) -> str:
    series, arc, _episode = extract_arc(body)
    if series and arc:
        return f"{clean_fragment(series)} - {clean_fragment(arc)}"

    cleaned = TECH_BRACKET_RE.sub(" ", body)
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)
    cleaned = re.sub(r"[-–—]\s*\d{1,3}(?:v\d+)?.*$", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:S\d{1,2}E\d{1,3}|Season\s*\d+|S\d{1,2}|Part\s*\d+|Cour\s*\d+)\b",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = clean_fragment(cleaned)
    return cleaned or clean_fragment(body)


def clean_fragment(text: str) -> str:
    text = VIDEO_EXT_RE.sub("", text)
    text = HASH_BRACKET_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:.")
    return text


def series_key(body: str, display_name: str) -> str:
    return normalize(display_name or body)


def detect_kind(body: str, season: int | None, episode: int | None, arc: str | None) -> MediaKind:
    if matches_any(MOVIE_PATTERNS, body):
        return MediaKind.MOVIE
    if matches_any(BATCH_PATTERNS, body):
        return MediaKind.BATCH
    if matches_any(OVA_PATTERNS, body):
        return MediaKind.OVA
    if matches_any(SPECIAL_PATTERNS, body):
        return MediaKind.SPECIAL
    if episode is not None or SEASON_EP_RE.search(body):
        return MediaKind.EPISODE
    if arc:
        return MediaKind.EPISODE
    return MediaKind.UNKNOWN


def finalize_parsed(
    *,
    title: str,
    release_group: str | None,
    body: str,
    kind: MediaKind,
    season: int | None,
    episode: int | None,
    arc: str | None,
) -> ParsedTitle:
    if arc and season is None:
        mapped_season = arc_to_season(arc)
        if mapped_season is not None:
            season = mapped_season
            arc = None

    display_name = extract_display_name(body)
    resolution = resolution_tag(title)
    return ParsedTitle(
        raw=title,
        release_group=release_group,
        series=series_key(body, display_name),
        display_name=display_name,
        kind=kind,
        season=season,
        episode=episode,
        arc=arc,
        quality=quality_score(title, release_group),
        resolution=resolution,
        is_repack=bool(re.search(r"\brepack\b", title, re.I)),
    )


def parse_title(title: str) -> ParsedTitle:
    release_group, body = strip_release_group(title)
    if is_manga(title, release_group):
        return finalize_parsed(
            title=title,
            release_group=release_group,
            body=body,
            kind=MediaKind.MANGA,
            season=None,
            episode=None,
            arc=None,
        )

    season: int | None = None
    episode: int | None = None
    arc: str | None = None

    if matches_any(MOVIE_PATTERNS, body):
        kind = MediaKind.MOVIE
    elif matches_any(BATCH_PATTERNS, body):
        kind = MediaKind.BATCH
        season = parse_season(body)
    else:
        season_ep = SEASON_EP_RE.search(body)
        if season_ep:
            season = int(season_ep.group("season"))
            episode = int(season_ep.group("episode"))
        else:
            arc_series, arc_name, arc_episode = extract_arc(body)
            if arc_name:
                arc = clean_fragment(arc_name)
                episode = arc_episode
                body_for_season = arc_series or body
            else:
                body_for_season = body
                episode = parse_episode(body)
            season = parse_season(body_for_season)
        kind = detect_kind(body, season, episode, arc)
        if kind == MediaKind.EPISODE and season is None and arc is None and episode is not None:
            season = 1

    return finalize_parsed(
        title=title,
        release_group=release_group,
        body=body,
        kind=kind,
        season=season,
        episode=episode,
        arc=arc,
    )

import re
from pathlib import Path


INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_name(text: str, limit: int = 120) -> str:
    cleaned = INVALID_CHARS.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:limit] or "anime"


def minimal_label(parsed: ParsedTitle) -> str:
    name = sanitize_name(parsed.display_name)
    quality = f" [{parsed.resolution}]" if parsed.resolution else ""

    if parsed.kind == MediaKind.EPISODE:
        season = parsed.season or 1
        episode = parsed.episode or 0
        if parsed.arc and parsed.season is None:
            return f"{name} E{episode:02d}{quality}"
        return f"{name} S{season:02d}E{episode:02d}{quality}"

    if parsed.kind == MediaKind.MOVIE:
        if re.search(r"\bmovie\b", name, re.I):
            return f"{name}{quality}"
        return f"{name} Movie{quality}"

    if parsed.kind == MediaKind.OVA:
        if parsed.episode is not None:
            return f"{name} OVA {parsed.episode:02d}{quality}"
        return f"{name} OVA{quality}"

    if parsed.kind == MediaKind.SPECIAL:
        if parsed.episode is not None:
            return f"{name} Special {parsed.episode:02d}{quality}"
        return f"{name} Special{quality}"

    if parsed.kind == MediaKind.BATCH:
        if parsed.episode is not None:
            season = parsed.season or 1
            return f"{name} S{season:02d}E{parsed.episode:02d} (batch){quality}"
        if parsed.season is not None:
            return f"{name} Batch S{parsed.season:02d}{quality}"
        return f"{name} Batch{quality}"

    return f"{name}{quality}"


def minimal_filename(parsed: ParsedTitle, source_name: str | None = None) -> str:
    ext = Path(source_name or parsed.raw).suffix.lower()
    if ext not in {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov"}:
        ext = ".mkv"
    return f"{minimal_label(parsed)}{ext}"

import re



def query_tokens(query: str) -> list[str]:
    return [token for token in normalize(query).split() if len(token) > 1]


def series_match_score(parsed: ParsedTitle, query: str) -> int:
    tokens = query_tokens(query)
    if not tokens:
        return 0

    haystacks = {
        parsed.series,
        normalize(parsed.display_name),
        normalize(parsed.raw),
    }
    hits = 0
    for token in tokens:
        if any(token in hay for hay in haystacks):
            hits += 1

    if hits == 0:
        return -1000
    return hits * 120 + (180 if hits == len(tokens) else 0)


def best_series_match_score(parsed: ParsedTitle, queries: list[str]) -> int:
    if not queries:
        return 0
    return max(series_match_score(parsed, query) for query in queries)


def _resolved_season(parsed: ParsedTitle) -> int | None:
    if parsed.season is not None:
        return parsed.season
    if parsed.kind == MediaKind.EPISODE and parsed.arc is None:
        return 1
    return None


def target_match_score(
    parsed: ParsedTitle,
    target: WatchTarget,
    *,
    match_queries: list[str] | None = None,
) -> int | None:
    queries = match_queries or [target.query]
    score = best_series_match_score(parsed, queries)
    if score < 0:
        return None

    if target.kind is not None and parsed.kind != target.kind:
        if not (target.kind == MediaKind.EPISODE and parsed.kind == MediaKind.BATCH):
            return None

    if target.season is not None:
        season = _resolved_season(parsed)
        if season != target.season and not (parsed.arc and parsed.season is None):
            return None
        score += 150

    if target.episode is not None:
        if parsed.kind == MediaKind.BATCH:
            score += 40
        elif parsed.episode == target.episode:
            score += 220
        else:
            return None

    if parsed.kind == MediaKind.MOVIE:
        score += 80
    elif parsed.kind == MediaKind.OVA:
        score += 60
    elif parsed.kind == MediaKind.SPECIAL:
        score += 50

    return score


def same_section(a: ParsedTitle, b: ParsedTitle) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == MediaKind.EPISODE:
        if a.arc and b.arc:
            return normalize(a.arc) == normalize(b.arc)
        if a.arc or b.arc:
            return False
        return _resolved_season(a) == _resolved_season(b)
    return True

import math



CRC_TAG_RE = re.compile(r"\[[0-9A-F]{8}\]", re.I)


def _filename_for_episode_match(name: str) -> str:
    return CRC_TAG_RE.sub("", Path(name).name)


def match_episode_filename(name: str, episode: int) -> bool:
    """Match episode number in fansub filenames, ignoring CRC/hash tags."""
    stem = _filename_for_episode_match(name)
    patterns = (
        rf"[\s\-]0?{episode}(?:v\d+)?(?=\s|\[|\.|$)",
        rf"[Ee]0?{episode}\b",
        rf"[Ss]\d+[Ee]0?{episode}\b",
    )
    return any(re.search(pattern, stem, re.I) for pattern in patterns)


def episode_file_query(episode: int) -> str:
    return rf"(?:[\s\-]0?{episode}(?:v\d+)?(?=\s|\[)|[Ee]0?{episode}\b|[Ss]\d+[Ee]0?{episode}\b)"


def rank_entry(
    entry: NyaaEntry,
    target: WatchTarget,
    *,
    match_queries: list[str] | None = None,
) -> tuple[float, ParsedTitle] | None:
    if is_manga(entry.title):
        return None
    parsed = parse_title(entry.title)
    if parsed.kind == MediaKind.MANGA:
        return None
    title_score = target_match_score(parsed, target, match_queries=match_queries)
    if title_score is None:
        return None

    seed_score = math.log1p(entry.seeders) * 20
    quality = parsed.quality
    trusted_bonus = 8 if entry.trusted else 0
    batch_penalty = -30 if target.episode is not None and parsed.kind == MediaKind.BATCH else 0
    repack_penalty = -8 if parsed.is_repack else 0
    unknown_penalty = -40 if parsed.kind == MediaKind.UNKNOWN else 0

    total = title_score * 1000 + seed_score + quality + trusted_bonus + batch_penalty + repack_penalty + unknown_penalty
    return total, parsed


def pick_best(entries: list[NyaaEntry], target: WatchTarget) -> tuple[NyaaEntry, ParsedTitle] | None:
    ranked: list[tuple[float, NyaaEntry, ParsedTitle]] = []
    for entry in entries:
        result = rank_entry(entry, target)
        if result is None:
            continue
        score, parsed = result
        ranked.append((score, entry, parsed))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, best, parsed = ranked[0]
    return best, parsed

import re


RECAP_MOVIE_PATTERNS = (
    re.compile(r"\bjourney'?s\s+dawn\b", re.I),
    re.compile(r"\bwandering\s+twilight\b", re.I),
    re.compile(r"\btabidachi\s+no\s+yoake\b", re.I),
    re.compile(r"\bhourou\s+suru\s+tasogare\b", re.I),
)


def section_key(parsed) -> str:
    if parsed.kind == MediaKind.EPISODE:
        if parsed.arc:
            return f"arc:{parsed.arc.lower()}"
        season = parsed.season or 1
        return f"season:{season:02d}"
    return f"{parsed.kind.value}:main"


def section_label(parsed) -> str:
    if parsed.kind == MediaKind.EPISODE:
        if parsed.arc:
            return parsed.arc
        season = parsed.season or 1
        return f"Season {season:02d}"
    labels = {
        MediaKind.MOVIE: "Movies",
        MediaKind.OVA: "OVA",
        MediaKind.SPECIAL: "Specials",
        MediaKind.BATCH: "Batch",
        MediaKind.UNKNOWN: "Other",
    }
    return labels.get(parsed.kind, parsed.kind.value)


def section_sort_key(section: MediaSection) -> tuple[int, int, str]:
    if section.kind == MediaKind.EPISODE:
        season = section.season or 1
        return (season * 10, 0, section.label.lower())
    slot = {
        MediaKind.MOVIE: 15,
        MediaKind.OVA: 25,
        MediaKind.SPECIAL: 26,
        MediaKind.BATCH: 30,
        MediaKind.UNKNOWN: 40,
    }
    base = slot.get(section.kind, 50)
    if section.kind == MediaKind.MOVIE:
        base = 15 if (section.season or 1) <= 1 else 25
    return (base, 0, section.label.lower())


def is_recap_movie(title: str) -> bool:
    return any(pattern.search(title) for pattern in RECAP_MOVIE_PATTERNS)


def upsert_episode(section: MediaSection, item: ResultItem) -> None:
    episode = item.parsed.episode
    if episode is None:
        section.singles.append(item)
        return
    current = section.episodes.get(episode)
    if current is None or item.score > current.score:
        section.episodes[episode] = item


def upsert_single(section: MediaSection, item: ResultItem) -> None:
    for index, current in enumerate(section.singles):
        if minimal_label(current.parsed) == minimal_label(item.parsed):
            if item.score > current.score:
                section.singles[index] = item
            return
    section.singles.append(item)


BATCH_EP_RANGE_RE = re.compile(
    r"(?:\(|\[|\s|-)(?P<a>\d{1,3})\s*[-–—~]\s*(?P<b>\d{1,3})(?:\)|\]|\s|\[|\(|$)",
    re.I,
)
SE_BATCH_RANGE_RE = re.compile(
    r"\bS(?P<s>\d{1,2})E(?P<a>\d{1,3})\s*[-–—~]\s*S?\d{0,2}E?(?P<b>\d{1,3})\b",
    re.I,
)
MOVIE_NOISE_RE = re.compile(
    r"\b(?:ost|soundtrack|discography|\bcd\b|\bmp3\b)\b",
    re.I,
)
MOVIE_PACK_RE = re.compile(
    r"\b(?:TV\s*\+\s*MOVIE|\+ MOVIE|\+ Movies|Season\s*\d+\s*\+\s*Season)\b",
    re.I,
)
MOVIE_ID_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmovie\s*1\b|\bjourney'?s\s+dawn\b|\btabidachi\s+no\s+yoake\b", re.I), "movie-1"),
    (re.compile(r"\bmovie\s*2\b|\bwandering\s+twilight\b|\bhourou\s+suru\s+tasogare\b", re.I), "movie-2"),
    (re.compile(r"\bmovie\s*3\b|\bdawn\s+of\s+the\s+deep\s+soul\b|\bfukaki\s+tamashii\b", re.I), "movie-3"),
)


def is_movie_noise(title: str) -> bool:
    if MOVIE_PACK_RE.search(title):
        return True
    if VIDEO_EXT_RE.search(title):
        return False
    if MOVIE_NOISE_RE.search(title):
        return True
    if re.search(r"\b(?:ost|soundtrack)\b", title, re.I):
        return True
    return False


def movie_canonical_key(title: str, parsed: ParsedTitle) -> str:
    hay = normalize(f"{title} {parsed.display_name}")
    for pattern, key in MOVIE_ID_PATTERNS:
        if pattern.search(hay):
            return key
    cleaned = normalize(parsed.display_name)
    cleaned = re.sub(r"\bmovie\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or normalize(title)


def infer_batch_season(body: str, season: int | None) -> int:
    if season is not None:
        return season
    normalized_body = normalize(body)
    for arc_name, mapped in ARC_SEASON_ALIASES.items():
        if arc_name in normalized_body:
            return mapped
    return 1


def parse_batch_episode_range(title: str) -> tuple[int | None, list[int]]:
    _, body = strip_release_group(title)
    season = parse_season(body)

    se_match = SE_BATCH_RANGE_RE.search(body)
    if se_match:
        season = infer_batch_season(body, int(se_match.group("s")))
        start, end = int(se_match.group("a")), int(se_match.group("b"))
        if end >= start and end - start < 60:
            return season, list(range(start, end + 1))

    for match in BATCH_EP_RANGE_RE.finditer(body):
        start, end = int(match.group("a")), int(match.group("b"))
        if end < start or end - start >= 60:
            continue
        if start == 0 or end == 0:
            continue
        return infer_batch_season(body, season), list(range(start, end + 1))

    return season, []


def item_for_episode(item: ResultItem, episode: int) -> ResultItem:
    parsed = item.parsed
    if parsed.episode == episode and parsed.kind == MediaKind.EPISODE:
        return item
    new_parsed = replace(
        parsed,
        episode=episode,
        season=parsed.season or infer_batch_season(item.entry.title, parsed.season),
    )
    penalty = 5.0 if parsed.kind == MediaKind.BATCH else 0.0
    return ResultItem(entry=item.entry, parsed=new_parsed, score=item.score - penalty)


def upsert_movie(section: MediaSection, item: ResultItem) -> None:
    if is_movie_noise(item.entry.title):
        return
    key = movie_canonical_key(item.entry.title, item.parsed)
    for index, current in enumerate(section.singles):
        if movie_canonical_key(current.entry.title, current.parsed) == key:
            if item.score > current.score:
                section.singles[index] = item
            return
    section.singles.append(item)


def apply_batch_episodes(sections: dict[str, MediaSection], batches: list[ResultItem]) -> None:
    for item in batches:
        season, episodes = parse_batch_episode_range(item.entry.title)
        if not episodes:
            continue
        key = f"season:{season:02d}"
        section = sections.get(key)
        if section is None:
            section = MediaSection(
                key=key,
                label=f"Season {season:02d}",
                kind=MediaKind.EPISODE,
                season=season,
            )
            sections[key] = section
        for episode in episodes:
            upsert_episode(section, item_for_episode(item, episode))


def _strict_target(
    query: str,
    *,
    season: int | None,
    episode: int | None,
    kind: MediaKind | None,
) -> WatchTarget | None:
    if season is None and episode is None and kind is None:
        return None
    resolved_kind = kind
    if resolved_kind is None and episode is not None:
        resolved_kind = MediaKind.EPISODE
    return WatchTarget(query=query, season=season, episode=episode, kind=resolved_kind)


def _annotate_batch_hints(sections: list[MediaSection]) -> None:
    episode_sections = [section for section in sections if section.kind == MediaKind.EPISODE]
    batch_sections = [section for section in sections if section.kind == MediaKind.BATCH]
    for section in episode_sections:
        if not section.has_episodes:
            continue
        sparse = len(section.episodes) <= 3
        has_batch = any(
            batch.season == section.season or batch.season in {None, section.season}
            for batch in batch_sections
        )
        section.batch_recommended = sparse and has_batch


def build_catalog(
    entries: list[NyaaEntry],
    query: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    kind: MediaKind | None = None,
    skip_recap_movies: bool = False,
    match_queries: list[str] | None = None,
) -> list[MediaSection]:
    loose_target = WatchTarget(query=query)
    strict_target = _strict_target(query, season=season, episode=episode, kind=kind)
    queries = list(dict.fromkeys([query, *(match_queries or [])]))
    sections: dict[str, MediaSection] = {}
    batches: list[ResultItem] = []

    for entry in entries:
        if is_manga(entry.title):
            continue
        if skip_recap_movies and is_recap_movie(entry.title):
            continue
        ranked = rank_entry(entry, loose_target, match_queries=queries)
        if ranked is None:
            continue
        score, parsed = ranked
        if strict_target is not None and target_match_score(parsed, strict_target, match_queries=queries) is None:
            continue

        item = ResultItem(entry=entry, parsed=parsed, score=score)
        key = section_key(parsed)
        section = sections.get(key)
        if section is None:
            section = MediaSection(
                key=key,
                label=section_label(parsed),
                kind=parsed.kind,
                season=parsed.season,
                arc=parsed.arc,
            )
            sections[key] = section

        if parsed.kind == MediaKind.EPISODE and parsed.episode is not None:
            upsert_episode(section, item)
        elif parsed.kind == MediaKind.MOVIE:
            upsert_movie(section, item)
        elif parsed.kind == MediaKind.BATCH:
            batches.append(item)
            upsert_single(section, item)
        else:
            upsert_single(section, item)

    apply_batch_episodes(sections, batches)

    result = [section for section in sections.values() if section.episodes or section.singles]
    result.sort(key=section_sort_key)
    _annotate_batch_hints(result)
    return result


def find_section(
    sections: list[MediaSection],
    season: int | None,
    kind: MediaKind | None,
) -> MediaSection | None:
    for section in sections:
        if kind is not None and section.kind != kind:
            continue
        if season is None:
            return section
        if section.season == season:
            return section
    return None


def _pick_section_for_release(parts: list[MediaSection], release: MalRelease) -> MediaSection | None:
    if not parts:
        return None

    if release.kind == MediaKind.EPISODE and release.season is not None:
        for section in parts:
            if section.kind == MediaKind.EPISODE and section.season == release.season:
                return section
        episode_sections = [section for section in parts if section.has_episodes]
        if episode_sections:
            return max(episode_sections, key=lambda section: len(section.episodes))

    if release.kind == MediaKind.MOVIE:
        movie_sections = [section for section in parts if section.kind == MediaKind.MOVIE]
        if movie_sections:
            return max(movie_sections, key=lambda section: len(section.singles))
        singles = [section for section in parts if section.singles]
        if singles:
            return max(singles, key=lambda section: len(section.singles))

    if release.kind in {MediaKind.OVA, MediaKind.SPECIAL}:
        for section in parts:
            if section.kind == release.kind:
                return section

    return max(parts, key=lambda section: len(section.episodes) + len(section.singles))


def normalize_section_episodes(section: MediaSection, expected: int | None) -> None:
    """Map absolute cours numbering (e.g. E29–E38 → S2E01–E10)."""
    if not expected or not section.episodes:
        return
    nums = sorted(section.episodes.keys())
    if not nums or max(nums) <= expected:
        return

    remapped: dict[int, ResultItem] = {
        ep: item for ep, item in section.episodes.items() if ep <= expected
    }
    high = [n for n in nums if n > expected]
    if not high:
        section.episodes = remapped
        return

    offset = min(high) - 1
    if offset < 1:
        section.episodes = remapped
        return

    for ep in high:
        rel = ep - offset
        if not (1 <= rel <= expected):
            continue
        item = item_for_episode(section.episodes[ep], rel)
        current = remapped.get(rel)
        if current is None or item.score > current.score:
            remapped[rel] = item

    section.episodes = remapped


def _safe_search(
    query: str,
    search,
    *,
    category: str,
    filter_code: str,
    pages: int | None = None,
) -> list[NyaaEntry]:
    try:
        kwargs: dict = {"category": category, "filter_code": filter_code}
        if pages is not None:
            kwargs["pages"] = pages
        return search(query, **kwargs)
    except Exception:
        return []


def _gap_search_queries(release: MalRelease, missing: list[int]) -> list[str]:
    queries: list[str] = []
    shorts: list[str] = []
    for base in release.nyaa_queries:
        if not base:
            continue
        short = base.split(":", 1)[0].strip()
        if short and short not in shorts:
            shorts.append(short)
    for ep in missing:
        for short in shorts[:2]:
            variant = f"{short} {ep:02d}"
            if variant not in queries:
                queries.append(variant)
    return queries[:24]


def _fill_missing_episodes(
    release: MalRelease,
    section: MediaSection,
    *,
    search,
    category: str,
    filter_code: str,
    skip_recap_movies: bool,
    pool: ThreadPoolExecutor | None,
) -> None:
    expected = release.episode_count
    if not expected or release.kind != MediaKind.EPISODE:
        return
    missing = [ep for ep in range(1, expected + 1) if ep not in section.episodes]
    if not missing:
        return

    gap_queries = _gap_search_queries(release, missing)
    if not gap_queries:
        return

    gap_entries: list[NyaaEntry] = []
    seen = {item.entry.magnet for item in section.choices()}

    def _fetch_gap(query: str) -> list[NyaaEntry]:
        return _safe_search(
            query,
            search,
            category=category,
            filter_code=filter_code,
            pages=2,
        )

    if pool is None:
        for query in gap_queries:
            gap_entries.extend(_fetch_gap(query))
    else:
        futures = [pool.submit(_fetch_gap, query) for query in gap_queries]
        for future in as_completed(futures):
            gap_entries.extend(future.result())

    gap_entries = [entry for entry in gap_entries if entry.magnet not in seen]
    if not gap_entries:
        return

    primary_query = release.nyaa_queries[0] if release.nyaa_queries else ""
    parts = build_catalog(
        gap_entries,
        primary_query,
        skip_recap_movies=skip_recap_movies,
        match_queries=release.nyaa_queries,
    )
    extra = _pick_section_for_release(parts, release)
    if extra is None:
        return

    for ep, item in extra.episodes.items():
        current = section.episodes.get(ep)
        if current is None or item.score > current.score:
            section.episodes[ep] = item

    normalize_section_episodes(section, expected)


def build_catalog_from_releases(
    releases: list[MalRelease],
    *,
    search,
    category: str,
    filter_code: str,
    skip_recap_movies: bool = False,
    pool: ThreadPoolExecutor | None = None,
) -> list[MediaSection]:
    sections: list[MediaSection] = []
    seen_magnets: set[str] = set()
    workers = min(NYAA_PARALLEL, max(len(releases), 1))

    unique_queries: list[str] = []
    query_freq: dict[str, int] = {}
    for release in releases:
        for query in release.nyaa_queries:
            if not query:
                continue
            query_freq[query] = query_freq.get(query, 0) + 1

    ranked_queries = sorted(
        query_freq.keys(),
        key=lambda query: (-query_freq[query], len(query.split()), query.lower()),
    )
    for query in ranked_queries[:MAX_FRANCHISE_QUERIES]:
        unique_queries.append(query)

    query_entries: dict[str, list[NyaaEntry]] = {}

    def _fetch_all(executor: ThreadPoolExecutor) -> None:
        futures = {
            executor.submit(
                _safe_search,
                query,
                search,
                category=category,
                filter_code=filter_code,
            ): query
            for query in unique_queries
        }
        for future in as_completed(futures):
            query = futures[future]
            entries = future.result()
            if entries:
                query_entries[query] = entries

    if unique_queries:
        if pool is None:
            with ThreadPoolExecutor(max_workers=workers) as local_pool:
                _fetch_all(local_pool)
        else:
            _fetch_all(pool)

    for release in releases:
        merged: list[NyaaEntry] = []
        local_magnets: set[str] = set()
        source_queries = list(
            dict.fromkeys([*release.nyaa_queries, *unique_queries[:6]])
        )
        for query in source_queries:
            for entry in query_entries.get(query, []):
                if entry.magnet in seen_magnets or entry.magnet in local_magnets:
                    continue
                local_magnets.add(entry.magnet)
                merged.append(entry)
        if not merged:
            continue

        primary_query = release.nyaa_queries[0] if release.nyaa_queries else ""
        parts = build_catalog(
            merged,
            primary_query,
            skip_recap_movies=skip_recap_movies,
            match_queries=release.nyaa_queries,
        )
        section = _pick_section_for_release(parts, release)
        if section is None:
            continue

        normalize_section_episodes(section, release.episode_count)
        _fill_missing_episodes(
            release,
            section,
            search=search,
            category=category,
            filter_code=filter_code,
            skip_recap_movies=skip_recap_movies,
            pool=pool,
        )

        section.key = f"mal:{release.mal_id}"
        section.label = release.label
        section.kind = release.kind if release.kind != MediaKind.UNKNOWN else section.kind
        section.season = release.season
        section.expected_episodes = release.episode_count
        section.mal_id = release.mal_id

        for item in section.choices():
            seen_magnets.add(item.entry.magnet)

        if section.episodes or section.singles:
            sections.append(section)

    sections.sort(key=lambda section: section_sort_key(section))
    _annotate_batch_hints(sections)
    return sections
