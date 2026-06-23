"""Interface Annie (fzf + console)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from annie.media import MediaKind, MediaSection, ParsedTitle, ResultItem, minimal_label


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDER = "\033[4m"

    FG = "\033[38;5;252m"
    MUTED = "\033[38;5;245m"
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


def stylize(text: str, *codes: str) -> str:
    if not codes:
        return text
    return f"{''.join(codes)}{text}{C.RESET}"


def banner_line(text: str, color: str) -> str:
    return stylize(text, color, C.BOLD)


FZF_COLOR = (
    "fg:-1,bg:-1,hl:#ff79c6,fg+:#ffffff,bg+:-1,hl+:#ff92df,"
    "prompt:#8be9fd,pointer:#50fa7b,marker:#50fa7b,"
    "spinner:#ffb86c,header:#bd93f9,info:#6272a4,border:#44475a"
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
{stylize('Navigation', C.PALE_PINK, C.BOLD)}
  {stylize('①', C.PALE_PINK)} season / movie / ova
  {stylize('②', C.PALE_PINK)} episode (best torrent)
  {stylize('Enter', C.GREEN)} stream · {stylize('Ctrl-O', C.CYAN)} magnet · {stylize('Esc', C.YELLOW)} back

{stylize('Shortcuts', C.PALE_PINK, C.BOLD)}
  frieren s2e10        stream directly
  frieren s2           pick episode in S2
  frieren movie 3        movie #3

{stylize('Commands', C.PALE_PINK, C.BOLD)}
  help · quit
"""

CACHE_DIR = Path.home() / ".cache" / "annie"
PREVIEW_FILE = CACHE_DIR / "previews.json"
SEP = "\x1f"


def _preview_command() -> str:
    root = Path(__file__).resolve().parent.parent
    return f"PYTHONPATH={root} {sys.executable} -m annie.preview {{1}}"

KIND_COLORS = {
    MediaKind.EPISODE: C.CYAN,
    MediaKind.MOVIE: C.MAGENTA,
    MediaKind.OVA: C.ORANGE,
    MediaKind.SPECIAL: C.YELLOW,
    MediaKind.BATCH: C.ROSE,
    MediaKind.UNKNOWN: C.MUTED,
}


def print_banner() -> None:
    print()
    for line in BANNER_ART:
        print(stylize(line, C.PALE_PINK))
    print()


def print_help() -> None:
    print(HELP)


def print_status(message: str, *, kind: str = "info") -> None:
    colors = {"info": C.CYAN, "ok": C.GREEN, "warn": C.YELLOW, "err": C.RED}
    icon = {"info": "◆", "ok": "✔", "warn": "!", "err": "✖"}.get(kind, "·")
    print(stylize(f"  {icon} {message}", colors.get(kind, C.FG)))


def fzf_available() -> bool:
    return shutil.which("fzf") is not None


def _meta_label(parsed: ParsedTitle) -> str:
    label = parsed.kind.value
    if parsed.season is not None:
        label += f" S{parsed.season:02d}"
    if parsed.episode is not None:
        label += f"E{parsed.episode:02d}"
    return label


def format_torrent_line(item: ResultItem) -> str:
    seeds = item.entry.seeders
    seed_color = C.GREEN if seeds >= 100 else C.YELLOW if seeds >= 20 else C.RED
    kind_color = KIND_COLORS.get(item.parsed.kind, C.MUTED)
    label = minimal_label(item.parsed)
    if item.parsed.episode is not None:
        label = stylize(label, kind_color, C.BOLD)
    return (
        f"{stylize(f'[{seeds:>4}S]', seed_color, C.BOLD)} "
        f"{label}  "
        f"{stylize(item.entry.size, C.MUTED):>8}  "
        f"{item.entry.title}"
    )


def format_section_line(section: MediaSection) -> str:
    color = KIND_COLORS.get(section.kind, C.PALE_PINK)
    if section.has_episodes:
        detail = f"{len(section.episodes)} episode(s)"
        if section.batch_recommended:
            detail += stylize(" · batch recommended", C.YELLOW)
    else:
        detail = f"{len(section.singles)} release(s)"
    return f"{stylize(section.label, color, C.BOLD):20} {stylize(detail, C.MUTED)}"


def format_preview_item(item: ResultItem) -> str:
    return "\n".join(
        [
            stylize(minimal_label(item.parsed), C.WHITE, C.BOLD),
            stylize(item.entry.title, C.MUTED),
            "",
            f"{stylize('type', C.MUTED)}      {_meta_label(item.parsed)}",
            f"{stylize('seeders', C.MUTED)}   {stylize(str(item.entry.seeders), C.GREEN)}",
            f"{stylize('leechers', C.MUTED)}  {item.entry.leechers}",
            f"{stylize('size', C.MUTED)}    {item.entry.size}",
            f"{stylize('score', C.MUTED)}     {item.score:,.0f}",
            f"{stylize('release', C.MUTED)}   {item.parsed.release_group or '—'}",
        ]
    )


