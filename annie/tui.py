"""TUI in-process : picker plein écran + chrome partagé."""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import sys
from typing import Any, Callable

from annie import theme as T

ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

_RESET = T.RESET
_BOLD = T.BOLD
_DIM = T.DIM
_TITLE = T.BOLD + T.ACC
_ACCENT = T.ACC
_RULE = T.RULE
_TEXT = T.FG
_SEL = T.SEL
_HINT = T.DIM
_OK = T.OK


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


def pad_visible(text: str, width: int) -> str:
    clipped = clip_visible(text, width)
    pad = max(0, width - visible_len(clipped))
    return clipped + (" " * pad)


def _bar(left: str, right: str, width: int) -> str:
    space = width - visible_len(left) - visible_len(right)
    if space < 1:
        left = clip_visible(left, max(0, width - visible_len(right) - 1))
        space = width - visible_len(left) - visible_len(right)
    return left + (" " * max(0, space)) + right


def screen_title(title: str) -> str:
    text = title.strip().rstrip("> ").strip()
    if text.lower().startswith("annie"):
        text = text[5:].lstrip(" ·").strip()
    return text


def layout(rows: int, preview_n: int) -> tuple[int, int, int]:
    """Hauteur liste, aperçu, spacer. Doit rester ≤ *rows* avec le chrome."""
    preview_h = 0
    extra = 0
    spacer = 0
    if preview_n > 0:
        cap = 8 if rows >= 28 else (5 if rows >= 20 else 3)
        preview_h = min(preview_n, cap)
        extra = 1
        spacer = 1 if rows >= 20 else 0
    body_h = max(3, rows - 4 - extra - spacer - preview_h)
    return body_h, preview_h, spacer


def select_row(text: str, width: int, *, selected: bool) -> str:
    inner = max(1, width - 2)
    if selected:
        return (
            f"{T.SEL_BAR}▏{T.RESET}{T.SEL} "
            f"{pad_visible(strip_ansi(text), inner)}{T.RESET}"
        )
    return f"  {pad_visible(text, inner)}"


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


def mask_secret(value: str) -> str:
    if not value:
        return "—"
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def cycle_choice(current: str, choices: tuple[str, ...]) -> str:
    if not choices:
        return current
    if current not in choices:
        return choices[0]
    return choices[(choices.index(current) + 1) % len(choices)]


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


def term_size() -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size()
        return max(40, size.columns), max(12, size.lines)
    except OSError:
        return 80, 24


class Session:
    """Écran alternatif + raw TTY. Un seul à la fois."""

    def __init__(self) -> None:
        self.stdin, self.stdout, self._owned = _open_tty()
        self._keys: _UnixKeys | _WinKeys
        if sys.platform == "win32":
            self._keys = _WinKeys()
        else:
            self._keys = _UnixKeys(self.stdin.fileno())
        self._prev_winch = None
        self.resized = False

        def _on_winch(_signum, _frame) -> None:
            self.resized = True

        if hasattr(signal, "SIGWINCH"):
            self._prev_winch = signal.signal(signal.SIGWINCH, _on_winch)

    def __enter__(self) -> Session:
        self.write("\033[?1049h\033[?25l\033[2J\033[H")
        return self

    def __exit__(self, *_exc) -> None:
        self.write("\033[?25h\033[?1049l")
        self._keys.restore()
        if self._prev_winch is not None:
            signal.signal(signal.SIGWINCH, self._prev_winch)
        if self._owned:
            try:
                self.stdin.close()
            except OSError:
                pass

    def write(self, text: str) -> None:
        self.stdout.write(text)
        self.stdout.flush()

    def draw(self, frame: str) -> None:
        self.write("\033[H\033[J" + frame.replace("\n", "\r\n"))

    def read(self) -> str:
        return self._keys.read()

    def show_cursor(self, visible: bool) -> None:
        self.write("\033[?25h" if visible else "\033[?25l")


