# Batch 21 — the answer to a tap appears on the card that was tapped

## The defect

Batch 20 taught the server to distinguish six ways a confirmation can be dead and to send
a different sentence for each. Those sentences were correct on the wire, and invisible on
the screen.

The sentence arrived as the `message` of a rejected `confirm_action`, and the client put it
in `setStatusMessage`. The status banner is rendered `&& !keyboardVisible`. A person taps
Confirm on a card they summoned by typing, so the keyboard is up — which is exactly the
state in which the banner is not drawn.

The rest of the press was equally quiet. The `catch` left `undxComponents` untouched, so the
card stayed exactly as it was. The token had already gone into `undxSpentTokens` before the
request was sent, so Confirm went grey; `undxActionBusy` released and Cancel went grey with
it. The entire visible consequence of pressing Confirm was two buttons dimming, on a card
with no remaining way to clear it.

That is the worst shape a defect can take for a green test suite: the value really is
correct at every layer a unit test looks at. It is only wrong at the one place nothing was
asserting — the screen.

## What was built

`mobile-native/src/undx/actionCards.ts`

* `readTapOutcome(error) -> { message, retryable }`. One place decides what a rejection
  means. It is a named function rather than an expression inside a two-thousand-line render
  precisely so that a test can address it.
* `UNDX_TAP_FALLBACK_MESSAGE`, so a rejection that carried no message still says something.
* `retryable` keys on the transport **code** (`request_unreachable`), never on the status.
  A reachable server also answers 503 — that is what `undx_actions_disabled` is — and
  reading the status alone would re-arm a button against a server that had already refused
  it.

`mobile-native/src/screens/ChatScreen.tsx`

* `undxTapOutcome` holds `{ message, retryable, token }`. Keyed by token, not held as a bare
  string, because a rail can hold more than one card and an outcome with no owner attaches
  itself to whichever one rendered first.
* The sentence is drawn on the card, above the controls, **unconditional on the keyboard**.
* Re-arming: `undxSpentTokens.current.delete(token)` runs only when `outcome.retryable`. A
  token is redeemable exactly once, so a second press can produce the write or the sentence
  saying it already ran — never a second write.
* A card whose approval the server called dead swaps both controls for a single **Dismiss**.
  There is nothing left to approve or to call off; what was missing was any way to clear
  the card.
* Cancel's `catch` was given the same treatment as Confirm's. A refusal the person cannot
  see is a button that did nothing, whichever button it was.
* The outcome text is deliberately not styled as an error. Four of the six sentences say
  nothing changed, which is information rather than a fault, and one reports a write that
  already ran. Red would misdescribe most of what it carries.

## Tests

`src/undx/__tests__/tapOutcome.test.ts` — 7 tests on the reading.
`src/screens/__tests__/undxTapOutcomeCard.test.tsx` — 8 tests on the drawing, through the
real screen: type a message, receive the card the server answers with, press Confirm,
read what appears.

Two of those eight exist because of things found while trying to break the suite rather
than while writing it.

**The keyboard.** Nothing under jest ever opens a keyboard, so `keyboardVisible` is
permanently false and every assertion in the file was being made in the one state where the
defect does not occur. A suite that cannot raise the keyboard would pass just as happily
with the card's sentence gated `&& !keyboardVisible` — which is the defect, unmoved.
`Keyboard.emit` does not exist under jest-expo; the handlers are captured with
`jest.spyOn(Keyboard, "addListener")` and called directly. The resulting test raises the
keyboard, presses Confirm, and asserts both that the card carries the sentence and that the
sentence appears exactly once — so it cannot be quietly reading the banner.

**The second card.** The `outcome_not_matched_to_card` mutation SURVIVED. Dropping the token
match changed nothing at all, because the suite only ever rendered one card, and with one
card on screen matching on the token and matching on nothing look identical. The match is
the thing that makes this an answer rather than a notice, and it was untested. The fix was a
second, unrelated approval and a test that presses one card and asserts exactly one card
carries the sentence — both carrying it would tell the person that a delete they never
pressed had also failed.

Full mobile regression: **105 suites, 1797 tests, all green.** `npx tsc --noEmit` clean.
The Python side is untouched by this batch and last ran **680 tests, OK**.

## Mutation results

`outputs/mutate21.py`, ten modes, **10/10 caught**.

Each mode names the single test that claims the property it destroys, and only that test is
run for it. That is not just a way around a slow harness — though it is also that; a jest
start on this mounted tree costs twenty-four seconds before a test runs. It is the sharper
check: a mutation caught by some unrelated test says only that something noticed, whereas
naming the assertion in advance and watching that one go red says the test written for the
property is the test holding it. A named test that matched nothing would run zero tests and
exit green, reading as SURVIVED — so `run_suite` refuses a run that executed no tests.

