#!/bin/bash

# First-run launcher for the BUILT DNA Thermocycler Console.
# It installs an isolated, checksum-verified Python runtime in the current
# user's Library. It never modifies the system Python and does not need sudo.

set -u

APP_NAME="BUILT DNA Thermocycler"
PYTHON_VERSION="3.12.14"
PYTHON_BUILD="20260814"
RESOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$RESOURCE_DIR/app"
SUPPORT_DIR="${BUILTDNA_SUPPORT_DIR:-$HOME/Library/Application Support/$APP_NAME}"
RUNTIME_ROOT="$SUPPORT_DIR/runtime"
LOG_DIR="$SUPPORT_DIR/logs"
PORT="${BUILTDNA_PORT:-8765}"
URL="http://127.0.0.1:$PORT"
STOP_REQUESTED=0

export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

trap 'STOP_REQUESTED=1' INT TERM

mkdir -p "$RUNTIME_ROOT" "$LOG_DIR"
LOG_FILE="$LOG_DIR/launcher.log"
touch "$LOG_FILE"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

fail() {
  message="$1"
  log "ERROR: $message"
  /usr/bin/osascript -e "display alert \"BUILT DNA Thermocycler could not start\" message \"$message\n\nDetails: $LOG_FILE\" as critical" >/dev/null 2>&1 || true
  if [ "${BUILTDNA_NONINTERACTIVE:-0}" != "1" ]; then
    printf '\nPress Return to close this window. '
    read -r _unused
  fi
  exit 1
}

if [ "$(uname -s)" != "Darwin" ]; then
  fail "This launcher requires macOS."
fi

case "$(uname -m)" in
  arm64)
    RUNTIME_ARCH="aarch64"
    RUNTIME_SHA256="dd5b76ab11451a4a4367c17c61d944dded56b425396b07f102922a7ebef7d55f"
    ;;
  x86_64)
    RUNTIME_ARCH="x86_64"
    RUNTIME_SHA256="aec265e3cddaccdb2a3d783331596351b24d4a63c97af0a38f75f643c9451de9"
    ;;
  *)
    fail "Unsupported Mac architecture: $(uname -m)."
    ;;
esac

RUNTIME_FILE="cpython-${PYTHON_VERSION}+${PYTHON_BUILD}-${RUNTIME_ARCH}-apple-darwin-install_only_stripped.tar.gz"
RUNTIME_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD}/cpython-${PYTHON_VERSION}%2B${PYTHON_BUILD}-${RUNTIME_ARCH}-apple-darwin-install_only_stripped.tar.gz"
RUNTIME_DIR="$RUNTIME_ROOT/${PYTHON_VERSION}-${PYTHON_BUILD}-${RUNTIME_ARCH}"
PYTHON="$RUNTIME_DIR/bin/python3"

