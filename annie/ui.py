"""Interface Annie (fzf + console)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

from annie.paths import cache_dir
from annie.media import MediaKind, MediaSection, ResultItem, minimal_label


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDER = "\033[4m"

    # Terminal / banner
    FG = "\033[38;5;252m"
    MUTED = "\033[38;5;244m"
    PINK = "\033[38;5;213m"
    PALE_PINK = "\033[38;5;218m"
    ROSE = "\033[38;5;176m"
    CYAN = "\033[38;5;86m"
    GREEN = "\033[38;5;84m"
    YELLOW = "\033[38;5;220m"
    MAGENTA = "\033[38;5;141m"
    ORANGE = "\033[38;5;215m"
    RED = "\033[38;5;203m"
    BLUE = "\033[38;5;117m"
    WHITE = "\033[38;5;255m"

    # fzf list — restrained palette
    LIST = "\033[38;5;252m"
    META = "\033[38;5;243m"
    CHROME = "\033[38;5;245m"
    SEED_HIGH = "\033[38;5;114m"
    SEED_MID = "\033[38;5;179m"
    SEED_LOW = "\033[38;5;174m"


def stylize(text: str, *codes: str) -> str:
    if not codes:
        return text
    return f"{''.join(codes)}{text}{C.RESET}"


def banner_line(text: str, color: str) -> str:
    return stylize(text, color, C.BOLD)


BUFFER_BAR_WIDTH = 24


def progress_bar(pct: int, width: int = BUFFER_BAR_WIDTH) -> str:
    pct = max(0, min(100, pct))
    filled = pct * width // 100
    return "█" * filled + "░" * (width - filled)


def _mib_label(current: int, total: int) -> str:
    return f"{current // 1024 // 1024}/{max(1, total) // 1024 // 1024} MiB"


def format_buffer_lines(
    *,
    contiguous: int,
    ready: int,
    file_size: int,
    target_bytes: int,
    peer_hint: str,
    download_kib: float,
    extra_hint: str = "",
) -> str:
    cont_pct = min(100, contiguous * 100 // target_bytes) if target_bytes else 0
    file_pct = ready * 100 // file_size if file_size else 0
    bar_cont = stylize(progress_bar(cont_pct), C.GREEN)
    bar_file = stylize(progress_bar(file_pct), C.PALE_PINK)
    rate_part = (
        stylize(f"{download_kib:.0f} KiB/s", C.GREEN) if download_kib > 0 else ""
    )
    hint = stylize(extra_hint, C.MUTED) if extra_hint else ""
    if rate_part:
        meta = f"{stylize(peer_hint, C.MUTED)} · {rate_part}{hint}"
    else:
        meta = f"{stylize(peer_hint, C.MUTED)}{hint}"
    lines = [
        _s("buffer", C.MUTED),
        (
            f"{stylize('contigu', C.MUTED)}  [{bar_cont}] {cont_pct:3d}%  "
            f"{_mib_label(contiguous, target_bytes)}"
        ),
        (
            f"{stylize('fichier', C.MUTED)}  [{bar_file}] {file_pct:3d}%  "
            f"{_mib_label(ready, file_size)}"
        ),
        meta,
    ]
    return "\n".join(lines)


class BufferStatusDisplay:
    def __init__(self) -> None:
        self._line_count = 0

    def update(self, text: str) -> None:
        line_count = text.count("\n") + 1
        if self._line_count:
            sys.stdout.write(f"\033[{self._line_count}A")
        for line in text.split("\n"):
            sys.stdout.write(f"\033[K{line}\n")
        sys.stdout.flush()
        self._line_count = line_count

    def finish(self, message: str) -> None:
        if self._line_count:
            sys.stdout.write(f"\033[{self._line_count}A\033[J")
        print(message, flush=True)
        self._line_count = 0


# fzf chrome — explicit selection bg so light terminals stay readable
FZF_COLOR = (
    "fg:-1,bg:-1,hl:#7aa2f7,"
    "fg+:#f4f6ff,bg+:#3a4a6b,hl+:#a8c4ff,"
    "prompt:#9aa0a6,pointer:#f0a8d0,marker:#7aa2f7,"
    "spinner:#6b7280,header:#6b7280,info:#6b7280,border:#4b5563"
)

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


HELP = f"""
{stylize("Navigation", C.PALE_PINK, C.BOLD)}
  {stylize("①", C.PALE_PINK)} season / movie / ova
  {stylize("②", C.PALE_PINK)} episode (best torrent)
  {stylize("Enter/→", C.GREEN)} select · {stylize("Ctrl-O", C.CYAN)} magnet · {stylize("←", C.YELLOW)} back · {stylize("Esc", C.YELLOW)} cancel

