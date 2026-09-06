"""TUI in-process : picker, réglages, chrome — couleurs ANSI 16 du terminal."""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import sys
from dataclasses import dataclass
from typing import Any, Callable

# Couleurs 16-ANSI : suivent le thème du terminal (Omarchy, Alacritty, …).
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
REV = "\033[7m"
FG = ""
ACC = "\033[1;34m"  # bold blue → accent terminal
RULE = DIM
OK = "\033[32m"
WARN = "\033[33m"
ERR = "\033[31m"
CYAN = "\033[36m"
MAG = "\033[35m"
TEXT = FG
HINT = DIM
SEL = BOLD + REV
SEL_BAR = ACC

# Compat imports historiques (_ACCENT etc.)
_RESET = RESET
_BOLD = BOLD
_DIM = DIM
_TITLE = BOLD + ACC
_ACCENT = ACC
_RULE = RULE
_TEXT = TEXT
_SEL = SEL
_HINT = HINT
_OK = OK

ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

def keychip(key: str) -> str:
    """Touche en reverse vidéo — suit le thème du terminal (style Omarchy)."""
    return f"{BOLD}{REV} {key} {RESET}"


def shortcut_line(pairs: list[tuple[str, str]], *, prefix: str = "  ") -> str:
    bits = [f"{keychip(key)}{DIM} {label}{RESET}" for key, label in pairs]
    return prefix + "  ".join(bits)


HELP_OVERLAY = [
    shortcut_line(
        [
            ("↑↓", "move"),
            ("enter", "open"),
            ("1-9", "jump"),
            ("type", "filter"),
            ("?", "close"),
            ("ctrl-o", "magnet"),
            ("esc", "back"),
        ],
        prefix="",
    ),
]


def available() -> bool:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return True
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
    return "".join(out) + RESET


def pad_visible(text: str, width: int) -> str:
    clipped = clip_visible(text, width)
    return clipped + (" " * max(0, width - visible_len(clipped)))


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
    preview_h = extra = spacer = 0
    if preview_n > 0:
        cap = 8 if rows >= 28 else (5 if rows >= 20 else 3)
        preview_h = min(preview_n, cap)
        extra = 1
        spacer = 1 if rows >= 20 else 0
    body_h = max(3, rows - 4 - extra - spacer - preview_h)
    return body_h, preview_h, spacer


def select_row(text: str, width: int, *, selected: bool, index: int | None = None) -> str:
    """Ligne sélectionnée en reverse vidéo (suit le thème). Numéro optionnel."""
    num = f"{index} " if index is not None and 1 <= index <= 9 else "  "
    if selected:
        inner = max(1, width - 1)
        return f"{SEL_BAR}▏{RESET}{SEL}{pad_visible(num + strip_ansi(text), inner)}{RESET}"
    if index is not None and 1 <= index <= 9:
        return f"{DIM}{index}{RESET} {pad_visible(text, max(1, width - 2))}"
    return f"  {pad_visible(text, max(1, width - 2))}"


def fuzzy_score(query: str, text: str) -> int | None:
    if not query:
        return 0
    needle = query.casefold()
    hay = text.casefold()
    start = 0
    score = 0
    prev = -2
    first = -1
    for char in needle:
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
    scored: list[tuple[int, int, tuple[str, str, str, Any]]] = []
    for index, row in enumerate(rows):
        score = fuzzy_score(query, strip_ansi(row[1]))
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


def cycle_choice_prev(current: str, choices: tuple[str, ...]) -> str:
    if not choices:
        return current
    if current not in choices:
        return choices[0]
    return choices[(choices.index(current) - 1) % len(choices)]


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
            "[5~": "pgup",
            "[6~": "pgdn",
        }
        for prefix, name in mapping.items():
            if seq.startswith(prefix):
                return name
        if seq.startswith("[3~"):
            return "delete"
        return "esc"