install_runtime() {
  if [ "${BUILTDNA_NONINTERACTIVE:-0}" != "1" ]; then
    /usr/bin/osascript -e 'display dialog "The first launch downloads a private Python runtime (about 25 MB) and installs it in your Library. No administrator password, Homebrew, or Xcode is required." with title "Set up BUILT DNA Thermocycler" buttons {"Cancel", "Continue"} default button "Continue" cancel button "Cancel" with icon note' >/dev/null 2>&1 || exit 0
  fi

  INSTALL_LOCK="$RUNTIME_ROOT/.install-lock"
  wait_count=0
  while ! mkdir "$INSTALL_LOCK" 2>/dev/null; do
    if [ -x "$PYTHON" ]; then
      return 0
    fi
    wait_count=$((wait_count + 1))
    if [ "$wait_count" -ge 120 ]; then
      fail "Another installation appears to be stuck. Remove $INSTALL_LOCK and try again."
    fi
    sleep 1
  done

  TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/builtdna-runtime.XXXXXX")" || fail "Could not create a temporary installation directory."
  ARCHIVE="$TMP_ROOT/$RUNTIME_FILE"
  EXTRACT_DIR="$TMP_ROOT/extract"
  mkdir -p "$EXTRACT_DIR"

  cleanup_install() {
    case "$TMP_ROOT" in
      "${TMPDIR:-/tmp}"/builtdna-runtime.*) rm -rf "$TMP_ROOT" ;;
    esac
    rmdir "$INSTALL_LOCK" 2>/dev/null || true
  }
  trap cleanup_install EXIT INT TERM

  log "Downloading Python $PYTHON_VERSION for $(uname -m)..."
  /usr/bin/curl --fail --location --retry 3 --progress-bar "$RUNTIME_URL" -o "$ARCHIVE" || fail "The Python runtime download failed. Check your internet connection and try again."

  actual_sha="$(/usr/bin/shasum -a 256 "$ARCHIVE" | /usr/bin/awk '{print $1}')"
  if [ "$actual_sha" != "$RUNTIME_SHA256" ]; then
    fail "The Python runtime checksum did not match; the download was discarded."
  fi
  log "Runtime checksum verified."

  /usr/bin/tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR" || fail "The Python runtime archive could not be unpacked."
  if [ ! -x "$EXTRACT_DIR/python/bin/python3" ]; then
    fail "The Python runtime archive has an unexpected layout."
  fi

  if [ -e "$RUNTIME_DIR" ]; then
    mv "$RUNTIME_DIR" "$RUNTIME_DIR.incomplete.$(date +%s)" || fail "Could not replace an incomplete runtime."
  fi
  mv "$EXTRACT_DIR/python" "$RUNTIME_DIR" || fail "Could not install the private Python runtime."
  cleanup_install
  trap - EXIT INT TERM
  log "Private Python runtime installed."
}

if [ ! -x "$PYTHON" ]; then
  install_runtime
fi

if ! PYTHONNOUSERSITE=1 "$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
  fail "The private Python runtime is damaged. Remove $RUNTIME_DIR and launch again."
fi

if ! PYTHONNOUSERSITE=1 "$PYTHON" -c 'import serial; assert serial.VERSION == "3.5"' >/dev/null 2>&1; then
  log "Installing the bundled USB serial dependency..."
  PYTHONNOUSERSITE=1 "$PYTHON" -m pip install \
    --disable-pip-version-check --no-index --no-deps \
    --find-links "$RESOURCE_DIR/vendor" \
    --requirement "$RESOURCE_DIR/requirements-web.txt" >>"$LOG_FILE" 2>&1 || fail "The bundled USB serial dependency could not be installed."
fi

if ! PYTHONNOUSERSITE=1 "$PYTHON" -c 'import serial; assert serial.VERSION == "3.5"' >/dev/null 2>&1; then
  fail "The USB serial dependency did not pass its verification check."
fi

trap 'STOP_REQUESTED=1' INT TERM

if /usr/bin/curl --fail --silent --max-time 1 "$URL" | /usr/bin/grep -q "BUILT DNA"; then
  log "The console is already running; opening it in your browser."
  if [ "${BUILTDNA_NO_BROWSER:-0}" != "1" ]; then
    /usr/bin/open "$URL"
  fi
  exit 0
fi

log "Dependencies verified. Starting $APP_NAME at $URL"
cd "$APP_DIR" || fail "The application files are missing. Download the release again."

SERVER_ARGS=("serve.py" "--port" "$PORT")
if [ "${BUILTDNA_NO_BROWSER:-0}" = "1" ]; then
  SERVER_ARGS+=("--no-browser")
fi
if [ "${BUILTDNA_NO_SCAN:-0}" = "1" ]; then
  SERVER_ARGS+=("--no-scan")
fi

PYTHONNOUSERSITE=1 "$PYTHON" "${SERVER_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
server_status=${PIPESTATUS[0]}
if [ "$STOP_REQUESTED" -eq 1 ] || [ "$server_status" -eq 130 ] || [ "$server_status" -eq 143 ]; then
  log "$APP_NAME stopped."
  exit 0
fi
if [ "$server_status" -ne 0 ]; then
  fail "The local console server exited unexpectedly."
fi

log "$APP_NAME stopped."
