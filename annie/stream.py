"""Streaming torrent (libtorrent) + lecteurs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from annie.media import _filename_for_episode_match, match_episode_filename

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov"}
MKV_MAGIC = b"\x1a\x45\xdf\xa3"
CACHE_DIR = Path.home() / ".cache" / "annie"
DEFAULT_MIN_BUFFER = 16 * 1024 * 1024
STREAM_MIN_BYTES = 48 * 1024 * 1024  # floor before playback
STREAM_MIN_PCT = 5  # % of file size
STREAM_BUFFER_CAP = 96 * 1024 * 1024  # ideal buffer ceiling (~1 min @ 1.5 MiB/s)
BUFFER_MAX_WAIT_SEC = 10.0  # start playback after this even if ideal buffer not met
BUFFER_FORCE_BYTES = 24 * 1024 * 1024  # minimum data for forced early start
MKV_CLUSTER_MAGIC = b"\x1f\x43\xb6\x75"
MKV_PROBE_BYTES = 4 * 1024 * 1024

_player_cache: str | None = None


def die(message: str, code: int = 1) -> None:
    print(f"annie: {message}", file=sys.stderr)
    raise SystemExit(code)


def available_players() -> list[str]:
    return [name for name in ("mpv", "vlc", "ffplay") if shutil.which(name)]


def resolve_player(requested: str | None = None) -> str:
    global _player_cache
    if requested and requested != "auto":
        if shutil.which(requested) is None:
            raise RuntimeError(f"player not found: {requested}")
        return requested
    env = os.environ.get("ANNIE_PLAYER", "").strip().lower()
    if env and env != "auto":
        if shutil.which(env) is None:
            raise RuntimeError(f"ANNIE_PLAYER={env} not found")
        return env
    if _player_cache and shutil.which(_player_cache):
        return _player_cache
    for name in available_players():
        _player_cache = name
        return name
    raise RuntimeError("no player found — install mpv, vlc, or ffmpeg")


def player_command(player: str, path: Path) -> list[str]:
    target = str(path.resolve())
    if player == "mpv":
        return [
            "mpv",
            "--force-window=immediate",
            "--keep-open=no",
            "--cache=yes",
            "--cache-secs=30",
            "--demuxer-readahead-secs=5",
            "--demuxer-lavf-analyzeduration=1",
            "--demuxer-lavf-probesize=2097152",
            "--demuxer-lavf-o=fflags=+genpts+discardcorrupt",
            "--vo=gpu",
            "--gpu-api=opengl",
            "--hwdec=auto-safe",
            target,
        ]
    if player == "vlc":
        return [
            "vlc",
            "--play-and-exit",
            "--no-video-title-show",
            "--file-caching=3000",
            "--network-caching=3000",
            target,
        ]
    if player == "ffplay":
        return [
            "ffplay",
            "-autoexit",
            "-infbuf",
            "-fflags",
            "+genpts+discardcorrupt",
            "-loglevel",
            "error",
            target,
        ]
    raise RuntimeError(f"unsupported player: {player}")


def launch_player(path: Path, player: str) -> int:
    return subprocess.run(player_command(player, path)).returncode


def min_buffer_for(path: Path) -> int:
    if path.suffix.lower() == ".mkv":
        return max(DEFAULT_MIN_BUFFER, 32 * 1024 * 1024)
    return DEFAULT_MIN_BUFFER


def playback_target_bytes(target: Path, file_size: int) -> int:
    floor = min_buffer_for(target)
    pct = file_size * STREAM_MIN_PCT // 100
    ideal = max(floor, pct, STREAM_MIN_BYTES)
    return min(file_size, ideal, STREAM_BUFFER_CAP)


def make_session() -> lt.session:
    session = lt.session()
    try:
        session.apply_settings(
            {
                "active_downloads": 1,
                "active_seeds": 0,
                "active_limit": 4,
                "connections_limit": 300,
                "unchoke_slots_limit": 12,
                "allow_multiple_connections_per_ip": True,
                "enable_dht": True,
                "enable_lsd": True,
                "enable_upnp": True,
                "enable_natpmp": True,
                "download_rate_limit": 0,
                "upload_rate_limit": 512 * 1024,
            }
        )
    except Exception:
        pass
    return session


def torrent_cache_dir(info: lt.torrent_info) -> Path:
    safe_name = re.sub(r"[^\w\-.]+", "_", info.name()).strip("_.")[:56] or "torrent"
    info_hash = info.info_hash()
    try:
        tag = info_hash.to_bytes().hex()[:10]
    except AttributeError:
        tag = str(info_hash).replace(":", "")[:10]
    return CACHE_DIR / "stream" / f"{safe_name}-{tag}"


def magnet_save_path(source: str) -> Path:
    match = re.search(r"btih:([0-9a-fA-F]{40})", source, re.I)
    if not match:
        die("magnet link missing info hash")
    return CACHE_DIR / "stream" / match.group(1).lower()


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def add_torrent(session: lt.session, source: str, save_path: Path) -> lt.torrent_handle:
    save_path.mkdir(parents=True, exist_ok=True)
    if source.startswith("magnet:?"):
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


def wait_metadata(handle: lt.torrent_handle, timeout: float = 120.0) -> lt.torrent_info:
    deadline = time.monotonic() + timeout
    delay = 0.05
    while not handle.status().has_metadata:
        if time.monotonic() > deadline:
            die("metadata timeout")
        time.sleep(delay)
        delay = min(delay * 1.25, 0.2)
    return handle.torrent_file()


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


def pick_file(files, index, query, *, episode: int | None = None):
    if not files:
        die("no video files in torrent")
    if index is not None:
        for item in files:
            if item[0] == index:
                return item
        die(f"index {index} not found")
    if episode is not None:
        matches = [f for f in files if match_episode_filename(f[1], episode)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            die(" multiple files match:\n" + "\n".join(f"  [{i}] {Path(n).name}" for i, n, _ in matches))
        die(f"no file matches episode {episode}")
    if query:
        pattern = re.compile(query, re.I)
        matches = [
            f for f in files if pattern.search(_filename_for_episode_match(Path(f[1]).name))
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            die(" multiple files match:\n" + "\n".join(f"  [{i}] {Path(n).name}" for i, n, _ in matches))
        die(f"no file matches « {query} »")
    if len(files) == 1:
        return files[0]
    die(" multiple files — use -n:\n" + "\n".join(f"  -n {i} {Path(n).name}" for i, n, _ in files))


def load_torrent_info(source: str) -> lt.torrent_info:
    if source.startswith("magnet:?"):
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


def configure_stream(handle, file_index, file_count):
    handle.set_sequential_download(True)
    priorities = [0] * file_count
    priorities[file_index] = 7
    handle.prioritize_files(priorities)
    try:
        handle.set_flags(lt.torrent_flags.sequential_download)
    except AttributeError:
        pass


def _has_mkv_header(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == MKV_MAGIC
    except OSError:
        return False


def _mkv_has_clusters(path: Path, *, limit: int = MKV_PROBE_BYTES) -> bool:
    try:
        size = path.stat().st_size
        if size < 65536:
            return False
        with path.open("rb") as f:
            chunk = f.read(min(size, limit))
        return MKV_CLUSTER_MAGIC in chunk
    except OSError:
        return False


def wait_buffer(handle, file_index, target: Path, file_size: int):
    ideal_bytes = playback_target_bytes(target, file_size)
    deadline = time.monotonic() + BUFFER_MAX_WAIT_SEC
    is_mkv = target.suffix.lower() == ".mkv"
    mkv_playable = not is_mkv
    last_ready = -1
    stall_ticks = 0
    forced = False

    label = f"buffer (≤{BUFFER_MAX_WAIT_SEC:.0f}s · ideal {ideal_bytes // 1024 // 1024} MiB)"
    print(f"annie: {label}…", flush=True)

    while True:
        progress = handle.file_progress()
        downloaded = progress[file_index] if file_index < len(progress) else 0
        on_disk = target.stat().st_size if target.exists() else 0
        ready = min(downloaded, on_disk)

        if is_mkv and not mkv_playable and ready >= 65536:
            if _has_mkv_header(target):
                mkv_playable = _mkv_has_clusters(target)

        timed_out = time.monotonic() >= deadline
        ideal_met = ready >= ideal_bytes and mkv_playable
        forced_met = timed_out and ready >= BUFFER_FORCE_BYTES and mkv_playable

        if ideal_met or forced_met:
            forced = forced_met and not ideal_met
            break

        status = handle.status()
        pct = ready * 100 // ideal_bytes if ideal_bytes else 0
        progress_text = f"{ready // 1024 // 1024}/{ideal_bytes // 1024 // 1024} MiB ({pct}%)"
        print(
            f"\rannie: {progress_text} · {status.num_peers} peers · "
            f"{status.download_rate / 1024:.0f} KiB/s",
            end="",
            flush=True,
        )

        if status.state == lt.torrent_status.seeding and mkv_playable and ready >= BUFFER_FORCE_BYTES:
            break

        if ready == last_ready:
            stall_ticks += 1
        else:
            stall_ticks = 0
            last_ready = ready

        if status.download_rate > 256 * 1024:
            time.sleep(0.12)
        elif stall_ticks > 6:
            time.sleep(0.35)
        else:
            time.sleep(0.2)
    if forced:
        print(f"\nannie: early start ({ready // 1024 // 1024} MiB buffered)", flush=True)
    else:
        print(flush=True)


def play(source: str, index: int | None, query: str | None, keep: bool, *, player: str | None = None, episode: int | None = None) -> int:
    session = make_session()
    player_future = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        player_future = pool.submit(resolve_player, player)

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
        file_index, rel_path, file_size = pick_file(files, index, query, episode=episode)
        configure_stream(handle, file_index, info.files().num_files())
        target = (save_path / rel_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        player_name = player_future.result()
        print(f"annie: playing → {target.name} ({player_name})", flush=True)
        wait_buffer(handle, file_index, target, file_size)

    if not target.is_file():
        die(f"file missing after download: {target}")
    try:
        code = launch_player(target, player_name)
        if code != 0:
            print(f"annie: {player_name} code {code}", file=sys.stderr)
    finally:
        session.remove_torrent(handle, 0 if keep else 1)
    return code
