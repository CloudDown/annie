"""Palette Annie — Tokyo Night (direction Omarchy)."""

from __future__ import annotations

# /usr/share/omarchy/themes/tokyo-night/colors.toml
ACCENT = "#7aa2f7"
SELECTION = "#292e42"
MUTED = "#414868"
BACKGROUND = "#1a1b26"
FOREGROUND = "#a9b1d6"
FG_DIM = "#565f89"
FG_BRIGHT = "#c0caf5"
RED = "#f7768e"
YELLOW = "#e0af68"
GREEN = "#9ece6a"
CYAN = "#449dab"
MAGENTA = "#ad8ee6"


def rgb(hex_color: str, *, bg: bool = False) -> str:
    raw = hex_color.lstrip("#")
    r = int(raw[0:2], 16)
    g = int(raw[2:4], 16)
    b = int(raw[4:6], 16)
    return f"\033[{48 if bg else 38};2;{r};{g};{b}m"


RESET = "\033[0m"
BOLD = "\033[1m"

FG = rgb(FOREGROUND)
DIM = rgb(FG_DIM)
BRIGHT = rgb(FG_BRIGHT)
ACC = rgb(ACCENT)
RULE = rgb(MUTED)
OK = rgb(GREEN)
WARN = rgb(YELLOW)
ERR = rgb(RED)
CYAN_FG = rgb(CYAN)
MAG = rgb(MAGENTA)
SEL = BOLD + rgb(FG_BRIGHT) + rgb(SELECTION, bg=True)
SEL_BAR = rgb(ACCENT) + rgb(SELECTION, bg=True)
