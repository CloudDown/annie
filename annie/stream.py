"""Streaming torrent (libtorrent) + lecteurs."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from pathlib import Path

import libtorrent as lt

from annie.paths import (
    cache_dir,
    ensure_directory,
    ipc_ready as mpv_ipc_is_ready,
    mpv_ipc_path,
    path_exists,
    path_open,
    windows_extended_path,
)
from annie.parsing import (
    _filename_for_episode_match,
    best_series_match_score,
    match_episode_filename,
    parse_title,
)
from annie.player import (  # noqa: F401 — resolve_player ré-exporté (install Windows)
    player_command,
    player_popen as _player_popen,
    resolve_player,
)
from annie.ui import (
    EXIT_CANCELLED,
    PLAY_COMPLETED,
    PLAY_INCOMPLETE,
    BufferStatusDisplay,
    begin_playback_ui,
    clear_terminal,
    end_playback_ui,
    format_buffer_forced_start,
    format_buffer_lines,
    format_buffer_local_file,
    format_buffer_quick_start,
    format_buffer_ready,
    format_stream_fatal,
    is_play_completed,
    is_user_cancel,
    log_buffer_pause,
    log_buffer_resume,
    log_playback_start,
    stream_log,
    stream_log_err,
)

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov"}
MKV_MAGIC = b"\x1a\x45\xdf\xa3"
MKV_CLUSTER = b"\x1f\x43\xb6\x75"
CACHE_DIR = cache_dir()
START_MIN_MKV_BYTES = 2 * 1024 * 1024
MKV_FRONTIER_PIECES = 64
START_UNPAUSE_BONUS_BYTES = 12 * 1024 * 1024
START_UNPAUSE_MAX_BONUS_BYTES = 32 * 1024 * 1024
START_UNPAUSE_MAX_WAIT_SEC = 15.0
# Prefetch binge : tête contiguë du prochain épisode dès 70 %.
BINGE_PREFETCH_PROGRESS = 0.70
BINGE_SWITCH_PROGRESS = 0.97
# Marge renforcée pendant les premières secondes de lecture (HEVC / BD).
START_WARMUP_SEC = 30.0
START_WARMUP_MARGIN_BYTES = 80 * 1024 * 1024
START_MIN_MP4_BYTES = 256 * 1024
START_MIN_OTHER_BYTES = 4 * 1024 * 1024
MP4_TAIL_BYTES = 8 * 1024 * 1024


def _settings():
    from annie.settings import AnnieSettings

    return AnnieSettings.load()


def _upload_limit_bytes() -> int:
    limit_kib = _settings().streaming.upload_limit_kib
    return 0 if limit_kib <= 0 else limit_kib * 1024


def _buffer_cfg():
    return _settings().buffer


def _mkv_start_bytes() -> int:
    return _buffer_cfg().mkv_start_mib * 1024 * 1024


def _mkv_head_bytes() -> int:
    return _buffer_cfg().mkv_head_mib * 1024 * 1024


def _stream_margin_bytes() -> int:
    return _buffer_cfg().stream_margin_mib * 1024 * 1024


def die(message: str, code: int = 1) -> None:
    print(format_stream_fatal(message), file=sys.stderr)
    raise SystemExit(code)


def make_session(*, seed_while_watching: bool = False) -> lt.session:
    settings = _settings()
    torrent = settings.torrent
    session = lt.session()
    upload_limit = 0 if seed_while_watching else _upload_limit_bytes()
    try:
        session.apply_settings(
            {
                "active_downloads": torrent.active_downloads,
                "active_seeds": 1 if seed_while_watching else 0,
                "active_limit": torrent.active_limit,
                "connections_limit": torrent.connections_limit,
                "unchoke_slots_limit": (
                    torrent.unchoke_slots_seeding
                    if seed_while_watching
                    else torrent.unchoke_slots
                ),
                "allow_multiple_connections_per_ip": True,
                "enable_dht": torrent.enable_dht,
                "enable_lsd": torrent.enable_lsd,
                "enable_upnp": torrent.enable_upnp,
                "enable_natpmp": torrent.enable_natpmp,
                "download_rate_limit": 0,
                "upload_rate_limit": upload_limit,
            }
        )
    except Exception:
        pass
    return session


def _enable_watch_seed(
    session: lt.session, handle: lt.torrent_handle, file_index: int
) -> None:
    torrent = _settings().torrent
    try:
        handle.set_upload_mode(False)
    except Exception:
        pass
    try:
        session.apply_settings(
            {
                "upload_rate_limit": 0,
                "unchoke_slots_limit": torrent.unchoke_slots_seeding,
            }
        )
    except Exception:
        pass
    info = handle.torrent_file()
    files = info.files()
    piece_len = info.piece_length()
    if piece_len <= 0:
        return
    file_offset = files.file_offset(file_index)
    file_size = files.file_size(file_index)
    first_piece = file_offset // piece_len
    last_piece = (file_offset + file_size - 1) // piece_len
    for piece in range(first_piece, last_piece + 1):
        if handle.have_piece(piece):
            handle.piece_priority(piece, 7)


def _disable_watch_seed(session: lt.session) -> None:
    torrent = _settings().torrent
    try:
        session.apply_settings(
            {
                "upload_rate_limit": _upload_limit_bytes(),
                "unchoke_slots_limit": torrent.unchoke_slots,
            }
        )
    except Exception:
        pass


def _info_hash_hex(info: lt.torrent_info) -> str:
    info_hash = info.info_hash()
    try:
        return info_hash.to_bytes().hex().lower()
    except AttributeError:
        return str(info_hash).replace(":", "").lower()


def torrent_file_cache_path(info_hash: str) -> Path:
    return CACHE_DIR / "stream" / f"{info_hash}.torrent"


def magnet_info_hash(source: str) -> str:
    match = re.search(r"btih:([0-9a-fA-F]{40})", source, re.I)
    if not match:
        die("magnet link missing info hash")
    return match.group(1).lower()


def torrent_cache_dir(info: lt.torrent_info) -> Path:
    safe_name = re.sub(r"[^\w\-.]+", "_", info.name()).strip("_.")[:56] or "torrent"
    tag = _info_hash_hex(info)[:10]
    return CACHE_DIR / "stream" / f"{safe_name}-{tag}"


def magnet_save_path(source: str) -> Path:
    return CACHE_DIR / "stream" / magnet_info_hash(source)


def _save_torrent_file(info: lt.torrent_info) -> None:
    path = torrent_file_cache_path(_info_hash_hex(info))
    if path.is_file():
        return
    try:
        ensure_directory(path.parent)
        params = lt.add_torrent_params()
        params.ti = info
        path.write_bytes(lt.bencode(lt.write_torrent_file(params)))
    except Exception:
        pass


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def _torrent_save_path(save_path: Path) -> str:
    return windows_extended_path(save_path)


def add_torrent(session: lt.session, source: str, save_path: Path) -> lt.torrent_handle:
    ensure_directory(save_path)
    save = _torrent_save_path(save_path)
    if source.startswith("magnet:?"):
        info_hash = magnet_info_hash(source)
        cached = torrent_file_cache_path(info_hash)
        if cached.is_file():
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(str(cached))
            params.save_path = save
            return session.add_torrent(params)
        params = lt.parse_magnet_uri(source)
        params.save_path = save
        return session.add_torrent(params)
    torrent_path = Path(source).expanduser().resolve()
    if not torrent_path.is_file():
        die(f"file not found: {torrent_path}")
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(str(torrent_path))
    params.save_path = save
    return session.add_torrent(params)


def wait_metadata(handle: lt.torrent_handle, timeout: float | None = None) -> lt.torrent_info:
    if timeout is None:
        timeout = _settings().torrent.metadata_timeout
    deadline = time.monotonic() + timeout
    delay = 0.03
    while not handle.status().has_metadata:
        if time.monotonic() > deadline:
            die("metadata timeout")
        time.sleep(delay)
        delay = min(delay * 1.2, 0.15)
    info = handle.torrent_file()
    _save_torrent_file(info)
    return info


def torrent_files(info: lt.torrent_info) -> list[tuple[int, str, int]]:
    files = info.files()
    return [
        (i, files.file_path(i), files.file_size(i))
        for i in range(files.num_files())
        if is_video(files.file_path(i))
    ]


def human_size(num: int) -> str:
    size, unit = float(num), "B"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _pick_best_series_match(matches, match_queries: list[str]):
    """Among episode matches, keep the unique best series score if decisive."""
    queries = [q for q in match_queries if q and q.strip()]
    if not queries or len(matches) < 2:
        return None
    scored: list[tuple[int, object]] = []
    for item in matches:
        parsed = parse_title(Path(item[1]).name)
        scored.append((best_series_match_score(parsed, queries), item))
    best_score = max(score for score, _ in scored)
    if best_score < 0:
        return None
    winners = [item for score, item in scored if score == best_score]
    if len(winners) == 1:
        return winners[0]
    return None


def pick_file(
    files,
    index,
    query,
    *,
    episode: int | None = None,
    season: int | None = None,
    source_episode: int | None = None,
    match_queries: list[str] | None = None,
):
    if not files:
        die("no video files in torrent")
    if index is not None:
        for item in files:
            if item[0] == index:
                return item
        die(f"index {index} not found")
    if episode is not None:
        # Le catalogue peut remapper la numérotation absolue (fichier « - 67 »
        # affiché S4E01) : on essaie le numéro affiché puis le numéro d'origine.
        candidates: list[tuple[int, int | None]] = [(episode, season)]
        if source_episode is not None and source_episode != episode:
            candidates.append((source_episode, None))
        for candidate, candidate_season in candidates:
            matches = [
                f
                for f in files
                if match_episode_filename(f[1], candidate, season=candidate_season)
            ]
            if len(matches) == 1:
                return matches[0]
            if matches:
                picked = _pick_best_series_match(matches, match_queries or [])
                if picked is not None:
                    return picked
                die(
                    " multiple files match:\n"
                    + "\n".join(f"  [{i}] {Path(n).name}" for i, n, _ in matches)
                )
        if len(files) == 1:
            return files[0]
        die(f"no file matches episode {episode}")
    if query:
        pattern = re.compile(query, re.I)
        matches = [
            f
            for f in files
            if pattern.search(_filename_for_episode_match(Path(f[1]).name))
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            picked = _pick_best_series_match(matches, match_queries or [])
            if picked is not None:
                return picked
            die(
                " multiple files match:\n"
                + "\n".join(f"  [{i}] {Path(n).name}" for i, n, _ in matches)
            )
        die(f"no file matches « {query} »")
    if len(files) == 1:
        return files[0]
    die(
        " multiple files — use -n:\n"
        + "\n".join(f"  -n {i} {Path(n).name}" for i, n, _ in files)
    )


def load_torrent_info(source: str) -> lt.torrent_info:
    if source.startswith("magnet:?"):
        cached = torrent_file_cache_path(magnet_info_hash(source))
        if cached.is_file():
            return lt.torrent_info(str(cached))
        session = make_session()
        handle = add_torrent(session, source, CACHE_DIR / "meta")
        try:
            return wait_metadata(handle)
        finally:
            session.remove_torrent(handle)
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        die(f"fichier introuvable : {path}")
    return lt.torrent_info(str(path))


def list_files(source: str) -> int:
    info = load_torrent_info(source)
    print(info.name())
    for index, path, size in torrent_files(info):
        print(f"  [{index:>2}]  {human_size(size):>9}  {Path(path).name}")
    return 0


def _file_piece_bounds(handle: lt.torrent_handle, file_index: int) -> tuple[int, int]:
    info = handle.torrent_file()
    files = info.files()
    piece_len = info.piece_length()
    file_offset = files.file_offset(file_index)
    file_size = files.file_size(file_index)
    first_piece = file_offset // piece_len
    last_piece = (file_offset + file_size - 1) // piece_len
    return first_piece, last_piece


def _frontier_piece(handle: lt.torrent_handle, file_index: int) -> int | None:
    first_piece, last_piece = _file_piece_bounds(handle, file_index)
    for piece in range(first_piece, last_piece + 1):
        if not handle.have_piece(piece):
            return piece
    return None


def _enforce_sequential_frontier(
    handle: lt.torrent_handle, file_index: int, *, urgent: bool = False
) -> None:
    """Download only the next pieces after the contiguous frontier (no holes ahead)."""
    first_piece, last_piece = _file_piece_bounds(handle, file_index)
    frontier = _frontier_piece(handle, file_index)
    if frontier is None:
        return
    window = MKV_FRONTIER_PIECES + (32 if urgent else 0)
    deadline_ms = 6 if urgent else 15
    window_end = min(last_piece, frontier + window - 1)
    for piece in range(first_piece, last_piece + 1):
        if frontier <= piece <= window_end:
            handle.piece_priority(piece, 7)
            try:
                handle.set_piece_deadline(piece, (piece - frontier) * deadline_ms)
            except AttributeError:
                pass
        elif piece > window_end:
            handle.piece_priority(piece, 0)


def _prioritize_file_head(
    handle: lt.torrent_handle, file_index: int, nbytes: int
) -> None:
    piece_range = _piece_range_for_file_bytes(handle, file_index, 0, nbytes)
    if piece_range is None:
        return
    first_piece, last_piece = piece_range
    for i, piece in enumerate(
        range(first_piece, min(first_piece + 32, last_piece + 1))
    ):
        handle.piece_priority(piece, 7)
        try:
            handle.set_piece_deadline(piece, i * 20)
        except AttributeError:
            pass


def _file_priorities(handle: lt.torrent_handle, file_count: int) -> list[int]:
    try:
        raw = handle.get_file_priorities()
    except AttributeError:
        try:
            raw = handle.file_priorities()
        except Exception:
            raw = [0] * file_count
    priorities = [int(p) for p in raw]
    if len(priorities) < file_count:
        priorities.extend([0] * (file_count - len(priorities)))
    return priorities


def _prefetch_head_nbytes() -> int:
    return max(_mkv_head_bytes(), _mkv_start_bytes())


def _prioritize_prefetch_head(
    handle: lt.torrent_handle, file_index: int, nbytes: int
) -> None:
    """Tête de n+1 en prio 6 (sous la lecture courante en 7)."""
    piece_range = _piece_range_for_file_bytes(handle, file_index, 0, nbytes)
    if piece_range is None:
        return
    first_piece, last_piece = piece_range
    for i, piece in enumerate(
        range(first_piece, min(first_piece + 32, last_piece + 1))
    ):
        handle.piece_priority(piece, 6)
        try:
            handle.set_piece_deadline(piece, 80 + i * 20)
        except AttributeError:
            pass


def _enforce_prefetch_sequential_frontier(
    handle: lt.torrent_handle, file_index: int
) -> None:
    """Télécharge n+1 entier en contigu (fenêtre séquentielle, prio 6)."""
    first_piece, last_piece = _file_piece_bounds(handle, file_index)
    frontier = _frontier_piece(handle, file_index)
    if frontier is None:
        return
    window = MKV_FRONTIER_PIECES
    window_end = min(last_piece, frontier + window - 1)
    for piece in range(first_piece, last_piece + 1):
        if frontier <= piece <= window_end:
            handle.piece_priority(piece, 6)
            try:
                handle.set_piece_deadline(piece, 80 + (piece - frontier) * 20)
            except AttributeError:
                pass
        elif piece > window_end:
            handle.piece_priority(piece, 0)


def _prefetch_binge_file(
    handle: lt.torrent_handle,
    next_index: int,
    file_count: int,
    *,
    target: Path | None = None,
    file_size: int = 0,
) -> None:
    """Précharge n+1 comme un épisode normal : fichier entier, contigu, en avance."""
    priorities = _file_priorities(handle, file_count)
    priorities[next_index] = max(priorities[next_index], 5)
    handle.prioritize_files(priorities)
    # Même schéma que configure_stream, en prio inférieure à la lecture courante.
    _prioritize_prefetch_head(handle, next_index, _prefetch_head_nbytes())
    _enforce_prefetch_sequential_frontier(handle, next_index)
    if target is not None and target.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        _prioritize_mp4_tail(handle, next_index, file_size)


def _reboost_prefetch_head(handle: lt.torrent_handle, next_index: int) -> None:
    """Maintient le téléchargement contigu du fichier n+1 préchargé."""
    _enforce_prefetch_sequential_frontier(handle, next_index)


def configure_stream(
    handle, file_index, file_count, *, target: Path | None = None, file_size: int = 0
):
    handle.set_sequential_download(True)
    priorities = [0] * file_count
    priorities[file_index] = 7
    handle.prioritize_files(priorities)
    try:
        handle.set_flags(lt.torrent_flags.sequential_download)
    except AttributeError:
        pass
    _prioritize_file_head(handle, file_index, _mkv_head_bytes())
    if target is not None and target.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        _prioritize_mp4_tail(handle, file_index, file_size)


def _file_ready(handle: lt.torrent_handle, file_index: int) -> int:
    progress = handle.file_progress()
    if file_index >= len(progress):
        return 0
    return int(progress[file_index])


def _contiguous_file_bytes(handle: lt.torrent_handle, file_index: int) -> int:
    """Bytes from file offset 0 through the last consecutive complete piece."""
    info = handle.torrent_file()
    files = info.files()
    piece_len = info.piece_length()
    if piece_len <= 0:
        return 0
    file_offset = files.file_offset(file_index)
    file_size = files.file_size(file_index)
    first_piece = file_offset // piece_len
    last_piece = (file_offset + file_size - 1) // piece_len
    end_byte = 0
    for piece in range(first_piece, last_piece + 1):
        if not handle.have_piece(piece):
            break
        piece_end = (piece + 1) * piece_len
        overlap_end = min(piece_end, file_offset + file_size)
        if overlap_end <= file_offset:
            continue
        end_byte = overlap_end - file_offset
    return end_byte


def _has_mkv_header(path: Path) -> bool:
    for _ in range(4):
        try:
            if not path_exists(path):
                time.sleep(0.05)
                continue
            with path_open(path, "rb") as f:
                if f.read(4) == MKV_MAGIC:
                    return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def _piece_range_for_file_bytes(
    handle: lt.torrent_handle, file_index: int, byte_start: int, byte_end: int
) -> tuple[int, int] | None:
    info = handle.torrent_file()
    files = info.files()
    piece_len = info.piece_length()
    if piece_len <= 0:
        return None
    file_offset = files.file_offset(file_index)
    file_size = files.file_size(file_index)
    start = file_offset + max(0, byte_start)
    end = file_offset + min(byte_end, file_size) - 1
    if end < start:
        return None
    return start // piece_len, end // piece_len


def _mp4_has_ftyp(path: Path) -> bool:
    try:
        with path_open(path, "rb") as f:
            head = f.read(12)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except OSError:
        return False


def _mp4_has_moov_in_bytes(data: bytes) -> bool:
    return b"moov" in data


def _mp4_moov_in_head(path: Path, nbytes: int) -> bool:
    try:
        with path_open(path, "rb") as f:
            data = f.read(max(0, nbytes))
        return _mp4_has_moov_in_bytes(data)
    except OSError:
        return False


def _mp4_moov_in_tail(path: Path, file_size: int) -> bool:
    try:
        if not path_exists(path) or file_size < 1024:
            return False
        read_len = min(MP4_TAIL_BYTES, file_size)
        start = max(0, file_size - read_len)
        with path_open(path, "rb") as f:
            f.seek(start)
            data = f.read(read_len)
        return _mp4_has_moov_in_bytes(data)
    except OSError:
        return False


def _mkv_has_clusters(path: Path, nbytes: int) -> bool:
    if nbytes < 1024:
        return False
    try:
        with path_open(path, "rb") as f:
            data = f.read(nbytes)
        return MKV_CLUSTER in data
    except OSError:
        return False


def _mkv_playable(path: Path, contiguous: int) -> bool:
    if contiguous < _mkv_start_bytes():
        return False
    if not _has_mkv_header(path):
        return False
    return _mkv_has_clusters(path, contiguous)


def _is_startable(
    path: Path,
    ready: int,
    file_size: int,
    *,
    handle: lt.torrent_handle | None = None,
    file_index: int | None = None,
) -> bool:
    ext = path.suffix.lower()
    if ext == ".mkv":
        if handle is None or file_index is None:
            return False
        contiguous = _contiguous_file_bytes(handle, file_index)
        return _mkv_playable(path, contiguous)
    if ext in {".mp4", ".m4v", ".mov"}:
        if ready < START_MIN_MP4_BYTES or not _mp4_has_ftyp(path):
            return False
        if _mp4_moov_in_head(path, min(ready, 32 * 1024 * 1024)):
            return True
        if _mp4_moov_in_tail(path, file_size):
            return True
        return file_size > 0 and ready >= file_size
    return ready >= START_MIN_OTHER_BYTES


def _prioritize_mp4_tail(
    handle: lt.torrent_handle, file_index: int, file_size: int
) -> None:
    if file_size < MP4_TAIL_BYTES * 2:
        return
    info = handle.torrent_file()
    files = info.files()
    offset = files.file_offset(file_index)
    size = files.file_size(file_index)
    piece_len = info.piece_length()
    if piece_len <= 0:
        return
    first_piece = offset // piece_len
    last_piece = (offset + size - 1) // piece_len
    tail_pieces = max(4, (MP4_TAIL_BYTES + piece_len - 1) // piece_len)
    for piece in range(max(first_piece, last_piece - tail_pieces + 1), last_piece + 1):
        handle.piece_priority(piece, 6)


def _is_downloading_metadata(status) -> bool:
    state = getattr(status, "state", None)
    if state is None:
        return False
    name = getattr(state, "name", None)
    if name == "downloading_metadata":
        return True
    try:
        return state == lt.torrent_status.downloading_metadata
    except Exception:
        return False


def _buffer_peer_state(
    status,
    *,
    listed_seeders: int | None = None,
) -> tuple[bool, str]:
    """Activité réseau utile et libellé peers (Nyaa seeders ≠ peers connectés)."""
    download_rate = int(getattr(status, "download_rate", 0) or 0)
    upload_rate = int(getattr(status, "upload_rate", 0) or 0)
    num_peers = int(getattr(status, "num_peers", 0) or 0)
    num_seeds = int(getattr(status, "num_seeds", 0) or 0)

    if download_rate > 0 or upload_rate > 0 or num_peers > 0:
        return True, f"{num_peers} peers"

    if _is_downloading_metadata(status):
        return False, "récupération métadonnées…"

    if num_seeds > 0:
        return False, f"connexion au swarm… ({num_seeds} seeds)"

    if listed_seeders and listed_seeders > 0:
        return False, f"connexion au swarm… ({listed_seeders}S Nyaa)"

    return False, "connexion au swarm…"


def _peer_wait_deadlines(
    buf,
    started_at: float,
    *,
    listed_seeders: int | None = None,
) -> tuple[float, float]:
    """Délais d'attente ; bonus si Nyaa annonçait des seeders."""
    no_peers_sec = buf.no_peers_sec
    absolute_sec = buf.absolute_sec
    if listed_seeders and listed_seeders > 0:
        bonus = min(90.0, 15.0 + listed_seeders * 1.5)
        no_peers_sec += bonus * 0.5
        absolute_sec += bonus
    return started_at + no_peers_sec, started_at + absolute_sec


