"""Parsing des titres Nyaa."""

from __future__ import annotations

import re
from pathlib import Path

from annie.types import MediaKind, ParsedTitle, WatchTarget


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
    if (
        release_group
        and release_group.lower() in MANGA_SCAN_GROUPS
        and MANGA_DIGITAL_RE.search(title)
    ):
        return True
    return False


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
SPINOFF_PATTERNS = (
    re.compile(r"\bpetit\b", re.I),
    re.compile(r"\bbreak\s+time\b", re.I),
    re.compile(r"\bmini\s+anime\b", re.I),
    re.compile(r"\bpicture\s+drama\b", re.I),
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
ORDINAL_DASH_RE = re.compile(r"(?P<season>\d)(?:st|nd|rd|th)\s*[-–—]", re.I)
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
        ORDINAL_DASH_RE,
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


def detect_kind(
    body: str, season: int | None, episode: int | None, arc: str | None
) -> MediaKind:
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
        if (
            kind == MediaKind.EPISODE
            and season is None
            and arc is None
            and episode is not None
            and episode <= 25
        ):
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


def query_tokens(query: str) -> list[str]:
    return [token for token in normalize(query).split() if len(token) > 1]


def _token_matches(token: str, hay: str) -> bool:
    if len(token) < 2:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
    return bool(re.search(pattern, hay))


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
        if any(_token_matches(token, hay) for hay in haystacks):
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
        if parsed.episode is not None and parsed.episode > 25:
            return None
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


CRC_TAG_RE = re.compile(r"\[[0-9A-Fa-f]{8,16}\]")
_NON_EPISODE_FILE_RE = re.compile(
    r"\b(?:NCED|NCOP|NCEP|Credit|Menu|PV|CM|Preview|Trailer|Interview|Extra)\b",
    re.I,
)


def _filename_for_episode_match(name: str) -> str:
    return CRC_TAG_RE.sub("", Path(name).name)


def _contradicts_season(stem: str, season: int) -> bool:
    for match in re.finditer(r"\b[Ss]0?(\d+)[Ee]\d+", stem, re.I):
        if int(match.group(1)) != season:
            return True
    for match in re.finditer(r"\b[Ss]eason\s*0?(\d+)\b", stem, re.I):
        if int(match.group(1)) != season:
            return True
    if season == 1 and re.search(r"\bR2\b", stem, re.I):
        return True
    if (
        season == 2
        and re.search(r"\bR1\b", stem, re.I)
        and not re.search(r"\bR2\b", stem, re.I)
    ):
        return True
    return False


def _match_dash_episode(stem: str, episode: int) -> bool:
    quality = r"(?:\s*\([^)]+\))?"
    crc = r"(?:\s*\[[A-Fa-f0-9]+\])?"
    ext = r"\s*\.(?:mkv|mp4|avi|webm|m4v|mov)\b"
    patterns = (
        rf"[\s\-—–_]0?{episode}(?:v\d+)?{quality}?\s*\[",
        rf"[\s\-—–_]0?{episode}(?:v\d+)?{quality}?{crc}?{ext}",
        rf"[\s\-—–_]0?{episode}(?:v\d+)?{ext}",
    )
    return any(re.search(pattern, stem, re.I) for pattern in patterns)


def match_episode_filename(
    name: str, episode: int, *, season: int | None = None
) -> bool:
    """Match episode (and optional season) in fansub filenames, ignoring CRC/hash tags."""
    stem = _filename_for_episode_match(name)
    if _NON_EPISODE_FILE_RE.search(stem):
        return False
    if re.search(r"\bMovie\b", stem, re.I):
        return False
    if re.search(r"\bOVA\b", stem, re.I) or re.search(r"\bOVA\d", stem, re.I):
        return False

    if season is not None:
        strict_patterns = (
            rf"[Ss]{season:02d}[Ee]{episode:02d}\b",
            rf"[Ss]0?{season}[Ee]0?{episode}\b",
        )
        if any(re.search(pattern, stem, re.I) for pattern in strict_patterns):
            return True
        if _contradicts_season(stem, season):
            return False
        return _match_dash_episode(stem, episode)

    if re.search(rf"[Ss]\d+[Ee]0?{episode}\b", stem, re.I):
        return True
    return _match_dash_episode(stem, episode)


def episode_file_query(episode: int, *, season: int | None = None) -> str:
    if season is not None:
        return rf"[Ss]0?{season}[Ee]0?{episode}\b"
    return rf"(?:[Ss]\d+[Ee]0?{episode}\b|[Ee]0?{episode}\b|[\s\-_]0?{episode}(?:v\d+)?(?=\.mkv|\.mp4))"