def _open_tty():
    if sys.stdin.isatty() and sys.stdout.isatty():
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
    def __init__(self) -> None:
        self.stdin, self.stdout, self._owned = _open_tty()
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
    width = max(24, cols - 1)
    preview_n = len(preview) if preview else 0
    body_h, preview_h, spacer = layout(rows, preview_n)
    rule = f"{RULE}{'─' * width}{RESET}"
    brand = f"{_TITLE}annie{RESET}"
    label = screen_title(title)
    left = brand if not label else f"{brand}  {DIM}{label}{RESET}"
    if meta and "\033" not in meta:
        right = f"{DIM}{meta}{RESET}"
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
            lines.append(f"{DIM}{pad_visible(row, width)}{RESET}")

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
    del header  # footer chips construits depuis *actions*

    query_buf = query
    scroll = 0
    cursor = 0
    help_open = False
    if cursor_key is not None:
        for index, row in enumerate(rows):
            if row[0] == cursor_key:
                cursor = index
                break

    try:
        with Session() as ses:
            while True:
                cols, lines = term_size()
                width = max(24, cols - 1)
                list_h = 3
                view: list[tuple[int, tuple[str, str, str, Any]]] = []
                filtered = filter_rows(rows, query_buf)
                if cursor >= len(filtered):
                    cursor = max(0, len(filtered) - 1)
                if cursor < 0:
                    cursor = 0

                if help_open:
                    body = [f"  {line}" if line else "" for line in HELP_OVERLAY]
                    while len(body) < max(3, lines - 4):
                        body.append("")
                    ses.draw(
                        chrome(
                            title="help",
                            body=body,
                            footer=shortcut_line(
                                [("?", "close"), ("esc", "back")],
                                prefix="",
                            ),
                            preview=None,
                            cols=cols,
                            rows=lines,
                        )
                    )
                else:
                    preview_src = (
                        strip_ansi(filtered[cursor][1][2]).splitlines()
                        if filtered
                        else ["no matches — clear filter or press esc"]
                    )
                    list_h, _ph, _sp = layout(lines, len(preview_src))
                    if filtered:
                        if cursor < scroll:
                            scroll = cursor
                        if cursor >= scroll + list_h:
                            scroll = cursor - list_h + 1

                    body: list[str] = []
                    view = filtered[scroll : scroll + list_h]
                    for offset, (_score, row) in enumerate(view):
                        index = scroll + offset
                        label = clip_visible(row[1], max(10, width - 4))
                        visible_n = offset + 1 if not query_buf else None
                        body.append(
                            select_row(
                                label,
                                width,
                                selected=index == cursor,
                                index=visible_n if visible_n and visible_n <= 9 else None,
                            )
                        )
                    if not filtered:
                        body.append(
                            select_row(
                                f"{DIM}no matches — backspace / esc{RESET}",
                                width,
                                selected=False,
                            )
                        )
                        view = []
                    while len(body) < list_h:
                        body.append("")

                    footer_pairs: list[tuple[str, str]] = [
                        ("↑↓", "move"),
                        ("enter", "open"),
                    ]
                    if "left" in actions:
                        footer_pairs.append(("←", "back"))
                    if "ctrl-o" in actions:
                        footer_pairs.append(("ctrl-o", "magnet"))
                    footer_pairs.append(("?", "help"))
                    footer = _bar(
                        f"{ACC}/{RESET}{TEXT}{query_buf}{RESET}" if query_buf else f"{ACC}/{RESET}",
                        shortcut_line(footer_pairs, prefix=""),
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
                            meta=f"{len(filtered)}/{len(rows)}",
                        )
                    )

                key = ses.read()
                if ses.resized or key == "resize":
                    ses.resized = False
                    continue
                if key in {"ctrl-c"}:
                    return None
                if help_open:
                    if key in {"esc", "char:?", "char:h"}:
                        help_open = False
                    continue
                if key == "char:?" or (key == "char:h" and not query_buf):
                    help_open = True
                    continue
                if key == "esc":
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
                if key in {"up", "ctrl-p", "char:k"} and filtered:
                    cursor = (cursor - 1) % len(filtered)
                elif key in {"down", "ctrl-n", "char:j"} and filtered:
                    cursor = (cursor + 1) % len(filtered)
                elif key == "pgup" and filtered:
                    cursor = max(0, cursor - max(1, list_h))
                elif key == "pgdn" and filtered:
                    cursor = min(len(filtered) - 1, cursor + max(1, list_h))
                elif key == "home":
                    cursor = 0
                elif key == "end" and filtered:
                    cursor = len(filtered) - 1
                elif key == "backspace":
                    query_buf = query_buf[:-1]
                    cursor = scroll = 0
                elif key in {"ctrl-u", "delete"}:
                    query_buf = ""
                    cursor = scroll = 0
                elif key.startswith("char:"):
                    ch = key[5:]
                    if ch in "jk" and not query_buf:
                        continue
                    if not query_buf and ch in "123456789" and filtered:
                        pick = int(ch) - 1
                        if 0 <= pick < len(view):
                            return "enter", view[pick][1][3]
                        continue
                    query_buf += ch
                    cursor = scroll = 0
    except KeyboardInterrupt:
        return None


# --- Settings -----------------------------------------------------------------

