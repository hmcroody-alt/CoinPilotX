#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z "${1:-}" ]]; then
  echo "Usage: $0 <CoreDevice identifier>" >&2
  exit 64
fi

# PULSESOC_APS_ENVIRONMENT below states the intent of this build. The Release
# configuration declares "production" so that a store build gets the entitlement it
# must have, and PulseSoc.entitlements reads the build setting rather than a literal;
# this script builds Release but signs for development, so it passes "development"
# back.
#
# Measured, because the obvious assumption is wrong: with CODE_SIGN_STYLE=Automatic
# Xcode rewrites aps-environment from the *provisioning profile* and ignores whatever
# the entitlements file resolved to. A Release build here produced an .xcent reading
# "development" with and without this override. So the override does not change the
# local product - it keeps the command honest about what is being built, and it is
# what makes this script correct under manual signing, where the entitlements file is
# authoritative and a mismatch does fail.
DEVICE_ID="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE_DIR="$ROOT_DIR/mobile-native"
DERIVED_DATA_PATH="${PULSESOC_DERIVED_DATA_PATH:-/tmp/pulsesoc-native-device-release}"
DEVELOPMENT_BUNDLE_ID="com.pulsesoc.nativeapp.dev"
DEVELOPMENT_DISPLAY_NAME="PulseSoc Native Dev"
PRODUCTION_BUNDLE_ID="com.pulsesoc.app"

if [[ "$DEVELOPMENT_BUNDLE_ID" == "$PRODUCTION_BUNDLE_ID" ]]; then
  echo "Refusing to build with the production App Store bundle identifier." >&2
  exit 65
fi

cd "$NATIVE_DIR"

env \
  -u EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN \
  -u EXPO_PUBLIC_PULSESOC_QA_START_ROUTE \
  -u EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES \
  -u EXPO_PUBLIC_PULSESOC_QA_REELS_FIXTURES \
  -u EXPO_PUBLIC_PULSESOC_QA_STATUS_FIXTURES \
  -u EXPO_PUBLIC_PULSESOC_QA_CHAT_STATE \
  -u EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER \
  -u EXPO_PUBLIC_PULSESOC_QA_REELS_STATE \
  EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com \
  xcodebuild \
    -workspace ios/PulseSoc.xcworkspace \
    -scheme PulseSoc \
    -configuration Release \
    -destination "id=$DEVICE_ID" \
    -derivedDataPath "$DERIVED_DATA_PATH" \
    PRODUCT_BUNDLE_IDENTIFIER="$DEVELOPMENT_BUNDLE_ID" \
    PULSESOC_DISPLAY_NAME="$DEVELOPMENT_DISPLAY_NAME" \
    PULSESOC_APS_ENVIRONMENT=development \
    CODE_SIGN_IDENTITY="Apple Development" \
    ARCHS=arm64 \
    ONLY_ACTIVE_ARCH=YES \
    -allowProvisioningUpdates \
    build

APP_PATH="$DERIVED_DATA_PATH/Build/Products/Release-iphoneos/PulseSoc.app"
INFO_PLIST="$APP_PATH/Info.plist"

if [[ ! -s "$APP_PATH/main.jsbundle" ]]; then
  echo "Refusing to install: the standalone JavaScript bundle is missing." >&2
  exit 66
fi

BUILT_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INFO_PLIST")"
BUILT_DISPLAY_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleDisplayName' "$INFO_PLIST")"

if [[ "$BUILT_BUNDLE_ID" != "$DEVELOPMENT_BUNDLE_ID" || "$BUILT_DISPLAY_NAME" != "$DEVELOPMENT_DISPLAY_NAME" ]]; then
  echo "Refusing to install: development identity verification failed." >&2
  exit 67
fi

xcrun devicectl device install app --device "$DEVICE_ID" "$APP_PATH"
xcrun devicectl device process launch --device "$DEVICE_ID" "$DEVELOPMENT_BUNDLE_ID"

echo "Installed and launched $DEVELOPMENT_DISPLAY_NAME ($DEVELOPMENT_BUNDLE_ID)."
echo "Production bundle $PRODUCTION_BUNDLE_ID was not targeted."
echo
echo "NOTE: this build cannot receive a VoIP push, and that is expected."
echo "  A PushKit token is minted for the bundle id, and the sender addresses devices"
echo "  as <bundle>.voip derived from APNS_BUNDLE_ID ($PRODUCTION_BUNDLE_ID). A token"
echo "  from $DEVELOPMENT_BUNDLE_ID therefore draws DeviceTokenNotForTopic, which is"
echo "  revoked rather than retried - unlike a wrong APNs host, it is not self-healing."
echo "  So incoming calls will not ring here. Verify ringing on a $PRODUCTION_BUNDLE_ID"
echo "  build; use this one for everything else. Making this bundle ring would need the"
echo "  App ID registered with Push and listed in APNS_ALLOWED_BUNDLE_IDS *before* the"
echo "  first call, not after."
