"""Réglages utilisateur Annie (streaming, buffer, lecteur)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from annie.user_config import SETTINGS_FILE, ensure_user_config

_settings_cache: AnnieSettings | None = None


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


def _str_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass
class StreamingSettings:
    seed_while_watching: bool = True
    upload_limit_kib: int = 512


@dataclass
class BufferSettings:
    max_wait_sec: float = 5.0
    no_peers_sec: float = 20.0
    absolute_sec: float = 45.0
    mkv_start_mib: int = 16
    mkv_head_mib: int = 16
    stream_margin_mib: int = 12
    mpv_retry_sec: float = 15.0
    mkv_playable_wait_sec: float = 8.0


@dataclass
class TorrentSettings:
    metadata_timeout: float = 60.0
    connections_limit: int = 300
    active_downloads: int = 1
    active_limit: int = 4
    unchoke_slots: int = 12
    unchoke_slots_seeding: int = 20
    enable_dht: bool = True
    enable_lsd: bool = True
    enable_upnp: bool = True
    enable_natpmp: bool = True


@dataclass
class MpvPlayerSettings:
    cache_secs: int = 30
    hwdec: str = "auto-safe"
    vo: str = "gpu"
    gpu_api: str = "opengl"
    really_quiet: bool = True
    force_window: bool = True
    extra_args: list[str] = field(default_factory=list)


@dataclass
class VlcPlayerSettings:
    file_caching_ms: int = 3000
    network_caching_ms: int = 3000
    extra_args: list[str] = field(default_factory=list)


@dataclass
class PlayerSettings:
    mpv: MpvPlayerSettings = field(default_factory=MpvPlayerSettings)
    vlc: VlcPlayerSettings = field(default_factory=VlcPlayerSettings)


@dataclass
class AnnieSettings:
    streaming: StreamingSettings = field(default_factory=StreamingSettings)
    buffer: BufferSettings = field(default_factory=BufferSettings)
    torrent: TorrentSettings = field(default_factory=TorrentSettings)
    player: PlayerSettings = field(default_factory=PlayerSettings)

    @property
    def seed_while_watching(self) -> bool:
        return self.streaming.seed_while_watching

    @classmethod
    def load(cls) -> AnnieSettings:
        global _settings_cache
        if _settings_cache is not None:
            return replace(_settings_cache)

        ensure_user_config()

        data: dict = {}
        if SETTINGS_FILE.is_file():
            data = tomllib.loads(SETTINGS_FILE.read_text(encoding="utf-8"))

        streaming_table = _table(data, "streaming")
        buffer_table = _table(data, "buffer")
        torrent_table = _table(data, "torrent")
        player_table = _table(data, "player")
        mpv_table = _table(player_table, "mpv")
        vlc_table = _table(player_table, "vlc")

        if "streaming" in data:
            seed_default = streaming_table.get("seed_while_watching")
        else:
            seed_default = data.get("seed_while_watching")
        seed_while_watching = _bool(seed_default, True)

        env = os.environ.get("ANNIE_SEED_WHILE_WATCHING", "").strip().lower()
        if env in {"0", "false", "no", "off"}:
            seed_while_watching = False
        elif env in {"1", "true", "yes", "on"}:
            seed_while_watching = True

        streaming = StreamingSettings(
            seed_while_watching=seed_while_watching,
            upload_limit_kib=_int(streaming_table.get("upload_limit_kib"), 512),
        )

        buffer = BufferSettings(
            max_wait_sec=_float(buffer_table.get("max_wait_sec"), 5.0),
            no_peers_sec=_float(buffer_table.get("no_peers_sec"), 20.0),
            absolute_sec=_float(buffer_table.get("absolute_sec"), 45.0),
            mkv_start_mib=_int(buffer_table.get("mkv_start_mib"), 16),
            mkv_head_mib=_int(buffer_table.get("mkv_head_mib"), 16),
            stream_margin_mib=_int(buffer_table.get("stream_margin_mib"), 12),
            mpv_retry_sec=_float(buffer_table.get("mpv_retry_sec"), 15.0),
            mkv_playable_wait_sec=_float(
                buffer_table.get("mkv_playable_wait_sec"), 8.0
            ),
        )

        torrent = TorrentSettings(
            metadata_timeout=_float(torrent_table.get("metadata_timeout"), 60.0),
            connections_limit=_int(torrent_table.get("connections_limit"), 300),
            active_downloads=_int(torrent_table.get("active_downloads"), 1),
            active_limit=_int(torrent_table.get("active_limit"), 4),
            unchoke_slots=_int(torrent_table.get("unchoke_slots"), 12),
            unchoke_slots_seeding=_int(
                torrent_table.get("unchoke_slots_seeding"), 20
            ),
            enable_dht=_bool(torrent_table.get("enable_dht"), True),
            enable_lsd=_bool(torrent_table.get("enable_lsd"), True),
            enable_upnp=_bool(torrent_table.get("enable_upnp"), True),
            enable_natpmp=_bool(torrent_table.get("enable_natpmp"), True),
        )

        player = PlayerSettings(
            mpv=MpvPlayerSettings(
                cache_secs=_int(mpv_table.get("cache_secs"), 30),
                hwdec=str(mpv_table.get("hwdec") or "auto-safe"),
                vo=str(mpv_table.get("vo") or "gpu"),
                gpu_api=str(mpv_table.get("gpu_api") or "opengl"),
                really_quiet=_bool(mpv_table.get("really_quiet"), True),
                force_window=_bool(mpv_table.get("force_window"), True),
                extra_args=_str_list(mpv_table.get("extra_args")),
            ),
            vlc=VlcPlayerSettings(
                file_caching_ms=_int(vlc_table.get("file_caching_ms"), 3000),
                network_caching_ms=_int(vlc_table.get("network_caching_ms"), 3000),
                extra_args=_str_list(vlc_table.get("extra_args")),
            ),
        )

        _settings_cache = cls(
            streaming=streaming,
            buffer=buffer,
            torrent=torrent,
            player=player,
        )
        return replace(_settings_cache)


def reload_settings() -> AnnieSettings:
    """Invalide le cache (tests ou rechargement manuel)."""
    global _settings_cache
    _settings_cache = None
    return AnnieSettings.load()
