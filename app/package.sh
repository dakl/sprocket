#!/bin/sh
# Builds SprocketApp and wraps it in a proper .app bundle, so macOS gives it
# a Dock icon and a Cmd-Tab entry. Without a bundle identifier the raw
# executable from `swift build` has neither.
set -e

cd "$(dirname "$0")"
swift build

APP=".build/SprocketApp.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/debug/SprocketApp "$APP/Contents/MacOS/SprocketApp"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>SprocketApp</string>
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

echo "built $APP"
