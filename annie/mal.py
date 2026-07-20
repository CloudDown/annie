"""MyAnimeList via Jikan API (franchise → Nyaa queries)."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from annie.cache import read_json, read_json_stale, write_json
from annie.catalog import is_recap_movie
from annie.net import TokenBucket, fetch_json
from annie.parsing import normalize
from annie.paths import cache_dir
from annie.types import MalRelease, MediaKind

JIKAN_BASE = "https://api.jikan.moe/v4"
USER_AGENT = "Annie/0.5 (+https://github.com/CloudDown/annie)"
MAL_PARALLEL = 10
DISK_CACHE_DIR = cache_dir() / "jikan"
DISK_CACHE_TTL = 7 * 24 * 3600


def _mal_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load().mal


def _mal_cache_ttl() -> int:
    return _mal_cfg().cache_ttl

SKIP_MAL_TYPES = frozenset({"Music", "PV", "CM", "Unknown"})
WATCHABLE_TYPES = frozenset({"TV", "Movie", "OVA", "Special", "ONA", "TV Special"})
MAIN_TV_RELATIONS = frozenset(
    {
        "Root",
        "Sequel",
        "Prequel",
        # Films compilation / recut (ex. Gurren Lagann) → série TV.
        # Uniquement si la racine n'est pas déjà une TV (voir _is_main_tv).
        "Alternative Version",
    }
)
FRANCHISE_EXPAND_RELATIONS = frozenset(
    {
        "Sequel",
        "Prequel",
        "Side story",
        "Spin-off",
        "Parent story",
        "Alternative setting",
        "Alternative Version",
        "Summary",
        "Adaptation",
        "Full story",
    }
)
SPLIT_COUR_RE = re.compile(
    r"\b(?:part\s*2|2(?:nd)?\s*cour|second\s*cour|cour\s*2)\b", re.I
)
SPINOFF_MARKERS_RE = re.compile(
    r"\b(nikki|diaries|picture drama|mini anime|chibi|break time|petit|"
    r"bakuen|gaiden|spin[- ]?off)\b",
    re.I,
)
_EXPLOSION_SPINOFF_RE = re.compile(r"\bexplosion\b", re.I)
# Suite / saison dans le titre alors que la query ne le demande pas.
_SEQUEL_TITLE_RE = re.compile(
    r"\b(?:"
    r"season\s*[2-9]|[2-9](?:nd|rd|th)\s*season|final\s*season|"
    r"after\s*story|aragoto|part\s*[2-9]|cour\s*[2-9]|"
    r"2wei|3rei|movie|film|ova|special|recap|picture\s*drama"
    r")\b"
    r"|(?:^|[\s:/-])(?:ii|iii|iv|v)(?:$|[\s:/-])"
    r"|(?:season\s+)?[2-9]\s*$"
    r"|\s+[2-9stw]\s*$",
    re.I,
)
_QUERY_WANTS_SEQUEL_RE = re.compile(
    r"\b(?:"
    r"season\s*[2-9]|[2-9](?:nd|rd|th)\s*season|final|"
    r"after\s*story|aragoto|part\s*[2-9]|movie|ova|special"
    r")\b"
    r"|\s+[2-9]\s*$",
    re.I,
)
# Sous-titre après « : » qui continue la série (pas un spin-off nommé).
_COLON_CONTINUATION_RE = re.compile(
    r"^\s*(?:"
    r"season\s*\d+|\d+(?:nd|rd|th)?\s*season|final(?:\s*season)?|"
    r"part\s*\d+|after\s*story|aragoto|shippuden|shippuuden|the\s+movie|"
    r".+\b(?:war|arc|chapter|cour|saga)\b"
    r")",
    re.I,
)

_response_cache: dict[str, dict] = {}

_jikan_limiter = TokenBucket(rate=4.0, burst=5)


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
    synonyms: tuple[str, ...] = ()
    anilist_id: int | None = None


@dataclass(frozen=True)
class TopAnimeEntry:
    mal_id: int
    rank: int
    title: str
    title_english: str | None
    anime_type: str


def fetch_top_anime(
    limit: int = 100, *, cache_path: Path | None = None
) -> list[TopAnimeEntry]:
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
    disk_cached = read_json(_disk_cache_path(path), ttl=_mal_cache_ttl())
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
            stale = read_json_stale(_disk_cache_path(path))
            if isinstance(stale, dict):
                _response_cache[path] = stale
                return stale
            raise RuntimeError(f"MAL API error ({exc.code})") from exc
        except urllib.error.URLError as exc:
            stale = read_json_stale(_disk_cache_path(path))
            if isinstance(stale, dict):
                _response_cache[path] = stale
                return stale
            raise RuntimeError(f"MAL API unreachable: {exc.reason}") from exc
    if last_error:
        stale = read_json_stale(_disk_cache_path(path))
        if isinstance(stale, dict):
            _response_cache[path] = stale
            return stale
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


def _parse_anime(
    data: dict, *, is_recap: bool = False, via_relation: str = "Root"
) -> MalAnime:
    aired = data.get("aired") or {}
    synonyms = tuple(
        str(s).strip()
        for s in (data.get("title_synonyms") or [])
        if isinstance(s, str) and s.strip()
    )
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
        synonyms=synonyms,
    )


def _search_anime_once(query: str, *, limit: int = 8) -> list[MalAnime]:
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    payload = _get(f"/anime?{params}")
    results: list[MalAnime] = []
    for item in payload.get("data", []):
        anime = _parse_anime(item)
        if anime.type in SKIP_MAL_TYPES:
            continue
        results.append(anime)
    return results


def _fallback_token(query: str) -> str | None:
    """Token le plus long (≥ 4) pour une seule requête de secours."""
    tokens = [token for token in normalize(query).split() if len(token) >= 4]
    if not tokens:
        return None
    return max(tokens, key=len)


def _fuzzy_title_ratio(query: str, title: str) -> float:
    q = normalize(query)
    t = normalize(title)
    if not q or not t:
        return 0.0
    if q == t or _phrase_in(q, t):
        return 1.0
    return SequenceMatcher(None, q, t).ratio()


def _phrase_in(phrase: str, haystack: str) -> bool:
    """Contiguïté au niveau des mots (évite « oshi no ko » ⊂ « hoshi no ko »)."""
    if not phrase or not haystack:
        return False
    if phrase == haystack:
        return True
    words = phrase.split()
    hay = haystack.split()
    if not words or len(words) > len(hay):
        return False
    n = len(words)
    for index in range(len(hay) - n + 1):
        if hay[index : index + n] == words:
            return True
    return False


def _token_matches(token: str, haystack: str) -> bool:
    """Mot entier exact, ou fuzzy / sous-chaîne pour tokens assez longs."""
    if not token or len(token) < 2:
        return False
    words = haystack.split()
    if token in words:
        return True
    if len(token) < 4:
        return False
    for word in words:
        if abs(len(word) - len(token)) > 2:
            continue
        if SequenceMatcher(None, token, word).ratio() >= 0.85:
            return True
    # Sous-chaîne multi-caractères seulement si token long (évite oshi⊂hoshi).
    if len(token) >= 5 and token in haystack:
        return True
    return False


def _significant_title_tokens(anime: MalAnime) -> set[str]:
    title = normalize(anime.title_english or anime.title or "")
    return {token for token in title.split() if len(token) >= 4}


_WEAK_TITLE_TOKENS = frozenset(
    {
        "certain",
        "story",
        "season",
        "anime",
        "movie",
        "special",
        "from",
        "with",
        "this",
        "that",
        "world",
        "another",
        "first",
        "second",
        "third",
        "final",
    }
)


def _tv_title_coherent(root: MalAnime | None, anime: MalAnime) -> bool:
    """Écarte les « sequels » AniList d'une autre série (ex. Chaos;Child, Index)."""
    if root is None or anime.via_relation == "Root":
        return True
    root_title = normalize(root.title_english or root.title or "")
    anime_title = normalize(anime.title_english or anime.title or "")
    if not root_title or not anime_title:
        return True
    if _phrase_in(root_title, anime_title) or _phrase_in(anime_title, root_title):
        return True
    rt = _significant_title_tokens(root)
    at = _significant_title_tokens(anime)
    shared = rt & at
    strong = {
        token
        for token in shared
        if len(token) >= 5 and token not in _WEAK_TITLE_TOKENS
    }
    if len(strong) >= 1 or len(shared - _WEAK_TITLE_TOKENS) >= 2:
        return True
    if SequenceMatcher(None, root_title, anime_title).ratio() >= 0.62:
        return True
    if anime.via_relation in {"Sequel", "Prequel"}:
        for token in rt:
            if (
                len(token) >= 5
                and token not in _WEAK_TITLE_TOKENS
                and token in anime_title
            ):
                return True
    return False


