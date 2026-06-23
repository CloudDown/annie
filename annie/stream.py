"""Streaming torrent (libtorrent) + lecteurs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import libtorrent as lt

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov"}
MKV_MAGIC = b"\x1a\x45\xdf\xa3"
CACHE_DIR = Path.home() / ".cache" / "annie"
DEFAULT_MIN_BUFFER = 16 * 1024 * 1024
FULL_DOWNLOAD_THRESHOLD = 2 * 1024 * 1024 * 1024  # 2 GiB — films, etc.


def die(message: str, code: int = 1) -> None:
    print(f"annie: {message}", file=sys.stderr)
    raise SystemExit(code)


def available_players() -> list[str]:
    return [name for name in ("mpv", "vlc", "ffplay") if shutil.which(name)]


def resolve_player(requested: str | None = None) -> str:
    if requested and requested != "auto":
        if shutil.which(requested) is None:
            raise RuntimeError(f"player not found: {requested}")
        return requested
    env = os.environ.get("ANNIE_PLAYER", "").strip().lower()
    if env and env != "auto":
        if shutil.which(env) is None:
            raise RuntimeError(f"ANNIE_PLAYER={env} not found")
        return env
    for name in available_players():
        return name
    raise RuntimeError("no player found — install mpv, vlc, or ffmpeg")


def player_command(player: str, path: Path) -> list[str]:
    target = str(path)
    if player == "mpv":
        return [
            "mpv", "--force-window=immediate", "--keep-open=no", "--cache=yes",
            "--demuxer-lavf-analyzeduration=0", "--demuxer-lavf-probesize=65536",
            "--demuxer-lavf-o=fflags=+genpts+discardcorrupt", "--vo=gpu",
            "--gpu-api=opengl", "--hwdec=auto-safe", target,
        ]
    if player == "vlc":
        return ["vlc", "--play-and-exit", "--no-video-title-show",
                "--file-caching=3000", "--network-caching=3000", target]
    if player == "ffplay":
        return ["ffplay", "-autoexit", "-infbuf", "-fflags", "+genpts+discardcorrupt",
                "-loglevel", "error", target]
    raise RuntimeError(f"unsupported player: {player}")


def launch_player(path: Path, player: str | None = None) -> tuple[int, str]:
    name = resolve_player(player)
    return subprocess.run(player_command(name, path)).returncode, name


def min_buffer_for(path: Path) -> int:
    if path.suffix.lower() == ".mkv":
        return max(DEFAULT_MIN_BUFFER, 20 * 1024 * 1024)
    return DEFAULT_MIN_BUFFER


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
    deadline = time.time() + timeout
    while not handle.status().has_metadata:
        if time.time() > deadline:
            die("metadata timeout")
        time.sleep(0.2)
    return handle.torrent_file()


def torrent_files(info: lt.torrent_info) -> list[tuple[int, str, int]]:
    files = info.files()
    return [(i, files.file_path(i), files.file_size(i))
            for i in range(files.num_files()) if is_video(files.file_path(i))]


def human_size(num: int) -> str:
    size, unit = float(num), "B"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def pick_file(files, index, query):
    if not files:
        die("no video files in torrent")
    if index is not None:
        for item in files:
            if item[0] == index:
                return item
        die(f"index {index} not found")
    if query:
        pattern = re.compile(query, re.I)
        matches = [f for f in files if pattern.search(Path(f[1]).name)]
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
        session = lt.session()
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


def _has_mkv_header(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == MKV_MAGIC
    except OSError:
        return False


def wait_buffer(handle, file_index, target: Path, file_size: int):
    if file_size >= FULL_DOWNLOAD_THRESHOLD:
        target_bytes = file_size
        label = "full download"
    else:
        target_bytes = min(min_buffer_for(target), file_size)
        label = f"buffer (min {target_bytes // 1024 // 1024} MiB)"
    print(f"annie: {label}…", flush=True)
    while True:
        progress = handle.file_progress()
        downloaded = progress[file_index] if file_index < len(progress) else 0
        on_disk = target.stat().st_size if target.exists() else 0
        ready = min(downloaded, on_disk)
        if ready >= target_bytes and (target.suffix.lower() != ".mkv" or _has_mkv_header(target)):
            break
        status = handle.status()
        if file_size >= FULL_DOWNLOAD_THRESHOLD:
            pct = ready * 100 // file_size
            progress_text = f"{ready // 1024 // 1024}/{file_size // 1024 // 1024} MiB ({pct}%)"
        else:
            progress_text = f"{ready // 1024 // 1024} MiB"
        print(
            f"\rannie: {progress_text} · {status.num_peers} peers · "
            f"{status.download_rate / 1024:.0f} KiB/s",
            end="",
            flush=True,
        )
        if status.state == lt.torrent_status.seeding and ready >= target_bytes:
            break
        time.sleep(0.5)
    print(flush=True)


def play(source: str, index: int | None, query: str | None, keep: bool, *, player: str | None = None) -> int:
    resolve_player(player)
    session = lt.session()
    save_path = CACHE_DIR / "stream"
    if source.startswith("magnet:?"):
        handle = add_torrent(session, source, save_path)
        info = wait_metadata(handle)
    else:
        info = load_torrent_info(source)
        params = lt.add_torrent_params()
        params.ti = info
        params.save_path = str(save_path)
        handle = session.add_torrent(params)
    files = torrent_files(info)
    file_index, rel_path, file_size = pick_file(files, index, query)
    configure_stream(handle, file_index, info.files().num_files())
    target = save_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    name = resolve_player(player)
    print(f"annie: playing → {target.name} ({name})", flush=True)
    wait_buffer(handle, file_index, target, file_size)
    try:
        code, used = launch_player(target, player)
        if code != 0:
            print(f"annie: {used} code {code}", file=sys.stderr)
    finally:
        session.remove_torrent(handle, 0 if keep else 1)
    return code
