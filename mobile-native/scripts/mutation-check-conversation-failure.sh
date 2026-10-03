#!/bin/bash
# COMMERCE CONVERSATION BRIDGE — anti-vacuity harness for the client half.
#
# The backend half of this mission is scored by its own battery. This one scores
# the screen: every mutant below re-creates one piece of the reported defect —
# "Conversation not found." under a RECONNECTING banner over a live composer —
# and a mutant that survives means the test claiming to guard it proves nothing.
#
# Product files are restored from a byte-for-byte backup after every run.

set -u
cd "$(dirname "$0")/.." || exit 1

# The taxonomy. Mutants 1-7 live here: wrong classification is the upstream
# cause, and it reaches every claim on the screen at once.
RULES=src/messaging/conversationFailure.ts
# The wiring. Mutants 8-13 live here, and none of them touch the taxonomy —
# a perfectly correct classification, read wrongly, still ships the defect.
SCREEN=src/screens/ChatScreen.tsx
SUITES="src/messaging/__tests__/conversationFailure.test.ts src/screens/__tests__/ChatScreenConversationFailure.test.tsx"

cp "$RULES" /tmp/ccf_rules.bak
cp "$SCREEN" /tmp/ccf_screen.bak
restore() {
  cp /tmp/ccf_rules.bak "$RULES"
  cp /tmp/ccf_screen.bak "$SCREEN"
}
trap restore EXIT

pass=0
fail=0

# run <name> — expects the mutation to already be applied. Kills = good.
#
# The first thing it checks is that the mutation applied at all: a pattern that
# silently matched nothing is indistinguishable from a surviving mutant, and
# would send me rewriting tests that were never weak.
run() {
  local name="$1"
  local out
  if cmp -s "$RULES" /tmp/ccf_rules.bak && cmp -s "$SCREEN" /tmp/ccf_screen.bak; then
    echo "  HARNESS ERROR    — $name   <<< mutation did not apply, result meaningless"
    fail=$((fail+1))
    return
  fi
  out=$(npx jest $SUITES 2>&1)
  if echo "$out" | grep -qE "Tests:.*failed|Test suite failed to run"; then
    local n
    n=$(echo "$out" | grep -oE "[0-9]+ failed" | head -1)
    echo "  KILLED  (${n:-suite}) — $name"
    pass=$((pass+1))
  else
    echo "  SURVIVED         — $name   <<< VACUOUS TEST"
    fail=$((fail+1))
  fi
  restore
}

# `run` scores a mutant KILLED whenever anything fails, which is only sound if
# the unmutated tree is green under the same command. Otherwise one pre-existing
# failure signs off on every mutant and the battery becomes a rubber stamp.
echo "=== commerce conversation bridge — client mutation battery ==="
if npx jest $SUITES 2>&1 | grep -qE "Tests:.*failed|Test suite failed to run"; then
  echo "  PREFLIGHT FAILED — the suites are not green at baseline; every mutant"
  echo "                     scored against them would read KILLED regardless."
  exit 1
fi
echo "  baseline green"
echo

# --- the taxonomy ------------------------------------------------------------

# 1. The reported defect at its source: a missing conversation is classified as
#    a server problem, which carries the `retrying` posture and therefore prints
#    "RECONNECTING" over a 404.
perl -0pi -e 's/  if \(status === 404\) return "conversation_not_found";/  if (status === 404) return "server_error";/' "$RULES"
run "404 classified as a server error (restores RECONNECTING)"

# 2. The same claim attacked from the copy table instead of the classifier: the
#    kind stays right and the posture is what lies.
perl -0pi -e 's/  conversation_not_found: "unavailable",/  conversation_not_found: "retrying",/' "$RULES"
run "the missing-conversation posture says retrying"