{stylize("Shortcuts", C.PALE_PINK, C.BOLD)}
  frieren s2e10        stream directly
  frieren s2           pick episode in S2
  frieren movie 3        movie #3

{stylize("Commands", C.PALE_PINK, C.BOLD)}
  help · quit
"""

CACHE_DIR = cache_dir()
PREVIEW_FILE = CACHE_DIR / "previews.json"
SEP = "\x1f"
_preview_digest: str | None = None


def _preview_command() -> str:
    path = shlex.quote(str(PREVIEW_FILE))
    if shutil.which("jq"):
        return f"jq -r --arg k {{1}} '.[$k] // empty' {path}"
    root = Path(__file__).resolve().parent.parent
    exe = shlex.quote(sys.executable)
    return f"PYTHONPATH={shlex.quote(str(root))} {exe} -m annie.preview {{1}}"


def _write_previews(previews: dict[str, str]) -> None:
    global _preview_digest
    payload = json.dumps(previews, ensure_ascii=False, sort_keys=True)
    if payload == _preview_digest:
        return
    _preview_digest = payload
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PREVIEW_FILE.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(PREVIEW_FILE)


def clear_terminal() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def print_banner() -> None:
    clear_terminal()
    print()
    for line in BANNER_ART:
        print(stylize(line, C.PALE_PINK))
    print()


def print_help() -> None:
    print(HELP)


def print_status(message: str, *, kind: str = "info") -> None:
    colors = {"info": C.GREEN, "ok": C.GREEN, "warn": C.YELLOW, "err": C.RED}
    icon = {"info": "◆", "ok": "✔", "warn": "!", "err": "✖"}.get(kind, "·")
    print(stylize(f"  {icon} {message}", colors.get(kind, C.FG)))


_STREAM_TONES = {
    "info": C.FG,
    "ok": C.GREEN,
    "warn": C.YELLOW,
    "muted": C.MUTED,
    "accent": C.CYAN,
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


def format_buffer_ready(mib: int) -> str:
    return format_stream_log("prêt", f"{mib} MiB contigu", tone="ok")


def format_buffer_quick_start(mib: int) -> str:
    return format_stream_log("démarrage rapide", f"{mib} MiB contigu", tone="info")


def format_buffer_forced_start(mib: int) -> str:
    return format_stream_log(
        "démarrage forcé",
        f"{mib} MiB contigu, tentative mpv",
        tone="warn",
    )


def format_buffer_local_file(mib: int) -> str:
    return format_stream_log("fichier local", f"{mib} MiB contigu", tone="ok")


def log_buffer_pause() -> None:
    line = f"{_annie_prefix()}{_s('⏸', C.RED)}  {_s('buffer insuffisant', C.RED)}"
    print(line, flush=True)


def log_buffer_resume() -> None:
    line = f"{_annie_prefix()}{_s('▶', C.GREEN)}  {_s('reprise', C.GREEN)}"
    print(line, flush=True)


_T = TypeVar("_T")
_SEARCH_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def run_search_spinner(query: str, fn: Callable[[], _T]) -> _T:
    """Run *fn* while showing a braille spinner on the current line."""
    del query  # titre affiché ailleurs (fzf) — spinner volontairement minimal

    if not sys.stdout.isatty():
        print(_s("Searching", C.MAGENTA), flush=True)
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

    threading.Thread(target=worker, daemon=True).start()

    frame = 0
    while not done.is_set():
        spin = _SEARCH_SPINNER[frame % len(_SEARCH_SPINNER)]
        line = f"{_s('Searching · ', C.MAGENTA)}{_s(spin, C.MAGENTA)}"
        print(f"\r{line}", end="", flush=True)
        frame += 1
        done.wait(0.09)

    print("\r\033[K", end="", flush=True)

    if error:
        raise error[0]
    return result[0]


def fzf_available() -> bool:
    return shutil.which("fzf") is not None


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
    cols, _ = _terminal_size()
    name = _clip(filename, max(40, cols - 28))
    line = (
        f"{_annie_prefix()}{_s('lecture', C.MUTED)}  "
        f"{_s(name, C.FG)}  {_s(player, C.MUTED)}"
    )
    print(line, flush=True)


def _fzf_header(text: str) -> str:
    return stylize(text, C.CHROME)


def _seed_color(seeders: int) -> str:
    if seeders >= 100:
        return C.SEED_HIGH
    if seeders >= 20:
        return C.SEED_MID
    return C.SEED_LOW


def _fzf_height() -> str:
    _, rows = _terminal_size()
    if rows < 28:
        return "100%"
    if rows < 40:
        return "92%"
    return "85%"


def _preview_window() -> str:
    _, rows = _terminal_size()
    if rows < 28:
        return "down:7:wrap:border-top"
    if _compact_ui():
        return "down:30%:wrap:border-top"
    return "down:35%:wrap:border-top"


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


def format_torrent_line(item: ResultItem) -> str:
    return stylize(_list_item_label(item), C.LIST, C.BOLD)


def format_section_line(section: MediaSection) -> str:
    label = _clip(section.label, 28 if _compact_ui() else 40)
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


def format_preview_item(item: ResultItem) -> str:
    parsed = item.parsed
    if parsed.episode is not None:
        title = stylize(f"Episode {parsed.episode:02d}", C.LIST, C.BOLD)
    else:
        title = stylize(_compact_ep_label(item), C.LIST, C.BOLD)
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


def _run_fzf(
    lines: list[str] | None,
    *,
    prompt: str,
    header: str,
    preview: bool = False,
    expect: str | None = None,
    query: str = "",
) -> tuple[int, str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        "fzf",
        "--ansi",
        "--no-sort",
        "--cycle",
        "--layout=reverse",
        f"--height={_fzf_height()}",
        "--border=rounded",
        "--margin=0,1",
        f"--color={FZF_COLOR}",
        f"--prompt={prompt}",
        f"--header={header}",
    ]
    if lines:
        command.append(f"--delimiter={SEP}")
    if query:
        command.extend([f"--query={query}"])
    if preview:
        preview_cmd = _preview_command()
        command.extend(
            [
                "--with-nth=2..",
                "--preview",
                preview_cmd,
                f"--preview-window={_preview_window()}",
            ]
        )
    if expect:
        command.extend(["--expect", expect])

    proc = subprocess.run(
        command,
        input="\n".join(lines or []),
        stdout=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout


def _extend_expect(expect: str) -> str:
    keys = [key.strip() for key in expect.split(",") if key.strip()]
    if "right" not in keys:
        keys.append("right")
    return ",".join(keys)


def _fzf_choose(
    indexed: dict[str, Any],
    previews: dict[str, str],
    lines: list[str],
    *,
    prompt: str,
    header: str,
    expect: str = "ctrl-o,enter",
    query: str = "",
) -> tuple[str, Any] | None:
    if not lines:
        return None

    _write_previews(previews)
    code, output = _run_fzf(
        lines,
        prompt=prompt,
        header=header,
        preview=True,
        expect=_extend_expect(expect),
        query=query,
    )
    if code != 0 or not output.strip():
        return None

    parts = output.splitlines()
    payload = parts[-1]
    key = payload.split(SEP, 1)[0]
    value = indexed.get(key)
    if value is None:
        return None
    action = "enter" if len(parts) == 1 else parts[0]
    if action == "right":
        action = "enter"
    return action, value


def read_query() -> str | None:
    try:
        print(stylize("> ", C.PALE_PINK), end="", flush=True)
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return raw or None


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

    picked = _fzf_choose(
        indexed,
        previews,
        lines,
        prompt="group> ",
        header=_fzf_header("→ Enter · Esc"),
    )
    if picked is None:
        return None
    _, group_key = picked
    return group_key


def _pick_section_flat(sections: list[MediaSection]) -> MediaSection | None:
    indexed: dict[str, MediaSection] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for index, section in enumerate(sections):
        key = f"s{index:03d}"
        indexed[key] = section
        previews[key] = format_preview_section(section)
        lines.append(f"{key}{SEP}{format_section_line(section)}")

    header = _fzf_header("→ Enter · Esc")
    picked = _fzf_choose(indexed, previews, lines, prompt="section> ", header=header)
    if picked is None:
        return None
    _, section = picked
    return section


def pick_section(
    sections: list[MediaSection], *, force_interactive: bool = False
) -> MediaSection | None:
    if not sections:
        return None
    if len(sections) == 1 and not force_interactive:
        return sections[0]

    groups = _group_sections(sections)
    active = [key for key in ("season", "movie", "other") if groups[key]]
    if len(active) > 1 and not force_interactive:
        group_key = pick_group(groups)
        if group_key is None:
            return None
        sections = groups[group_key]

    if len(sections) == 1 and not force_interactive:
        return sections[0]
    return _pick_section_flat(sections)


def _episode_query(item: ResultItem) -> str:
    if item.parsed.episode is not None:
        return f"{item.parsed.episode:02d}"
    return minimal_label(item.parsed)


def pick_episode(section: MediaSection) -> tuple[str, ResultItem] | None:
    items = section.choices()
    if not items:
        return None
    if len(items) == 1:
        return "enter", items[0]

    indexed: dict[str, ResultItem] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for item_index, item in enumerate(items):
        key = f"e{item_index:03d}"
        indexed[key] = item
        previews[key] = format_preview_item(item)
        lines.append(f"{key}{SEP}{format_torrent_line(item)}")

    prompt = f"{_clip(section.label, 18)}> "
    header = _fzf_header("→ Enter · Ctrl-N/P · Ctrl-O · ← · Esc")
    index = 0
    jumped = False
    while 0 <= index < len(items):
        # fzf --query filters the list; only pass a query after Ctrl-N/P navigation.
        picked = _fzf_choose(
            indexed,
            previews,
            lines,
            prompt=prompt,
            header=header,
            expect="ctrl-n,ctrl-p,ctrl-o,enter,left",
            query=_episode_query(items[index]) if jumped else "",
        )
        if picked is None:
            return None
        action, item = picked
        if action == "left":
            return "left", None
        if action == "ctrl-n":
            index = min(index + 1, len(items) - 1)
            jumped = True
            continue
        if action == "ctrl-p":
            index = max(index - 1, 0)
            jumped = True
            continue
        return action, item
    return None


class _SkipSubs:
    """Sentinelle fzf — lecture sans sous-titres externes."""


class _BackToEpisode:
    """Sentinelle — retour à la sélection d'épisode depuis le menu langue."""


