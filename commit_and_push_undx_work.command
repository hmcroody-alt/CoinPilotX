#!/bin/zsh
#
# Stage, commit and push the UNDX Batch 20 and Batch 21 work.
#
#   Batch 20 — the server decides which kind of dead a confirmation is.
#   Batch 21 — the client puts that answer where the person is looking.
#
# Double-click this file in Finder.
#
# Why this exists, rather than the assistant just running `git commit`:
#
#   1. `.git/index.lock` is owned by the Mac user and the assistant's sandbox
#      mounts the repo without permission to unlink it. A stale zero-byte lock
#      is therefore fatal there and trivial here.
#   2. The sandbox has the repo mounted but no route to github.com — both
#      git@github.com:22 and https://github.com:443 are refused by its proxy.
#      The push has to happen on the Mac, where the SSH key and the network are.
#
# The lock is only removed if no git process is actually running, because a lock
# held by a live process is not stale — it is the thing keeping two writers from
# corrupting the index.

set -e
cd "${0:A:h}"

print "=============================================="
print " commit_and_push_undx_work"
print "=============================================="

# git takes more than one lock, and a process that dies mid-command leaves
# whichever one it held at the time. Clearing only `index.lock` gets as far as
# `git add` and then fails on `HEAD.lock` during the commit — which is exactly
# what happened the first time this ran. So sweep them all, under one judgement.
setopt local_options null_glob
locks=(.git/*.lock .git/refs/**/*.lock .git/logs/**/*.lock)