RES_QUALITY = {"auto": 26, "720p": 26, "1080p": 38, "2160p": 45}
LANG_CHOICES = ("", "fr", "en", "es", "de", "it", "pt", "ja")
PLAYER_CHOICES = ("auto", "mpv", "vlc", "ffplay")
MODE_CHOICES = ("auto", "anilist", "mal", "off")
RES_CHOICES = ("auto", "720p", "1080p", "2160p")


@dataclass
class _Field:
    key: str
    label: str
    kind: str
    section: str
    toml_key: str
    choices: tuple[str, ...] = ()
    hint: str = ""


_FIELDS: tuple[_Field, ...] = (
    _Field("os_key", "OpenSubtitles API key", "secret", "subtitles", "api_key",
           hint="https://www.opensubtitles.com/en/consumers"),
    _Field("os_user", "OpenSubtitles username", "text", "subtitles", "username"),
    _Field("os_pass", "OpenSubtitles password", "secret", "subtitles", "password"),
    _Field("sub_on", "Subtitles", "toggle", "subtitles", "enabled"),
    _Field("sub_lang", "Subtitle language", "choice", "subtitles", "default_lang",
           LANG_CHOICES, hint="empty = ask each time"),
    _Field("resolution", "Preferred resolution", "choice", "catalog", "preferred_resolution",
           RES_CHOICES, hint="influences torrent ranking"),
    _Field("player", "Player", "choice", "player", "command", PLAYER_CHOICES),
    _Field("meta", "Metadata", "choice", "metadata", "mode", MODE_CHOICES,
           hint="auto · anilist · mal · off (Nyaa only)"),
    _Field("seed", "Seed while watching", "toggle", "streaming", "seed_while_watching"),
    _Field("groups", "Preferred groups", "list", "catalog", "preferred_groups",
           hint="e.g. SubsPlease, Erai-raws"),
)


def _settings_values() -> dict[str, object]:
    from annie.config import AnnieConfig
    from annie.settings import AnnieSettings

    cfg = AnnieConfig.load()
    settings = AnnieSettings.load()
    return {
        "os_key": cfg.subtitles.api_key,
        "os_user": cfg.subtitles.username,
        "os_pass": cfg.subtitles.password,
        "sub_on": cfg.subtitles.enabled,
        "sub_lang": cfg.subtitles.default_lang,
        "resolution": getattr(cfg.catalog, "preferred_resolution", "auto") or "auto",
        "player": cfg.player or "auto",
        "meta": cfg.metadata.mode,
        "seed": settings.seed_while_watching,
        "groups": list(cfg.catalog.preferred_groups),
    }


def _settings_display(field: _Field, value: object) -> str:
    if field.kind == "toggle":
        return "yes" if value else "no"
    if field.kind == "secret":
        return mask_secret(str(value or ""))
    if field.kind == "list":
        items = value if isinstance(value, list) else []
        return ", ".join(str(item) for item in items) if items else "—"
    if field.key == "sub_lang":
        return str(value) if value else "ask"
    text = str(value or "")
    return text if text else "—"


def _settings_save(field: _Field, value: object) -> None:
    from annie.config import reload_config
    from annie.settings import reload_settings
    from annie.user_config import set_config_value

    if field.key == "player":
        from annie.user_config import set_player_command

        set_player_command(str(value), only_if_auto=False)
    else:
        set_config_value(field.section, field.toml_key, value)
    if field.key == "resolution":
        res = str(value or "auto")
        set_config_value("catalog", "min_quality_strict", RES_QUALITY.get(res, 26))
    if field.key == "meta" and value == "off":
        set_config_value("metadata", "enabled", False)
    elif field.key == "meta":
        set_config_value("metadata", "enabled", True)
    reload_config()
    reload_settings()


