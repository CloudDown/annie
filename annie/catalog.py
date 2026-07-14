"""Construction du catalogue Nyaa aligné MAL."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace

from annie.nyaa import NyaaEntry
from annie.parsing import (
    SPINOFF_PATTERNS,
    VIDEO_EXT_RE,
    _token_matches,
    best_series_match_score,
    is_franchise_pack_with_movie,
    is_manga,
    minimal_label,
    normalize,
    parse_season,
    parse_title,
    query_tokens,
    strip_release_group,
)
from annie.scoring import catalog_episode_pick_rank, rank_entry, target_match_score
from annie.season_coherence import assess_season_coherence
from annie.types import (
    MalRelease,
    MediaKind,
    MediaSection,
    ParsedTitle,
    ResultItem,
    WatchTarget,
)


def _catalog_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load().catalog


RECAP_MOVIE_PATTERNS = (
    re.compile(r"\brecap\b", re.I),
    re.compile(r"\bsummary\b", re.I),
)
_SEASON_TAG_RE = re.compile(r"\bS0?(\d{1,2})\b", re.I)
# Pluriel « Seasons 1-4 » = plage de saisons.
# Singulier « Season 1-2 » (sans espaces) seulement si hi ≤ 5 (packs courts).
# « Season 3 - 04 » / « Season 1~13 » = épisode(s), pas plage de saisons.
_SEASON_SPAN_IN_TITLE_RE = re.compile(
    r"\bseasons\s*(?P<lo>\d{1,2})\s*[-–—~]\s*(?P<hi>\d{1,2})\b"
    r"|\bseason\s*(?P<lo2>\d{1,2})[-–—~](?P<hi2>[1-5])\b",
    re.I,
)
_S_SPAN_IN_TITLE_RE = re.compile(
    r"\bS0?(?P<lo>\d{1,2})\s*[-–—~]\s*S0?(?P<hi>\d{1,2})\b",
    re.I,
)
_FINAL_SEASON_RE = re.compile(r"\b(?:final|last)\s+season\b", re.I)


def section_key(parsed) -> str:
    if parsed.kind == MediaKind.EPISODE:
        if parsed.arc:
            return f"arc:{parsed.arc.lower()}"
        if parsed.season is not None:
            return f"season:{parsed.season:02d}"
        if parsed.episode is not None and parsed.episode > 25:
            return "season:absolute"
        return "season:01"
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


def is_spinoff(title: str) -> bool:
    return any(pattern.search(title) for pattern in SPINOFF_PATTERNS)


def _batch_title_body(title: str) -> str:
    """Ignore Nyaa alias segments that often contain false episode ranges."""
    _, body = strip_release_group(title)
    return body.split("|", 1)[0].strip()


def scope_releases_for_target(
    releases: list[MalRelease],
    *,
    season: int | None = None,
    kind: MediaKind | None = None,
) -> list[MalRelease]:
    """Réduit les requêtes Nyaa quand la cible saison/type est connue."""
    if season is None and kind is None:
        return releases
    scoped: list[MalRelease] = []
    for release in releases:
        if kind is not None and release.kind != kind:
            continue
        if season is not None:
            if release.kind != MediaKind.EPISODE or release.season != season:
                continue
        scoped.append(release)
    return scoped or releases


def franchise_absolute_offsets(releases: list[MalRelease]) -> dict[int, int]:
    """mal_id → index absolu (0-based) du premier épisode de la saison."""
    tv = sorted(
        [
            release
            for release in releases
            if release.kind == MediaKind.EPISODE
            and release.season is not None
            and release.episode_count
        ],
        key=lambda release: (release.season or 0, release.sort_key),
    )
    offsets: dict[int, int] = {}
    cursor = 0
    for release in tv:
        offsets[release.mal_id] = cursor
        cursor += release.episode_count or 0
    return offsets


def effective_absolute_offsets(releases: list[MalRelease]) -> dict[int, int]:
    """Offset absolu par mal_id : max entre l'offset stocké (franchise complète)
    et celui recalculé sur la liste courante (compatible releases scoped)."""
    computed = franchise_absolute_offsets(releases)
    return {
        release.mal_id: max(
            release.absolute_episode_offset, computed.get(release.mal_id, 0)
        )
        for release in releases
    }


def max_tv_season(releases: list[MalRelease]) -> int | None:
    seasons = [
        release.season
        for release in releases
        if release.kind == MediaKind.EPISODE and release.season is not None
    ]
    return max(seasons) if seasons else None


def _explicit_seasons_in_title(title: str) -> set[int]:
    seasons: set[int] = set()
    parsed = parse_season(title)
    if parsed is not None and parsed >= 1:
        seasons.add(parsed)
    for match in _SEASON_TAG_RE.finditer(title):
        value = int(match.group(1))
        if value >= 1:
            seasons.add(value)
    for pattern in (_SEASON_SPAN_IN_TITLE_RE, _S_SPAN_IN_TITLE_RE):
        for match in pattern.finditer(title):
            groups = match.groupdict()
            lo = int(groups.get("lo") or groups.get("lo2") or 0)
            hi = int(groups.get("hi") or groups.get("hi2") or 0)
            if lo >= 1 and hi >= lo:
                seasons.update(range(lo, hi + 1))
    return seasons


def is_franchise_multi_season_batch(title: str) -> bool:
    """Pack Nyaa couvrant plusieurs saisons (ex. Seasons 1-2 + Movies)."""
    body = _batch_title_body(title)
    return bool(
        _SEASON_SPAN_IN_TITLE_RE.search(body) or _S_SPAN_IN_TITLE_RE.search(body)
    )


def _magnet_reusable_across_seasons(title: str) -> bool:
    """Torrent franchise réutilisable pour plusieurs saisons MAL (pack complet)."""
    if is_franchise_multi_season_batch(title):
        return True
    _, episodes = parse_batch_episode_range(title)
    return len(episodes) > 13


def _batch_range_is_season_span(body: str, match: re.Match[str]) -> bool:
    """Évite de lire « Seasons 1-2 » / « Season 3 - 04 » comme plage d'épisodes."""
    prefix = body[max(0, match.start() - 24) : match.start()]
    if re.search(r"seasons\s*$", prefix, re.I):
        return True
    # « Season 3 - 04 » : le 1er nombre = n° de saison, plage courte → pas un batch.
    # « Season 1~13 » / « Season 1-13 » : vraie plage d'épisodes (écart > 2).
    if re.search(r"\bseason\s*$", prefix, re.I):
        season = parse_season(body)
        try:
            start = int(match.group("a"))
            end = int(match.group("b"))
        except (IndexError, ValueError):
            return False
        if season is not None and start == season and (end - start) <= 2:
            return True
    return bool(re.search(r"S0?\d+\s*[-–—~]\s*$", prefix, re.I))