| mode | destroys |
|---|---|
| `banner_only` | gates the card's sentence on `!keyboardVisible` — the original defect, exactly |
| `no_outcome_at_all` | replaces the outcome `<Text>` with `{null}` |
| `outcome_not_matched_to_card` | drops the token match, so one card's answer shows under another |
| `no_way_out` | removes the Dismiss branch, leaving the dead card permanently inert |
| `dead_card_keeps_its_buttons` | files the outcome under a token no card holds |
| `spent_token_swallows_the_retry` | never re-arms, so a press that never reached a server is unrepeatable |
| `everything_retries` | re-arms against states the server answered |
| `nothing_retries` | re-arms against nothing, including the unreachable case |
| `retry_by_status` | keys retry on `status === 503` instead of the code |
| `client_rewrites_the_sentence` | discards the server's sentence for one generic line — Batch 20's defect restored from the client |

Like `mutate20.py`, the script parks the untouched source in `outputs/.mutate21-original`
before mutating and heals from it on the next run, so a mutation left applied by a killed
run cannot masquerade as a working tree. It was needed repeatedly: the harness caps a
command at forty-five seconds and a full pass over the render suite costs about forty, so
`apply` / `check` / `restore` are separate invocations, and the sidecar is what makes them
safe to interleave — `restore` needs no memory of what `apply` did.

## DONE — the live simulator demonstration

Performed on the **iPhone 17 Pro Max simulator, iOS 26.5**, against the local backend
(`restart_undx_live_backend.command`) and Metro on 8082 (`restart_metro_local_backend.command`),
on 2026-07-30.

The expired case is the one reachable by tapping — the client refuses a second press of a
token it already sent, so `consumed` cannot be reached from chat — and it is also the case
that previously produced the false "read-only" sentence.

1. Typed **"can you resume my btc alert"** (resume rather than pause, because the BTC alert
   was already paused and a pause would have been a no-op). The hedge forced
   `REQUIRE_CONFIRMATION` and produced a card reading `Resume one paused crypto alert` /
   `Approval expires 2026-07-31T01:28:41+00:00`, with `Cancel` and `Confirm`.
2. Waited past that timestamp without touching the card. At 6:30 PM PDT — `01:30 UTC`, past
   the `01:28:41` deadline — the card was still on the rail with both controls live.
3. Tapped **Confirm**. What appeared, on the card itself:

   > BTC alert · above · 999,999: paused → active
   > Resume one paused crypto alert
   > Approval expires 2026-07-31T01:28:41+00:00
   > **That confirmation ran out of time before it was used, so nothing changed. Ask again
   > and confirm the new one.**

   with `Cancel` and `Confirm` replaced by a single **Dismiss**. That is Batch 20's `expired`
   sentence, verbatim, rendered by Batch 21's on-card outcome and dead-card branch — and it
   is exactly the press that used to produce nothing but two dimmed buttons.
4. Tapped **Dismiss**; the card left the rail and the conversation closed cleanly.
5. Read back independently from `coinpilotx.db`:

   * `pulse_ai_confirmations` id 7, `expires_at 2026-07-31T01:28:41+00:00` — `status pending`,
     `consumed_at None`. Untouched and lapsed, as the sentence claims.
   * `alert_rules` id 29 — `status paused`, `active 0`, `updated_at 2026-07-30T23:00:48`,
     which predates the press. The resume did not happen.

## Found by the demonstration — the tap that never reaches the button

Two separate taps on **Confirm** with the software keyboard raised did nothing except close
the keyboard. The card was unchanged, both controls stayed live, and no request left the
device.

The rail is a `ScrollView` (`ChatScreen.tsx:1144`) with no `keyboardShouldPersistTaps`, so it
takes React Native's default of `"never"`: while the keyboard is up, the first touch anywhere
outside the focused input is consumed to dismiss it and is **not** delivered to the child.
A person who has just typed is in precisely that state, so the first press of Confirm is
always swallowed, and looks from the outside exactly like a button that does nothing.

This does not weaken anything above — the outcome sentence and the Dismiss branch are what
the screenshot shows — but it does mean the on-device path currently reaches the `catch` with
the keyboard already down. It is fixed in **Batch 22**, and the fix is what makes the state
this batch was written for (`keyboardVisible === true` at the moment of the press) reachable
by a finger rather than only by `Keyboard.addListener` under jest.
