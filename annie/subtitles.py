"""Sous-titres externes via l'API REST OpenSubtitles.com."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from annie.cache import read_json, write_json
from annie.net import fetch_bytes
from annie.types import MediaKind, ResultItem

USER_AGENT = "Annie/0.5 (+https://github.com/CloudDown/annie)"
API_BASE = "https://api.opensubtitles.com/api/v1"
CACHE_DIR = Path.home() / ".cache" / "annie" / "subs"
TOKEN_CACHE = Path.home() / ".cache" / "annie" / "opensubtitles_token.json"
CACHE_TTL = 7 * 24 * 3600
TOKEN_TTL = 20 * 3600

SUBTITLE_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub"}


class SubtitlesError(RuntimeError):
    """Erreur configuration ou API sous-titres."""


@dataclass(frozen=True)
class SubtitleLanguage:
    code: str
    label: str
    os_id: str  # alias API (ISO 639-1)


LANGUAGES: tuple[SubtitleLanguage, ...] = (
    SubtitleLanguage("en", "English", "en"),
    SubtitleLanguage("zh", "中文", "zh"),
    SubtitleLanguage("hi", "हिन्दी", "hi"),
    SubtitleLanguage("es", "Español", "es"),
    SubtitleLanguage("fr", "Français", "fr"),
)

_OS_BY_CODE = {lang.code: lang for lang in LANGUAGES}


@dataclass(frozen=True)
class SubtitleQuery:
    title: str
    season: int | None = None
    episode: int | None = None
    kind: str = "tv"
    extra_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubtitleCandidate:
    file_id: int
    release: str
    downloads: int = 0


def languages() -> tuple[SubtitleLanguage, ...]:
    return LANGUAGES


def language_for(code: str) -> SubtitleLanguage | None:
    return _OS_BY_CODE.get(code.lower())


def build_query(item: ResultItem, *, series_title: str | None = None) -> SubtitleQuery:
    title = (
        series_title or item.parsed.display_name or item.parsed.series or ""
    ).strip()
    kind = "movie" if item.parsed.kind == MediaKind.MOVIE else "tv"
    extra_titles: list[str] = []
    for candidate in (series_title, item.parsed.display_name, item.parsed.series):
        if candidate and candidate.strip() and candidate.strip() != title:
            extra_titles.append(candidate.strip())
    return SubtitleQuery(
        title=title,
        season=item.parsed.season,
        episode=item.parsed.episode,
        kind=kind,
        extra_titles=tuple(extra_titles),
    )


def _resolve_api_key(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("OPENSUBTITLES_API_KEY", "").strip()
    if env:
        return env
    from annie.config import AnnieConfig

    return AnnieConfig.load().opensubtitles_api_key.strip()


def _resolve_credentials() -> tuple[str, str]:
    from annie.config import AnnieConfig

    config = AnnieConfig.load()
    username = os.environ.get(
        "OPENSUBTITLES_USERNAME", config.opensubtitles_username
    ).strip()
    password = os.environ.get(
        "OPENSUBTITLES_PASSWORD", config.opensubtitles_password
    ).strip()
    return username, password


def _require_api_key(explicit: str | None = None) -> str:
    key = _resolve_api_key(explicit)
    if not key:
        raise SubtitlesError(
            "clé API OpenSubtitles manquante — définissez opensubtitles_api_key dans "
            "~/.config/annie/config.toml (gratuit : https://www.opensubtitles.com/en/consumers)"
        )
    return key


def _api_headers(api_key: str, token: str | None = None) -> dict[str, str]:
    headers = {
        "Api-Key": api_key,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_request(
    method: str,
    path: str,
    *,
    api_key: str,
    token: str | None = None,
    body: dict | None = None,
    params: dict | None = None,
) -> dict:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
    url = f"{API_BASE}{path}{query}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers=_api_headers(api_key, token),
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise SubtitlesError(f"OpenSubtitles HTTP {exc.code}: {detail}") from exc
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def _login(api_key: str) -> str | None:
    username, password = _resolve_credentials()
    if not username or not password:
        return None
    cached = read_json(TOKEN_CACHE, ttl=TOKEN_TTL)
    if isinstance(cached, dict) and cached.get("token"):
        return str(cached["token"])
    payload = _api_request(
        "POST",
        "/login",
        api_key=api_key,
        body={"username": username, "password": password},
    )
    token = payload.get("token")
    if not token:
        return None
    write_json(TOKEN_CACHE, {"token": token, "ts": time.time()})
    return str(token)


def _auth_token(api_key: str) -> str | None:
    try:
        return _login(api_key)
    except SubtitlesError:
        return None


def parse_api_results(payload: dict) -> list[SubtitleCandidate]:
    candidates: list[SubtitleCandidate] = []
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        attributes = row.get("attributes") or {}
        files = attributes.get("files") or []
        if not files:
            continue
        file_id = files[0].get("file_id")
        if file_id is None:
            continue
        release = (
            attributes.get("release")
            or attributes.get("feature_details", {}).get("title")
            or files[0].get("file_name")
            or f"subtitle-{file_id}"
        )
        downloads = int(attributes.get("download_count") or 0)
        candidates.append(
            SubtitleCandidate(
                file_id=int(file_id),
                release=str(release),
                downloads=downloads,
            )
        )
    return candidates


def subtitle_title_variants(
    title: str, *, extra: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Variantes de titre pour OpenSubtitles (indexation souvent plus courte que Nyaa)."""
    from annie.mal import _title_shortcuts

    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        seen.add(key)
        variants.append(cleaned)

    def colon_re_zero(value: str) -> None:
        parts = value.split()
        if (
            len(parts) >= 2
            and parts[0].casefold() == "re"
            and parts[1].casefold().startswith("zero")
        ):
            add(f"{parts[0]}:{parts[1]}")

    add(title)
    for value in extra:
        add(value)

    lowered = title.casefold()
    for sep in (" kara ", " wo "):
        idx = lowered.find(sep)
        if idx > 0:
            add(title[:idx])

    idx_no = lowered.find(" no ")
    if idx_no >= 0:
        tail = title[idx_no + 4 :].strip()
        if tail:
            add(tail)

    if ":" in title:
        head = title.split(":", 1)[0].strip()
        if len(head) >= 3:
            add(head)

    if " - " in title:
        add(title.split(" - ", 1)[0].strip())

    for candidate in list(variants):
        for short in _title_shortcuts(candidate):
            add(short)

    for candidate in list(variants):
        colon_re_zero(candidate)

    return tuple(variants)


