#!/usr/bin/env python3
"""Render Annie TUI screenshots for the README (Tokyo Night palette)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT  # noqa: E402

from annie.tui import (  # noqa: E402
    chrome,
    select_row,
    shortcut_line,
)
from annie.ui import (  # noqa: E402
    BANNER_ART,
    BANNER_HINT,
    C,
    format_buffer_lines,
    stylize,
)

OUT = ROOT / "docs" / "screenshots"
COLS, ROWS = 92, 26
CELL_W, CELL_H = 9, 18
PAD = 18

# Tokyo Night (Omarchy)
BG = (26, 27, 38)
FG = (169, 177, 214)
DIM = (86, 95, 137)
BLUE = (122, 162, 247)
CYAN = (68, 157, 171)
GREEN = (158, 206, 106)
YELLOW = (224, 175, 104)
RED = (247, 118, 142)
MAGENTA = (173, 142, 230)
BRIGHT = (192, 202, 245)
SEL_BG = (41, 46, 66)

ANSI_SGR = re.compile(r"\033\[([0-9;]*)m")
ANSI_OTHER = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def _font(size: int = 15) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf",
        "/usr/share/fonts/TTF/JetBrainsMono-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _parse_sgr(codes: str, state: dict) -> None:
    if not codes:
        codes = "0"
    for part in codes.split(";"):
        if not part:
            n = 0
        else:
            n = int(part)
        if n == 0:
            state.update(fg=FG, bg=BG, bold=False, dim=False, rev=False)
        elif n == 1:
            state["bold"] = True
        elif n == 2:
            state["dim"] = True
        elif n == 7:
            state["rev"] = True
        elif n == 22:
            state["bold"] = False
            state["dim"] = False
        elif n == 27:
            state["rev"] = False
        elif n == 30:
            state["fg"] = (60, 60, 70)
        elif n == 31:
            state["fg"] = RED
        elif n == 32:
            state["fg"] = GREEN
        elif n == 33:
            state["fg"] = YELLOW
        elif n == 34:
            state["fg"] = BLUE
        elif n == 35:
            state["fg"] = MAGENTA
        elif n == 36:
            state["fg"] = CYAN
        elif n == 37:
            state["fg"] = FG
        elif n == 39:
            state["fg"] = FG
        elif n == 90:
            state["fg"] = DIM
        elif n == 91:
            state["fg"] = RED
        elif n == 92:
            state["fg"] = GREEN
        elif n == 93:
            state["fg"] = YELLOW
        elif n == 94:
            state["fg"] = BLUE
        elif n == 95:
            state["fg"] = MAGENTA
        elif n == 96:
            state["fg"] = CYAN
        elif n == 97:
            state["fg"] = BRIGHT


def _fg_of(state: dict) -> tuple[int, int, int]:
    color = state["fg"]
    if state["dim"]:
        return DIM
    if state["bold"] and color == FG:
        return BRIGHT
    if state["bold"] and color == BLUE:
        return BLUE
    return color


def render_ansi(text: str, *, cols: int = COLS, rows: int = ROWS) -> Image.Image:
    lines = text.splitlines()
    while len(lines) < rows:
        lines.append("")
    lines = lines[:rows]

    width = PAD * 2 + cols * CELL_W
    height = PAD * 2 + rows * CELL_H
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    font = _font(15)

    for y, line in enumerate(lines):
        state = {"fg": FG, "bg": BG, "bold": False, "dim": False, "rev": False}
        x = 0
        i = 0
        while i < len(line) and x < cols:
            m = ANSI_SGR.match(line, i)
            if m:
                _parse_sgr(m.group(1), state)
                i = m.end()
                continue
            m = ANSI_OTHER.match(line, i)
            if m:
                i = m.end()
                continue
            ch = line[i]
            i += 1
            if ch == "\t":
                ch = " "
            fg = _fg_of(state)
            bg = state["bg"]
            if state["rev"]:
                fg, bg = BRIGHT, SEL_BG
            px = PAD + x * CELL_W
            py = PAD + y * CELL_H
            if bg != BG or state["rev"]:
                draw.rectangle([px, py, px + CELL_W, py + CELL_H], fill=bg)
            draw.text((px, py + 1), ch, fill=fg, font=font)
            x += 1

    # Soft border
    draw.rectangle([0, 0, width - 1, height - 1], outline=(36, 40, 59))
    return img


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
        preview=["Preferred resolution", "Influences torrent ranking", "auto · 720p · 1080p · 2160p"],
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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    shots = {
        "prompt.png": shot_prompt,
        "seasons.png": shot_seasons,
        "episodes.png": shot_episodes,
        "settings.png": shot_settings,
        "playback.png": shot_playback,
    }
    for name, builder in shots.items():
        img = render_ansi(builder())
        path = OUT / name
        img.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({img.size[0]}x{img.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