def run_settings() -> bool:
    """Settings screen. True if any value changed."""
    if not available():
        return False

    values = _settings_values()
    cursor = 0
    dirty = False
    # Inline edit for text/secret/list — same screen, Esc cancels edit (not settings).
    editing = False
    edit_buf = ""
    edit_pos = 0

    with Session() as ses:
        while True:
            cols, rows = term_size()
            width = max(24, cols - 1)
            field = _FIELDS[cursor]

            body: list[str] = []
            for index, item in enumerate(_FIELDS):
                if editing and index == cursor:
                    raw = ("•" * len(edit_buf)) if item.kind == "secret" else edit_buf
                    before, after = raw[:edit_pos], raw[edit_pos:]
                    line = (
                        f"{SEL_BAR}▏{RESET}{SEL} "
                        f"{item.label:<26} {before}{RESET}{ACC}█{RESET}{SEL}{after}{RESET}"
                    )
                    body.append(pad_visible(line, width))
                else:
                    shown = _settings_display(item, values[item.key])
                    label = f"{item.label:<26} {shown}"
                    if index == cursor:
                        body.append(select_row(label, width, selected=True))
                    else:
                        body.append(
                            select_row(
                                f"{TEXT}{item.label:<26}{RESET} {ACC}{shown}{RESET}",
                                width,
                                selected=False,
                            )
                        )

            if editing:
                footer = shortcut_line(
                    [("←→", "cursor"), ("enter", "save"), ("esc", "cancel")],
                    prefix="",
                )
                preview = [field.hint or "edit value", "~/.config/annie/config.toml"]
            elif field.kind in {"toggle", "choice"}:
                footer = shortcut_line(
                    [("↑↓", "move"), ("←→", "change"), ("esc", "back")],
                    prefix="",
                )
                preview = [field.hint or "← → to change value", "~/.config/annie/config.toml"]
            else:
                footer = shortcut_line(
                    [("↑↓", "move"), ("enter", "edit"), ("esc", "back")],
                    prefix="",
                )
                preview = [field.hint or "enter to edit", "~/.config/annie/config.toml"]

            ses.draw(
                chrome(
                    title="settings",
                    body=body,
                    footer=footer,
                    preview=preview,
                    cols=cols,
                    rows=rows,
                    meta=f"{OK}ok{RESET}" if dirty else "",
                )
            )
            key = ses.read()
            if ses.resized or key == "resize":
                ses.resized = False
                continue

            if editing:
                if key in {"esc", "ctrl-c"}:
                    editing = False
                    ses.show_cursor(False)
                    continue
                if key == "enter":
                    nxt: object = (
                        [
                            p.strip()
                            for p in edit_buf.replace(";", ",").split(",")
                            if p.strip()
                        ]
                        if field.kind == "list"
                        else edit_buf.strip()
                    )
                    values[field.key] = nxt
                    _settings_save(field, nxt)
                    dirty = True
                    editing = False
                    ses.show_cursor(False)
                    continue
                if key == "left":
                    edit_pos = max(0, edit_pos - 1)
                elif key == "right":
                    edit_pos = min(len(edit_buf), edit_pos + 1)
                elif key == "home":
                    edit_pos = 0
                elif key == "end":
                    edit_pos = len(edit_buf)
                elif key == "backspace":
                    if edit_pos > 0:
                        edit_buf = edit_buf[: edit_pos - 1] + edit_buf[edit_pos:]
                        edit_pos -= 1
                elif key == "delete":
                    if edit_pos < len(edit_buf):
                        edit_buf = edit_buf[:edit_pos] + edit_buf[edit_pos + 1 :]
                elif key == "ctrl-u":
                    edit_buf = ""
                    edit_pos = 0
                elif key.startswith("char:"):
                    ch = key[5:]
                    edit_buf = edit_buf[:edit_pos] + ch + edit_buf[edit_pos:]
                    edit_pos += len(ch)
                continue

            if key in {"esc", "ctrl-c"}:
                return dirty
            if key in {"up", "ctrl-p", "char:k"}:
                cursor = (cursor - 1) % len(_FIELDS)
                continue
            if key in {"down", "ctrl-n", "char:j"}:
                cursor = (cursor + 1) % len(_FIELDS)
                continue
            if key == "char:?":
                continue

            current = values[field.key]
            if field.kind == "toggle" and key in {
                "enter",
                "right",
                "left",
                "char: ",
            }:
                nxt = not bool(current)
                values[field.key] = nxt
                _settings_save(field, nxt)
                dirty = True
                continue
            if field.kind == "choice" and key in {"enter", "right", "char: "}:
                nxt = cycle_choice(str(current or field.choices[0]), field.choices)
                values[field.key] = nxt
                _settings_save(field, nxt)
                dirty = True
                continue
            if field.kind == "choice" and key == "left":
                nxt = cycle_choice_prev(str(current or field.choices[0]), field.choices)
                values[field.key] = nxt
                _settings_save(field, nxt)
                dirty = True
                continue
            if field.kind in {"toggle", "choice"}:
                continue
            if key not in {"enter", "right"}:
                continue

            # Text / secret / list: edit in place (same settings screen).
            initial = (
                ", ".join(str(item) for item in current)
                if field.kind == "list" and isinstance(current, list)
                else str(current or "")
            )
            editing = True
            edit_buf = initial
            edit_pos = len(edit_buf)
            ses.show_cursor(True)

    return dirty
