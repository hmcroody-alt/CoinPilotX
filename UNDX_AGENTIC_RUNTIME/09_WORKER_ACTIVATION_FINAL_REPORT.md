# UNDX Railway Worker — End-to-End Activation

**Stage 40 final report.** Mission: turn the verified durable worker substrate into a real
user-facing, worker-backed agent execution path.

---

## Verdict

**PASS on the read path, with two named limits.** A read submitted by a real user is now
queued, claimed by the worker, executed through the real gateway, settled honestly, and
retrieved by its owner and nobody else. The two limits are deliberate and were chosen by
the operator rather than discovered late: the write path is unproven because the write stop
stayed engaged throughout, and nothing has been pushed or deployed because the commit was
to be handed over rather than shipped.

The mission also uncovered and fixed a defect that made the entire feature a lie in
production. That is the substance of this report; the plumbing was already sound.

---

## The defect

Stage 25 ran a read-only QA pass end to end. The run queued, a worker claimed it, the
gateway executed it, rows came back — and the stored row said `failed`, with the client
projection reading **"This did not happen."**

Two independent faults compounded into that one sentence. Neither was reachable by any
existing test, and the reason they were not reachable is the more useful half of the
finding.

### Fault A — the settlement rule read a field that does not exist

`services/undx_agent_runs._settled_status` decided whether a finished run had succeeded
with:

```python
if bool(getattr(outcome, "may_claim_completed", False)):
```

`GatewayOutcome` has never carried `may_claim_completed`. Its `__slots__` are `receipt`,
`confirmation`, `result`, `verification`, `is_write`, which makes the absence permanent
rather than accidental. The `getattr` default therefore answered "not a success" for
**every run the queue ever settled** — writes as well as reads. No run of any kind could
reach `succeeded`.

This is the general argument against a defensive `getattr` across a contract boundary. It
converts a loud `AttributeError` into a quiet wrong answer, and here the quiet wrong answer
was the system telling people their completed work had failed.

### Fault B — a read was held to a write's evidence standard

Underneath sat a second fault that would have survived a naive fix. `may_claim_completed`
is a *write* predicate: it requires status `completed` **and** an independent read-back that
verified. A read-only capability declares no verifier, so `_verify` returns `impossible` and
the receipt truthfully records `impossible_to_verify`. No read can ever satisfy a write's
read-back requirement. Every capability in `WORKER_ELIGIBLE_CAPABILITIES` is a read.

So even with Fault A repaired, every worker-eligible run would still have settled `failed`.

### The measurement

`scripts/undx_read_settlement_probe.py` calls the real gateway on `activity.daily_summary`
and prints every field the settlement rule consults. It is a script rather than an argument
because the question was decidable in one run:

```
capability_id      : activity.daily_summary
risk               : read_only
is_write           : False
verifier           : ''   ('' means no read-back path)

receipt.status             : verified_success
receipt.verification_state : impossible_to_verify
receipt.may_claim_completed: False
outcome.succeeded          : False

hasattr(outcome, 'may_claim_completed'): False
GatewayOutcome.__slots__: ('receipt', 'confirmation', 'result', 'verification', 'is_write')
_settled_status(...) -> 'failed'
```

A row reading `status=failed` beside `outcome=verified_success` is not a cosmetic problem.
It is the audit trail disagreeing with itself, and `last_error` — the first field a support
engineer reads — held the word `verified_success`.

### The fix

One seam, chosen because it already promised the behaviour it failed to implement.
`GatewayOutcome.succeeded`'s docstring said "A read that answered from the account
succeeded"; the body returned `self.receipt.may_claim_completed`. The body now matches the
docstring:

```python
if not self.is_write:
    return self.receipt.status in AgentOutcome.COMPLETED
return self.receipt.may_claim_completed
```

The asymmetry can only widen the **read** answer. A write's answer is the receipt's answer,
unchanged, whatever the outcome and whatever the read-back said — swept over the full
`AgentOutcome` × `VerificationState` product in
`test_the_write_reading_is_untouched`. `succeeded` had zero production callers before this
change, so the blast radius is the settlement rule and nothing else.

`_settled_status` then reads `outcome.succeeded` as an attribute, so a future rename fails
loudly instead of silently answering "no".

### A third untruth, created by fixing the first two

Widening the read path made `RunStatus.COMPLETED` reachable for the first time — and its
stock sentence was **"Done, and confirmed by a separate read of your account."** No
read-only capability performs that read-back. The sentence had never been shown to anyone
because no run had ever reached `COMPLETED`, so it had never been wrong in practice. It
would have been wrong on the first successful read.

