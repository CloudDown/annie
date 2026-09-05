"""Configuration Annie (~/.config/annie/config.toml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from annie import toml_util
from annie.user_config import CONFIG_FILE, ensure_user_config

_config_cache: AnnieConfig | None = None


def _load_config_data() -> dict:
    ensure_user_config()
    return toml_util.read_toml(CONFIG_FILE)


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


def _metadata_mode_defaults(mode: str) -> tuple[bool, str, str]:
    """Defaults (enabled, provider, structure) pour metadata.mode."""
    if mode == "off":
        return False, "anilist", "allanime"
    if mode == "anilist":
        return True, "anilist", "franchise"
    if mode == "mal":
        return True, "mal", "franchise"
    return True, "anilist", "allanime"


@dataclass
class MetadataConfig:
    """Source métadonnées franchise (saisons / titres / synonymes)."""

    enabled: bool = True
    mode: str = "auto"  # auto | anilist | mal | off
    provider: str = "anilist"  # anilist | mal
    # Découpe saisons/films : AllAnime (ani-cli) ou graphe AniList/MAL.
    structure: str = "allanime"  # allanime | franchise
    fallback_mal: bool = True
    fallback_anilist: bool = False
    confirm_ambiguous: bool = True
    parallel: int = 8
    cache_ttl_hours: int = 168

    @property
    def cache_ttl(self) -> int:
        return max(1, self.cache_ttl_hours) * 3600


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
    show_download_progress: bool = True


@dataclass
class AnnieConfig:
    player: str = "auto"
    nyaa: NyaaConfig = field(default_factory=NyaaConfig)
    mal: MalConfig = field(default_factory=MalConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
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
        player_table = toml_util.table(data, "player")
        nyaa_table = toml_util.table(data, "nyaa")
        mal_table = toml_util.table(data, "mal")
        metadata_table = toml_util.table(data, "metadata")
        catalog_table = toml_util.table(data, "catalog")
        subtitles_table = toml_util.table(data, "subtitles")
        ui_table = toml_util.table(data, "ui")

        player_raw = data.get("player")
        if isinstance(player_raw, dict):
            player_raw = player_raw.get("command")
        player = os.environ.get("ANNIE_PLAYER") or toml_util.str_val(
            player_raw or player_table.get("command"),
            "auto",
        )

        nyaa = NyaaConfig(
            category=toml_util.str_val(
                data.get("category") or nyaa_table.get("category"), "0_0"
            ),
            filter_code=toml_util.str_val(
                data.get("filter")
                or data.get("filter_code")
                or nyaa_table.get("filter")
                or nyaa_table.get("filter_code"),
                "0",
            ),
            search_pages=toml_util.int_val(nyaa_table.get("search_pages"), 2),
            parallel=toml_util.int_val(nyaa_table.get("parallel"), 10),
            rate=toml_util.float_val(nyaa_table.get("rate"), 6.0),
            rate_burst=toml_util.int_val(nyaa_table.get("rate_burst"), 8),
            cache_ttl_minutes=toml_util.int_val(
                nyaa_table.get("cache_ttl_minutes"), 45
            ),
            sort=toml_util.str_val(nyaa_table.get("sort"), "seeders"),
            order=toml_util.str_val(nyaa_table.get("order"), "desc"),
            timeout=toml_util.float_val(nyaa_table.get("timeout"), 30.0),
            retries=toml_util.int_val(nyaa_table.get("retries"), 4),
        )

        mal = MalConfig(
            enabled=toml_util.bool_val(mal_table.get("enabled"), True),
            parallel=toml_util.int_val(mal_table.get("parallel"), 10),
            cache_ttl_hours=toml_util.int_val(mal_table.get("cache_ttl_hours"), 168),
        )

        top_meta = data.get("metadata")
        mode = toml_util.str_val(
            os.environ.get("ANNIE_METADATA_MODE")
            or (top_meta if isinstance(top_meta, str) else None)
            or metadata_table.get("mode"),
            "auto",
        ).lower()
        if mode not in {"auto", "anilist", "mal", "off"}:
            mode = "auto"
        enabled_default, provider_default, structure_default = (
            _metadata_mode_defaults(mode)
        )
        provider = toml_util.str_val(
            os.environ.get("ANNIE_METADATA_PROVIDER")
            or metadata_table.get("provider"),
            provider_default,
        ).lower()
        if provider not in {"anilist", "mal"}:
            provider = provider_default
        structure = toml_util.str_val(
            os.environ.get("ANNIE_METADATA_STRUCTURE")
            or metadata_table.get("structure"),
            structure_default,
        ).lower()
        if structure not in {"allanime", "franchise"}:
            structure = structure_default
        meta_enabled = metadata_table.get("enabled")
        if meta_enabled is None:
            if mode == "off":
                meta_enabled = False
            elif mode in {"anilist", "mal"}:
                meta_enabled = True
            else:
                meta_enabled = mal.enabled
        metadata = MetadataConfig(
            enabled=toml_util.bool_val(meta_enabled, enabled_default),
            mode=mode,
            provider=provider,
            structure=structure,
            fallback_mal=toml_util.bool_val(metadata_table.get("fallback_mal"), True),
            fallback_anilist=toml_util.bool_val(
                metadata_table.get("fallback_anilist"), False
            ),
            confirm_ambiguous=toml_util.bool_val(
                metadata_table.get("confirm_ambiguous"), True
            ),
            parallel=toml_util.int_val(metadata_table.get("parallel"), 8),
            cache_ttl_hours=toml_util.int_val(
                metadata_table.get("cache_ttl_hours"), mal.cache_ttl_hours
            ),
        )

        skip_recap = data.get("skip_recap_movies")
        if skip_recap is None:
            skip_recap = catalog_table.get("skip_recap_movies")

        catalog = CatalogConfig(
            skip_recap_movies=toml_util.bool_val(skip_recap, False),
            fill_gaps_on_search=toml_util.bool_val(
                catalog_table.get("fill_gaps_on_search"), False
            ),
            search_results_limit=toml_util.int_val(
                catalog_table.get("search_results_limit"), 8
            ),
            franchise_max_queries=toml_util.int_val(
                catalog_table.get("franchise_max_queries"), 20
            ),
            primary_search_pages=toml_util.int_val(
                catalog_table.get("primary_search_pages"), 2
            ),
            franchise_search_pages=toml_util.int_val(
                catalog_table.get("franchise_search_pages"), 2
            ),
            gap_search_pages=toml_util.int_val(
                catalog_table.get("gap_search_pages"), 1
            ),
            gap_max_missing=toml_util.int_val(
                catalog_table.get("gap_max_missing"), 6
            ),
            gap_max_queries=toml_util.int_val(
                catalog_table.get("gap_max_queries"), 10
            ),
            preferred_groups=toml_util.str_list(
                data.get("preferred_groups")
                or catalog_table.get("preferred_groups")
            ),
            preferred_group_bonus=toml_util.int_val(
                catalog_table.get("preferred_group_bonus"), 10
            ),
            min_seeders_strict=toml_util.int_val(
                catalog_table.get("min_seeders_strict"), 10
            ),
            min_seeders_relaxed=toml_util.int_val(
                catalog_table.get("min_seeders_relaxed"), 3
            ),
            min_quality_strict=toml_util.int_val(
                catalog_table.get("min_quality_strict"), 26
            ),
            min_quality_relaxed=toml_util.int_val(
                catalog_table.get("min_quality_relaxed"), 12
            ),
            coverage_relaxed=toml_util.float_val(
                catalog_table.get("coverage_relaxed"), 0.85
            ),
            prefer_season_batch=toml_util.bool_val(
                catalog_table.get("prefer_season_batch"), True
            ),
            season_batch_min_coverage=toml_util.float_val(
                catalog_table.get("season_batch_min_coverage"), 0.85
            ),
            coherence_min_share=toml_util.float_val(
                catalog_table.get("coherence_min_share"), 0.60
            ),
        )

        subtitles_enabled = data.get("subtitles_enabled")
        if subtitles_enabled is None:
            subtitles_enabled = subtitles_table.get("enabled")

        subtitles = SubtitlesConfig(
            enabled=toml_util.bool_val(subtitles_enabled, True),
            default_lang=toml_util.str_val(
                data.get("default_sub_lang")
                or subtitles_table.get("default_lang")
            ),
            fetch_timeout=toml_util.float_val(
                subtitles_table.get("fetch_timeout"), 20.0
            ),
            api_key=toml_util.str_val(
                os.environ.get("OPENSUBTITLES_API_KEY")
                or data.get("opensubtitles_api_key")
                or subtitles_table.get("api_key")
            ),
            username=toml_util.str_val(
                os.environ.get("OPENSUBTITLES_USERNAME")
                or data.get("opensubtitles_username")
                or subtitles_table.get("username")
            ),
            password=toml_util.str_val(
                os.environ.get("OPENSUBTITLES_PASSWORD")
                or data.get("opensubtitles_password")
                or subtitles_table.get("password")
            ),
        )

        ui = UiConfig(
            seeders_highlight=toml_util.int_val(
                ui_table.get("seeders_highlight"), 50
            ),
            show_banner=toml_util.bool_val(ui_table.get("show_banner"), True),
            mal_pool_workers=toml_util.int_val(ui_table.get("mal_pool_workers"), 16),
            show_download_progress=toml_util.bool_val(
                ui_table.get("show_download_progress"), True
            ),
        )

        _config_cache = cls(
            player=player,
            nyaa=nyaa,
            mal=mal,
            metadata=metadata,
            catalog=catalog,
            subtitles=subtitles,
            ui=ui,
        )
        return replace(_config_cache)

    load_cached = load

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
