#!/bin/sh
# Builds SprocketApp and wraps it in a proper .app bundle, so macOS gives it
# a Dock icon and a Cmd-Tab entry. Without a bundle identifier the raw
# executable from `swift build` has neither.
#
# With --bundle it also builds in release mode and copies the viewer and a
# self-contained Python engine inside, so the .app runs with no source tree.
# Add --prune after it to cut the bundle's size (see docs/PACKAGING.md).
set -e

cd "$(dirname "$0")"
if [ "$1" = "--bundle" ]; then
    CONFIG=release
else
    CONFIG=debug
fi
swift build -c "$CONFIG"

# The engine's version is the release version (release.yml raises it and tags it).
VERSION=$(grep -m1 '^version' ../engine/pyproject.toml | cut -d'"' -f2)

# Only a release bundle checks for updates (Updater.swift), so a source build
# never replaces itself with a release. The private key that matches
# SUPublicEDKey is in the login keychain (account "sprocket") and in the
# SPARKLE_PRIVATE_KEY secret; release.yml signs each DMG with it.
FEED_URL="https://github.com/dakl/sprocket-releases/releases/latest/download/appcast.xml"
PUBLIC_ED_KEY="Rra3r/uPJX+8Oh/51CVOwS3gPBcTQHkfxH3jtzlsk5c="
UPDATE_KEYS=""
if [ "$1" = "--bundle" ]; then
    UPDATE_KEYS="<key>SUFeedURL</key>
    <string>$FEED_URL</string>
    <key>SUPublicEDKey</key>
    <string>$PUBLIC_ED_KEY</string>"
fi

APP=".build/Sprocket.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp ".build/$CONFIG/SprocketApp" "$APP/Contents/MacOS/Sprocket"
# The binary finds Sparkle through the @executable_path/../Frameworks rpath.
mkdir -p "$APP/Contents/Frameworks"
ditto ".build/$CONFIG/Sparkle.framework" "$APP/Contents/Frameworks/Sparkle.framework"

# Finder and the Dock read the bundle's .icns. Build it from AppIcon.png, which
# already has the squircle and shadow (make_icon.py). macOS adds neither.
ICONSET=".build/AppIcon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET" "$APP/Contents/Resources"
for size in 16 32 128 256 512; do
    sips -z $size $size AppIcon.png --out "$ICONSET/icon_${size}x${size}.png" > /dev/null
done
for size in 16 32 128 256; do
    sips -z $((size * 2)) $((size * 2)) AppIcon.png --out "$ICONSET/icon_${size}x${size}@2x.png" > /dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Sprocket</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>dev.sprocket.app</string>
    <key>CFBundleName</key>
    <string>Sprocket</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    $UPDATE_KEYS
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
</dict>
</plist>
EOF

if [ "$1" = "--bundle" ]; then
    if [ "$2" = "--prune" ]; then export SPROCKET_PRUNE=1; fi
    ./bundle_engine.sh "$APP/Contents/Resources"
fi

# Ad hoc, until phase 3 of docs/PACKAGING.md signs with a Developer ID. Sparkle's
# helpers (Autoupdate, the XPC services) are bundles inside the framework, so
# --deep reaches them. The app is signed last, after everything inside it.
codesign --force --deep -s - "$APP/Contents/Frameworks/Sparkle.framework"
codesign --force -s - "$APP"

echo "built $APP"