def format_preview_section(section: MediaSection) -> str:
    lines = [
        stylize(section.label, C.WHITE, C.BOLD),
        "",
        f"{stylize('type', C.MUTED)}  {section.kind.value}",
    ]
    if section.season is not None:
        lines.append(f"{stylize('season', C.MUTED)}  {section.season:02d}")
    if section.batch_recommended:
        lines.append(stylize("batch recommended for this season", C.YELLOW))
    if section.has_episodes:
        nums = sorted(section.episodes)
        lines.append(f"{stylize('episodes', C.MUTED)}  E{nums[0]:02d} → E{nums[-1]:02d} ({len(nums)})")
    else:
        lines.append(f"{stylize('releases', C.MUTED)}  {len(section.singles)}")
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
        "--cycle",
        "--layout=reverse",
        "--height=85%",
        "--border=rounded",
        "--margin=1,2",
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
                "--preview-window=right:45%:wrap",
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

    PREVIEW_FILE.write_text(json.dumps(previews, ensure_ascii=False), encoding="utf-8")
    code, output = _run_fzf(
        lines,
        prompt=prompt,
        header=header,
        preview=True,
        expect=expect,
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
    return action, value


def read_query() -> str | None:
    try:
        print(stylize("> ", C.PALE_PINK), end="", flush=True)
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return raw or None


def pick_section(sections: list[MediaSection]) -> MediaSection | None:
    if not sections:
        return None
    if len(sections) == 1:
        return sections[0]

    indexed: dict[str, MediaSection] = {}
    previews: dict[str, str] = {}
    lines: list[str] = []
    for index, section in enumerate(sections):
        key = f"s{index:03d}"
        indexed[key] = section
        previews[key] = format_preview_section(section)
        lines.append(f"{key}{SEP}{format_section_line(section)}")

    header = (
        f"{stylize('①', C.PALE_PINK)} type / season  ·  "
        f"{stylize('Enter', C.GREEN)} continue  ·  "
        f"{stylize('Esc', C.YELLOW)} back"
    )
    picked = _fzf_choose(indexed, previews, lines, prompt="season / movie / ova> ", header=header)
    if picked is None:
        return None
    _, section = picked
    return section


def _episode_query(item: ResultItem) -> str:
    if item.parsed.episode is not None:
        return f"E{item.parsed.episode:02d}"
    return minimal_label(item.parsed)


def pick_episode(section: MediaSection) -> tuple[str, ResultItem] | None:
    items = section.choices()
    if not items:
        return None
    if len(items) == 1:
        return "enter", items[0]

    index = 0
    while 0 <= index < len(items):
        indexed: dict[str, ResultItem] = {}
        previews: dict[str, str] = {}
        lines: list[str] = []
        for item_index, item in enumerate(items):
            key = f"e{item_index:03d}"
            indexed[key] = item
            previews[key] = format_preview_item(item)
            lines.append(f"{key}{SEP}{format_torrent_line(item)}")

        if section.has_episodes:
            prompt = f"{section.label} · episode> "
            step = "② episode"
        else:
            prompt = f"{section.label}> "
            step = "② release"

        header = (
            f"{stylize(step, C.PALE_PINK)}  ·  "
            f"{stylize('Enter', C.GREEN)} stream  ·  "
            f"{stylize('Ctrl-N/P', C.CYAN)} next/prev ep  ·  "
            f"{stylize('Ctrl-O', C.CYAN)} magnet  ·  "
            f"{stylize('Esc', C.YELLOW)} back"
        )
        picked = _fzf_choose(
            indexed,
            previews,
            lines,
            prompt=prompt,
            header=header,
            expect="ctrl-n,ctrl-p,ctrl-o,enter",
            query=_episode_query(items[index]),
        )
        if picked is None:
            return None
        action, item = picked
        if action == "ctrl-n":
            index = min(index + 1, len(items) - 1)
            continue
        if action == "ctrl-p":
            index = max(index - 1, 0)
            continue
        return action, item
    return None


def pick_catalog(
    sections: list[MediaSection],
    *,
    season: int | None = None,
    episode: int | None = None,
    kind: MediaKind | None = None,
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

    if season is not None or kind is not None:
        matched = [
            section
            for section in sections
            if (kind is None or section.kind == kind)
            and (season is None or section.season == season)
        ]
        if len(matched) == 1:
            section = matched[0]
        elif matched:
            section = pick_section(matched)
        else:
            section = pick_section(sections)
    else:
        section = pick_section(sections)

    if section is None:
        return None

    if episode is not None and section.has_episodes:
        item = section.episodes.get(episode)
        if item is not None:
            return "enter", item

    return pick_episode(section)


def copy_magnet(magnet: str) -> bool:
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy", magnet], check=False)
        return True
    if shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard"], input=magnet, text=True, check=False)
        return True
    return False


def preview_key(key: str) -> None:
    if PREVIEW_FILE.exists():
        print(json.loads(PREVIEW_FILE.read_text(encoding="utf-8")).get(key, ""))
