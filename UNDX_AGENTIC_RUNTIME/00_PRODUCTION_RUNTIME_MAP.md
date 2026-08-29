# 00 — Production runtime map

What actually executes when a person types a sentence into UNDX on the PulseSoc app.
Every claim below is a file and line in this repository at `b076ef32`, or a measurement
from `scripts/undx_production_gate_probe.py`. Nothing here is inferred from how systems
like this are usually built.

## Processes

`Procfile` declares six, all in one Railway project, all from the same image:

| Process | Command | Role |
| --- | --- | --- |
| `web` | `gunicorn bot:app --workers 2 --threads 4 --timeout 120` | serves every HTTP route, including the whole UNDX turn |
| `undx_worker` | `python undx_worker.py` | long-running UNDX missions; heartbeats as `coinpilotx-undx-worker` |
| `email_worker` | `python email_worker.py` | outbound mail |
| `ads_worker` | `python pulse_ads_worker.py` | ad delivery/billing |
| `alert_worker` | `python alert_worker.py` | crypto price alerts |
| `media_worker` | `python media_worker.py` | media transcode |

`CLAUDE.md` still says only `web`, `undx_worker` and `email_worker` are in the
Procfile. That is now stale — `ads_worker`, `alert_worker` and `media_worker` are
declared. Worth correcting there, because a reader who trusts it will conclude that
crypto alerts have no runner.

Two workers matter to this mission and they matter in opposite directions.
`undx_worker` is **not** on the path of a simple action — see "Execution classes"
below. `alert_worker` is the system UNDX *orchestrates* for scheduled crypto alerts
rather than reimplementing.

## The request path

The web process, and only the web process, runs all of this. There is no queue hop, no
second service, and no cross-process handoff anywhere in it.

```
native app  mobile-native/src/screens/ChatScreen.tsx
   │            sendPulseAiMessage()            messenger.ts:578
   │            reads data.response_components  ChatScreen.tsx:665
   ▼
POST /api/pulse-ai/message
   │  pulse_communications_v2/routes.py:629
   │  blueprint registered by bot.py:1247 _load_route_pack(...)
   ▼
services/pulse_ai_service.send_message()
   │
   ├─ undx_agent_runtime.available(user_id)      pulse_ai_service.py:724
   │     └─ undx_agent_policy.user_enabled()     undx_agent_policy.py:166
   │        ── returns False ⇒ the agent is never consulted at all ──┐
   ▼                                                                 │
services/undx_agent_runtime.handle()                                 │
   │  intent → capability match → argument resolution                │
   │  resolve_recent_post() for "my most recent post"                │
   │  Resolution.agent_chose_target ─────────────┐                   │
   ▼                                             │                   │
services/undx_tool_gateway.execute()             │                   │
   │  undx_agent_policy.evaluate(...)  ◄─────────┘ target_chosen_by_agent
   │    ALLOW / REQUIRE_CONFIRMATION / DENY                          │
   │                                                                 │
   ├─ needs_confirmation and no handle presented                     │
   │     → create_confirmation() writes pulse_ai_confirmations       │
   │       (sha256 of the token only; plaintext leaves with the card)│
   │     → GatewayOutcome CONFIRMATION_REQUIRED                      │
   │                                                                 │
   └─ handle presented → consume_confirmation() burns it under       │
         status='pending', argument_hash must match, then            │
         services/undx_agent_tools.py executor                       │
         services/undx_verification.py verifier reads canonical state│
         receipt: verified_success | verification_failed | ...       │
                                                                     │
                                    ordinary conversational reply ◄──┘
```

The second turn — the person typing "Yes" — re-enters at exactly the same place. It
matches no capability, falls through to `_confirm_pending()` in
`services/undx_agent_runtime.py`, which looks the grant up **by id in
`pulse_ai_confirmations`** and replays the *stored* arguments. It never re-resolves
the target from the word "yes".

Tapping the card's Confirm button instead goes to
`POST /api/pulse-ai/actions/confirm` (`routes.py:820` →
`pulse_ai_service.confirm_action`) carrying the plaintext token. Both routes converge
on the same gateway and the same table.

## State: where a pending action actually lives

`pulse_ai_confirmations`, a real table in the application database — SQLite locally,
PostgreSQL in production via `DATABASE_URL`. Not process memory, not the LLM context,
not React state.

This is the answer to Phase 3's central worry, and it was already true before this
mission. Consequences that follow from it and were verified rather than assumed:

- A different gunicorn worker, or a different Railway container, can serve the "Yes"
  that follows a card created elsewhere. Nothing about the grant is local to a process.
- A restart between the two turns loses nothing.
- Single use is a SQL property: `consume_confirmation` updates under
  `status='pending'`, so a duplicate "Yes" matches zero rows rather than racing.
- Expiry is a SQL predicate, not a timer.

`/health/undx` (`bot.py:115393`) reports `coordination.mode` as
`postgresql-durable-leases`. There is no Redis on this path and none is needed for it.

## Execution classes

| Class | Runs where | Examples | Evidence |
| --- | --- | --- | --- |
| A — synchronous | inside the web request | like/unlike, save/unsave, follow/unfollow, pause alert, notification preference, update bio | the whole gateway path above is in-request; the probe completes both turns and reads the row back with no worker running |
| B — durable async | `undx_worker` | long missions, bulk analysis | `undx_worker.py`, heartbeat surfaced at `/health/undx` → `worker` |
| C — scheduled/conditional | existing subsystem workers | crypto price alerts → `alert_worker` | `Procfile`; UNDX creates and pauses alerts, `alert_worker` fires them |

**A like does not require `undx_agent_worker`, and one should not be added.** The
mission asks this to be decided on evidence rather than on what agent platforms
usually do. The evidence: the executor, the verifier and the receipt all complete
inside the request in the probe run below, against a real database, with no worker
process in existence. Adding a queue hop to a sub-second owner-scoped `INSERT` would
add a failure mode (the queue) and a state (in-flight) to a path that currently has
neither, and would make the user wait for a poll interval to be told whether their own
like landed. The existing `undx_worker` remains correct for class B, and the existing
`alert_worker` for class C.

## Observability that already exists

Do not rebuild these.

- `/health/undx` (`bot.py:115393`) — SHA, pid, uptime, the full agent flag surface,
  brain, verification/audit/idempotency availability, registry↔executor parity,
  corpus checksum, `undx_worker` heartbeat and online-ness, coordination mode,
  provider configured, and a `degraded` list. Unauthenticated on purpose, so it can be
  read when sign-in is what is broken.
- `/health/routes` (`bot.py:115343`) — which optional route packs registered.
  `pulse_communications_v2` is loaded through `_load_route_pack` inside
  `except Exception`, so a failed import would make every `/api/pulse-ai/*` endpoint
  404 while the app booted cleanly. This endpoint is how that is caught.
- `undx_agent_policy.log_rollout_surface()` — logs pid and flag values at import, so
  the log records what the process *serving requests* reads, not what a launcher
  banner claimed.

## The gap this mission found

`/health/undx` is unauthenticated, so it can only report `qa_cohort_configured` — a
boolean that is true of any non-empty list regardless of who is in it. Cohort
membership is per-account and could not be asked anywhere. That gap is closed in
`01_LOCAL_PRODUCTION_PARITY_REPORT.md`.