def probe_search(
    query: SubtitleQuery,
    lang: SubtitleLanguage,
    *,
    api_key: str | None = None,
) -> list[tuple[str, list[SubtitleCandidate]]]:
    """Essaie chaque variante de titre et retourne les résultats (outil debug)."""
    key = _require_api_key(api_key)
    token = _auth_token(key)
    probed: list[tuple[str, list[SubtitleCandidate]]] = []
    for title in subtitle_title_variants(query.title, extra=query.extra_titles):
        variant = SubtitleQuery(
            title=title,
            season=query.season,
            episode=query.episode,
            kind=query.kind,
        )
        payload = _api_request(
            "GET",
            "/subtitles",
            api_key=key,
            token=token,
            params=search_params(variant, lang),
        )
        probed.append((title, parse_api_results(payload)))
    return probed


def search_params(query: SubtitleQuery, lang: SubtitleLanguage) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "query": query.title,
        "languages": lang.code,
    }
    if query.kind == "movie":
        params["type"] = "movie"
    else:
        if query.season is not None:
            params["season_number"] = query.season
        if query.episode is not None:
            params["episode_number"] = query.episode
    return params


def search(
    query: SubtitleQuery,
    lang: SubtitleLanguage,
    *,
    api_key: str | None = None,
) -> list[SubtitleCandidate]:
    for _title, candidates in probe_search(query, lang, api_key=api_key):
        if candidates:
            return candidates
    return []


