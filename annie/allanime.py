"""AllAnime GraphQL — structure saisons/films (comme ani-cli)."""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

from annie.cache import read_json, write_json
from annie.mal import MalAnime, nyaa_queries_for
from annie.net import TokenBucket, fetch_json_post
from annie.parsing import normalize, query_tokens
from annie.paths import cache_dir
from annie.catalog import is_recap_movie
from annie.releases import extra_release, movie_release, tv_release
from annie.types import MalRelease, MediaKind

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_ORIGIN = "https://youtu-chan.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
    "Gecko/20100101 Firefox/150.0"
)
DISK_CACHE_DIR = cache_dir() / "allanime"
DISK_CACHE_TTL = 6 * 3600

_SEARCH_GQL = """
query(
  $search: SearchInput
  $limit: Int
  $page: Int
  $translationType: VaildTranslationTypeEnumType
  $countryOrigin: VaildCountryOriginEnumType
) {
  shows(
    search: $search
    limit: $limit
    page: $page
    translationType: $translationType
    countryOrigin: $countryOrigin
  ) {
    edges { _id name availableEpisodes __typename }
  }
}
"""

_limiter = TokenBucket(rate=4.0, burst=6)
_response_cache: dict[str, dict] = {}

_MOVIE_RE = re.compile(r"\b(?:movie|gekijouban|film|the\s+movie)\b", re.I)
_OVA_RE = re.compile(r"\b(?:ova|ona|special|bonus\s*stage|o\.v\.a)\b", re.I)
_JUNK_RE = re.compile(
    r"\b(?:camrip|hdcam|cam\b|ts\b|tc\b|workprint|sample)\b",
    re.I,
)
_SPINOFF_MARKERS = re.compile(
    r"\b(?:alternative|gaiden|spin[\s-]?off|recap|compilation)\b",
    re.I,
)
_STEM_NOISE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}(?:st|nd|rd|th)?"
    r"|season\s*\d+"
    r"|s\d+"
    r"|movie|gekijouban|film|ova|ona|special|bonus|stage"
    r"|part\s*\d+"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class AllAnimeShow:
    show_id: str
    name: str
    sub_episodes: int
    dub_episodes: int = 0

    @property
    def episode_count(self) -> int:
        return self.sub_episodes or self.dub_episodes or 0


def _cache_path(key: str):
    safe = re.sub(r"[^\w.-]+", "_", key)[:120]
    return DISK_CACHE_DIR / f"{safe}.json"


def _graphql(variables: dict, *, cache_key: str | None = None) -> dict:
    if cache_key and cache_key in _response_cache:
        return _response_cache[cache_key]
    if cache_key:
        cached = read_json(_cache_path(cache_key), ttl=DISK_CACHE_TTL)
        if isinstance(cached, dict) and cached.get("data") is not None:
            _response_cache[cache_key] = cached
            return cached

    _limiter.acquire()
    body = {"query": _SEARCH_GQL, "variables": variables}
    data = fetch_json_post(
        ALLANIME_API,
        body=body,
        user_agent=USER_AGENT,
        timeout=25,
        extra_headers={
            "Origin": ALLANIME_ORIGIN,
            "Referer": ALLANIME_ORIGIN + "/",
        },
    )
    if cache_key and isinstance(data, dict):
        _response_cache[cache_key] = data
        write_json(_cache_path(cache_key), data)
    return data


def search_shows(
    query: str,
    *,
    limit: int = 40,
    mode: str = "sub",
) -> list[AllAnimeShow]:
    """Recherche AllAnime (même API qu'ani-cli)."""
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    payload = _graphql(
        {
            "search": {
                "allowAdult": False,
                "allowUnknown": False,
                "query": cleaned,
            },
            "limit": limit,
            "page": 1,
            "translationType": mode,
            "countryOrigin": "ALL",
        },
        cache_key=f"search:{mode}:{cleaned.lower()}",
    )
    edges = (
        (payload.get("data") or {}).get("shows") or {}
    ).get("edges") or []
    shows: list[AllAnimeShow] = []
    for edge in edges:
        show_id = str(edge.get("_id") or "").strip()
        name = str(edge.get("name") or "").strip()
        if not show_id or not name:
            continue
        eps = edge.get("availableEpisodes") or {}
        sub = int(eps.get("sub") or 0)
        dub = int(eps.get("dub") or 0)
        if sub < 1 and dub < 1:
            continue
        shows.append(
            AllAnimeShow(
                show_id=show_id,
                name=name,
                sub_episodes=sub,
                dub_episodes=dub,
            )
        )
    return shows


