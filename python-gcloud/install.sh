#!/usr/bin/env bash
# python-gcloud installer for macOS and Linux.
#
# Idempotent. Re-run any time to refresh source / re-auth.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/python-gcloud/install.sh | bash
# or:
#   bash install.sh

set -euo pipefail

ARCHIVE_URL="https://github.com/webgress/gcp_bulk_user/archive/refs/heads/main.tar.gz"
INSTALL_PARENT="${PYGCLOUD_INSTALL_DIR:-$HOME}"
INSTALL_DIR="$INSTALL_PARENT/gcp_bulk_user-main/python-gcloud"

GCLOUD_DL="https://dl.google.com/dl/cloudsdk/channels/rapid/downloads"
GCLOUD_HOME="$HOME/google-cloud-sdk"

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*" >&2; exit 1; }

detect_platform() {
  local os arch
  os=$(uname -s)
  arch=$(uname -m)
  case "$os-$arch" in
    Darwin-arm64)   GCLOUD_PKG="google-cloud-cli-darwin-arm.tar.gz" ;;
    Darwin-x86_64)  GCLOUD_PKG="google-cloud-cli-darwin-x86_64.tar.gz" ;;
    Linux-x86_64)   GCLOUD_PKG="google-cloud-cli-linux-x86_64.tar.gz" ;;
    Linux-aarch64)  GCLOUD_PKG="google-cloud-cli-linux-arm.tar.gz" ;;
    *) die "Unsupported platform: $os-$arch. Supported: macOS (arm64/x86_64), Linux (x86_64/aarch64)." ;;
  esac
  ok "Platform: $os-$arch"
}

check_python() {
  for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        PYTHON_BIN="$cmd"
        ok "Python: $("$cmd" --version) ($(command -v "$cmd"))"
        return
      fi
    fi
  done
  die "Python 3.10+ not found. Install from https://www.python.org/downloads/ and re-run."
}

install_gcloud() {
  if command -v gcloud >/dev/null 2>&1; then
    ok "gcloud already installed: $(gcloud --version 2>/dev/null | head -n1)"
    return
  fi
  say "Installing Google Cloud SDK into $GCLOUD_HOME"
  local tmp
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' RETURN
  curl -fSL --progress-bar -o "$tmp/$GCLOUD_PKG" "$GCLOUD_DL/$GCLOUD_PKG"
  tar -xzf "$tmp/$GCLOUD_PKG" -C "$HOME"
  "$GCLOUD_HOME/install.sh" \
    --quiet \
    --usage-reporting=false \
    --command-completion=true \
    --path-update=true
  # Make gcloud available in this shell.
  # shellcheck disable=SC1091
  source "$GCLOUD_HOME/path.bash.inc" 2>/dev/null || export PATH="$GCLOUD_HOME/bin:$PATH"
  ok "Installed gcloud: $(gcloud --version | head -n1)"
}

install_alpha() {
  if gcloud components list --filter="id:alpha" --format="value(state.name)" 2>/dev/null | grep -q Installed; then
    ok "gcloud alpha component already installed"
    return
  fi
  say "Installing gcloud alpha component"
  gcloud components install alpha --quiet
}

download_source() {
  say "Downloading source to $INSTALL_DIR"
  mkdir -p "$INSTALL_PARENT"
  local tmp
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' RETURN
  curl -fSL --progress-bar -o "$tmp/src.tar.gz" "$ARCHIVE_URL"
  tar -xzf "$tmp/src.tar.gz" -C "$INSTALL_PARENT"
  ok "Source extracted to $INSTALL_DIR"
}

setup_venv_and_deps() {
  say "Creating virtualenv and installing dependencies"
  cd "$INSTALL_DIR"
  "$PYTHON_BIN" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  ok "Dependencies installed in $INSTALL_DIR/.venv"
}

authenticate() {
  if gcloud auth application-default print-access-token >/dev/null 2>&1; then
    ok "Application Default Credentials already set"
    return
  fi
  say "Launching gcloud auth (browser will open)"
  cat <<'EOF'

   When prompted, say YES to setting a quota project and pick a project you own.
   Without one, API calls fail with "User project specified in the request is invalid".

EOF
  gcloud auth application-default login
}

print_next_steps() {
  cat <<EOF

$(printf '\033[1;32m✓ Install complete\033[0m')

To run the tool:

  cd $INSTALL_DIR
  source .venv/bin/activate
  python -m gcp_appliance_status --org-id YOUR_ORG_ID

If 'gcloud' is not on your PATH in a new shell, add this to your ~/.bashrc or ~/.zshrc:

  source "$GCLOUD_HOME/path.bash.inc"

EOF
}

main() {
  say "python-gcloud installer"
  detect_platform
  check_python
  install_gcloud
  install_alpha
  download_source
  setup_venv_and_deps
  authenticate
  print_next_steps
}

main "$@"
