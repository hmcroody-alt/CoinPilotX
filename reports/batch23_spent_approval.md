# Batch 23 — a spent approval is actually spent

## The defect

Found by reading the database back after Batch 22's live simulator run, not by any test in
this repository. Batch 22's claim — that the first press of Confirm lands with the keyboard
up — held. The press landed, the write ran, the change was verified. The approval that
authorised it was never redeemed.

`pulse_ai_confirmations` rows 6, 7 and 8, all `crypto.alerts.resume`, all still `pending`
with `consumed_at` null, against `pulse_ai_tool_operations` row 46 recording the resume as
`verified` at `2026-07-31T02:45:06+00:00` with `confirmation_evidence: "no_grant"`.

The cause is two questions treated as one.

**"Is an approval needed?"** is the policy engine's question, and it is asked of the
*request*. **"Is an approval being spent?"** is the gateway's question, and it is answered
by whether a token *arrived*. `undx_tool_gateway.execute` nested its entire redemption block
under `if decision.needs_confirmation:`, which made the second question conditional on the
first. A presented token was ignored whenever the policy concluded no card was needed.

That combination is not a corner case — it is the normal confirm path for half the
capabilities in the registry. `_agent_confirm` calls the gateway with
`explicit_request=True`, which is truthful: pressing Confirm is about as explicit as a
person gets. `undx_agent_policy.evaluate` then reaches the `CONTEXTUAL` arm, sees an
explicit request against a single resolved resource, and returns `ALLOW` with reason
`explicit_single_resource`. `ALLOW` means `needs_confirmation` is `False`. The token that
was just presented is never looked at.

## Why every existing test passed

`test_confirm_path.py::test_token_cannot_be_replayed` asserts single use, and has since the
gateway was written, and passed throughout the entire period in which a `CONTEXTUAL`
approval was replayable for its whole TTL.

It asserts it against `crypto.alerts.delete`, whose confirmation policy is `ALWAYS`. Every
capability the single-use guard has ever been tested against is an `ALWAYS` capability.
`pause` and `resume` are `CONTEXTUAL` and take the other branch.

This is the more useful half of the batch. A suite can assert the right property, in the
right words, against the wrong arm of a branch, and read for months as coverage.

## What it cost

* **The approval was replayable for the remainder of its TTL.** Pressing Confirm twice
  inside five minutes performed the write twice. Idempotency is not a defence: the key is
  derived from the caller's request id, and a second press carries a fresh one.
* **Batch 20's `consumed` state was unreachable on this path.** Batch 20 taught a dead
  approval to say *which kind* of dead it is. An approval spent this way never reaches
  `consumed`, so the one message that tells a person "it was already used, go and look" —
  the single state where "press it again" is the wrong advice — could never be produced.
* **The audit trail could not answer "authorised by what".** A confirmed action recorded
  `confirmation_state="not_required"`, `confirmation_evidence="no_grant"`.

## What was built

### `services/undx_tool_gateway.py` — redemption no longer hangs off the policy verdict

Step 5 is now two independent conditions. A token that was presented is redeemed, whatever
the policy concluded. A token that cannot be redeemed **refuses the call** rather than
falling through to an execution the presented approval no longer authorises — expired,
already used, minted for another action and never existed are deliberately
indistinguishable to the caller, because telling an attacker which one applies turns the
token into an oracle.

The `begin_tool_operation` call now passes `confirmed=bool(grant)` — what this operation was
authorised by, rather than what the tool usually needs.

### `services/undx_architecture.py` — the audit column says something about the operation

`pulse_ai_tool_operations.confirmation_state` is a column about an *operation* that was
being filled from `undx_policy.PRODUCTION_TOOL_REGISTRY` — a fact about the *tool*. A
contextual capability is registered as not normally needing approval, so an operation a
person had explicitly approved was written down identically to one nobody was ever asked
about.

Three named constants replace the bare strings, and `confirmed` is *added* to the vocabulary
rather than substituted for anything, so existing readers keep working. In
`record_tool_result` the clause order is reversed: the redeemed grant is checked before the
registry default, because it is the stronger and more specific fact.

## Tests

`tests/undx_agent/test_spent_approval.py` — **11 tests**.

The first five state the defect. `_hedged_pause` asserts that the phrasing it uses actually
earns a card before returning its token, because the whole defect lives in the difference
between a request that needs a card and a redemption the policy engine thinks does not — a
helper that quietly stopped producing cards would turn every test in the file green and
meaningless.

