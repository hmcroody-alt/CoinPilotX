# UNDX — Second-Definition-of-Success Audit

**Mission A, Part 4.** Scope: every location in the UNDX subsystem that decides, or
appears to decide, whether a turn may tell a person their change is done.

## The canonical authority

Completion is licensed by one rule with two halves, both required:

`AgentReceipt.may_claim_completed` (`services/undx_agent_contracts.py:670`) — a status in
`AgentOutcome.COMPLETED` **and** a verification state of `verified` — conjoined with
`evidence.derive(...).may_claim_done` (`services/undx_brain/evidence.py`), the Brain's
independently written resolution of the same pair. The conjunction lives at
`GatewayOutcome.may_claim_done` (`services/undx_tool_gateway.py:157`).

A conjunction can only narrow. That asymmetry is why the rule runs unflagged: a defect in
either half can withhold a claim the system was entitled to make, and never manufacture
one it was not.

Everything else may report that a service accepted a call, that an operation was
attempted, that execution returned, that verification is pending, or that verification
failed. Nothing else may reach the word "done" by its own arithmetic.

## Findings

| Location | Current success definition | Canonical? | Risk | Action |
|---|---|---|---|---|
| `undx_agent_contracts.py:670` `AgentReceipt.may_claim_completed` | `status in COMPLETED and verification_state == VERIFIED` | **Canonical (half 1)** | — | Unchanged. Pinned by `TheCanonicalRuleIsTheOnlyRule`. |
| `undx_brain/evidence.py` `derive().may_claim_done` | State machine over the same two facts | **Canonical (half 2)** | — | Unchanged. |
| `undx_tool_gateway.py:157` `GatewayOutcome.may_claim_done` | Conjunction of the two halves, with divergence logged | **Canonical (the authority)** | — | Unchanged. |
| `undx_tool_gateway.py:106` `GatewayOutcome.succeeded` | `receipt.may_claim_completed` alone | Not a completion claim | Low — deliberately a different question ("did the call do what it was asked"), documented as such, and a successful *read* is meant to satisfy it | Left alone. Renaming it would be churn; the docstring already says why the two names are kept apart. |
| `undx_response_intelligence.py:1013` `_action_state_for` | **was** `status == VERIFIED_SUCCESS` alone | **No — half the rule** | **High.** `plan.action_state` is what `_lead_forms:1736` keys "Done — …, and I read it back from PulseSoc to confirm it" off. Any completed status without a read-back rendered that sentence. Reachable through the gateway's idempotent replay. | **Fixed.** Now also requires `verification.state == VERIFIED`, else `DEGRADED`. This layer can now only agree with the receipt or be more cautious, never less. |
| `undx_tool_gateway.py:206` `_receipt` undo gating | **was** `status == VERIFIED_SUCCESS` alone | **No — half the rule** | **High.** Undo is itself a write, aimed at state whose value is in doubt. The highest-consequence affordance had the weakest definition behind it. | **Fixed.** Gate is now the receipt's own two-condition rule. |
| `undx_tool_gateway.py:787` idempotent replay | `prior_status == "verified"` → `VERIFIED_SUCCESS` with `verification=None` (→ `IMPOSSIBLE`) | **No — status and verification disagreed** | **High.** Receipt said "cannot claim done"; prose said "I had already done that"; undo was offered. Three readers, three answers. | **Fixed.** A ledger `verified` is a read-back that was genuinely taken and recorded, so it is carried forward as a real `VerificationResult`. `ok` is deliberately *not* — it stays `IMPOSSIBLE`, and the prose guard now catches its sentence. |
| `undx_agent_runtime.py:2950` metacognitive self-check | **was** a local tuple `(" completed", " is paused", " is active", " was deleted")` | **No — a private vocabulary** | **High, and wrong in both directions.** It missed every sentence the system actually writes to claim completion ("Done — …", "I confirmed this against your account", "the change went through", "I had already done that"), and it matched bare state descriptions a *read* is entitled to make — `" is paused"` would have rewritten "your BTC alert is paused" into a non-answer. | **Fixed.** Replaced by `undx_response_intelligence.completion_claim()`, the single reader of `_COMPLETION_CLAIM_PATTERNS`, which `validate_consistency` already used. One list, one reader. |
| `undx_response_intelligence.py:397` `_COMPLETION_CLAIM_PATTERNS` | The claim vocabulary | Canonical (subordinate to the rule above) | Medium — two genuine near-misses found | **Extended.** Added the plural (`are now off`, not only `is now off`) and the replay sentence (`I had already done that`). |
| `pulse_ai_service.py:1302` `_agent_confirm_payload` | **was** `receipt.may_claim_completed` alone | **No — half the conjunction** | Medium. The HTTP boundary had a wider definition than the sentence in the same body, so a turn the Brain had rejected could leave as `ok: true`. | **Fixed.** Now `outcome.may_claim_done`. |
| `pulse_ai_service.py:1520/1556` `_confirm_action` legacy branch | `verified = actual == proposed`, computed locally; emits `"status": "verified_success"` and `"verified_success_card"` | **No — a complete parallel executor** | Medium **because dormant**. Never builds an `AgentReceipt`, never calls the gateway. Reachable only when `_agent_confirm` declines the token **and** `UNDX_V4_ACTIONS`/`UNDX_V5_NOTIFICATION_ACTIONS` are on — both default closed, and the file's own comment records they are off in every environment the agent runs in. | **Pinned, not rewritten.** Three tests assert the agent path is consulted first, returns before this branch can run, and that the flags default closed. Rewriting a dormant legacy executor to route through the gateway would be building a system to make an audit look productive; what the audit owes is a test that fails the day it stops being dormant. |
| `undx_architecture.py:747` `record_tool_result` `verified` | `success and approved and observed_ok` | Not a claim to the user — the ledger's own verdict | Medium, and newly load-bearing. `observed_ok` falls back to a *structural* heuristic ("was it a POST, did an id come back") when `canonical_verified` is omitted, which could file an unread write as `verified`. Since the replay path now carries a ledger `verified` forward, an invented verdict would become a completion claim on a later turn. | **Pinned.** Every current call site passes `canonical_verified` explicitly, and the gateway's value is exactly `verification.state == VERIFIED`. An AST scan now fails if any call site omits it. |
| `undx_agent_tools.py` executors (`ToolResult(ok=True)`) | "the call returned without error" | Correctly weaker | — | No change. This is the honest thing for an executor to report. |
| `undx_operator.py`, `build_card` (`runtime:1819`) | Read from the receipt | Correct | — | No change. |

