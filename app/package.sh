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

APP=".build/SprocketApp.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp ".build/$CONFIG/SprocketApp" "$APP/Contents/MacOS/SprocketApp"

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
    <string>SprocketApp</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>dev.sprocket.app</string>
    <key>CFBundleName</key>
    <string>Sprocket</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
</dict>
</plist>
EOF

if [ "$1" = "--bundle" ]; then
    if [ "$2" = "--prune" ]; then export SPROCKET_PRUNE=1; fi
    ./bundle_engine.sh "$APP/Contents/Resources"
fi

echo "built $APP"
