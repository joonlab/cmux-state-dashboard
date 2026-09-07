#!/bin/bash
# CmuxMinimap.app 번들 빌드 + 코드서명
# 사용: bash scripts/bundle.sh [debug|release]   (기본 release)
#
# ⚠️ .app 번들이 필요한 이유는 **권한이 아니라 LSUIElement** 다.
#    `swift build` 가 낸 맨 바이너리에는 Info.plist 가 없어 Dock 에 아이콘이 뜨고,
#    메뉴바 전용 앱이 되지 않는다. 이 앱은 자기 status item 만 그리고 127.0.0.1 에만
#    말을 걸므로 TCC(손쉬운 사용·화면 기록) 권한도, 로컬 네트워크 권한도 필요 없다.
#    (루프백은 macOS 의 로컬 네트워크 권한 대상이 아니다.)
#    → 그래서 ad-hoc 서명으로 충분하다. 안정 서명이 필요하면 CMUXMM_SIGN_ID 로 지정한다.
set -euo pipefail

CONFIG="${1:-release}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_NAME="CmuxMinimap"
BUNDLE_ID="com.joonlab.cmux-minimap"
VERSION="0.1"
BUILD="1"

echo "▶ swift build ($CONFIG)…"
swift build -c "$CONFIG"
BIN="$(swift build -c "$CONFIG" --show-bin-path)/$APP_NAME"

APP="$ROOT/$APP_NAME.app"
echo "▶ 번들 조립: $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>cmux 관제실</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$BUILD</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSHumanReadableCopyright</key><string>JoonLab</string>
</dict>
</plist>
PLIST

SIGN_ID="${CMUXMM_SIGN_ID:--}"
[ "$SIGN_ID" = "-" ] && echo "▶ ad-hoc 코드서명…" || echo "▶ 코드서명 (정체성: $SIGN_ID)…"
codesign --force --deep --sign "$SIGN_ID" "$APP"

echo "✅ 완료: $APP"