def _synthetic_mal_id(show_id: str) -> int:
    digest = zlib.adler32(show_id.encode("utf-8")) & 0x7FFFFFFF
    return -digest if digest else -1


def _franchise_stem(name: str) -> list[str]:
    cleaned = _STEM_NOISE_RE.sub(" ", normalize(name))
    tokens = [
        t
        for t in query_tokens(cleaned)
        if len(t) >= 3 and t not in {"the", "and", "wo", "ni", "no", "wa"}
    ]
    return tokens[:8]


def _stem_overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def _infer_season(name: str) -> int | None:
    # Ignore « Part 2 » (split-cour), ce n'est pas une saison.
    cleaned = re.sub(r"\bparts?\s*\d+\b", " ", name, flags=re.I)
    cleaned = re.sub(r"\bcour\s*\d+\b", " ", cleaned, flags=re.I)
    # « 2nd Season », « Season 2 », « S2 »
    explicit = re.search(
        r"(?i)\b(?:(\d{1,2})(?:st|nd|rd|th)\s*season|season\s*(\d{1,2})|s(\d{1,2}))\b",
        cleaned,
    )
    if explicit:
        for g in explicit.groups():
            if g:
                value = int(g)
                if 1 <= value <= 40:
                    return value
    # Romain en fin : « Sword Art Online II »
    roman = re.search(r"\b([IVX]{1,4})\b\s*$", cleaned.strip())
    if roman:
        table = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
        value = table.get(roman.group(1).upper())
        if value:
            return value
    # « … wo! 2 », « … 3: » — chiffre de saison (pas un « 0 » film).
    tail = re.search(
        r"(?:^|[\s!！])(?P<n>[2-9]|1[0-9])(?:\s*[:：\-]|\s*$)",
        cleaned.strip(),
    )
    if tail and not _MOVIE_RE.search(name):
        return int(tail.group("n"))
    return None


def _infer_kind(show: AllAnimeShow) -> MediaKind:
    name = show.name
    if _JUNK_RE.search(name):
        return MediaKind.UNKNOWN
    if _MOVIE_RE.search(name):
        return MediaKind.MOVIE
    # Films one-shot sans le mot movie (JJK 0, Code: White…).
    if show.episode_count == 1 and (
        re.search(r"(?<![\w.])0(?![\w.])", name)
        or re.search(r"\bcode:\s*white\b", name, re.I)
        or re.search(r"\b(?:gekijou|gekijo)\b", name, re.I)
    ):
        return MediaKind.MOVIE
    if _OVA_RE.search(name):
        return MediaKind.OVA
    # One-shots / extras (1–2 ep) → pas une saison TV.
    if show.episode_count <= 2:
        return MediaKind.SPECIAL
    return MediaKind.EPISODE


def _movie_label(name: str) -> str:
    if ":" in name:
        return name.split(":", 1)[1].strip() or name
    match = re.search(r"\bMovie\s*:?\s*(.+)$", name, re.I)
    if match:
        return match.group(1).strip()
    return name


def _show_to_queries(
    show: AllAnimeShow,
    *,
    user_query: str,
    chosen: MalAnime | None,
    season: int | None,
) -> list[str]:
    queries: list[str] = []
    if user_query.strip():
        queries.append(user_query.strip())
    queries.append(show.name)
    # Variantes utiles Nyaa.
    englishish = show.name
    if ":" in englishish:
        queries.append(englishish.split(":", 1)[0].strip())
    if chosen is not None:
        for title in (
            chosen.title_english,
            chosen.title,
            *chosen.synonyms[:6],
        ):
            if title and str(title).strip():
                queries.append(str(title).strip())
        # Réutilise les variantes saison MAL si possible.
        fake = MalAnime(
            mal_id=_synthetic_mal_id(show.show_id),
            title=show.name,
            title_english=show.name,
            title_japanese=None,
            type="Movie" if _infer_kind(show) == MediaKind.MOVIE else "TV",
            episodes=show.episode_count or None,
            aired_from=None,
            synonyms=chosen.synonyms,
        )
        queries.extend(
            nyaa_queries_for(fake, user_query=user_query, season=season)[:8]
        )
    # Déduplique en gardant l'ordre.
    return list(dict.fromkeys(q for q in queries if q and str(q).strip()))


