#!/usr/bin/env bash
# One-shot Omarchy install: clone (if needed) then `make omarchy`.
#
#   curl -fsSL https://raw.githubusercontent.com/CloudDown/annie/master/install-omarchy.sh | bash
#
set -euo pipefail

REPO="https://github.com/CloudDown/annie.git"

if [[ -f Makefile && -f packaging/omarchy/install.sh ]]; then
  DEST="$(pwd)"
elif [[ -n "${ANNIE_DIR:-}" ]]; then
  DEST="$ANNIE_DIR"
elif [[ -d "$HOME/projet/annie/.git" ]]; then
  DEST="$HOME/projet/annie"
else
  DEST="$HOME/annie"
fi

if [[ ! -f "$DEST/Makefile" ]]; then
  git clone "$REPO" "$DEST"
fi

cd "$DEST"
exec make omarchy
