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
