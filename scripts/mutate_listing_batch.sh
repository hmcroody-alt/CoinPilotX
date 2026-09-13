#!/bin/bash
# Mutation battery for the bulk listing authority.
#
# Companion to mutate_listing_readiness.sh, which covers the verdict engine.
# This one covers the other half: eligibility, the three counts, and the
# idempotency ledger. A mutant that SURVIVES names a rule no test defends.
#
# This harness EDITS THE REAL FILE and restores it from a backup on exit,
# because the module is imported by path and a sandbox copy would not be the
# thing under test. Two guards make that safe to rely on rather than hope about:
# it refuses to start if the source is already dirty (otherwise the restore
# would silently revert your uncommitted work), and it verifies the restored
# file byte-for-byte before exiting non-silently.
set -u
cd "$(dirname "$0")/.."

SRC="services/business_os/marketplace/listing_batch.py"
TESTS="tests/business_os/test_listing_batch.py"

if ! git diff --quiet -- "$SRC"; then
  echo "REFUSING: $SRC has uncommitted changes."
  echo "-- this harness restores from a backup taken now, which would discard them."
  exit 1
fi

BAK="$(mktemp)"
cp "$SRC" "$BAK"
BEFORE="$(shasum -a 256 "$SRC" | awk '{print $1}')"
restore() {
  cp "$BAK" "$SRC"
  local after
  after="$(shasum -a 256 "$SRC" | awk '{print $1}')"
  rm -f "$BAK"
  echo
  if [ "$after" = "$BEFORE" ]; then
    echo "[restored $SRC -- sha matches]"
  else
    echo "[!!] $SRC DID NOT RESTORE CLEANLY -- run: git checkout -- $SRC"
  fi
}
trap restore EXIT

CAUGHT=0
SURVIVED=0
BROKEN=0

# Anchored literal replacement. Fails loudly when the anchor has drifted, so a
# mutant that silently stopped applying cannot be mistaken for a passing one.
mutate() {
  .venv/bin/python3 - "$SRC" "$1" "$2" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
if old not in s:
    sys.exit("ANCHOR NOT FOUND")
open(path, "w").write(s.replace(old, new, 1))
PY
}

run_mutant() {
  local name="$1"; shift
  cp "$BAK" "$SRC"
  if ! "$@"; then
    echo "=== $name: ANCHOR DRIFTED -- not evidence, fix the mutation ==="
    BROKEN=$((BROKEN + 1))
    return
  fi
  if ! .venv/bin/python3 -c "import ast; ast.parse(open('$SRC').read())" 2>/dev/null; then
    echo "=== $name: MALFORMED -- not evidence, fix the mutation ==="
    BROKEN=$((BROKEN + 1))
    return
  fi
  if diff -q "$BAK" "$SRC" >/dev/null; then
    echo "=== $name: NO-OP -- the edit did not apply ==="
    BROKEN=$((BROKEN + 1))
    return
  fi
  local out
  out=$(.venv/bin/python3 -m pytest "$TESTS" -q 2>&1 | tail -25)
  if echo "$out" | grep -qE "[0-9]+ failed|error"; then
    echo "=== $name: CAUGHT ==="
    echo "$out" | grep "^FAILED" | head -4 | sed 's/^/    /'
    CAUGHT=$((CAUGHT + 1))
  else
    echo "=== $name: SURVIVED  <-- no test defends this rule ==="
    echo "$out" | tail -2 | sed 's/^/    /'
    SURVIVED=$((SURVIVED + 1))
  fi
}

# --- eligibility: the rules that decide whether a row moves ------------------

# 1. Publish without a readiness check. "No verdict" starts meaning "yes", which
#    is the §35 mutation: the unexamined row goes live beside the examined ones.
run_mutant "1 absent verdict treated as approval" mutate \
'    if verdict is None:
        return {"code": "NO_READINESS", "reason": "No readiness check yet"}' \
'    if verdict is None:
        return None'

# 2. Bulk publish skips the blocker: an unpublishable verdict stops blocking.
run_mutant "2 unpublishable no longer blocks" mutate \
'    if verdict.get("publishable"):
        return None' \
'    return None
    if verdict.get("publishable"):
        return None'

