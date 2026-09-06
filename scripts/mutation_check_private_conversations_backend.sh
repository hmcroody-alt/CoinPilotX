#!/bin/bash
# Stage 124 — anti-vacuity harness, server side.
#
# The mobile battery
# (`mobile-native/scripts/mutation-check-private-conversations.sh`) proves the
# client does not turn a refusal into an empty list. That is a claim about
# honesty. This one is a claim about *access*: the reverse-link route answers
# "which conversations discuss this object", and the only thing standing
# between a stranger and that answer is a membership intersection they cannot
# see. A test that would still pass with the intersection removed proves
# nothing, so each mutant below removes exactly one part of it.
#
# Product files are restored from a byte-for-byte backup after every run.

set -u
cd "$(dirname "$0")/.." || exit 1

PY="${PY:-/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python3}"
SERVICE=services/private_office/conversations.py
ROUTES=services/private_office_conversations_routes.py
SUITE=tests/private_office/test_private_conversations_routes.py

if [ ! -x "$PY" ]; then
  echo "HARNESS ERROR — no interpreter at $PY (set PY=... to override)"
  exit 1
fi

cp "$SERVICE" /tmp/mut_po_service.bak
cp "$ROUTES" /tmp/mut_po_routes.bak
restore() {
  cp /tmp/mut_po_service.bak "$SERVICE"
  cp /tmp/mut_po_routes.bak "$ROUTES"
}
trap restore EXIT

pass=0
fail=0

# run <name> — expects the mutation to already be applied. Kills = good.
#
# The applied-at-all check comes first for the same reason it does in the mobile
# battery: a pattern that silently matched nothing is indistinguishable from a
# surviving mutant, and would send someone rewriting a test that was fine.
run() {
  local name="$1"
  local out
  if cmp -s "$SERVICE" /tmp/mut_po_service.bak && cmp -s "$ROUTES" /tmp/mut_po_routes.bak; then
    echo "  HARNESS ERROR    — $name   <<< mutation did not apply, result meaningless"
    fail=$((fail+1))
    return
  fi
  out=$("$PY" -m pytest "$SUITE" -q 2>&1)
  if echo "$out" | grep -qE "[0-9]+ (failed|error)"; then
    local n
    n=$(echo "$out" | grep -oE "[0-9]+ (failed|error)" | head -1)
    echo "  KILLED  ($n) — $name"
    pass=$((pass+1))
  else
    echo "  SURVIVED         — $name   <<< VACUOUS TEST"
    fail=$((fail+1))
  fi
  restore
}

echo "=== Stage 124 mutation battery (backend) ==="

# 1. THE leak. The route answers straight out of the link table instead of
#    intersecting with the member's own visible threads, so anyone entitled and
#    unlocked learns the count — and then the titles — of conversations they
#    were never part of. This is the mutant the whole route is shaped to
#    prevent, and `test_a_stranger_learns_nothing_about_a_document_linked_
#    elsewhere` exists for exactly this.
perl -0pi -e 's/    def keep\(conversation_id: int, _row: dict\) -> bool:\n        return conversation_id in linked/    def keep(conversation_id: int, _row: dict) -> bool:\n        return True/' "$SERVICE"
run "reverse lookup answers from the link table, not the member's threads"

# 2. The narrowing predicate is inverted rather than removed. A subtler shape of
#    the same bug: every thread except the linked ones comes back.
perl -0pi -e 's/        return conversation_id in linked/        return conversation_id not in linked/' "$SERVICE"
run "reverse lookup returns the threads that do NOT reference the object"

# 3. Link types stop being validated, so an unknown kind is no longer a 400 and
#    instead reads the link table with an arbitrary string.
#
#    Both call sites go at once, deliberately. `list_for_target` cleans the type
#    for its audit id and `conversations_for_target` cleans it again before the
#    query, so each one alone is an *equivalent* mutant: remove either and the
#    other still returns 400, and the run reports a vacuous test that is in fact
#    fine. Two earlier drafts of this battery made exactly that mistake in both
#    directions. The claim under test is "an unknown link type is refused", not
#    "this particular line is the one that refuses it", so the mutant has to
#    remove the property rather than one expression of it.
perl -0pi -e 's/    kind = _clean_link_type\(link_type\)/    kind = str(link_type)/g' "$SERVICE"
run "an unknown link type is accepted instead of refused"

# 4. The target-id shape check goes away. `<path:target_id>` is a permissive
#    converter on purpose — record ids contain slashes — and this is the mutant
#    that proves the permissive converter did not become a permissive validator.
perl -0pi -e 's/    target = _clean_target_id\(target_id\)/    target = str(target_id)/' "$SERVICE"
run "the target id shape check is skipped"

# 5. The route drops the gate chain, so an unauthenticated or unentitled caller
#    reaches the read. The gate is shared with every other route on this
#    surface; this pins that the new route actually stands behind it.
perl -0pi -e 's/(def api_private_office_conversations_for_target\(link_type, target_id\):\n(?:.|\n)*?    user, refusal = _entry\(\)\n)    if refusal:\n        return refusal/$1    refusal = None/' "$ROUTES"
run "the reverse-lookup route skips the auth, tier and lock gates"

# 6. A failed read reports conversations anyway. `_unavailable` is replaced with
#    a 200 carrying an empty list — the server-side spelling of the same lie the
#    mobile battery hunts in mutants 4 and 11.
perl -0pi -e 's/        LOGGER\.exception\("PRIVATE_CONVERSATIONS_FOR_TARGET_FAILED"\)\n        return _unavailable\((?:.|\n)*?\)/        LOGGER.exception("PRIVATE_CONVERSATIONS_FOR_TARGET_FAILED")\n        return po_http._no_store({"ok": True, "conversations": [], "count": 0})/' "$ROUTES"
run "a failed reverse lookup reports zero linked conversations"

echo
echo "killed=$pass survived=$fail"
[ "$fail" -eq 0 ] || exit 1