A completed read now says **"Done. This looked something up and changed nothing."**
Inventing a verification is the same class of false statement as the `failed` it replaced,
pointed the other way, and it is fixed in the same change for that reason.

### Why no test caught any of it

`test_run_lifecycle.py` and `test_worker_substrate.py` both stubbed the gateway with an
object built around `may_claim_completed` — the field the real outcome does not have. The
stub and the settlement rule agreed with each other about a name neither the real gateway
nor the real queue could exchange. Both suites passed throughout; production settled every
run as a failure.

`tests/undx_brain/test_evidence.py` synthesised receipts with `verification_state=VERIFIED`,
so it never exercised a read with `verifier=""` either.

Two closures, not one:

- `TheStubMatchesTheRealContract` (in `test_run_lifecycle.py`) asserts every field the stub
  offers exists on the real `GatewayOutcome`, and asserts `may_claim_completed` specifically
  does *not*. A stub is a claim about a contract, so the claim is now checked against the
  contract.
- `tests/undx_agent/test_run_execution.py` is new and contains **no stubs at all**: one real
  read, through the real gateway, settled by the real queue. Both faults *were* the contract
  being wrong, which is exactly what a stub cannot detect.

---

## Report fields

| Field | Value |
|---|---|
| STARTING_SHA | `922896a8938198eb60d06b7d9003422d81cea66c` |
| FINAL_SHA | **not created** — see *Commit* below |
| MAIN_SHA | `d9968ca2538c97c12b85a5df3827a5ac85f6386c` (`origin/main`) |
| WEB_SHA / WORKER_SHA / SHA_MATCH | `unknown` / `unknown` / `null` — the health surface reports SHA agreement, but both sides read `RAILWAY_GIT_COMMIT_SHA`, which is unset locally. Verifiable only after deploy. |
| WORKER_SERVICE | `coinpilotx-undx-worker` |
| WORKER_START_COMMAND | `python undx_worker.py` (Procfile `undx_worker`) |
| WEB_IMPORT_DEPENDENCY_REMOVED | **YES** — proven by denial, not by reading source |
| STRIPE_REQUIRED_BY_WORKER | **NO** |
| QUEUE_BACKEND | PostgreSQL (`undx_agent_runs`); SQLite locally. No Redis. |
| AGENT_RUN_API | `GET /api/undx/runs`, `GET /api/undx/runs/<run_id>`, `POST /api/undx/runs/<run_id>/cancel`, `GET /health/undx/runs` |
| ENQUEUE_CALL_SITE | `services/undx_agent_runtime.py` — the existing `/api/pulse-ai/message` path |
| FOR_USER_CALL_SITE | `services/undx_agent_run_routes.py` |
| RUN_OWNERSHIP | Enforced — a worker never reaches another account's run; cross-account retrieval 404s |
| CONFIRMATION_BINDING | Enforced — a run holding an unanswered confirmation is unclaimable |
| IDEMPOTENCY | Enforced — anchored on `run_id`, not on the attempt |
| WORKER_CLAIM | **PROVEN** — `attempt_count>=1` and `completed_at` set, via `poll_once`, not synchronous request execution |
| WORKER_RESTART | PROVEN — a crash between claim and settle repeats nothing |
| RAILWAY_REDEPLOY | **UNPROVEN** — nothing was deployed |
| APP_CLOSED_CONTINUATION | **UNPROVEN** — requires a live deploy |
| READ QA RUN | **PASS** — queued → claimed → executed through the real gateway → settled `succeeded` / `verified_success` → retrieved by owner only. `last_error` empty. Client status `completed`. |
| WRITE QA RUN | **UNPROVEN, not failed** — the write stop was engaged throughout by instruction (`writes_enabled: False`, `global_write_kill_switch: True`). Stages 26–29 are unproven for that reason and no other. |
| NEW_TESTS | `test_run_execution.py` (new, 8 tests / 40 subtests), `TheStubMatchesTheRealContract` (new class), plus the run/queue/worker/health suites: **143 passed** across seven files |
| NEW_REGRESSIONS | **0** |
| FOREIGN_LIVE_FILES_TOUCHED | **0** |
| COMMIT | **BLOCKED** — see below |
| PUSH | **NOT PERFORMED**, by instruction |
| DEPLOYMENT | **NOT PERFORMED**, by instruction |

### Regression measurement (Stage 36)

The rule is NEW FAILURE SET ⊆ BASELINE FAILURE SET. It holds with equality.