def _is_series_entry(anime: MalAnime) -> bool:
    if anime.type == "TV":
        return True
    # ONA / OVA long-form (Edgerunners, Sakamoto Days, Hellsing Ultimate…).
    if anime.type in {"ONA", "OVA"} and (anime.episodes or 0) >= 3:
        return True
    return False


def _is_named_spinoff_branch(root: MalAnime | None, anime: MalAnime) -> bool:
    """« My Hero Academia: Vigilantes » ≠ suite de la série principale."""
    if root is None or anime.mal_id == root.mal_id or anime.via_relation == "Root":
        return False
    title = anime.title_english or anime.title or ""
    root_title = root.title_english or root.title or ""
    # Exige « : » + espace (sous-titre EN), pas Re:Zero / Code:Breaker.
    if ": " not in title:
        return False
    prefix, suffix = title.split(": ", 1)
    prefix_n = normalize(prefix)
    root_n = normalize(root_title)
    root_base = normalize(root_title.split(":", 1)[0])
    if not (
        prefix_n == root_base
        or _phrase_in(prefix_n, root_n)
        or _phrase_in(root_base, prefix_n)
    ):
        return False
    if _COLON_CONTINUATION_RE.match(suffix.strip()):
        return False
    return True


def _is_main_tv(anime: MalAnime, *, root: MalAnime | None) -> bool:
    if not _is_series_entry(anime) or anime.is_recap or not anime.episodes:
        return False
    rel = anime.via_relation
    if rel not in MAIN_TV_RELATIONS:
        return False
    if _is_named_spinoff_branch(root, anime):
        return False
    if rel == "Alternative Version":
        # TV↔TV retellings (FMA 2003 vs Brotherhood) : pas deux saisons.
        # Générations d'une franchise (Love Live) : titres assez différents → OK.
        if root is not None and _is_series_entry(root) and anime.mal_id != root.mal_id:
            # OVA/ONA racine (Hellsing Ultimate) : ne pas remonter la TV originale.
            if root.type in {"OVA", "ONA"} and anime.type == "TV":
                return False
            root_title = normalize(root.title_english or root.title or "")
            anime_title = normalize(anime.title_english or anime.title or "")
            similarity = SequenceMatcher(None, root_title, anime_title).ratio()
            if similarity >= 0.72:
                return False
            if not _tv_title_coherent(root, anime):
                return False
            return True
    if not _tv_title_coherent(root, anime):
        return False
    return True