Three of them look redundant and are not:

* the approval row reaches `consumed`;
* the token cannot be replayed — *stated separately*, because a redemption that burned the
  row but let execution through anyway would pass the first and fail this one;
* the replay does not reach the executor — *asserted at the audit table*, because a refusal
  that still ran the write would answer the second correctly and be exactly the bug.

Two guard what must not regress: an `ALWAYS` capability still burns its approval (the case
that was already covered, kept so a fix cannot trade one for the other), and an unhedged
`"pause alert N"` still needs no card at all.

### Three tests that exist because a mutation survived

The remaining three were written after `mutate23.py` reported SURVIVED on modes that broke
real code, and the reasons are worth recording because both are the same shape as the
original defect — a guard whose proof came from somewhere else.

**Deleting the gateway's `if not grant:` refusal changed nothing.** Not because the refusal
is wrong, but because a replay through `confirm_action` never reaches it: `_agent_confirm`
routes on `pending_confirmation_action`, which selects on `status='pending'`. Once the first
press consumes the row the routing read finds nothing, `_agent_confirm` returns `None`, and
the request falls through to the legacy branch and its 409. A guard whose only proof is that
some caller upstream happens to stop first is not a guard — the runtime reaches the gateway
without passing through `confirm_action`. So `test_the_gateway_refuses_a_dead_token_presented_directly_to_it`
presents a dead token to `execute` itself.

**Setting `confirmed=` to either constant changed nothing.** `record_tool_result` recomputes
`confirmation_state` from the redeemed grant when the operation finishes, overwriting what
`begin_tool_operation` wrote. That makes the argument invisible to any test that reads the
finished row, while leaving it load-bearing in the one case that matters: an operation that
begins and never finishes. A write that crashes mid-flight leaves the row exactly as `begin`
wrote it, and that is the row a person investigating an interrupted change has to read. So
the argument is asserted where it is passed, in both directions, plus a third test on the
reservation itself.

## Mutation results

`outputs/mutate23.py`, seven modes, **7/7 caught**.

| mode | destroys |
|---|---|
| `redemption_under_policy` | puts redemption back under the policy verdict — the original defect, restored exactly |
| `presented_token_is_ignored` | stops redeeming at all, so every press performs the write again |
| `dead_token_executes_anyway` | burns the grant but lets an unredeemable token through to the executor |
| `audit_forgets_the_grant` | stops telling the audit layer a grant was redeemed |
| `confirmed_becomes_a_constant` | labels every write confirmed, making the column worthless — catches a fix applied by flattening |
| `registry_outranks_the_grant` | asks the tool registry first again, so a fact about the tool overwrites a fact about the operation |
| `every_write_demands_a_card` | mints an approval for everything — the cheap way to pass the rest of the file |

Each mode names the single test that claims the property it destroys and runs only that
test. `run_suite` refuses a run in which `unittest -k` matched nothing, because "Ran 0 tests
… OK" exits green and would print as SURVIVED — a mis-typed guard name would read as a hole
in the suite. The untouched source is parked in `outputs/.mutate23-original` before mutating,
and `heal()` runs first on any later invocation.

`run_suite` writes to a file rather than using `capture_output`, for the reason found in
Batch 22: a child process holding the write end of an inherited pipe can block the read
forever after the parent has exited.

## The regression

**33 suites run, 32 green.**

29 UNDX suites under `tests/undx_agent`, of which 28 pass. `test_feed_intelligence_pack`
fails on `ModuleNotFoundError: No module named 'werkzeug'`, raised through
`services/media_service.py`. It is a missing package in this sandbox rather than a
regression — `python3 -c "import werkzeug"` fails on its own — and it touches nothing this
batch edited. It is not counted as a pass either.

The suites most exposed to this change are all green: `test_confirm_path` (14),
`test_adversarial` (31), `test_audit_durability` (11), `test_dead_approval_says_which` (27),
`test_point_of_no_return` (21), `test_end_to_end` (28), `test_review_hardening` (60),
`test_continuation` (84), `test_crypto_alert_pack` (35), `test_response_intelligence` (95).

### The four suites that reported "Ran 0 tests … OK"

`tests/business_os/test_confirmations`, `test_crypto_alerts`, `test_undx_engine` and
`test_confirmation_conformance` each reported `Ran 0 tests in 0.000s  OK` under
`python3 -m unittest`, and that is not a pass. It is exactly the result that misreads as
one.

