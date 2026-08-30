# 08 — UNDX Railway agentic worker: durable agent runs

Continues `07_RAILWAY_WORKER_RECON.md`, which decided to reuse `coinpilotx-undx-worker`
rather than create a second Railway service. This report covers what was built on top of
that decision, what it deliberately does not do, and what remains open.

## What was actually missing, and what now exists

Recon found the blocking gap was not the worker and not the queue. Both already existed.
It was the **envelope**: a queued action carried no owner-bound approval, no resolved
arguments and no idempotency key, so `undx_mission_runtime.advance_claimed` refused
`call_tool` nodes with `governed_tool_requires_request_context`. That refusal was a policy
the worker applied to itself, not a technical wall — `undx_tool_gateway.execute` imports no
Flask, reads no `request`, and takes a plain cursor as its first argument.

`services/undx_agent_runs.py` (555 lines, new) is that envelope. It stores what a person
already approved and hands it back to the unmodified gateway later.

## The design decision that shapes everything else

**Confirm before queueing.** The HTTP request resolves the target deterministically from
the person's own words, mints the approval, and the person confirms in-app. Only then does
the run become worker-managed.

The consequence is the property worth having: **the worker can never choose a row.** It
executes a decision, not an intention. `enqueue` refuses outright without a
`confirmation_id` — that is the module's single invariant, and it is the only condition it
raises on that is not an input-shape complaint:

```
unconfirmed_run        no confirmation id            → refuse, write nothing
unauthenticated        no owner                      → refuse
unsupported_capability capability not in registry    → refuse
missing_request_id     no client request id          → refuse
arguments_too_large    over 8000 bytes               → refuse
```

Resolution stays in the request, where the person's words are. This is the same constraint
the capability planner was built under: the model may name the action, never the row.

## No second authority system

The mission forbids the worker growing its own policy. It did not. `undx_agent_runs.py`
contains **no policy decisions at all** — no allowlist check of its own, no ownership
predicate of its own, no idempotency logic of its own. It calls one function:

```python
outcome = undx_tool_gateway.execute(
    cur, user_id=..., capability_id=..., proposed_arguments=arguments,
    request_id=run_id, task_id=run_id,
    client_request_id=..., correlation_id=...,
    confirmation_id=confirmation_id,      # the id, never a token
    explicit_request=True,
    target_chosen_by_agent=False,
)
```

Every one of the gateway's nine ordered checks — authentication, capability allowlisting,
schema validation, ownership scope, deterministic policy, confirmation, idempotency, ledger
reservation, executor and read-back — runs exactly as it does in a request. The gateway was
not modified. A test monkeypatches `execute` and asserts the call shape directly, including
that `target_chosen_by_agent` is `False`, that `explicit_request` is `True`, and that no
bearer token appears in the arguments.

**No credential at rest.** The table stores the confirmation *id*, never the token. The ID
route (`consume_approval`) applies identical scope, predicate and argument bindings without
requiring a secret, so nothing is lost by storing the weaker reference.

## Claiming: exclusive, leased, bounded

Compare-and-swap on the row's own `updated_at`:

```sql
UPDATE undx_agent_runs SET status='running', lease_owner=?, lease_expires_at=?,
       attempt_count=attempt_count+1, updated_at=?
 WHERE run_id=? AND status=? AND updated_at=?
```
followed by `cur.rowcount == 1`. Two containers racing one row produce exactly one winner.

This is deliberately **not** `FOR UPDATE SKIP LOCKED`. SQLite has no such clause, so a claim
path built on it would have its race exercised for the first time in production. The CAS
path behaves identically on SQLite locally and PostgreSQL in production, which means the
tests above are testing the thing that ships.

Bounds are fixed at enqueue and never escalated at runtime. A run at or over `max_attempts`
is dead-lettered **before** another execution is attempted, not after — so a crash loop
costs attempts, not repeated executor calls. An unreadable or absent lease counts as
expired; a live one is skipped. The claim is committed before execution begins, so a
container dying mid-run cannot roll the claim back into an unbounded retry.

## Failure is refusal

`execute_claimed` refuses on `lease_not_owned`, `arguments_unreadable` and `unconfirmed_run`
before reaching the gateway. Unreadable arguments fail rather than defaulting to `{}` — a
test asserts this specifically, because defaulting an unreadable payload to empty is how a
scoped action becomes an unscoped one. A typed `AgentError` settles the run `failed`; an
untyped exception returns it to `queued` with the lease released, since an unknown failure
is not evidence the run is bad. Success settles on `outcome.may_claim_completed` rather than
a recomputed status string, so the run's verdict is the gateway's verdict.

A lapsed confirmation returns `CONFIRMATION_REQUIRED` and forces `failed`. Confirmations
live 300s; the worker's short-sleep-when-not-drained means a confirmed run is claimed within
roughly 1–60s. If it lapses anyway, failing closed is correct, and a test asserts it.

## Landing behind the write stop

Production carries `global_write_stop=true` and `writes_enabled=false`. This work ships
behind that stop, deliberately. `UNDX_AGENT_RUNS_ENABLED` defaults to `0` and is
`fail="closed"`. `surface()` resolves in this precedence:

```
UNDX_EMERGENCY_KILL_SWITCH               → emergency_kill_switch
UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION    → dynamic_limit_escalation_is_unsafe
not UNDX_WORKER_ENABLED                  → worker_disabled
otherwise                                → agent_runs_disabled
```

Two tests cover the stop rather than assuming it: with the flag off nothing is claimed, and
during an emergency stop the row keeps `attempt_count == 0` and `status == 'queued'` — a
halt must not silently consume the run's budget.

Three flags were added to the `CATALOG` in `services/undx_brain/config.py`, resolving to
`False`, `120`, `3`.