def chrome(
    *,
    title: str,
    body: list[str],
    footer: str,
    preview: list[str] | None = None,
    cols: int,
    rows: int,
    meta: str = "",
) -> str:
    """Barre Omarchy : marque, filet, liste, aperçu, footer — sans cadre."""
    width = max(24, cols - 1)
    preview_n = len(preview) if preview else 0
    body_h, preview_h, spacer = layout(rows, preview_n)
    rule = f"{_RULE}{'─' * width}{_RESET}"
    brand = f"{_TITLE}annie{_RESET}"
    label = screen_title(title)
    left = brand if not label else f"{brand}  {_DIM}{label}{_RESET}"
    if meta and "\033" not in meta:
        right = f"{_DIM}{meta}{_RESET}"
    else:
        right = meta
    lines = [_bar(left, right, width), rule]

    view = list(body[:body_h])
    while len(view) < body_h:
        view.append("")
    for row in view:
        lines.append(pad_visible(row, width))

    if preview:
        if spacer:
            lines.append("")
        lines.append(rule)
        shown = list(preview[:preview_h])
        while len(shown) < preview_h:
            shown.append("")
        for row in shown:
            lines.append(f"{_DIM}{pad_visible(row, width)}{_RESET}")

    lines.append(rule)
    lines.append(pad_visible(footer, width))
    return "\n".join(lines)


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
    if not rows or not available():
        return None
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

    try:
        with Session() as ses:
            while True:
                cols, lines = term_size()
                filtered = filter_rows(rows, query_buf)
                if cursor >= len(filtered):
                    cursor = max(0, len(filtered) - 1)
                if cursor < 0:
                    cursor = 0

                preview_src = (
                    strip_ansi(filtered[cursor][1][2]).splitlines() if filtered else []
                )
                list_h, _preview_h, _spacer = layout(lines, len(preview_src))
                width = max(24, cols - 1)
                if filtered:
                    if cursor < scroll:
                        scroll = cursor
                    if cursor >= scroll + list_h:
                        scroll = cursor - list_h + 1

                body: list[str] = []
                view = filtered[scroll : scroll + list_h]
                for offset, (_score, row) in enumerate(view):
                    index = scroll + offset
                    label = clip_visible(row[1], max(10, width - 2))
                    body.append(select_row(label, width, selected=index == cursor))
                if not filtered:
                    body.append(select_row(f"{_DIM}rien{_RESET}", width, selected=False))
                while len(body) < list_h:
                    body.append("")

                shown = len(filtered)
                typed = f"{_TEXT}{query_buf}{_RESET}" if query_buf else ""
                footer = _bar(
                    f"{_ACCENT}/{_RESET}{typed}",
                    f"{_HINT}{header}{_RESET}",
                    width,
                )
                ses.draw(
                    chrome(
                        title=prompt,
                        body=body,
                        footer=footer,
                        preview=preview_src or None,
                        cols=cols,
                        rows=lines,
                        meta=f"{shown}/{len(rows)}",
                    )
                )

                key = ses.read()
                if ses.resized or key == "resize":
                    ses.resized = False
                    continue
                if key in {"ctrl-c", "esc"}:
                    return None
                if key in {"enter", "right"}:
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
                if key in {"up", "ctrl-p"} and filtered:
                    cursor = (cursor - 1) % len(filtered)
                elif key in {"down", "ctrl-n"} and filtered:
                    cursor = (cursor + 1) % len(filtered)
                elif key == "home":
                    cursor = 0
                elif key == "end" and filtered:
                    cursor = len(filtered) - 1
                elif key == "backspace":
                    query_buf = query_buf[:-1]
                    cursor = 0
                    scroll = 0
                elif key in {"ctrl-u", "delete"}:
                    query_buf = ""
                    cursor = 0
                    scroll = 0
                elif key.startswith("char:"):
                    query_buf += key[5:]
                    cursor = 0
                    scroll = 0
    except KeyboardInterrupt:
        return None


def prompt_edit(
    session: Session,
    *,
    title: str,
    label: str,
    initial: str,
    secret: bool = False,
    hint: str = "",
) -> str | None:
    """Saisie inline dans la session déjà ouverte. None = annuler."""
    buf = initial
    session.show_cursor(True)
    try:
        while True:
            cols, rows = term_size()
            shown = ("•" * len(buf)) if secret else buf
            caret = f"{_TEXT}{shown}{_ACCENT}▍{_RESET}"
            body = [
                f"{_DIM}{label}{_RESET}",
                "",
                caret,
                "",
                f"{_DIM}{hint}{_RESET}" if hint else "",
            ]
            footer = f"{_HINT}enter  esc{_RESET}"
            session.draw(
                chrome(
                    title=title,
                    body=body,
                    footer=footer,
                    preview=None,
                    cols=cols,
                    rows=rows,
                )
            )
            key = session.read()
            if key in {"esc", "ctrl-c"}:
                return None
            if key == "enter":
                return buf
            if key == "backspace":
                buf = buf[:-1]
            elif key in {"ctrl-u", "delete"}:
                buf = ""
            elif key.startswith("char:"):
                buf += key[5:]
    finally:
        session.show_cursor(False)
