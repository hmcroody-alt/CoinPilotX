# 07 — Railway agentic worker: recon and the reuse decision

Stages 0–2 of the UNDX Railway agentic worker mission. No code was changed to produce
this. The mission gates implementation on it: *do not create a duplicate worker until
existing Railway services are audited.*

## Decision

**Reuse `coinpilotx-undx-worker`. Do not create a second worker service.**

It already runs `python undx_worker.py` on `main`, already claims work under durable
PostgreSQL leases, already heartbeats into the health surface, and already handles
SIGTERM. A second Railway service would put a second executor on the same mission table
under a second set of assumptions, which is the one thing the mission forbids by name.

Three defects block the target flow, and all three are changes *to* that worker rather
than reasons to replace it. They are named in "What is actually missing" below.

## Stage 0 — git ground truth

```
BRANCH        release/full-sweep-20260826
LOCAL HEAD    d9968ca2538c97c12b85a5df3827a5ac85f6386c
origin/main   d9968ca2538c97c12b85a5df3827a5ac85f6386c
AHEAD/BEHIND  0 / 0
REMOTE        git@github.com:hmcroody-alt/CoinPilotX.git
```

Local, `origin/main` and the deployed worker SHA are the same commit. The SHA invariant
the mission asks for (WEB == WORKER == MAIN) currently holds.

**Concurrent foreign work is present and quarantined.** Between two `git status` calls
about twenty minutes apart, three files appeared modified that are not mine: `bot.py`
(hunks at `pulse_live_publish_replay_reel` ≈50931 and `api_pulse_live_end`
≈51007–51057), `media_worker.py` (`_process_live_replay_job` ≈676), and
`mobile-native/src/screens/LiveHostSessionScreen.tsx` (≈308–318). This is another
agent's live-replay-reel work and it sits inside this mission's DO-NOT-TOUCH zone. It is
recorded here so that a later diff does not read as mine. I will not edit those three
files, and any commit I make must exclude them.

Separate housekeeping, unrelated to this mission but worth not losing:
`services/undx_flag_diagnostics.py` is untracked while its `bot.py` caller is already
committed. Committed code imports an uncommitted module. It sits inside a `try/except`
so `/health/undx` degrades rather than breaks, but the file belongs in the next commit.

## Stage 1 — every Railway service, audited

Project `coinpilotx-alert-worker` (`111b3838-09d4-4f13-8b8b-6ed332bad06f`), environment
`production` (`8bf01340-99d0-49be-a951-abffc17aa4d3`). Nine services, all deploying
`hmcroody-alt/CoinPilotX` on branch `main`, all single-replica in `sfo`.

| Service | Start command | Role |
|---|---|---|
| `CoinPilotX` | *(none — Procfile `web:` gunicorn)* | user-facing web; pulsesoc.com, www, coinpilotx.app |
| `coinpilotx-undx-worker` | `python undx_worker.py` | **the UNDX worker** |
| `PulseSoc Command Center Worker` | `gunicorn services.command_center_worker.app:app` | **HTTP service despite the name** |
| `coinpilotx-pulse-worker` | `python pulse_worker.py` | pulse domain; only NIXPACKS service |
| `pulsesoc-ads-worker` | `python pulse_ads_worker.py` | ads |
| `python telegram_worker.py` | `python telegram_worker.py` | telegram |
| `python alert_worker.py` | `python alert_worker.py` | crypto price alerts |
| `coinpilotx-media-engine` | `python media_worker.py` | media/transcode |
| `Postgres` | — | database |

Two findings matter.

`PulseSoc Command Center Worker` is named "Worker" and is a gunicorn web process. This is
exactly the trap the mission's Stage 1 warns about — a service whose name asserts a role
its start command contradicts. It is not the UNDX worker and must not be mistaken for a
spare one.

`coinpilotx-undx-worker` is genuinely the worker it claims to be. Its start command is
`python undx_worker.py`, its latest deployment (`535b55c1-8e01-42e4-84a9-d8c2dca0530d`)
is SUCCESS, and its startup marker reports `deployed_sha d9968ca2…`, matching `main`.
The marker payload also confirms the live safety posture: `writes_enabled false`,
`global_write_stop true`, `reads_enabled true`, `mission_runtime_enabled true`,
`dynamic_limit_escalation false`, `emergency_stop false`, all five provider keys present.

**There is no Redis service in the project, and `REDIS_URL` is absent from the worker's
environment.** That settles the queue-backend question on evidence rather than taste:
Postgres is not merely acceptable, it is the only durable substrate deployed.

`UNDX_CAPABILITY_PLANNER_ENABLED` is set on neither the web service nor the worker. The
worker also lacks `UNDX_HTTP_RUNTIME_ENABLED`, `UNDX_NATIVE_CONTEXT_*`, `UNDX_V5_*`,
`UNDX_HEALTH_ENDPOINT_ENABLED` and `UNDX_CONFIG_VERSION`, all of which the web service
has. Divergent flag surfaces between two processes that are supposed to reach identical
decisions is a defect in itself; it means a capability can be reachable on the web and
silently unreachable on the worker.

## Stage 2 — the queue substrate that already exists

The durable queue is not missing. `services/undx_mission_runtime.py` (357 lines) is a
real coordination layer over `pulse_ai_missions` / `pulse_ai_task_nodes`:

