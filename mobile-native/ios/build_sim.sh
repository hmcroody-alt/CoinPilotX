#!/bin/bash
# Simulator build + install for the booted iPhone 17 Pro Max.
#
# Must never run at the same time as build_device.sh. ReactCodegen generates
# into the shared source tree rather than into derived data, so a separate
# -derivedDataPath does NOT isolate two concurrent builds.
#
# THE RE-SIGN LOOP IS LOad-BEARING. Building with CODE_SIGNING_ALLOWED=NO
# produces an .app that builds and installs cleanly and then dies at launch:
# dyld refuses the prebuilt Agora frameworks, which ship unsigned. The failure
# reads like a missing library but is a signature, so every framework is
# re-signed ad-hoc below, then the .app itself.
set -euo pipefail

cd "$(dirname "$0")"

SIM_ID="E859950D-B187-4897-B389-05447C5AD796"   # iPhone 17 Pro Max
DERIVED="$HOME/Library/Developer/Xcode/DerivedData/pulsesoc-sim-build"

echo "=== [sim] xcodebuild Release ==="
xcodebuild \
  -workspace PulseSoc.xcworkspace \
  -scheme PulseSoc \
  -configuration Release \
  -destination "id=${SIM_ID}" \
  -derivedDataPath "$DERIVED" \
  CODE_SIGNING_ALLOWED=NO \
  build

APP="$DERIVED/Build/Products/Release-iphonesimulator/PulseSoc.app"
echo "=== [sim] built: $APP ==="
test -d "$APP" || { echo "MISSING APP BUNDLE at $APP"; exit 1; }

echo "=== [sim] re-signing frameworks (dyld rejects the unsigned Agora set) ==="
count=0
if [ -d "$APP/Frameworks" ]; then
  for fw in "$APP"/Frameworks/*.framework; do
    [ -e "$fw" ] || continue
    codesign --force --sign - --timestamp=none "$fw"
    count=$((count + 1))
  done
fi
echo "=== [sim] re-signed $count framework(s) ==="
codesign --force --sign - --timestamp=none "$APP"

echo "=== [sim] verifying every framework carries a signature ==="
bad=0
for fw in "$APP"/Frameworks/*.framework; do
  [ -e "$fw" ] || continue
  codesign -v "$fw" 2>/dev/null || { echo "UNSIGNED: $fw"; bad=$((bad + 1)); }
done
test "$bad" -eq 0 || { echo "$bad framework(s) failed verification"; exit 1; }
echo "=== [sim] all $count frameworks verified signed ==="

echo "=== [sim] installing ==="
xcrun simctl boot "$SIM_ID" 2>/dev/null || true
xcrun simctl install "$SIM_ID" "$APP"

echo "=== [sim] DONE ==="
