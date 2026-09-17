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
# One push-capable bundle id, so this script builds the deployment one. It used to
# build com.pulsesoc.nativeapp.dev and refuse the line below, which kept the App Store
# app installed alongside - but that separation cost the thing this script is mostly
# used for. A PushKit token is minted for the bundle id and the sender addresses
# <bundle>.voip, so a token from any other bundle draws DeviceTokenNotForTopic, which
# is revoked rather than retried. A build under the dev id could never ring, which is
# a poor property for the device you test calls on.
BUNDLE_ID="com.pulsesoc.app"
DEVELOPMENT_DISPLAY_NAME="PulseSoc Native Dev"

# Read this before running it. Same bundle id means iOS treats this as an *upgrade*
# of whatever is installed: an App Store or TestFlight PulseSoc on this device is
# replaced, and re-installing the store build later replaces this one back. The
# container survives an upgrade in place, so this is not a data wipe - but it is a
# downgrade to an unsigned-for-distribution build, and the only thing distinguishing
# the two on the home screen afterwards is the display name below.
echo "WARNING: installing $BUNDLE_ID - this replaces any App Store or TestFlight"
echo "  PulseSoc on device $DEVICE_ID. It will appear as \"$DEVELOPMENT_DISPLAY_NAME\"."

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
    PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID" \
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

# The display name is checked as well as the bundle id, and now that both builds share
# a bundle id it is the *only* on-device signal telling this apart from the store app.
# It is also the setting most likely to come back empty: Info.plist expands
# $(PULSESOC_DISPLAY_NAME), and an undefined build setting expands to the empty string
# rather than failing, at which point iOS silently falls back to CFBundleName and the
# home screen looks correct while the check below is the only thing that noticed.
if [[ "$BUILT_BUNDLE_ID" != "$BUNDLE_ID" || "$BUILT_DISPLAY_NAME" != "$DEVELOPMENT_DISPLAY_NAME" ]]; then
  echo "Refusing to install: build identity verification failed." >&2
  echo "  expected $BUNDLE_ID / $DEVELOPMENT_DISPLAY_NAME" >&2
  echo "  built    $BUILT_BUNDLE_ID / $BUILT_DISPLAY_NAME" >&2
  exit 67
fi

xcrun devicectl device install app --device "$DEVICE_ID" "$APP_PATH"
xcrun devicectl device process launch --device "$DEVICE_ID" "$BUNDLE_ID"

echo "Installed and launched $DEVELOPMENT_DISPLAY_NAME ($BUNDLE_ID)."
echo
echo "NOTE: VoIP pushes reach this build, but by way of a correction."
echo "  It is signed for development, so PushKit mints a *sandbox* token, while the"
echo "  deployment leaves APNS_USE_SANDBOX unset and addresses the production host."
echo "  That draws BadDeviceToken - which the sender replays once against the other"
echo "  host and then remembers, so the call connects and the correction is paid once"
echo "  per token rather than once per call. Expect one voip_push_environment_corrected"
echo "  event the first time this device is called. A token both hosts reject is dead;"
echo "  this one is not."
