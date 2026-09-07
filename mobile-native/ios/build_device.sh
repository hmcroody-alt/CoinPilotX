#!/bin/bash
# Device build + install for P3r7or.
#
# `expo run:ios` is broken in this Xcode, so this drives xcodebuild directly and
# installs with devicectl. Release rather than Debug: Release is self-contained,
# so the install does not depend on a Metro server still being up when the app
# launches.
#
# Must never run at the same time as build_sim.sh. ReactCodegen generates into
# the shared source tree rather than into derived data, so a separate
# -derivedDataPath does NOT isolate two concurrent builds from each other.
set -euo pipefail

cd "$(dirname "$0")"

DEVICE_ID="F45E640F-6D02-514E-877C-B764E8D6818F"   # P3r7or
DERIVED="$HOME/Library/Developer/Xcode/DerivedData/pulsesoc-device-build"

echo "=== [device] xcodebuild Release ==="
xcodebuild \
  -workspace PulseSoc.xcworkspace \
  -scheme PulseSoc \
  -configuration Release \
  -destination "id=${DEVICE_ID}" \
  -derivedDataPath "$DERIVED" \
  -allowProvisioningUpdates \
  build

APP="$DERIVED/Build/Products/Release-iphoneos/PulseSoc.app"
echo "=== [device] built: $APP ==="
# Absolute path deliberately: a relative .app path has previously produced a
# "build produced nothing" install error that was really a wrong cwd.
test -d "$APP" || { echo "MISSING APP BUNDLE at $APP"; exit 1; }

echo "=== [device] entitlements ==="
codesign -d --entitlements :- "$APP" 2>/dev/null | head -40 || true

echo "=== [device] installing to P3r7or ==="
xcrun devicectl device install app --device "$DEVICE_ID" "$APP"

echo "=== [device] DONE ==="
