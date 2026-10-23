#!/bin/sh
# Signs a built Sprocket.app with a Developer ID, so notarytool accepts it.
# Usage: sign.sh <path to Sprocket.app> [identity]
# The identity defaults to $SIGN_IDENTITY, a name or SHA-1 hash that
# `security find-identity -v -p codesigning` lists. Pass "-" to sign ad hoc
# with the same layout, which tests the hardened runtime without a certificate.
#
# Notarisation needs every Mach-O file signed with the hardened runtime and a
# secure timestamp, inner files first and the app last (docs/PACKAGING.md,
# phase 3). One identity signs everything, so library validation passes
# without an entitlement.
set -e

APP="$1"
IDENTITY="${2:-$SIGN_IDENTITY}"
test -d "$APP" && test -n "$IDENTITY" || { echo "usage: sign.sh <Sprocket.app> [identity]"; exit 2; }

if [ "$IDENTITY" = "-" ]; then
    # An ad-hoc signature cannot take a timestamp.
    sign() { codesign --force --options runtime -s - "$@"; }
else
    sign() { codesign --force --options runtime --timestamp -s "$IDENTITY" "$@"; }
fi

# The bundled Python: the interpreter, libpython and every extension module.
# A wheel's binary can have any name, so look at each file's header, not its
# extension.
LIST="$(mktemp)"
find "$APP/Contents/Resources" -type f -print0 | xargs -0 file --no-pad -F '|' \
    | grep '| Mach-O' | cut -d'|' -f1 > "$LIST"
echo "signing $(wc -l < "$LIST" | tr -d ' ') Mach-O files in Resources"
while IFS= read -r FILE; do
    sign "$FILE" 2> /dev/null || { sign "$FILE"; exit 1; }
done < "$LIST"
rm -f "$LIST"

# An ad-hoc signature has no Team ID, so library validation would refuse
# every extension module and Sparkle ("different Team IDs"). Only the ad-hoc
# test build turns it off, on the two executables that load libraries.
if [ "$IDENTITY" = "-" ]; then
    ENTITLEMENTS="$(mktemp)"
    cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
PLIST
    sign --entitlements "$ENTITLEMENTS" "$(readlink -f "$APP/Contents/Resources/python/bin/python3")"
    set -- --entitlements "$ENTITLEMENTS"
else
    set --
fi

# Sparkle, in the order its documentation gives for a Developer ID. The
# Downloader keeps its own entitlements (network access inside its sandbox).
SPARKLE="$APP/Contents/Frameworks/Sparkle.framework"
sign "$SPARKLE/Versions/B/XPCServices/Installer.xpc"
sign --preserve-metadata=entitlements "$SPARKLE/Versions/B/XPCServices/Downloader.xpc"
sign "$SPARKLE/Versions/B/Autoupdate"
sign "$SPARKLE/Versions/B/Updater.app"
sign "$SPARKLE"

# The app last: its signature seals everything above.
sign "$@" "$APP"
test -z "$ENTITLEMENTS" || rm -f "$ENTITLEMENTS"
codesign --verify --deep --strict "$APP"
echo "signed $APP"
