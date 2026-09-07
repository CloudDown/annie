"""Fichier de configuration utilisateur (config.toml)."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from annie.paths import config_dir

CONFIG_DIR = config_dir()
CONFIG_FILE = CONFIG_DIR / "config.toml"

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


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, list):
        inner = ", ".join(_toml_literal(item) for item in value)
        return f"[{inner}]"
    return json.dumps(str(value), ensure_ascii=False)


def _is_assignment(stripped: str, key: str) -> bool:
    return stripped.startswith(f"{key}=") or stripped.startswith(f"{key} =")


def set_config_value(section: str, key: str, value: object) -> bool:
    """Écrit ``[section].key`` dans config.toml sans casser le reste du fichier."""
    ensure_user_config()
    literal = _toml_literal(value)
    new_line = f"{key} = {literal}"
    heading = f"[{section}]"
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_section = False
    seen_section = False
    seen_key = False
    changed = False

    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            seen_section = True
            out.append(line)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not seen_key:
                out.append(new_line)
                changed = True
                seen_key = True
            in_section = False
        if in_section and _is_assignment(stripped, key):
            comment = ""
            if " #" in line:
                comment = " #" + line.split(" #", 1)[1]
            replacement = new_line + comment
            if line != replacement:
                changed = True
            out.append(replacement)
            seen_key = True
            continue
        out.append(line)

    if not seen_section:
        if out and out[-1].strip():
            out.append("")
        out.extend([heading, new_line])
        changed = True
    elif in_section and not seen_key:
        out.append(new_line)
        changed = True

    if changed:
        CONFIG_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass
    return changed
