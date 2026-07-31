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
# Two selectors, because one of them missed. `lsof -ti tcp:5050` reported an
# empty list while a server was demonstrably still answering on 127.0.0.1:5050,
# so the kill was skipped, the guard below passed, and the new process bound the
# wildcard address alongside the old one instead of replacing it. Matching the
# command line as well catches the holder that the socket lookup did not.
kill_bot_processes() {
  local signal=$1
  local pids
  pids=$( { lsof -ti tcp:5050 2>/dev/null; pgrep -f "[p]ython.*bot\.py" 2>/dev/null; } \
          | sort -u | grep -v "^$$\$" )
  if [[ -n "$pids" ]]; then
    print "  killing (${signal}): ${pids//$'\n'/ }"
    print "$pids" | while read -r pid; do
      [[ -n "$pid" ]] && kill "-${signal}" "$pid" 2>/dev/null || true
    done
    return 0
  fi
  return 1
}

# Ask the socket, not the process table. `lsof` reports what the kernel will
# tell *this* user about open files; a connection that is accepted proves
# something is serving the port regardless of who owns it or whether lsof saw
# it. This is the check that would have caught the failure the first time.
#
# curl rather than python: this runs before the venv is activated, and the probe
# guarding against a stale server must not itself depend on an interpreter that
# may not be on PATH yet. Exit 7 is specifically "failed to connect", which is
# the only outcome that means the port is genuinely free — a timeout or a 404
# both mean somebody is there.
port_5050_answers() {
  curl -s --max-time 2 -o /dev/null http://127.0.0.1:5050/health
  [[ $? -ne 7 ]]
}

print "Stopping anything already serving port 5050..."
if kill_bot_processes TERM; then sleep 2; else print "  nothing matched"; fi
if kill_bot_processes KILL; then sleep 2; fi

# Refuse to continue if anything still accepts connections. Starting anyway is
# not a harmless no-op: the new process binds 0.0.0.0 with SO_REUSEADDR, prints
# a healthy banner, passes its own self-check, and then serves nothing, because
# the old socket bound to the more specific 127.0.0.1 keeps winning. Everything
# an operator would look at says success while the stale server answers.
if port_5050_answers; then
  print -u2 ""
  print -u2 "restart_undx_live_backend: something is STILL answering on 127.0.0.1:5050"
  print -u2 "after SIGKILL. It identifies itself as:"
  curl -s --max-time 2 http://127.0.0.1:5050/health/undx >&2 || print -u2 "  (no /health/undx response)"
  print -u2 ""
  print -u2 "Holder(s) per lsof (may be empty — that is the bug this guard exists for):"
  lsof -i tcp:5050 >&2 || true
  print -u2 "Processes matching bot.py:"
  pgrep -fl "[p]ython.*bot\.py" >&2 || print -u2 "  (none)"
  print -u2 ""
  print -u2 "Not starting a second server: it would bind the wildcard address,"
  print -u2 "look completely healthy, and serve nothing."
  exit 1
fi
print "Port 5050 is free (verified by connect, not by lsof)."

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
print "Policy module self-check (this shell's view — NOT proof, see below):"
python - <<'PY' || print "  (self-check failed to import; continuing)"
from services import undx_agent_policy as p
f = p.flags()
for k in ("agent_enabled", "reads_enabled", "writes_enabled",
          "writes_kill_switch", "qa_cohort_configured"):
    print(f"  {k:22} {f[k]}")
print(f"  {'writes_available()':22} {p.writes_available()}")
PY
print ""