## Not examined

`undx_worker.py`, `undx_execution_kernel.py`, `undx_desktop_connector.py`. None appeared
in the grep for `may_claim`, `verified_success` or `VERIFIED_SUCCESS`, but absence from a
keyword sweep is weaker evidence than a read, and this is recorded as an open edge rather
than as a clean result.

## Drift tests

`tests/undx_agent/test_one_definition_of_success.py` — 17 tests in five classes:

`TheCanonicalRuleIsTheOnlyRule` asserts each half is insufficient alone, that both
together suffice, and — over the full cross product of nine outcomes × four verification
states × read/write — that `may_claim_done` never reaches `True` where the receipt said
no.

`NoModuleDefinesSuccessForItself` pins the four repaired locations against the specific
expression each used to contain.

`TheClaimDetectorRecognisesTheSystemsOwnVocabulary` runs eight real completion sentences
and seven honest non-claims through the detector, including the specific false positive
the old tuple had, and asserts the pattern list has not grown a second reader.

`TheLedgerVerdictIsNeverInventedStructurally` walks the AST of every module in `services/`
and fails on any `record_tool_result` call that omits `canonical_verified`.

`TheDormantParallelExecutorStaysDormant` asserts the ordering and the closed-by-default
flags that keep the legacy branch unreachable.

## Suite state after the change

`tests/undx_agent` 781 passed (764 before, +17). `tests/undx_brain` 837 passed. No
regressions.