def _pick_best(candidates: list[SubtitleCandidate]) -> SubtitleCandidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.downloads, item.file_id))


def _cache_key(query: SubtitleQuery, lang_code: str) -> str:
    payload = f"{query.title}|{query.kind}|{query.season}|{query.episode}|{lang_code}"
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _cache_meta_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _cache_file_path(query: SubtitleQuery, lang_code: str, suffix: str) -> Path:
    return CACHE_DIR / f"{_subtitle_basename(query, lang_code)}{suffix}"


def _subtitle_basename(query: SubtitleQuery, lang_code: str) -> str:
    slug = (
        re.sub(r"[^a-z0-9]+", "-", query.title.casefold()).strip("-")[:48] or "subtitle"
    )
    parts = [slug]
    if query.season is not None:
        parts.append(f"s{query.season:02d}")
    if query.episode is not None:
        parts.append(f"e{query.episode:02d}")
    parts.append(lang_code.casefold())
    return "-".join(parts)


def _read_cache(key: str) -> Path | None:
    meta = read_json(_cache_meta_path(key), ttl=CACHE_TTL)
    if not isinstance(meta, dict):
        return None
    path = Path(meta.get("path", ""))
    if path.is_file():
        return path
    return None


def _write_cache(key: str, path: Path) -> None:
    write_json(_cache_meta_path(key), {"path": str(path), "ts": time.time()})


def _extract_subtitle(data: bytes) -> tuple[bytes, str]:
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [
                name
                for name in archive.namelist()
                if Path(name).suffix.lower() in SUBTITLE_EXT
            ]
            if not names:
                raise ValueError("zip sans fichier sous-titre")
            name = sorted(names, key=lambda n: (len(n), n))[0]
            return archive.read(name), Path(name).suffix.lower()
    if data.lstrip()[:1] in {b"1", b"["} or b"-->" in data[:4096]:
        return data, ".srt"
    return data, ".srt"


def _fetch_download_link(
    candidate: SubtitleCandidate,
    *,
    api_key: str,
    token: str | None,
) -> str:
    payload = _api_request(
        "POST",
        "/download",
        api_key=api_key,
        token=token,
        body={"file_id": candidate.file_id},
    )
    link = payload.get("link")
    if not link:
        raise SubtitlesError("OpenSubtitles: lien de téléchargement absent")
    return str(link)


def download(
    candidate: SubtitleCandidate,
    dest_dir: Path,
    *,
    api_key: str | None = None,
) -> Path:
    key = _require_api_key(api_key)
    token = _auth_token(key)
    dest_dir.mkdir(parents=True, exist_ok=True)
    link = _fetch_download_link(candidate, api_key=key, token=token)
    raw = fetch_bytes(link, user_agent=USER_AGENT)
    content, suffix = _extract_subtitle(raw)
    dest = dest_dir / f"{candidate.file_id}{suffix}"
    dest.write_bytes(content)
    return dest


def fetch_best(
    query: SubtitleQuery,
    lang_code: str,
    *,
    dest_dir: Path | None = None,
    api_key: str | None = None,
) -> Path | None:
    lang = language_for(lang_code)
    if lang is None:
        return None

    key = _require_api_key(api_key)
    cache_key = _cache_key(query, lang.code)
    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    candidates = search(query, lang, api_key=key)
    best = _pick_best(candidates)
    if best is None:
        return None

    out_dir = dest_dir or CACHE_DIR
    path = download(best, out_dir, api_key=key)
    final = _cache_file_path(query, lang.code, path.suffix)
    if path != final:
        final.write_bytes(path.read_bytes())
        path.unlink(missing_ok=True)
        path = final
    _write_cache(cache_key, path)
    return path