| Suite | Baseline | After | Failures |
|---|---|---|---|
| `tests/undx_agent` | 16 | 16 | identical set |
| `tests/undx_brain` | 7 | 7 | identical set |

The `undx_agent` set was re-measured rather than assumed, because production code changed
after the baseline was taken. It was then verified by reverting `services/undx_tool_gateway.py`
to `HEAD` and re-running the failing files: the same 16 failures appear with and without the
fix. They are five test IDs — twelve of the sixteen are subtests of one — and they split into
two pre-existing causes, neither related to settlement:

- `pulse_saved_collections` has no `description` column in the test fixture
  (`test_content_graph_intelligence_pack`, `test_saved_post_write_pack` ×3)
- citation drift in `test_knowledge_map_grounding` (12 subtests)

### Stage 34 — clean-checkout import

`scripts/undx_clean_checkout_import_proof.py`: **PASS**, 4753 files, 0 untracked excluded.

```
PASS imported 5 modules from a clean tree with bot, stripe, flask denied
  ok undx_worker
  ok services.undx_worker_runtime
  ok services.undx_agent_runs
  ok services.undx_mission_runtime
  ok services.undx_run_health
```

Run against the *deployed* file set, not the dirty local tree, and with `bot`, `stripe` and
`flask` denied at import time — so this is proof by denial rather than by inspection. A
dirty-tree pass with a clean-checkout failure is the failure mode this exists to catch.

---

## Commit — blocked, and what to run

**I could not create the commit.** Two filesystem guards in this environment block it:

1. `.git/index.lock` exists (0 bytes, stale) and cannot be removed — `rm` returns
   `Operation not permitted`.
2. `git add` cannot write to `.git/objects` — every blob write returns
   `unable to unlink '.git/objects/../tmp_obj_*': Operation not permitted`, so nothing stages.

Everything else was verified against a simulated post-commit tree using an alternate index
and `git add -N`, which records paths without writing blobs. That is why Stage 34 could be
proven despite the block.

To make the commit yourself:

```bash
cd ~/Desktop/CoinPilotX
rm -f .git/index.lock
git add bot.py services/ tests/undx_agent/ undx_router.py undx_worker.py \
        scripts/undx_read_qa_run.py scripts/undx_read_settlement_probe.py \
        scripts/undx_clean_checkout_import_proof.py \
        mobile-native/src/api/undxRuns.ts \
        mobile-native/src/api/__tests__/undxRuns.test.ts \
        UNDX_AGENTIC_RUNTIME/ UNDX_CAPABILITY_PLANNER_REPORT.md
git status                     # review before committing
git commit -m "UNDX durable agent runs: worker-backed read path, run API, settlement fix"
```

Review `git status` before committing. The working tree also carries changes from the
capability-planner mission (`services/undx_capability_planner.py`,
`services/undx_flag_diagnostics.py`, `services/undx_response_intelligence.py` and their
tests) that predate this one; the `git add` above includes them because they are entangled
in the same directories, and you may prefer to split them out.

**Do not push.** That was your instruction and it stands.

### `bot.py` — three lines

The mission's standing rule is PREFER ZERO EDITS to `bot.py`. Three additive lines proved
unavoidable, all in the existing `_load_route_pack` call list, each registering one route
pack behind its own flag:

```python
_load_route_pack("undx_agent_runs", "services.undx_agent_run_routes")
_load_route_pack("undx_agent_run_control", "services.undx_agent_run_control_routes")
_load_route_pack("undx_run_health", "services.undx_run_health_routes")
```

Three packs rather than one, deliberately. The read pack is asserted GET-only over the URL
map; registering the cancel POST alongside it would retire that proof rather than fail a
test. The health surface is unauthenticated by design and is kept out of both so neither
loses its "every route here is owner-scoped" guarantee.

---

## Outstanding

- **Stages 26–29 (write, app-closed, restart, redeploy QA)** — UNPROVEN. They need the
  write stop released and a live deploy. Neither was in scope by your decision.
- **`undx.run.status.*` i18n keys do not exist in any catalog.** The run-status vocabulary
  is 12 statuses; the client currently renders English from `status_detail`. Adding the keys
  belongs to whoever builds the run-status screen, and must land in all 12 locales at once
  or CI fails on the gate.
- **SHA_MATCH is unverifiable locally.** The health surface reports it correctly; both sides
  read `RAILWAY_GIT_COMMIT_SHA`, unset outside Railway. Check it after the first deploy —
  a mismatch there means web and worker are running different code, which is the specific
  condition that makes a queue behave inexplicably.
