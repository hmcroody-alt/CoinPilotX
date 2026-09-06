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
# The reverse-link panel. Shared by the documents, records, facts and meetings
# screens, so a mutant that survives here is a false claim on four surfaces at
# once — which is exactly why the panel is one component and not four copies.
LINKED=src/privateOffice/LinkedConversations.tsx
# The two hosts that adopted the panel. The panel itself is proven by mutants
# 11-15; these files carry the WIRING — which link type, which target id, and
# (on meetings) which row's disclosure — none of which the panel can defend.
FACTS=src/screens/PrivateFactsScreen.tsx
MEETINGS=src/screens/PrivateMeetingsScreen.tsx
SUITES="src/api/__tests__/privateConversations.test.ts src/screens/__tests__/PrivateConversationsScreen.test.tsx src/privateOffice/__tests__/LinkedConversations.test.tsx"

# Host suites are run under narrower commands, deliberately.
#
# `PrivateFactsScreen.test.tsx` carries ONE pre-existing failure unrelated to
# this mission. `run` scores a mutant by "did anything fail", so folding that
# suite in whole would report every mutant as KILLED whether or not it was —
# turning the battery into the exact rubber stamp it exists to prevent. The
# filter selects the describe block added for this wiring, which is green at
# baseline. The meetings suite is new and wholly green, so it needs no filter.
# `preflight` below re-proves both of those claims rather than trusting them.
# One word: the list is expanded unquoted, so a multi-word pattern would split
# and jest would read the tail as another path.
FACTS_SUITE="src/screens/__tests__/PrivateFactsScreen.test.tsx -t linked"
MEETINGS_SUITE="src/screens/__tests__/PrivateMeetingsScreen.test.tsx"

