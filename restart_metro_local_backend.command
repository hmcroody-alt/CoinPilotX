#!/bin/zsh
#
# Restart Metro so the simulator app talks to the LOCAL backend on 127.0.0.1:5050.
#
# Double-click this file in Finder. Leave the window open for the whole session —
# closing it stops Metro, and a development client with no Metro has no JavaScript.
#
# Why this exists, and it is the most expensive thing learned this session:
#
# The installed "PulseSoc Dev" app has no main.jsbundle. It is a development
# client, so its JavaScript is fetched from Metro at launch. That makes
# EXPO_PUBLIC_PULSE_API_BASE_URL not a property of the build but a property of
# the shell that started Metro, frozen into the bundle Metro serves.
#
# mobile-native/src/api/config.ts falls back to "https://pulsesoc.com" when that
# variable is unset. Metro had been started without it. Dumping the served bundle
# showed ten occurrences of https://pulsesoc.com and not one of 127.0.0.1:5050 —
# so the app was talking to production, and every local flag change, every backend
# restart and every /health/undx probe was aimed at a server the app had never
# once contacted. The "UNDX is currently read-only" reply on screen was produced
# by a server on the internet.
#
# Nothing about that is visible from either end. The local server logs nothing,
# because no connection arrives. The app shows a normal-looking reply, because it
# got a real answer from a real server. The only way to see it is to ask Metro for
# the bundle and read the URL out of it, which is what
# diagnose_app_backend_link.command does.
#
# Port 8082, not the default 8081, because that is the port the installed dev
# client is already configured to load from. Changing it here would leave the app
# pointing at a Metro that is no longer there.

set -e
cd "${0:A:h}/mobile-native"

METRO_PORT=8082
export EXPO_PUBLIC_PULSE_API_BASE_URL="http://127.0.0.1:5050"

# Auto-login, because pointing the app at the local backend invalidates the
# session it was holding: that cookie was issued by pulsesoc.com and means nothing
# here, so the app lands on the sign-in screen. The two accounts already in
# UNDX_AGENT_QA_USER_IDS are temporary QA registrations whose passwords were
# random strings generated once at signup and never stored anywhere recoverable,
# so signing back in as them is not possible.
#
# LoginScreen calls createQaSimulatorLocalSession() when both of these are "1",
# which POSTs /api/mobile/auth/register with a freshly generated username and
# password and keeps the resulting cookie. Nobody has to type a credential, and
# none is written to disk. Both flags are additionally gated in
# qaTemporaryAccount.ts on the API base URL being loopback, so this cannot take
# effect against a real deployment even if the variables leak into another shell.
#
# The new account will not be in the UNDX cohort — that is expected. Read its
# user_id out of the users table afterwards and add it to UNDX_AGENT_QA_USER_IDS.
export EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN="1"
export EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT="1"

print "=============================================="
print " restart_metro_local_backend"
print "=============================================="
print "EXPO_PUBLIC_PULSE_API_BASE_URL=${EXPO_PUBLIC_PULSE_API_BASE_URL}"
print "EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN=${EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN}"
print "EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT=${EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT}"
print "Metro port: ${METRO_PORT}"
print ""

# Refuse to start if the backend that value points at is not actually there. The
# whole failure being fixed here is an app pointed at the wrong server, and
# pointing it at a right-but-absent one would just trade a wrong answer for a
# hang. Exit 7 is curl's "failed to connect".
curl -s --max-time 3 -o /dev/null http://127.0.0.1:5050/health/undx
if [[ $? -eq 7 ]]; then
  print -u2 "restart_metro_local_backend: nothing is listening on 127.0.0.1:5050."
  print -u2 "Start the backend first (restart_undx_live_backend.command), then run this."
  exit 1
fi
print "Backend on 5050 answers:"
curl -s --max-time 3 http://127.0.0.1:5050/health/undx
print ""
print ""

print "Stopping any Metro already on ${METRO_PORT}..."
# Match both the npm wrapper and the node process it spawns. Killing only one
# leaves the other holding the port, and the restart then fails with EADDRINUSE
# after the old bundle server has already stopped serving — the worst of both.
metro_pids=$( { lsof -ti tcp:${METRO_PORT} 2>/dev/null; pgrep -f "[e]xpo start.*--port ${METRO_PORT}" 2>/dev/null; } | sort -u )
if [[ -n "$metro_pids" ]]; then
  print "  killing: ${metro_pids//$'\n'/ }"
  print "$metro_pids" | while read -r pid; do
    [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 3
  metro_pids=$(lsof -ti tcp:${METRO_PORT} 2>/dev/null || true)
  if [[ -n "$metro_pids" ]]; then
    print "  still up, sending KILL: ${metro_pids//$'\n'/ }"
    print "$metro_pids" | while read -r pid; do kill -KILL "$pid" 2>/dev/null || true; done
    sleep 2
  fi
else
  print "  nothing matched"
fi
print ""

print "=============================================="
print "Starting Metro. When it is ready:"
print "  1. In the simulator, shake the device (Device > Shake) or press Cmd-Ctrl-Z"
print "  2. Choose Reload"
print "  3. Watch coinpilotx.log — app traffic should appear within seconds"
print "=============================================="
print ""

# --clear, because the transform cache keys on file contents, not on the value of
# an environment variable that was inlined into the output. Without it Metro
# happily re-serves the cached bundle that still says https://pulsesoc.com, and
# the fix silently does nothing.
exec npx expo start --dev-client --port ${METRO_PORT} --clear
