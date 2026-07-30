#!/bin/zsh
#
# Bring up the local PulseSoc backend for a live UNDX simulator demonstration.
#
# Double-click this file in Finder. macOS opens it in Terminal and runs it; there is
# nothing to type. Leave the window open for the whole session — closing it stops the
# server and the simulator goes back to "Can't reach PulseSoc".
#
# Why this file exists: the agent driving these sessions can control the Simulator and
# can click Xcode's Run button, but it is not permitted to type into a terminal. The
# one thing it cannot do for itself is start this process, so this reduces that step to
# a double-click.
#
# What it does, in order: move to the repo, load .env.local (APP_BASE_URL=127.0.0.1:5050,
# SESSION_COOKIE_SECURE=0, and the UNDX cohort gates), enable agent writes for this run
# only, activate the virtualenv, and start the server on port 5050.

set -e
cd "${0:A:h}"

if [[ ! -f .env.local ]]; then
  print -u2 "start_undx_live_backend: .env.local not found in $PWD"
  exit 1
fi

set -a
source .env.local
set +a

# .env.local deliberately omits this: the standing local default is reads-only, so that
# an accidental run cannot mutate anything. A confirmation-card demonstration needs it,
# because with writes off "pause my bitcoin alert" returns permission_denied and never
# reaches the card the demo is about to inspect.
#
# Set to 0 to run the session read-only instead. Exported here rather than written into
# .env.local so the permissive setting lasts exactly as long as this window does.
export UNDX_AGENT_WRITES_ENABLED=1

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
else
  print -u2 "start_undx_live_backend: .venv not found in $PWD"
  exit 1
fi

print "PulseSoc backend starting on http://127.0.0.1:5050"
print "UNDX_AGENT_ENABLED=${UNDX_AGENT_ENABLED:-unset}  READS=${UNDX_AGENT_READS_ENABLED:-unset}  WRITES=${UNDX_AGENT_WRITES_ENABLED:-unset}"
print "QA user ids: ${UNDX_AGENT_QA_USER_IDS:-unset}"
print "Leave this window open. Ctrl-C stops the server."
print ""

exec python bot.py