# 3. The composer gate relaxed for the one kind the buyer actually hit. A send
#    here is accepted by the UI and refused by the server: the words are lost
#    and the person believes the seller was contacted.
perl -0pi -e 's/  conversation_not_found: true,/  conversation_not_found: false,/' "$RULES"
run "a missing conversation no longer blocks sending"

# 4. The gate relaxed for a block. Worse than mutant 3 — it offers to message
#    someone the platform has decided these two may not reach.
perl -0pi -e 's/  messaging_not_allowed: true,/  messaging_not_allowed: false,/' "$RULES"
run "a block no longer blocks sending"

# 5. The composer survives any failure that left history on screen, including
#    the refusals. This is the plausible edit: it reads like the hiccup case.
perl -0pi -e 's/  if \(failure\.blocksSending\) return false;/  \/* mutant: no send gate *\//' "$RULES"
run "composer availability ignores blocksSending"

# 6. Recovery stops re-resolving the pair, so Retry re-asks for the id the
#    server already refused — the loop the buyer was stuck in.
perl -0pi -e 's/    needsResolution: kind === "conversation_not_found"/    needsResolution: false/' "$RULES"
run "no failure needs the conversation re-resolved"

# 7. The classifier trusts `code` alone. The v2 blueprint never sets it (the
#    code arrives inside `details.status`), so a 403 block degrades to a plain
#    lack of access and the person is told the wrong thing about why.
perl -0pi -e 's/  const detail = error\.details && typeof error\.details === "object" \? error\.details\.status : undefined;/  const detail = undefined;/' "$RULES"
run "the v2 code inside details.status is ignored"

# 8. The network/bug split collapses: a client-side TypeError is reported as a
#    connection problem, sending the person to fix something that is fine.
perl -0pi -e 's/  return \/network request failed\|network error\|failed to fetch\|timed out\|timeout\/i\.test\(message\);/  return true;/' "$RULES"
run "any unstatused throw is blamed on the network"

# --- the wiring --------------------------------------------------------------
#
# Mutants 1-8 prove the rules. They cannot prove the screen READS them: a
# correct classification displayed from the old error string ships the identical
# defect, which is precisely what the screen-level suite exists to catch.

# 9. The status line goes back to deriving its own wording from the presence of
#    an error — the single line that printed "RECONNECTING" under a 404.
perl -0pi -e 's/failureCopy \? t\(failureCopy\.state\)/error ? t("messaging:chat.stateReconnecting")/' "$SCREEN"
run "the composer status line re-derives RECONNECTING from the error string"

# 10. The header reverts to its own independent claim, so the panel and the
#     header describe two different problems again.
perl -0pi -e 's/  const headerStatus = failureCopy\n    \? t\(failureCopy\.header\)/  const headerStatus = failure\n    ? t("messaging:chat.headerUnavailable")/' "$SCREEN"
run "the header states its own failure, independent of the panel"

# 11. The composer input is ungated. Every other control may still be disabled —
#     this is the one that takes the keystrokes and loses them.
perl -0pi -e 's/            editable=\{composerAvailable\}/            editable={true}/' "$SCREEN"
run "the composer text input stays editable"

# 12. Retry aims at the wrong subsystem: it re-fetches instead of re-resolving
#     the pair, so the refused id is sent back to a server that has refused it.
perl -0pi -e 's/    const resolvePair = peerUserId > 0 && Boolean\(failure\?\.needsResolution\);/    const resolvePair = false;/' "$SCREEN"
run "retry re-fetches the refused id instead of resolving the pair"

# 13. The repair pushes instead of replacing, leaving the refused conversation
#     on the stack underneath for Back to walk straight back into.
perl -0pi -e 's/          navigation\.replace\("Chat", \{ \.\.\.route\.params, conversationId: resolved \}\);/          navigation.navigate("Chat", { ...route.params, conversationId: resolved });/' "$SCREEN"
run "the repaired conversation is pushed over the refused one"

echo
echo "killed=$pass survived=$fail"
[ "$fail" -eq 0 ] || exit 1