def _buffer_start_mode(
    *,
    startable: bool,
    can_start: bool,
    contiguous: int,
    target_bytes: int,
    soft_timeout: bool,
    hard_timeout: bool,
    seeding: bool,
) -> str | None:
    """Décide si on lance mpv. None = continuer d'attendre."""
    full = startable and can_start and contiguous >= target_bytes
    if full:
        return "ready"
    if seeding and startable:
        return "seeding"
    if soft_timeout and startable and can_start:
        return "quick"
    if hard_timeout:
        if can_start and startable:
            return "forced"
        return "timeout"
    return None


def _head_buffered(
    handle, file_index, target: Path, file_size: int
) -> bool:
    """True si la tête du fichier est déjà assez remplie pour lancer mpv."""
    ready = _file_ready(handle, file_index)
    contiguous = _contiguous_file_bytes(handle, file_index)
    startable = _is_startable(
        target, ready, file_size, handle=handle, file_index=file_index
    )
    return startable and contiguous >= _mkv_start_bytes()


def wait_startable(
    handle,
    file_index,
    target: Path,
    file_size: int,
    *,
    listed_seeders: int | None = None,
) -> tuple[int, str]:
    """Wait until the file is startable or timeout. Returns (ready_bytes, mode)."""
    buf = _buffer_cfg()
    started_at = time.monotonic()
    deadline = started_at + buf.max_wait_sec
    no_peers_deadline, absolute_deadline = _peer_wait_deadlines(
        buf, started_at, listed_seeders=listed_seeders
    )
    last_ready = -1
    stall_ticks = 0
    target_bytes = _mkv_start_bytes()
    display = BufferStatusDisplay()

    try:
        while True:
            ready = _file_ready(handle, file_index)
            contiguous = _contiguous_file_bytes(handle, file_index)
            now = time.monotonic()
            status = handle.status()
            has_transfer, peer_hint = _buffer_peer_state(
                status, listed_seeders=listed_seeders
            )
            # Tête déjà en cache (contigu OK) : lancer même sans peer actif —
            # le buffer pendant la lecture gère le manque de données.
            head_ready = contiguous >= target_bytes and _is_startable(
                target, ready, file_size, handle=handle, file_index=file_index
            )
            can_start = (
                has_transfer
                or ready >= int(file_size * 0.98)
                or head_ready
            )
            startable = _is_startable(
                target, ready, file_size, handle=handle, file_index=file_index
            )

            soft_timeout = now >= (no_peers_deadline if not has_transfer else deadline)
            hard_timeout = now >= absolute_deadline
            seeding = status.state == lt.torrent_status.seeding
            mode = _buffer_start_mode(
                startable=startable,
                can_start=can_start,
                contiguous=contiguous,
                target_bytes=target_bytes,
                soft_timeout=soft_timeout,
                hard_timeout=hard_timeout,
                seeding=seeding,
            )
            if mode == "ready":
                display.finish(format_buffer_ready(contiguous // 1024 // 1024))
                return contiguous, "ready"
            if mode == "quick":
                display.finish(format_buffer_quick_start(contiguous // 1024 // 1024))
                return contiguous, "quick"
            if mode == "seeding":
                display.finish(format_buffer_local_file(contiguous // 1024 // 1024))
                return contiguous, "seeding"
            if mode == "forced":
                display.finish(format_buffer_forced_start(contiguous // 1024 // 1024))
                return contiguous, "forced"
            if mode == "timeout":
                display.finish("")
                peer_note = ""
                if listed_seeders and listed_seeders > 0:
                    peer_note = (
                        f" — Nyaa affichait {listed_seeders} seeders ; "
                        "la connexion au swarm peut prendre plus de temps"
                    )
                die(
                    "buffer timeout — fichier incomplet "
                    f"({contiguous // 1024 // 1024} MiB contigu / "
                    f"{ready // 1024 // 1024} MiB total, {peer_hint.lower()})"
                    f"{peer_note}"
                )

            probe_hint = ""
            if (
                target.suffix.lower() == ".mkv"
                and contiguous >= START_MIN_MKV_BYTES
                and not startable
            ):
                probe_hint = " · clusters…"
            display.update(
                format_buffer_lines(
                    contiguous=contiguous,
                    ready=ready,
                    file_size=file_size,
                    target_bytes=target_bytes,
                    peer_hint=peer_hint,
                    download_kib=status.download_rate / 1024,
                    extra_hint=probe_hint,
                )
            )

            if ready == last_ready:
                stall_ticks += 1
            else:
                stall_ticks = 0
                last_ready = ready

            _enforce_sequential_frontier(handle, file_index)

            if status.download_rate > 256 * 1024:
                time.sleep(0.08)
            elif stall_ticks > 6:
                time.sleep(0.2)
            else:
                time.sleep(0.1)
    except KeyboardInterrupt:
        display.finish("")
        raise


def _mpv_ipc_request(ipc_path: Path, command: list) -> object | None:
    payload_bytes = json.dumps({"command": command}).encode() + b"\n"
    try:
        if sys.platform == "win32":
            with open(ipc_path, "r+b", buffering=0) as pipe:
                pipe.write(payload_bytes)
                chunks: list[bytes] = []
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
                if not chunks:
                    return None
                line = b"".join(chunks).split(b"\n", 1)[0]
        else:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.connect(str(ipc_path))
                sock.sendall(payload_bytes)
                chunks = []
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
                if not chunks:
                    return None
                line = b"".join(chunks).split(b"\n", 1)[0]
        parsed = json.loads(line.decode())
        if parsed.get("error") == "success":
            return parsed.get("data")
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def _estimate_play_byte(time_pos: object, duration: object, file_size: int) -> int:
    try:
        pos = float(time_pos)  # type: ignore[arg-type]
        total = float(duration)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    if total <= 0 or file_size <= 0:
        return 0
    return int(min(file_size, max(0, pos / total * file_size)))


def _playback_lead_required(
    play_byte: int,
    *,
    download_rate: int = 0,
    consumption_rate: float = 0,
    elapsed_sec: float = 0,
) -> int:
    """Bytes contigus requis pour tenir devant la position de lecture."""
    margin = _stream_margin_bytes()
    if elapsed_sec < START_WARMUP_SEC:
        # Début de lecture : marge plus large (HEVC / BD / Re:Zero).
        margin = max(margin, START_WARMUP_MARGIN_BYTES)
    if consumption_rate > 0 and download_rate > 0:
        if consumption_rate > download_rate * 0.8:
            margin = max(margin, int(consumption_rate * 6))
    elif download_rate <= 256 * 1024 and play_byte > 0:
        margin = int(margin * 1.25)
    return play_byte + margin


def _initial_unpause_threshold(download_rate: int) -> int:
    """Seuil de reprise au lancement — remplit le buffer pendant que mpv est en pause."""
    base = max(_stream_margin_bytes(), START_WARMUP_MARGIN_BYTES)
    bonus = min(
        START_UNPAUSE_MAX_BONUS_BYTES,
        max(START_UNPAUSE_BONUS_BYTES, int(download_rate * 3)),
    )
    return base + bonus


def _wait_mpv_ipc(ipc_path: Path, *, timeout_sec: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if mpv_ipc_is_ready(ipc_path):
            return True
        time.sleep(0.05)
    return mpv_ipc_is_ready(ipc_path)


def _mpv_near_end(time_pos: object, duration: object) -> bool:
    try:
        pos = float(time_pos)  # type: ignore[arg-type]
        total = float(duration)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if total <= 0:
        return False
    remaining = total - pos
    return remaining <= max(8.0, total * 0.03)


def _mpv_playback_completed(ipc_path: Path) -> bool:
    eof = _mpv_ipc_request(ipc_path, ["get_property", "eof-reached"])
    if eof is True:
        return True
    time_pos = _mpv_ipc_request(ipc_path, ["get_property", "time-pos"])
    duration = _mpv_ipc_request(ipc_path, ["get_property", "duration"])
    return _mpv_near_end(time_pos, duration)


def _normalize_playback_code(
    exit_code: int,
    *,
    ipc_path: Path | None,
    ipc_was_ready: bool,
    saw_near_end: bool = False,
    max_progress: float = 0.0,
) -> int:
    if exit_code == EXIT_CANCELLED or is_user_cancel(exit_code):
        return EXIT_CANCELLED
    if exit_code != 0:
        return exit_code
    # mpv ferme l'IPC avant qu'on puisse lire eof — se fier au suivi pendant la lecture.
    if saw_near_end or max_progress >= 0.90:
        return PLAY_COMPLETED
    if ipc_path is not None and ipc_was_ready:
        return PLAY_COMPLETED if _mpv_playback_completed(ipc_path) else PLAY_INCOMPLETE
    return PLAY_COMPLETED


def _mpv_loadfile(
    ipc_path: Path,
    path: Path,
    *,
    sub_file: Path | None = None,
) -> bool:
    """Charge un nouveau fichier dans mpv sans fermer la fenêtre."""
    target = windows_extended_path(path.resolve())
    result = _mpv_ipc_request(ipc_path, ["loadfile", target, "replace"])
    if result is None and not _wait_mpv_ipc(ipc_path, timeout_sec=0.5):
        return False
    if sub_file is not None and path_exists(sub_file):
        _mpv_ipc_request(
            ipc_path,
            ["sub-add", windows_extended_path(sub_file.resolve()), "select"],
        )
    _mpv_ipc_request(ipc_path, ["set_property", "pause", False])
    return True


def _play_while_downloading(
    proc: subprocess.Popen,
    handle: lt.torrent_handle,
    file_index: int,
    target: Path,
    file_size: int,
    *,
    ipc_path: Path | None = None,
    session: lt.session | None = None,
    seed_while_watching: bool = False,
    show_download_progress: bool = True,
    listed_seeders: int | None = None,
    on_eof_next: Callable[
        [], tuple[int, Path, int, Path | None, object] | None
    ]
    | None = None,
    on_prefetch_next: Callable[[], int | None] | None = None,
    on_episode_done: Callable[[object], None] | None = None,
    playback_item: object | None = None,
) -> int:
    ipc_available = ipc_path is not None and _wait_mpv_ipc(ipc_path)
    paused_for_buffer = False
    display = BufferStatusDisplay() if show_download_progress else None
    target_bytes = _mkv_start_bytes()
    playback_started_at = time.monotonic()
    prev_play_byte = 0
    prev_tick = playback_started_at
    consumption_rate = 0.0
    saw_near_end = False
    max_progress = 0.0
    eof_handled = False
    current_item = playback_item
    prefetch_index: int | None = None
    last_prefetch_boost = 0.0
    last_seed_boost = 0.0

    if ipc_available and ipc_path is not None:
        _mpv_ipc_request(ipc_path, ["set_property", "pause", True])
        paused_for_buffer = True

    try:
        while proc.poll() is None:
            now = time.monotonic()
            dt = max(0.05, now - prev_tick)
            status = handle.status()
            download_rate = int(getattr(status, "download_rate", 0) or 0)
            urgent = consumption_rate > download_rate * 0.7
            _enforce_sequential_frontier(handle, file_index, urgent=urgent)
            if seed_while_watching and session is not None:
                if now - last_seed_boost >= 2.0:
                    _enable_watch_seed(session, handle, file_index)
                    last_seed_boost = now
            contiguous = _contiguous_file_bytes(handle, file_index)
            ready = _file_ready(handle, file_index)

            play_byte = 0
            if ipc_available and ipc_path is not None:
                time_pos = _mpv_ipc_request(ipc_path, ["get_property", "time-pos"])
                duration = _mpv_ipc_request(ipc_path, ["get_property", "duration"])
                eof = _mpv_ipc_request(ipc_path, ["get_property", "eof-reached"])
                try:
                    if duration is not None and time_pos is not None:
                        total = float(duration)  # type: ignore[arg-type]
                        pos = float(time_pos)  # type: ignore[arg-type]
                        if total > 0:
                            max_progress = max(max_progress, pos / total)
                except (TypeError, ValueError):
                    pass
                if eof is True or _mpv_near_end(time_pos, duration):
                    saw_near_end = True

                # Dès 70 % : précharger n+1 entier en contigu (comme un épisode normal).
                if (
                    prefetch_index is None
                    and on_prefetch_next is not None
                    and max_progress >= BINGE_PREFETCH_PROGRESS
                ):
                    prefetch_index = on_prefetch_next()
                    last_prefetch_boost = now
                elif (
                    prefetch_index is not None
                    and now - last_prefetch_boost >= 1.0
                    and max_progress < BINGE_SWITCH_PROGRESS
                ):
                    _reboost_prefetch_head(handle, prefetch_index)
                    last_prefetch_boost = now

                # Enchaînement binge : charger le prochain fichier sans fermer mpv.
                if (
                    saw_near_end
                    and not eof_handled
                    and on_eof_next is not None
                    and (eof is True or max_progress >= BINGE_SWITCH_PROGRESS)
                ):
                    eof_handled = True
                    if display is not None:
                        display.finish("")
                    nxt = on_eof_next()
                    if nxt is not None:
                        next_index, next_path, next_size, next_sub, next_item = nxt
                        if _mpv_loadfile(ipc_path, next_path, sub_file=next_sub):
                            if on_episode_done is not None and current_item is not None:
                                on_episode_done(current_item)
                            current_item = next_item
                            file_index = next_index
                            file_size = next_size
                            playback_started_at = time.monotonic()
                            prev_play_byte = 0
                            consumption_rate = 0.0
                            saw_near_end = False
                            max_progress = 0.0
                            eof_handled = False
                            paused_for_buffer = False
                            prefetch_index = None
                            last_prefetch_boost = 0.0
                            prev_tick = time.monotonic()
                            continue
                    # Pas de suivant (ou loadfile échoué) : fermer mpv.
                    _mpv_ipc_request(ipc_path, ["quit"])
                    break

                play_byte = _estimate_play_byte(time_pos, duration, file_size)
                if play_byte > prev_play_byte:
                    consumption_rate = max(
                        0.0, (play_byte - prev_play_byte) / dt
                    )
                required = _playback_lead_required(
                    play_byte,
                    download_rate=download_rate,
                    consumption_rate=consumption_rate,
                    elapsed_sec=now - playback_started_at,
                )
                at_start = play_byte <= 0 and (
                    time_pos is None
                    or (isinstance(time_pos, (int, float)) and float(time_pos) <= 0.05)
                )
                if at_start and now < playback_started_at + START_UNPAUSE_MAX_WAIT_SEC:
                    required = max(required, _initial_unpause_threshold(download_rate))

                need_pause = contiguous < required

                if need_pause and not paused_for_buffer:
                    _mpv_ipc_request(ipc_path, ["set_property", "pause", True])
                    paused_for_buffer = True
                    if display is None:
                        log_buffer_pause()
                elif paused_for_buffer and contiguous >= required:
                    _mpv_ipc_request(ipc_path, ["set_property", "pause", False])
                    paused_for_buffer = False
                    if display is None:
                        log_buffer_resume()

                prev_play_byte = play_byte

            if display is not None:
                _has_transfer, peer_hint = _buffer_peer_state(
                    status, listed_seeders=listed_seeders
                )
                extra_hint = " · ⏸ buffer insuffisant" if paused_for_buffer else ""
                display.update(
                    format_buffer_lines(
                        contiguous=contiguous,
                        ready=ready,
                        file_size=file_size,
                        target_bytes=target_bytes,
                        peer_hint=peer_hint,
                        download_kib=download_rate / 1024,
                        extra_hint=extra_hint,
                    )
                )

            prev_tick = now
            lead = contiguous - play_byte
            if lead < _stream_margin_bytes():
                time.sleep(0.08)
            else:
                time.sleep(0.2)
    except KeyboardInterrupt:
        try:
            proc.kill()
        except OSError:
            pass
        if display is not None:
            display.finish("")
        return EXIT_CANCELLED

    if display is not None:
        display.finish("")
    exit_code = proc.wait()
    code = _normalize_playback_code(
        exit_code,
        ipc_path=ipc_path,
        ipc_was_ready=ipc_available,
        saw_near_end=saw_near_end,
        max_progress=max_progress,
    )
    if (
        is_play_completed(code)
        and on_episode_done is not None
        and current_item is not None
    ):
        on_episode_done(current_item)
    return code


def _wait_mkv_playable(
    handle: lt.torrent_handle,
    file_index: int,
    target: Path,
    file_size: int,
    *,
    max_wait_sec: float,
) -> int:
    deadline = time.monotonic() + max_wait_sec
    contiguous = _contiguous_file_bytes(handle, file_index)
    while time.monotonic() < deadline:
        contiguous = _contiguous_file_bytes(handle, file_index)
        if _mkv_playable(target, contiguous):
            return contiguous
        _enforce_sequential_frontier(handle, file_index)
        time.sleep(0.15)
    return contiguous


def _launch_and_stream(
    handle: lt.torrent_handle,
    file_index: int,
    target: Path,
    file_size: int,
    player_name: str,
    *,
    session: lt.session | None = None,
    seed_while_watching: bool = False,
    retry: bool = True,
    sub_file: Path | None = None,
    listed_seeders: int | None = None,
    on_eof_next: Callable[
        [], tuple[int, Path, int, Path | None, object] | None
    ]
    | None = None,
    on_prefetch_next: Callable[[], int | None] | None = None,
    on_episode_done: Callable[[object], None] | None = None,
    playback_item: object | None = None,
    keep_open: bool = False,
) -> int:
    buf = _buffer_cfg()
    wait_startable(
        handle, file_index, target, file_size, listed_seeders=listed_seeders
    )
    if not path_exists(target):
        die(f"file missing: {target}")

    ready = _contiguous_file_bytes(handle, file_index)
    if target.suffix.lower() == ".mkv":
        ready = _wait_mkv_playable(
            handle, file_index, target, file_size, max_wait_sec=buf.mkv_playable_wait_sec
        )
        if not _mkv_playable(target, ready):
            die(
                "fichier MKV illisible — données insuffisantes "
                f"({ready // 1024 // 1024} MiB contigu)"
            )

    active_sub = sub_file
    if active_sub is not None and player_name != "mpv":
        stream_log_err(
            "sous-titres",
            "externes supportés uniquement avec mpv",
            tone="warn",
        )
        active_sub = None

    from annie.config import AnnieConfig

    show_progress = AnnieConfig.load().ui.show_download_progress

    def _run_pass(*, mpv_profile: str = "default") -> int:
        pass_ipc: Path | None = None
        if player_name == "mpv":
            ipc_dir = CACHE_DIR / "ipc"
            ipc_dir.mkdir(parents=True, exist_ok=True)
            pass_ipc = mpv_ipc_path(ipc_dir)
        try:
            proc = _player_popen(
                player_command(
                    player_name,
                    target,
                    ipc_path=pass_ipc,
                    sub_file=active_sub,
                    mpv_profile=mpv_profile,
                    streaming=pass_ipc is not None,
                    keep_open=keep_open and player_name == "mpv",
                )
            )
            return _play_while_downloading(
                proc,
                handle,
                file_index,
                target,
                file_size,
                ipc_path=pass_ipc,
                session=session,
                seed_while_watching=seed_while_watching,
                show_download_progress=show_progress,
                listed_seeders=listed_seeders,
                on_eof_next=on_eof_next if player_name == "mpv" else None,
                on_prefetch_next=on_prefetch_next if player_name == "mpv" else None,
                on_episode_done=on_episode_done,
                playback_item=playback_item,
            )
        finally:
            if pass_ipc is not None and sys.platform != "win32" and pass_ipc.exists():
                pass_ipc.unlink(missing_ok=True)

    code = _run_pass(mpv_profile="default")

    if retry and player_name == "mpv" and code not in (
        PLAY_COMPLETED,
        PLAY_INCOMPLETE,
    ) and not is_user_cancel(code):
        stream_log("mpv", "échec lecture, nouvel essai…", tone="warn")
        if target.suffix.lower() == ".mkv":
            ready = _wait_mkv_playable(
                handle,
                file_index,
                target,
                file_size,
                max_wait_sec=buf.mpv_retry_sec,
            )
            if not _mkv_playable(target, ready):
                return code
        retry_profiles = ["safe", "software"]
        for profile in retry_profiles:
            code = _run_pass(mpv_profile=profile)
            if code == 0:
                break

    return code


def play(
    source: str,
    index: int | None,
    query: str | None,
    keep: bool,
    *,
    player: str | None = None,
    episode: int | None = None,
    season: int | None = None,
    source_episode: int | None = None,
    match_queries: list[str] | None = None,
    seed_while_watching: bool = True,
    subtitle_lang: str | None = None,
    subtitle_query=None,
    listed_seeders: int | None = None,
    on_ui_start: Callable[[], None] | None = None,
    binge_items: list | None = None,
    on_episode_done: Callable[[object], None] | None = None,
    current_item: object | None = None,
) -> int:
    session = make_session(seed_while_watching=seed_while_watching)
    handle: lt.torrent_handle | None = None
    target: Path
    player_name: str

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            player_future = pool.submit(resolve_player, player)
            sub_future = None
            sub_file: Path | None = None
            sub_status: tuple[str, str, str] | None = None
            if subtitle_lang and subtitle_query is not None:
                from annie.subtitles import (
                    _opensubtitles_config_hint,
                    fetch_best,
                    subtitles_api_available,
                )

                if subtitles_api_available():
                    sub_future = pool.submit(fetch_best, subtitle_query, subtitle_lang)
                else:
                    sub_status = ("warn", "sous-titres", _opensubtitles_config_hint())

            if source.startswith("magnet:?"):
                save_path = magnet_save_path(source)
                handle = add_torrent(session, source, save_path)
                info = wait_metadata(handle)
            else:
                info = load_torrent_info(source)
                save_path = torrent_cache_dir(info)
                params = lt.add_torrent_params()
                params.ti = info
                params.save_path = _torrent_save_path(save_path)
                handle = session.add_torrent(params)

            files = torrent_files(info)
            file_index, rel_path, file_size = pick_file(
                files,
                index,
                query,
                episode=episode,
                season=season,
                source_episode=source_episode,
                match_queries=match_queries,
            )
            target = (save_path / rel_path).resolve()
            ensure_directory(target.parent)
            configure_stream(
                handle,
                file_index,
                info.files().num_files(),
                target=target,
                file_size=file_size,
            )
            player_name = player_future.result()
            if sub_future is not None:
                from annie.config import AnnieConfig

                sub_timeout = AnnieConfig.load().subtitles.fetch_timeout
                try:
                    sub_file = sub_future.result(timeout=sub_timeout)
                    if sub_file is not None:
                        sub_status = ("ok", "sous-titres", sub_file.name)
                    else:
                        from annie.subtitles import no_subtitles_message

                        detail = (
                            no_subtitles_message(subtitle_query, subtitle_lang)
                            if subtitle_query and subtitle_lang
                            else "aucun trouvé"
                        )
                        sub_status = ("warn", "sous-titres", detail)
                except Exception as exc:
                    from annie.subtitles import SubtitlesError

                    if isinstance(exc, SubtitlesError):
                        sub_status = ("err", "sous-titres", str(exc))
                    else:
                        sub_status = ("err", "sous-titres", f"indisponibles ({exc})")

        # Clear + ligne ◆ juste avant le bloc lecture (pas pendant le fetch metadata).
        if on_ui_start is not None:
            on_ui_start()
        else:
            begin_playback_ui()

        try:
            if sub_status is not None:
                kind, tag, detail = sub_status
                if kind == "ok":
                    stream_log(tag, detail, tone="accent")
                else:
                    stream_log_err(
                        tag, detail, tone="warn" if kind == "warn" else "err"
                    )

            log_playback_start(target.name, player_name)
            if seed_while_watching:
                stream_log("seed", "actif pendant la lecture", tone="muted")
                _enable_watch_seed(session, handle, file_index)

            binge_queue = list(binge_items or [])
            # Prefetch dès 70 % : fichier n+1 entier (contigu) + sous-titres.
            prefetch_box: dict[str, object | None] = {
                "item": None,
                "index": None,
                "path": None,
                "size": None,
                "sub": None,
                "sub_pending": False,
                "sub_done": False,
            }

            def _resolve_binge_file(nxt: object) -> tuple[int, Path, int] | None:
                nxt_parsed = getattr(nxt, "parsed", None)
                nxt_ep = getattr(nxt_parsed, "episode", None)
                nxt_season = getattr(nxt_parsed, "season", None)
                nxt_source = getattr(nxt_parsed, "source_episode", None)
                assert handle is not None
                info_now = handle.torrent_file()
                if info_now is None:
                    return None
                files_now = torrent_files(info_now)
                try:
                    next_index, next_rel, next_size = pick_file(
                        files_now,
                        None,
                        None,
                        episode=nxt_ep,
                        season=nxt_season,
                        source_episode=nxt_source,
                        match_queries=match_queries,
                    )
                except SystemExit:
                    return None
                next_path = (save_path / next_rel).resolve()
                ensure_directory(next_path.parent)
                return next_index, next_path, next_size

            def _start_sub_prefetch(nxt: object) -> None:
                if not subtitle_lang or prefetch_box.get("sub_pending"):
                    return
                prefetch_box["sub_pending"] = True
                prefetch_box["sub_done"] = False
                prefetch_box["sub"] = None

                def _worker() -> None:
                    try:
                        from annie.subtitles import build_query, fetch_best

                        mal_titles = ()
                        series_title = None
                        if subtitle_query is not None:
                            series_title = getattr(
                                subtitle_query, "series_title", None
                            )
                            mal_titles = (
                                getattr(subtitle_query, "mal_titles", ()) or ()
                            )
                        q = build_query(
                            nxt, series_title=series_title, mal_titles=mal_titles
                        )
                        prefetch_box["sub"] = fetch_best(q, subtitle_lang)
                    except Exception:
                        prefetch_box["sub"] = None
                    finally:
                        prefetch_box["sub_done"] = True

                threading.Thread(target=_worker, daemon=True).start()

            def _on_prefetch_next() -> int | None:
                if not binge_queue:
                    return None
                nxt = binge_queue[0]
                resolved = _resolve_binge_file(nxt)
                if resolved is None:
                    return None
                next_index, next_path, next_size = resolved
                assert handle is not None
                info_now = handle.torrent_file()
                if info_now is None:
                    return None
                _prefetch_binge_file(
                    handle,
                    next_index,
                    info_now.files().num_files(),
                    target=next_path,
                    file_size=next_size,
                )
                prefetch_box["item"] = nxt
                prefetch_box["index"] = next_index
                prefetch_box["path"] = next_path
                prefetch_box["size"] = next_size
                _start_sub_prefetch(nxt)
                stream_log("prefetch", next_path.name, tone="muted")
                return next_index

            def _on_eof_next() -> tuple[int, Path, int, Path | None, object] | None:
                if not binge_queue:
                    return None
                nxt = binge_queue.pop(0)
                assert handle is not None
                info_now = handle.torrent_file()
                if info_now is None:
                    return None

                if (
                    prefetch_box.get("item") is nxt
                    and prefetch_box.get("index") is not None
                ):
                    next_index = int(prefetch_box["index"])  # type: ignore[arg-type]
                    next_path = prefetch_box["path"]  # type: ignore[assignment]
                    next_size = int(prefetch_box["size"])  # type: ignore[arg-type]
                    assert isinstance(next_path, Path)
                else:
                    resolved = _resolve_binge_file(nxt)
                    if resolved is None:
                        return None
                    next_index, next_path, next_size = resolved

                configure_stream(
                    handle,
                    next_index,
                    info_now.files().num_files(),
                    target=next_path,
                    file_size=next_size,
                )
                if not _head_buffered(handle, next_index, next_path, next_size):
                    wait_startable(
                        handle,
                        next_index,
                        next_path,
                        next_size,
                        listed_seeders=listed_seeders,
                    )

                next_sub: Path | None = None
                if subtitle_lang:
                    if (
                        prefetch_box.get("item") is nxt
                        and prefetch_box.get("sub_pending")
                    ):
                        deadline = time.monotonic() + 2.0
                        while (
                            not prefetch_box.get("sub_done")
                            and time.monotonic() < deadline
                        ):
                            time.sleep(0.05)
                    if (
                        prefetch_box.get("item") is nxt
                        and prefetch_box.get("sub_done")
                    ):
                        cached = prefetch_box.get("sub")
                        next_sub = cached if isinstance(cached, Path) else None
                    else:
                        try:
                            from annie.subtitles import build_query, fetch_best

                            mal_titles = ()
                            series_title = None
                            if subtitle_query is not None:
                                series_title = getattr(
                                    subtitle_query, "series_title", None
                                )
                                mal_titles = (
                                    getattr(subtitle_query, "mal_titles", ()) or ()
                                )
                            q = build_query(
                                nxt,
                                series_title=series_title,
                                mal_titles=mal_titles,
                            )
                            next_sub = fetch_best(q, subtitle_lang)
                        except Exception:
                            next_sub = None

                prefetch_box["item"] = None
                prefetch_box["index"] = None
                prefetch_box["path"] = None
                prefetch_box["size"] = None
                prefetch_box["sub"] = None
                prefetch_box["sub_pending"] = False
                prefetch_box["sub_done"] = False

                clear_terminal()
                try:
                    from annie.parsing import minimal_label
                    from annie.ui import C, stylize

                    print(
                        stylize(f"◆ {minimal_label(nxt.parsed)}", C.YELLOW, C.BOLD),
                        flush=True,
                    )
                except Exception:
                    print(f"◆ {next_path.name}", flush=True)
                if next_sub is not None:
                    stream_log("sous-titres", next_sub.name, tone="accent")
                log_playback_start(next_path.name, player_name)
                return next_index, next_path, next_size, next_sub, nxt

            code = 1
            try:
                code = _launch_and_stream(
                    handle,
                    file_index,
                    target,
                    file_size,
                    player_name,
                    session=session,
                    seed_while_watching=seed_while_watching,
                    sub_file=sub_file,
                    listed_seeders=listed_seeders,
                    on_eof_next=_on_eof_next if binge_queue else None,
                    on_prefetch_next=_on_prefetch_next if binge_queue else None,
                    on_episode_done=on_episode_done,
                    playback_item=current_item,
                    keep_open=bool(binge_queue),
                )
                if code not in (PLAY_COMPLETED, PLAY_INCOMPLETE) and not is_user_cancel(
                    code
                ):
                    stream_log_err(player_name, f"code {code}")
            finally:
                if seed_while_watching:
                    _disable_watch_seed(session)
                if handle is not None:
                    session.remove_torrent(handle, 0 if keep else 1)

            return code
        finally:
            end_playback_ui()
    except KeyboardInterrupt:
        end_playback_ui()
        if handle is not None:
            try:
                session.remove_torrent(handle, 0 if keep else 1)
            except Exception:
                pass
        return EXIT_CANCELLED
