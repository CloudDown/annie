"""Fichier de configuration utilisateur (config.toml)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from annie.paths import config_dir

CONFIG_DIR = config_dir()
CONFIG_FILE = CONFIG_DIR / "config.toml"
# Conservé pour la rétrocompatibilité (anciennes installations).
SETTINGS_FILE = CONFIG_DIR / "settings.toml"

_TEMPLATES = files("annie") / "templates"


def _install_template(name: str, dest: Path, *, mode: int | None = None) -> bool:
    if dest.is_file():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text((_TEMPLATES / name).read_text(encoding="utf-8"), encoding="utf-8")
    if mode is not None:
        dest.chmod(mode)
    return True


def ensure_user_config() -> list[Path]:
    """Crée config.toml s'il n'existe pas. Ne remplace jamais."""
    created: list[Path] = []
    if _install_template("config.toml", CONFIG_FILE, mode=0o600):
        created.append(CONFIG_FILE)
    return created


def _read_player_command() -> str:
    from annie import toml_util

    data = toml_util.read_toml(CONFIG_FILE)
    player_table = toml_util.table(data, "player")
    return str(player_table.get("command") or "auto").strip() or "auto"


def set_player_command(command: str, *, only_if_auto: bool = True) -> bool:
    """Écrit ``[player].command`` dans config.toml."""
    ensure_user_config()
    command = command.strip()
    if not command:
        return False
    if only_if_auto:
        current = _read_player_command()
        if current not in {"", "auto"}:
            return False

    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'command = "{escaped}"'
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_player = False
    has_player_section = False
    has_command = False
    changed = False

    for line in lines:
        stripped = line.strip()
        if stripped == "[player]":
            in_player = True
            has_player_section = True
            out.append(line)
            continue
        if in_player and stripped.startswith("[") and stripped.endswith("]"):
            if not has_command:
                out.append(new_line)
                changed = True
                has_command = True
            in_player = False
        if in_player and stripped.startswith("command"):
            if line.strip() != new_line:
                out.append(new_line)
                changed = True
            else:
                out.append(line)
            has_command = True
            continue
        out.append(line)

    if not has_player_section:
        if out and out[-1].strip():
            out.append("")
        out.extend(["[player]", new_line])
        changed = True
    elif in_player and not has_command:
        out.append(new_line)
        changed = True

    if changed:
        CONFIG_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def ensure_media_player_config(*, force: bool = False) -> str | None:
    """Configure le lecteur dans config.toml et vérifie la résolution."""
    from annie.paths import find_best_media_player

    found = find_best_media_player()
    if not found:
        return None
    _name, exe = found
    set_player_command(exe, only_if_auto=not force)
    from annie.stream import resolve_player

    resolve_player()
    return exe
