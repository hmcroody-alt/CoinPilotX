#!/bin/bash
# Stage 124 — anti-vacuity harness.
#
# Each mutant breaks exactly one product behaviour that the new suites claim to
# guard. A mutant that survives means the test guarding it proves nothing, and
# must be rewritten rather than explained away. Product files are restored from
# a byte-for-byte backup after every run.

set -u
cd "$(dirname "$0")/.." || exit 1

CLIENT=src/api/privateConversations.ts
SCREEN=src/screens/PrivateConversationsScreen.tsx
# Not a file this mission wrote, but one it depends on for a security property:
# the conversation list is only account-safe because the gate wrapped around it
# calls `reconcileOfficeOwner`. Mutant 10 lives here for that reason.
LOCK=src/privateOffice/officeLock.ts
SUITES="src/api/__tests__/privateConversations.test.ts src/screens/__tests__/PrivateConversationsScreen.test.tsx"

cp "$CLIENT" /tmp/mut_client.bak
cp "$SCREEN" /tmp/mut_screen.bak
cp "$LOCK" /tmp/mut_lock.bak
restore() {
  cp /tmp/mut_client.bak "$CLIENT"
  cp /tmp/mut_screen.bak "$SCREEN"
  cp /tmp/mut_lock.bak "$LOCK"
}
trap restore EXIT

pass=0
fail=0

# run <name> — expects the mutation to already be applied. Kills = good.
#
# The first thing it checks is that the mutation applied at all. A pattern that
# silently matched nothing looks exactly like a surviving mutant, and this
# battery produced two such false alarms before the guard existed — which would
# have sent me rewriting perfectly good tests.
run() {
  local name="$1"
  local out
  if cmp -s "$CLIENT" /tmp/mut_client.bak &&
     cmp -s "$SCREEN" /tmp/mut_screen.bak &&
     cmp -s "$LOCK" /tmp/mut_lock.bak; then
    echo "  HARNESS ERROR    — $name   <<< mutation did not apply, result meaningless"
    fail=$((fail+1))
    return
  fi
  out=$(npx jest $SUITES 2>&1)
  if echo "$out" | grep -qE "Tests:.*failed"; then
    local n
    n=$(echo "$out" | grep -oE "[0-9]+ failed" | head -1)
    echo "  KILLED  ($n) — $name"
    pass=$((pass+1))
  else
    echo "  SURVIVED         — $name   <<< VACUOUS TEST"
    fail=$((fail+1))
  fi
  restore
}

echo "=== Stage 124 mutation battery ==="

# 1. Stage 53: strict-true relaxed to truthiness. Survives any suite that only
#    ever feeds boolean false.
perl -0pi -e 's/return value === true;/return Boolean(value);/' "$CLIENT"
run "asStrictTrue -> Boolean(value)"

# 2. Stage 53: strict-true relaxed to not-false. Kills only on absent/null.
perl -0pi -e 's/return value === true;/return value !== false;/' "$CLIENT"
run "asStrictTrue -> value !== false"

# 3. Stage 53 at the render layer: the footnote upgrades on the presence of a
#    note rather than on the server's answer.
perl -0pi -e 's/if \(capabilities\.endToEndEncrypted\) \{/if (capabilities.endToEndEncrypted || capabilities.encryptionNote) {/' "$SCREEN"
run "footnote claims encryption when a note exists"

# 4. Mutual exclusion: empty is gated on the request having finished rather than
#    on it having succeeded. This is the real-world regression.
perl -0pi -e 's/\{ready && ready\.conversations\.length === 0 \?/{result \&\& (!ready || ready.conversations.length === 0) ?/' "$SCREEN"
run "empty panel renders on a failed read"

# 5. Refusal collapses into an empty list in the client. Targeted by line
#    number: `return privateFeatureRefusal(error);` appears in every catch block
#    in this module, and a pattern match silently hits the wrong one — which is
#    how the first run of this battery reported a false vacuity against
#    `getPrivateConversationCapabilities` instead of the list.
LIST_CATCH=$(grep -n "return privateFeatureRefusal(error);" "$CLIENT" | awk -F: -v s="$(grep -n 'export async function listPrivateConversations' "$CLIENT" | cut -d: -f1)" '$1 > s {print $1; exit}')
sed -i '' "${LIST_CATCH}s|.*|    return { state: \"READY\", conversations: [], count: 0, capabilities: parsePrivateConversationCapabilities({}) } as PrivateConversationListResult;|" "$CLIENT"
run "list refusal returns an empty READY (line $LIST_CATCH)"

# 5b. The same collapse in the standalone capabilities read.
CAP_CATCH=$(grep -n "return privateFeatureRefusal(error);" "$CLIENT" | awk -F: -v s="$(grep -n 'export async function getPrivateConversationCapabilities' "$CLIENT" | cut -d: -f1)" '$1 > s {print $1; exit}')
sed -i '' "${CAP_CATCH}s|.*|    return { state: \"READY\", capabilities: parsePrivateConversationCapabilities({}) };|" "$CLIENT"
run "capabilities refusal returns an empty READY (line $CAP_CATCH)"

# 6. The list trusts the server's count over what it parsed.
perl -0pi -e 's/      count: conversations\.length,/      count: asCount(body.count),/' "$CLIENT"
run "list reports the server count, not the parsed length"

# 7. No second messenger: rows open an Office-side thread route.
perl -0pi -e 's/                navigation\.navigate\("Chat", \{\n                  conversationId:\n                    summary\.conversation\.conversation_id \|\| summary\.conversation\.id,/                navigation.navigate("PrivateConversationInfo", {\n                  conversationId:\n                    summary.conversation.conversation_id || summary.conversation.id,/' "$SCREEN"
run "row opens a second thread screen instead of Chat"

# 8. Stale rows survive a scope change.
perl -0pi -e 's/    setResult\(null\);\n    load\(\);/    load();/' "$SCREEN"
run "scope change leaves the previous scope's rows on screen"

# 9. A 423 no longer relocks the office.
perl -0pi -e 's/    if \(next\.state === "LOCKED"\) lockOfficeLocally\(\);/    \/* mutant: no relock *\//' "$SCREEN"
run "a 423 read does not relock the office"

# 10. Stage 107: the account-switch boundary stops firing, so a grant minted by
#     one member stays live for whoever signs in next. The screen itself has no
#     defence against this and should not grow one — the assertion under test is
#     that it is wrapped in the gate that does.
perl -0pi -e 's/(export function reconcileOfficeOwner\(currentUserId: number\) \{\n)/$1  return;\n/' "$LOCK"
run "an account switch does not relock the previous member's office"

echo
echo "killed=$pass survived=$fail"
[ "$fail" -eq 0 ] || exit 1
