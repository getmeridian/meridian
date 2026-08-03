#!/usr/bin/env bash
# =============================================================================
# Meridian CLI Installer
#
# Installs `meridian` from PyPI via uv (preferred) or pipx (fallback).
#
# Usage:
#   curl -sSf https://getmeridian.org/install.sh | bash
# =============================================================================
set -euo pipefail

R='\033[0m' B='\033[1m' D='\033[2m'
G='\033[32m' C='\033[36m' RED='\033[31m'

info()  { printf "  ${C}→${R} %s\n" "$*"; }
ok()    { printf "  ${G}✓${R} %s\n" "$*"; }
fail()  { printf "\n  ${RED}✗ %s${R}\n" "$*" >&2; exit 1; }

PYPI_PACKAGE="meridian-vpn"

printf "\n"
printf "  ${B}Meridian Installer${R}\n"
printf "\n"

# =============================================================================
# Install uv if not present
# =============================================================================
install_uv() {
  info "Installing uv (Python package manager)..."
  if command -v curl &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh </dev/null 2>/dev/null | bash 2>/dev/null
  elif command -v wget &>/dev/null; then
    wget -qO- https://astral.sh/uv/install.sh </dev/null 2>/dev/null | bash 2>/dev/null
  else
    return 1
  fi
  # Source uv's env to get it on PATH
  # shellcheck disable=SC1091
  [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env" 2>/dev/null || true
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

# =============================================================================
# Install meridian-vpn from PyPI
# =============================================================================
INSTALLED=false

# Strategy 1: uv tool install (preferred — fastest, manages Python)
if command -v uv &>/dev/null || install_uv; then
  if command -v uv &>/dev/null; then
    info "Installing meridian via uv..."
    if uv tool install "$PYPI_PACKAGE" --upgrade </dev/null 2>&1; then
      INSTALLED=true
      ok "Installed via uv"
    fi
  fi
fi

# Strategy 2: pipx (fallback — available via apt/brew on many systems)
if [[ "$INSTALLED" != true ]] && command -v pipx &>/dev/null; then
  info "Installing meridian via pipx..."
  if pipx install "$PYPI_PACKAGE" --force </dev/null 2>&1; then
    INSTALLED=true
    ok "Installed via pipx"
  fi
fi

# Strategy 3: pip3 --user (last resort)
if [[ "$INSTALLED" != true ]] && command -v pip3 &>/dev/null; then
  info "Installing meridian via pip3..."
  if pip3 install --user --upgrade "$PYPI_PACKAGE" </dev/null 2>&1; then
    INSTALLED=true
    ok "Installed via pip3"
  elif pip3 install --user --break-system-packages "$PYPI_PACKAGE" </dev/null 2>&1; then
    INSTALLED=true
    ok "Installed via pip3"
  fi
fi

if [[ "$INSTALLED" != true ]]; then
  fail "Could not install meridian. Please install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# =============================================================================
# Ensure PATH includes tool bin directories
# =============================================================================
# On Debian/Ubuntu, .bashrc has an interactivity guard near the top:
#   case $- in *i*) ;; *) return;; esac
# Anything appended AFTER this guard is unreachable for non-interactive shells
# (e.g. `ssh host 'meridian ...'`). We prepend our PATH export so it runs
# before the guard, making tools available in all shell contexts.
ensure_path() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0

  SHELL_NAME=$(basename "${SHELL:-/bin/bash}")
  PROFILE=""
  case "$SHELL_NAME" in
    zsh)  PROFILE="$HOME/.zshrc" ;;
    bash)
      if [[ -f "$HOME/.bashrc" ]]; then
        PROFILE="$HOME/.bashrc"
      elif [[ -f "$HOME/.bash_profile" ]]; then
        PROFILE="$HOME/.bash_profile"
      fi ;;
  esac

  MARKER="# Meridian CLI"
  EXPORT_LINE="export PATH=\"$dir:\$PATH\""

  if [[ -n "$PROFILE" ]] && ! grep -qF "$MARKER" "$PROFILE" 2>/dev/null; then
    if [[ "$SHELL_NAME" == "bash" ]] && grep -q 'case \$- in' "$PROFILE" 2>/dev/null; then
      # Prepend before interactivity guard so non-interactive SSH works
      TMPFILE=$(mktemp)
      printf '%s\n%s\n\n' "$MARKER" "$EXPORT_LINE" > "$TMPFILE"
      cat "$PROFILE" >> "$TMPFILE"
      mv "$TMPFILE" "$PROFILE"
    else
      # No guard (zsh, bash_profile, etc.) — append is fine
      printf '\n%s\n%s\n' "$MARKER" "$EXPORT_LINE" >> "$PROFILE"
    fi
    ok "Added $dir to PATH in $PROFILE"
    PATH_ADDED=1
  fi

  # Ensure current session has the path too
  if [[ ":$PATH:" != *":$dir:"* ]]; then
    export PATH="$dir:$PATH"
  fi
}

PATH_ADDED=0
ensure_path "$HOME/.local/bin"
# macOS pip3 --user installs to ~/Library/Python/*/bin
for pybin in "$HOME"/Library/Python/*/bin; do
  [[ -d "$pybin" ]] && ensure_path "$pybin"
done

# =============================================================================
# Symlink to /usr/local/bin/ so `sudo meridian` works on servers
# =============================================================================
MERIDIAN_BIN="$(command -v meridian 2>/dev/null || true)"
if [[ -n "$MERIDIAN_BIN" ]] && [[ "$MERIDIAN_BIN" != "/usr/local/bin/meridian" ]]; then
  if sudo -n ln -sf "$MERIDIAN_BIN" /usr/local/bin/meridian 2>/dev/null; then
    ok "Linked to /usr/local/bin/meridian (sudo meridian will work)"
  fi
fi

# =============================================================================
# Verify and show success
# =============================================================================
INSTALLED_VERSION=""
if command -v meridian &>/dev/null; then
  INSTALLED_VERSION=$(meridian --version 2>/dev/null | awk '{print $2}' || true)
fi

printf "\n"
printf "  ${G}${B}Meridian ${INSTALLED_VERSION:-CLI} installed.${R}\n\n"

if [[ "$PATH_ADDED" == "1" ]]; then
  PROFILE_NAME=$(basename "${PROFILE:-shell config}")
  printf "  ${D}To start using meridian, run:${R}\n\n"
  printf "     ${C}source ~/${PROFILE_NAME}${R}\n\n"
  printf "  ${D}Then:${R}\n\n"
fi
printf "  ${B}Quick start:${R}\n"
printf "     ${C}meridian deploy${R}              ${D}# deploy proxy (interactive wizard)${R}\n"
printf "     ${C}meridian deploy 198.51.100.10${R} ${D}# deploy to specific server${R}\n\n"
printf "  ${B}Before deploying:${R}\n"
printf "     ${C}meridian preflight 198.51.100.10${R} ${D}# validate server (ports, OS, DNS)${R}\n"
printf "     ${C}meridian scan 198.51.100.10${R}  ${D}# find best SNI target nearby${R}\n\n"
printf "  ${B}After deploying:${R}\n"
printf "     ${C}meridian test 198.51.100.10${R}  ${D}# test connection from your device${R}\n"
printf "     ${C}meridian client add alice${R}    ${D}# share access with others${R}\n"
printf "     ${C}meridian client list${R}         ${D}# view all clients${R}\n\n"
printf "  ${D}All commands: meridian --help${R}\n"
printf "  ${D}Docs: https://getmeridian.org${R}\n"
printf "\n"
