# 01 — Local vs production parity

## The question

The local suite executes the mission's acceptance sentence end to end and verifies the
write against `pulse_reactions`. Production does not act. Phase 2 asks for the cause to
be *proved*, not guessed.

## What was ruled out, with evidence

**The fix is not missing from the deployable branch.** `HEAD` is `b076ef32`
("fix(undx): let a typed approval resume the action it approved", 2026-08-28 23:48),
the working tree is clean, and `git rev-list --left-right --count origin/main...HEAD`
returns `0 0`. The commit that added typed-approval resumption is on `origin/main`.

**The route exists and the native client calls it correctly.**
`POST /api/pulse-ai/message` is registered in `pulse_communications_v2/routes.py:629`
and loaded by `bot.py:1247`. The app calls it at `messenger.ts:578`, and
`ChatScreen.tsx:665` reads `data.response_components`, which is where the action card
arrives. `ChatScreen.tsx:353` posts the token back to `/api/pulse-ai/actions/confirm`.
The client wiring is complete in both directions.

**Pending-action state is already durable and already shared.** Grants live in
`pulse_ai_confirmations` in the application database, not in process memory. A second
container can serve the "Yes". No Redis is involved and none is required. This is
Phase 3's requirement, and it was met before this mission started.

**No worker is on the path.** The acceptance flow completes inside one HTTP request
with no worker process running at all (measurement below). A missing or stale
`undx_agent_worker` cannot be the cause, because there is no such process and none is
needed.

## The measurement

`scripts/undx_production_gate_probe.py` holds the code constant at `b076ef32` and
varies only the environment — the one axis that differs between a green test run and a
live container. Each row runs "Like my most recent post." then "Yes" against a real
temporary SQLite database with two owner-scoped posts seeded, and records what a person
would see.

| Environment shape | Turn 1 | Turn 2 | Post liked |
| --- | --- | --- | --- |
| fully enabled, caller in cohort *(the local test's shape)* | `confirmation_required`, card names the post | `verified_success` | **yes** |
| master flag `UNDX_AGENT_ENABLED` unset | not handled | not handled | no |
| enabled, caller outside `UNDX_AGENT_QA_USER_IDS` | not handled | not handled | no |
| cohort set under the retired `UNDX_V5_QA_USER_IDS` only | not handled | not handled | no |
| `UNDX_EMERGENCY_KILL_SWITCH` set | not handled | not handled | no |
| reads on, `UNDX_AGENT_WRITES_ENABLED` unset | `permission_denied` — "UNDX is currently read-only" | not handled | no |
| `UNDX_AGENT_DISABLE_WRITES` set | `permission_denied` — "UNDX is currently read-only" | not handled | no |
| `UNDX_AGENT_REQUIRE_VERIFICATION=0` | `permission_denied` — "UNDX is currently read-only" | not handled | no |
| allowlist omits `feed.posts.like` | `permission_denied` — "UNDX cannot do that right now." *(was `clarification_required`; see below)* | not handled | no |

"Not handled" is `undx_agent_runtime.available()` returning `False` at
`pulse_ai_service.py:724`, before any agent work happens. The turn falls through to
ordinary conversation. The person sees an assistant that talks fluently and never acts
— which is the reported production symptom, reproduced exactly.

## Root cause

**The production failure is a configuration state, not a code defect.** The code at
`b076ef32` executes the acceptance sentence correctly and verifies the write. Four
environment shapes reproduce the reported symptom identically, and the code cannot be
made to fail this way by any means other than one of them.

The mission is explicit that if only deployment or configuration changed, no source
changes should be manufactured. So none were made to the runtime. The defect that *is*
in the source is a different one, and it is the reason this took a mission to find:

**Those four shapes are indistinguishable from every surface the server publishes.**
`/health/undx` reports `qa_cohort_configured` — true whenever the list is non-empty,
regardless of who is in it. A deployment with the master flag on and a populated cohort
that happens to omit the tester reads as fully healthy on every endpoint, while every
request that tester makes is silently skipped. There was no way, from inside or outside
the running process, to ask *is the agent on for this account*.

That is the real finding, and it is a genuine infrastructure gap rather than a
misconfiguration: an agent platform whose availability cannot be interrogated per
account will lose a debugging session to this every time it happens.

## What was built

`pulse_ai_service.status(user_id)` now returns an `agent` block, surfaced at the
already-authenticated `GET /api/pulse-ai/status`:

```json
{"agent": {"available": false,
           "writes_available": true,
           "reads_available": true,
           "reason": "this account is not in the agent cohort"}}
```

Design points, each of which is a test in
`tests/undx_agent/test_availability_diagnosis.py` (8 tests, all passing):

- **The reported reason is the gate that actually closed.** Probes are consulted in
  the same order `undx_agent_policy.user_enabled` consults them, so a deployment-wide
  cause outranks a per-account one. Without that ordering, a master flag that is off
  would report "not in the cohort" and send an operator to edit a list that is already
  correct.
- **Read-only is not reported as unavailable.** A rollout with writes deliberately dark
  is a state somebody chose. Collapsing it into `available: false` would start a hunt
  for a broken cohort.
- **The cohort is never echoed.** A list of privileged account ids is not something an
  availability probe hands out.
- **It cannot take the endpoint down.** Any exception degrades to
  `reason: "unavailable"`, because a status endpoint is what people reach for when
  other things are already failing.

`/health/undx` was deliberately not extended: it is unauthenticated so that it can be
read when sign-in is broken, and per-account membership cannot be answered there
without either accepting an arbitrary user id or leaking the cohort.

## What is still open

**I cannot read the live Railway environment from here**, so I can prove which
configurations cause the symptom but not which one is currently live. That is now a
one-request question rather than a forensic one:

1. Sign in on the affected account and `GET /api/pulse-ai/status`. Read `agent.reason`.
2. If it says the account is not in the cohort, check whether the cohort is set under
   `UNDX_V5_QA_USER_IDS` — nothing in the runtime reads that name; it survives only in
   `scripts/undx_railway_variable_audit.py` as a known equivalent of the live
   `UNDX_AGENT_QA_USER_IDS`.
3. `GET /health/undx` and compare `sha` and `pid` against the expected deploy. A stale
   process that survived a restart is a real failure mode this codebase has hit before
   — that is why `pid` and `started_at` are in that payload.
4. `GET /health/routes` to confirm `pulse_communications_v2` registered.

Note that step 1 requires this change to be deployed. Until then, `/health/undx` will
answer everything except cohort membership.

## Suite state

`tests/undx_agent`: 888 passed, 3127 subtests, 16 failed — the same 16 as the
pre-existing baseline (`test_content_graph_intelligence_pack` ×1,
`test_saved_post_write_pack` ×3, 12 subfailures in `test_knowledge_map_grounding`),
plus the 8 new tests. No regressions.

## Defect found in passing — now fixed

When `UNDX_AGENT_ENABLED_CAPABILITIES` omitted `feed.posts.like`, the user was told
*"Which post? Tell me its number, or open it and ask again."* — a rollout state
reported as an ambiguous target. See `02_CAPABILITY_WITHDRAWAL_REPORT.md`. That row
of the table above now reads `permission_denied` — "UNDX cannot do that right now."