def _remap_item_for_release(
    item: ResultItem,
    release: MalRelease,
    *,
    absolute_offset: int = 0,
) -> ResultItem | None:
    if release.kind != MediaKind.EPISODE or release.season is None:
        return item
    parsed = item.parsed
    if parsed.episode is None:
        return None
    expected = release.episode_count or 0
    if not expected:
        return item

    episode = parsed.episode
    if parsed.season is None:
        if 1 <= episode <= expected:
            return ResultItem(
                entry=item.entry,
                parsed=replace(parsed, season=release.season, episode=episode),
                score=item.score,
            )
        if absolute_offset > 0:
            relative = episode - absolute_offset
            if 1 <= relative <= expected:
                return ResultItem(
                    entry=item.entry,
                    parsed=replace(
                        parsed,
                        season=release.season,
                        episode=relative,
                        source_episode=parsed.source_episode or episode,
                    ),
                    score=item.score,
                )
        return None

    if parsed.season == release.season and 1 <= episode <= expected:
        return item

    if absolute_offset > 0:
        relative = episode - absolute_offset
        if 1 <= relative <= expected:
            return ResultItem(
                entry=item.entry,
                parsed=replace(
                    parsed,
                    season=release.season,
                    episode=relative,
                    source_episode=parsed.source_episode or episode,
                ),
                score=item.score,
            )

    return None


def _primary_query_token_hits(parsed: ParsedTitle, primary_query: str) -> int:
    if not primary_query:
        return 0
    haystacks = {parsed.series, normalize(parsed.display_name)}
    return sum(
        1
        for token in query_tokens(primary_query)
        if any(_token_matches(token, hay) for hay in haystacks)
    )


def franchise_absolute_offsets_from_sections(
    sections: list[MediaSection],
) -> dict[int, int]:
    releases = [
        MalRelease(
            mal_id=section.mal_id or 0,
            label=section.label,
            kind=section.kind,
            season=section.season,
            episode_count=section.expected_episodes,
            nyaa_queries=section.nyaa_queries or [section.label],
            sort_key=(section.season or 0, section.label.lower()),
        )
        for section in sections
        if section.kind == MediaKind.EPISODE
        and section.season is not None
        and section.expected_episodes
        and section.mal_id
    ]
    return franchise_absolute_offsets(releases)


def _relative_episode_number(
    parsed: ParsedTitle,
    release: MalRelease,
    *,
    absolute_offset: int = 0,
) -> int | None:
    if parsed.episode is None:
        return None
    expected = release.episode_count
    if not expected:
        return parsed.episode

    episode = parsed.episode
    if parsed.season is not None and release.season is not None:
        if parsed.season != release.season:
            return None
        return episode if 1 <= episode <= expected else None

    relative = episode - absolute_offset
    if 1 <= relative <= expected:
        return relative
    if absolute_offset == 0 and 1 <= episode <= expected:
        return episode
    return None


def _series_conflicts_with_release(
    parsed: ParsedTitle,
    release: MalRelease,
    *,
    title: str = "",
    max_tv_season: int | None = None,
) -> bool:
    """Rejette une autre partie de franchise (ex. Final Season hors dernière entrée TV MAL)."""
    if release.season is None:
        return False
    hay = normalize(
        f"{parsed.series} {parsed.arc or ''} {parsed.display_name} {title}"
    )
    if (
        max_tv_season is not None
        and release.season != max_tv_season
        and _FINAL_SEASON_RE.search(hay)
    ):
        return True
    explicit = _explicit_seasons_in_title(title)
    if explicit and not any(season == release.season for season in explicit):
        return True
    return False


def _episode_belongs_to_release(
    item: ResultItem,
    release: MalRelease,
    *,
    absolute_offset: int = 0,
    max_tv_season: int | None = None,
) -> bool:
    if release.kind != MediaKind.EPISODE or release.season is None:
        return True
    if is_spinoff(item.entry.title):
        return False

    remapped = _remap_item_for_release(
        item, release, absolute_offset=absolute_offset
    )
    if remapped is None:
        return False

    parsed = remapped.parsed
    title = item.entry.title
    if _series_conflicts_with_release(
        parsed, release, title=title, max_tv_season=max_tv_season
    ):
        if not is_franchise_multi_season_batch(title):
            return False
    if (
        release.nyaa_queries
        and best_series_match_score(parsed, release.nyaa_queries) < 0
    ):
        return False
    if parsed.episode is None:
        return True
    relative = _relative_episode_number(
        parsed, release, absolute_offset=absolute_offset
    )
    if relative is None:
        return False
    uses_absolute = (
        absolute_offset > 0
        and item.parsed.episode is not None
        and item.parsed.episode > absolute_offset
    )
    if uses_absolute:
        primary = release.nyaa_queries[0] if release.nyaa_queries else ""
        primary_tokens = query_tokens(primary)
        if (
            len(primary_tokens) >= 2
            and _primary_query_token_hits(parsed, primary) < 2
        ):
            return False
    return True


def _filter_section_for_release(
    section: MediaSection,
    release: MalRelease,
    *,
    absolute_offset: int = 0,
    max_tv_season: int | None = None,
) -> None:
    section.episodes = {
        ep: item
        for ep, item in section.episodes.items()
        if _episode_belongs_to_release(
            item,
            release,
            absolute_offset=absolute_offset,
            max_tv_season=max_tv_season,
        )
    }
    section.singles = [
        item
        for item in section.singles
        if not is_spinoff(item.entry.title)
        and (
            release.kind != MediaKind.MOVIE
            or _movie_belongs_to_release(item, release)
        )
    ]
    if release.kind == MediaKind.MOVIE:
        # Une section film ne doit jamais garder des épisodes de saison.
        section.episodes = {}
        _keep_best_movie_only(section)


def _better_episode_pick(new: ResultItem, current: ResultItem | None) -> bool:
    if current is None:
        return True
    return catalog_episode_pick_rank(new) > catalog_episode_pick_rank(current)


