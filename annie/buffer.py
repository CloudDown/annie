"""Buffer torrent : probes MKV/MP4, frontier pièces, décision de lancement mpv."""

from __future__ import annotations

import time
from pathlib import Path

import libtorrent as lt

from annie.ui import (
    BufferStatusDisplay,
    die,
    format_buffer_forced_start,
    format_buffer_lines,
    format_buffer_local_file,
    format_buffer_quick_start,
    format_buffer_ready,
)

MKV_MAGIC = b"\x1a\x45\xdf\xa3"
MKV_CLUSTER = b"\x1f\x43\xb6\x75"
START_MIN_MKV_BYTES = 2 * 1024 * 1024
MKV_FRONTIER_PIECES = 64
START_MIN_MP4_BYTES = 256 * 1024
START_MIN_OTHER_BYTES = 4 * 1024 * 1024
MP4_TAIL_BYTES = 8 * 1024 * 1024


def _buffer_cfg():
    from annie.config import AnnieConfig

    return AnnieConfig.load().buffer


def _mkv_start_bytes() -> int:
    return _buffer_cfg().mkv_start_mib * 1024 * 1024


def _mkv_head_bytes() -> int:
    return _buffer_cfg().mkv_head_mib * 1024 * 1024


def _stream_margin_bytes() -> int:
    return _buffer_cfg().stream_margin_mib * 1024 * 1024


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
        return False, "fetching metadata…"

    if num_seeds > 0:
        return False, f"connecting to swarm… ({num_seeds} seeds)"

    if listed_seeders and listed_seeders > 0:
        return False, f"connecting to swarm… ({listed_seeders}S Nyaa)"

    return False, "connecting to swarm…"


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
    player: str | None = None,
    seed: bool = False,
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
                        f" — Nyaa listed {listed_seeders} seeders; "
                        "swarm connection may take longer"
                    )
                die(
                    "buffer timeout — incomplete file "
                    f"({contiguous // 1024 // 1024} MiB contiguous / "
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
                    player=player,
                    seed=seed,
                    filename=target.name,
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
