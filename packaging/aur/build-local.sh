#!/usr/bin/env bash
# Build the package from the local repo (no GitHub clone).
set -euo pipefail
cd "$(dirname "$0")"
makepkg -sr --nodeps "$@"