def _season_movie_pack_covers_release(title: str, release: MalRelease) -> bool:
    """Pack « Season N + Movie » : couvre les épisodes de la saison N."""
    if release.kind != MediaKind.EPISODE or release.season is None:
        return False
    if not is_franchise_pack_with_movie(title):
        return False
    explicit = _explicit_seasons_in_title(title)
    if explicit:
        return release.season in explicit
    inferred = parse_season(_batch_title_body(title))
    if inferred is not None:
        return inferred == release.season
    # Pack franchise multi sans numéro clair : utile pour S1.
    return release.season == 1


def _franchise_batch_covers_full_season(
    title: str, episodes: list[int], expected: int
) -> bool:
    """Pack multi-saisons : « Season 1-4 » est parsé 1~4, pas les 51 épisodes."""
    if is_franchise_multi_season_batch(title):
        if not episodes:
            return True
        if max(episodes) <= 2:
            return True
        if expected and len(episodes) < expected * 0.5:
            return True
        return False
    return False


def upsert_episode(section: MediaSection, item: ResultItem) -> None:
    episode = item.parsed.episode
    if episode is None:
        section.singles.append(item)
        return
    current = section.episodes.get(episode)
    if _better_episode_pick(item, current):
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
    r"\b(?:ost|o\.?\s?s\.?\s?t\.?|soundtrack|discography|\bcd\b|\bmp3\b|"
    r"original\s+sound(?:track)?|groundwork|lossless\s+flac|\bflac\b|"
    r"short|preview|teaser|trailer|\bpv\b|\bcm\b|digest|"
    r"yen\s*press|light\s*novels?|\bmanga\b|\bportable\b|"
    r"realta\s*nua|ultimate\s+edition|visual\s+novel|\bvn\b|"
    r"audiobook|unabridged|\bebook\b|\bepub\b|\bpdft?\b)\b",
    re.I,
)
MOVIE_PACK_RE = re.compile(
    r"\b(?:TV\s*\+\s*MOVIE|\+ MOVIE|\+ Movies|Season\s*\d+\s*\+\s*Season)\b",
    re.I,
)
MOVIE_NUM_RE = re.compile(r"\bmovie\s*(\d+)\b", re.I)
# Épisode TV explicite : ne doit pas atterrir dans une section film.
_TV_EPISODE_IN_TITLE_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bS\d{1,2}E\d{1,3}\b"
    r"|\bSeason\s*\d{1,2}\s*Episode\s*\d{1,3}\b"
    r"|\bEpisode\s+\d{1,3}\b"
    r")"
)


def is_movie_noise(title: str) -> bool:
    if is_franchise_pack_with_movie(title):
        return True
    if MOVIE_PACK_RE.search(title):
        return True
    if re.search(r"\.(?:ass|srt|ssa|sub|flac|mp3|m4a)\b", title, re.I):
        return True
    if re.search(r"\bcollection\s+\d+\s+movies?\b", title, re.I):
        return True
    if VIDEO_EXT_RE.search(title):
        # Fichier vidéo : encore filtrer OST / artbook collés au titre.
        if MOVIE_NOISE_RE.search(title):
            return True
        return False
    if MOVIE_NOISE_RE.search(title):
        return True
    if re.search(r"\b(?:ost|soundtrack)\b", title, re.I):
        return True
    return False


def _looks_like_tv_episode_title(title: str) -> bool:
    """Titre d'épisode / saison TV, pas un film standalone."""
    if is_franchise_pack_with_movie(title):
        return True
    parsed = parse_title(title)
    if parsed.kind == MediaKind.BATCH:
        return True
    movie_marker = re.search(
        r"\b(?:gekijouban|the\s+movie|movie\s*\d+|film)\b", title, re.I
    )
    if parsed.kind == MediaKind.EPISODE and parsed.episode is not None:
        return not movie_marker
    if _TV_EPISODE_IN_TITLE_RE.search(title) and not movie_marker:
        return True
    return False


_MOVIE_STOP_TOKENS = frozenset(
    {
        "the",
        "of",
        "you",
        "are",
        "can",
        "not",
        "a",
        "an",
        "to",
        "and",
        "or",
        "movie",
        "film",
        "edition",
        "anime",
        "season",
        "does",
        "dream",
    }
)


def _movie_specific_queries(release: MalRelease) -> list[str]:
    """Requêtes discriminantes pour un film (ignore la racine franchise trop large)."""
    queries = [q for q in (release.nyaa_queries or []) if q and str(q).strip()]
    if release.label:
        queries.append(release.label)
    movieish = re.compile(
        r"\b(?:\d\.\d|movie|gekijouban|film|crimson|thrice|end of|alone|advance|redo|"
        r"legend|kurenai|densetsu|mugen|train|eternity|ordinal|scale|"
        r"heaven'?s?\s*feel|presage|butterfly|spring\s*song)\b",
        re.I,
    )
    generic_movie = re.compile(
        r"^(?P<head>.+?)\s+(?:the\s+)?movies?\s*$",
        re.I,
    )
    focus_tokens = {
        t
        for t in query_tokens(_movie_label_focus(release.label or ""))
        if t not in _MOVIE_STOP_TOKENS and len(t) >= 3
    }
    specific: list[str] = []
    for query in dict.fromkeys(queries):
        if movieish.search(query):
            # « Sword Art Online the Movie » sans sous-titre = trop large.
            if generic_movie.match(query.strip()) and release.label:
                label_tokens = set(query_tokens(release.label))
                if label_tokens - set(query_tokens(query)):
                    continue
            specific.append(query)
            continue
        if release.label and query.strip().lower() == release.label.strip().lower():
            specific.append(query)
            continue
        # Synonyme JP / alt title qui porte les ancres du film.
        q_tokens = set(query_tokens(query))
        if focus_tokens and focus_tokens & q_tokens and len(q_tokens) >= 3:
            specific.append(query)
    if not specific and release.label:
        specific.append(release.label)
    return specific or queries[:1]