- **Claim** — `claim_next` reads the 25 oldest `ready`/`running` rows and takes one by
  compare-and-swap: `UPDATE … WHERE mission_id=? AND status=? AND updated_at=?`, then
  checks `cur.rowcount == 1`. Two containers racing the same row produce one winner. This
  is not `FOR UPDATE SKIP LOCKED`, but it is a correct optimistic claim, and it works
  identically on SQLite locally and Postgres in production — which `SKIP LOCKED` would
  not.
- **Lease** — `lease_owner` / `lease_expires_at`, default 90s via
  `UNDX_WORKER_LEASE_SECONDS`. An expired lease on a `running` mission is reclaimable
  when both reconciliation flags are on, so a container that dies mid-mission does not
  strand it.
- **Bounds** — `max_node_advances` is fixed at plan creation and exhausting it fails the
  mission rather than looping. `attempt_count` increments per claim.
- **State** — `checkpoint_json`, `worker_state_json`, `last_error`, `heartbeat_at`,
  `completed_at`, `paused_at`, `cancel_requested_at`, all added idempotently by
  `ensure_schema`.
- **Controls** — `request_pause` / `resume` / `cancel`, every one scoped `AND user_id=?`.

A generic `pulse_jobs` table also exists and is used by the media and feed pipelines. It
is a second persistence system, already deployed, and unrelated to agent runs. Two of its
indexes (`idx_pulse_jobs_status_run`, `idx_pulse_jobs_type_status`) fail to create in
production with `LockNotAvailable`. Irrelevant to this mission, and it should not become
relevant — agent runs belong on the mission tables, not on `pulse_jobs`.

## The finding that changes the plan

A previous mission concluded **no worker needed** (`03_WORKER_AUTHORIZATION_REPORT.md`).
That conclusion was correct for the question it asked — every governed capability
completes inside the HTTP request, so nothing needed relieving. This mission asks a
different question: can the user close the app and have execution continue? That requires
crossing the boundary report 03 closed.

Report 03 justified the closure by pointing at `advance_claimed`, where a node of type
`call_tool` is refused with `governed_tool_requires_request_context`, and argued the
gateway "binds a confirmation token to an owner, an action and an argument set inside a
request; outside one there is nothing to bind to."

**That justification does not survive reading the gateway.** `undx_tool_gateway.execute`
imports no Flask, reads no `request`, touches no `session`. Its first argument is a plain
cursor and its docstring says so explicitly: *"``cur`` is an open database cursor owned by
the caller."* Everything it needs — `user_id`, `capability_id`, `proposed_arguments`,
`confirmation_token` / `confirmation_id` — arrives as parameters. And confirmations are
themselves durable: `pulse_ai_confirmations` is a real table with a token, an argument
binding, a TTL and a `pending → consumed | revoked` lifecycle, created and consumed
through `undx_architecture`.

So the refusal in `advance_claimed` is a *policy* the worker applies to itself, not a
technical wall. That is a better position to be in than report 03 described, and it also
means the safety argument has to be made properly rather than inherited.

What genuinely does not exist is the envelope. A `call_tool` node today carries no
resolved arguments, no owner-bound approval and no idempotency key of its own. The
worker refuses it because it has nothing to execute, not because execution outside a
request is unsound.

## What is actually missing

Three gaps, in the order they block the target flow.

**1. No durable authorization envelope for a queued action.** The request must write down
what was authorized — owner, capability, *deterministically resolved* arguments,
confirmation id, idempotency key — so the worker executes a decision a person already
made rather than re-deciding it. The resolution stays in the request, where the person's
own words are. This is the load-bearing constraint and it is the same one the capability
planner was built under: the model may name the action, never the row.

**2. `import bot` boots the web app inside the worker.** Worker logs show
`CoinPilotX web boot starting` → `PORT= 8080` → `DB_INIT_STARTED_ONCE` →
`CoinPilotX web boot complete` before `UNDX_WORKER_START`. A worker that runs the web
service's boot path as a side effect is a Stage 38 violation, and it is the reason the
two processes can drift: they share a module but not a configuration surface.

**3. Divergent flag surfaces.** The worker and the web service must resolve the same
flags to the same values, or "the worker reuses the same policy" is true in code and
false in production.

Two smaller items carried forward from report 03 and still open: no dead-letter
collection for missions that exhaust their bounds, and `attempt_count` is recorded but
never consulted, so a mission blocked for a persistent reason is re-claimed every cycle
once its lease expires.

## Report fields, as far as recon can fill them

```
STARTING SHA        d9968ca2538c97c12b85a5df3827a5ac85f6386c
RAILWAY PROJECT     coinpilotx-alert-worker (111b3838-09d4-4f13-8b8b-6ed332bad06f)
WORKER SERVICE      coinpilotx-undx-worker (c2c9b804-3e7e-4a5d-9061-54542a5f7d89)
EXISTING WORKER     YES — python undx_worker.py, deployed, healthy, SHA matches main
REUSED              YES — no new Railway service
OLD START COMMAND   python undx_worker.py
NEW START COMMAND   python undx_worker.py   (unchanged)
QUEUE BACKEND       PostgreSQL — pulse_ai_missions / pulse_ai_task_nodes,
                    compare-and-swap claim, lease_owner + lease_expires_at
REDIS REQUIRED      NO — no Redis service exists in the project and REDIS_URL is
                    absent from every service environment
```

`FINAL VERDICT` is deliberately not filled in. Recon does not get to award itself a pass.
