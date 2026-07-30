#!/bin/zsh
#
# Answer one question: what address is the installed "PulseSoc Dev" app talking to,
# and is anything listening there?
#
# Double-click this file in Finder. It writes its findings to
# reports/app_backend_link_diagnosis.txt as well as printing them, because the
# assistant's sandbox can read that file but cannot see this window.
#
# Why this exists: after a clean backend restart the server logged its own health
# probe and then nothing at all — no polling, no POST — while the app sat on
# "UNDX reconnecting". Force-quitting and relaunching the app changed nothing.
# Zero server-side log lines is the signature of a connection that never arrived,
# which means either the app is dialling a different address or nothing is
# listening on the one it dials. Both are invisible from the server's log, so
# they have to be asked about directly.
#
# mobile-native/src/api/config.ts defaults EXPO_PUBLIC_PULSE_API_BASE_URL to
# https://pulsesoc.com. If the simulator build was made without that variable set,
# the app has been talking to production this whole time and no amount of local
# backend restarting will ever reach it. That is the first hypothesis this checks.

set +e
cd "${0:A:h}"

BUNDLE_ID="com.pulsesoc.nativeapp.dev"
OUT="reports/app_backend_link_diagnosis.txt"
mkdir -p reports

exec > >(tee "$OUT") 2>&1

print "=============================================="
print " diagnose_app_backend_link   $(date '+%Y-%m-%d %H:%M:%S')"
print "=============================================="
print ""

# `print -- ` on every one of these headings, not `print `. zsh's print parses a
# leading `---` as an option and aborts with "bad option: -", which under `set -e`
# kills the whole script on its first heading. bash -n does not catch it, because
# bash's print is a different builtin entirely — the same class of shell-dialect
# trap as the BSD-xargs `-r` bug in restart_undx_live_backend.command.
print -- "--- 1. Is anything answering 127.0.0.1:5050? ---"
health=$(curl -s --max-time 3 http://127.0.0.1:5050/health/undx 2>&1)
curl_status=$?
print -- "curl exit: ${curl_status}  (7 = nothing listening, 0 = answered)"
print -- "body: ${health:-(empty)}"
print ""

print -- "--- 2. Who holds port 5050? ---"
lsof -i tcp:5050 2>/dev/null || print "  (lsof reported nothing)"
print "Processes matching bot.py:"
pgrep -fl "[p]ython.*bot\.py" || print "  (none)"
print ""

print -- "--- 3. Is a Metro/Expo dev server running? ---"
# A development client with no Metro cannot pick up a rebuilt bundle, and the
# EXPO_PUBLIC_* values baked into whatever bundle it did load are whatever the
# shell that started Metro had — not what is in the repo now.
pgrep -fl "[e]xpo start|[m]etro" || print "  (no Metro process)"
lsof -i tcp:8081 2>/dev/null || print "  (nothing on 8081)"
print ""

print -- "--- 4. What base URL is baked into the installed app? ---"
device=$(xcrun simctl list devices booted | sed -n 's/.*(\([0-9A-F-]\{36\}\)) (Booted).*/\1/p' | head -1)
print "Booted device: ${device:-none}"
if [[ -n "$device" ]]; then
  container=$(xcrun simctl get_app_container "$device" "$BUNDLE_ID" 2>/dev/null || true)
  print "App container: ${container:-not installed}"
  if [[ -n "$container" && -d "$container" ]]; then
    bundle=$(/usr/bin/find "$container" -name "main.jsbundle" -maxdepth 3 2>/dev/null | head -1)
    print "JS bundle:     ${bundle:-none found (development client — JS comes from Metro)}"
    if [[ -n "$bundle" ]]; then
      print "Base-URL-looking strings inside the bundle:"
      strings "$bundle" 2>/dev/null \
        | grep -Eo 'https?://[a-zA-Z0-9._:-]+' \
        | grep -Ei 'pulsesoc|127\.0\.0\.1|localhost|:5050' \
        | sort | uniq -c | sort -rn | head -20
    fi
    print ""
    print "Info.plist bundle id / name:"
    /usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$container/Info.plist" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Print :CFBundleDisplayName" "$container/Info.plist" 2>/dev/null || true
  fi
fi
print ""

print -- "--- 5. What base URL did Metro bake into the bundle the app is running? ---"
# This is the question that matters. The installed app has no main.jsbundle: it is
# a development client, so its JavaScript is fetched from Metro at run time. That
# means PULSE_API_BASE_URL is not a property of the build — it is whatever
# EXPO_PUBLIC_PULSE_API_BASE_URL was set to in the shell that started Metro, frozen
# into the bundle Metro serves.
#
# config.ts falls back to "https://pulsesoc.com" when that variable is unset. If
# that is what comes back here, the app has been talking to production for the
# whole session, every local flag change has been irrelevant, and the read-only
# refusal on screen was produced by a server on the internet rather than the one
# on this Mac.
#
# Asking Metro for the bundle over HTTP is the only way to see the value the app
# actually received. Reading .env or app.config.js would only show what a *new*
# bundle would get.
metro_port=$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk '/node/ {print $9}' | sed 's/.*://' | sort -u | head -1)
print "Metro listening port: ${metro_port:-unknown}"
if [[ -n "$metro_port" ]]; then
  bundle_url="http://127.0.0.1:${metro_port}/index.bundle?platform=ios&dev=true&minify=false"
  print "Fetching ${bundle_url}"
  tmp=$(mktemp)
  if curl -s --max-time 120 -o "$tmp" "$bundle_url"; then
    print "Bundle bytes: $(wc -c < "$tmp")"
    print "PULSE_API_BASE_URL assignment(s) found in the served bundle:"
    grep -Eo 'PULSE_API_BASE_URL[^,;]{0,120}' "$tmp" | sort -u | head -10
    print "Candidate base URLs in the served bundle:"
    grep -Eo 'https?://[a-zA-Z0-9._:-]+' "$tmp" \
      | grep -Ei 'pulsesoc|127\.0\.0\.1|localhost' \
      | sort | uniq -c | sort -rn | head -15
    print ""
    # The decisive value. Expo compiles EXPO_PUBLIC_* into a literal object in the
    # bundle (`_expoVirtualEnv.env`), so the string below is the exact value the
    # running app reads — not a default, not a fallback, not what a rebuild would
    # produce. If EXPO_PUBLIC_PULSE_API_BASE_URL is absent from this list, the
    # `|| "https://pulsesoc.com"` fallback in config.ts is what took effect.
    print "EXPO_PUBLIC_* values actually compiled into the bundle:"
    grep -Eo 'EXPO_PUBLIC_[A-Z0-9_]+["'"'"']?[[:space:]]*:[[:space:]]*["'"'"'][^"'"'"']*["'"'"']' "$tmp" \
      | sort -u | head -30
    print ""
    print "Occurrences of a local backend port in the bundle (5050/5051):"
    grep -Eo 'https?://[0-9a-z.]+:505[01]' "$tmp" | sort | uniq -c || print "  (none)"
  else
    print "  (bundle fetch failed)"
  fi
  rm -f "$tmp"
fi
print ""

print "=============================================="
print "Written to ${OUT}"
print "=============================================="
