#!/bin/zsh
#
# Restart the local PulseSoc backend with UNDX agent writes enabled.
#
# Double-click this file in Finder. It stops whatever is already serving port
# 5050, then starts bot.py again with the same .env.local plus one extra flag.
#
# Why this exists alongside start_undx_live_backend.command: that script assumes
# nothing is listening yet. A live session that began read-only already has a
# server on 5050, and a confirmation-card demonstration cannot proceed against
# it — "pause my btc alert" comes back "UNDX is currently read-only" and the card
# the demo is about to inspect is never drawn. Stopping and restarting is the
# only way to change a process's environment from outside it.
#
# Leave this window open for the whole session. Closing it stops the server.

set -e
cd "${0:A:h}"

if [[ ! -f .env.local ]]; then
  print -u2 "restart_undx_live_backend: .env.local not found in $PWD"
  exit 1
fi

print "=============================================="
print " restart_undx_live_backend  (writes enabled)"
print "=============================================="

# Kill by port rather than by process name: the point is to free the socket, and
# a stale worker holding it would make the restart fail with "address in use"
# after the old server had already stopped answering.
#
# Written without xargs on purpose. The first version of this script used
# ``lsof -ti tcp:5050 | xargs -r kill``, and -r is a GNU extension that BSD
# xargs — the one macOS ships — does not have. With the error swallowed by
# 2>/dev/null the kill silently never happened, the read-only server kept the
# socket, and the whole point of the script was lost while it appeared to work.
kill_port_5050() {
  local signal=$1
  local pids
  pids=$(lsof -ti tcp:5050 2>/dev/null) || pids=""
  if [[ -n "$pids" ]]; then
    print "  killing (${signal}): ${pids//$'\n'/ }"
    print "$pids" | while read -r pid; do
      [[ -n "$pid" ]] && kill "-${signal}" "$pid" 2>/dev/null || true
    done
    return 0
  fi
  return 1
}

print "Stopping anything already listening on port 5050..."
if kill_port_5050 TERM; then sleep 2; else print "  nothing was listening"; fi
if kill_port_5050 KILL; then sleep 2; fi

# Refuse to continue if the socket is still held. Starting anyway would bind-fail
# and leave the OLD read-only server answering, so the demo would run against a
# process that ignores everything this script just set — which is precisely the
# failure that made this rewrite necessary.
if lsof -ti tcp:5050 >/dev/null 2>&1; then
  print -u2 ""
  print -u2 "restart_undx_live_backend: port 5050 is STILL held after SIGKILL."
  print -u2 "Holder(s):"
  lsof -i tcp:5050 >&2 || true
  print -u2 ""
  print -u2 "Not starting a second server: it would fail to bind and the old"
  print -u2 "read-only one would keep answering, which looks like success."
  exit 1
fi
print "Port 5050 is free."

set -a
source .env.local
set +a

# Exported here rather than written into .env.local so the permissive setting
# lasts exactly as long as this window does. Set to 0 to run read-only.
export UNDX_AGENT_WRITES_ENABLED=1

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
else
  print -u2 "restart_undx_live_backend: .venv not found in $PWD"
  exit 1
fi

print ""
print "=============================================="
print "PulseSoc backend starting on http://127.0.0.1:5050"
print "  python:   $(command -v python)"
print "  ENABLED:  ${UNDX_AGENT_ENABLED:-unset}"
print "  READS:    ${UNDX_AGENT_READS_ENABLED:-unset}"
print "  WRITES:   ${UNDX_AGENT_WRITES_ENABLED:-unset}   <-- must be 1 for card demos"
print "  KILLSWCH: ${UNDX_AGENT_DISABLE_WRITES:-unset}   <-- must be unset or 0"
print "  QA users: ${UNDX_AGENT_QA_USER_IDS:-unset}"
print "=============================================="
print "Leave this window open. Ctrl-C stops the server."
print ""

# The kill switch overrides every other write flag, so a stray 1 here would make
# the demo fail with the same sentence the missing WRITES flag produces. Caught
# now, where the cause is visible, rather than in the simulator where it is not.
if [[ "${UNDX_AGENT_DISABLE_WRITES:-0}" == "1" ]]; then
  print -u2 "restart_undx_live_backend: UNDX_AGENT_DISABLE_WRITES=1 overrides writes. Aborting."
  exit 1
fi

# Ask the policy module itself, under this exact environment, rather than trusting
# that the shell's view of the flags is the one the server will act on. Printing
# ${UNDX_AGENT_WRITES_ENABLED} above only proves the shell has it; this proves the
# code that decides reads it as true. The two came apart once already — the first
# version of this script reported WRITES=1 in its banner while the process that
# actually answered the simulator was a different, older one it had failed to kill.
print "Policy module self-check:"
python - <<'PY' || print "  (self-check failed to import; continuing)"
from services import undx_agent_policy as p
f = p.flags()
for k in ("agent_enabled", "reads_enabled", "writes_enabled",
          "writes_kill_switch", "qa_cohort_configured"):
    print(f"  {k:22} {f[k]}")
print(f"  {'writes_available()':22} {p.writes_available()}")
PY
print ""

exec python bot.py
