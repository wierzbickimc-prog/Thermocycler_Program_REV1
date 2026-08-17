#!/bin/bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
SHORT_VERSION="${VERSION%%-*}"
BUILD_VERSION="${BUILTDNA_BUILD_NUMBER:-1}"
APP_NAME="BUILT DNA Thermocycler.app"
DIST_DIR="$ROOT/dist"
APP_PATH="$DIST_DIR/$APP_NAME"
ZIP_NAME="BUILT-DNA-Thermocycler-macOS-v${VERSION}.zip"
ZIP_PATH="$DIST_DIR/$ZIP_NAME"
PY_SERIAL_SHA256="c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This release builder requires macOS." >&2
  exit 1
fi

rm -rf "$APP_PATH"
rm -f "$ZIP_PATH" "$ZIP_PATH.sha256"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources/app/web" \
  "$APP_PATH/Contents/Resources/vendor"

install -m 755 "$ROOT/macos/AppLauncher" "$APP_PATH/Contents/MacOS/launcher"
install -m 755 "$ROOT/macos/Start.command" \
  "$APP_PATH/Contents/Resources/Start BUILT DNA Thermocycler.command"

sed -e "s/__SHORT_VERSION__/$SHORT_VERSION/g" \
    -e "s/__BUILD_VERSION__/$BUILD_VERSION/g" \
    "$ROOT/macos/Info.plist.in" > "$APP_PATH/Contents/Info.plist"
printf 'APPL????' > "$APP_PATH/Contents/PkgInfo"

for source in serve.py devices.py profiles.py qc.py thermocycler_core.py; do
  cp "$ROOT/$source" "$APP_PATH/Contents/Resources/app/$source"
done
cp -R "$ROOT/web/." "$APP_PATH/Contents/Resources/app/web/"
cp "$ROOT/requirements-web.txt" "$APP_PATH/Contents/Resources/requirements-web.txt"
cp "$ROOT/VERSION" "$APP_PATH/Contents/Resources/VERSION"
cp "$ROOT/THIRD_PARTY_NOTICES.md" "$APP_PATH/Contents/Resources/THIRD_PARTY_NOTICES.md"

python3 -m pip download --disable-pip-version-check --only-binary=:all: \
  --no-deps --requirement "$ROOT/requirements-web.txt" \
  --dest "$APP_PATH/Contents/Resources/vendor" >/dev/null

PY_SERIAL_WHEEL="$APP_PATH/Contents/Resources/vendor/pyserial-3.5-py2.py3-none-any.whl"
actual_wheel_sha="$(shasum -a 256 "$PY_SERIAL_WHEEL" | awk '{print $1}')"
if [ "$actual_wheel_sha" != "$PY_SERIAL_SHA256" ]; then
  echo "pyserial wheel checksum mismatch" >&2
  exit 1
fi

BUILD_TMP="$(mktemp -d "${TMPDIR:-/tmp}/builtdna-app-build.XXXXXX")"
cleanup() {
  case "$BUILD_TMP" in
    "${TMPDIR:-/tmp}"/builtdna-app-build.*) rm -rf "$BUILD_TMP" ;;
  esac
}
trap cleanup EXIT

mkdir -p "$BUILD_TMP/preview" "$BUILD_TMP/AppIcon.iconset"
qlmanage -t -s 1024 -o "$BUILD_TMP/preview" "$ROOT/macos/AppIcon.svg" >/dev/null 2>&1
ICON_SOURCE="$BUILD_TMP/preview/AppIcon.svg.png"
if [ ! -f "$ICON_SOURCE" ]; then
  echo "Could not render the application icon." >&2
  exit 1
fi

while read -r size filename; do
  sips -z "$size" "$size" "$ICON_SOURCE" --out "$BUILD_TMP/AppIcon.iconset/$filename" >/dev/null
done <<'EOF'
16 icon_16x16.png
32 icon_16x16@2x.png
32 icon_32x32.png
64 icon_32x32@2x.png
128 icon_128x128.png
256 icon_128x128@2x.png
256 icon_256x256.png
512 icon_256x256@2x.png
512 icon_512x512.png
1024 icon_512x512@2x.png
EOF
iconutil -c icns "$BUILD_TMP/AppIcon.iconset" \
  -o "$APP_PATH/Contents/Resources/AppIcon.icns"

plutil -lint "$APP_PATH/Contents/Info.plist" >/dev/null
codesign --force --deep --sign - "$APP_PATH" >/dev/null
codesign --verify --deep --strict "$APP_PATH"

ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
archive_sha="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
printf '%s  %s\n' "$archive_sha" "$ZIP_NAME" > "$ZIP_PATH.sha256"

echo "Built: $ZIP_PATH"
echo "SHA-256: $archive_sha"
