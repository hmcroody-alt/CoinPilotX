# 03 — Does the agent need a worker?

## Verdict

**No new worker.** A durable worker already exists, is deployed, and is running. Every
governed capability completes inside the HTTP request, and the one thing a worker could
add — executing a mutation outside a request — is refused by design, not by omission.

Building `undx_agent_worker` would put a second executor on the same mission table under
a second set of assumptions. The mission's own constraint rules that out: *do not create
a second authority system.*

## What was already there

`undx_worker.py` (105 lines) is not a queue consumer, and says so in its own docstring:
it *"does not call providers, read files, run commands, or execute repository actions."*
Its loop logs provider status, calls `undx_mission_runtime.poll_once()`, records a
heartbeat, and waits `max(15, UNDX_WORKER_SLEEP_SECONDS)`. SIGTERM and SIGINT set
`STOP_EVENT`, so graceful shutdown is present.

The substance is in `services/undx_mission_runtime.py` (357 lines), which is a real
durable coordination layer rather than the polling stub it looks like from the outside:

- **Queue** — `pulse_ai_missions` filtered to `status IN ('ready','running')`, ordered
  by `updated_at`, capped at 25 per pass. No broker, no Redis.
- **Lease** — `lease_owner` / `lease_expires_at`, default 90s
  (`UNDX_WORKER_LEASE_SECONDS`), claimed by compare-and-swap:
  `WHERE mission_id=? AND status=? AND updated_at=?`. Two containers racing the same
  row produce one winner and one `rowcount == 0`.
- **Restart safety** — an expired lease on a `running` mission is reclaimed when
  `UNDX_RECONCILIATION_ENABLED` and `UNDX_WORKER_RECONCILIATION_ENABLED` are both on. A
  container that dies mid-mission does not strand it.
- **Attempts and bounds** — `attempt_count` increments on every claim; `node_advances`
  is checked against `max_node_advances` fixed at plan creation, and exhausting it fails
  the mission with `fixed_node_bound_exhausted` rather than looping.
- **Persistent state** — `checkpoint_json`, `worker_state_json`, `last_error`,
  `heartbeat_at`, `completed_at`, `paused_at`, `cancel_requested_at`, all added
  idempotently by `ensure_schema`.
- **Idempotency** — every node carries `idempotency_key = f"{mission_id}:{index}"`,
  written at `persist_plan`.
- **Controls** — `request_pause` / `resume` / `cancel`, each owner-scoped by
  `AND user_id=?` and each reachable from `pulse_ai_service`.
- **Identity and heartbeat** — `worker_identity()` is
  `coinpilotx-undx-worker:{hostname}:{pid}`; heartbeats land in the health payload.

Production confirms it is alive: `worker.online: true`, `worker.status: "healthy"`,
`worker.heartbeat_present: true`, `coordination.mode: "postgresql-durable-leases"`,
`mission_runtime.enabled: true`, `planner.enabled: true`, `task_graph.enabled: true`.

## Why no agent action needs it

Two independent reasons, and either alone is sufficient.

**The worker cannot execute a capability, deliberately.** In `advance_claimed`, a node of
type `call_tool` is not attempted — it is marked `blocked` with the reason
`governed_tool_requires_request_context`. Only three node kinds advance: `understand`,
`retrieve` when `retrieval_proof` is already durable, and `verify` when
`verification_ready` is. That is the architecture answering the mission's safety rule
structurally rather than by policy: the worker never bypasses authentication, ownership,
confirmation or idempotency because it never reaches the gateway at all. The gateway
binds a confirmation token to an owner, an action and an argument set inside a request;
outside one there is nothing to bind to.

**No capability is routed through missions anyway.** `persist_plan` is called from one
place — `pulse_ai_service.py:939` — and only when `reasoning_mode` is one of
`deep`, `deliberate`, `strategic`, `crisis`, `high_stakes`, or the caller passes
`persist_mission: true`. The agent capability path (`undx_agent_runtime.handle`) does not
touch it. Class B is empty for agent actions today, so there is no queue depth to relieve.

