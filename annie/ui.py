"""Interface Annie (fzf + console)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    if parsed.episode is not None and parsed.kind in {MediaKind.EPISODE, MediaKind.BATCH}:
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
        lines.append(stylize(f"{section.kind.value} · {section.expected_episodes} ep", C.META))
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
        header=_fzf_header("Enter · Esc"),
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

    header = _fzf_header("Enter · Esc")
    picked = _fzf_choose(indexed, previews, lines, prompt="section> ", header=header)
    if picked is None:
        return None
    _, section = picked
    return section


def pick_section(sections: list[MediaSection]) -> MediaSection | None:
    if not sections:
        return None
    if len(sections) == 1:
        return sections[0]

    groups = _group_sections(sections)
    active = [key for key in ("season", "movie", "other") if groups[key]]
    if len(active) > 1:
        group_key = pick_group(groups)
        if group_key is None:
            return None
        sections = groups[group_key]

    if len(sections) == 1:
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
    header = _fzf_header("Enter · Ctrl-N/P · Ctrl-O · Esc")
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
            expect="ctrl-n,ctrl-p,ctrl-o,enter",
            query=_episode_query(items[index]) if jumped else "",
        )
        if picked is None:
            return None
        action, item = picked
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