## Worker wiring

`undx_worker.py` gained 26 lines. Runs are polled after missions and in their **own** `try`:

> the two are independent kinds of work and a failure in one is not evidence about the
> other. Folding them together would mean a mission-storage error silently stopping the
> execution of actions a person already approved.

The loop's sleep became adaptive — `max(15, sleep_seconds)` when drained, `1` second when
the last pass did work. That is a poll interval, not a rate limit: the queue is already
bounded by one run per pass, by per-run attempt caps, and by the fact that every run in it
was individually approved by a person. The start command is unchanged.

## Verification

```
tests/undx_agent/test_agent_runs.py     23 passed
tests/undx_agent (with new file)        1113 passed, 4154 subtests, 16 failed
tests/undx_agent (without new file)     1090 passed, 4154 subtests, 16 failed
```

The delta is exactly 23 — the new file — and the 16 failures are the recorded pre-existing
baseline, matched by identity and not merely by count: `test_content_graph_intelligence_pack`
×1, `test_saved_post_write_pack` ×3, and 12 subfailures in `test_knowledge_map_grounding`.
No regressions.

Realtime-audio gate: my five files match **none** of the 59 protected patterns, verified
programmatically against `config/realtime-audio-protected-paths.json`. The gate does report
`mobile-native/src/screens/LiveHostSessionScreen.tsx` between `origin/main` and `HEAD` —
that is commit `922896a8`, another agent's live-replay work, committed while this mission
was in progress. It is not mine and its declaration is already accepted.

## Deferred, with reasons

**The request-side call site does not exist yet.** Nothing calls `enqueue` after a person
confirms, and no API surface exposes `for_user`. The durable substrate is complete and
tested; the two ends that connect it to a human are not built. The target flow — ask, close
the app, reopen and find the verified result — is therefore **not yet reachable end to end**.
This is the single largest honest gap in this report.

**Stage 38: `import bot` still boots the web app inside the worker.** Worker logs show
`CoinPilotX web boot starting` → `PORT= 8080` → `DB_INIT_STARTED_ONCE` → `web boot complete`
before `UNDX_WORKER_START`. New evidence this session: `import undx_worker` fails locally
with `ModuleNotFoundError: No module named 'stripe'`, reached through `bot.py:22`. The
worker cannot be imported without the web service's entire dependency set. Fixing it means
editing `bot.py`, which held foreign uncommitted work throughout the implementation window
and sits in the DO-NOT-TOUCH zone. Deferred to keep the blast radius honest, not because it
is acceptable.

**Divergent flag surfaces.** The worker lacks `UNDX_HTTP_RUNTIME_ENABLED`,
`UNDX_NATIVE_CONTEXT_*`, `UNDX_V5_*`, `UNDX_HEALTH_ENDPOINT_ENABLED`, `UNDX_CONFIG_VERSION`
and `UNDX_CAPABILITY_PLANNER_ENABLED`, all of which the web service has. Two processes that
must reach identical decisions do not currently see identical inputs. Unchanged by this
mission.

**Carried from report 03, still open:** no dead-letter collection for *missions* (runs now
have one); `attempt_count` in `undx_mission_runtime` is recorded but never consulted, so a
mission blocked for a persistent reason is re-claimed each cycle once its lease expires.

**Housekeeping:** `services/undx_flag_diagnostics.py` remains untracked while its `bot.py`
caller is committed. Committed code imports an uncommitted module. It degrades inside a
`try/except` rather than breaking `/health/undx`, but it belongs in the next commit.

## Report fields

```
STARTING SHA        d9968ca2538c97c12b85a5df3827a5ac85f6386c
FINAL SHA           922896a8938198eb60d06b7d9003422d81cea66c  (foreign commit; this
                    mission's work is uncommitted in the working tree)
BRANCH              release/full-sweep-20260826
RAILWAY PROJECT     coinpilotx-alert-worker (111b3838-09d4-4f13-8b8b-6ed332bad06f)
WORKER SERVICE      coinpilotx-undx-worker (c2c9b804-3e7e-4a5d-9061-54542a5f7d89)
EXISTING WORKER     YES — python undx_worker.py, deployed, healthy
REUSED              YES — no new Railway service created
OLD START COMMAND   python undx_worker.py
NEW START COMMAND   python undx_worker.py   (unchanged)
QUEUE BACKEND       PostgreSQL — undx_agent_runs, compare-and-swap claim,
                    lease_owner + lease_expires_at, UNIQUE(user_id, client_request_id)
REDIS REQUIRED      NO — no Redis service exists in the project and REDIS_URL is absent
                    from every service environment
NEW AUTHORITY       NONE — single call into unmodified undx_tool_gateway.execute
WRITES ENABLED      NO — landed behind global_write_stop, UNDX_AGENT_RUNS_ENABLED=0
FILES ADDED         services/undx_agent_runs.py (555)
                    tests/undx_agent/test_agent_runs.py (463)
FILES MODIFIED      undx_worker.py (+26), services/undx_brain/config.py (+18)
PROTECTED PATHS     none touched (verified against all 59 patterns)
TESTS               23 new, all passing; baseline failure set matched by identity

FINAL VERDICT       PARTIAL
```

**Why PARTIAL and not PASS.** The foundation the mission asked for is built, tested and
safe: durable, leased, bounded, resumable, reusing the one authority chain, executing only
what a person confirmed. But the mission's stated target is a round trip — ask UNDX, close
the app, reopen, find the verified result. Without the enqueue call site and the read API,
that round trip cannot be demonstrated. A verdict of PASS would be claiming a flow that has
never run. The remaining work is small and well-defined; the honest label until it exists
is PARTIAL.
