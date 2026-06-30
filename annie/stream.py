"""Streaming torrent (libtorrent) + lecteurs."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import libtorrent as lt

from annie.paths import cache_dir, find_program, ipc_ready as mpv_ipc_is_ready, mpv_ipc_path
from annie.parsing import _filename_for_episode_match, match_episode_filename
from annie.ui import (
    BufferStatusDisplay,
    format_buffer_forced_start,
    format_buffer_lines,
    format_buffer_local_file,
    format_buffer_quick_start,
    format_buffer_ready,
    format_stream_fatal,
    log_playback_start,
    stream_log,
    stream_log_err,
)

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov"}
MKV_MAGIC = b"\x1a\x45\xdf\xa3"
MKV_CLUSTER = b"\x1f\x43\xb6\x75"
CACHE_DIR = cache_dir()
START_MIN_MKV_BYTES = 2 * 1024 * 1024
MKV_START_CONTIGUOUS_BYTES = 16 * 1024 * 1024
MKV_FRONTIER_PIECES = 64
STREAM_MARGIN_BYTES = 12 * 1024 * 1024
START_MIN_MP4_BYTES = 256 * 1024
START_MIN_OTHER_BYTES = 4 * 1024 * 1024
START_TARGET_BYTES = MKV_START_CONTIGUOUS_BYTES
BUFFER_MAX_WAIT_SEC = 5.0
BUFFER_NO_PEERS_SEC = 45.0
BUFFER_ABSOLUTE_SEC = 90.0
MP4_TAIL_BYTES = 8 * 1024 * 1024
MPV_RETRY_WAIT_SEC = 15.0
MKV_HEAD_BYTES = 16 * 1024 * 1024
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


_player_cache: str | None = None
_player_exe_cache: str | None = None
_ffprobe_cache: bool | None = None


def _remember_player(kind: str, exe: str) -> str:
    global _player_cache, _player_exe_cache
    _player_cache = kind
    _player_exe_cache = exe
    return kind


def player_binary(player: str) -> str:
    if _player_exe_cache and _player_cache == player:
        return _player_exe_cache
    return find_program(player) or player


def available_players() -> list[str]:
    return [name for name in ("mpv", "vlc", "ffplay") if find_program(name)]


def resolve_player(requested: str | None = None) -> str:
    global _player_cache, _player_exe_cache
    if requested and requested != "auto":
        exe = find_program(requested)
        if exe is None:
            raise RuntimeError(f"player not found: {requested}")
        kind = Path(exe).stem.lower()
        if kind not in {"mpv", "vlc", "ffplay"}:
            kind = Path(requested).stem.lower()
        return _remember_player(kind, exe)
    env = os.environ.get("ANNIE_PLAYER", "").strip().lower()
    if env and env != "auto":
        exe = find_program(env)
        if exe is None:
            raise RuntimeError(f"ANNIE_PLAYER={env} not found")
        return _remember_player(env, exe)
    if (
        _player_cache
        and _player_exe_cache
        and find_program(_player_cache) == _player_exe_cache
    ):
        return _player_cache
    for name in ("mpv", "vlc", "ffplay"):
        exe = find_program(name)
        if exe:
            return _remember_player(name, exe)
    raise RuntimeError("no player found — install mpv, vlc, or ffmpeg")


def die(message: str, code: int = 1) -> None:
    print(format_stream_fatal(message), file=sys.stderr)
    raise SystemExit(code)


def player_command(
    player: str,
    path: Path,
    *,
    ipc_path: Path | None = None,
    sub_file: Path | None = None,
) -> list[str]:
    settings = _settings()
    target = str(path.resolve())
    if player == "mpv":
        mpv = settings.player.mpv
        cmd = [player_binary("mpv")]
        if mpv.force_window:
            cmd.append("--force-window=immediate")
        cmd.extend(
            [
                "--keep-open=no",
                "--no-terminal",
            ]
        )
        if mpv.really_quiet:
            cmd.extend(
                [
                    "--really-quiet",
                    "--msg-level=all=fatal",
                ]
            )
        cmd.extend(
            [
                "--cache=yes",
                f"--cache-secs={mpv.cache_secs}",
                "--cache-pause=yes",
                "--cache-pause-initial=yes",
                "--demuxer-readahead-secs=3",
                "--demuxer-max-bytes=32M",
                "--demuxer-lavf-analyzeduration=5",
                "--demuxer-lavf-probesize=5242880",
                f"--vo={mpv.vo}",
                f"--gpu-api={mpv.gpu_api}",
                f"--hwdec={mpv.hwdec}",
            ]
        )
        if ipc_path is not None:
            cmd.append(f"--input-ipc-server={ipc_path}")
        if sub_file is not None:
            cmd.append(f"--sub-file={sub_file.resolve()}")
        cmd.extend(mpv.extra_args)
        cmd.append(target)
        return cmd
    if player == "vlc":
        vlc = settings.player.vlc
        cmd = [
            player_binary("vlc"),
            "--intf",
            "dummy",
            "--quiet",
            "--play-and-exit",
            "--no-video-title-show",
            f"--file-caching={vlc.file_caching_ms}",
            f"--network-caching={vlc.network_caching_ms}",
        ]
        cmd.extend(vlc.extra_args)
        cmd.append(target)
        return cmd
    if player == "ffplay":
        return [
            player_binary("ffplay"),
            "-autoexit",
            "-infbuf",
            "-fflags",
            "+genpts+discardcorrupt",
            "-loglevel",
            "error",
            target,
        ]
    raise RuntimeError(f"unsupported player: {player}")


def _player_popen(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        path.parent.mkdir(parents=True, exist_ok=True)
        params = lt.add_torrent_params()
        params.ti = info
        path.write_bytes(lt.bencode(lt.write_torrent_file(params)))
    except Exception:
        pass


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def add_torrent(session: lt.session, source: str, save_path: Path) -> lt.torrent_handle:
    save_path.mkdir(parents=True, exist_ok=True)
    if source.startswith("magnet:?"):
        info_hash = magnet_info_hash(source)
        cached = torrent_file_cache_path(info_hash)
        if cached.is_file():
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(str(cached))
            params.save_path = str(save_path)
            return session.add_torrent(params)
        params = lt.parse_magnet_uri(source)
        params.save_path = str(save_path)
        return session.add_torrent(params)
    torrent_path = Path(source).expanduser().resolve()
    if not torrent_path.is_file():
        die(f"file not found: {torrent_path}")
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(str(torrent_path))
    params.save_path = str(save_path)
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


def pick_file(
    files, index, query, *, episode: int | None = None, season: int | None = None
):
    if not files:
        die("no video files in torrent")
    if index is not None:
        for item in files:
            if item[0] == index:
                return item
        die(f"index {index} not found")
    if episode is not None:
        matches = [
            f for f in files if match_episode_filename(f[1], episode, season=season)
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            die(
                " multiple files match:\n"
                + "\n".join(f"  [{i}] {Path(n).name}" for i, n, _ in matches)
            )
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


def _enforce_sequential_frontier(handle: lt.torrent_handle, file_index: int) -> None:
    """Download only the next pieces after the contiguous frontier (no holes ahead)."""
    first_piece, last_piece = _file_piece_bounds(handle, file_index)
    frontier = _frontier_piece(handle, file_index)
    if frontier is None:
        return
    window_end = min(last_piece, frontier + MKV_FRONTIER_PIECES - 1)
    for piece in range(first_piece, last_piece + 1):
        if frontier <= piece <= window_end:
            handle.piece_priority(piece, 7)
            try:
                handle.set_piece_deadline(piece, (piece - frontier) * 15)
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
            if not path.exists():
                time.sleep(0.05)
                continue
            with path.open("rb") as f:
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


def _pieces_available(
    handle: lt.torrent_handle, first_piece: int, last_piece: int
) -> bool:
    for piece in range(first_piece, last_piece + 1):
        if not handle.have_piece(piece):
            return False
    return True


def _file_header_on_disk(
    handle: lt.torrent_handle, file_index: int, nbytes: int = 65536
) -> bool:
    piece_range = _piece_range_for_file_bytes(handle, file_index, 0, nbytes)
    if piece_range is None:
        return False
    return _pieces_available(handle, piece_range[0], piece_range[1])


def _mp4_has_ftyp(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(12)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except OSError:
        return False


def _mp4_has_moov_in_bytes(data: bytes) -> bool:
    return b"moov" in data


def _mp4_moov_in_head(path: Path, nbytes: int) -> bool:
    try:
        with path.open("rb") as f:
            data = f.read(max(0, nbytes))
        return _mp4_has_moov_in_bytes(data)
    except OSError:
        return False


def _mp4_moov_in_tail(path: Path, file_size: int) -> bool:
    try:
        if not path.exists() or file_size < 1024:
            return False
        read_len = min(MP4_TAIL_BYTES, file_size)
        start = max(0, file_size - read_len)
        with path.open("rb") as f:
            f.seek(start)
            data = f.read(read_len)
        return _mp4_has_moov_in_bytes(data)
    except OSError:
        return False


def _ffprobe_available() -> bool:
    global _ffprobe_cache
    if _ffprobe_cache is None:
        _ffprobe_cache = shutil.which("ffprobe") is not None
    return _ffprobe_cache


def _probe_with_ffprobe(path: Path) -> bool:
    if not _ffprobe_available() or not path.exists():
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _mkv_has_clusters(path: Path, nbytes: int) -> bool:
    if nbytes < 1024:
        return False
    try:
        with path.open("rb") as f:
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

    while True:
        ready = _file_ready(handle, file_index)
        contiguous = _contiguous_file_bytes(handle, file_index)
        now = time.monotonic()
        status = handle.status()
        has_transfer, peer_hint = _buffer_peer_state(
            status, listed_seeders=listed_seeders
        )
        can_start = has_transfer or ready >= int(file_size * 0.98)
        startable = _is_startable(
            target, ready, file_size, handle=handle, file_index=file_index
        )

        soft_timeout = now >= (no_peers_deadline if not has_transfer else deadline)
        hard_timeout = now >= absolute_deadline

        if startable and can_start and contiguous >= target_bytes:
            display.finish(format_buffer_ready(contiguous // 1024 // 1024))
            return contiguous, "ready"

        if soft_timeout and startable and can_start:
            display.finish(format_buffer_quick_start(contiguous // 1024 // 1024))
            return contiguous, "quick"

        if hard_timeout:
            if can_start and _is_startable(
                target, ready, file_size, handle=handle, file_index=file_index
            ):
                display.finish(format_buffer_forced_start(contiguous // 1024 // 1024))
                return contiguous, "forced"
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

        if status.state == lt.torrent_status.seeding and _is_startable(
            target, ready, file_size, handle=handle, file_index=file_index
        ):
            display.finish(format_buffer_local_file(contiguous // 1024 // 1024))
            return contiguous, "seeding"

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


def _wait_mpv_ipc(ipc_path: Path, *, timeout_sec: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if mpv_ipc_is_ready(ipc_path):
            return True
        time.sleep(0.05)
    return mpv_ipc_is_ready(ipc_path)


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
) -> int:
    paused_for_buffer = False
    ipc_available = ipc_path is not None and _wait_mpv_ipc(ipc_path)
    display = BufferStatusDisplay() if show_download_progress else None
    target_bytes = _mkv_start_bytes()

    while proc.poll() is None:
        _enforce_sequential_frontier(handle, file_index)
        if seed_while_watching and session is not None:
            _enable_watch_seed(session, handle, file_index)
        contiguous = _contiguous_file_bytes(handle, file_index)
        ready = _file_ready(handle, file_index)
        status = handle.status()

        if ipc_available and ipc_path is not None:
            time_pos = _mpv_ipc_request(ipc_path, ["get_property", "time-pos"])
            duration = _mpv_ipc_request(ipc_path, ["get_property", "duration"])
            play_byte = _estimate_play_byte(time_pos, duration, file_size)
            need_pause = contiguous < play_byte + _stream_margin_bytes()

            if need_pause and not paused_for_buffer:
                _mpv_ipc_request(ipc_path, ["set_property", "pause", True])
                paused_for_buffer = True
            elif paused_for_buffer and contiguous >= play_byte + _stream_margin_bytes():
                _mpv_ipc_request(ipc_path, ["set_property", "pause", False])
                paused_for_buffer = False

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
                    download_kib=status.download_rate / 1024,
                    extra_hint=extra_hint,
                )
            )

        time.sleep(0.25)

    if display is not None:
        display.finish("")
    return proc.wait()


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
) -> int:
    buf = _buffer_cfg()
    wait_startable(
        handle, file_index, target, file_size, listed_seeders=listed_seeders
    )
    if not target.exists():
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

    ipc_path: Path | None = None
    if player_name == "mpv":
        ipc_dir = CACHE_DIR / "ipc"
        ipc_dir.mkdir(parents=True, exist_ok=True)
        ipc_path = mpv_ipc_path(ipc_dir)

    from annie.config import AnnieConfig

    show_progress = AnnieConfig.load().ui.show_download_progress

    try:
        proc = _player_popen(
            player_command(player_name, target, ipc_path=ipc_path, sub_file=active_sub)
        )
        code = _play_while_downloading(
            proc,
            handle,
            file_index,
            target,
            file_size,
            ipc_path=ipc_path,
            session=session,
            seed_while_watching=seed_while_watching,
            show_download_progress=show_progress,
            listed_seeders=listed_seeders,
        )
    finally:
        if ipc_path is not None and sys.platform != "win32" and ipc_path.exists():
            ipc_path.unlink(missing_ok=True)

    if code == 2 and retry:
        stream_log("mpv", "n'a pas pu lire le fichier, nouvel essai…", tone="warn")
        ready = _wait_mkv_playable(
            handle, file_index, target, file_size, max_wait_sec=buf.mpv_retry_sec
        )
        if target.suffix.lower() == ".mkv" and not _mkv_playable(target, ready):
            return code
        ipc_path = None
        if player_name == "mpv":
            ipc_dir = CACHE_DIR / "ipc"
            ipc_dir.mkdir(parents=True, exist_ok=True)
            ipc_path = mpv_ipc_path(ipc_dir)
        try:
            proc = _player_popen(
                player_command(
                    player_name, target, ipc_path=ipc_path, sub_file=active_sub
                )
            )
            code = _play_while_downloading(
                proc,
                handle,
                file_index,
                target,
                file_size,
                ipc_path=ipc_path,
                session=session,
                seed_while_watching=seed_while_watching,
                show_download_progress=show_progress,
                listed_seeders=listed_seeders,
            )
        finally:
            if ipc_path is not None and sys.platform != "win32" and ipc_path.exists():
                ipc_path.unlink(missing_ok=True)

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
    seed_while_watching: bool = True,
    subtitle_lang: str | None = None,
    subtitle_query=None,
    listed_seeders: int | None = None,
) -> int:
    session = make_session(seed_while_watching=seed_while_watching)
    handle: lt.torrent_handle
    target: Path
    player_name: str

    with ThreadPoolExecutor(max_workers=2) as pool:
        player_future = pool.submit(resolve_player, player)
        sub_future = None
        if subtitle_lang and subtitle_query is not None:
            from annie.subtitles import fetch_best

            sub_future = pool.submit(fetch_best, subtitle_query, subtitle_lang)

        if source.startswith("magnet:?"):
            save_path = magnet_save_path(source)
            handle = add_torrent(session, source, save_path)
            info = wait_metadata(handle)
        else:
            info = load_torrent_info(source)
            save_path = torrent_cache_dir(info)
            params = lt.add_torrent_params()
            params.ti = info
            params.save_path = str(save_path)
            handle = session.add_torrent(params)

        files = torrent_files(info)
        file_index, rel_path, file_size = pick_file(
            files, index, query, episode=episode, season=season
        )
        target = (save_path / rel_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        configure_stream(
            handle,
            file_index,
            info.files().num_files(),
            target=target,
            file_size=file_size,
        )
        player_name = player_future.result()
        sub_file: Path | None = None
        if sub_future is not None:
            from annie.config import AnnieConfig

            sub_timeout = AnnieConfig.load().subtitles.fetch_timeout
            try:
                sub_file = sub_future.result(timeout=sub_timeout)
                if sub_file is not None:
                    stream_log("sous-titres", sub_file.name, tone="accent")
                else:
                    from annie.subtitles import SubtitlesError, no_subtitles_message

                    detail = (
                        no_subtitles_message(subtitle_query, subtitle_lang)
                        if subtitle_query and subtitle_lang
                        else "aucun trouvé"
                    )
                    stream_log_err("sous-titres", detail, tone="warn")
            except Exception as exc:
                from annie.subtitles import SubtitlesError

                if isinstance(exc, SubtitlesError):
                    stream_log_err("sous-titres", str(exc))
                else:
                    stream_log_err("sous-titres", f"indisponibles ({exc})")

        log_playback_start(target.name, player_name)
        if seed_while_watching:
            stream_log("seed", "actif pendant la lecture", tone="muted")
            _enable_watch_seed(session, handle, file_index)

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
            )
            if code != 0:
                stream_log_err(player_name, f"code {code}")
        finally:
            if seed_while_watching:
                _disable_watch_seed(session)
            session.remove_torrent(handle, 0 if keep else 1)

    return code