if (( ${#locks} )); then
  print "Lock files present:"
  for lock in $locks; do
    print "  ${lock}  ($(stat -f %z "$lock") bytes, $(stat -f %Sm "$lock"))"
  done
  print ""

  # `pgrep -x git` is too blunt on its own: an IDE, a shell prompt or a Finder
  # extension can keep a short-lived `git` around, and none of them is holding
  # *this* repository. What matters is whether a git process has this repo open.
  repo="${0:A:h}"
  print "git processes on this machine:"
  ps -Ao pid,etime,command | grep '[g]it' || print "  (none)"
  print ""

  holders=$(ps -Ao pid,command | grep '[g]it' | grep -F "$repo" | grep -v commit_and_push || true)
  if [[ -n "$holders" ]]; then
    print -u2 "A git process has this repository open:"
    print -u2 "$holders"
    print -u2 "Those locks are real. Wait for it to finish and run this again."
    exit 1
  fi

  # A real lock is held for a fraction of a second. One that has been sitting
  # there for minutes with nothing holding the repo is the corpse of a process
  # that died mid-command — here, a git run in a sandbox that cannot unlink it
  # afterwards because the file belongs to this account.
  now=$(date +%s)
  for lock in $locks; do
    age=$(( now - $(stat -f %m "$lock") ))
    if (( age < 60 )); then
      print -u2 "${lock} is ${age}s old — too new to call dead."
      print -u2 "Run this again in a minute."
      exit 1
    fi
  done

  for lock in $locks; do
    print "Removing stale ${lock} (nothing has this repo open)."
    rm -f "$lock"
  done
  print ""
fi

branch=$(git rev-parse --abbrev-ref HEAD)
print "Branch: ${branch}"
print ""

# Scratch that must not be committed.
#
# `__spike__` held a throwaway probe for whether jest-expo's Keyboard can be
# driven at all (it cannot via `emit`; the answer was `spyOn(addListener)`).
# It served its purpose inside one afternoon and has no business in history.
# The assistant's sandbox cannot unlink files in this tree — every `rm` there
# returns "Operation not permitted" — so the deletion happens here.
if [[ -d mobile-native/src/screens/__spike__ ]]; then
  print "Removing mobile-native/src/screens/__spike__ (throwaway probe)."
  rm -rf mobile-native/src/screens/__spike__
  print ""
fi

# The mutation harnesses park the untouched source in outputs/.mutate*-original
# while a mode is applied. That sidecar is a crash-recovery artifact with a
# lifetime of seconds; committing it would put a copy of a source file in
# history under a name that looks like a source file.
if ! grep -q 'mutate\*-original' .gitignore 2>/dev/null; then
  print "outputs/.mutate*-original" >> .gitignore
  print "Added outputs/.mutate*-original to .gitignore."
  print ""
fi

# Two commits, split by path rather than by `git add -A`, because they are two
# batches and the split is exactly clean: Batch 20 is the server deciding what
# to say, Batch 21 is the client putting it somewhere a person can see it.
# Either can be read, reverted or bisected without dragging the other along.

print -r -- "----- Batch 20 (server: which kind of dead) -----"
git add services/undx_architecture.py services/pulse_ai_service.py \
        tests/undx_agent/test_dead_approval_says_which.py \
        reports/batch20_dead_approval.md outputs/mutate20.py .gitignore

if git diff --cached --quiet; then
  print "Nothing staged — Batch 20 was already committed."
else
  print "Staged:"
  git diff --cached --name-status
  print ""
  git commit -F - <<'MSG'
feat(undx): a dead confirmation button says which kind of dead it is

confirm_action answered six unrelated situations with one sentence:

    That confirmation expired, was already used, or belongs to another account.

consume_confirmation returns None for all six and the endpoint had nothing else
to go on. Five of them mean nothing happened. One — consumed — means the write
was already attempted, and usually succeeded. A person who taps Confirm, sees no
visible change and reads that sentence concludes "nothing happened, do it
again". For five states that is correct. For the sixth it repeats a write.

Naming the state for any presented token would be worse than the defect: anybody
with a guessed string could learn whether it names a real approval, and whether
some other account holds one. pending_confirmation_action states that collapse
as a deliberate property — "an unknown, expired, spent or foreign token yields
an empty result, all four indistinguishable from each other".

So this narrows rather than loosens.

  * services/undx_architecture.py — approval_state(cur, user_id, token),
    owner-scoped and non-writing, returning live / expired / consumed / revoked
    / stale_state / unknown. The lookup is filtered on user_id, so a row
    belonging to another account takes exactly the same branch as a row that
    does not exist and both return unknown. The owner learns what happened to
    their own approval; nobody learns anything about anybody else's.
  * expired is decided from expires_at, not from a status, because nothing
    writes an expired status — a lapsed approval sits at pending past its
    deadline. A row that is both spent and lapsed reports consumed: it was
    redeemed while it was live, and that is the fact that determines whether the
    change happened. A status outside _APPROVAL_TERMINAL reports unknown rather
    than being echoed, so a column value invented by a later migration cannot
    become a sentence.
  * APPROVAL_STATE_MESSAGE, one sentence per state, each resolving "did my tap
    do anything?". consumed speaks about the approval, not the outcome: the
    statuses folded into it include failed_verification, where the write ran and
    could not be read back, so "it was already done" would be a claim this
    function cannot support.
  * services/pulse_ai_service.py — the flat 409 selects its sentence by state
    and carries a reason field. error stays confirmation_invalid and the status
    stays 409, because existing clients key off those.

The part that was found by asking where this code actually runs: the first
version of the fix was unreachable in the configuration that ships.
UNDX_V4_ACTIONS is absent from .env.local and from the running backend — the
agent replaced the legacy V4/V5 executor rather than joining it — so the flag
gate above the consume call returned first and every dead agent-minted approval
was answered "UNDX actions are currently read-only for this account." That is
not merely uninformative, it is false, and false in the direction that hides a
change which already happened. The dead-approval answer therefore now runs
before the executor's kill switch, for terminal states only; unknown and live
still fall through to the 503, so a stranger holding a leaked token gets the
same answer as somebody with a typo.

tests/undx_agent/test_dead_approval_says_which.py — 27 tests in five classes:
each state including the spent-and-lapsed precedence and the unrecognised-status
case; a foreign token and a fabricated one giving the same answer at the
primitive and end to end; the sentence for each state and the absence of the old
one from every value; the pre-gate ordering asserted in the configuration nobody
deploys UNDX_V4_ACTIONS for; and that the success path never consults
approval_state, asserted by making it raise.

outputs/mutate20.py — nine modes, 9/9 caught, including gate_before_state, which
restores the ordering that made the whole batch a no-op. The script parks the
original source in outputs/.mutate20-original before mutating and heals from it
on the next run: an earlier run was killed by a harness timeout before its
finally, leaving a mutation applied to a working tree that looked clean at a
glance and failed three tests for no visible reason.

reports/batch20_dead_approval.md records all of the above, and records the live
iPhone 17 Pro Max simulator demonstration, which HAS now been performed. An
approval for "resume one paused crypto alert" was left to lapse past its printed
expires_at and then confirmed; the screen returned "That confirmation ran out of
time before it was used, so nothing changed. Ask again and confirm the new one."
in place of the false "UNDX actions are currently read-only for this account".
Read back independently: the pulse_ai_confirmations row still pending with
consumed_at NULL, and alert_rules id 29 still paused with an updated_at that
predates the press.

Full UNDX suite: 680 tests, OK (653 before this batch).
MSG
  print ""
  print "Committed:"
  git log --oneline -1
  print ""
fi

print -r -- "----- Batch 21 (client: the answer lands on the card) -----"
git add mobile-native/src/screens/ChatScreen.tsx \
        mobile-native/src/undx/actionCards.ts \
        mobile-native/src/undx/__tests__/tapOutcome.test.ts \
        mobile-native/src/screens/__tests__/undxTapOutcomeCard.test.tsx \
        reports/batch21_tap_outcome.md outputs/mutate21.py

if git diff --cached --quiet; then
  print "Nothing staged — Batch 21 was already committed."
else
  print "Staged:"
  git diff --cached --name-status
  print ""
  git commit -F - <<'MSG'
feat(undx): the answer to a tap appears on the card that was tapped

Batch 20 taught the server to distinguish six ways a confirmation can be dead
and to send a different sentence for each. Those sentences were correct on the
wire and invisible on the screen.

The sentence arrived as the message of a rejected confirm_action and the client
put it in setStatusMessage. The status banner is rendered && !keyboardVisible.
A person taps Confirm on a card they summoned by typing, so the keyboard is up —
precisely the state in which the banner is not drawn.

The rest of the press was equally quiet. The catch left undxComponents
untouched, so the card stayed as it was. The token had gone into undxSpentTokens
before the request was sent, so Confirm went grey; undxActionBusy released and
Cancel went grey with it. The entire visible consequence of pressing Confirm was
two buttons dimming, on a card with no remaining way to clear it.

That is the worst shape a defect can take for a green suite: the value really is
correct at every layer a unit test looks at. It is wrong only at the one place
nothing was asserting — the screen.

  * mobile-native/src/undx/actionCards.ts — readTapOutcome(error) returning
    { message, retryable }, plus UNDX_TAP_FALLBACK_MESSAGE so a rejection that
    carried no message still says something. One place decides what a rejection
    means, and it is a named function rather than an expression inside a
    two-thousand-line render precisely so a test can address it. retryable keys
    on the transport code (request_unreachable), never on the status: a
    reachable server also answers 503 — that is undx_actions_disabled — and
    reading the status alone would re-arm a button against a server that had
    already refused it.
  * mobile-native/src/screens/ChatScreen.tsx — undxTapOutcome holds
    { message, retryable, token }, keyed by token rather than held as a bare
    string, because a rail can hold more than one card and an outcome with no
    owner attaches itself to whichever rendered first. The sentence is drawn on
    the card, above the controls, unconditional on the keyboard.
  * The spent token is returned only when outcome.retryable. A token is
    redeemable exactly once, so a second press can produce the write or the
    sentence saying it already ran — never a second write.
  * A card whose approval the server called dead swaps both controls for a
    single Dismiss. There is nothing left to approve or to call off; what was
    missing was any way to clear the card.
  * Cancel's catch got the same treatment as Confirm's. A refusal the person
    cannot see is a button that did nothing, whichever button it was.
  * The outcome text is deliberately not styled as an error. Four of the six
    sentences say nothing changed, which is information rather than a fault, and
    one reports a write that already ran.

Two of the fifteen tests exist because of things found while trying to break the
suite rather than while writing it.

Nothing under jest ever opens a keyboard, so keyboardVisible is permanently
false and every assertion in the file was being made in the one state where the
defect does not occur — a suite that cannot raise the keyboard would pass just
as happily with the card's sentence gated the same way, which is the defect,
unmoved. Keyboard.emit does not exist under jest-expo; the handlers are captured
with spyOn(Keyboard, "addListener") and called directly.

And the outcome_not_matched_to_card mutation SURVIVED. Dropping the token match
changed nothing, because the suite only ever rendered one card, and with one
card on screen matching on the token and matching on nothing look identical. The
match is what makes this an answer rather than a notice, and it was untested.
The fix is a second, unrelated approval and a test that presses one card and
asserts exactly one card carries the sentence — both carrying it would tell the
person that a delete they never pressed had also failed.

outputs/mutate21.py — ten modes, 10/10 caught, including banner_only, which
restores the original defect exactly, and client_rewrites_the_sentence, which
restores Batch 20's defect from the client side. Each mode names the single test
that claims the property it destroys and only that test is run for it: a
mutation caught by some unrelated test says only that something noticed, whereas
naming the assertion in advance and watching that one go red says the test
written for the property is the test holding it. A named test matching nothing
would run zero tests and exit green, reading as SURVIVED, so run_suite refuses a
run that executed no tests.

Mobile regression: 105 suites, 1797 tests, all green. tsc --noEmit clean. The
Python side is untouched by this batch and stands at 680 tests, OK.

reports/batch21_tap_outcome.md records all of the above, and records the live
iPhone 17 Pro Max simulator demonstration, which HAS now been performed. A
lapsed approval was confirmed and the card itself drew "That confirmation ran
out of time before it was used, so nothing changed. Ask again and confirm the
new one.", with Cancel and Confirm replaced by a single Dismiss that cleared the
card from the rail. The alert and the confirmation row were both read back
independently and were untouched.

The run also turned up a defect this batch did not cause and does not fix: the
rail is a ScrollView with no keyboardShouldPersistTaps, so React Native's default
of "never" consumes the first tap to dismiss the keyboard and never delivers it
to the button. Two presses of Confirm with the keyboard up did nothing but close
the keyboard. That is Batch 22, and it is what makes keyboardVisible === true at
the moment of the press reachable by a finger rather than only under jest.

One honest note about what is in this commit. Batch 22's fix is two props in this
same ChatScreen.tsx, and this script stages whole files rather than hunks, so
those two lines ride along here. The commit that follows carries Batch 22's tests,
its mutation harness and its report, and names them.
MSG
  print ""
  print "Committed:"
  git log --oneline -1
  print ""
fi

print -r -- "----- Batch 22 (the first press reaches the button) -----"
git add mobile-native/src/screens/ChatScreen.tsx \
        mobile-native/src/screens/__tests__/undxKeyboardTaps.test.tsx \
        reports/batch22_keyboard_taps.md outputs/mutate22.py

if git diff --cached --quiet; then
  print "Nothing staged — Batch 22 was already committed."
else
  print "Staged:"
  git diff --cached --name-status
  print ""
  git commit -F - <<'MSG'
fix(undx): the first press of a card control reaches the control

Found by a finger on an iPhone 17 Pro Max simulator while demonstrating Batch 21,
not by any test in this repository.

An approval was left to lapse past its printed expires_at and Confirm was pressed
with the software keyboard raised. Nothing happened except that the keyboard
closed. It was pressed again, from a corrected coordinate, and nothing happened
except that the keyboard closed. In both cases the card was unchanged, both
controls stayed live, and no request left the device. The press only registered on
a third attempt, made with the keyboard already down.

The cause is a default. ScrollView and FlatList take keyboardShouldPersistTaps of
"never" unless told otherwise, and under "never" the first touch anywhere outside
the focused input is consumed to dismiss the keyboard and is never delivered to
the child beneath it. The UNDX action rail and the message list both took the
default.

A person reaches an UNDX card by typing. The keyboard is therefore up at the
moment the card arrives and up at the moment they reach for Confirm. The swallow
is not an edge case on this screen — it is every first press.

The three things a person can conclude from a press that does nothing are all
wrong: that the button is broken; that the press did land and the change is
silently in flight, which is what makes people press a second time; or that the
app is refusing them without saying why. It is also the exact failure mode
Batches 20 and 21 were written to eliminate one layer up. Batch 20 made the
server say precisely which kind of dead an approval is; Batch 21 put that
sentence on the card the press was made on. A swallowed touch produces silence
before either of them can run.

  * mobile-native/src/screens/ChatScreen.tsx — keyboardShouldPersistTaps="handled"
    on the UNDX action rail, and on the message list, which carries the Retry
    control on a message that failed to send: the same default, the same
    consequence, and a person retries a failed send while still looking at the
    composer that produced it.
  * "handled" rather than "always": a touch that no control claims should still
    put the keyboard away, because a tap on the empty part of a scroll view is the
    ordinary way of asking for that. "always" fixes the reported bug and
    introduces a smaller one.
  * This is the house convention rather than a new idea. Twenty-odd scrollables
    across this app already say "handled" — Screen.tsx, LoginScreen, SignupScreen,
    MessengerScreen, NewChatScreen, SettingsShell, ConversationControlCenter and
    others. These two were the omissions, and they were the two on the screen
    where a missed press costs the most.

src/screens/__tests__/undxKeyboardTaps.test.tsx — 5 tests, and they are contract
assertions rather than presses. The file says so at the top and the reason is not
convenience: the swallow lives in the native responder system, and
fireEvent.press dispatches straight at the element's handler and never consults a
scroll container. A test that presses Confirm passes identically with the prop
set, unset, or set to "never" — it would be a test that cannot fail, on the one
property the batch is about. Writing it would be worse than writing nothing,
because it would look like coverage.

So what is asserted is the value React Native is given, which is the entire fix
and the entire thing that was wrong: that the rail's value delivers the touch;
that the rail is not on the default, stated separately and in the negative
because the failure mode is an absence and undefined is easy to skim past in a
list of allowed values; that the rail is specifically "handled" and not the
looser "always"; that the message list's value delivers the touch; and that no
scrollable on this screen is on the default — the reading done exhaustively, so a
third omission does not have to be found by a finger the way these two were.

outputs/mutate22.py — four modes, 4/4 caught: rail_on_the_default restores the
observed defect exactly, rail_says_never spells the default out loud so the
swallow looks deliberate to a reader, rail_says_always loosens to the value that
delivers the touch and strands the keyboard, and list_on_the_default makes Retry
unpressable. Each mode names the single test that claims the property it destroys
and runs only that test, and run_suite refuses a run that executed no tests.

One thing changed from mutate21.py: run_suite writes jest's output to a file
instead of using capture_output=True. --forceExit ends jest's own process while a
worker it spawned can still hold the write end of an inherited pipe, and
subprocess.run then blocks reading a pipe nobody will close — the tests finish,
the harness kills the script at its wall clock, and the verdict never prints.
That is what the first two check invocations of this batch did, twice, before the
cause was found. A file has no end to hold open.

Batch 21's 8 render tests and Batch 20's 7 unit tests are unchanged and still
green with this applied. tsc --noEmit is clean.

The mobile regression is reported honestly rather than as a round number. The
suite is 105 files; 68 were re-run in this sitting and all 68 passed, and the
other 37 were not re-run. The sandbox's mount degraded to where one jest
invocation naming three suites produced no output inside a 45s cap, having
produced twenty-one inside the same cap an hour earlier. The overhead was
measured rather than assumed: a seven-test file took 32.6s of wall clock to
report Time: 10.5s with 5s of user CPU, because jest crawls node_modules —
44,160 files on this mount, 12.4s to walk with find alone — and restricting the
crawl with --roots '<rootDir>/src' cut that run to 12.9s. The one suite that
hangs outright, navigation/sellerEntryPoints, hangs identically with
ChatScreen.tsx replaced by its HEAD contents, so it is not this change. One
failure was seen and is not counted: settings/store.test.tsx failed one
hydration test inside a parallel batch at 30.1s and passed all 44 alone, which
is three workers on four contended cores rather than anything in the code. What
bounds the risk is that this batch is two JSX props on one screen, and every
suite that renders ChatScreen is among the 68 that passed.

The fixed build was demonstrated on an iPhone 17 Pro Max simulator, iOS 26.5.
"can you resume my btc alert" was sent with the software keyboard up. The card
arrived clipped behind the composer and was scrolled into view by a single drag
that the keyboard survived — under the old default that first touch would have
been eaten and the rail would not have moved. Confirm was then pressed exactly
once, with the keyboard still raised, and it landed: the card was replaced by a
result carrying Undo · Resume. It is the first press of a card control on this
screen to register on the first touch with the keyboard up.

The screen was not taken at its word. pulse_ai_tool_operations row 46,
undx_op_86ef59f9a13d2fbfceec, records pulsesoc.crypto_alerts.resume against
alert_rule:29 as verified at 2026-07-31T02:45:06+00:00 with canonical_read_back
true, and alert_rules id 29 carries that same second in updated_at. The card is
tied to a specific approval rather than a plausible one: the expires_at printed
on it, 2026-07-31T02:48:37+00:00, is that row's own value to the second.

Two things are not claimed. Retry on a failed send, pressed with the keyboard up,
was not exercised by a finger — the prop is asserted by the suite and by a
mutation mode, and that is all it is. And reading the tables back turned up a
separate defect that is NOT fixed here: the approval was still pending with
consumed_at null after the press, and the operation records confirmation_state
not_required with confirmation_evidence no_grant. The change was made, verified
and read back, so this batch's claim stands — but the approval that authorised it
was never redeemed and stayed replayable until its own expiry. That is written up
on its own rather than folded in here.
MSG
  print ""
  print "Committed:"
  git log --oneline -1
  print ""
fi

print -r -- "----- Batch 23 (a spent approval is actually spent) -----"
git add services/undx_tool_gateway.py services/undx_architecture.py \
        tests/undx_agent/test_spent_approval.py \
        reports/batch23_spent_approval.md outputs/mutate23.py

if git diff --cached --quiet; then
  print "Nothing staged — Batch 23 was already committed."
else
  print "Staged:"
  git diff --cached --name-status
  print ""
  git commit -F - <<'MSG'
fix(undx): redeem the approval that was presented, not the one the policy expected

Found by reading the database back after Batch 22's simulator run, not by any test
here. Batch 22's claim held — the press landed, the write ran, the change was
verified. The approval that authorised it was never redeemed.
pulse_ai_confirmations rows 6, 7 and 8, all crypto.alerts.resume, all still
pending with consumed_at null, against pulse_ai_tool_operations row 46 recording
the resume as verified at 2026-07-31T02:45:06+00:00 with confirmation_evidence
no_grant.

The cause is two questions treated as one. "Is an approval needed?" is the policy
engine's question and it is asked of the request. "Is an approval being spent?" is
the gateway's question and it is answered by whether a token arrived. execute()
nested the whole redemption block under `if decision.needs_confirmation:`, which
made the second conditional on the first, so a presented token was ignored
whenever the policy concluded no card was needed.

That is the normal confirm path for half the registry rather than a corner case.
_agent_confirm calls the gateway with explicit_request=True — truthfully, since
pressing Confirm is explicit — and for a CONTEXTUAL capability the policy engine
returns ALLOW via explicit_single_resource. ALLOW means needs_confirmation is
False, so the token just presented is never looked at.

Every existing test passed throughout. test_confirm_path::test_token_cannot_be_
replayed has asserted single use since the gateway was written, against
crypto.alerts.delete, whose policy is ALWAYS. Every capability the single-use
guard has ever been tested against is an ALWAYS capability; pause and resume are
CONTEXTUAL and take the other branch. That is the more useful half of this batch:
a suite can assert the right property, in the right words, against the wrong arm
of a branch, and read for months as coverage.

What it cost. The approval was replayable for the rest of its TTL — two presses
inside five minutes performed the write twice, and idempotency is no defence
because the key derives from the caller's request id and the second press carries
a fresh one. Batch 20's `consumed` state was unreachable on this path, so the one
message that tells a person "it was already used, go and look" — the single state
where pressing again is the wrong advice — could never be produced. And the audit
trail could not answer "authorised by what".

undx_tool_gateway.py: step 5 is now two independent conditions. A presented token
is redeemed whatever the policy concluded, and a token that cannot be redeemed
refuses the call rather than falling through to an execution it no longer
authorises. Expired, already used, minted for another action and never existed
stay indistinguishable to the caller, because saying which one applies turns the
token into an oracle. begin_tool_operation is now told confirmed=bool(grant).

undx_architecture.py: pulse_ai_tool_operations.confirmation_state is a column
about an operation that was being filled from the tool registry — a fact about
the tool. A contextual capability is registered as not normally needing approval,
so an operation a person explicitly approved was written down identically to one
nobody was asked about. Three named constants replace the bare strings, with
`confirmed` added to the vocabulary rather than substituted so existing readers
keep working, and in record_tool_result the clause order is reversed: the
redeemed grant is checked before the registry default because it is the stronger
and more specific fact.

tests/undx_agent/test_spent_approval.py — 11 tests. _hedged_pause asserts that its
phrasing actually earns a card before returning a token, because the whole defect
lives in the gap between a request that needs a card and a redemption the policy
thinks does not, and a helper that quietly stopped producing cards would turn the
file green and meaningless. Three assertions look redundant and are not: the row
reaches consumed; the token cannot be replayed, stated separately because a
redemption that burned the row and let execution through would pass the first and
fail this; and the replay does not reach the executor, asserted at the audit table
because a refusal that still ran the write would answer the second correctly and
be exactly the bug. Two guard what must not regress — an ALWAYS capability still
burns its approval, and an unhedged "pause alert N" still needs no card.

Three of the eleven exist because a mutation survived, and both reasons are the
same shape as the original defect: a guard whose proof came from somewhere else.
Deleting the gateway's `if not grant:` refusal changed nothing, because a replay
through confirm_action never reaches it — _agent_confirm routes on
pending_confirmation_action, which selects on status='pending', so once the first
press consumes the row the routing read finds nothing and the request falls
through to the legacy 409. A guard whose only proof is that some caller upstream
stops first is not a guard, and the runtime reaches the gateway without passing
through confirm_action. And setting confirmed= to either constant changed nothing,
because record_tool_result recomputes the column when the operation finishes —
which leaves the argument load-bearing only for an operation that begins and never
finishes, which is exactly the row a person investigating an interrupted change
has to read. So it is asserted where it is passed, both ways, plus a third test on
the reservation itself.

outputs/mutate23.py — seven modes, 7/7 caught: redemption_under_policy restores
the original defect exactly, presented_token_is_ignored stops redeeming at all,
dead_token_executes_anyway lets an unredeemable token reach the executor,
audit_forgets_the_grant and confirmed_becomes_a_constant break the two directions
of the audit flag, registry_outranks_the_grant reverts the clause order, and
every_write_demands_a_card is the guard against buying the fix by demanding
approval for actions that are their own approval. run_suite refuses a run in which
unittest -k matched nothing, because "Ran 0 tests ... OK" exits green and would
print as SURVIVED.

Regression: 33 suites, 32 green. 28 of 29 UNDX suites pass; test_feed_intelligence_
pack fails on ModuleNotFoundError: No module named 'werkzeug' through
media_service.py, which is a missing package in this sandbox rather than a
regression — it fails on its own import — and it is not counted as a pass either.

Four business_os suites first reported "Ran 0 tests in 0.000s  OK" under
python3 -m unittest, and that is not a pass; it is precisely the result that
misreads as one. They are pytest-style module-level functions with no TestCase and
pytest is not installed here, so plain unittest collects nothing. Each carries its
own _run_standalone() under __main__ — the invocation its docstring names. Run
that way: test_confirmations 5/5, test_crypto_alerts 8/8, test_undx_engine 15/15,
test_confirmation_conformance 34/34. 62/62. The last matters most here: it runs one
set of required properties against every approval boundary in the repo, and its
five L5 tests cover undx_architecture + pulse_ai, the exact surface this batch
edited. All five pass, including test_L5_audit_trail_requires_grant_evidence_not_
a_claim.

Demonstrated on an iPhone 17 Pro Max simulator, iOS 26.5, against the local backend
restarted onto this code. "can you pause my bitcoin alert" was sent with the
keyboard up; the card printed "Approval expires 2026-07-31T03:33:37+00:00" and
Confirm landed on the first touch.

Read back from the database rather than the screen. pulse_ai_confirmations row 9 —
expires_at matching the card to the second — is status=consumed with consumed_at
2026-07-31T03:29:01+00:00. pulse_ai_tool_operations row 48 records
confirmation_state=confirmed and confirmation_evidence=grant_consumed. The same
fields on row 46, the equivalent confirmed press made before this batch, read
not_required and no_grant against an approval row that is still pending with
consumed_at null. alert_rules 29 is active 0, status paused, updated_at
2026-07-31T03:29:02.

What is NOT claimed on device: the second press. Batch 21 replaces the card with
the result card as soon as the first press succeeds, so there is no second Confirm
to press inside the TTL. That is the same finding the mutation harness produced from
the other side — a replay through confirm_action never reaches the gateway's refusal
because _agent_confirm routes on pending_confirmation_action, which selects on
status='pending'. The refusal of a spent token is proven at the gateway by
test_the_gateway_refuses_a_dead_token_presented_directly_to_it, not on the device.
MSG
  print ""
  print "Committed:"
  git log --oneline -1
  print ""
fi

print -r -- "----- Batch 24 (a refused confirmation leaves a trace) -----"
git add services/pulse_ai_service.py pulse_communications_v2/routes.py \
        tests/undx_agent/test_confirm_trace.py \
        reports/batch24_refusal_leaves_no_trace.md \
        reports/evidence/batch24_live_log_extract.txt \
        outputs/mutate24.py

# Anything left over — including this script's own edits and the backend launcher
# changed to make this batch's evidence greppable — goes with the last commit
# rather than being silently dropped. The explicit `git add` lines above are about
# ordering the history, not about excluding work.
git add -A

if git diff --cached --quiet; then
  print "Nothing staged — Batch 24 was already committed."
else
  print "Staged:"
  git diff --cached --name-status
  print ""
  git commit -F - <<'MSG'
fix(undx): a refused confirmation leaves a record on both sides

Batch 20 gave a dead Confirm button a sentence that tells the person to go and
check where things stand. It gave them nothing to check it by, and gave the server
nothing at all. From the server's point of view the press did not happen:
correlation_id None, log lines 0, rows written 0.

Four omissions of the same kind.

Seven of confirm_action's nine return paths discarded the correlation_id it
computes on its first line — every refusal, plus the legacy success payload. Only
the accepted_unverified 202 and _agent_confirm_payload carried it. Nobody decided
that; each return was written on its own day.

A refusal was recorded nowhere. Not a fabricated token, not a lapsed one, not
another account's. A support conversation opening "I pressed Confirm and it told me
to check where things stand" had no thread to pull.

_timed_json logged payload.get("trace_id"), and pulse_ai_service emits "trace_id"
zero times and "correlation_id" eleven. The one request-level log line on those
endpoints read trace_id=None, and the route's own freshly computed id was dead code
because these payloads are always dicts. The correct precedence chain was already
written twelve lines below for the call-route warning, and was not reused.

record_tool_result was handed correlation_id=_trace() — a second random id for the
audit row of an operation that already had one. The record that outlives log
retention could not be joined to the request that caused it.

The fix is a wrapper, not seven edited dictionaries: confirm_action mints the id,
passes it into _confirm_action, and stamps the answer with setdefault, so a return
path nobody has written yet is stamped too, and a payload naming its own downstream
trace keeps it. One log line on refusal only. _payload_trace(payload, fallback) in
routes.py names the precedence chain once and both call sites use it.

The token is deliberately not logged. A pending approval token is a live bearer
credential and it is the most obviously useful thing in scope when somebody decides
a refusal log looks thin. reason is logged and is safe to log, because
approval_state is owner-scoped upstream: a foreign token and a fictional one both
report unknown, and a test asserts the two log lines are byte-identical modulo the
id — a log that distinguished them would undo Batch 20's indistinguishability
property from behind, for anybody who can read logs.

tests/undx_agent/test_confirm_trace.py — 27 tests, all passing.
outputs/mutate24.py — ten modes, 10/10 caught. Five of them do not restore the
original defect; they restore the mistakes this fix invites, because "the response
has a correlation_id" passes against a stamp that overwrites the payload's own id,
against a stamp that matches nothing, and against a log line that leaks the token
alongside it.

Two modes SURVIVED on the first run and both found real holes in these tests.
stamp_mints_a_second_id survived because its guard asserted on the success path,
where _agent_confirm_payload has already set the key and setdefault does nothing —
the test could not fail on the property it named. resolver_prefers_the_key_nobody_
emits survived because its guard's payload carried only correlation_id, so swapping
the precedence still returned it: the test asserted presence and was named for
precedence. Both guards were rewritten onto payload shapes that can observe the
property, and the mutation mode that exposed each is named in its docstring.

Regression: the whole of tests/undx_agent re-run in three passes, 718 tests passed.

Demonstrated on an iPhone 17 Pro Max simulator, iOS 26.5, against the local backend
restarted onto this code. "Change my ethereum alert to 777777" minted an approval
because crypto.alerts.update is risk high, confirmation True; the card printed
"Approval expires 2026-07-31T04:37:25+00:00"; the approval was left to lapse and
Confirm pressed at 04:38:33, sixty-eight seconds late, with the keyboard up. The app
answered with Batch 20's expired sentence. coinpilotx.log lines 40405 and 40406:

  UNDX_CONFIRM_REFUSED user_id=10910211866 correlation_id=ff96be6afb90 error=confirmation_invalid reason=expired http_status=409
  PULSE_COMM_V2_TIMING metric=pulse_ai_confirm_action duration_ms=112 method=POST path=/api/pulse-ai/actions/confirm ok=False status=None trace_id=ff96be6afb90

Same millisecond, one id, and grep returns those two and nothing else. Before this
batch the first line did not exist and the second read trace_id=None. The approval's
confirmation_id and token_hash appear nowhere in the log, and neither does the
string "token" in the refusal line. Row 10 is still pending with consumed_at null —
an expired approval is refused without being spent — and alert_rules 30 still reads
target_value 888888.0, updated_at 04:30:10, eight minutes before the press. The
sentence the person was shown is literally true.

The timing half is proved by the same file spanning both regimes across the 21:12
restart: 6,517 requests logged trace_id=None before and none after.

A claim in the first draft of the report was wrong and the live log is what caught
it. It said "89 endpoints route through _timed_json, and all 89 logged
trace_id=None". The AST count is 88, and it was never all of them:
pulse_communications_v2/service.py:4238 does result.setdefault("trace_id", _trace()),
so metric=api_active_calls logged a real id 3,885 times before the fix and is the
control that shows the change did not disturb payloads that already carried one. The
report is corrected and says so in those words.

No Python test exercises routes.py at runtime, here or anywhere in this repository,
because it cannot be imported without Flask. That is the honest bound on the
_timed_json half: its argument is asserted by AST, its helper by executing the real
FunctionDef parsed out of the shipped source, and the wiring between them on the
device rather than in this suite.

Also in this commit: restart_undx_live_backend.command tees the server's output to
logs/undx_backend.log. Written with zsh process substitution rather than a pipe
because `python bot.py | tee ... &` sets $! to the tee process, and $! is the entire
basis of the script's stale-server pid guard — it would have reported MISMATCH on
every healthy start and been switched off by the next person to hit it. And with
python -u, because Python block-buffers stdout when it is not a tty: the first run of
that change looked like a hung server, terminal frozen mid-boot and the log sitting
at 3,991 bytes while the process was healthy and serving.
MSG
  print ""
  print "Committed:"
  git log --oneline -1
  print ""
fi

print "Unpushed commits:"
git log --oneline "origin/${branch}..HEAD" 2>/dev/null || print "  (no remote-tracking branch yet)"
print ""

print "Pushing to origin/${branch}..."
git push origin "${branch}"

print ""
print "=============================================="
print "Pushed. Remote is now:"
git log --oneline -1 "origin/${branch}"
print "=============================================="
print ""
print "You can close this window."
