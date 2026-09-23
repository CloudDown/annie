#!/usr/bin/env bash
# Capture Annie README screens from a real foot window (user theme).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/screenshots"
APP_ID="org.omarchy.annie"
CHARS="92x24"
HOLD_SEC=2.2

mkdir -p "$OUT"
cd "$ROOT"

if [[ -z "${WAYLAND_DISPLAY:-}" && -z "${DISPLAY:-}" ]]; then
  echo "Need a graphical session (WAYLAND_DISPLAY)." >&2
  exit 1
fi

capture_one() {
  local name="$1"
  local title="annie-shot-$name"
  local out="$OUT/$name.png"
  local pid=""

  # Kill leftover Annie shot windows (same app-id as the real launcher).
  hyprctl clients -j | jq -r --arg id "$APP_ID" '
    .[] | select(.class==$id and (.title|startswith("annie-shot-"))) | .pid
  ' | while read -r p; do kill "$p" 2>/dev/null || true; done

  foot \
    --app-id="$APP_ID" \
    --title="$title" \
    --window-size-chars="$CHARS" \
    --override=colors-dark.alpha=1.0 \
    --override=colors-light.alpha=1.0 \
    --working-directory="$ROOT" \
    env TERM=xterm-256color PYTHONPATH="$ROOT" python3 \
      "$ROOT/scripts/show_readme_shot.py" "$name" 30 &
  pid=$!

  local geo=""
  for _ in $(seq 1 50); do
    geo="$(hyprctl clients -j | jq -r --arg t "$title" '
      .[] | select(.title==$t)
      | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"
    ' | head -n1)"
    if [[ -n "$geo" && "$geo" != "null" ]]; then
      break
    fi
    sleep 0.1
  done

  if [[ -z "$geo" || "$geo" == "null" ]]; then
    kill "$pid" 2>/dev/null || true
    echo "window not found for $name" >&2
    return 1
  fi

  sleep "$HOLD_SEC"
  grim -g "$geo" "$out"
  echo "wrote $out ($geo)"

  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  sleep 0.25
}

for shot in prompt seasons episodes settings playback; do
  capture_one "$shot"
done

echo "done → $OUT"
