#!/bin/zsh
#
# Force-quit and relaunch "PulseSoc Dev" in the booted iOS simulator.
#
# Double-click this file in Finder. It closes on its own when done.
#
# Why this exists: restarting the backend breaks every in-flight request the app
# has open, and the client does not always recover. After the 15:01 restart the
# app sat on "UNDX reconnecting. Conversation history remains visible." and sent
# the server nothing at all — not a poll, not a retry — for seven minutes. The
# server log is the proof: the only line after boot was the launcher's own
# /health/undx probe. Tapping "Retry failed send" produced no request either, so
# the failure is in the app's own state, not in the network or the server.
#
# Sending the app to the home screen and reopening it is not enough: that resumes
# the same process with the same broken JS state. The process has to die.
#
# `simctl terminate` on an app that is not running exits non-zero, which is not an
# error condition here, so its failure is tolerated deliberately.

set -e
cd "${0:A:h}"

BUNDLE_ID="com.pulsesoc.nativeapp.dev"

print "=============================================="
print " restart_pulsesoc_dev_app"
print "=============================================="

device=$(xcrun simctl list devices booted | sed -n 's/.*(\([0-9A-F-]\{36\}\)) (Booted).*/\1/p' | head -1)
if [[ -z "$device" ]]; then
  print -u2 "restart_pulsesoc_dev_app: no booted simulator found."
  print -u2 "Boot one from Xcode (iPhone 17 Pro Max) and run this again."
  exit 1
fi
print "Booted device: ${device}"

# Confirm the bundle id is actually installed before killing anything. A typo
# here would otherwise look exactly like a successful restart: terminate fails
# silently, launch fails, and the app on screen is untouched.
if ! xcrun simctl get_app_container "$device" "$BUNDLE_ID" >/dev/null 2>&1; then
  print -u2 "restart_pulsesoc_dev_app: ${BUNDLE_ID} is not installed on ${device}."
  print -u2 "Installed bundle ids containing 'pulsesoc':"
  xcrun simctl listapps "$device" 2>/dev/null | grep -io 'com\.pulsesoc[a-z.]*' | sort -u >&2 || true
  exit 1
fi

print "Terminating ${BUNDLE_ID}..."
xcrun simctl terminate "$device" "$BUNDLE_ID" 2>/dev/null || print "  (was not running)"
sleep 2

print "Launching ${BUNDLE_ID}..."
launch_output=$(xcrun simctl launch "$device" "$BUNDLE_ID" 2>&1) || {
  print -u2 "restart_pulsesoc_dev_app: launch failed:"
  print -u2 "$launch_output"
  exit 1
}
print "  ${launch_output}"

# Report the pid the launch reported, so that a later "the app is stuck again"
# can be checked against a specific process rather than guessed at.
print ""
print "=============================================="
print "Relaunched. Give it a few seconds to reconnect,"
print "then check the server log for app traffic:"
print "    tail -f coinpilotx.log"
print "=============================================="