cp "$CLIENT" /tmp/mut_client.bak
cp "$SCREEN" /tmp/mut_screen.bak
cp "$LOCK" /tmp/mut_lock.bak
cp "$LINKED" /tmp/mut_linked.bak
cp "$FACTS" /tmp/mut_facts.bak
cp "$MEETINGS" /tmp/mut_meetings.bak
restore() {
  cp /tmp/mut_client.bak "$CLIENT"
  cp /tmp/mut_screen.bak "$SCREEN"
  cp /tmp/mut_lock.bak "$LOCK"
  cp /tmp/mut_linked.bak "$LINKED"
  cp /tmp/mut_facts.bak "$FACTS"
  cp /tmp/mut_meetings.bak "$MEETINGS"
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
  # Optional second argument: the jest invocation for this mutant. Host mutants
  # are scored against their own screen's suite, because the shared set does not
  # mount either host and would report every one of them as SURVIVED.
  local suites="${2:-$SUITES}"
  local out
  if cmp -s "$CLIENT" /tmp/mut_client.bak &&
     cmp -s "$SCREEN" /tmp/mut_screen.bak &&
     cmp -s "$LOCK" /tmp/mut_lock.bak &&
     cmp -s "$LINKED" /tmp/mut_linked.bak &&
     cmp -s "$FACTS" /tmp/mut_facts.bak &&
     cmp -s "$MEETINGS" /tmp/mut_meetings.bak; then
    echo "  HARNESS ERROR    — $name   <<< mutation did not apply, result meaningless"
    fail=$((fail+1))
    return
  fi
  out=$(npx jest $suites 2>&1)
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

# `run` scores a mutant as KILLED whenever anything fails. That rule is only
# sound if the unmutated tree is green under the same command — otherwise a
# pre-existing failure signs off on every mutant, and a battery that reports all
# green while proving nothing is worse than no battery. So each command is run
# once against clean source first, and the whole thing aborts if any is red.
preflight() {
  local label="$1"
  shift
  if npx jest "$@" 2>&1 | grep -qE "Tests:.*failed"; then
    echo "  PREFLIGHT FAILED — $label is not green at baseline; every mutant"
    echo "                     scored against it would read KILLED regardless."
    exit 1
  fi
  echo "  baseline green   — $label"
}

echo "=== Stage 124 mutation battery ==="
echo "--- preflight ---"
preflight "shared suites" $SUITES
preflight "facts host" $FACTS_SUITE
preflight "meetings host" $MEETINGS_SUITE
echo

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

# --- the reverse-link panel --------------------------------------------------
#
# Same class of claim as mutants 4 and 5, re-pinned on the shared component,
# because "where was this discussed" is answered on four screens and a false
# empty there is the more damaging one: a member concludes an object was never
# discussed and stops looking, on a surface whose entire selling point is that
# the absence is trustworthy.

# 11. Mutual exclusion at the panel: the refusal branch falls through to the
#     rows branch, so a failed read renders "not referenced in any conversation".
perl -0pi -e 's/  if \(result\.state !== "READY"\) \{/  if (false) {/' "$LINKED"
run "a refused reverse lookup renders the empty line"

# 12. The in-flight branch is removed, so a read that has not answered yet is
#     drawn as an answered-and-empty one. `result` is null here, so this also
#     proves the empty branch is not merely optional-chained into silence.
perl -0pi -e 's/  if \(result === null\) \{/  if (result === undefined) {/' "$LINKED"
run "an in-flight reverse lookup renders as answered"

# 13. A 423 on this panel stops relocking, so the office door is left open on a
#     surface that just learned it was locked.
perl -0pi -e 's/    if \(next\.state === "LOCKED"\) lockOfficeLocally\(\);/    \/* mutant: no relock *\//' "$LINKED"
run "a 423 reverse lookup does not relock the office"

# 14. The client collapses a reverse-lookup refusal into an empty READY. Located
#     by line number for the same reason as mutant 5: the catch body is
#     identical in every function in this module.
TARGET_CATCH=$(grep -n "return privateFeatureRefusal(error);" "$CLIENT" | awk -F: -v s="$(grep -n 'export async function listConversationsForTarget' "$CLIENT" | cut -d: -f1)" '$1 > s {print $1; exit}')
sed -i '' "${TARGET_CATCH}s|.*|    return { state: \"READY\", conversations: [], count: 0, capabilities: parsePrivateConversationCapabilities({}) } as PrivateConversationListResult;|" "$CLIENT"
run "reverse-lookup refusal returns an empty READY (line $TARGET_CATCH)"

# 15. No second messenger: the panel opens an Office-side reader rather than
#     handing the conversation id to the canonical Chat screen.
perl -0pi -e 's/            onPress=\{\(\) => onOpenConversation\(Number\(conversationId\)\)\}/            onPress={() => undefined}/' "$LINKED"
run "the panel does not open the canonical thread"

# --- the two hosts that adopted the panel -----------------------------------
#
# Mutants 11-15 prove the panel behaves. They cannot prove it was CONNECTED
# correctly: a host that asks under the wrong link type, or about the wrong
# object, gets a perfectly well-behaved panel confidently answering a question
# the member did not ask. Every mutant below leaves the panel untouched and
# still produces a wrong answer on screen, which is the whole point.

# 16. The facts sheet asks under the wrong link type. The route answers for
#     DOCUMENT too, so this returns 200 with someone else's rows rather than
#     failing — invisible without an assertion on the path.
perl -0pi -e 's/                  linkType="FACT"/                  linkType="DOCUMENT"/' "$FACTS"
run "facts sheet asks under the DOCUMENT link type" "$FACTS_SUITE"

# 17. The facts sheet keys the lookup on the provenance `source_id` — a pointer
#     into private storage that the sheet is explicitly built not to surface.
#     It is a plausible edit (it is right there on the object) and it both asks
#     the wrong question and leaks the locator into a URL.
perl -0pi -e 's/                  targetId=\{inspecting\.id\}/                  targetId={inspecting.provenance.source_id}/' "$FACTS"
run "facts sheet keys the lookup on the private source_id" "$FACTS_SUITE"

# 18. Opening a thread no longer dismisses the sheet, so the modal is left
#     sitting over the conversation the member just asked to read.
perl -0pi -e 's/      setInspecting\(null\);\n      navigation\.navigate\("Chat"/      navigation.navigate("Chat"/' "$FACTS"
run "facts sheet stays open over the thread it opened" "$FACTS_SUITE"

# 19. The meetings row asks under the wrong link type — same class as 16.
perl -0pi -e 's/                  linkType="MEETING"/                  linkType="DOCUMENT"/' "$MEETINGS"
run "meetings row asks under the DOCUMENT link type" "$MEETINGS_SUITE"

# 20. The meetings row keys the lookup on the title instead of the public id.
#     Titles are member-supplied display text, so this is both the wrong key and
#     a way to put meeting titles into request paths.
perl -0pi -e 's/                  targetId=\{meeting\.public_id\}/                  targetId={meeting.title}/' "$MEETINGS"
run "meetings row keys the lookup on the display title" "$MEETINGS_SUITE"

# 21. The per-row disclosure collapses into one shared flag, so opening any row
#     opens every row. Each then mounts its own panel and the member sees one
#     meeting's conversations under another meeting's heading, with nothing on
#     screen to distinguish them.
perl -0pi -e 's/        const isOpen = expanded === meeting\.public_id;/        const isOpen = Boolean(expanded);/' "$MEETINGS"
run "every meeting row discloses at once" "$MEETINGS_SUITE"

echo
echo "killed=$pass survived=$fail"
[ "$fail" -eq 0 ] || exit 1
