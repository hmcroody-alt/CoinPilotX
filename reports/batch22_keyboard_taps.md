# Batch 22 — the first press of a card button reaches the button

## The defect

Found on the iPhone 17 Pro Max simulator while demonstrating Batch 21, not by any test in
this repository.

An approval was left to lapse past its printed `expires_at` and **Confirm** was pressed with
the software keyboard raised. Nothing happened except that the keyboard closed. It was
pressed again, from a corrected coordinate, and nothing happened except that the keyboard
closed. In both cases the card was unchanged, both controls stayed live, and no request left
the device. The press only registered on a third attempt, made with the keyboard already
down.

The cause is a default. `ScrollView` and `FlatList` take `keyboardShouldPersistTaps` of
`"never"` unless told otherwise, and under `"never"` the first touch anywhere outside the
focused input is consumed to dismiss the keyboard and is **never delivered** to the child
beneath it. The UNDX action rail (`ChatScreen.tsx:1144`) and the message list
(`ChatScreen.tsx:1117`) both took the default.

A person reaches an UNDX card by typing. The keyboard is therefore up at the moment the card
arrives, and up at the moment they reach for Confirm. The swallow is not an edge case on this
screen — it is *every* first press.

## Why this is worse than a slow button

The three things a person can conclude from a press that does nothing are all wrong:

* that the button is broken, and the feature does not work;
* that the press *did* land and the change is silently in flight, which is what makes people
  press a second time;
* that the app is refusing them without saying why.

It is also the exact failure mode Batches 20 and 21 were written to eliminate one layer up.
Batch 20 made the server say precisely which kind of dead an approval is; Batch 21 put that
sentence on the card the press was made on. Both of them are about never letting a press
produce silence. A swallowed touch produces silence before either of them can run.

## What was built

`mobile-native/src/screens/ChatScreen.tsx`

* `keyboardShouldPersistTaps="handled"` on the UNDX action rail.
* `keyboardShouldPersistTaps="handled"` on the message list, which carries the Retry control
  on a message that failed to send — the same default, the same consequence, and a person
  retries a failed send while still looking at the composer that produced it.

`"handled"` rather than `"always"`: a touch that no control claims should still put the
keyboard away, because a tap on the empty part of a scroll view is the ordinary way of asking
for that. `"always"` fixes the reported bug and introduces a smaller one.

This is the house convention rather than a new idea. Twenty-odd scrollables across this app
already say `"handled"` — `Screen.tsx`, `LoginScreen`, `SignupScreen`, `MessengerScreen`,
`NewChatScreen`, `SettingsShell`, `ConversationControlCenter` and others. These two were the
omissions, and they were the two on the screen where a missed press costs the most.

## Tests

`src/screens/__tests__/undxKeyboardTaps.test.tsx` — 5 tests.

These are **contract assertions rather than presses**, and the file says so at the top.

The swallow lives in the native responder system. `fireEvent.press` dispatches straight at
the element's handler and never consults a scroll container, so a test that presses Confirm
passes identically with the prop set, unset, or set to `"never"`. It would be a test that
cannot fail, on the one property the batch is about. Writing it would be worse than writing
nothing, because it would look like coverage.

So what is asserted is the value React Native is given — which is the entire fix, and the
entire thing that was wrong:

* the rail's value is one that delivers the touch;
* the rail is not on the default, stated separately and in the negative because the failure
  mode is an absence and `undefined` is easy to skim past in a list of allowed values;
* the rail is specifically `"handled"`, not the looser `"always"`;
* the message list's value delivers the touch;
* **no** scrollable on this screen is on the default — the reading done exhaustively, so a
  third omission does not have to be found by a finger the way these two were.

The behaviour itself was verified the only way it can be, and that run is recorded below.

Batch 21's 8 render tests and Batch 20's 7 unit tests are unchanged and still green with this
applied.

### The regression, and what it does not cover

`tsc --noEmit` is clean, in 10.4 s.

The mobile suite is 105 files. **68 of them were re-run in this sitting and all 68 passed.**
The other 37 were not re-run, and this report will not pretend otherwise. They were not
skipped for convenience — the sandbox's FUSE mount to the Mac degraded over the sitting to
the point where a single `jest` invocation naming three suites produced no output at all
inside the harness's 45-second cap, having produced twenty-one suites inside the same cap
an hour earlier.

Two things were established before giving up on the rest, because "the environment is slow"
is exactly the excuse a real regression would hide behind:

* The overhead is a directory crawl, not the tests. A seven-test file took 32.6 s of wall
  clock to report `Time: 10.5 s`, with 5 s of user CPU — the remainder is jest crawling
  `node_modules`, which is 44,160 files on this mount and takes 12.4 s to walk with `find`
  alone. Restricting the crawl with `--roots '<rootDir>/src'` — every test file lives under
  `src`, so nothing is lost — cut the same run to 12.9 s. That helped, and then stopped
  helping as the mount got slower.
* The one suite that hangs outright, `src/navigation/__tests__/sellerEntryPoints.test.ts`,
  hangs **identically with this batch reverted**. `ChatScreen.tsx` was replaced with its
  `HEAD` contents and the suite still produced nothing in 40 s. It is not this change.

