"""TUI in-process : picker plein écran, sans fzf."""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import sys
from typing import Any, Callable

ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

# Surlignage liste — lisible sur thème clair ou sombre.
_SEL = "\033[1;38;5;255;48;5;61m"
_RESET = "\033[0m"
_DIM = "\033[38;5;245m"
_TITLE = "\033[1;38;5;218m"
_RULE = "\033[38;5;238m"


def available() -> bool:
    """True si un TTY est utilisable pour le picker."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return True
    if sys.platform == "win32":
        return False
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
        os.close(fd)
        return True
    except OSError:
        return False


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def clip_visible(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if visible_len(text) <= width:
        return text
    out: list[str] = []
    n = 0
    i = 0
    while i < len(text) and n < width:
        match = ANSI_RE.match(text, i)
        if match:
            out.append(match.group(0))
            i = match.end()
            continue
        out.append(text[i])
        n += 1
        i += 1
    return "".join(out) + _RESET


def fuzzy_score(query: str, text: str) -> int | None:
    """Score subsequence façon fzf. None = pas de match. Plus haut = mieux."""
    if not query:
        return 0
    needle = query.casefold()
    hay = text.casefold()
    start = 0
    score = 0
    prev = -2
    first = -1
    for i, char in enumerate(needle):
        found = hay.find(char, start)
        if found < 0:
            return None
        if first < 0:
            first = found
        if found == start:
            score += 8
        if found == prev + 1:
            score += 16
        if found == 0 or (found > 0 and not hay[found - 1].isalnum()):
            score += 12
        prev = found
        start = found + 1
    score -= first
    score -= len(hay) - len(needle)
    return score


def filter_rows(
    rows: list[tuple[str, str, str, Any]], query: str
) -> list[tuple[int, tuple[str, str, str, Any]]]:
    """Retourne (score, row) triés, matches seulement."""
    scored: list[tuple[int, int, tuple[str, str, str, Any]]] = []
    for index, row in enumerate(rows):
        hay = strip_ansi(row[1])
        score = fuzzy_score(query, hay)
        if score is None:
            continue
        scored.append((score, -index, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(score, row) for score, _index, row in scored]


def parse_expect(expect: str) -> set[str]:
    return {part.strip() for part in expect.split(",") if part.strip()}


class _UnixKeys:
    def __init__(self, fd: int) -> None:
        import termios
        import tty

        self.fd = fd
        self._termios = termios
        self._old = termios.tcgetattr(fd)
        tty.setraw(fd, termios.TCSANOW)

    def restore(self) -> None:
        try:
            self._termios.tcsetattr(self.fd, self._termios.TCSADRAIN, self._old)
        except OSError:
            pass

    def read(self) -> str:
        chunk = self._read_bytes()
        if not chunk:
            return "resize"
        if chunk == b"\x03":
            return "ctrl-c"
        if chunk == b"\x0e":
            return "ctrl-n"
        if chunk == b"\x0f":
            return "ctrl-o"
        if chunk == b"\x10":
            return "ctrl-p"
        if chunk == b"\x15":
            return "ctrl-u"
        if chunk in {b"\r", b"\n"}:
            return "enter"
        if chunk in {b"\x7f", b"\x08"}:
            return "backspace"
        if chunk == b"\x1b":
            return self._escape()
        try:
            return "char:" + chunk.decode("utf-8")
        except UnicodeDecodeError:
            return "esc"

    def _read_bytes(self) -> bytes:
        try:
            first = os.read(self.fd, 1)
        except InterruptedError:
            return b""
        if not first:
            return b""
        if first[0] < 0x80 or first == b"\x1b":
            return first
        need = 1
        if first[0] & 0xE0 == 0xC0:
            need = 2
        elif first[0] & 0xF0 == 0xE0:
            need = 3
        elif first[0] & 0xF8 == 0xF0:
            need = 4
        extra = b""
        while len(first) + len(extra) < need:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                break
            extra += os.read(self.fd, need - len(first) - len(extra))
        return first + extra

    def _escape(self) -> str:
        ready, _, _ = select.select([self.fd], [], [], 0.04)
        if not ready:
            return "esc"
        rest = os.read(self.fd, 8)
        seq = rest.decode("latin1", errors="replace")
        mapping = {
            "[A": "up",
            "[B": "down",
            "[C": "right",
            "[D": "left",
            "[H": "home",
            "[F": "end",
            "OA": "up",
            "OB": "down",
            "OC": "right",
            "OD": "left",
        }
        for prefix, name in mapping.items():
            if seq.startswith(prefix):
                return name
        if seq.startswith("[3~"):
            return "delete"
        return "esc"


class _WinKeys:
    def restore(self) -> None:
        return

    def read(self) -> str:
        import msvcrt

        char = msvcrt.getwch()
        if char in {"\x00", "\xe0"}:
            code = msvcrt.getwch()
            return {
                "H": "up",
                "P": "down",
                "K": "left",
                "M": "right",
                "S": "delete",
                "G": "home",
                "O": "end",
            }.get(code, "esc")
        if char == "\x03":
            return "ctrl-c"
        if char == "\x0e":
            return "ctrl-n"
        if char == "\x0f":
            return "ctrl-o"
        if char == "\x10":
            return "ctrl-p"
        if char == "\x15":
            return "ctrl-u"
        if char in {"\r", "\n"}:
            return "enter"
        if char in {"\x08", "\x7f"}:
            return "backspace"
        if char == "\x1b":
            return "esc"
        if char.isprintable():
            return f"char:{char}"
        return "esc"


def _open_tty():
    if sys.stdin.isatty() and sys.stdout.isatty():
        return sys.stdin, sys.stdout, False
    if sys.platform == "win32":
        return sys.stdin, sys.stdout, False
    tty = open("/dev/tty", "r+", encoding="utf-8", errors="replace")  # noqa: SIM115
    return tty, tty, True


def choose(
    rows: list[tuple[str, str, str, Any]],
    *,
    prompt: str,
    header: str,
    actions: set[str],
    query: str = "",
    cursor_key: str | None = None,
    on_suspend: Callable[[], None] | None = None,
) -> tuple[str, Any] | None:
    """Picker plein écran. rows = (key, label_ansi, preview_ansi, value)."""
    if not rows:
        return None
    if not available():
        return None

    stdin, stdout, owned = _open_tty()
    resize = {"flag": False}

    def _on_winch(_signum, _frame) -> None:
        resize["flag"] = True

    prev_winch = None
    if hasattr(signal, "SIGWINCH"):
        prev_winch = signal.signal(signal.SIGWINCH, _on_winch)

    keys: _UnixKeys | _WinKeys
    if sys.platform == "win32":
        keys = _WinKeys()
    else:
        keys = _UnixKeys(stdin.fileno())

    if on_suspend is not None:
        on_suspend()

    query_buf = query
    scroll = 0
    cursor = 0
    if cursor_key is not None:
        for index, row in enumerate(rows):
            if row[0] == cursor_key:
                cursor = index
                break

    def write(text: str) -> None:
        stdout.write(text)
        stdout.flush()

    write("\033[?1049h\033[?25l\033[2J\033[H")
    try:
        while True:
            cols, lines = _term_size()
            filtered = filter_rows(rows, query_buf)
            if not filtered:
                filtered = []
            if cursor >= len(filtered):
                cursor = max(0, len(filtered) - 1)
            if cursor < 0:
                cursor = 0

            preview_h = _preview_height(lines)
            list_h = max(3, lines - preview_h - 6)
            if filtered:
                if cursor < scroll:
                    scroll = cursor
                if cursor >= scroll + list_h:
                    scroll = cursor - list_h + 1

            frame = _render(
                prompt=prompt,
                header=header,
                query=query_buf,
                filtered=filtered,
                cursor=cursor,
                scroll=scroll,
                list_h=list_h,
                preview_h=preview_h,
                cols=cols,
                total=len(rows),
            )
            write("\033[H\033[J" + frame.replace("\n", "\r\n"))

            key = keys.read()
            if resize["flag"]:
                resize["flag"] = False
                continue
            if key == "resize":
                continue
            if key == "ctrl-c":
                return None
            if key == "esc":
                return None
            if key == "enter" or key == "right":
                if not filtered:
                    continue
                if key == "right" and "enter" not in actions and "right" not in actions:
                    continue
                return "enter", filtered[cursor][1][3]
            if key == "left" and "left" in actions:
                if not filtered:
                    return "left", None
                return "left", filtered[cursor][1][3]
            if key == "ctrl-o" and "ctrl-o" in actions:
                if not filtered:
                    continue
                return "ctrl-o", filtered[cursor][1][3]
            if key in {"up", "ctrl-p"}:
                if filtered:
                    cursor = (cursor - 1) % len(filtered)
                continue
            if key in {"down", "ctrl-n"}:
                if filtered:
                    cursor = (cursor + 1) % len(filtered)
                continue
            if key == "home":
                cursor = 0
                continue
            if key == "end" and filtered:
                cursor = len(filtered) - 1
                continue
            if key == "backspace":
                query_buf = query_buf[:-1]
                cursor = 0
                scroll = 0
                continue
            if key == "ctrl-u" or key == "delete":
                query_buf = ""
                cursor = 0
                scroll = 0
                continue
            if key.startswith("char:"):
                query_buf += key[5:]
                cursor = 0
                scroll = 0
    except KeyboardInterrupt:
        return None
    finally:
        write("\033[?25h\033[?1049l")
        keys.restore()
        if prev_winch is not None:
            signal.signal(signal.SIGWINCH, prev_winch)
        if owned:
            try:
                stdin.close()
            except OSError:
                pass


def _term_size() -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size()
        return max(40, size.columns), max(12, size.lines)
    except OSError:
        return 80, 24


def _preview_height(lines: int) -> int:
    if lines < 20:
        return 4
    if lines < 32:
        return 6
    return 8


def _render(
    *,
    prompt: str,
    header: str,
    query: str,
    filtered: list[tuple[int, tuple[str, str, str, Any]]],
    cursor: int,
    scroll: int,
    list_h: int,
    preview_h: int,
    cols: int,
    total: int,
) -> str:
    width = max(20, cols - 1)
    rule = f"{_RULE}{'─' * width}{_RESET}"
    title = clip_visible(f"{_TITLE}{prompt}{_RESET}  {_DIM}{header}{_RESET}", width)
    lines = [title, rule]

    view = filtered[scroll : scroll + list_h]
    for offset, (_score, row) in enumerate(view):
        index = scroll + offset
        label = clip_visible(row[1], width - 2)
        if index == cursor:
            plain = strip_ansi(label)
            lines.append(clip_visible(f"{_SEL}❯ {plain} {_RESET}", width))
        else:
            lines.append(clip_visible(f"  {label}", width))
    for _ in range(max(0, list_h - len(view))):
        lines.append("")

    lines.append(rule)
    preview = ""
    if filtered:
        preview = filtered[cursor][1][2]
    preview_lines = (preview or "").splitlines() or [""]
    for i in range(preview_h):
        chunk = preview_lines[i] if i < len(preview_lines) else ""
        lines.append(clip_visible(f"{_DIM}{chunk}{_RESET}" if chunk else "", width))

    lines.append(rule)
    shown = len(filtered)
    filter_txt = query if query else ""
    status = f"{_DIM}/{filter_txt}{_RESET}  {_DIM}{shown}/{total}{_RESET}"
    lines.append(clip_visible(status, width))
    return "\n".join(lines)