# 3. Only the first row is actually evaluated; the rest inherit no verdict and,
#    with the NO_READINESS rule intact, this is the "skipped blocker" in its
#    more plausible shape -- a loop that computes the verdict once.
run_mutant "3 the verdict is computed once for the whole page" mutate \
'    lookup = media_by_listing or {}
    decided = []
    for row in rows:
        if action == "publish":
            verdict = _readiness.evaluate(row, media=lookup.get(int(row.get("id") or 0)))
        else:
            verdict = None
        decided.append((row, block_reason(row, action, verdict)))
    return decided' \
'    lookup = media_by_listing or {}
    rows = list(rows)
    verdict = None
    if action == "publish" and rows:
        first = rows[0]
        verdict = _readiness.evaluate(first, media=lookup.get(int(first.get("id") or 0)))
    return [(row, block_reason(row, action, verdict)) for row in rows]'

# 4. Double publish duplicates: a row already in review becomes publishable
#    again, so a second tap files a second review submission.
run_mutant "4 already-in-review republishes" mutate \
'    if status == "pending_review":
        return {"code": "ALREADY_SUBMITTED", "reason": "Already in review"}' \
'    if status == "pending_review":
        return None'

# 5. A live listing is publishable again, which is the select-all footgun: the
#    whole storefront goes back to pending_review and it is counted a success.
run_mutant "5 anything may be published from any state" mutate \
'PUBLISHABLE_FROM = ("", "draft", "changes_requested", "rejected", "paused", "hidden")' \
'PUBLISHABLE_FROM = ("", "draft", "changes_requested", "rejected", "paused", "hidden",
                    "active", "pending_review")'

# 6. publishable=False with no blockers listed starts failing OPEN.
run_mutant "6 a contradictory verdict fails open" mutate \
'    return {"code": "NOT_READY", "reason": "Not ready to publish", "blockers": []}' \
'    return None'

# --- the answer: partial success, and blocked-is-not-failed ------------------

# 7. Partial success becomes full success -- the exact §19/§35 mutation.
run_mutant "7 every row is reported successful" mutate \
'    counts = {SUCCEEDED: 0, BLOCKED: 0, FAILED: 0}
    for entry in results:
        outcome = entry.get("outcome")
        if outcome not in counts:
            raise ValueError(f"unknown outcome {outcome!r}")
        counts[outcome] += 1' \
'    counts = {SUCCEEDED: len(results), BLOCKED: 0, FAILED: 0}'

# 8. Blocked collapses into failed, so the seller gets a number with no task
#    attached to it.
run_mutant "8 blocked is renamed failed" mutate \
'BLOCKED = "blocked"' \
'BLOCKED = "failed"'

# 9. The counts stop being derived from the detail and become a second source of
#    truth that can disagree with it.
run_mutant "9 requested_count stops matching the detail" mutate \
'        "requested_count": len(results),' \
'        "requested_count": counts[SUCCEEDED],'

# --- the ledger: one tap, one batch -----------------------------------------

# 10. Cross-store: the key lookup drops its seller scope, so one seller can be
#     handed another seller's batch -- the §27 ownership mutation on the
#     idempotency path rather than the listing path.
run_mutant "10 the idempotency key is global, not per-seller" mutate \
'        "SELECT batch_id, request_hash, response_json FROM marketplace_listing_batches "
        "WHERE seller_user_id=? AND idempotency_key=? LIMIT 1",
        (seller, key),' \
'        "SELECT batch_id, request_hash, response_json FROM marketplace_listing_batches "
        "WHERE idempotency_key=? LIMIT 1",
        (key,),'

# 11. The fingerprint stops covering the selection, so a recycled key replays
#     the wrong batch and the rows the seller picked never move.
run_mutant "11 the request hash ignores the selection" mutate \
'    payload = json.dumps(
        {"action": action, "listing_ids": sorted(int(i) for i in listing_ids)},
        sort_keys=True,
        separators=(",", ":"),
    )' \
'    payload = json.dumps({"action": action}, sort_keys=True, separators=(",", ":"))'

# 12. An in-flight claim answers "done" instead of "still running", so a retry
#     through a timeout is told the work finished when it has not.
run_mutant "12 an unfinished batch reports itself complete" mutate \
'        if not row["response_json"]:' \
'        if False:'

echo
echo "=== listing_batch mutation battery: caught=$CAUGHT survived=$SURVIVED unusable=$BROKEN ==="
test "$SURVIVED" -eq 0 && test "$BROKEN" -eq 0