def _score_show_against_chosen(
    show: AllAnimeShow,
    *,
    chosen: MalAnime | None,
    user_query: str,
) -> float:
    """Score d'alignement show AllAnime ↔ anime choisi (AniList/MAL)."""
    if _JUNK_RE.search(show.name) or _infer_kind(show) == MediaKind.UNKNOWN:
        return -1.0
    hay = normalize(show.name)
    stem = _franchise_stem(show.name)
    score = 0.0
    titles: list[str] = []
    if chosen is not None:
        for title in (
            chosen.title_english,
            chosen.title,
            chosen.title_japanese,
            *chosen.synonyms[:6],
        ):
            if title and str(title).strip():
                titles.append(str(title).strip())
    if user_query.strip():
        titles.append(user_query.strip())

    for title in titles:
        norm = normalize(title)
        if not norm:
            continue
        if hay == norm:
            score = max(score, 100.0)
        elif norm in hay or hay in norm:
            score = max(score, 70.0 + 10.0 * _stem_overlap(stem, _franchise_stem(title)))
        else:
            score = max(score, 40.0 * _stem_overlap(stem, _franchise_stem(title)))

    # Pénalise les spinoffs si le pick principal n'en est pas un.
    chosen_blob = normalize(" ".join(titles[:3]))
    if _SPINOFF_MARKERS.search(show.name) and not _SPINOFF_MARKERS.search(chosen_blob):
        score -= 40.0
    if re.search(r"\balternative\b", show.name, re.I) and "alternative" not in chosen_blob:
        score -= 50.0
    return score


def filter_franchise_shows(
    shows: list[AllAnimeShow],
    *,
    chosen: MalAnime | None,
    user_query: str,
) -> list[AllAnimeShow]:
    """Garde les shows de la même franchise que le pick AniList/MAL."""
    if not shows:
        return []

    ranked = sorted(
        (
            (
                _score_show_against_chosen(
                    show, chosen=chosen, user_query=user_query
                ),
                show,
            )
            for show in shows
        ),
        key=lambda row: row[0],
        reverse=True,
    )
    ranked = [(score, show) for score, show in ranked if score >= 25.0]
    if not ranked:
        return []

    best_score, best = ranked[0]
    # Stem = nom AllAnime du meilleur show (pas le titre EN AniList :
    # « KONOSUBA God's blessing… » ≠ « Kono Subarashii Sekai… »).
    root_stem = _franchise_stem(best.name)
    if chosen is not None and chosen.title:
        jp_stem = _franchise_stem(chosen.title)
        if jp_stem and _stem_overlap(jp_stem, root_stem) >= 0.5:
            root_stem = jp_stem

    kept: list[AllAnimeShow] = []
    for score, show in ranked:
        if score < max(30.0, best_score * 0.45):
            continue
        stem = _franchise_stem(show.name)
        sa, sb = set(stem), set(root_stem)
        shared = len(sa & sb)
        diff = len(sa ^ sb)
        kind = _infer_kind(show)
        if kind == MediaKind.UNKNOWN:
            continue
        # TV : stem très proche du pick.
        if kind == MediaKind.EPISODE:
            if shared >= 3 and diff <= 2:
                kept.append(show)
            elif shared >= 2 and _stem_overlap(stem, root_stem) >= 0.75:
                kept.append(show)
            continue
        # Films / OVA / specials : un peu plus souple.
        if shared >= 2 and _stem_overlap(stem, root_stem) >= 0.5:
            kept.append(show)
    return kept or [best]