def search_anime(query: str, *, limit: int = 8) -> list[MalAnime]:
    """Recherche MAL : query utilisateur, puis un fallback token si besoin."""
    cleaned = normalize(query).strip() or query.strip()
    merged: dict[int, MalAnime] = {}

    try:
        for anime in _search_anime_once(cleaned, limit=limit):
            merged.setdefault(anime.mal_id, anime)
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
            for anime in _search_anime_once(fallback, limit=limit):
                merged.setdefault(anime.mal_id, anime)
        except RuntimeError:
            pass

    ranked = sorted(
        merged.values(),
        key=lambda anime: _score_candidate(anime, query),
        reverse=True,
    )
    return ranked[:limit]


def _score_candidate(anime: MalAnime, query: str) -> int:
    tokens = [token for token in normalize(query).split() if len(token) > 1]
    title = anime.title_english or anime.title or ""
    haystacks = [
        normalize(anime.title),
        normalize(anime.title_english or ""),
        normalize(anime.title_japanese or ""),
        *[normalize(syn) for syn in anime.synonyms if syn],
    ]
    score = 0
    query_wants_movie = bool(re.search(r"\bmovie\b", query, re.I))
    query_wants_sequel = bool(_QUERY_WANTS_SEQUEL_RE.search(query))
    query_wants_special = bool(re.search(r"\b(?:ova|special)\b", query, re.I))
    for token in tokens:
        if any(_token_matches(token, hay) for hay in haystacks):
            score += 120
    qn = normalize(query)
    strong_title_hit = bool(
        qn and any(_phrase_in(qn, hay) or qn == hay for hay in haystacks if hay)
    )
    if qn and strong_title_hit:
        score += 200
        # Titre ≈ query (peu de mots en plus) : favorise la saison 1 / entry root.
        best_hay = min(
            (hay for hay in haystacks if hay and (_phrase_in(qn, hay) or qn == hay)),
            key=len,
            default="",
        )
        if best_hay:
            q_words = set(qn.split())
            extra_words = [
                word
                for word in best_hay.split()
                if word not in q_words and not word.isdigit()
            ]
            score -= min(160, len(extra_words) * 55)
        # Synonyme seul (« Onigiri » aka Demon Slayer) ≪ titre officiel.
        main_blob = normalize(f"{anime.title} {anime.title_english or ''}")
        if main_blob and not (
            _phrase_in(qn, main_blob)
            or qn == main_blob
            or any(_token_matches(token, main_blob) for token in tokens)
        ):
            score -= 220
    else:
        best_fuzzy = max(
            (_fuzzy_title_ratio(query, hay) for hay in haystacks if hay),
            default=0.0,
        )
        if best_fuzzy >= 0.85:
            score += 180
            strong_title_hit = True
        elif best_fuzzy >= 0.72:
            score += 100
    if anime.type == "TV":
        score += 80
    elif anime.type == "ONA" and (anime.episodes or 0) >= 3:
        # Séries ONA (Edgerunners, Sakamoto Days…) ≈ TV.
        score += 80
    elif anime.type == "Movie" and not query_wants_movie:
        score -= 60
    elif anime.type == "Special" and not query_wants_special:
        score -= 220
    elif anime.type == "OVA" and not query_wants_special:
        score -= 120
    if anime.episodes:
        score += min(anime.episodes, 40)
        # Remakes longs (HxH 2011) : seulement si le titre matche vraiment.
        if anime.episodes >= 100 and strong_title_hit:
            score += 80
    if SPINOFF_MARKERS_RE.search(title) or SPINOFF_MARKERS_RE.search(anime.title):
        score -= 400
    if _EXPLOSION_SPINOFF_RE.search(title) and not _EXPLOSION_SPINOFF_RE.search(
        query
    ):
        score -= 400
    if not query_wants_sequel and _SEQUEL_TITLE_RE.search(title):
        score -= 160
    elif re.search(
        r"\bseason\s*[2-9]|\b[2-9](?:nd|rd|th)\s*season|\bpart\s*[2-9]\b"
        r"|\b(?:ii|iii|iv|v)\b|(?:season\s+)?[2-9]\s*$",
        title,
        re.I,
    ):
        score -= 100
    # Léger bonus d'ancienneté seulement pour départager S1 vs suite proche.
    if anime.aired_from and len(anime.aired_from) >= 4 and not query_wants_sequel:
        try:
            year = int(anime.aired_from[:4])
            if 1960 <= year <= 2100 and not _SEQUEL_TITLE_RE.search(title):
                score += max(0, min(20, (2015 - year) // 5))
        except ValueError:
            pass
    return score


def ranked_candidates(
    candidates: list[MalAnime], query: str
) -> list[tuple[int, MalAnime]]:
    ranked = sorted(
        ((_score_candidate(anime, query), anime) for anime in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    return ranked


def is_ambiguous_pick(candidates: list[MalAnime], query: str) -> bool:
    """Vrai si le top-2 est proche ou le meilleur fuzzy est faible."""
    if len(candidates) < 2:
        return False
    ranked = ranked_candidates(candidates, query)
    top_score, top = ranked[0]
    second_score = ranked[1][0]
    if top_score - second_score < 100:
        return True
    haystacks = [
        normalize(top.title),
        normalize(top.title_english or ""),
        normalize(top.title_japanese or ""),
        *[normalize(syn) for syn in top.synonyms if syn],
    ]
    best_fuzzy = max(
        (_fuzzy_title_ratio(query, hay) for hay in haystacks if hay),
        default=0.0,
    )
    return best_fuzzy < 0.82 and top_score < 380


def pick_candidate(candidates: list[MalAnime], query: str) -> MalAnime | None:
    if not candidates:
        return None
    ranked = ranked_candidates(candidates, query)
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
                    synonyms=tuple(dict.fromkeys([*cur.synonyms, *nxt.synonyms])),
                    anilist_id=cur.anilist_id,
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

    def _run_batch(
        batch: list[tuple[int, bool, str]], executor: ThreadPoolExecutor
    ) -> None:
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
            executor.submit(_fetch_anime_full, mal_id): (
                mal_id,
                from_recap,
                via_relation,
            )
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
        with ThreadPoolExecutor(max_workers=_mal_cfg().parallel) as local_pool:
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
    words = cleaned.split()
    if len(words) <= 2:
        first = words[0]
        if len(first) >= 3 and first not in shortcuts:
            shortcuts.append(first)
    return shortcuts


def _franchise_short_query(user_query: str, anime: MalAnime) -> str:
    if user_query.strip():
        return user_query.strip()
    title = (anime.title_english or anime.title or "").strip()
    if ": " in title:
        return title.split(": ", 1)[0].strip()
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    return title


def nyaa_queries_for(
    anime: MalAnime,
    *,
    user_query: str = "",
    season: int | None = None,
) -> list[str]:
    queries: list[str] = []
    if user_query.strip():
        queries.append(user_query.strip())

    season_variants: list[str] = []
    if season is not None:
        short = _franchise_short_query(user_query, anime)
        if short:
            ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(season, f"{season}th")
            for variant in (
                f"{short} S{season:02d}",
                f"{short} Season {season:02d}",
                f"{short} {ordinal} Season",
            ):
                if variant not in season_variants:
                    season_variants.append(variant)
            if anime.episodes and anime.episodes <= 52:
                batch_variant = f"{short} batch"
                if batch_variant not in season_variants:
                    season_variants.append(batch_variant)

    for value in (
        anime.title_english,
        anime.title,
        anime.title_japanese,
        *anime.synonyms,
    ):
        if not value:
            continue
        if value not in queries:
            queries.append(value)
        for short in _title_shortcuts(value):
            if short not in queries:
                queries.append(short)

    insert_at = 1 if queries else 0
    for variant in season_variants:
        if variant not in queries:
            queries.insert(insert_at, variant)
            insert_at += 1

    return queries[:14]


def _short_title_for_label(anime: MalAnime) -> str:
    raw = (anime.title_english or anime.title or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s*[\[(].*$", "", raw).strip()
    if ": " in raw:
        # Garder le titre principal si le sous-titre est long.
        head, tail = raw.split(": ", 1)
        if len(tail) > 28 and len(head) >= 3:
            raw = head
    if len(raw) > 36:
        return raw[:33] + "…"
    return raw


def _season_release_label(index: int, anime: MalAnime) -> str:
    parts = [f"Season {index:02d}"]
    year = (anime.aired_from or "")[:4]
    if year.isdigit():
        parts.append(year)
    if anime.episodes:
        parts.append(f"{anime.episodes} ep")
    short = _short_title_for_label(anime)
    if short:
        parts.append(short)
    return " · ".join(parts)


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
            if _is_main_tv(anime, root=root)
        ],
        key=lambda anime: anime.aired_from or "9999",
    )
    tv_seasons = _merge_split_cours_tv(tv)
    movies = sorted(
        [anime for anime in entries if anime.type == "Movie"],
        key=lambda anime: anime.aired_from or "9999",
    )
    extras = sorted(
        [
            anime
            for anime in entries
            if anime.type not in {"TV", "Movie"} and not _is_main_tv(anime, root=root)
        ],
        key=lambda anime: anime.aired_from or "9999",
    )

    releases: list[MalRelease] = []
    cursor = 0
    for index, (anime, part2) in enumerate(tv_seasons, start=1):
        queries: list[str] = []
        for value in (
            *nyaa_queries_for(anime, user_query=user_query, season=index),
            *(
                nyaa_queries_for(part2, user_query=user_query, season=index)
                if part2
                else ()
            ),
            root_query,
        ):
            if value and value not in queries:
                queries.append(value)
        releases.append(
            MalRelease(
                mal_id=anime.mal_id,
                label=_season_release_label(index, anime),
                kind=MediaKind.EPISODE,
                season=index,
                episode_count=anime.episodes,
                nyaa_queries=queries,
                sort_key=(index * 10, anime.title.lower()),
                absolute_episode_offset=cursor,
            )
        )
        cursor += anime.episodes or 0

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
