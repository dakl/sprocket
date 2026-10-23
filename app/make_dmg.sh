#!/bin/sh
# Builds Sprocket.app with the bundled engine and packs it into a DMG with a
# shortcut to /Applications, so the user drags one onto the other.
# Usage: make_dmg.sh [--prune]    Writes .build/Sprocket.dmg.
# With SIGN_IDENTITY set it signs the app and the DMG with that Developer ID
# (sign.sh). notarize.sh then notarises the DMG.
set -e

cd "$(dirname "$0")"
./package.sh --bundle $1
if [ -n "$SIGN_IDENTITY" ]; then
    ./sign.sh .build/Sprocket.app
fi

STAGING=".build/dmg"
rm -rf "$STAGING" .build/Sprocket.dmg
mkdir -p "$STAGING"
cp -R .build/Sprocket.app "$STAGING/Sprocket.app"
ln -s /Applications "$STAGING/Applications"

hdiutil create -quiet -srcfolder "$STAGING" -format UDZO -volname Sprocket .build/Sprocket.dmg
rm -rf "$STAGING"
if [ -n "$SIGN_IDENTITY" ]; then
    codesign --timestamp -s "$SIGN_IDENTITY" .build/Sprocket.dmg
fi
echo "built .build/Sprocket.dmg ($(du -h .build/Sprocket.dmg | cut -f1))"
