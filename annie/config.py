"""Configuration Annie (~/.config/annie/config.toml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from annie.user_config import CONFIG_DIR, CONFIG_FILE, ensure_user_config

_config_cache: AnnieConfig | None = None


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _str_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _load_config_data() -> dict:
    ensure_user_config()
    if not CONFIG_FILE.is_file():
        return {}
    return tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))


@dataclass
class NyaaConfig:
    category: str = "0_0"
    filter_code: str = "0"
    search_pages: int = 2
    parallel: int = 10
    rate: float = 6.0
    rate_burst: int = 8
    cache_ttl_minutes: int = 45
    sort: str = "seeders"
    order: str = "desc"
    timeout: float = 30.0
    retries: int = 4

    @property
    def cache_ttl(self) -> int:
        return max(1, self.cache_ttl_minutes) * 60


@dataclass
class MalConfig:
    enabled: bool = True
    parallel: int = 10
    cache_ttl_hours: int = 168

    @property
    def cache_ttl(self) -> int:
        return max(1, self.cache_ttl_hours) * 3600


@dataclass
class CatalogConfig:
    skip_recap_movies: bool = False
    fill_gaps_on_search: bool = False
    search_results_limit: int = 8
    franchise_max_queries: int = 20
    primary_search_pages: int = 2
    franchise_search_pages: int = 2
    gap_search_pages: int = 1
    gap_max_missing: int = 6
    gap_max_queries: int = 10
    preferred_groups: list[str] = field(default_factory=list)
    preferred_group_bonus: int = 10
    min_seeders_strict: int = 10
    min_seeders_relaxed: int = 3
    min_quality_strict: int = 26
    min_quality_relaxed: int = 12
    coverage_relaxed: float = 0.85
    prefer_season_batch: bool = True
    season_batch_min_coverage: float = 0.85
    coherence_min_share: float = 0.60


@dataclass
class SubtitlesConfig:
    enabled: bool = True
    default_lang: str = ""
    fetch_timeout: float = 20.0
    api_key: str = ""
    username: str = ""
    password: str = ""


@dataclass
class UiConfig:
    seeders_highlight: int = 50
    show_banner: bool = True
    mal_pool_workers: int = 16


@dataclass
class AnnieConfig:
    player: str = "auto"
    nyaa: NyaaConfig = field(default_factory=NyaaConfig)
    mal: MalConfig = field(default_factory=MalConfig)
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    subtitles: SubtitlesConfig = field(default_factory=SubtitlesConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    @property
    def category(self) -> str:
        return self.nyaa.category

    @property
    def filter_code(self) -> str:
        return self.nyaa.filter_code

    @property
    def skip_recap_movies(self) -> bool:
        return self.catalog.skip_recap_movies

    @property
    def preferred_groups(self) -> list[str]:
        return self.catalog.preferred_groups

    @property
    def subtitles_enabled(self) -> bool:
        return self.subtitles.enabled

    @property
    def default_sub_lang(self) -> str:
        return self.subtitles.default_lang

    @property
    def opensubtitles_api_key(self) -> str:
        return self.subtitles.api_key

    @property
    def opensubtitles_username(self) -> str:
        return self.subtitles.username

    @property
    def opensubtitles_password(self) -> str:
        return self.subtitles.password

    @classmethod
    def load(cls) -> AnnieConfig:
        global _config_cache
        if _config_cache is not None:
            return replace(_config_cache)

        data = _load_config_data()
        player_table = _table(data, "player")
        nyaa_table = _table(data, "nyaa")
        mal_table = _table(data, "mal")
        catalog_table = _table(data, "catalog")
        subtitles_table = _table(data, "subtitles")
        ui_table = _table(data, "ui")

        player_raw = data.get("player")
        if isinstance(player_raw, dict):
            player_raw = player_raw.get("command")
        player = os.environ.get("ANNIE_PLAYER") or _str(
            player_raw or player_table.get("command"),
            "auto",
        )

        nyaa = NyaaConfig(
            category=_str(data.get("category") or nyaa_table.get("category"), "0_0"),
            filter_code=_str(
                data.get("filter")
                or data.get("filter_code")
                or nyaa_table.get("filter")
                or nyaa_table.get("filter_code"),
                "0",
            ),
            search_pages=_int(nyaa_table.get("search_pages"), 2),
            parallel=_int(nyaa_table.get("parallel"), 10),
            rate=_float(nyaa_table.get("rate"), 6.0),
            rate_burst=_int(nyaa_table.get("rate_burst"), 8),
            cache_ttl_minutes=_int(nyaa_table.get("cache_ttl_minutes"), 45),
            sort=_str(nyaa_table.get("sort"), "seeders"),
            order=_str(nyaa_table.get("order"), "desc"),
            timeout=_float(nyaa_table.get("timeout"), 30.0),
            retries=_int(nyaa_table.get("retries"), 4),
        )

        mal = MalConfig(
            enabled=_bool(mal_table.get("enabled"), True),
            parallel=_int(mal_table.get("parallel"), 10),
            cache_ttl_hours=_int(mal_table.get("cache_ttl_hours"), 168),
        )

        skip_recap = data.get("skip_recap_movies")
        if skip_recap is None:
            skip_recap = catalog_table.get("skip_recap_movies")

        catalog = CatalogConfig(
            skip_recap_movies=_bool(skip_recap, False),
            fill_gaps_on_search=_bool(
                catalog_table.get("fill_gaps_on_search"), False
            ),
            search_results_limit=_int(
                catalog_table.get("search_results_limit"), 8
            ),
            franchise_max_queries=_int(
                catalog_table.get("franchise_max_queries"), 20
            ),
            primary_search_pages=_int(
                catalog_table.get("primary_search_pages"), 2
            ),
            franchise_search_pages=_int(
                catalog_table.get("franchise_search_pages"), 2
            ),
            gap_search_pages=_int(catalog_table.get("gap_search_pages"), 1),
            gap_max_missing=_int(catalog_table.get("gap_max_missing"), 6),
            gap_max_queries=_int(catalog_table.get("gap_max_queries"), 10),
            preferred_groups=_str_list(
                data.get("preferred_groups")
                or catalog_table.get("preferred_groups")
            ),
            preferred_group_bonus=_int(
                catalog_table.get("preferred_group_bonus"), 10
            ),
            min_seeders_strict=_int(
                catalog_table.get("min_seeders_strict"), 10
            ),
            min_seeders_relaxed=_int(
                catalog_table.get("min_seeders_relaxed"), 3
            ),
            min_quality_strict=_int(
                catalog_table.get("min_quality_strict"), 26
            ),
            min_quality_relaxed=_int(
                catalog_table.get("min_quality_relaxed"), 12
            ),
            coverage_relaxed=_float(
                catalog_table.get("coverage_relaxed"), 0.85
            ),
            prefer_season_batch=_bool(
                catalog_table.get("prefer_season_batch"), True
            ),
            season_batch_min_coverage=_float(
                catalog_table.get("season_batch_min_coverage"), 0.85
            ),
            coherence_min_share=_float(
                catalog_table.get("coherence_min_share"), 0.60
            ),
        )

        subtitles_enabled = data.get("subtitles_enabled")
        if subtitles_enabled is None:
            subtitles_enabled = subtitles_table.get("enabled")

        subtitles = SubtitlesConfig(
            enabled=_bool(subtitles_enabled, True),
            default_lang=_str(
                data.get("default_sub_lang")
                or subtitles_table.get("default_lang")
            ),
            fetch_timeout=_float(subtitles_table.get("fetch_timeout"), 20.0),
            api_key=_str(
                os.environ.get("OPENSUBTITLES_API_KEY")
                or data.get("opensubtitles_api_key")
                or subtitles_table.get("api_key")
            ),
            username=_str(
                os.environ.get("OPENSUBTITLES_USERNAME")
                or data.get("opensubtitles_username")
                or subtitles_table.get("username")
            ),
            password=_str(
                os.environ.get("OPENSUBTITLES_PASSWORD")
                or data.get("opensubtitles_password")
                or subtitles_table.get("password")
            ),
        )

        ui = UiConfig(
            seeders_highlight=_int(ui_table.get("seeders_highlight"), 50),
            show_banner=_bool(ui_table.get("show_banner"), True),
            mal_pool_workers=_int(ui_table.get("mal_pool_workers"), 16),
        )

        _config_cache = cls(
            player=player,
            nyaa=nyaa,
            mal=mal,
            catalog=catalog,
            subtitles=subtitles,
            ui=ui,
        )
        return replace(_config_cache)

    def resolved_player(self, override: str | None = None) -> str | None:
        if override and override != "auto":
            return override
        if self.player != "auto":
            return self.player
        return None


def reload_config() -> AnnieConfig:
    """Invalide le cache (tests ou rechargement manuel)."""
    global _config_cache
    _config_cache = None
    return AnnieConfig.load()