def shows_to_releases(
    shows: list[AllAnimeShow],
    *,
    user_query: str = "",
    chosen: MalAnime | None = None,
    skip_recap: bool = False,
) -> list[MalRelease]:
    """Convertit des shows AllAnime discrets en MalRelease pour Nyaa."""
    if not shows:
        return []

    tv_rows: list[tuple[AllAnimeShow, int | None]] = []
    movies: list[AllAnimeShow] = []
    extras: list[AllAnimeShow] = []

    tv_candidates: list[AllAnimeShow] = []
    for show in shows:
        kind = _infer_kind(show)
        if kind == MediaKind.UNKNOWN:
            continue
        if skip_recap and is_recap_movie(show.name):
            continue
        if kind == MediaKind.MOVIE:
            movies.append(show)
        elif kind in {MediaKind.OVA, MediaKind.SPECIAL}:
            extras.append(show)
        else:
            tv_candidates.append(show)

    # « Part 2 » (split-cour) : ignorer s'il existe déjà la saison de base.
    has_base = any(
        not re.search(r"\bparts?\s*\d+\b", s.name, re.I) for s in tv_candidates
    )
    for show in tv_candidates:
        if has_base and re.search(r"\bparts?\s*[2-9]\b", show.name, re.I):
            if _infer_season(show.name) is None:
                continue
        tv_rows.append((show, _infer_season(show.name)))

    # Assigne S1 aux shows TV sans numéro ; fusionne les doublons de saison.
    numbered = [(s, n) for s, n in tv_rows if n is not None]
    unnumbered = [s for s, n in tv_rows if n is None]
    used: set[int] = set()
    by_season: dict[int, AllAnimeShow] = {}

    def _prefer(a: AllAnimeShow, b: AllAnimeShow) -> AllAnimeShow:
        """Préfère le titre « Season N » / plus d'épisodes."""
        a_explicit = bool(re.search(r"(?i)\bseason\s*\d|\d(?:st|nd|rd|th)\s*season", a.name))
        b_explicit = bool(re.search(r"(?i)\bseason\s*\d|\d(?:st|nd|rd|th)\s*season", b.name))
        if a_explicit != b_explicit:
            return a if a_explicit else b
        return a if a.episode_count >= b.episode_count else b

    for show, num in sorted(numbered, key=lambda row: row[1] or 99):
        assert num is not None
        if num in by_season:
            by_season[num] = _prefer(by_season[num], show)
        else:
            by_season[num] = show
        used.add(num)
    next_season = 1
    def _unnumbered_key(show: AllAnimeShow) -> tuple:
        # Saison racine avant les suites sans numéro explicite.
        partish = 1 if re.search(r"\bparts?\s*\d+", show.name, re.I) else 0
        return (partish, -show.episode_count, show.name)

    for show in sorted(unnumbered, key=_unnumbered_key):
        while next_season in used:
            next_season += 1
        by_season[next_season] = show
        used.add(next_season)
        next_season += 1
    assigned = [(show, season) for season, show in sorted(by_season.items())]

    releases: list[MalRelease] = []
    cursor = 0
    for show, season in assigned:
        queries = _show_to_queries(
            show, user_query=user_query, chosen=chosen, season=season
        )
        label = f"Season {season:02d} · {show.episode_count} ep · {show.name}"
        releases.append(
            tv_release(
                mal_id=_synthetic_mal_id(show.show_id),
                label=label,
                season=season,
                episode_count=show.episode_count or None,
                nyaa_queries=queries,
                sort_key=(season * 10, show.name.lower()),
                absolute_episode_offset=cursor,
            )
        )
        cursor += show.episode_count or 0

    for index, show in enumerate(movies, start=1):
        queries = _show_to_queries(
            show, user_query=user_query, chosen=chosen, season=None
        )
        releases.append(
            movie_release(
                mal_id=_synthetic_mal_id(show.show_id),
                label=_movie_label(show.name),
                nyaa_queries=queries,
                sort_key=(15 + index, show.name.lower()),
                episode_count=show.episode_count or 1,
            )
        )

    for show in extras:
        kind = _infer_kind(show)
        queries = _show_to_queries(
            show, user_query=user_query, chosen=chosen, season=None
        )
        label = show.name
        if kind == MediaKind.OVA:
            label = f"OVA · {label}"
        releases.append(
            extra_release(
                mal_id=_synthetic_mal_id(show.show_id),
                label=label,
                kind=kind,
                nyaa_queries=queries,
                sort_key=(25, show.name.lower()),
                episode_count=show.episode_count or None,
            )
        )

    releases.sort(key=lambda release: release.sort_key)
    return releases


def releases_for_query(
    query: str,
    *,
    chosen: MalAnime | None = None,
    skip_recap: bool = False,
    limit: int = 40,
) -> list[MalRelease]:
    """Search AllAnime → filtre franchise → MalRelease (structure ani-cli)."""
    seen: dict[str, AllAnimeShow] = {}
    # Titres AniList/MAL d'abord (plus précis que la query utilisateur courte).
    for q in dict.fromkeys(
        [
            (chosen.title_english if chosen else None) or "",
            (chosen.title if chosen else None) or "",
            query,
        ]
    ):
        if not q or not str(q).strip():
            continue
        for show in search_shows(str(q).strip(), limit=limit):
            seen.setdefault(show.show_id, show)
    shows = list(seen.values())
    shows = filter_franchise_shows(shows, chosen=chosen, user_query=query)
    return shows_to_releases(
        shows, user_query=query, chosen=chosen, skip_recap=skip_recap
    )


