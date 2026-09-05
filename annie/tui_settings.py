"""Écran réglages TUI — écrit config.toml."""

from __future__ import annotations

from dataclasses import dataclass

from annie.config import AnnieConfig, reload_config
from annie.settings import AnnieSettings, reload_settings
from annie.tui import (
    Session,
    _ACCENT,
    _HINT,
    _OK,
    _RESET,
    _TEXT,
    available,
    chrome,
    cycle_choice,
    mask_secret,
    prompt_edit,
    select_row,
    term_size,
)
from annie.user_config import set_config_value

RES_QUALITY = {"auto": 26, "720p": 26, "1080p": 38, "2160p": 45}
LANG_CHOICES = ("", "fr", "en", "es", "de", "it", "pt", "ja")
PLAYER_CHOICES = ("auto", "mpv", "vlc", "ffplay")
MODE_CHOICES = ("auto", "anilist", "mal", "off")
RES_CHOICES = ("auto", "720p", "1080p", "2160p")


@dataclass
class Field:
    key: str
    label: str
    kind: str  # toggle | choice | text | secret | list
    section: str
    toml_key: str
    choices: tuple[str, ...] = ()
    hint: str = ""


FIELDS: tuple[Field, ...] = (
    Field(
        "os_key",
        "Clé API OpenSubtitles",
        "secret",
        "subtitles",
        "api_key",
        hint="https://www.opensubtitles.com/en/consumers",
    ),
    Field(
        "os_user",
        "Identifiant OpenSubtitles",
        "text",
        "subtitles",
        "username",
    ),
    Field(
        "os_pass",
        "Mot de passe OpenSubtitles",
        "secret",
        "subtitles",
        "password",
    ),
    Field(
        "sub_on",
        "Sous-titres",
        "toggle",
        "subtitles",
        "enabled",
    ),
    Field(
        "sub_lang",
        "Langue sous-titres",
        "choice",
        "subtitles",
        "default_lang",
        LANG_CHOICES,
        hint="vide = menu à chaque lecture",
    ),
    Field(
        "resolution",
        "Résolution préférée",
        "choice",
        "catalog",
        "preferred_resolution",
        RES_CHOICES,
        hint="influence le choix des torrents",
    ),
    Field(
        "player",
        "Lecteur",
        "choice",
        "player",
        "command",
        PLAYER_CHOICES,
    ),
    Field(
        "meta",
        "Métadonnées",
        "choice",
        "metadata",
        "mode",
        MODE_CHOICES,
        hint="auto · anilist · mal · off (Nyaa seul)",
    ),
    Field(
        "seed",
        "Seed pendant la lecture",
        "toggle",
        "streaming",
        "seed_while_watching",
    ),
    Field(
        "groups",
        "Groupes préférés",
        "list",
        "catalog",
        "preferred_groups",
        hint="ex. SubsPlease, Erai-raws",
    ),
)


def _current_values() -> dict[str, object]:
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


def _display(field: Field, value: object) -> str:
    if field.kind == "toggle":
        return "oui" if value else "non"
    if field.kind == "secret":
        return mask_secret(str(value or ""))
    if field.kind == "list":
        items = value if isinstance(value, list) else []
        return ", ".join(str(item) for item in items) if items else "—"
    if field.key == "sub_lang":
        return str(value) if value else "menu"
    text = str(value or "")
    return text if text else "—"


def _save(field: Field, value: object) -> None:
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


def _parse_groups(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def run_settings() -> bool:
    """Ouvre l'écran réglages. True si au moins une valeur a changé."""
    if not available():
        return False

    values = _current_values()
    cursor = 0
    dirty = False

    with Session() as ses:
        while True:
            cols, rows = term_size()
            width = max(24, cols - 1)
            body: list[str] = []
            for index, field in enumerate(FIELDS):
                shown = _display(field, values[field.key])
                label = f"{field.label:<26} {shown}"
                if index == cursor:
                    body.append(select_row(label, width, selected=True))
                else:
                    body.append(
                        select_row(
                            f"{_TEXT}{field.label:<26}{_RESET} {_ACCENT}{shown}{_RESET}",
                            width,
                            selected=False,
                        )
                    )
            field = FIELDS[cursor]
            preview = [
                field.hint or "enter pour modifier",
                "~/.config/annie/config.toml",
            ]
            footer = f"{_HINT}↑↓  enter  esc{_RESET}"
            ses.draw(
                chrome(
                    title="réglages",
                    body=body,
                    footer=footer,
                    preview=preview,
                    cols=cols,
                    rows=rows,
                    meta=f"{_OK}ok{_RESET}" if dirty else "",
                )
            )
            key = ses.read()
            if ses.resized or key == "resize":
                ses.resized = False
                continue
            if key in {"esc", "ctrl-c", "left"}:
                return dirty
            if key in {"up", "ctrl-p"}:
                cursor = (cursor - 1) % len(FIELDS)
                continue
            if key in {"down", "ctrl-n"}:
                cursor = (cursor + 1) % len(FIELDS)
                continue
            if key not in {"enter", "right"}:
                continue

            current = values[field.key]
            if field.kind == "toggle":
                nxt = not bool(current)
                values[field.key] = nxt
                _save(field, nxt)
                dirty = True
                continue
            if field.kind == "choice":
                nxt = cycle_choice(str(current or field.choices[0]), field.choices)
                values[field.key] = nxt
                _save(field, nxt)
                dirty = True
                continue

            initial = (
                ", ".join(str(item) for item in current)
                if field.kind == "list" and isinstance(current, list)
                else str(current or "")
            )
            edited = prompt_edit(
                ses,
                title="réglages",
                label=field.label,
                initial=initial,
                secret=field.kind == "secret",
                hint=field.hint,
            )
            if edited is None:
                continue
            nxt: object = _parse_groups(edited) if field.kind == "list" else edited.strip()
            values[field.key] = nxt
            _save(field, nxt)
            dirty = True

    return dirty