def _movie_franchise_roots(release: MalRelease) -> list[str]:
    """Jetons franchise qui doivent apparaître dans le torrent film."""
    roots: list[str] = []
    movieish = re.compile(
        r"\b(?:\d\.\d|movie|gekijouban|film|crimson|thrice|end of|alone|legend)\b",
        re.I,
    )
    primary = (release.nyaa_queries or [None])[0] or ""
    primary_tokens = set(query_tokens(primary))
    for query in release.nyaa_queries or []:
        raw = query_tokens(query)
        tokens = [
            t
            for t in raw
            if t not in _MOVIE_STOP_TOKENS and len(t) >= 4
        ]
        if not tokens:
            continue
        if not re.search(r"[A-Za-z]", tokens[0]):
            continue
        if len(raw) > 3 or movieish.search(query):
            # « KonoSuba Legend of Crimson » → konosuba si déjà racine primaire.
            if tokens[0] in primary_tokens or tokens[0] in set(
                query_tokens(release.label or "")
            ):
                roots.append(tokens[0])
            continue
        # Requête courte hors franchise (« Unanswered//butterfly ») → ignorer.
        if (
            query != primary
            and primary_tokens
            and not (set(tokens) & primary_tokens)
        ):
            continue
        roots.append(tokens[0])
        if len(tokens) >= 2 and re.search(r"[A-Za-z]", tokens[1]):
            roots.append(tokens[1])
    return list(dict.fromkeys(roots))


def _movie_label_focus(label: str) -> str:
    """Sous-titre film (après « - » / « of a … ») pour ancrer le matching."""
    focus = label.strip()
    for sep in ("!- ", "! - ", " – ", " — ", " - ", ": "):
        if sep in focus:
            tail = focus.rsplit(sep, 1)[-1].strip()
            if len(query_tokens(tail)) >= 1:
                focus = tail
                break
    # « Rascal Does Not Dream of a Dear Friend » → « Dear Friend »
    # « Aria of a Starless Night » → « Aria Starless Night ».
    of_a = re.search(r"\bof\s+(?:an?\s+)(.+)$", focus, re.I)
    if of_a:
        head = focus[: of_a.start()].strip()
        head_tokens = query_tokens(head)
        tail = of_a.group(1).strip()
        if len(query_tokens(tail)) >= 1:
            if len(head_tokens) >= 3:
                last = head_tokens[-1]
                series_tail = _MOVIE_STOP_TOKENS | {
                    "progressive",
                    "online",
                    "movie",
                    "edition",
                    "order",
                    "grand",
                    "fate",
                    "sword",
                    "art",
                }
                if last in series_tail:
                    return tail
                return f"{last} {tail}".strip()
            if len(head_tokens) == 1:
                return f"{head_tokens[0]} {tail}".strip()
    return focus


def _movie_label_anchors(label: str, release: MalRelease) -> list[str]:
    """Jetons discriminants du label film (ex. 1.0, alone, crimson, end)."""
    franchise_tokens: set[str] = set(_movie_franchise_roots(release))
    for query in release.nyaa_queries or []:
        tokens = query_tokens(query)
        # Uniquement les racines courtes (pas le titre complet du film).
        if len(tokens) <= 3 and not re.search(
            r"\b(?:\d\.\d|movie|gekijouban|film)\b", query, re.I
        ):
            franchise_tokens.update(
                t for t in tokens if t not in _MOVIE_STOP_TOKENS
            )

    focus = _movie_label_focus(label)
    anchors = re.findall(r"\b\d+\.\d+\+?\d*\b", label)
    # Indices film : 0, 1, 2… (pas des années / résolutions)
    for match in re.finditer(r"(?<![\w.])(\d{1,2})(?![\w.])", focus):
        anchors.append(match.group(1))
    # Romains standalone majuscules (I, II, III…) — évite « and » / « doll ».
    for match in re.finditer(r"(?<![\w])([IVXLC]{1,4})(?![\w])", focus):
        anchors.append(match.group(1).lower())
    for token in query_tokens(focus):
        if token in _MOVIE_STOP_TOKENS or token in franchise_tokens:
            continue
        if len(token) >= 3:
            anchors.append(token)
    # Si le focus n'a rien donné, retomber sur le label entier.
    if not anchors:
        for token in query_tokens(label):
            if token in _MOVIE_STOP_TOKENS or token in franchise_tokens:
                continue
            if len(token) >= 3:
                anchors.append(token)
    return list(dict.fromkeys(anchors))


def _title_has_movie_anchors(title: str, anchors: list[str]) -> bool:
    if not anchors:
        return True
    hay = normalize(f"{title} {parse_title(title).display_name}")
    version_anchors = [
        a for a in anchors if re.match(r"^(?:\d+\.\d+|\d{1,2}|[ivxlcdm]{1,4})$", a, re.I)
    ]
    word_anchors = [a for a in anchors if a not in version_anchors]
    if version_anchors:
        def _version_hit(anchor: str) -> bool:
            if re.match(r"^\d+\.\d+", anchor):
                return anchor.lower() in hay or _token_matches(anchor, hay)
            # « 0 » / « II » : jeton isolé, pas un sous-chaîne d'année.
            return bool(
                re.search(rf"(?<![\d.]){re.escape(anchor)}(?![\d.])", hay, re.I)
            )

        if not any(_version_hit(a) for a in version_anchors):
            return False
    if not word_anchors:
        return True
    hits = sum(
        1 for a in word_anchors if a.lower() in hay or _token_matches(a, hay)
    )
    # 1 ancre → 1 ; 2+ → au moins 2 (évite Paladin via seul « Agateram »).
    need = 1 if len(word_anchors) == 1 else min(2, len(word_anchors))
    return hits >= need