And the empirical half: `scripts/undx_production_gate_probe.py` runs the full acceptance
sentence plus the "Yes" against a real database **with no worker process running at all**
and reaches `verified_success` with the row written. A worker cannot be required for a
flow that completes without one.

## The classification

All 29 write capabilities and all 72 reads execute synchronously in the request. The
mission's own guidance — *simple actions should remain synchronous if they can reliably
complete in the request lifecycle* — is satisfied by every one of them: each is a single
statement against the application database behind an already-open connection.

**Synchronous — writes (29).** `crypto.alerts.create/update/delete/pause/resume`,
`crypto.portfolio.holding.add/update/delete`, `crypto.watchlist.add/remove`,
`feed.posts.like/unlike/delete`, `reels.like/unlike/save/unsave`, `saved.post.set`,
`social.follow/unfollow`, `notifications.mark_read/mark_all_read/preference.update`,
`profile.preferences.update`, `presence.privacy.update`,
`settings.appearance.theme.update`, `settings.privacy.audience.update`,
`localization.region.update`, `localization.translation.update`.

**Synchronous — reads (72).** The `search.*`, `crypto.*.list/summary/history`,
`feed.*.get/list/summary`, `messages.*`, `notifications.*`, `profile.*`, `reels.*`,
`security.*`, `settings.*`, `status.*`, `marketplace.*`, `live.*`, `learning.*`,
`groups.*`, `premium.*` families.

**Asynchronous (worker-managed).** Mission lifecycle nodes only — `understand`,
`retrieve`, `verify` — for plans persisted under a deep reasoning mode. Never a mutation.

**Scheduled / conditional.** Not the agent's. `alert_worker.py` evaluates price
conditions, `email_worker.py` and `pulse_ads_worker.py` handle their own domains. None
of them route through the capability registry.

## What is honestly missing

Naming these is not an argument for building the worker; each is a bounded change to the
existing one if a future action needs it.

- **No dead-letter queue.** A mission that exhausts `max_node_advances` is set to
  `failed` and stops, which is safe, but nothing collects failures for inspection.
- **No backoff.** `attempt_count` is recorded and never consulted. A mission that blocks
  for a persistent reason is re-claimed every cycle once its lease expires.
- **`UNDX_WORKER_FAIL_CLOSED` is unset in production**, running on the documented
  default `1`. The health payload flags this itself under `configuration_notes`. Making
  it explicit removes a default that a future change could quietly move.
- **Two `pulse_jobs` indexes may not exist.** Boot logs show
  `psycopg2.errors.LockNotAvailable: canceling statement due to lock timeout` while
  creating `idx_pulse_jobs_status_run` and `idx_pulse_jobs_type_status`. Irrelevant
  today because nothing queues through `pulse_jobs`, and worth fixing before anything
  does.

## The report fields

```
WORKER NEEDED:   NO
WHY:             Every governed capability completes in-request; the durable worker
                 that exists cannot execute one by design (call_tool nodes are
                 blocked with governed_tool_requires_request_context); and no
                 capability is routed through missions. The acceptance flow was
                 measured to completion with no worker process running.
EXISTING WORKER: coinpilotx-undx-worker  (Railway service c2c9b804-3e7e-4a5d-9061-54542a5f7d89)
PROCESS COMMAND: undx_worker: python undx_worker.py   (Procfile)
QUEUE:           pulse_ai_missions / pulse_ai_task_nodes, PostgreSQL durable leases.
                 No broker added, none required.
HEARTBEAT:       bot.record_worker_heartbeat("coinpilotx-undx-worker"), surfaced at
                 /health/undx as worker.last_seen_at / online / status
RETRY:           attempt_count per claim; 90s lease reclaim on expiry; bounded by
                 max_node_advances fixed at plan creation
DEAD LETTER:     none — see "What is honestly missing"
```
