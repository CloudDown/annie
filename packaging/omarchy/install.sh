#!/usr/bin/env bash
# Wire Annie into Omarchy: PATH, omarchy-tui-install (launcher), Super+Shift+I.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
BIN_DIR="$HOME/.local/bin"
BINDINGS="$HOME/.config/hypr/bindings.lua"
HYPRLAND="$HOME/.config/hypr/hyprland.lua"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"

if ! command -v omarchy >/dev/null 2>&1; then
  printf '%s\n' "omarchy: this installer is for Omarchy Linux." >&2
  exit 1
fi

if ! command -v omarchy-tui-install >/dev/null 2>&1; then
  printf '%s\n' "omarchy: omarchy-tui-install is missing." >&2
  exit 1
fi

need=()
command -v mpv >/dev/null 2>&1 || need+=(mpv)
command -v uv >/dev/null 2>&1 || need+=(uv)
if ((${#need[@]})); then
  omarchy pkg add "${need[@]}"
fi

mkdir -p "$BIN_DIR"

# Prefer the venv entry point from `make install` / `uv sync`.
if [[ -x "$ROOT/.venv/bin/annie" ]]; then
  ln -sfn "$ROOT/.venv/bin/annie" "$BIN_DIR/annie"
elif [[ -x "$ROOT/bin/annie.py" ]]; then
  ln -sfn "$ROOT/bin/annie.py" "$BIN_DIR/annie"
elif ! command -v annie >/dev/null 2>&1; then
  printf '%s\n' "omarchy: run 'make install' first (no annie on PATH)." >&2
  exit 1
fi

# --- migrate away from legacy custom wiring ---
rm -f "$DESKTOP_DIR/Annie.desktop"
rm -f "$ICON_BASE/scalable/apps/annie.svg" \
  "$ICON_BASE/256x256/apps/annie.png" \
  "$ICON_BASE/256x256/apps/annie.svg"
gtk-update-icon-cache "$ICON_BASE" &>/dev/null || true
update-desktop-database "$DESKTOP_DIR" &>/dev/null || true

if [[ -f "$MENU" ]] && grep -q '"trigger.annie"' "$MENU"; then
  python3 - "$MENU" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text2 = re.sub(
    r'\n  "trigger\.annie": \{.*?\n  \},?\n',
    "\n",
    text,
    count=1,
    flags=re.DOTALL,
)
if text2 != text:
    path.write_text(text2, encoding="utf-8")
PY
fi

if [[ -f "$HYPRLAND" ]] && grep -q 'org.omarchy.annie' "$HYPRLAND"; then
  python3 - "$HYPRLAND" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text2 = re.sub(
    r"\n-- Annie TUI:[^\n]*\n"
    r"(?:o\.window\(\"org\.omarchy\.annie\"[^\n]*\n)+",
    "\n",
    text,
)
if text2 != text:
    path.write_text(text2, encoding="utf-8")
PY
fi

# Super+Shift+A is ChatGPT — Annie uses Super+Shift+I (same app-id as tui-install).
if [[ -f "$BINDINGS" ]]; then
  python3 - "$BINDINGS" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = re.sub(
    r"\n-- Annie[^\n]*\n"
    r"(?:hl\.unbind\(\"SUPER \+ SHIFT \+ A\"\)\n)?"
    r"o\.bind\(\"SUPER \+ SHIFT \+ [AI]\", \"Annie\"[^\n]*\n",
    "\n",
    text,
)
marker = 'omarchy-launch-or-focus-tui --app-id=TUI.float annie'
if marker not in text:
    text = text.rstrip() + """

-- Annie (Super+Shift+A stays ChatGPT)
o.bind("SUPER + SHIFT + I", "Annie", "omarchy-launch-or-focus-tui --app-id=TUI.float annie")
"""
    if not text.endswith("\n"):
        text += "\n"
path.write_text(text, encoding="utf-8")
PY
fi

ICON="$ROOT/packaging/omarchy/annie.png"
if [[ ! -f "$ICON" ]]; then
  ICON="$ROOT/packaging/omarchy/annie.svg"
fi
if [[ ! -f "$ICON" ]]; then
  printf '%s\n' "omarchy: missing packaging/omarchy/annie.png (or .svg)." >&2
  exit 1
fi

omarchy-tui-install "Annie" annie float "$ICON"

if command -v hyprctl >/dev/null 2>&1; then
  hyprctl reload >/dev/null
  errors="$(hyprctl configerrors 2>/dev/null || true)"
  if [[ -n "${errors// }" ]]; then
    printf '%s\n' "$errors" >&2
    exit 1
  fi
fi

printf '%s\n' "Annie is on Omarchy:"
printf '%s\n' "  Super+Shift+I     launch / focus"
printf '%s\n' "  Super+Space       Apps → Annie"
printf '%s\n' "  Super+Shift+A stays ChatGPT."
printf '%s\n' "Paths:"
printf '%s\n' "  app     $ROOT"
printf '%s\n' "  binary  $BIN_DIR/annie"
printf '%s\n' "  config  ${XDG_CONFIG_HOME:-$HOME/.config}/annie"
