#!/usr/bin/env bash
# Wire Annie into Omarchy like a stock TUI (btop, Docker): PATH, app launcher,
# Super+Shift+A, menu search, floating window.
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

if ! command -v xdg-terminal-exec >/dev/null 2>&1; then
  printf '%s\n' "omarchy: xdg-terminal-exec is missing." >&2
  exit 1
fi

need=()
command -v mpv >/dev/null 2>&1 || need+=(mpv)
command -v uv >/dev/null 2>&1 || need+=(uv)
if ((${#need[@]})); then
  omarchy pkg add "${need[@]}"
fi

mkdir -p "$BIN_DIR" "$DESKTOP_DIR" \
  "$ICON_BASE/scalable/apps" \
  "$ICON_BASE/256x256/apps"

# Prefer the venv entry point from `make install` / `uv sync`.
if [[ -x "$ROOT/.venv/bin/annie" ]]; then
  ln -sfn "$ROOT/.venv/bin/annie" "$BIN_DIR/annie"
elif [[ -x "$ROOT/bin/annie.py" ]]; then
  ln -sfn "$ROOT/bin/annie.py" "$BIN_DIR/annie"
elif ! command -v annie >/dev/null 2>&1; then
  printf '%s\n' "omarchy: run 'make install' first (no annie on PATH)." >&2
  exit 1
fi

install -m644 "$ROOT/packaging/omarchy/annie.desktop" "$DESKTOP_DIR/Annie.desktop"
install -m644 "$ROOT/packaging/omarchy/annie.svg" "$ICON_BASE/scalable/apps/annie.svg"
if [[ -f "$ROOT/packaging/omarchy/annie.png" ]]; then
  install -m644 "$ROOT/packaging/omarchy/annie.png" "$ICON_BASE/256x256/apps/annie.png"
fi

if [[ ! -f "$ICON_BASE/index.theme" ]]; then
  cat >"$ICON_BASE/index.theme" <<'EOF'
[Icon Theme]
Name=Hicolor
Comment=User icon theme
Directories=256x256/apps,scalable/apps

[256x256/apps]
Size=256
Context=Applications
Type=Fixed

[scalable/apps]
Size=128
MaxSize=512
Context=Applications
Type=Scalable
EOF
fi

gtk-update-icon-cache "$ICON_BASE" &>/dev/null || true
update-desktop-database "$DESKTOP_DIR" &>/dev/null || true

# Super+Shift+A is ChatGPT in Omarchy preinstalls — Annie takes the letter.
if [[ -f "$BINDINGS" ]] && ! grep -q 'org.omarchy.annie\|"Annie"' "$BINDINGS"; then
  cat >>"$BINDINGS" <<'EOF'

-- Annie — was ChatGPT (still in Apps menu / Super+Shift+Alt+A = Grok)
hl.unbind("SUPER + SHIFT + A")
o.bind("SUPER + SHIFT + A", "Annie", { tui = "annie", focus = true })
EOF
fi

if [[ -f "$HYPRLAND" ]] && ! grep -q 'org.omarchy.annie' "$HYPRLAND"; then
  cat >>"$HYPRLAND" <<'EOF'

-- Annie TUI: larger float than btop so the catalog fits.
o.window("org.omarchy.annie", { float = true })
o.window("org.omarchy.annie", { center = true })
o.window("org.omarchy.annie", { size = { 1100, 720 } })
EOF
fi

if [[ -f "$MENU" ]] && ! grep -q '"trigger.annie"' "$MENU"; then
  python3 - "$MENU" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
needle = '"trigger.download"'
block = '''  "trigger.annie": {
    "icon": "󰎁",
    "label": "Annie",
    "aliases": ["annie", "anime", "nyaa", "mpv"],
    "description": "Search and stream anime",
    "when": "omarchy-cmd-present annie",
    "action": "omarchy-launch-or-focus-tui annie"
  },

  '''
if '"trigger.annie"' in text:
    raise SystemExit(0)
if needle not in text:
    raise SystemExit("omarchy-menu.jsonc: missing trigger.download anchor")
path.write_text(text.replace(needle, block + needle, 1), encoding="utf-8")
PY
fi

if command -v hyprctl >/dev/null 2>&1; then
  hyprctl reload >/dev/null
  errors="$(hyprctl configerrors 2>/dev/null || true)"
  if [[ -n "${errors// }" ]]; then
    printf '%s\n' "$errors" >&2
    exit 1
  fi
fi

printf '%s\n' "Annie is on Omarchy:"
printf '%s\n' "  Super+Shift+A     launch / focus"
printf '%s\n' "  Super+Space       type annie / anime"
printf '%s\n' "  Super+Alt+Space   Apps → Annie"
printf '%s\n' "  Note: Super+Shift+A was ChatGPT (unbind). Grok is still Super+Shift+Alt+A."
