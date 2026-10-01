#!/bin/bash
# Simulator build + install for the booted iPhone 17 Pro Max.
#
# Must never run at the same time as build_device.sh. ReactCodegen generates
# into the shared source tree rather than into derived data, so a separate
# -derivedDataPath does NOT isolate two concurrent builds.
#
# THE RE-SIGN LOOP IS LOAD-BEARING. dyld refuses the prebuilt Agora frameworks,
# which ship unsigned; the failure reads like a missing library but is a
# signature, so every framework is re-signed ad-hoc below, then the .app itself.
#
# CODE SIGNING MUST STAY ENABLED. This build used to pass
# CODE_SIGNING_ALLOWED=NO and re-sign everything ad-hoc afterwards. That
# produces an app that builds, installs, launches and signs in -- and cannot
# use the keychain at all:
#
#   securityd: PulseSoc[...] add Error Code=-34018
#   "Client has neither application-identifier nor keychain-access-groups
#    entitlements"
#
# A simulator binary carries its entitlements in a `__TEXT,__entitlements`
# section that the LINKER embeds, not in the code signature; disabling signing
# skips that step and there is no later chance to add it. (Signing entitlements
# into the signature instead does not work either -- the app then fails to
# spawn with launchd INIT code 163.)
#
# The consequence is silent at every layer above. expo-secure-store swallows
# keychain failures by design -- it deliberately refuses to fall back to
# plaintext against a production backend -- and RN's native NSHTTPCookieStorage
# keeps the session cookie regardless. So the app looks signed in and every
# READ works, while the session envelope is never persisted: no bearer is ever
# attached, the access token is never refreshed, and every authenticated WRITE
# is refused 403 for the life of the install. That is what made CJ Connect
# answer "This device couldn't prove the request came from you" forever.
set -euo pipefail

cd "$(dirname "$0")"

SIM_ID="E859950D-B187-4897-B389-05447C5AD796"   # iPhone 17 Pro Max
DERIVED="$HOME/Library/Developer/Xcode/DerivedData/pulsesoc-sim-build"

# WHICH BACKEND THIS BUILD WILL TALK TO -- say it out loud, then prove it.
#
# This script carries no API base of its own, so running it bare bakes in the
# production fallback from src/api/config.ts. That is a real trap and not a
# hypothetical one: a staging build was replaced by a production one here simply
# by re-running this file without the variable, and nothing about the resulting
# app looked different -- same name, same icon, same screens -- until the store
# it listed turned out to be the live one.
#
# config.ts says the same thing about itself: "every way this resolution can
# fail lands on production". So the target is printed before the build and
# verified in the artifact after it, because the failure is silent by nature.
TARGET_BASE="${EXPO_PUBLIC_PULSE_API_BASE_URL:-https://pulsesoc.com}"
echo "=== [sim] backend for this build: $TARGET_BASE ==="
echo "=== [sim] declared environment: ${EXPO_PUBLIC_PULSE_ENVIRONMENT:-(undeclared)} ==="

# A base URL without a declared environment is the dangerous half-expressed
# wish config.ts warns about: the mismatch guard there only binds once an
# environment has been claimed, so an un-inlined or typo'd URL would fall back
# to production with nothing to catch it. Refuse rather than build that.
if [ -n "${EXPO_PUBLIC_PULSE_API_BASE_URL:-}" ] && [ -z "${EXPO_PUBLIC_PULSE_ENVIRONMENT:-}" ]; then
  echo "REFUSING: EXPO_PUBLIC_PULSE_API_BASE_URL is set but EXPO_PUBLIC_PULSE_ENVIRONMENT is not."
  echo "-- config.ts can only catch a URL/intent mismatch when the intent is stated,"
  echo "-- and every failure path in that resolution lands on production."
  exit 1
fi

# No signing overrides: the project already carries DEVELOPMENT_TEAM
# (87ZC69AGSR), CODE_SIGN_STYLE=Automatic and CODE_SIGN_ENTITLEMENTS, which is
# exactly what build_device.sh relies on. For a simulator destination Xcode
# still signs ad-hoc, but it generates the .xcent and embeds the entitlements
# section, which is the part that matters here.
echo "=== [sim] xcodebuild Release (signed, so entitlements get embedded) ==="
xcodebuild \
  -workspace PulseSoc.xcworkspace \
  -scheme PulseSoc \
  -configuration Release \
  -destination "id=${SIM_ID}" \
  -derivedDataPath "$DERIVED" \
  -allowProvisioningUpdates \
  build