def rank_shows_for_picker(
    shows: list[AllAnimeShow],
    *,
    user_query: str,
) -> list[AllAnimeShow]:
    """Trie les shows pour fzf (meilleurs matches en tête), sans filtrage franchise."""
    ranked = sorted(
        (
            (
                _score_show_against_chosen(
                    show, chosen=None, user_query=user_query
                ),
                show,
            )
            for show in shows
            if _infer_kind(show) != MediaKind.UNKNOWN
        ),
        key=lambda row: (-row[0], row[1].name.lower()),
    )
    return [show for _score, show in ranked]


def show_to_release(
    show: AllAnimeShow,
    *,
    user_query: str = "",
    chosen: MalAnime | None = None,
) -> MalRelease | None:
    """Un show AllAnime → une seule MalRelease (Nyaa scopé)."""
    releases = shows_to_releases(
        [show], user_query=user_query, chosen=chosen, skip_recap=False
    )
    return releases[0] if releases else None


def enrich_release_queries(
    release: MalRelease,
    *,
    query: str,
    show_name: str = "",
) -> MalRelease:
    """Ajoute synonymes AniList/MAL + offset absolu franchise si match titre."""
    from dataclasses import replace

    from annie import metadata as meta
    from annie.catalog import franchise_absolute_offsets
    from annie.mal import franchise_to_releases, pick_candidate

    titles = [t for t in (show_name, release.label, query) if t and str(t).strip()]
    chosen: MalAnime | None = None
    for title in titles:
        try:
            cands = meta.search_anime(str(title).strip(), limit=6)
        except Exception:
            continue
        if not cands:
            continue
        picked = pick_candidate(cands, str(title).strip())
        if picked is None:
            continue
        # Garde seulement si le pick ressemble au show AllAnime.
        blob = normalize(
            f"{picked.title_english or ''} {picked.title} {show_name} {release.label}"
        )
        show_stem = _franchise_stem(show_name or release.label)
        pick_stem = _franchise_stem(picked.title_english or picked.title)
        if _stem_overlap(show_stem, pick_stem) < 0.4 and not (
            normalize(show_name) in blob or normalize(release.label) in blob
        ):
            continue
        chosen = picked
        break

    if chosen is None:
        return release

    extra: list[str] = []
    for title in (
        chosen.title_english,
        chosen.title,
        chosen.title_japanese,
        *chosen.synonyms[:8],
    ):
        if title and str(title).strip():
            extra.append(str(title).strip())
    merged = list(dict.fromkeys([*release.nyaa_queries, *extra]))
    updates: dict = {"nyaa_queries": merged}

    # Offset absolu : sans ça, un seul show AllAnime S2 a offset=0 et
    # accepte les E01 saisonless de la S1.
    try:
        franchise = meta.collect_franchise(chosen)
        fr_releases = franchise_to_releases(
            franchise, root_id=chosen.mal_id, user_query=query
        )
        offsets = franchise_absolute_offsets(fr_releases)
        match = None
        if release.season is not None:
            for row in fr_releases:
                if row.kind != MediaKind.EPISODE:
                    continue
                if row.season == release.season:
                    match = row
                    break
        if match is None and release.kind == MediaKind.EPISODE:
            # Fallback : meilleur overlap de titre.
            best_score = 0.0
            for row in fr_releases:
                if row.kind != MediaKind.EPISODE:
                    continue
                score = _stem_overlap(
                    _franchise_stem(show_name or release.label),
                    _franchise_stem(row.label),
                )
                if score > best_score:
                    best_score = score
                    match = row
        if match is not None:
            updates["absolute_episode_offset"] = max(
                release.absolute_episode_offset,
                match.absolute_episode_offset,
                offsets.get(match.mal_id, 0),
            )
            if match.episode_count and not release.episode_count:
                updates["episode_count"] = match.episode_count
            if match.mal_id > 0:
                updates["mal_id"] = match.mal_id
    except Exception:
        pass

    return replace(release, **updates)
