#!/usr/bin/env python3
"""Print one Annie screen to the TTY, then sleep (for grim capture)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT  # noqa: E402

from annie.tui import chrome, select_row, shortcut_line  # noqa: E402
from annie.ui import BANNER_ART, BANNER_HINT, C, format_buffer_lines, stylize  # noqa: E402

COLS, ROWS = 92, 22


def clear() -> None:
    # Hide cursor — foot would otherwise leave a block under the UI.
    sys.stdout.write("\033[?25l\033[2J\033[H\033[3J")
    sys.stdout.flush()


def shot_prompt() -> str:
    lines = [""] + list(BANNER_ART) + ["", BANNER_HINT, "", f"  {stylize('>', C.BLUE)} frieren_"]
    while len(lines) < ROWS:
        lines.append("")
    return "\n".join(lines[:ROWS])


def shot_seasons() -> str:
    width = COLS - 1
    items = [
        (False, f"{stylize('Season 01', C.LIST, C.BOLD)}  {stylize('28 ep', C.META)}"),
        (True, f"{stylize('Season 02', C.LIST, C.BOLD)}  {stylize('12 ep', C.META)}"),
        (False, f"{stylize('Season 03', C.LIST, C.BOLD)}  {stylize('12 ep · airing', C.META)}"),
        (False, f"{stylize('Movie', C.LIST, C.BOLD)}       {stylize('Frieren Beyond Journey', C.META)}"),
    ]
    body = [select_row(label, width, selected=sel) for sel, label in items]
    footer = shortcut_line(
        [("↑↓", "move"), ("enter", "open"), ("←", "back"), ("?", "help")],
        prefix="",
    )
    return chrome(
        title="seasons",
        body=body,
        footer=footer,
        preview=[
            "Frieren: Beyond Journey's End",
            "Season 02 · 12 episodes",
            "AniList / AllAnime",
        ],
        cols=COLS,
        rows=ROWS,
        meta=f"{stylize('2/4', C.MUTED)}",
    )


def shot_episodes() -> str:
    width = COLS - 1
    rows_data = []
    for ep in range(1, 9):
        label = (
            f"{stylize(f'E{ep:02d}', C.LIST, C.BOLD)}  "
            f"{stylize('[SubsPlease] Frieren S2', C.META)}  "
            f"{stylize('1080p · 42S', C.MUTED)}"
        )
        if ep == 3:
            label += f"  {stylize('●', C.RED)}"
        rows_data.append((ep == 4, label))
    body = [select_row(label, width, selected=sel) for sel, label in rows_data]
    footer = shortcut_line(
        [("↑↓", "move"), ("enter", "play"), ("ctrl-o", "magnet"), ("esc", "back")],
        prefix="",
    )
    return chrome(
        title="Season 02",
        body=body,
        footer=footer,
        preview=[
            "E04 · Beyond Journey's End",
            "SubsPlease · 1080p · 42 seeders",
            "batch magnet ready",
        ],
        cols=COLS,
        rows=ROWS,
        meta=f"{stylize('4/12', C.MUTED)}",
    )


def shot_settings() -> str:
    width = COLS - 1
    fields = [
        (False, "OpenSubtitles API key", "••••••••"),
        (False, "Subtitles", "yes"),
        (True, "Preferred resolution", "1080p"),
        (False, "Player", "mpv"),
        (False, "Metadata", "auto"),
        (False, "Seed while watching", "yes"),
        (False, "Preferred groups", "SubsPlease, Erai-raws"),
    ]
    body = []
    for sel, name, value in fields:
        label = f"{stylize(name, C.LIST)}{stylize(' · ', C.MUTED)}{stylize(value, C.META)}"
        body.append(select_row(label, width, selected=sel))
    footer = shortcut_line(
        [("↑↓", "move"), ("enter", "edit"), ("←→", "cycle"), ("esc", "back")],
        prefix="",
    )
    return chrome(
        title="settings",
        body=body,
        footer=footer,
        preview=[
            "Preferred resolution",
            "Influences torrent ranking",
            "auto · 720p · 1080p · 2160p",
        ],
        cols=COLS,
        rows=ROWS,
    )


def shot_playback() -> str:
    header = [
        stylize("◆ Frieren S02E04", C.YELLOW, C.BOLD),
        stylize("─" * 48, C.MUTED),
        f"{stylize('subs  ', C.MUTED)}{stylize('Frieren.S02E04.en.srt', C.CYAN)}",
        "",
    ]
    bars = format_buffer_lines(
        contiguous=62 * 1024 * 1024,
        ready=180 * 1024 * 1024,
        file_size=1400 * 1024 * 1024,
        target_bytes=80 * 1024 * 1024,
        peer_hint="18 peers",
        download_kib=1840,
        player="mpv",
        seed=True,
        filename="[SubsPlease] Frieren - S02E04 (1080p).mkv",
    )
    lines = header + bars.splitlines()
    while len(lines) < ROWS:
        lines.append("")
    return "\n".join(lines[:ROWS])


SHOTS = {
    "prompt": shot_prompt,
    "seasons": shot_seasons,
    "episodes": shot_episodes,
    "settings": shot_settings,
    "playback": shot_playback,
}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "prompt"
    if name not in SHOTS:
        print(f"unknown shot: {name}", file=sys.stderr)
        return 2
    clear()
    sys.stdout.write(SHOTS[name]())
    sys.stdout.write("\n")
    sys.stdout.flush()
    # Keep the window open for grim.
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 3600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