SKIP_SUBS = _SkipSubs()
BACK_TO_EPISODE = _BackToEpisode()
BackToEpisode = _BackToEpisode


def pick_subtitle_language() -> str | None | _BackToEpisode:
    """fzf : langues + Aucun. Retourne code ISO, None (sans subs), ou BACK_TO_EPISODE."""
    from annie.subtitles import languages

    indexed: dict[str, str | _SkipSubs] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []

    for index, lang in enumerate(languages()):
        key = f"lang{index:02d}"
        indexed[key] = lang.code
        previews[key] = stylize(f"{lang.label}\nOpenSubtitles · {lang.os_id}", C.META)
        lines.append(f"{key}{SEP}{stylize(lang.label, C.LIST, C.BOLD)}")

    indexed["lang99"] = SKIP_SUBS
    previews["lang99"] = stylize("Lecture sans sous-titres externes", C.META)
    lines.append(f"lang99{SEP}{stylize('Aucun', C.MUTED)}")

    picked = _fzf_choose(
        indexed,
        previews,
        lines,
        prompt="langue> ",
        header=_fzf_header("→ Enter · ← · Esc"),
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
) -> tuple[str, ResultItem] | None:
    if not sections:
        return None

    if episode is not None:
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

    force_section_pick = False
    while True:
        if (
            not force_section_pick
            and len(pool) == 1
            and (season is not None or kind is not None)
        ):
            section = pool[0]
        else:
            force_section_pick = False
            section = pick_section(pool, force_interactive=force_section_pick)
        if section is None:
            return None

        if episode is not None and section.has_episodes:
            item = section.episodes.get(episode)
            if item is not None:
                return "enter", item

        if on_section is not None:
            on_section(section)

        picked = pick_episode(section)
        if picked is None:
            return None
        action, item = picked
        if action == "left":
            force_section_pick = True
            continue
        if item is None:
            return None
        return action, item


def copy_magnet(magnet: str) -> bool:
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["clip"],
                input=magnet,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
            )
            return True
        except OSError:
            return False
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy", magnet], check=False)
        return True
    if shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "clipboard"], input=magnet, text=True, check=False
        )
        return True
    return False


def preview_key(key: str) -> None:
    if PREVIEW_FILE.exists():
        print(json.loads(PREVIEW_FILE.read_text(encoding="utf-8")).get(key, ""))