One failure was seen and is not counted as one: `src/settings/__tests__/store.test.tsx`
failed `hydration › renders before the network answers` at 30.1 s inside a parallel batch,
and passed all 44 of its tests when run alone. Three jest workers on four contended cores
push a suite past jest's own per-test limit, which is a fact about this machine rather than
about the code.

What the 37 cover is worth stating plainly, because it bounds the risk: this batch is two
JSX props on one screen. Every suite that renders `ChatScreen` — Batch 20's, Batch 21's and
this batch's — is in the 68 that passed.

## Mutation results

`outputs/mutate22.py`, four modes, **4/4 caught**.

| mode | destroys |
|---|---|
| `rail_on_the_default` | removes the prop from the rail — the observed defect, restored exactly |
| `rail_says_never` | spells the default out loud, so the swallow looks deliberate to a reader |
| `rail_says_always` | loosens to `"always"`, delivering the touch but stranding the keyboard |
| `list_on_the_default` | removes the prop from the message list, so Retry is unpressable |

Each mode names the single test that claims the property it destroys and runs only that test,
and `run_suite` refuses a run that executed no tests, so a mis-named guard cannot read as
SURVIVED. The untouched source is parked in `outputs/.mutate22-original` before mutating, and
`heal()` runs first on any later invocation.

One thing changed from `mutate21.py`: `run_suite` writes jest's output to a file instead of
using `capture_output=True`. `--forceExit` ends jest's own process while a worker it spawned
can still hold the write end of an inherited pipe, and `subprocess.run` then blocks reading a
pipe nobody will close — the tests finish, the harness kills the script at its wall clock, and
the verdict never prints. That is what the first two `check` invocations of this batch did,
twice, before the cause was found. A file has no end to hold open.

## The live demonstration

The demonstration is the origin of this batch rather than a confirmation of it: the defect was
found by pressing Confirm twice on an iPhone 17 Pro Max simulator, iOS 26.5, and watching
nothing happen but the keyboard closing. That run is recorded in full in
`reports/batch21_tap_outcome.md`, along with the third press that did land and the sentence it
produced.

### The run performed

iPhone 17 Pro Max simulator, iOS 26.5, Metro fast-refresh of the two props — no rebuild.

**"can you resume my btc alert"** was pasted into the composer and sent. The card arrived
with the software QWERTY up and the composer still focused, and arrived clipped behind the
composer, so the rail had to be scrolled to bring Cancel and Confirm into view.

*That scroll is itself the first piece of evidence, and it is easy to miss.* A drag begins
with a touch. Under the old `"never"` default that touch would have been consumed to dismiss
the keyboard and the rail would not have moved. It moved, on the first gesture, and the
keyboard stayed up.

The card read:

> Approval expires 2026-07-31T02:48:37+00:00

with **Cancel** and **Confirm** live beneath it. **Confirm was pressed once, at (269, 478),
with the keyboard still raised — and it landed.** The card was replaced by a result carrying
a green **Undo · Resume**.

This is the first press of an UNDX card control on this screen that has ever registered on
the first touch with the keyboard up.

### Read back from the database, not from the screen

A result card is a claim the app makes about itself, so the claim was checked against the
tables the app does not draw from. `pulse_ai_tool_operations` row 46:

| field | value |
|---|---|
| `operation_id` | `undx_op_86ef59f9a13d2fbfceec` |
| `tool_name` | `pulsesoc.crypto_alerts.resume` |
| `canonical_entity_id` | `alert_rule:29` |
| `status` | `verified` |
| `created_at` | `2026-07-31T02:45:06+00:00` |

with `verification_json` reporting `canonical_read_back: true` and `result_json` reporting
`success: true`. `alert_rules` id 29 carries `updated_at` of `2026-07-31T02:45:06` — the same
second — at `active 1`, `status active`.

The card on screen is tied to a specific row rather than to a plausible one: the approval it
drew, `undx_confirm_a196f90d19eda664b4b6`, carries `expires_at` of
`2026-07-31T02:48:37+00:00`, which is the timestamp printed on the card to the second.

### What was not done, and one thing found while doing it

Step 5 of the written run — **Retry** on a message sent with the backend stopped, pressed
with the keyboard up — **was not performed.** The prop it depends on is asserted by the suite
and by a mutation mode, but no finger has been put on that control, and this report does not
imply otherwise.

Reading the tables back also turned up something that is **not** a Batch 22 defect and is not
fixed here. The approval row was still `pending` with `consumed_at` null after the press, and
the operation that ran records `confirmation_state: not_required` and
`confirmation_evidence: "no_grant"`. The change was made, verified and read back — Batch 22's
claim is unaffected — but the approval that authorised it was never redeemed, so it stayed
replayable until its own expiry, and the audit row for a confirmed action does not name the
approval it was confirmed against. Rows 6 and 7, both earlier `crypto.alerts.resume`
approvals, are `pending` and unconsumed for the same reason. That is written up separately.
