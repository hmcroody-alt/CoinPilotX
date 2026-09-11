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

# WHICH BACKEND THIS BUILD WILL TALK TO -- say it out loud, then prove it.
#
# build_sim.sh grew this guard; this script did not, and the gap was not
# hypothetical. P3r7or spent two days carrying a build that targeted
# https://pulsesoc.com while the simulator beside it targeted staging, and
# nothing about the phone said so -- same name, same icon, same screens. The
# phone is the worse place for this to happen, because the phone is where
# supplier flows get exercised by hand, and those write.
#
# This script carries no API base of its own, so running it bare bakes in the
# production fallback from src/api/config.ts, which says of itself that "every
# way this resolution can fail lands on production".
TARGET_BASE="${EXPO_PUBLIC_PULSE_API_BASE_URL:-https://pulsesoc.com}"
echo "=== [device] backend for this build: $TARGET_BASE ==="
echo "=== [device] declared environment: ${EXPO_PUBLIC_PULSE_ENVIRONMENT:-(undeclared)} ==="

# A base URL set without a declared environment is a half-expressed wish:
# config.ts's mismatch guard only binds once an environment has been claimed, so
# an un-inlined or typo'd URL falls back to production with nothing to catch it.
# Refuse rather than build that.
if [ -n "${EXPO_PUBLIC_PULSE_API_BASE_URL:-}" ] && [ -z "${EXPO_PUBLIC_PULSE_ENVIRONMENT:-}" ]; then
  echo "REFUSING: EXPO_PUBLIC_PULSE_API_BASE_URL is set but EXPO_PUBLIC_PULSE_ENVIRONMENT is not."
  echo "-- config.ts can only catch a URL/intent mismatch when the intent is stated,"
  echo "-- and every failure path in that resolution lands on production."
  exit 1
fi

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

# Prove the backend above actually reached the artifact.
#
# Presence in the environment is not presence in the bundle: babel-preset-expo
# substitutes `process.env.X` only for a string-literal key, so an inlining
# failure leaves the production fallback in place while the shell still shows
# the variable set. Only the built bytecode settles it, and `strings -a` is
# required -- main.jsbundle is Hermes bytecode, where plain grep finds nothing
# and reports no error for it.
if [ "$TARGET_BASE" != "https://pulsesoc.com" ]; then
  TARGET_HOST="${TARGET_BASE#https://}"
  echo "=== [device] verifying '$TARGET_HOST' is inlined in the Hermes bundle ==="
  # `grep -q` must NOT be used here, and the reason is worth the paragraph.
  # grep -q exits the instant it matches; `strings` is then killed by SIGPIPE
  # (141); `set -o pipefail` promotes that to a failed pipeline; and `if !`
  # inverts it into "not found". So a correct bundle is reported as having
  # fallen back to production. It is a race against whether strings has finished
  # writing, which makes it rare, cache-dependent, and non-reproducible on retry
  # -- the guard failed once here on a cold cache and then matched six times in a
  # row against the identical file. That is the worst possible shape for a safety
  # check: it cries wolf about production, and the natural next move is to delete
  # it. grep -c reads its input to the end, so nothing is ever SIGPIPEd.
  MATCHES="$(strings -a "$APP/main.jsbundle" | grep -c "$TARGET_HOST" || true)"
  if [ "${MATCHES:-0}" -eq 0 ]; then
    echo "BUNDLE DOES NOT CONTAIN '$TARGET_HOST' -- it fell back to production."
    echo "-- The app would look correct and talk to the live site."
    exit 1
  fi
  echo "=== [device] confirmed: bundle targets $TARGET_HOST ==="
fi

echo "=== [device] entitlements ==="
codesign -d --entitlements :- "$APP" 2>/dev/null | head -40 || true

echo "=== [device] installing to P3r7or ==="
xcrun devicectl device install app --device "$DEVICE_ID" "$APP"

echo "=== [device] DONE ==="
