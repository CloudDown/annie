#!/usr/bin/env bash
# One-shot Omarchy install: clone (if needed) then `make omarchy`.
#
#   curl -fsSL https://raw.githubusercontent.com/CloudDown/annie/cursor/initial-release/install-omarchy.sh | bash
#
# Layout (XDG):
#   app     ~/.local/share/annie
#   binary  ~/.local/bin/annie
#   config  ~/.config/annie
#
# Optional: ANNIE_DIR=/path ANNIE_BRANCH=master bash …
set -euo pipefail

REPO="https://github.com/CloudDown/annie.git"
# Feature branch until this lands on master (raw …/master/… is 404 today).
BRANCH="${ANNIE_BRANCH:-cursor/initial-release}"
DEFAULT_DEST="${XDG_DATA_HOME:-$HOME/.local/share}/annie"

if [[ -n "${ANNIE_DIR:-}" ]]; then
  DEST="$ANNIE_DIR"
elif [[ -f Makefile && -f packaging/omarchy/install.sh ]]; then
  # Already in a checkout: install from here (dev / existing clone).
  DEST="$(pwd)"
else
  DEST="$DEFAULT_DEST"
fi

if [[ ! -f "$DEST/Makefile" ]]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --branch "$BRANCH" --single-branch "$REPO" "$DEST"
elif [[ -d "$DEST/.git" ]]; then
  git -C "$DEST" fetch --quiet origin "$BRANCH" || true
  git -C "$DEST" checkout --quiet "$BRANCH" 2>/dev/null || true
fi

cd "$DEST"
exec make omarchy
