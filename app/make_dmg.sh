#!/bin/sh
# Builds Sprocket.app with the bundled engine and packs it into a DMG with a
# shortcut to /Applications, so the user drags one onto the other.
# Usage: make_dmg.sh [--prune]    Writes .build/Sprocket.dmg.
set -e

cd "$(dirname "$0")"
./package.sh --bundle $1

STAGING=".build/dmg"
rm -rf "$STAGING" .build/Sprocket.dmg
mkdir -p "$STAGING"
cp -R .build/Sprocket.app "$STAGING/Sprocket.app"
ln -s /Applications "$STAGING/Applications"

hdiutil create -quiet -srcfolder "$STAGING" -format UDZO -volname Sprocket .build/Sprocket.dmg
rm -rf "$STAGING"
echo "built .build/Sprocket.dmg ($(du -h .build/Sprocket.dmg | cut -f1))"
