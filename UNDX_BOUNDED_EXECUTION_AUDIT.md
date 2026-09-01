# UNDX — Bounded-Execution Audit

**Mission C.** Read-only. No edits, no commits. Scope: every loop, ceiling and retry path
that decides how long a UNDX turn may run, and whether anything at runtime can make it
run longer.

## Correction to the first version of this report

The first draft of this document was written from targeted greps and claimed a strong
negative result: *nothing in the UNDX subsystem can widen an execution limit at runtime.*
Complete reads of all ten mandated files have **falsified that claim**. Three
caller-widenable limits exist. The grep found none of them, because none is written in a
shape a `+=` or `*=` sweep can see — each is a `max(FLOOR, min(caller_value, CONSTANT))`
that simply omits the *configured* value from the `min`. The sweep was sound and the
conclusion drawn from it was not, which is the whole reason the mandate said to read the
files.

A second correction, to Part 5's `UNDX_LIMIT_ESCALATION_AUDIT.md`: it states `render()`
"can build up to forty-four drafts" from "eleven framings". Both numbers are wrong. The
widest `_lead_forms` branch is the read branch at five forms
(`undx_response_intelligence.py:1886–1911`, where `:1891`/`:1893` are mutually exclusive),
and `_ORDERS` holds four orderings (`:2009–2014`). The true maximum is **20**. Part 5's
conclusion survives — the sentinel 64 is still above the real search space — but the
figure it was argued from was never checked.

## Escalating limits — the finding the first pass missed

**1. A caller can exceed the operator's character budget. `undx_brain/knowledge.py:400`**
`retrieve` clamps a caller-supplied `limit` correctly at `:398`:
`max(0, min(int(limit), MAX_RESULTS, applied_limit))` — the configured value is in the
`min`. The very next branch does not: `applied_chars = max(200, min(int(char_limit),
MAX_CONTEXT_CHARS))`. `applied_chars` is absent. An operator who sets
`UNDX_KNOWLEDGE_MAX_CONTEXT_CHARS=500` is silently overridden up to 6000 by any in-process
caller. The comment at `:393–397` states the governing policy in full — *"may lower the
configured one and may not raise it"* — and the two lines beneath it break that policy for
the sibling field. Only the module constant still binds. **Latent:** no production caller
passes `char_limit`; the only callers are tests.

**2. A budget with no ceiling of any kind. `undx_agent_runtime.py:2660`**
`recent_replies(..., limit: int = 5)` passes `max(1, int(limit))` straight into a SQL
`LIMIT ?`. This is not an omission from a `min` — there is no `min`, no module constant,
no configured value. Each returned row is then expanded to 4000 characters (`:2667`). The
real ceiling is applied downstream and after the fact, by
`undx_tool_gateway.py:689` slicing `[-HISTORY_WINDOW:]` on the already-fetched list.
`recent_replies` is public (`__all__`, `:3351`). **Latent:** the request path passes the
default (`:2869`).

**3. The render budget is a default, not a ceiling. `undx_response_intelligence.py:2384`**
`render(..., attempts: int = MAX_RENDER_ATTEMPTS)` consumes it as
`len(candidates) >= max(1, int(attempts))`. No `min` against `MAX_RENDER_ATTEMPTS`
anywhere. The constant documented at `:63–65` as the thing that makes a repetitive turn
terminate is only a default value. Impact is capped by the 20-draft search space and every
extra candidate has already passed `validate_consistency`, so this is a discipline defect
rather than a live hazard. **Latent:** no caller passes `attempts`.

## Other findings

| Finding | Location | Assessment |
|---|---|---|
| `UNDX_SOURCE_CORPUS_MAX_RECORDS` has no module-constant clamp; `config.py` permits 200,000 against a default of 5,000 | `corpus.py:475, 483, 549` | Environment-raisable 40×. The only ceiling in the subsystem an operator can widen |
| Regeneration budget off by one — `rejected > budget` permits N+1 rejected drafts; `0` permits one | `undx_response_intelligence.py:2350, 2353` | Configuration exceeded by exactly one. Mine, from Part 5 |
| Recursion with no depth limit and no visited set, over tool-supplied payload | `undx_response_intelligence.py:1161–1169` | The only loop in the subsystem whose depth is data-driven. `RecursionError` inside `build_plan` |
| Per-id database fan-out with no cap — ~2,000 owner-scoped reads in one turn — while the sibling branch of the same function caps at `_MAX_REFERENCE_SCAN = 50` | `undx_agent_runtime.py:508` vs `:384, 534` | The cap exists and is not applied to this branch |
| Memory retrieval has no volume ceiling: `fetchall()`, no required `LIMIT`, no row or character cap | `undx_brain/memory.py:389` | Isolation is enforced rigorously; volume is an axis the module was never built to hold |
| `MAX_EXECUTION_SECONDS` is measured *after* the executor returns and only tags `slow_execution`. No signal, no thread pool, no `timeout=` in `services/undx_*.py` | `undx_tool_gateway.py:529` | A stopwatch, not a limit. Confirmed against a full read |
| `MAX_PLAN_STEPS`, `MAX_TOOL_CALLS`, `MAX_RETRIES`, `CONFIRMATION_TTL_SECONDS` have no reader anywhere | `undx_agent_contracts.py:252–258` | Three are shadowed by the Brain's enforced equivalents; TTL is enforced via a duplicated literal at `undx_architecture.py:830`. Confirmations do expire |
| `staleness_report(limit=0)` — default 0 means *no* bound | `corpus.py:769, 785` | Hashes the whole corpus |
| `_checkpoint` is a bare `commit()` deferring its bound to a database busy timeout that is never set here | `undx_tool_gateway.py:313` | Unbounded wait |

## Two claims checked and refuted

A parallel reader reported that an unvalidated `confirmation_token` disables the
question-framed-write guard via `chosen_by_caller` (`undx_agent_runtime.py:3293`). The
guard does step aside — but a presented token is redeemed unconditionally at
`undx_tool_gateway.py:764–777`, and a token that cannot be redeemed refuses the call. A
garbage string does not reach the executor. **Not a bypass.**

The write-retry guarantee **holds**. `_run_executor` has exactly one call site
(`undx_tool_gateway.py:869`), no loop, no recursion. `retryable` is a label consumed once
at `:565` to choose an outcome name. Nothing re-enters execution. The residual worth
naming: an exception raised *after* a partial mutation sets `retryable=True`, and
`:628–629` then tells the person it is worth trying again — the gateway never retries a
write, but it invites the human to, on a write whose outcome is unknown.

## Loops

No loop in the subsystem exits only on work completing. `execution.py:314` is the
strongest shape present — four exits, two enforced by the monotonic `Ledger`.
`attention.py:752` spends a decrement-only counter. The remaining `while` loops terminate
by strict advance over clamped input (`undx_agent_runtime.py:462`,
`undx_response_intelligence.py:2198`) or by fixpoint over a static constant
(`undx_knowledge_map.py:256`). The single recursion above is the one exception.

## Disclosure

`PROFILES`, `profile()`, the `prompt_block` omission notice, the shared `render_line` cost
model and the two newly-wired ceilings were authored in this session under Mission A
Part 5, minutes before this audit began. They are this-session changes, not discovered
pre-existing properties — and finding 3 and the off-by-one above are defects in that work.
