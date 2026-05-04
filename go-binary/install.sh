#!/usr/bin/env bash
# go-binary prerequisites installer for macOS and Linux.
#
# Installs Google Cloud SDK (if missing) and runs interactive auth so that
# gcp-appliance-status finds Application Default Credentials when launched.
#
# Does NOT download the gcp-appliance-status binary itself — fetch it from
# https://github.com/webgress/gcp_bulk_user/releases/latest separately.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/webgress/gcp_bulk_user/main/go-binary/install.sh | bash
# or:
#   bash install.sh

set -euo pipefail

GCLOUD_DL="https://dl.google.com/dl/cloudsdk/channels/rapid/downloads"
GCLOUD_HOME="$HOME/google-cloud-sdk"
RELEASE_PAGE="https://github.com/webgress/gcp_bulk_user/releases/latest"

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*" >&2; exit 1; }

detect_platform() {
  local os arch
  os=$(uname -s)
  arch=$(uname -m)
  case "$os-$arch" in
    Darwin-arm64)   GCLOUD_PKG="google-cloud-cli-darwin-arm.tar.gz";       BIN_SUFFIX="darwin-arm64" ;;
    Darwin-x86_64)  GCLOUD_PKG="google-cloud-cli-darwin-x86_64.tar.gz";    BIN_SUFFIX="darwin-amd64" ;;
    Linux-x86_64)   GCLOUD_PKG="google-cloud-cli-linux-x86_64.tar.gz";     BIN_SUFFIX="linux-amd64"  ;;
    Linux-aarch64)  GCLOUD_PKG="google-cloud-cli-linux-arm.tar.gz";        BIN_SUFFIX="linux-arm64"  ;;
    *) die "Unsupported platform: $os-$arch. Supported: macOS (arm64/x86_64), Linux (x86_64/aarch64)." ;;
  esac
  ok "Platform: $os-$arch (binary suffix: $BIN_SUFFIX)"
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
  # shellcheck disable=SC1091
  source "$GCLOUD_HOME/path.bash.inc" 2>/dev/null || export PATH="$GCLOUD_HOME/bin:$PATH"
  ok "Installed gcloud: $(gcloud --version | head -n1)"
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

$(printf '\033[1;32m✓ Prerequisites complete\033[0m')

Next:
  1. Download the binary for this machine from:
       $RELEASE_PAGE
     File:  gcp-appliance-status-$BIN_SUFFIX
  2. Make it executable and put it on your PATH:
       chmod +x gcp-appliance-status-$BIN_SUFFIX
       sudo mv gcp-appliance-status-$BIN_SUFFIX /usr/local/bin/gcp-appliance-status
  3. Run:
       gcp-appliance-status --org-id YOUR_ORG_ID

If 'gcloud' is not on your PATH in a new shell, add this to your ~/.bashrc or ~/.zshrc:

  source "$GCLOUD_HOME/path.bash.inc"

EOF
}

main() {
  say "go-binary prerequisites installer"
  detect_platform
  install_gcloud
  authenticate
  print_next_steps
}

main "$@"
