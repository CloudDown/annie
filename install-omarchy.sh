#!/usr/bin/env bash
# Compat wrapper — prefer the unified installer:
#
#   curl -fsSL https://raw.githubusercontent.com/CloudDown/annie/cursor/initial-release/install.sh | bash
#
# This script forwards to install.sh (same detection: Omarchy → desktop wiring).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HERE/install.sh" ]]; then
  exec bash "$HERE/install.sh" "$@"
fi

# Curled alone (no sibling install.sh in the same fetch).
BRANCH="${ANNIE_BRANCH:-cursor/initial-release}"
exec bash -c "$(curl -fsSL "https://raw.githubusercontent.com/CloudDown/annie/${BRANCH}/install.sh")"