def _movie_belongs_to_release(item: ResultItem, release: MalRelease) -> bool:
    title = item.entry.title
    if is_movie_noise(title) or _looks_like_tv_episode_title(title):
        return False
    # Saison TV explicite sans film standalone → pas un movie release.
    if re.search(r"\bS\d{1,2}\b|\bSeasons?\s*\d", title, re.I) and not re.search(
        r"\b(?:gekijouban|the\s+movie|movie\s*\d+)\b", title, re.I
    ):
        return False
    parsed = item.parsed
    if parsed.kind not in {MediaKind.MOVIE, MediaKind.UNKNOWN}:
        if parse_title(title).kind != MediaKind.MOVIE:
            return False
    roots = _movie_franchise_roots(release)
    if roots:
        hay = normalize(f"{title} {parsed.display_name} {parsed.series}")
        if not any(_token_matches(root, hay) for root in roots):
            return False
    queries = _movie_specific_queries(release)
    if queries:
        score = best_series_match_score(parsed, queries)
        if score < 300:
            return False
    anchors = _movie_label_anchors(release.label or "", release)
    if anchors and not _title_has_movie_anchors(title, anchors):
        return False
    # Label générique (= nom franchise) : le torrent doit porter les jetons
    # discriminants des queries non-franchise (ex. unanswered / butterfly).
    root_set = set(roots)
    label_tokens = {
        t
        for t in query_tokens(release.label or "")
        if t not in _MOVIE_STOP_TOKENS
    }
    label_is_franchise = bool(label_tokens) and label_tokens <= (
        root_set | {t for t in label_tokens if len(t) < 4}
    )
    if label_is_franchise or (
        anchors and not any(a not in root_set and len(a) >= 4 for a in anchors)
    ):
        distinctive: set[str] = set()
        for q in release.nyaa_queries or []:
            for t in query_tokens(q):
                if (
                    t not in root_set
                    and t not in _MOVIE_STOP_TOKENS
                    and len(t) >= 4
                ):
                    distinctive.add(t)
        if distinctive:
            hay = normalize(f"{title} {parsed.display_name} {parsed.series}")
            if not any(_token_matches(tok, hay) for tok in distinctive):
                return False
    return True


