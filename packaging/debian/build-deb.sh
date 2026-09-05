#!/usr/bin/env bash
# Build a .deb package from the repo root (Debian / Ubuntu).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
PKG="annie_${VERSION}_all"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "==> Build wheel"
python3 -m pip install -q build
python3 -m build --wheel --outdir "$STAGING/wheels"

echo "==> Stage files"
mkdir -p "$STAGING/$PKG/DEBIAN"
mkdir -p "$STAGING/$PKG/usr"

PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 python3 -m pip install \
  --root="$STAGING/$PKG/usr" \
  --prefix=/usr \
  --no-deps \
  --no-compile \
  "$STAGING/wheels"/*.whl

find "$STAGING/$PKG/usr" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -f "$STAGING/$PKG/usr/lib/python"*/site-packages/annie-*.dist-info/direct_url.json 2>/dev/null || true

install -Dm644 README.md "$STAGING/$PKG/usr/share/doc/annie/README.md"

cat >"$STAGING/$PKG/DEBIAN/control" <<EOF
Package: annie
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-libtorrent, fzf
Recommends: mpv | vlc
Maintainer: CloudDown <noreply@github.com>
Description: Anime torrent CLI — Nyaa, MAL catalog, fzf, libtorrent streaming
 CLI pour chercher des anime sur Nyaa.si, parcourir un catalogue MAL,
 choisir une release avec fzf, et lire en streaming via libtorrent.
EOF

OUT="$ROOT/dist/${PKG}.deb"
mkdir -p "$ROOT/dist"
dpkg-deb --build --root-owner-group "$STAGING/$PKG" "$OUT"
echo "==> Package built: $OUT"
