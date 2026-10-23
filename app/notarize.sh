#!/bin/sh
# Notarises a signed DMG and staples the ticket to it, so Gatekeeper opens the
# app on a Mac that is offline. Usage: notarize.sh <path to .dmg>
# Needs APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD (from appleid.apple.com) and
# APPLE_TEAM_ID, the same secrets codez and papershelf use.
set -e

DMG="$1"
test -f "$DMG" || { echo "usage: notarize.sh <dmg>"; exit 2; }
for VAR in APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID; do
    eval "test -n \"\$$VAR\"" || { echo "$VAR is not set"; exit 2; }
done

# --wait returns when Apple has decided, usually in a few minutes. A rejection
# still exits 0, so read the status, and print Apple's log, which names each
# file it refused.
RESULT="$(mktemp)"
xcrun notarytool submit "$DMG" --wait --output-format json \
    --apple-id "$APPLE_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$APPLE_TEAM_ID" > "$RESULT"
cat "$RESULT"
STATUS="$(plutil -extract status raw "$RESULT")"
ID="$(plutil -extract id raw "$RESULT")"
rm -f "$RESULT"
if [ "$STATUS" != "Accepted" ]; then
    xcrun notarytool log "$ID" \
        --apple-id "$APPLE_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$APPLE_TEAM_ID"
    echo "notarisation: $STATUS"
    exit 1
fi

xcrun stapler staple "$DMG"
echo "notarised and stapled $DMG"
