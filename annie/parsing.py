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


def _merged_preferred_groups() -> dict[str, int]:
    from annie.config import AnnieConfig

    groups = dict(PREFERRED_GROUPS)
    cfg = AnnieConfig.load().catalog
    for name in cfg.preferred_groups:
        groups[name.lower()] = cfg.preferred_group_bonus
    return groups


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
        score += _merged_preferred_groups().get(release_group.lower(), 0)
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
    re.compile(r"\bhensh[uū]\b", re.I),
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
MANGA_CHAPTER_YEAR_RE = re.compile(r"\b\d{2,4}\s*\(20\d{2}\)")
MANGA_SCAN_GROUPS = frozenset(
    {
        "danke-empire",
        "lucaz",
        "1r0n",
        "nif",
        "stick",
        "rascal",
        "philia",
        "yen press",
    }
)


def is_manga(title: str, release_group: str | None = None) -> bool:
    if MANGA_CHAPTER_YEAR_RE.search(title) and not VIDEO_EXT_RE.search(title):
        return True
    if re.search(r"\bv\d{2}\b", title, re.I) and re.search(
        r"\b(?:yen press|digital|stick|1r0n)\b", title, re.I
    ):
        return True
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
    if volume_match and not VIDEO_EXT_RE.search(title):
        if not re.search(r"\bS\d{1,2}E\d{1,3}\b", title, re.I):
            return True
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
    re.compile(r"\bchronicle\b", re.I),
    re.compile(r"\bthe very final\b", re.I),
    re.compile(r"\bEpisode\s+[IVX]+\b", re.I),
    re.compile(r"\b(?:silent voice|kimi no na wa|your name\.?)\b", re.I),
    re.compile(r"\botona e no kaidan\b", re.I),
    re.compile(r"\bstairway to adult\b", re.I),
    re.compile(r"\bsemi[- ]?final\b", re.I),
    re.compile(r"\bfirst kiss that never ends\b", re.I),
    re.compile(r"\bspirited away\b", re.I),
    re.compile(r"\bsen to chihiro\b", re.I),
    re.compile(r"\bhowl'?s moving castle\b", re.I),
    re.compile(r"\bprincess mononoke\b", re.I),
    re.compile(r"\bgrave of the fireflies\b", re.I),
    re.compile(r"\bmy neighbor totoro\b", re.I),
    re.compile(r"\bponyo\b", re.I),
    re.compile(r"\bthe wind rises\b", re.I),
    re.compile(r"\binfinity castle\b", re.I),
    re.compile(r"\blegend of (?:hei|luo xiaohei)\b", re.I),
)
BRACKET_EP_RANGE_RE = re.compile(r"\[\d{1,3}\s*[-–—]\s*\d{1,3}\]")
MANGA_VOLUME_IN_TITLE_RE = re.compile(
    r"\bv(?:ol(?:ume)?\.?\s*)?\d{1,2}\s*[-–—]\s*v?\d{1,2}\b",
    re.I,
)
OVA_PATTERNS = (
    re.compile(r"\bova\b", re.I),
    re.compile(r"\boad\b", re.I),
    re.compile(r"\boav\b", re.I),
)
OVA_EP_RE = re.compile(r"\b(?:OVA|OAV)\s*(?P<episode>\d{1,3})\b", re.I)
SPECIAL_PATTERNS = (
    re.compile(r"\bspecial\b", re.I),
    re.compile(r"\btv\s+special\b", re.I),
    re.compile(r"\b(?:^|\s)sp(?:\s|$|\d)", re.I),
    re.compile(r"\bpilot\b", re.I),
    re.compile(r"\bfan\s+letter\b", re.I),
    re.compile(r"\bsoudansho\b", re.I),
    re.compile(r"\brecap\b", re.I),
)
SPINOFF_PATTERNS = (
    re.compile(r"\bpetit\b", re.I),
    re.compile(r"\bbreak\s+time\b", re.I),
    re.compile(r"\bmini\s+anime\b", re.I),
    re.compile(r"\bpicture\s+drama\b", re.I),
    re.compile(r"\bbiyori\b", re.I),
)
BATCH_PATTERNS = (
    re.compile(r"\bbatch\b", re.I),
    re.compile(r"\bcomplete\b", re.I),
    re.compile(r"\bfull\s+season\b", re.I),
    re.compile(r"\b(?:collection|integrale|intégrale|discography|adventure\s+series)\b", re.I),
    re.compile(r"\[Ep\.\s*\d+\s+ao\s+\d+\]", re.I),
    re.compile(r"\[\d{1,3}\s*[-–—~]\s*\d{1,3}\]", re.I),
    re.compile(r"\((?:Japanese|English)[^)]*\bsub\)", re.I),
    re.compile(r"\b\d{1,3}\s*[-–—~]\s*\d{1,3}\b"),
    re.compile(r"\b\d{1,3}\s*[~]\s*\d{1,3}\b"),
    re.compile(r"\bS\d{1,2}E\d{1,3}\s*[-–—~]\s*S?\d{0,2}E?\d{1,3}\b", re.I),
    re.compile(r"\bS\d{1,2}E\d{1,3}\s*-\s*S?\d{1,2}E\d{1,3}\b", re.I),
)
VIDEO_EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|webm|m4v|mov)\b", re.I)
EP_NUM = r"\d{1,4}"
HASH_BRACKET_RE = re.compile(r"\[[0-9A-Fa-f]{6,}\]")
SEASON_EP_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b", re.I)
SE_BATCH_RANGE_RE = re.compile(
    r"\bS(?P<s>\d{1,2})E(?P<a>\d{1,3})\s*[-–—~]\s*S?\d{0,2}E?(?P<b>\d{1,3})\b",
    re.I,
)
ORDINAL_SEASON_RE = re.compile(r"(?P<season>\d)(?:st|nd|rd|th)\s+Season", re.I)
ORDINAL_DASH_RE = re.compile(r"(?P<season>\d)(?:st|nd|rd|th)\s*[-–—]", re.I)
SEASON_WORD_RE = re.compile(r"\bSeason\s*(?P<season>\d+)\b", re.I)
SEASON_SHORT_RE = re.compile(r"(?<![A-Za-z0-9])S(?P<season>\d{1,2})(?!E\d)", re.I)
R_CODE_RE = re.compile(r"\bR(?P<season>[12])\b", re.I)
SEASON_PACK_RE = re.compile(
    r"\b(?:BD|BluRay|Blu-?Ray|Remux|Batch|Complete|Full\s+Series|"
    r"Dual[\s-]?Audio|BDRip|WEBRip|WEB-?DL|WEB|DVDRip|DVD\d{3}p?|LaserDisc|DVD|BDMV)\b",
    re.I,
)
NON_ANIME_EXTRA_RE = re.compile(
    r"\b(?:soundtrack|ost\b|original soundtrack|artbook|picture collection|"
    r"patches?\b|discography|music collection|opening|drama\s+cd|vocal\s+best|"
    r"op\s+single|my darling'?s embrace|phenogram|octet|visual novel|"
    r"patch pre-applied|lossless|qianzhuan|ending theme|creditless|"
    r"theme by|single\)|\bmp3\b|OP\s*&\s*ED|\(op\.|toki wo kizamu|"
    r"op cage|anime oped|(?:\d{1,2}(?:st|nd|rd|th)\s+)?single\b|theatrical trailer|joe hisaishi|"
    r"hi-res|テーマ|for windows|\[pc\]|goldberg|portable\)|seven seas|"
    r"festival|hololive|yoasobi|catalog set)\b",
    re.I,
)
NON_ANIME_EXTRA_RE2 = re.compile(
    r"\[\d+\s*CDs?\]|(?:^|\s)OP\s+[\[-]|(?:^|\[)OP\[|\]OP\[|\bED\s*\d|"
    r"\bOP\s*\d\b|\.(?:flac|rar)\b|\b\d{3}kbps\b",
    re.I,
)
AFTER_STORY_BATCH_RE = re.compile(
    r"\b(?:after\s+story|clannad).*?\(\s*(?:1080|720|480|2160|4k|10-bit|full|\d{3,4}x\d{3,4})",
    re.I,
)
LEGACY_YEAR_PACK_RE = re.compile(r"\b(?:19|20)\d{2}\b")
LEADING_EP_DOT_RE = re.compile(r"^(?P<episode>\d{1,3})\.", re.I)
PAREN_EP_RE = re.compile(rf"(?<![a-z0-9])(?P<episode>{EP_NUM})\s*\(", re.I)
TILDE_EP_RE = re.compile(r"~\s*(?P<episode>\d{1,3})\(", re.I)
FRACTION_EP_DASH_RE = re.compile(
    r"[-–—]\s*(?P<episode>\d{1,3})\.\d+\s*[\[(]", re.I
)
UNDERSCORE_EP_RE = re.compile(r"_(?P<episode>\d{1,3})(?:\s+\[|\s{2,}|\s*$)")
TRAILING_EP_RE = re.compile(rf"\s(?P<episode>{EP_NUM})(?:v\d+)?\s*$")
STORY_EP_RE = re.compile(rf"Story\s+(?P<episode>{EP_NUM})(?:v\d+)?(?:\s|\[|$)")
GINTAMA_KAI_EP_RE = re.compile(r"\bKa[iï]\s+(?P<episode>\d{1,3})\s*[-–—]", re.I)
ROMAN_SEASON_BATCH_RE = re.compile(
    r"\bMob Psycho 100\s+(?P<roman>II|III|IV)\b.*\[1080", re.I
)
UNDERSCORE_ORDINAL_EP_RE = re.compile(
    r"_(?P<season>\d)(?:st|nd|rd|th)_(?P<episode>\d{1,3})_", re.I
)
RESOLUTION_IN_PAREN_RE = re.compile(r"\(\d{3,4}x\d{3,4}")
SIMPLE_UNDERSCORE_EP_RE = re.compile(r"_(?P<episode>\d{1,3})_\[")
DOT_SEASON_RE = re.compile(r"\.S(?P<season>\d{1,2})\.", re.I)
DOT_SE_EP_RE = re.compile(r"\.S(?P<season>\d{1,2})E(?P<episode>\d{1,3})", re.I)
STEINS_GATE_ZERO_DOT_RE = re.compile(r"Steins\.Gate\.0", re.I)
YEAR_SPAN_RE = re.compile(r"\(20\d{2}-\d{4}\)")
BARE_SERIES_PACK_RE = re.compile(
    r"^(?:\[[^\]]+\]\s*)?(?:3-gatsu no Lion|Fullmetal Alchemist:?\s*Brotherhood)\s*$",
    re.I,
)
STANDALONE_FILM_RE = re.compile(
    r"\b(?:"
    r"the\s+exiled|fukkatsu|resurrection|stardust|milos|"
    r"memories\s+of\s+hatred|episode\s+[ivx]+"
    r")\b",
    re.I,
)
PART_RE = re.compile(r"\bPart\s*(?P<season>\d+)\b", re.I)
COUR_RE = re.compile(r"\bCour\s*(?P<season>\d+)\b", re.I)
EP_DASH_RE = re.compile(
    rf"[-–—]\s*(?P<episode>{EP_NUM})(?:v\d+)?(?:\s|\[|\(|\.|$)",
    re.I,
)
JP_EP_RE = re.compile(r"第(?P<episode>\d{1,3})話?")
BRACKET_EP_RE = re.compile(r"\]\s*\[(?P<episode>\d{1,3})\]\[")
LEADING_BRACKET_EP_RE = re.compile(r"^\[(?P<episode>\d{1,3})\]")
SPACE_EP_RAW_RE = re.compile(r"\s(?P<episode>\d{1,3})\s+RAW\b", re.I)
EP_WORD_RE = re.compile(r"\b(?:Episode|EP)\s*(?P<episode>\d{1,3})\b", re.I)
DOT_EP_RE = re.compile(
    r"\.(?:e(?:p(?:isode)?)?)?0?(?P<episode>\d{1,3})(?:v\d+)?\."
    r"(?:(?:v\d+|[a-z0-9]+)\.)*(?:1080|720|480|2160|4k|bluray|bdrip|web|x26|hevc|h\.26|av1|opus|mkv|mp4)",
    re.I,
)
YEAR_PACK_RE = re.compile(r"\(20\d{2}\)")
MULTISUB_PACK_RE = re.compile(r"\[multisubs?:", re.I)
GATE_ZERO_RE = re.compile(r"\bGate\s*0\b", re.I)
GATE_HD_RE = re.compile(r"\bSTEINS;GATE\s+HD\b", re.I)
YEAR_EMBED_RE = re.compile(r"[\(_]20\d{2}[\)_]")
SPACE_EP_QUALITY_RE = re.compile(
    rf"\b(?P<episode>{EP_NUM})\s+(?:1080|720|480|2160|4k)p\b", re.I
)
FAN_LETTER_EP_RE = re.compile(
    rf"\bFan\s+Letter[_\s-]*0?(?P<episode>{EP_NUM})\b", re.I
)
ARC_EP_RE = re.compile(
    r"^(?P<series>.+?)\s+[-–—]\s+(?P<arc>.+?)\s+[-–—]\s+(?P<episode>\d{1,3})(?:v\d+)?(?:\s|\[|\(|$)",
    re.I,
)


