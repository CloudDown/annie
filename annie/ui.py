"""Interface Annie (TUI in-process + console)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

from annie.parsing import minimal_label
from annie.types import MediaKind, MediaSection, ResultItem

# Code de sortie conventionnel (SIGINT / Ctrl+C volontaire).
EXIT_CANCELLED = 130
PLAY_COMPLETED = 0
PLAY_INCOMPLETE = 2


def is_user_cancel(code: int | None) -> bool:
    return code == EXIT_CANCELLED


def is_play_completed(code: int | None) -> bool:
    return code == PLAY_COMPLETED


class C:
    """Couleurs ANSI 16 — suivent le thème du terminal."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDER = "\033[4m"

    FG = ""
    MUTED = "\033[2m"
    PINK = "\033[35m"
    PALE_PINK = "\033[1;34m"
    ROSE = "\033[35m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    ORANGE = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[1;34m"
    WHITE = "\033[1m"

    LIST = "\033[1m"
    META = "\033[2m"
    CHROME = "\033[2m"
    SEED_HIGH = "\033[32m"
    SEED_MID = "\033[33m"
    SEED_LOW = "\033[31m"


def stylize(text: str, *codes: str) -> str:
    if not codes:
        return text
    return f"{''.join(codes)}{text}{C.RESET}"


BUFFER_BAR_WIDTH = 24
_PLAYBACK_TAG_WIDTH = 6


def progress_bar(pct: int, width: int = BUFFER_BAR_WIDTH) -> str:
    pct = max(0, min(100, pct))
    filled = pct * width // 100
    return "█" * filled + "░" * (width - filled)


def _mib_label(current: int, total: int) -> str:
    return f"{current // 1024 // 1024}/{max(1, total) // 1024 // 1024} MiB"


def _playback_tag(name: str, *, stream: Any = None) -> str:
    return _s(f"{name:<{_PLAYBACK_TAG_WIDTH}}", C.MUTED, stream=stream)


def _shorten_sub_detail(detail: str, *, limit: int = 72) -> str:
    lower = detail.lower()
    if "api key" in lower or "api_key" in lower or "key missing" in lower:
        return (
            "key missing — type settings · "
            "https://www.opensubtitles.com/en/consumers"
        )
    if len(detail) <= limit:
        return detail
    return detail[: limit - 1] + "…"


def format_buffer_lines(
    *,
    contiguous: int,
    ready: int,
    file_size: int,
    target_bytes: int,
    peer_hint: str,
    download_kib: float,
    extra_hint: str = "",
    player: str | None = None,
    seed: bool = False,
    filename: str | None = None,
) -> str:
    cont_pct = min(100, contiguous * 100 // target_bytes) if target_bytes else 0
    file_pct = ready * 100 // file_size if file_size else 0
    bar_cont = stylize(progress_bar(cont_pct), C.GREEN)
    bar_file = stylize(progress_bar(file_pct), C.PALE_PINK)
    meta_parts: list[str] = []
    if player:
        meta_parts.append(stylize(player, C.MUTED))
    if seed:
        meta_parts.append(stylize("seed", C.MUTED))
    if filename:
        cols, _ = _terminal_size()
        limit = max(24, min(42, cols - 36))
        name = filename if len(filename) <= limit else filename[: limit - 1] + "…"
        meta_parts.append(name)
    meta_parts.append(stylize(peer_hint, C.MUTED))
    if download_kib > 0:
        meta_parts.append(stylize(f"{download_kib:.0f} KiB/s", C.GREEN))
    if extra_hint:
        meta_parts.append(stylize(extra_hint.lstrip(" ·"), C.MUTED))
    meta = " · ".join(meta_parts)
    lines = [
        (
            f"{_playback_tag('buffer')}  [{bar_cont}] {cont_pct:3d}%  "
            f"{_mib_label(contiguous, target_bytes)}"
        ),
        (
            f"{_playback_tag('file')}  [{bar_file}] {file_pct:3d}%  "
            f"{_mib_label(ready, file_size)}"
        ),
        meta,
    ]
    return "\n".join(lines)


def print_playback_header(
    label: str,
    *,
    sub_status: tuple[str, str, str] | None = None,
) -> None:
    """Bloc fixe : titre + règle + ligne subs optionnelle."""
    print(stylize(f"◆ {label}", C.YELLOW, C.BOLD), flush=True)
    cols, _ = _terminal_size()
    width = min(52, max(28, cols - 2))
    print(stylize("─" * width, C.MUTED), flush=True)
    if sub_status is None:
        return
    kind, _tag, detail = sub_status
    tone = {"ok": C.CYAN, "warn": C.YELLOW, "err": C.RED}.get(kind, C.FG)
    print(
        f"{_playback_tag('subs')}  {stylize(_shorten_sub_detail(detail), tone)}",
        flush=True,
    )


class BufferStatusDisplay:
    def __init__(self) -> None:
        self._line_count = 0

    def update(self, text: str) -> None:
        lines = text.split("\n")
        line_count = len(lines)
        if self._line_count:
            sys.stdout.write(f"\033[{self._line_count}A")
        # Efface toute ligne orpheline si le bloc a rétréci.
        clear_extra = max(0, self._line_count - line_count)
        for line in lines:
            sys.stdout.write(f"\033[K{line}\n")
        for _ in range(clear_extra):
            sys.stdout.write("\033[K\n")
        if clear_extra:
            sys.stdout.write(f"\033[{clear_extra}A")
        sys.stdout.flush()
        self._line_count = line_count

    def finish(self, message: str) -> None:
        if self._line_count:
            sys.stdout.write(f"\033[{self._line_count}A\033[J")
            self._line_count = 0
        if message:
            print(message, flush=True)
        else:
            sys.stdout.flush()


BANNER_ART = [
    "⣿⠛⠛⠛⠛⠻⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠛⢛⣿⠋⢀⡾⠃⠀⠀⠀⠀⢀⣤⣤⠤⠤⣤⣤⣀⣀⣀⣠⠶⡶⣤⣀⣠⠾⡷⣦⣀⣤⣤⡤⠤⠦⢤⣤⣄⡀⠀⢠⡶⢶⡄⠀⠀",
    "⢠⡟⠁⣴⣿⢤⡄⣴⢶⠶⡆⠈⢷⡀⠀⠀⠀⠀⢀⣭⣫⠵⠥⠽⣄⣝⠵⢍⣘⣄⠳⣤⣀⠀⠀⢀⡤⠊⣽⠁⠀⠸⣇⠀⢿⠀⠀",
    "⠸⢷⣴⣤⡤⠾⠇⣽⠋⠼⣷⠀⠈⢷⡄⢀⣤⡶⠋⠀⣀⡄⠤⠀⡲⡆⠀⠀⠈⠙⡄⠘⢮⢳⡴⠯⣀⢠⡏⠀⠀⠀⢻⠀⢸⠇⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠙⠛⠋⠉⢀⣴⠟⠉⢯⡞⡠⢲⠉⣼⠀⠀⡰⠁⡇⢀⢷⠀⣄⢵⠀⠈⡟⢄⠀⠀⠙⢷⣤⣤⣤⡿⢢⡿⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⠟⠑⠊⠁⡼⣌⢠⢿⢸⢸⡀⢰⠁⡸⡇⡸⣸⢰⢈⠘⡄⠀⢸⠀⢣⡀⠀⠈⢮⢢⣏⣤⡾⠃⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⣯⣴⠞⡠⣼⠁⡘⣾⠏⣿⢇⣳⣸⣞⣀⢱⣧⣋⣞⡜⢳⡇⠀⢸⠀⢆⢧⠀⠰⣄⢏⢧⣾⠁⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢹⡏⢰⠁⡻⠀⡟⡏⠉⠀⣀⠀⠀⠀⠀⣀⠁⠀⠉⠛⢽⠇⠀⣼⡆⠈⡆⠃⠀⡏⠻⣾⣽⣇⡀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⠁⡇⠀⡇⡄⣿⠷⠿⠿⠛⠀⠀⠀⠀⠛⠻⠿⠿⠿⡜⢀⡴⡟⢸⣸⡼⠀⠀⡇⠀⡞⡆⢻⠙⢦⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⡶⢀⣼⣿⣬⣽⠧⠬⠇⠀⠀⠀⠀⠀⠀⢞⣯⣭⢺⣔⣪⣾⣤⠺⡇⢳⠀⢠⣧⡾⠛⠛⠻⠶⠞⠁",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠷⢿⠟⠉⡀⠈⢦⡀⠀⠀⣠⠖⠒⠒⢤⡀⠀⢀⡼⠿⢇⡣⢬⣶⠷⢿⣤⡾⠁⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠷⠾⠷⠖⠛⠛⠲⠶⠿⠤⣤⠤⠤⢷⣶⠋⠀⠀⠀⣱⠞⠁⠀⠀⠈⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠓⠒⠚⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
]


# Raccourcis style Omarchy : touche en reverse + libellé muted.
def keychip(key: str) -> str:
    return f"{C.BOLD}\033[7m {key} \033[0m"


def shortcut_line(pairs: list[tuple[str, str]], *, prefix: str = "  ") -> str:
    bits = [f"{keychip(key)}{stylize(f' {label}', C.MUTED)}" for key, label in pairs]
    return prefix + "  ".join(bits)


# Une ligne sous le logo — chips Omarchy (comme la barre raccourcis).
BANNER_HINT = shortcut_line(
    [
        ("help", "menu"),
        ("settings", "config"),
        ("↑↓", "move"),
        ("enter", "open"),
        ("esc", "back"),
    ]
)

HELP = BANNER_HINT

SEP = "\x1f"

PROMPT_COMMANDS = {
    "help": "help",
    "?": "help",
    "h": "help",
    "settings": "settings",
    "config": "settings",
    "quit": "quit",
    "exit": "quit",
    "q": "quit",
}


def parse_prompt_command(raw: str) -> str | None:
    """Retourne help|settings|quit, ou None. Sans slash (optionnel en tête)."""
    text = raw.strip()
    if not text:
        return None
    lowered = text.casefold()
    if lowered.startswith("/"):
        lowered = lowered[1:].strip()
    if " " in lowered:
        return None
    return PROMPT_COMMANDS.get(lowered)


def _tty_streams() -> list[Any]:
    """Flux réellement attachés au terminal."""
    streams: list[Any] = []
    try:
        streams.append(
            open("/dev/tty", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        )
    except OSError:
        pass
    if sys.stdout.isatty():
        streams.append(sys.stdout)
    return streams


def _write_tty(seq: str) -> bool:
    streams = _tty_streams()
    if not streams:
        return False
    owned = [s for s in streams if s is not sys.stdout]
    try:
        for stream in streams:
            try:
                stream.write(seq)
                stream.flush()
            except OSError:
                pass
        return True
    finally:
        for stream in owned:
            try:
                stream.close()
            except OSError:
                pass


_playback_alt_screen = False


def clear_terminal() -> None:
    """Efface l'écran comme la commande shell ``clear``."""
    os.system("clear")


def begin_playback_ui() -> None:
    """Écran propre pour la lecture."""
    global _playback_alt_screen
    clear_terminal()
    # Buffer alternatif si le terminal le supporte (évite de remonter l'ASCII).
    if _write_tty("\033[?1049h"):
        _playback_alt_screen = True
        clear_terminal()


def end_playback_ui() -> None:
    """Quitte le buffer alternatif après lecture."""
    global _playback_alt_screen
    if not _playback_alt_screen:
        return
    _write_tty("\033[?1049l")
    _playback_alt_screen = False


def print_banner() -> None:
    end_playback_ui()
    clear_terminal()
    print()
    for line in BANNER_ART:
        print(stylize(line, C.BLUE))
    print()
    print(BANNER_HINT)
    print()


def print_help() -> None:
    print(HELP)


def print_status(message: str, *, kind: str = "info") -> None:
    colors = {"info": C.FG, "ok": C.GREEN, "warn": C.YELLOW, "err": C.RED}
    print(stylize(f"  {message}", colors.get(kind, C.FG)))


_STREAM_TONES = {
    "info": C.FG,
    "ok": C.GREEN,
    "warn": C.YELLOW,
    "muted": C.MUTED,
    "accent": C.BLUE,
    "err": C.RED,
}


def _color_enabled(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _s(text: str, *codes: str, stream: Any = None) -> str:
    if not _color_enabled(stream):
        return text
    return stylize(text, *codes)


def _annie_prefix(*, stream: Any = None) -> str:
    del stream
    return ""


def format_stream_log(
    tag: str,
    detail: str = "",
    *,
    tone: str = "info",
    stream: Any = None,
) -> str:
    color = _STREAM_TONES.get(tone, C.FG)
    tag_part = _s(tag, C.MUTED, stream=stream)
    if detail:
        detail_part = _s(detail, color, stream=stream)
        return f"{_annie_prefix(stream=stream)}{tag_part}  {detail_part}"
    return f"{_annie_prefix(stream=stream)}{tag_part}"


def stream_log(tag: str, detail: str = "", *, tone: str = "info") -> None:
    print(format_stream_log(tag, detail, tone=tone), flush=True)


def stream_log_err(tag: str, detail: str = "", *, tone: str = "err") -> None:
    print(
        format_stream_log(tag, detail, tone=tone, stream=sys.stderr),
        file=sys.stderr,
        flush=True,
    )


def format_stream_fatal(message: str) -> str:
    return f"{_annie_prefix(stream=sys.stderr)}{_s(message, C.RED, stream=sys.stderr)}"


def die(message: str, code: int = 1) -> None:
    print(format_stream_fatal(message), file=sys.stderr)
    raise SystemExit(code)


def format_buffer_ready(mib: int) -> str:
    return format_stream_log("ready", f"{mib} MiB contiguous", tone="ok")


def format_buffer_quick_start(mib: int) -> str:
    return format_stream_log("quick start", f"{mib} MiB contiguous", tone="info")


def format_buffer_forced_start(mib: int) -> str:
    return format_stream_log(
        "forced start",
        f"{mib} MiB contiguous, trying mpv",
        tone="warn",
    )


def format_buffer_local_file(mib: int) -> str:
    return format_stream_log("local file", f"{mib} MiB contiguous", tone="ok")


def log_buffer_pause() -> None:
    print(format_stream_log("pause", "buffer too low", tone="err"), flush=True)


def log_buffer_resume() -> None:
    print(format_stream_log("resume", tone="ok"), flush=True)


_T = TypeVar("_T")
_SEARCH_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Mis à False pendant le TUI pour ne pas écraser l'UI interactive.
_spinner_active = threading.Event()
_spinner_active.set()


@contextmanager
def pause_search_spinner():
    """Coupe le spinner Searching pendant un picker (sinon lignes au-dessus)."""
    was_set = _spinner_active.is_set()
    _spinner_active.clear()
    try:
        print("\r\033[K", end="", flush=True)
        yield
    finally:
        if was_set:
            _spinner_active.set()


def run_search_spinner(query: str, fn: Callable[[], _T]) -> _T:
    """Run *fn* while showing a braille spinner on the current line."""
    del query  # titre affiché ailleurs (TUI) — spinner volontairement minimal

    if not sys.stdout.isatty():
        print(_s("search", C.MUTED), flush=True)
        return fn()

    result: list[_T] = []
    error: list[BaseException] = []
    done = threading.Event()

    def worker() -> None:
        try:
            result.append(fn())
        except BaseException as exc:
            error.append(exc)
        finally:
            done.set()

    _spinner_active.set()
    threading.Thread(target=worker, daemon=True).start()

    frame = 0
    try:
        while not done.is_set():
            if not _spinner_active.is_set():
                print("\r\033[K", end="", flush=True)
                done.wait(0.05)
                continue
            spin = _SEARCH_SPINNER[frame % len(_SEARCH_SPINNER)]
            line = f"{_s('search', C.MUTED)}  {_s(spin, C.BLUE)}"
            print(f"\r{line}", end="", flush=True)
            frame += 1
            done.wait(0.09)
    except KeyboardInterrupt:
        print("\r\033[K", end="", flush=True)
        raise

    print("\r\033[K", end="", flush=True)

    if error:
        raise error[0]
    return result[0]


def tui_available() -> bool:
    from annie.tui import available

    return available()


def tty_required_hint() -> str:
    return "run Annie in a real terminal (TTY)"


def _terminal_size() -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 80, 24


def _compact_ui() -> bool:
    cols, _ = _terminal_size()
    return cols < 110


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def log_playback_start(filename: str, player: str) -> None:
    """Compat : meta désormais dans le bloc buffer ; no-op si header dashboard."""
    del filename, player


def _fzf_header(text: str) -> str:
    return text


def _seed_color(seeders: int) -> str:
    if seeders >= 100:
        return C.SEED_HIGH
    if seeders >= 20:
        return C.SEED_MID
    return C.SEED_LOW


def _list_item_label(item: ResultItem) -> str:
    parsed = item.parsed
    if parsed.episode is not None and parsed.kind in {
        MediaKind.EPISODE,
        MediaKind.BATCH,
    }:
        return f"{parsed.episode:02d}"
    return _clip(minimal_label(parsed), 36)


def _compact_ep_label(item: ResultItem) -> str:
    parsed = item.parsed
    if parsed.episode is not None:
        season = parsed.season or 1
        return f"S{season:02d}E{parsed.episode:02d}"
    return _clip(minimal_label(parsed), 28)


def _item_is_watched(section, item, watch_history) -> bool:
    if section is None or watch_history is None:
        return False
    episode = item.parsed.episode
    is_movie = (
        section.kind == MediaKind.MOVIE or item.parsed.kind == MediaKind.MOVIE
    )
    if episode is None and not is_movie:
        return False
    return watch_history.is_watched(
        mal_id=section.mal_id,
        section_key=section.key,
        season=item.parsed.season or section.season,
        episode=episode,
    )


def format_torrent_line(
    item: ResultItem,
    *,
    section: MediaSection | None = None,
    watch_history=None,
) -> str:
    label = stylize(_list_item_label(item), C.LIST, C.BOLD)
    bits = [f"{item.entry.seeders}S"]
    if item.parsed.resolution:
        bits.append(item.parsed.resolution)
    line = f"{label}  {stylize(' · '.join(bits), C.MUTED)}"
    if _item_is_watched(section, item, watch_history):
        return f"{line} {stylize('●', C.RED)}"
    return line


def format_section_line(section: MediaSection) -> str:
    # Labels enrichis « Season 02 · 2019 · … » : clip plus large.
    label_width = 52 if " · " in section.label else (28 if _compact_ui() else 40)
    label = _clip(section.label, label_width)
    if section.has_episodes:
        count = len(section.episodes)
        if section.expected_episodes:
            detail = f"{count}/{section.expected_episodes} ep"
        else:
            detail = f"{count} ep"
        if section.batch_recommended:
            detail += " batch"
    else:
        detail = f"{len(section.singles)} rel"
    return f"{stylize(label, C.LIST, C.BOLD)}  {stylize(detail, C.META)}"


def format_preview_item(
    item: ResultItem,
    *,
    section: MediaSection | None = None,
    watch_history=None,
) -> str:
    parsed = item.parsed
    if parsed.episode is not None:
        title = stylize(f"Episode {parsed.episode:02d}", C.LIST, C.BOLD)
    else:
        title = stylize(_compact_ep_label(item), C.LIST, C.BOLD)
    if _item_is_watched(section, item, watch_history):
        title = f"{title} {stylize('· watched', C.RED)}"
    seeds = item.entry.seeders
    seed_line = stylize(
        f"{seeds} seeders · {item.entry.leechers} leechers · {item.entry.size}",
        _seed_color(seeds),
    )
    group = item.parsed.release_group or "—"
    return "\n".join(
        [
            title,
            stylize(item.entry.title, C.META),
            seed_line,
            stylize(group, C.CHROME),
        ]
    )


def format_preview_section(section: MediaSection) -> str:
    lines = [stylize(section.label, C.LIST, C.BOLD)]
    if section.expected_episodes:
        lines.append(
            stylize(f"{section.kind.value} · {section.expected_episodes} ep", C.META)
        )
    else:
        lines.append(stylize(section.kind.value, C.META))
    if section.batch_recommended:
        lines.append(stylize("batch recommended", C.META))
    if section.has_episodes:
        nums = sorted(section.episodes)
        lines.append(stylize(f"E{nums[0]:02d}-E{nums[-1]:02d} ({len(nums)})", C.META))
    else:
        lines.append(stylize(f"{len(section.singles)} release(s)", C.META))
    return "\n".join(lines)


def _tui_choose(
    indexed: dict[str, Any],
    previews: dict[str, str],
    lines: list[str],
    *,
    prompt: str,
    header: str,
    expect: str = "ctrl-o,enter",
    query: str = "",
    cursor_key: str | None = None,
) -> tuple[str, Any] | None:
    if not lines:
        return None

    from annie.tui import choose, parse_expect

    rows: list[tuple[str, str, str, Any]] = []
    for line in lines:
        key, _, label = line.partition(SEP)
        if key not in indexed:
            continue
        rows.append((key, label, previews.get(key, ""), indexed[key]))
    if not rows:
        return None

    with pause_search_spinner():
        return choose(
            rows,
            prompt=prompt,
            header=header,
            actions=parse_expect(expect) | {"enter", "right"},
            query=query,
            cursor_key=cursor_key,
        )


def read_query() -> str | None:
    try:
        print(stylize("> ", C.BLUE), end="", flush=True)
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return raw


GROUP_LABELS = {
    "season": "Seasons",
    "movie": "Movies",
    "other": "Other",
}


def _bucket_section(section: MediaSection) -> str:
    if section.kind == MediaKind.EPISODE:
        return "season"
    if section.kind == MediaKind.MOVIE:
        return "movie"
    return "other"


def _group_sections(sections: list[MediaSection]) -> dict[str, list[MediaSection]]:
    groups: dict[str, list[MediaSection]] = {"season": [], "movie": [], "other": []}
    for section in sections:
        groups[_bucket_section(section)].append(section)
    return groups


def pick_anime_candidate(candidates: list, query: str = "") -> Any | None:
    """TUI : confirmer quel anime correspond à la recherche."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    from annie.mal import MalAnime, ranked_candidates

    ranked = ranked_candidates(candidates, query) if query else [
        (0, anime) for anime in candidates
    ]
    indexed: dict[str, MalAnime] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for index, (_score, anime) in enumerate(ranked):
        key = f"a{index:03d}"
        indexed[key] = anime
        year = (anime.aired_from or "")[:4] or "?"
        eps = f"{anime.episodes} ep" if anime.episodes else "? ep"
        romaji = anime.title or "—"
        english = anime.title_english or ""
        line_title = english or romaji
        if english and romaji and english != romaji:
            detail = f"{anime.type} · {year} · {eps} · {romaji}"
        else:
            detail = f"{anime.type} · {year} · {eps}"
        previews[key] = stylize(
            f"{line_title}\n{romaji}\n{english or '—'}\n"
            f"{anime.type} · {year} · {eps}\n"
            f"MAL {anime.mal_id}"
            + (f" · AniList {anime.anilist_id}" if anime.anilist_id else ""),
            C.META,
        )
        lines.append(
            f"{key}{SEP}{stylize(line_title, C.LIST, C.BOLD)}  "
            f"{stylize(detail, C.META)}"
        )

    picked = _tui_choose(
        indexed,
        previews,
        lines,
        prompt="anime",
        header=_fzf_header("↑↓ enter · 1-9 · ? · esc"),
        expect="enter",
    )
    if picked is None:
        return None
    return picked[1]


def pick_group(groups: dict[str, list[MediaSection]]) -> str | None:
    options = [
        (key, GROUP_LABELS[key], groups[key])
        for key in ("season", "movie", "other")
        if groups[key]
    ]
    if len(options) <= 1:
        return options[0][0] if options else None

    indexed: dict[str, str] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for index, (key, label, items) in enumerate(options):
        fzf_key = f"g{index:03d}"
        indexed[fzf_key] = key
        previews[fzf_key] = stylize(f"{label}\n{len(items)} section(s)", C.META)
        lines.append(
            f"{fzf_key}{SEP}{stylize(label, C.LIST, C.BOLD)}  "
            f"{stylize(f'{len(items)}', C.META)}"
        )

    picked = _tui_choose(
        indexed,
        previews,
        lines,
        prompt="type",
        header=_fzf_header("↑↓ enter · ← · ? · esc"),
        expect="left,enter",
    )
    if picked is None:
        return None
    action, group_key = picked
    if action == "left":
        return None
    return group_key


def _pick_section_flat(
    sections: list[MediaSection], *, back_label: str = "search"
) -> MediaSection | None:
    indexed: dict[str, MediaSection] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for index, section in enumerate(sections):
        key = f"s{index:03d}"
        indexed[key] = section
        previews[key] = format_preview_section(section)
        lines.append(f"{key}{SEP}{format_section_line(section)}")

    header = _fzf_header(f"↑↓ enter · ← {back_label} · ? · esc")
    picked = _tui_choose(
        indexed,
        previews,
        lines,
        prompt="season",
        header=header,
        expect="left,enter",
    )
    if picked is None:
        return None
    action, section = picked
    if action == "left":
        return None
    return section


def pick_section(
    sections: list[MediaSection],
    *,
    force_interactive: bool = False,
    resume_from: MediaSection | None = None,
) -> MediaSection | None:
    """Choisit une section (groupe → liste).

    ``force_interactive`` : afficher le TUI même pour une seule section (retour ←).
    ``resume_from`` : au retour depuis les épisodes, rouvrir le même groupe
    (Seasons / Movies / Other) pour que la liste soit identique à l'aller.
    """
    if not sections:
        return None
    if len(sections) == 1 and not force_interactive:
        return sections[0]

    groups = _group_sections(sections)
    multi_group = sum(1 for key in ("season", "movie", "other") if groups[key]) > 1
    current_group: str | None = None
    if resume_from is not None and multi_group:
        bucket = _bucket_section(resume_from)
        if groups.get(bucket):
            current_group = bucket

    while True:
        if multi_group and current_group is None:
            group_key = pick_group(groups)
            if group_key is None:
                return None
            current_group = group_key

        pool = (
            groups[current_group]
            if multi_group and current_group is not None
            else sections
        )

        if len(pool) == 1 and not force_interactive:
            return pool[0]

        section = _pick_section_flat(
            pool, back_label="group" if multi_group else "search"
        )
        if section is not None:
            return section

        # ← depuis la liste de sections → remonter au choix de groupe.
        if multi_group:
            current_group = None
            force_interactive = False
            continue
        return None


def pick_episode(
    section: MediaSection,
    *,
    force_interactive: bool = False,
    watch_history=None,
) -> tuple[str, ResultItem] | None:
    items = section.choices()
    if not items:
        return None
    if len(items) == 1 and not force_interactive:
        return "enter", items[0]

    indexed: dict[str, ResultItem] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for item_index, item in enumerate(items):
        key = f"e{item_index:03d}"
        indexed[key] = item
        previews[key] = format_preview_item(
            item, section=section, watch_history=watch_history
        )
        lines.append(
            f"{key}{SEP}{format_torrent_line(item, section=section, watch_history=watch_history)}"
        )

    prompt = _clip(section.label, 24)
    header = _fzf_header("↑↓ enter · ctrl-o · ← · ? · esc")
    picked = _tui_choose(
        indexed,
        previews,
        lines,
        prompt=prompt,
        header=header,
        expect="ctrl-o,enter,left",
    )
    if picked is None:
        return None
    action, item = picked
    if action == "left":
        return "left", None
    return action, item


class _SkipSubs:
    """Sentinelle fzf — lecture sans sous-titres externes."""


class _BackToEpisode:
    """Sentinelle — retour à la sélection d'épisode depuis le menu langue."""


SKIP_SUBS = _SkipSubs()
BACK_TO_EPISODE = _BackToEpisode()
BackToEpisode = _BackToEpisode


def pick_subtitle_language() -> str | None | _BackToEpisode:
    """TUI : langues + Aucun. Retourne code ISO, None (sans subs), ou BACK_TO_EPISODE."""
    from annie.subtitles import languages, subtitles_api_available, _opensubtitles_config_hint

    if not subtitles_api_available():
        print_status(_opensubtitles_config_hint(), kind="warn")
        return None

    indexed: dict[str, str | _SkipSubs] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []

    for index, lang in enumerate(languages()):
        key = f"lang{index:02d}"
        indexed[key] = lang.code
        previews[key] = stylize(f"{lang.label}\nOpenSubtitles · {lang.os_id}", C.META)
        lines.append(f"{key}{SEP}{stylize(lang.label, C.LIST, C.BOLD)}")

    indexed["lang99"] = SKIP_SUBS
    previews["lang99"] = stylize("Play without external subtitles", C.META)
    lines.append(f"lang99{SEP}{stylize('None', C.MUTED)}")

    picked = _tui_choose(
        indexed,
        previews,
        lines,
        prompt="language",
        header=_fzf_header("↑↓ enter · ← · ? · esc"),
        expect="left,enter",
    )
    if picked is None:
        return None
    action, value = picked
    if action == "left":
        return BACK_TO_EPISODE
    if value is SKIP_SUBS:
        return None
    return str(value)


def pick_catalog(
    sections: list[MediaSection],
    *,
    season: int | None = None,
    episode: int | None = None,
    kind: MediaKind | None = None,
    on_section: Callable[[MediaSection], None] | None = None,
    require_episode_pick: bool = False,
    watch_history=None,
) -> tuple[str, ResultItem] | None:
    if not sections:
        return None

    if episode is not None and not require_episode_pick:
        for section in sections:
            if kind is not None and section.kind != kind:
                continue
            if season is not None and section.season not in {season, None}:
                continue
            item = section.episodes.get(episode)
            if item is not None:
                return "enter", item

    pool = sections
    if season is not None or kind is not None:
        matched = [
            section
            for section in sections
            if (kind is None or section.kind == kind)
            and (season is None or section.season == season)
        ]
        if matched:
            pool = matched

    pinned_season = season
    pinned_episode = episode
    force_section_pick = False
    last_section: MediaSection | None = None
    while True:
        auto_pick_section = (
            not force_section_pick
            and len(pool) == 1
            and (
                require_episode_pick
                or (
                    pinned_episode is None
                    and (pinned_season is not None or kind is not None)
                )
            )
        )
        if auto_pick_section:
            section = pool[0]
        else:
            section = pick_section(
                pool,
                force_interactive=force_section_pick,
                resume_from=last_section if force_section_pick else None,
            )
            force_section_pick = False
        if section is None:
            return None
        last_section = section

        if (
            pinned_episode is not None
            and not require_episode_pick
            and section.has_episodes
        ):
            item = section.episodes.get(pinned_episode)
            if item is not None:
                return "enter", item

        if on_section is not None:
            on_section(section)

        picked = pick_episode(
            section,
            force_interactive=require_episode_pick,
            watch_history=watch_history,
        )
        if picked is None:
            return None
        action, item = picked
        if action == "left":
            force_section_pick = True
            pinned_episode = None
            if pinned_season is not None:
                pinned_season = None
                pool = sections
            continue
        if item is None:
            return None
        return action, item


def copy_magnet(magnet: str) -> bool:
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy", magnet], check=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False
    if shutil.which("xclip"):
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=magnet,
                text=True,
                check=True,
            )
            return True
        except (OSError, subprocess.CalledProcessError):
            return False
    return False
