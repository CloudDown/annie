#!/usr/bin/env bash
# Build du paquet depuis le dépôt local (sans clone GitHub).
set -euo pipefail
cd "$(dirname "$0")"
makepkg -sr --nodeps "$@"