They are pytest-style module-level functions with no `TestCase`, so plain `unittest`
collects nothing, and `pytest` is not installed in this sandbox. Each carries its own
`_run_standalone()` under `__main__` — the invocation its docstring names. Run that way:

| suite | result |
|---|---|
| `test_confirmations` | 5/5 passed |
| `test_crypto_alerts` | 8/8 passed |
| `test_undx_engine` | 15/15 passed |
| `test_confirmation_conformance` | 34/34 passed |

**62/62.** `test_confirmation_conformance` is the one that matters most here: it runs one
set of required properties against every approval boundary in the repo, and its five **L5**
tests cover `services.undx_architecture` + pulse_ai — the exact surface this batch edited.
All five pass, including `test_L5_audit_trail_requires_grant_evidence_not_a_claim`.

## The live demonstration

### The run performed

iPhone 17 Pro Max simulator, iOS 26.5, against the local backend restarted onto the fixed
code. The restart printed its policy self-check first — `agent_enabled True, reads_enabled
True, writes_enabled True, writes_kill_switch False, qa_cohort_configured True,
writes_available() True` — so the run went through the write path rather than a read-only
stub.

**"can you pause my bitcoin alert"** was sent with the software keyboard raised. The approval
card arrived clipped behind the composer, reading:

> Pause one crypto alert so it stops triggering
> Approval expires 2026-07-31T03:33:37+00:00

with **Cancel** and **Confirm** beneath it. **Confirm was pressed once, at (269, 492), with
the keyboard still up — and it landed on the first touch.** The card was replaced by a result
card reading *"Done — BTC alert … above $99,999 is now paused, and I read it back from
PulseSoc to confirm it"*, carrying a green **Undo · Resume**. Batch 22's property holds on a
second, independent run.

### Read back from the database, not from the screen

`pulse_ai_confirmations` row 9 — the approval this press drew:

| field | value |
|---|---|
| `confirmation_id` | `undx_confirm_2ac6c222cd9debde9146` |
| `action_id` | `crypto.alerts.pause` |
| `target_id` | `29` |
| `arguments_json` | `{"alert_id":29}` |
| `status` | **`consumed`** |
| `expires_at` | `2026-07-31T03:33:37+00:00` |
| `consumed_at` | **`2026-07-31T03:29:01+00:00`** |

The `expires_at` is the timestamp printed on the card, to the second, so the row read back is
the row the press was made on rather than a plausible one.

The comparison that makes this a fix rather than a screenshot is row 48 against row 46 — the
same capability family, the same alert, the same confirmed press, one before this batch and
one after:

| | row 46 — Batch 22's run, before | row 48 — this run, after |
|---|---|---|
| `tool_name` | `pulsesoc.crypto_alerts.resume` | `pulsesoc.crypto_alerts.pause` |
| `status` | `verified` | `verified` |
| `confirmation_state` | `not_required` | **`confirmed`** |
| `confirmation_evidence` | `no_grant` | **`grant_consumed`** |
| approval row afterwards | `pending`, `consumed_at` null | **`consumed`** at `03:29:01` |

`alert_rules` id 29 carries `active 0`, `status paused`, `updated_at 2026-07-31T03:29:02` —
the write happened, one second after the approval was spent. `verification_json` reports
`canonical_read_back: true`.

Rows 6, 7 and 8 are still `pending` with `consumed_at` null. They were minted before the fix
and nothing retroactively redeems them; they are left as they are, because they are the
evidence the defect was real.

### The second press, and why it could not be made

The written run called for pressing **Confirm twice inside the five-minute TTL**. That press
**was not made, and could not be**: Batch 21 replaces the card with the result card the moment
the first press succeeds, so there is no second Confirm on screen to press. The TTL had four
minutes left and no control to spend it with.

That is not a gap in the run — it is the same finding the mutation harness produced, arriving
from the other direction. `dead_token_executes_anyway` SURVIVED because a replay through
`confirm_action` never reaches the gateway's refusal: `_agent_confirm` routes on
`pending_confirmation_action`, which selects on `status='pending'`, and the row is now
`consumed`. The UI cannot offer the second press for the same reason the mutation could not
be caught through that path. Which is precisely why
`test_the_gateway_refuses_a_dead_token_presented_directly_to_it` presents a dead token to
`execute` itself, and why that test — not the UI — is what proves the refusal.

So the on-device claim is bounded, and stated as such: **the first press now spends its
approval**, proven by row 9 reaching `consumed` and row 48 recording `grant_consumed`. **The
refusal of a spent token is proven at the gateway, not on the device.**