def movie_canonical_key(title: str, parsed: ParsedTitle) -> str:
    hay = normalize(f"{title} {parsed.display_name}")
    match = MOVIE_NUM_RE.search(hay)
    if match:
        return f"movie-{match.group(1)}"
    cleaned = normalize(parsed.display_name)
    cleaned = re.sub(r"\bmovie\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or normalize(title)


def infer_batch_season(body: str, season: int | None) -> int:
    if season is not None:
        return season
    inferred = parse_season(body)
    return inferred if inferred is not None else 1


def parse_batch_episode_range(title: str) -> tuple[int | None, list[int]]:
    body = _batch_title_body(title)
    season = parse_season(body)

    se_match = SE_BATCH_RANGE_RE.search(body)
    if se_match:
        season = infer_batch_season(body, int(se_match.group("s")))
        start, end = int(se_match.group("a")), int(se_match.group("b"))
        if end >= start and end - start < 60:
            return season, list(range(start, end + 1))

    for match in BATCH_EP_RANGE_RE.finditer(body):
        if _batch_range_is_season_span(body, match):
            continue
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
    # Batch déjà étendu (E3, E12, …) : ne pas réutiliser parsed.episode comme
    # source_episode — sinon pick_file tombe sur le mauvais fichier (Tanya E12→E03).
    # La numérotation absolue (Re:Zero « - 67 ») est posée plus tard via
    # _remap_item_for_release / source_episode déjà présent.
    if parsed.kind == MediaKind.BATCH:
        source = parsed.source_episode
    else:
        source = parsed.source_episode or parsed.episode
    new_parsed = replace(
        parsed,
        episode=episode,
        season=parsed.season or infer_batch_season(item.entry.title, parsed.season),
        source_episode=source if source != episode else None,
    )
    penalty = 5.0 if parsed.kind == MediaKind.BATCH else 0.0
    return ResultItem(entry=item.entry, parsed=new_parsed, score=item.score - penalty)


def upsert_movie(section: MediaSection, item: ResultItem) -> None:
    if is_movie_noise(item.entry.title):
        return
    key = movie_canonical_key(item.entry.title, item.parsed)
    for index, current in enumerate(section.singles):
        if movie_canonical_key(current.entry.title, current.parsed) == key:
            if catalog_episode_pick_rank(item) > catalog_episode_pick_rank(current):
                section.singles[index] = item
            return
    section.singles.append(item)


def _keep_best_movie_only(section: MediaSection) -> None:
    """Un film MAL = un seul torrent (meilleur seeders / qualité)."""
    if len(section.singles) <= 1:
        return
    best = max(section.singles, key=catalog_episode_pick_rank)
    section.singles = [best]


def apply_batch_episodes(
    sections: dict[str, MediaSection], batches: list[ResultItem]
) -> None:
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


def _batch_episodes_for_release(
    item: ResultItem,
    release: MalRelease,
    *,
    absolute_offset: int = 0,
) -> list[tuple[int, int]]:
    """Paires (épisode relatif release, numéro brut batch) à importer."""
    title = item.entry.title
    _, episodes = parse_batch_episode_range(title)
    expected = release.episode_count or 0
    if not expected:
        return []

    pairs: list[tuple[int, int]] = []
    seen: set[int] = set()

    def add(relative: int, raw: int) -> None:
        if 1 <= relative <= expected and relative not in seen:
            seen.add(relative)
            pairs.append((relative, raw))

    if _franchise_batch_covers_full_season(
        title, episodes, expected
    ) or _season_movie_pack_covers_release(title, release):
        for relative in range(1, expected + 1):
            add(relative, absolute_offset + relative)
        return pairs

    for raw in episodes:
        if absolute_offset == 0 and 1 <= raw <= expected:
            add(raw, raw)
            continue
        relative = raw - absolute_offset
        if 1 <= relative <= expected:
            add(relative, raw)

    return pairs


def _merge_batches_into_section(
    section: MediaSection,
    parts: list[MediaSection],
    release: MalRelease,
    *,
    absolute_offset: int = 0,
    max_tv_season: int | None = None,
) -> None:
    """Expand batch torrents from catalog parts into the MAL-aligned section."""
    seen: set[str] = set()
    for part in parts:
        for item in part.singles:
            if item.parsed.kind != MediaKind.BATCH:
                continue
            if item.entry.magnet in seen:
                continue
            seen.add(item.entry.magnet)

            for relative, raw in _batch_episodes_for_release(
                item, release, absolute_offset=absolute_offset
            ):
                candidate = item_for_episode(item, raw)
                remapped = _remap_item_for_release(
                    candidate, release, absolute_offset=absolute_offset
                )
                if remapped is None:
                    continue
                if not _episode_belongs_to_release(
                    remapped,
                    release,
                    absolute_offset=absolute_offset,
                    max_tv_season=max_tv_season,
                ):
                    continue
                upsert_episode(section, remapped)


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
    from annie.parsing import parse_title

    franchise_batch_magnets: set[str] = set()
    for section in sections:
        for item in list(section.episodes.values()) + section.singles:
            if (
                item.parsed.kind == MediaKind.BATCH
                or parse_title(item.entry.title).kind == MediaKind.BATCH
                or is_franchise_multi_season_batch(item.entry.title)
            ):
                franchise_batch_magnets.add(item.entry.magnet)

    for section in sections:
        if section.kind != MediaKind.EPISODE or not section.has_episodes:
            continue
        sparse = len(section.episodes) <= 3
        magnets = {item.entry.magnet for item in section.episodes.values()}
        has_batch = bool(magnets & franchise_batch_magnets) or any(
            parse_title(item.entry.title).kind == MediaKind.BATCH
            for item in section.episodes.values()
        )
        shared_pack = any(
            sum(1 for i in section.episodes.values() if i.entry.magnet == magnet)
            >= 3
            for magnet in magnets
        )
        section.batch_recommended = sparse and (has_batch or shared_pack)


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
        if (
            strict_target is not None
            and target_match_score(parsed, strict_target, match_queries=queries) is None
        ):
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

    for section in sections.values():
        if section.kind == MediaKind.EPISODE and section.episodes:
            apply_coherent_season_picks(section, expected=section.expected_episodes)

    result = [
        section for section in sections.values() if section.episodes or section.singles
    ]
    result.sort(key=section_sort_key)
    _annotate_batch_hints(result)
    return result


def _empty_section_for_release(release: MalRelease) -> MediaSection:
    return MediaSection(
        key=f"mal:{release.mal_id}",
        label=release.label,
        kind=release.kind,
        season=release.season,
        expected_episodes=release.episode_count,
        mal_id=release.mal_id,
        nyaa_queries=list(release.nyaa_queries),
    )


def _pick_section_for_release(
    parts: list[MediaSection],
    release: MalRelease,
    *,
    absolute_offset: int = 0,
    max_tv_season: int | None = None,
) -> MediaSection | None:
    if not parts:
        return None

    if release.kind == MediaKind.EPISODE and release.season is not None:
        merged = _empty_section_for_release(release)

        for section in parts:
            if section.kind != MediaKind.EPISODE:
                continue
            for _ep, item in section.episodes.items():
                remapped = _remap_item_for_release(
                    item, release, absolute_offset=absolute_offset
                )
                if remapped is None:
                    continue
                if _episode_belongs_to_release(
                    remapped,
                    release,
                    absolute_offset=absolute_offset,
                    max_tv_season=max_tv_season,
                ):
                    upsert_episode(merged, remapped)

        if merged.episodes:
            return merged

        for section in parts:
            if section.kind == MediaKind.EPISODE and section.season == release.season:
                return section
        return _empty_section_for_release(release)

    if release.kind == MediaKind.MOVIE:
        # Ne jamais récupérer une section TV/batch en secours (sinon « Movies »
        # affiche des épisodes de saison). Construire une section film filtrée.
        merged = _empty_section_for_release(release)
        for part in parts:
            for item in list(part.singles):
                if not _movie_belongs_to_release(item, release):
                    continue
                upsert_movie(merged, item)
            for item in part.episodes.values():
                if not _movie_belongs_to_release(item, release):
                    continue
                upsert_movie(merged, item)
        _keep_best_movie_only(merged)
        return merged

    if release.kind in {MediaKind.OVA, MediaKind.SPECIAL}:
        for section in parts:
            if section.kind == release.kind:
                return section

    return None


def _coherence_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load().catalog


def _batch_candidates(section: MediaSection) -> list[ResultItem]:
    from annie.parsing import parse_title

    seen: set[str] = set()
    candidates: list[ResultItem] = []

    def _add(item: ResultItem) -> None:
        if item.entry.magnet in seen:
            return
        title_kind = parse_title(item.entry.title).kind
        if item.parsed.kind != MediaKind.BATCH and title_kind != MediaKind.BATCH:
            return
        seen.add(item.entry.magnet)
        # Repartir d'un parse propre pour ne pas propager un épisode déjà remappé.
        if item.parsed.episode is not None or item.parsed.source_episode is not None:
            base = parse_title(item.entry.title)
            item = ResultItem(
                entry=item.entry,
                parsed=replace(
                    item.parsed,
                    kind=MediaKind.BATCH,
                    episode=base.episode,
                    source_episode=base.source_episode,
                    season=item.parsed.season or base.season,
                ),
                score=item.score,
            )
        candidates.append(item)

    for item in section.singles:
        _add(item)
    for item in section.episodes.values():
        _add(item)
    return candidates


def _batch_coverage(
    batch: ResultItem, expected: int | None
) -> tuple[list[int], float]:
    title = batch.entry.title
    _, episodes = parse_batch_episode_range(title)
    if not expected:
        if not episodes:
            return [], 0.0
        return episodes, 1.0
    if _franchise_batch_covers_full_season(title, episodes, expected):
        covered = list(range(1, expected + 1))
        return covered, 1.0
    if not episodes:
        return [], 0.0
    covered = [episode for episode in episodes if 1 <= episode <= expected]
    return covered, len(covered) / expected


def _batch_pick_score(batch: ResultItem, expected: int | None) -> tuple[float, list[int]]:
    covered, coverage = _batch_coverage(batch, expected)
    if not covered:
        return 0.0, []
    score = coverage * batch.entry.seeders * max(1, batch.parsed.quality)
    return score, covered


def apply_coherent_season_picks(
    section: MediaSection,
    *,
    expected: int | None = None,
    prefer_batch: bool | None = None,
    season_batch_min_coverage: float | None = None,
    coherence_min_share: float | None = None,
) -> None:
    """Privilégie un pack saison cohérent avant les épisodes isolés."""
    if section.kind != MediaKind.EPISODE or not section.episodes:
        return

    cfg = _coherence_cfg()
    if prefer_batch is None:
        prefer_batch = cfg.prefer_season_batch
    if not prefer_batch:
        return

    expected = expected or section.expected_episodes
    min_coverage = (
        season_batch_min_coverage
        if season_batch_min_coverage is not None
        else cfg.season_batch_min_coverage
    )
    min_share = (
        coherence_min_share
        if coherence_min_share is not None
        else cfg.coherence_min_share
    )

    candidates = _batch_candidates(section)
    best_batch: ResultItem | None = None
    best_score = 0.0
    best_covered: list[int] = []

    for batch in candidates:
        score, covered = _batch_pick_score(batch, expected)
        _, coverage = _batch_coverage(batch, expected)
        if coverage >= min_coverage and score > best_score:
            best_score = score
            best_batch = batch
            best_covered = covered

    if best_batch is not None:
        for episode in best_covered:
            section.episodes[episode] = item_for_episode(best_batch, episode)

    report = assess_season_coherence(section, coherence_min_share=min_share)
    if not report.dominant_magnet or report.magnet_coverage < min_share:
        return

    dominant_by_episode: dict[int, ResultItem] = {}
    for batch in candidates:
        if batch.entry.magnet != report.dominant_magnet:
            continue
        covered, _ = _batch_coverage(batch, expected)
        for episode in covered:
            dominant_by_episode[episode] = item_for_episode(batch, episode)
    for episode, item in section.episodes.items():
        if item.entry.magnet == report.dominant_magnet:
            dominant_by_episode.setdefault(episode, item)

    for outlier in report.outliers:
        if outlier.reason != "magnet":
            continue
        replacement = dominant_by_episode.get(outlier.episode)
        if replacement is not None:
            section.episodes[outlier.episode] = replacement


def normalize_section_episodes(
    section: MediaSection,
    expected: int | None,
    *,
    absolute_offset: int = 0,
) -> None:
    """Remappe numérotation absolue franchise (ex. E42 → S2E17 si offset=25)."""
    if not expected or not section.episodes:
        return

    remapped: dict[int, ResultItem] = {}
    for episode, item in section.episodes.items():
        relative: int | None
        if 1 <= episode <= expected:
            relative = episode
        elif absolute_offset > 0 and episode > absolute_offset:
            candidate = episode - absolute_offset
            relative = candidate if 1 <= candidate <= expected else None
        else:
            relative = None

        if relative is None:
            continue

        candidate = item if relative == episode else item_for_episode(item, relative)
        current = remapped.get(relative)
        if _better_episode_pick(candidate, current):
            remapped[relative] = candidate

    if absolute_offset == 0:
        _legacy_contiguous_remap(remapped, section.episodes, expected)

    section.episodes = remapped


def _legacy_contiguous_remap(
    remapped: dict[int, ResultItem],
    source: dict[int, ResultItem],
    expected: int,
) -> None:
    """Heuristique legacy : bloc contigu E26+ quand l'offset MAL est inconnu."""
    nums = sorted(source.keys())
    if not nums or max(nums) <= expected:
        return
    high = [episode for episode in nums if episode > expected]
    if not high or min(high) != expected + 1:
        return
    offset = min(high) - 1
    if offset < 1:
        return
    for episode in high:
        relative = episode - offset
        if not (1 <= relative <= expected):
            continue
        item = item_for_episode(source[episode], relative)
        current = remapped.get(relative)
        if _better_episode_pick(item, current):
            remapped[relative] = item


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


def _gap_search_queries(
    release: MalRelease,
    missing: list[int],
    *,
    absolute_offset: int = 0,
    max_missing: int | None = None,
) -> list[str]:
    cfg = _catalog_cfg()
    if max_missing is None:
        max_missing = cfg.gap_max_missing
    queries: list[str] = []
    shorts: list[str] = []
    for base in release.nyaa_queries:
        if not base:
            continue
        short = base.split(":", 1)[0].strip()
        if short and short not in shorts:
            shorts.append(short)
    for episode in missing[:max_missing]:
        absolute_ep = episode + absolute_offset
        for short in shorts[:1]:
            if release.season is not None:
                se_variant = f"{short} S{release.season:02d}E{episode:02d}"
                if se_variant not in queries:
                    queries.append(se_variant)
            variant = f"{short} {episode:02d}"
            if variant not in queries:
                queries.append(variant)
            if absolute_offset > 0:
                abs_variant = f"{short} {absolute_ep:02d}"
                if abs_variant not in queries:
                    queries.append(abs_variant)
    return queries[: cfg.gap_max_queries]


def _fill_missing_episodes(
    release: MalRelease,
    section: MediaSection,
    *,
    search,
    category: str,
    filter_code: str,
    skip_recap_movies: bool,
    pool: ThreadPoolExecutor | None,
    absolute_offset: int = 0,
    max_missing: int | None = None,
    max_tv_season: int | None = None,
) -> None:
    cfg = _catalog_cfg()
    if max_missing is None:
        max_missing = cfg.gap_max_missing
    expected = release.episode_count
    if not expected or release.kind != MediaKind.EPISODE:
        return
    missing = [ep for ep in range(1, expected + 1) if ep not in section.episodes]
    if not missing:
        return

    gap_queries = _gap_search_queries(
        release, missing, absolute_offset=absolute_offset, max_missing=max_missing
    )
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
            pages=cfg.gap_search_pages,
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
    extra = _pick_section_for_release(
        parts,
        release,
        absolute_offset=absolute_offset,
        max_tv_season=max_tv_season,
    )
    if extra is None:
        return

    for ep, item in extra.episodes.items():
        current = section.episodes.get(ep)
        if _better_episode_pick(item, current):
            section.episodes[ep] = item

    _filter_section_for_release(
        section,
        release,
        absolute_offset=absolute_offset,
        max_tv_season=max_tv_season,
    )
    normalize_section_episodes(section, expected, absolute_offset=absolute_offset)


def fill_catalog_gaps(
    sections: list[MediaSection],
    *,
    search,
    category: str,
    filter_code: str,
    skip_recap_movies: bool = False,
    pool: ThreadPoolExecutor | None = None,
) -> None:
    sparse = [
        section
        for section in sections
        if section.expected_episodes
        and len(section.episodes) < max(1, int(section.expected_episodes * 0.85))
    ]
    if not sparse:
        return

    offsets = franchise_absolute_offsets_from_sections(sections)
    franchise_max_tv_season = max_tv_season(
        [
            MalRelease(
                mal_id=section.mal_id or 0,
                label=section.label,
                kind=section.kind,
                season=section.season,
                episode_count=section.expected_episodes,
                nyaa_queries=section.nyaa_queries or [section.label],
                sort_key=(section.season or 0, section.label.lower()),
            )
            for section in sections
            if section.kind == MediaKind.EPISODE and section.season is not None
        ]
    )

    def _run(executor: ThreadPoolExecutor) -> None:
        futures = [
            executor.submit(
                fill_section_gaps,
                section,
                search=search,
                category=category,
                filter_code=filter_code,
                skip_recap_movies=skip_recap_movies,
                pool=None,
                absolute_offset=offsets.get(
                    section.mal_id or 0, section.absolute_episode_offset
                ),
                max_tv_season=franchise_max_tv_season,
            )
            for section in sparse
        ]
        for future in as_completed(futures):
            future.result()

    if pool is None:
        with ThreadPoolExecutor(max_workers=4) as local_pool:
            _run(local_pool)
    else:
        _run(pool)


def fill_section_gaps(
    section: MediaSection,
    *,
    search,
    category: str,
    filter_code: str,
    skip_recap_movies: bool = False,
    pool: ThreadPoolExecutor | None = None,
    absolute_offset: int | None = None,
    target_episode: int | None = None,
    max_tv_season: int | None = None,
) -> None:
    expected = section.expected_episodes
    if not expected or section.kind != MediaKind.EPISODE:
        return
    if len(section.episodes) >= max(1, int(expected * 0.85)):
        return
    missing = [ep for ep in range(1, expected + 1) if ep not in section.episodes]
    if not missing:
        return
    if target_episode is not None:
        if target_episode not in missing:
            return
        missing = [target_episode]

    release = MalRelease(
        mal_id=section.mal_id or 0,
        label=section.label,
        kind=section.kind,
        season=section.season,
        episode_count=expected,
        nyaa_queries=section.nyaa_queries or [section.label],
        sort_key=(section.season or 0, section.label.lower()),
    )
    offset = (
        section.absolute_episode_offset if absolute_offset is None else absolute_offset
    )
    _fill_missing_episodes(
        release,
        section,
        search=search,
        category=category,
        filter_code=filter_code,
        skip_recap_movies=skip_recap_movies,
        pool=pool,
        absolute_offset=offset,
        max_missing=1 if target_episode is not None else None,
        max_tv_season=max_tv_season,
    )


def build_catalog_from_releases(
    releases: list[MalRelease],
    *,
    search,
    category: str,
    filter_code: str,
    skip_recap_movies: bool = False,
    pool: ThreadPoolExecutor | None = None,
    fill_gaps: bool = False,
    coherent_picks: bool | None = None,
) -> list[MediaSection]:
    from annie.config import AnnieConfig

    app_cfg = AnnieConfig.load()
    cat_cfg = app_cfg.catalog
    sections: list[MediaSection] = []
    seen_magnets: set[str] = set()
    workers = min(app_cfg.nyaa.parallel, max(len(releases), 1))
    absolute_offsets = effective_absolute_offsets(releases)
    franchise_max_tv_season = max_tv_season(releases)

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
    for query in ranked_queries[: cat_cfg.franchise_max_queries]:
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
                pages=cat_cfg.primary_search_pages
                if index < 2
                else cat_cfg.franchise_search_pages,
            ): query
            for index, query in enumerate(unique_queries)
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
        source_queries = list(dict.fromkeys(release.nyaa_queries))
        for query in source_queries:
            if query not in query_entries:
                query_entries[query] = _safe_search(
                    query,
                    search,
                    category=category,
                    filter_code=filter_code,
                    pages=cat_cfg.franchise_search_pages,
                )
        for query in source_queries:
            for entry in query_entries.get(query, []):
                if entry.magnet in local_magnets:
                    continue
                if entry.magnet in seen_magnets and not _magnet_reusable_across_seasons(
                    entry.title
                ):
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
        absolute_offset = absolute_offsets.get(release.mal_id, 0)
        section = _pick_section_for_release(
            parts,
            release,
            absolute_offset=absolute_offset,
            max_tv_season=franchise_max_tv_season,
        )
        if section is None:
            continue

        section.expected_episodes = release.episode_count
        _merge_batches_into_section(
            section,
            parts,
            release,
            absolute_offset=absolute_offset,
            max_tv_season=franchise_max_tv_season,
        )
        _filter_section_for_release(
            section,
            release,
            absolute_offset=absolute_offset,
            max_tv_season=franchise_max_tv_season,
        )
        normalize_section_episodes(
            section, release.episode_count, absolute_offset=absolute_offset
        )
        apply_coherent_season_picks(
            section,
            expected=release.episode_count,
            prefer_batch=coherent_picks,
        )
        if fill_gaps:
            _fill_missing_episodes(
                release,
                section,
                search=search,
                category=category,
                filter_code=filter_code,
                skip_recap_movies=skip_recap_movies,
                pool=pool,
                absolute_offset=absolute_offset,
                max_tv_season=franchise_max_tv_season,
            )
            _filter_section_for_release(
                section,
                release,
                absolute_offset=absolute_offset,
                max_tv_season=franchise_max_tv_season,
            )

        section.key = f"mal:{release.mal_id}"
        section.label = release.label
        section.kind = (
            release.kind if release.kind != MediaKind.UNKNOWN else section.kind
        )
        section.season = release.season
        section.expected_episodes = release.episode_count
        section.mal_id = release.mal_id
        section.absolute_episode_offset = absolute_offset
        section.nyaa_queries = list(release.nyaa_queries)

        for item in section.choices():
            if _magnet_reusable_across_seasons(item.entry.title):
                continue
            seen_magnets.add(item.entry.magnet)

        keep = (
            section.episodes
            or section.singles
            or (
                release.kind == MediaKind.EPISODE
                and release.episode_count
                and release.season is not None
            )
        )
        if keep:
            sections.append(section)

    sections.sort(key=lambda section: section_sort_key(section))
    _annotate_batch_hints(sections)
    return sections


_MOVIE_NUMBER_RE = re.compile(r"\bmovie\s*(?P<num>[1-9])\b", re.I)


def resolve_catalog_target(
    sections: list[MediaSection],
    *,
    season: int | None = None,
    episode: int | None = None,
    kind: MediaKind | None = None,
    movie_number: int | None = None,
) -> ResultItem | None:
    """Résout une cible précise dans un catalogue MAL sans fzf."""
    if episode is not None:
        for section in sections:
            if kind is not None and section.kind != kind:
                continue
            if season is not None and section.season not in {season, None}:
                continue
            item = section.episodes.get(episode)
            if item is not None:
                return item

    if movie_number is not None:
        for section in sections:
            if section.kind != MediaKind.MOVIE:
                continue
            for item in section.singles:
                match = _MOVIE_NUMBER_RE.search(item.entry.title)
                if match and int(match.group("num")) == movie_number:
                    return item

    return None
