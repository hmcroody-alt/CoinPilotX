# Batch 20 — a dead confirmation button says which kind of dead it is

## The defect

`confirm_action` answered six unrelated situations with one sentence:

    That confirmation expired, was already used, or belongs to another account.

`consume_confirmation` returns `None` for all of them, and the endpoint had nothing
else to go on. Five of the six mean nothing happened. One — `consumed` — means the
write was already attempted, and usually succeeded.

A person who taps Confirm, sees no visible change, and reads that sentence concludes
"nothing happened, do it again". For five states that conclusion is correct. For the
sixth it is wrong, and acting on it repeats a write.

## Why the obvious fix is the wrong one

Naming the state for any presented token would let anybody with a guessed string learn
whether it names a real approval, and whether some other account holds one. The collapse
is a deliberate security property, stated in `pending_confirmation_action`:

> an unknown, expired, spent or foreign token yields an empty result, all four
> indistinguishable from each other.

`approval_state` resolves the tension by **narrowing rather than loosening**. The lookup
is filtered on `user_id`, so a row belonging to another account takes exactly the same
branch as a row that does not exist, and both return `unknown`. The owner of an approval
learns what happened to their own approval; nobody learns anything about anybody else's.

## What was built

`services/undx_architecture.py`

* `approval_state(cur, user_id, token) -> str`, owner-scoped and non-writing, returning
  `live` / `expired` / `consumed` / `revoked` / `stale_state` / `unknown`.
* `expired` is decided from `expires_at`, not from a status, because nothing writes an
  `expired` status — a lapsed approval simply sits at `pending` past its deadline.
* A row that is both spent and lapsed reports `consumed`. It was redeemed while it was
  live, and that is the fact that determines whether the change happened.
* A row status outside `_APPROVAL_TERMINAL` reports `unknown` rather than being echoed,
  so a column value invented by a later migration cannot become a sentence.
* `APPROVAL_STATE_MESSAGE`, one sentence per state. Every sentence resolves "did my tap
  do anything?" — either it says nothing changed, or it says the write was already
  attempted. `consumed` speaks about the *approval*, not the outcome: the statuses folded
  into it include `failed_verification`, where the write was attempted and could not be
  read back, so "it was already done" would be a claim this function cannot support.

`services/pulse_ai_service.py`

* The flat 409 now selects its sentence by state and carries a `reason` field. `error`
  stays `confirmation_invalid` and the status stays 409, because existing clients key off
  those; what improves is what the person reads.

## The part that was found by asking where this code actually runs

The first version of the fix was **unreachable in the configuration that ships.**

`UNDX_V4_ACTIONS` is absent from `.env.local` and from the running backend — the agent
replaced the legacy V4/V5 executor rather than joining it. So the flag gate above the
consume call was returning first, and every dead agent-minted approval was answered:

    UNDX actions are currently read-only for this account.

That is not merely uninformative. It is **false** — the agent is enabled for that account
and has just performed writes — and it is false in the direction that hides a change which
already happened. The original defect, one layer up, and worse.

The dead-approval answer therefore now runs *before* the executor's kill switch, for
terminal states only. `unknown` and `live` still fall through to the 503: explaining why a
button did not work is a read, but a stranger holding a leaked token must still get the
same answer as somebody with a typo, and an approval that is still good and blocked by a
switch is exactly what a 503 describes.

## Tests

`tests/undx_agent/test_dead_approval_says_which.py` — 27 tests in five classes:

* `TheStateItselfTests` — each state, including the spent-and-lapsed precedence, the
  unrecognised-status case, totality of the message map, and that reading does not write.
* `NobodyLearnsAboutSomebodyElsesApprovalTests` — a foreign token and a fabricated one
  are the same answer, at the primitive and end to end.
* `TheSentenceThePersonReadsTests` — the sentence for each state, that the old one is
  gone from every value, and that every sentence resolves whether anything changed.
* `ReachableInTheConfigurationThatShipsTests` — the ordering above, asserted in the
  configuration nobody deploys `UNDX_V4_ACTIONS` for.
* `TheContractDidNotMoveTests` — the error code, the status, the 400, and that the
  success path never consults `approval_state` (asserted by making it raise).

Full UNDX suite: **680 tests, OK** (653 before this batch).

## Mutation results

`outputs/mutate20.py`, nine modes, **9/9 caught**:

| mode | destroys | caught by |
|---|---|---|
| `scope` | the owner filter | 3 failures |
| `spent_reads_as_lapsed` | deadline checked before status | 1 failure |
| `echo_unknown_status` | unrecognised status echoed out | 1 failure |
| `live_reads_as_spent` | old sentence for a good approval | 3 failures |
| `consumed_says_nothing_happened` | the original defect, restored | 1 failure |
| `flat_message` | state computed then discarded | 4 failures |
| `gate_before_state` | kill switch back in front | 2 errors |
| `stranger_gets_a_named_state` | `unknown` answered by name | 3 failures |
| `state_on_the_success_path` | diagnostic read in the hot path | 1 failure |

The script now parks the original source in `outputs/.mutate20-original` before mutating
and heals from it on the next run. That is not tidiness: an earlier run of this script was
killed by a harness timeout before its `finally`, leaving `live_reads_as_spent` applied to
a working tree that looked clean at a glance and failed three tests for no visible reason.

## DONE — the live simulator demonstration

Performed on the **iPhone 17 Pro Max simulator, iOS 26.5**, against the local backend and
Metro on 8082, on 2026-07-30. Screen capture, which had previously returned
`Screenshot capture returned nil (permission missing or SCContentFilter failure)` on every
attempt, now works; the grant reports Simulator at tier `full` with
`"screenshotFiltering":"native"`.

The client disables Confirm for a token it has already sent in the same session
(`undxSpentTokens`, `ChatScreen.tsx`), so a double-tap will not reach the `consumed`
sentence from chat. The **expired** case is the one reachable by tapping, and it is also
the case that previously produced the false "read-only" sentence.

Typing **"can you resume my btc alert"** — resume rather than pause, because the BTC alert
was already paused — forced `REQUIRE_CONFIRMATION` and produced a card reading
`Resume one paused crypto alert` / `Approval expires 2026-07-31T01:28:41+00:00`. The card was
left untouched past that deadline and Confirm was pressed at 6:33 PM PDT. What appeared,
in place of *"UNDX actions are currently read-only for this account"*:

> That confirmation ran out of time before it was used, so nothing changed. Ask again and
> confirm the new one.

Read back independently from `coinpilotx.db`: `pulse_ai_confirmations` id 7 is `status
pending` with `consumed_at None` and the lapsed `expires_at` above, and `alert_rules` id 29
is still `status paused`, `active 0`, `updated_at 2026-07-30T23:00:48` — earlier than the
press. The sentence is true: nothing changed.

The card-level rendering of that sentence, and the **Dismiss** control that replaced
`Cancel` / `Confirm` once the approval was declared dead, are Batch 21's work; see
`reports/batch21_tap_outcome.md` for the same run recorded from the client side, and for a
`keyboardShouldPersistTaps` defect the run turned up.