APP="$DERIVED/Build/Products/Release-iphonesimulator/PulseSoc.app"
echo "=== [sim] built: $APP ==="
test -d "$APP" || { echo "MISSING APP BUNDLE at $APP"; exit 1; }

# Prove the backend above actually reached the artifact.
#
# Presence in the environment is not presence in the bundle: babel-preset-expo
# substitutes `process.env.X` only for a string-literal key, so an inlining
# failure leaves the production fallback in place while the shell still shows
# the variable set. Only the built bytecode settles it. `strings -a` is required
# -- main.jsbundle is Hermes bytecode and plain grep finds nothing in it.
if [ "$TARGET_BASE" != "https://pulsesoc.com" ]; then
  TARGET_HOST="${TARGET_BASE#https://}"
  echo "=== [sim] verifying '$TARGET_HOST' is inlined in the Hermes bundle ==="
  # `grep -q` must NOT be used here, and the reason is worth the paragraph.
  # grep -q exits the instant it matches; `strings` is then killed by SIGPIPE
  # (141); `set -o pipefail` promotes that to a failed pipeline; and `if !`
  # inverts it into "not found". So a correct bundle is reported as having
  # fallen back to production. It is a race against whether strings has finished
  # writing, which makes it rare, cache-dependent, and non-reproducible on retry
  # -- the device copy of this guard failed once on a cold cache and then matched
  # six times in a row against the identical file. That is the worst possible
  # shape for a safety check: it cries wolf about production, and the natural
  # next move is to delete it. grep -c reads its input to the end, so nothing is
  # ever SIGPIPEd.
  MATCHES="$(strings -a "$APP/main.jsbundle" | grep -c "$TARGET_HOST" || true)"
  if [ "${MATCHES:-0}" -eq 0 ]; then
    echo "BUNDLE DOES NOT CONTAIN '$TARGET_HOST' -- it fell back to production."
    echo "-- The app would look correct and talk to the live site."
    exit 1
  fi
  echo "=== [sim] confirmed: bundle targets $TARGET_HOST ==="
fi

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

# Re-signing the frameworks invalidates the app's CodeResources seal, so the
# app has to be signed again too. Ad-hoc and WITHOUT --entitlements on purpose:
# the entitlements live in the linked-in __TEXT,__entitlements section, which a
# re-signature does not touch, and passing them to codesign here is what makes
# the app fail to spawn (launchd INIT code 163).
codesign --force --sign - --timestamp=none "$APP"

echo "=== [sim] verifying every framework carries a signature ==="
bad=0
for fw in "$APP"/Frameworks/*.framework; do
  [ -e "$fw" ] || continue
  codesign -v "$fw" 2>/dev/null || { echo "UNSIGNED: $fw"; bad=$((bad + 1)); }
done
test "$bad" -eq 0 || { echo "$bad framework(s) failed verification"; exit 1; }
echo "=== [sim] all $count frameworks verified signed ==="

# Assert a keychain-bearing entitlement is actually embedded in the binary.
# Losing it is silent at every later layer -- the app installs, launches, signs
# in, and reads fine, and only writes fail -- so it has to be caught here or not
# at all.
#
# EITHER key satisfies securityd, which is why this is an OR and not an AND:
#
#   "Client has neither application-identifier nor keychain-access-groups
#    entitlements"
#
# In practice only application-identifier is present. PulseSoc.entitlements does
# not declare keychain-access-groups on the device either -- an app with an
# application-identifier gets a default keychain access group equal to it -- so
# demanding both here would fail a build that is in fact correct.
echo "=== [sim] verifying embedded keychain entitlements ==="
embedded="$(otool -X -s __TEXT __entitlements "$APP/PulseSoc" 2>/dev/null \
  | awk '{$1=""; print}' | xxd -r -p 2>/dev/null || true)"
case "$embedded" in
  *application-identifier*|*keychain-access-groups*) ;;
  *)
    echo "BINARY HAS NEITHER 'application-identifier' NOR 'keychain-access-groups'"
    echo "-- SecureStore will fail -34018, the session envelope will never persist,"
    echo "-- reads will still work and every authenticated write will 403 forever."
    exit 1
    ;;
esac
echo "=== [sim] keychain entitlements embedded ==="

echo "=== [sim] installing ==="
xcrun simctl boot "$SIM_ID" 2>/dev/null || true
xcrun simctl install "$SIM_ID" "$APP"

echo "=== [sim] DONE ==="
