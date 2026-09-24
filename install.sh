#!/usr/bin/env bash
# One-shot Annie install — detects the host and installs accordingly.
#
#   curl -fsSL https://raw.githubusercontent.com/CloudDown/annie/cursor/initial-release/install.sh | bash
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

log() { printf 'annie: %s\n' "$*"; }
die() { printf 'annie: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# Prefer user bins from uv / previous installs.
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

detect_flavor() {
  if have omarchy; then
    printf '%s\n' omarchy
  elif [[ "$(uname -s)" == Darwin ]]; then
    printf '%s\n' macos
  elif [[ -f /etc/arch-release ]] || have pacman; then
    printf '%s\n' arch
  elif [[ -f /etc/debian_version ]]; then
    printf '%s\n' debian
  elif [[ -f /etc/fedora-release ]] || [[ -f /etc/redhat-release ]]; then
    printf '%s\n' fedora
  else
    printf '%s\n' linux
  fi
}

ensure_git() {
  have git && return 0
  case "$FLAVOR" in
    omarchy) omarchy pkg add git ;;
    arch) sudo pacman -S --needed --noconfirm git ;;
    debian) sudo apt-get update -qq && sudo apt-get install -y -qq git ;;
    fedora) sudo dnf install -y git ;;
    macos)
      if have brew; then brew install git
      else die "install Xcode CLT or git, then re-run"
      fi
      ;;
    *) die "git is required (install it, then re-run)" ;;
  esac
  have git || die "git still missing after install"
}

ensure_uv() {
  have uv && return 0
  log "installing uv…"
  case "$FLAVOR" in
    omarchy) omarchy pkg add uv ;;
    arch)
      if pacman -Si uv &>/dev/null; then
        sudo pacman -S --needed --noconfirm uv
      else
        curl -LsSf https://astral.sh/uv/install.sh | sh
      fi
      ;;
    macos)
      if have brew; then brew install uv
      else curl -LsSf https://astral.sh/uv/install.sh | sh
      fi
      ;;
    *)
      curl -LsSf https://astral.sh/uv/install.sh | sh
      ;;
  esac
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
  have uv || die "uv still missing — open a new shell or add ~/.local/bin to PATH"
}

ensure_mpv() {
  have mpv && return 0
  log "installing mpv…"
  case "$FLAVOR" in
    omarchy) omarchy pkg add mpv ;;
    arch) sudo pacman -S --needed --noconfirm mpv ;;
    debian) sudo apt-get update -qq && sudo apt-get install -y -qq mpv ;;
    fedora) sudo dnf install -y mpv ;;
    macos)
      if have brew; then brew install mpv
      else
        log "install mpv yourself (brew install mpv), continuing without it"
        return 0
      fi
      ;;
    *)
      log "install mpv yourself for playback, continuing without it"
      return 0
      ;;
  esac
  have mpv || log "mpv not on PATH yet — install it for playback"
}

ensure_make() {
  have make && return 0
  case "$FLAVOR" in
    omarchy) omarchy pkg add make ;;
    arch) sudo pacman -S --needed --noconfirm make ;;
    debian) sudo apt-get update -qq && sudo apt-get install -y -qq make ;;
    fedora) sudo dnf install -y make ;;
    macos) true ;; # Xcode CLT
    *) true ;;
  esac
}

resolve_dest() {
  if [[ -n "${ANNIE_DIR:-}" ]]; then
    DEST="$ANNIE_DIR"
  elif [[ -f Makefile && -f pyproject.toml ]]; then
    DEST="$(pwd)"
  else
    DEST="$DEFAULT_DEST"
  fi
}

sync_repo() {
  if [[ ! -f "$DEST/Makefile" ]]; then
    log "cloning $REPO ($BRANCH) → $DEST"
    mkdir -p "$(dirname "$DEST")"
    git clone --branch "$BRANCH" --single-branch "$REPO" "$DEST"
  elif [[ -d "$DEST/.git" ]]; then
    log "updating $DEST ($BRANCH)"
    git -C "$DEST" fetch --quiet origin "$BRANCH" || true
    git -C "$DEST" checkout --quiet "$BRANCH" 2>/dev/null || true
    git -C "$DEST" pull --ff-only --quiet origin "$BRANCH" 2>/dev/null || true
  fi
  [[ -f "$DEST/Makefile" ]] || die "no Makefile in $DEST"
}

install_from_source() {
  cd "$DEST"
  if have make; then
    make install
  else
    uv sync
    uv run python -c "from annie.user_config import ensure_user_config; ensure_user_config()"
    mkdir -p "$HOME/.local/bin"
    ln -sfn "$DEST/.venv/bin/annie" "$HOME/.local/bin/annie"
  fi
}

install_omarchy() {
  cd "$DEST"
  if have make; then
    make omarchy
  else
    install_from_source
    "$DEST/packaging/omarchy/install.sh"
  fi
}

path_hint() {
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      log "add ~/.local/bin to your PATH, e.g.:"
      log "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
      ;;
  esac
}

# --- main ---
FLAVOR="$(detect_flavor)"
log "detected: $FLAVOR"

ensure_git
ensure_uv
ensure_mpv
ensure_make
resolve_dest
sync_repo

case "$FLAVOR" in
  omarchy)
    log "Omarchy → tui-install + Super+Shift+I"
    install_omarchy
    ;;
  *)
    log "source install via uv (AUR package not required)"
    install_from_source
    log "done. run: annie"
    log "  app     $DEST"
    log "  binary  $HOME/.local/bin/annie"
    log "  config  ${XDG_CONFIG_HOME:-$HOME/.config}/annie"
    ;;
esac

path_hint