def _primary_body_segment(body: str) -> str:
    """Ignore les alias Nyaa après | en gardant le segment utile (saison / épisode / film)."""
    parts = [part.strip() for part in body.split("|") if part.strip()]
    if len(parts) <= 1:
        return body
    for part in parts:
        if (
            parse_season(part) is not None
            or SEASON_EP_RE.search(part)
            or EP_DASH_RE.search(part)
            or matches_any(MOVIE_PATTERNS, part)
            or matches_any(BATCH_PATTERNS, part)
            or matches_any(OVA_PATTERNS, part)
            or matches_any(SPECIAL_PATTERNS, part)
        ):
            return part
    best = parts[0]
    best_score = 0
    for part in parts:
        score = 0
        if SEASON_PACK_RE.search(part):
            score += 3
        if resolution_tag(part):
            score += 1
        if score > best_score:
            best_score = score
            best = part
    return best


def strip_release_group(title: str) -> tuple[str | None, str]:
    match = RELEASE_GROUP_RE.match(title)
    if not match:
        return None, title
    return match.group(1), title[match.end() :].strip()


def matches_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def parse_season(body: str) -> int | None:
    for pattern in (
        R_CODE_RE,
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


def is_non_anime_extra(title: str) -> bool:
    """OST, scans, etc. — hors périmètre catalogue épisodes."""
    return bool(NON_ANIME_EXTRA_RE.search(title) or NON_ANIME_EXTRA_RE2.search(title))


def is_season_pack(body: str, season: int | None, episode: int | None) -> bool:
    """Pack saison BD/WEB sans numéro d'épisode explicite."""
    if episode is not None:
        return False
    if SEASON_EP_RE.search(body) or EP_DASH_RE.search(body):
        return False
    if DOT_EP_RE.search(body):
        return False
    if VIDEO_EXT_RE.search(body) and re.search(
        r"[\._-]0?\d{1,3}(?:v\d+)?\.(?:mkv|mp4|avi|webm|m4v|mov)\b", body, re.I
    ):
        return False
    if season is not None:
        return True
    return bool(SEASON_PACK_RE.search(body))


def is_standalone_film(body: str, episode: int | None) -> bool:
    """Films / OVA longs sans le mot « movie » (remux, Akito the Exiled, etc.)."""
    if episode is not None:
        return False
    if not STANDALONE_FILM_RE.search(body):
        return False
    return bool(
        re.search(
            r"\b(?:remux|bluray|bdrip|bd\b|movie|film|hybrid)\b",
            body,
            re.I,
        )
    )


def _paren_episode(body: str) -> re.Match[str] | None:
    for match in PAREN_EP_RE.finditer(body):
        episode = match.group("episode")
        if re.search(
            rf"\b(?:Season|Part|Cour)\s+{episode}\s*\(",
            body,
            re.I,
        ):
            continue
        return match
    return None


def parse_episode(body: str) -> int | None:
    if MANGA_VOLUME_IN_TITLE_RE.search(body):
        return None
    match = (
        EP_DASH_RE.search(body)
        or EP_WORD_RE.search(body)
        or OVA_EP_RE.search(body)
        or JP_EP_RE.search(body)
        or BRACKET_EP_RE.search(body)
        or LEADING_BRACKET_EP_RE.search(body)
        or SPACE_EP_RAW_RE.search(body)
        or TRAILING_EP_RE.search(body)
        or STORY_EP_RE.search(body)
        or GINTAMA_KAI_EP_RE.search(body)
        or DOT_EP_RE.search(body)
        or SPACE_EP_QUALITY_RE.search(body)
        or FAN_LETTER_EP_RE.search(body)
        or LEADING_EP_DOT_RE.search(body)
        or TILDE_EP_RE.search(body)
        or _paren_episode(body)
        or UNDERSCORE_EP_RE.search(body)
    )
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
    if matches_any(MOVIE_PATTERNS, body) or is_standalone_film(body, episode):
        return MediaKind.MOVIE
    if matches_any(BATCH_PATTERNS, body) or is_season_pack(body, season, episode):
        return MediaKind.BATCH
    if matches_any(SPECIAL_PATTERNS, body):
        return MediaKind.SPECIAL
    if re.search(r"\bbrotherhood\b.*\bspecials?\b", body, re.I):
        return MediaKind.SPECIAL
    if matches_any(SPINOFF_PATTERNS, body):
        return MediaKind.SPECIAL
    if matches_any(OVA_PATTERNS, body):
        return MediaKind.OVA
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
    release_group, raw_body = strip_release_group(title)
    segment = _primary_body_segment(raw_body)
    body = segment.replace("_", " ")
    if is_non_anime_extra(title):
        return finalize_parsed(
            title=title,
            release_group=release_group,
            body=body,
            kind=MediaKind.UNKNOWN,
            season=None,
            episode=None,
            arc=None,
        )
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

    se_batch = SE_BATCH_RANGE_RE.search(body)
    ord_ep = UNDERSCORE_ORDINAL_EP_RE.search(segment)
    roman_batch = ROMAN_SEASON_BATCH_RE.search(body)
    dot_se = DOT_SE_EP_RE.search(segment)
    if se_batch:
        kind = MediaKind.BATCH
        season = int(se_batch.group("s"))
    elif dot_se:
        kind = MediaKind.EPISODE
        season = int(dot_se.group("season"))
        episode = int(dot_se.group("episode"))
    elif roman_batch:
        kind = MediaKind.BATCH
        roman = roman_batch.group("roman")
        season = {"II": 2, "III": 3, "IV": 4}.get(roman, 1)
    elif ord_ep:
        kind = MediaKind.EPISODE
        season = int(ord_ep.group("season"))
        episode = int(ord_ep.group("episode"))
    elif FRACTION_EP_DASH_RE.search(body):
        frac = FRACTION_EP_DASH_RE.search(body)
        kind = MediaKind.SPECIAL
        episode = int(frac.group("episode")) if frac else None
    elif STEINS_GATE_ZERO_DOT_RE.search(body) and DOT_SEASON_RE.search(body):
        kind = MediaKind.BATCH
        season = int(DOT_SEASON_RE.search(body).group("season"))
    elif YEAR_SPAN_RE.search(body):
        kind = MediaKind.BATCH
    elif re.search(r"\b3-gatsu no lion\b", body, re.I) and re.search(
        r"\[(?:x265|1920)", body, re.I
    ):
        kind = MediaKind.BATCH
    elif matches_any(MOVIE_PATTERNS, body) and not BRACKET_EP_RANGE_RE.search(body):
        kind = MediaKind.MOVIE
    elif is_standalone_film(body, None):
        kind = MediaKind.MOVIE
    elif matches_any(BATCH_PATTERNS, body) or AFTER_STORY_BATCH_RE.search(body):
        kind = MediaKind.BATCH
        season = parse_season(body)
    elif RESOLUTION_IN_PAREN_RE.search(body) and parse_episode(body) is None:
        kind = MediaKind.BATCH
        season = parse_season(body)
    elif BARE_SERIES_PACK_RE.search(body.strip()):
        kind = MediaKind.BATCH
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
                episode = parse_episode(body) or (
                    int(m.group("episode"))
                    if (m := UNDERSCORE_EP_RE.search(segment))
                    else None
                ) or (
                    int(m.group("episode"))
                    if (m := SIMPLE_UNDERSCORE_EP_RE.search(segment))
                    else None
                )
            season = parse_season(body_for_season)
        kind = detect_kind(body, season, episode, arc)
        if kind == MediaKind.UNKNOWN and is_standalone_film(body, episode):
            kind = MediaKind.MOVIE
        if kind == MediaKind.UNKNOWN and is_season_pack(body, season, episode):
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and YEAR_PACK_RE.search(body)
        ):
            season = season or 1
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and MULTISUB_PACK_RE.search(body)
            and resolution_tag(title)
        ):
            kind = MediaKind.BATCH
        if kind == MediaKind.UNKNOWN and GATE_ZERO_RE.search(body):
            kind = MediaKind.BATCH
        if kind == MediaKind.UNKNOWN and GATE_HD_RE.search(body):
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and YEAR_EMBED_RE.search(body)
            and resolution_tag(title)
        ):
            season = season or 1
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and LEGACY_YEAR_PACK_RE.search(body)
            and resolution_tag(title)
        ):
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and resolution_tag(title)
            and re.search(
                r"\b(?:brotherhood|3-gatsu no lion|march comes in like a lion|"
                r"hajime no ippo|fighting spirit)\b",
                body,
                re.I,
            )
        ):
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and re.search(r"\bbrotherhood\b", body, re.I)
            and re.search(r"\[(?:multi|br)\]", body, re.I)
        ):
            kind = MediaKind.BATCH
        if (
            kind == MediaKind.UNKNOWN
            and episode is None
            and re.search(r"\bginga eiyuu densetsu\b", body, re.I)
            and re.search(r"\[(?:VOSTFR|BR)\]|(?:19|20)\d{2}\b", body, re.I)
        ):
            kind = MediaKind.BATCH
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