# Started in the background rather than exec'd, so that the script survives long
# enough to verify its own work. The previous version ended with `exec python
# bot.py` and therefore could never check anything after the server came up —
# its last word on the subject was a banner printed before the server had bound
# a socket, which is exactly when the interesting failure happens.
#
# stdin comes from /dev/null deliberately. A background job that reads from the
# terminal is sent SIGTTIN and stopped by the kernel, and a stopped server is
# the worst possible failure here: it keeps the listening socket, so the port
# still looks taken and the process still looks alive, but it accepts nothing.
# That is indistinguishable from a network problem from the client's side — the
# app just says it is reconnecting, forever.
# Output is teed to a file as well as the terminal. The terminal alone is not
# enough evidence for anything log-shaped: a correlation id is twelve hex
# characters, and reading twelve hex characters off a screenshot and retyping
# them is a transcription, not a proof. `logs/undx_backend.log` can be grepped.
#
# The redirection is written with zsh process substitution rather than a pipe
# for one specific reason: `python bot.py | tee ... &` sets `$!` to the *tee*
# process, and `$!` is the entire basis of the stale-server guard below. That
# guard would then compare the serving pid against a pid that never served
# anything, report MISMATCH on every healthy start, and be turned off by the
# next person to hit it. With `> >(tee -a ...)`, `$!` is still python's pid.
#
# `-u` is not decoration. Python line-buffers stdout when it is a tty and
# *block*-buffers it when it is not, and the redirection above makes it a pipe.
# The first run of this change looked like a hung server: the terminal stopped
# updating mid-boot and the log file sat at 3,991 bytes while the process was
# healthy and serving — the remaining output was sitting in an 8 KB buffer that
# nothing was going to fill until the next burst. For a log-shaped demonstration
# that is worse than no log, because the line you are waiting for arrives after
# you have concluded it never will.
mkdir -p logs
python -u bot.py < /dev/null > >(tee -a logs/undx_backend.log) 2>&1 &
server_pid=$!

# Ask over HTTP, because that is the only question that identifies the process
# actually serving the app. A self-check run inside the process we just spawned
# tells us what that process believes, and the whole failure mode is that the
# process we spawned is not the one answering.
print "Waiting for the server to answer /health/undx..."
health=""
for _ in {1..40}; do
  health=$(curl -s --max-time 2 http://127.0.0.1:5050/health/undx 2>/dev/null) || health=""
  [[ -n "$health" ]] && break
  sleep 1
done

if [[ -z "$health" ]]; then
  print -u2 ""
  print -u2 "restart_undx_live_backend: no answer from /health/undx after 40s."
  print -u2 "The server did not come up. Scroll up for the traceback."
  kill "$server_pid" 2>/dev/null || true
  exit 1
fi

serving_pid=$(print "$health" | sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')
print ""
print "=============================================="
print "Server answered. Reported by the process on the socket:"
print "$health"
print "=============================================="

# The check the old script could not make. If these disagree, a stale server is
# answering and every flag printed above belongs to a process nobody is talking
# to — the failure that looked, for an entire session, like a policy bug.
if [[ -n "$serving_pid" && "$serving_pid" != "$server_pid" ]]; then
  print -u2 ""
  print -u2 "MISMATCH: this script started pid ${server_pid}, but pid ${serving_pid}"
  print -u2 "is answering on port 5050. A stale server survived the kill and is"
  print -u2 "serving the app. Nothing this script configured is in effect."
  print -u2 ""
  print -u2 "Kill it by hand and run this script again:"
  print -u2 "    kill -9 ${serving_pid}"
  kill "$server_pid" 2>/dev/null || true
  exit 1
fi

# A stopped process answers nothing while looking entirely healthy from outside:
# it still owns the port, still appears in ps, and the guard above already
# passed because it answered once before being stopped. macOS marks this state
# "T" in ps. Worth one line to name it, because the symptom on the client is an
# endless "reconnecting" that looks like a network fault rather than a job
# control accident.
proc_state=$(ps -o stat= -p "$server_pid" 2>/dev/null | tr -d ' ')
if [[ "$proc_state" == T* ]]; then
  print -u2 ""
  print -u2 "restart_undx_live_backend: server pid ${server_pid} is STOPPED (ps stat=${proc_state})."
  print -u2 "It holds the port but will accept nothing. Resume it with: kill -CONT ${server_pid}"
  exit 1
fi

if print "$health" | grep -q '"writes_available"[[:space:]]*:[[:space:]]*true'; then
  print "writes_available: TRUE  — confirmation-card demos can proceed."
else
  print -u2 "writes_available: FALSE — the serving process will refuse every write."
  print -u2 "This is the process that decides, so this is the answer that counts."
fi
print ""
print "Leave this window open. Ctrl-C stops the server."

wait "$server_pid"
