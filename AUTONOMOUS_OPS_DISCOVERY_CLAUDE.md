# PULSESOC AUTONOMOUS OPERATIONS + COMMAND CENTER — INDEPENDENT DISCOVERY REPORT (CLAUDE)

Date: 2026-08-09 · Mode: read-only architecture discovery · No code was modified.
Scope: full system scan (bot.py, 239 services, workers, tests/protection, CI, mobile-native, UNDX, scripts, deploy config).
All file:line references verified against the working tree on branch `codex/emergency-live-audio-recovery`.

---

## 1. EXECUTIVE SUMMARY

PulseSoc does not need a new automation platform. It needs **a scheduler, a consumer for signals it already records, and about five latent reliability bugs fixed**. The codebase already contains most of the primitives an autonomous-ops system requires — worker heartbeats, honest 503-capable health endpoints, a job queue with backoff, a durable payments webhook inbox with an idempotent reconcile worker, a fail-closed permissioned AI tool gateway with cryptographic confirmation tokens and independent write verification, per-request trace IDs, and an existing (skeletal) admin Operations Center. Almost none of it is deployed or wired together:

- **There is no scheduler anywhere.** No cron, no APScheduler, no Celery. Dozens of functions across `services/business_os/*/api.py` are labeled "operator/cron entry point" and have zero callers.
- **Four of seven workers are not in the Procfile** (`alert_worker`, `media_worker`, `pulse_worker`, and the payments `reconcile_worker`). Their queues accumulate with no consumer unless someone manually attaches services in Railway.
- **Signals are recorded but never consumed.** `security_events`, `mux_retryable` replay states, failed payouts, stale heartbeats — all written to the database, all reviewed (if at all) by a human opening an admin page.
- **The single deepest architectural insight of this scan:** the highest-value "automation" is not AI, and mostly is not even new code. It is (a) running what already exists, (b) one small `ops_worker` that sweeps known stuck states on a timer, and (c) routing high-severity events to the owner's Telegram. That alone converts PulseSoc from "founder must look at dashboards" to "system tells founder when it matters."

Second insight: **several current behaviors are actively unsafe to automate on top of** and must be fixed first — most critically the legacy Stripe webhook path, which can permanently lose a payment event if the process crashes mid-handler (dedupe row is inserted before processing; Stripe's retry then gets a 200 from the duplicate check — bot.py:95042-95045). Automating around a lossy foundation would institutionalize the loss.

Third insight: **UNDX already contains the right permission architecture** for operations — but it has two personalities. The agent path (capability registry → deterministic policy → tool gateway → independent verification → receipts) is genuinely fail-closed and is the correct substrate for runbook execution. The older kernel/desktop-connector path (static approval phrases, no post-write verification, `git push` to main behind the same phrase as a file write — undx_execution_kernel.py:837-840) must be **excluded** from the ops system entirely.

Recommended first mission: a 2-3 day "Phase 0" that deploys existing infrastructure, fixes the webhook-loss hole, and ships a minimal `ops_worker` with 4 sweepers and Telegram escalation. Details in §17-18 and §22.

---

## 2. CURRENT AUTOMATION FOUNDATIONS (what exists, verified)

**Health endpoints (honest, incident-derived):**
- `/health` — liveness, cached DB ping (bot.py:110121); `/health/ready` — 503 on DB failure or route-pack registration failure, explicitly the LB target (bot.py:110163); `/health/routes` — verifies 9 required mobile API rules exist in the URL map, built after a real "TestFlight build vs missing endpoint" incident (bot.py:110197); `/health/undx` — deepest probe: policy flags, registry/executor parity, worker heartbeat freshness with a 180s window (bot.py:110247, 110309); `/health/database` (bot.py:110424); `/api/service/health` returns **version and git commit SHA** (bot.py:14974-14991, SHA resolution 14957-14971).
- ~15 per-subsystem admin health pages (telegram 24162, email 26719, provider 26780, chat 30989, pulse-feed 86543, payments/stripe 87615-16, media 89223, etc.).

**Worker/heartbeat plumbing:** `worker_heartbeats` table (bot.py:105203) + `record_worker_heartbeat`/`get_worker_heartbeat` (bot.py:112578, 112600), used uniformly by all workers and consumed by `/health/undx` and `/admin/ops/status.json` (bot.py:14252, 10-minute freshness window at 14277).

**Job queues (per-feature, DB-polling; no framework):**
- `pulse_jobs` — status/attempts/max_attempts/run_after/error_message (bot.py:101682); consumed by media_worker with real backoff `min(900, 30*attempts)`, MAX_ATTEMPTS=3 (media_worker.py:542-550, 63).
- `failed_email_queue` with idempotency_key/retry_count/next_retry_at/trace_id (services/notification_service.py:693-784); email_worker drains it with retry/dead-letter semantics (bot.py:96047).
- `alert_delivery_jobs` (services/alert_engine.py:248), `notification_delivery_jobs`, `push_delivery_jobs`, `payout_queue`, `background_jobs` (AUTO_PK_TABLES in services/db.py:143ff).
- **Payments webhook inbox** `provider_webhook_events` with status/retry_count/last_error and retry-exhaustion logic (services/business_os/payments/webhook_inbox.py:88-138, 239-265) + idempotent `reconcile_worker` (services/business_os/payments/reconcile_worker.py) — flag-gated behind `BUSINESS_OS_LEDGER`, **off by default, worker not deployed**.

**Idempotency (real implementations):** marketplace checkout `UNIQUE(user_id, idempotency_key)` (services/marketplace_cart_routes.py:28,138-141); UNDX tool gateway `UNIQUE(user_id, tool_name, idempotency_key)` with replay returning the prior receipt (services/undx_tool_gateway.py:470-483); notification dedupe keys (services/pulsesoc_notification_system.py:672); command-center client derives an idempotency key for every dispatched event (services/command_center_client.py:123-137).

**Tracing:** every request gets `g.performance_trace_id` from `X-Trace-Id` or generated (bot.py:2275-2277), echoed on responses (bot.py:2457), threaded into email queue rows, security events, and admin audits. ~1,189 trace/request-id mentions in bot.py. Logging is stdout key=value with grep-able UPPERCASE event tokens — not JSON, no external sink.

**Kill switches / flags:** UNDX layered kill switches — emergency/write/read plus six `REQUIRED_WRITE_GUARDS` where disabling any guard disables all writes (services/undx_agent_policy.py:48-72, 189-202); ads kill-switch endpoint (bot.py:18634); per-feature security kill switches via env (services/pulse_security_core.py:89-96); provider selection env-driven (`MEDIA_STORAGE_PROVIDER`, Mux/Agora config-presence gating). All flags are env vars → changing one requires a redeploy/restart.

**Existing ops UI:** `/admin` Operations Center page loads `static/js/admin_ops_center.js` fed by `/admin/ops/status.json` — DB check, heartbeat freshness, honest config-presence for Stripe/OpenAI/Agora with an explicit "no fabricated greens" comment (bot.py:14246-14300). Plus `services/system_mission_control.py` aggregating 10 subsystem modules, and a **dormant command-center dispatch skeleton** (`services/command_center_client.py` + `services/command_center_worker/`, env-gated off, bounded timeouts, idempotent enqueue).

**Security automation already live:** failed-login velocity → automatic 15-minute IP/email/domain cooldown controls + login challenge at 3 failures (bot.py:4812-4818, 5219-5257); refresh-token-reuse and device-mismatch → automatic session-family revocation (bot.py:27491-27547); three custom rate-limit layers (bot.py:2540, 2606, 2692).

**UNDX (agent path):** ~40 registered capabilities with mandatory verifiers for writes and verified-field enforcement (services/undx_capability_registry.py:44-128); deterministic LLM-free policy with truth-table tests (services/undx_agent_policy.py; tests/undx_agent/test_safety_precedence.py); server-minted SHA-256-hashed single-use expiring confirmation tokens (services/undx_architecture.py:833-843, 991-1008); 9-step unskippable gateway order ending in independent read-back verification and receipts (services/undx_tool_gateway.py:1-38; services/undx_verification.py:1-25); durable mission runtime with immutable execution-bound envelopes and optimistic-concurrency leasing that **refuses to run if dynamic limit escalation is enabled** (services/undx_mission_runtime.py:79-82, 99-124, 187-219).

**Release/protection:** one CI workflow (`.github/workflows/realtime-audio.yml`) gating audio changes + running the 19-suite protection runner that fails on zero-check suites (scripts/protection/run_protection_suite.py:13-23, 53-95). Backup script with restore-verification and retention exists (`scripts/ops/backup_database.py`) — **unscheduled**.

**Mobile:** delta event sync with cursor + full-resync fallback (src/core/eventSync.ts:80-151, 183-193); presence heartbeat every ~45s with server-tuned cadence — a de-facto device liveness ping (src/api/presenceSession.ts:26-29); perf-trace and live-audio telemetry rings with sanitization built, **sinks never wired** (src/core/perfTrace.ts:99-102; src/live/liveAudioTelemetry.ts:143-156).

---

## 3. MAJOR OPERATIONAL GAPS

1. **No scheduler.** The defining absence. Everything below is downstream of it.
2. **Undeployed consumers.** alert/media/pulse/reconcile workers absent from Procfile (Procfile:1-3) → media jobs, alert delivery, feed jobs, and payment reconciliation only run if manually attached in Railway (invisible from the repo).
3. **Record-only signals.** No process consumes `security_events`, stale heartbeats, `mux_retryable`, `payout.failed`, or stuck `pulse_jobs`.
4. **At-most-once Stripe webhook path** (legacy): crash after dedupe-insert = permanent event loss (bot.py:95042-95045; handlers unguarded). The designed fix (inbox + reconcile) exists but is flag-gated off.
5. **Replay pipeline has no retry and is synchronous in a request handler.** Finalization (R2 segment concat + Mux ingest) runs inside `POST /live/<id>/end` under gunicorn's 120s timeout (bot.py:47898-47941; services/agora_cloud_recording_service.py:101-159). `mux_retryable` is written with a message saying it "may be retried" (bot.py:45364) — nothing ever retries it. If `MUX_WEBHOOK_SECRET` is unset, all Mux webhooks are rejected and replays silently never finalize (services/mux_live_service.py:200-201).
6. **Job-claim leak:** media_worker claims jobs by setting `processing` with no timeout-reclaim; a worker crash strands the row forever (media_worker.py:632-635).
7. **Stale live sessions:** cleanup only runs when the *same host* starts a new stream (bot.py:44826, 46147-46231); a crashed host's session shows live indefinitely; abandoned recordings orphan R2 objects. Viewer counts ignore `last_seen_at` and inflate until stream end (bot.py:44421 vs 47941).
8. **No error tracker, no metrics, no external uptime monitor** (verified zero Sentry/StatsD/Prometheus in requirements and code). Logs are stdout → Railway only.
9. **No incident concept.** No table, no state machine, no dedupe — a sustained attack writes one `security_events` row per blocked request (bot.py:2643, 2699); only failed-login bursts have a 60s dedupe (bot.py:5186-5199).
10. **No deploy pipeline.** CI gates but does not ship; Railway auto-deploys main; no repo-declared healthcheck (no railway.json/toml exists); rollback is manual UI action. Deploy SHA *is* queryable (`/api/service/health`) — unused.
11. **Env-var-only flags:** every kill switch requires a redeploy. There is no runtime flag store, so "disable feature X now" is not an ops action, it's a deploy.
12. **Mobile emits nothing.** No crash reporting, no request timeouts, telemetry sinks unwired, store-only releases (no OTA).

---

## 4. CURRENT MANUAL OPERATION LOOPS

| Loop | Trigger | Human action today | Data already available | Safe automatable action | Risk | Authority |
|---|---|---|---|---|---|---|
| Stuck replay | User complaint / owner notices | Re-end stream or nothing; replay lost | `recording_status` in `pulse_live_sessions`; `pulse_live_events` log | Sweeper: re-run finalization for `mux_retryable`/stale `processing_replay` (idempotent — reel claim already is, bot.py:47856-47864) | Low | L1 |
| Dead worker / silent queue | Nothing detects it | Owner opens `/admin/ops`, sees stale heartbeat, restarts in Railway | `worker_heartbeats` | Alert on staleness; optionally Railway API restart | Low(alert)/Med(restart) | L1 alert, L2 restart |
| Stuck `processing` job | Nothing | Manual SQL reset | `pulse_jobs.status`, timestamps | Reclaim `processing` older than N min back to `pending` (attempts++) | Low | L1 |
| Missed/lost Stripe event | User reports missing entitlement | `/admin/users/<id>/retry-stripe-session` (bot.py:19221), `reprocess-latest-webhook` (19282), `repair-user-pro` (25320) | `stripe_events`, `payment_webhook_events`, `unmatched_payments` | Enable inbox + schedule reconcile_worker (exists, idempotent) | Low | L1 |
| Unmatched payment | Row lands in `unmatched_payments` (bot.py:94694) | Admin resolves in queue (bot.py:16497) | Full queue with resolved_at | Auto-notify + auto-match retry on obvious keys; resolution stays human | Low | L1 notify / L3 resolve |
| Failed email/notification | Queue grows | `/admin/emails/retry-failed` (bot.py:15974) | `failed_email_queue` with retry schedule | Already automated *if* email_worker healthy; add queue-depth alert | Low | L1 |
| Security event review | None (pull-only) | Owner opens Security Center | `security_events`, `failed_login_controls` | Dedupe + severity rollup + escalate Critical (e.g. `refresh_token_reuse`) to owner | Low | L1 |
| Seller/teacher verification | Application submitted | Full manual doc review (bot.py:88800-88859) | Docs, selfie compare UI | Notify + queue summary only. **Do not automate the decision.** | High if automated | L3/L4 |
| Deployment verification | Push to main | Owner manually checks the site | `/health/ready`, `/health/routes`, `/api/service/health` SHA | Post-deploy probe loop: new SHA detected → run health + synthetic checks → alert on failure | Low | L1 probe, L3 rollback |
| Ad wallet/delivery issues | Advertiser complains | Admin fraud/billing pages (bot.py:20693-20790) | Ledger + idempotent spend keys already strong | Reconcile wallet job (reconcile_wallet exists, creator_economy_service.py:153) | Low | L1 |
| Failed payout | Row recorded (bot.py:95483-95491) | Nothing / seller complains | `seller_payouts.failure_reason` | Notify seller + owner. **No auto-retry of money movement.** | Med | L1 notify only |
| Stale live session / inflated viewers | None | None | `pulse_live_viewers.last_seen_at`, session state | Sweeper: end sessions with dead heartbeats; prune viewers by last_seen | Low-Med (avoid killing healthy streams — conservative thresholds) | L1 |
| DB backup | None scheduled | Owner runs script manually (if ever) | `scripts/ops/backup_database.py` with restore-verify | Schedule nightly + alert on failure | Low | L1 |
| Account recovery support | User email | Admin resend/reset (bot.py:12699-12703) | Reset tokens, delivery tracking | Keep human; automate delivery-failure detection only | Med | L3 |

---

## 5. OBSERVABILITY GAPS (smallest useful additions)

| Gap | Smallest addition |
|---|---|
| No queue depth visibility | One SQL view/endpoint: counts by status for pulse_jobs, failed_email_queue, alert_delivery_jobs, provider_webhook_events, notification/push delivery jobs |
| No job duration | Add `started_at`/`finished_at` to pulse_jobs claims (2 columns, workers already touch the row) |
| No provider latency/error rate | Wrap existing provider calls (Stripe/Mux/Agora/Brevo/R2) in a 10-line timing recorder writing to a small `ops_provider_health` rollup |
| No canonical failure reason | `error_message` exists on pulse_jobs; standardize a short `error_code` string on job failure paths |
| No incident dedupe key | Derive `(subsystem, error_signature)`; store on new `ops_incidents` |
| No deployment SHA history | Ops worker records SHA from `/api/service/health` on change → deploy history for free |
| No health history | Persist the health evaluator's snapshot each cycle (`ops_health_snapshots`, pruned) |
| No webhook liveness | Record `last_mux_webhook_at` / `last_stripe_webhook_at`; alert when silent too long *while activity exists* |
| No error tracking | Add Sentry (backend first) — one dependency, DSN env var. Biggest single observability win per line of code. |
| No client telemetry | Wire the already-built mobile perfTrace/liveAudioTelemetry sinks to one batched ingest endpoint (sanitization already written) |
| Cost blindness | Daily provider-usage poll jobs (Agora/Mux/R2/AI token counts) → thresholds (§15) |

---

## 6. PROPOSED COMMAND CENTER ARCHITECTURE (V1)

**Extend, don't rebuild.** The Operations Center at `/admin` + `/admin/ops/status.json` already exists with the right honesty ethos. V1 = enrich that JSON and page.

Backing data (all real, no fabricated values):
- Subsystem health states from the health evaluator (§8) — API, DB, workers (per-heartbeat), Mux, Agora (config + last-webhook/last-token-issue recency), R2, Stripe (config + last-event recency), Messenger (queue depths + send error rate), Payments (inbox lag), Security (event-rate anomaly), UNDX (`/health/undx`).
- Open incidents from `ops_incidents` (count + top 3 by severity).
- "Recovered automatically today" = count of `ops_runbook_runs` with outcome=verified_success in 24h.
- "Owner actions required" = incidents in state `awaiting_owner` + pending L3 approvals.
- Deploy panel: current SHA, deploy time, post-deploy check results.
- Queue panel: depths + oldest-pending age.

Where a check does not exist yet, the tile must show **UNKNOWN** — never a default green. This principle is already stated in the code (bot.py:14254) and should be a hard rule of the ops layer.

---

## 7. INCIDENT ENGINE DESIGN

Nothing incident-shaped exists today (closest: `unmatched_payments` as a resolvable queue, and admin tasks from failed-login alerts). Recommend **one new table**, `ops_incidents`:

`incident_id, dedupe_key (subsystem+error_signature, UNIQUE while open), subsystem, severity (info/low/medium/high/critical), state (open/auto_recovering/awaiting_owner/resolved/expired), first_seen, last_seen, occurrence_count, trace_ids (sample, JSON), error_signature, provider, suspected_cause (nullable; UNDX may fill, clearly labeled as AI opinion), runbook_id (nullable), attempt_count, last_attempt_at, resolution, resolved_by (system/owner/undx-approved), created_deploy_sha, audit JSON.`

Rules: new signal matching an open incident's dedupe key → increment count + update last_seen (kills the "1000 rows per attack" problem at the incident layer without touching `security_events`, which stays as raw evidence). State transitions only by the ops worker or an authenticated admin action, every transition appended to audit. `affected_users`/`affected_requests` are estimates in v1 (count distinct user ids in sampled events) — do not pretend precision.

Producers: health evaluator (state flips), queue monitor (depth/age thresholds), security rollup (Critical event types), webhook-liveness monitor, deploy watcher, runbook engine (exhausted retries → escalate).

---

## 8. HEALTH MODEL

States: HEALTHY / DEGRADED / FAILED / UNKNOWN / RECOVERING. Driven by real signals only:

- **API**: `/health/ready` (503-capable — already correct) + route pack status. FAILED = 503 or unreachable; DEGRADED = any route pack failed (`ROUTE_PACK_STATUS`).
- **Database**: `db.ping()` + pool errors. Existing (services/db.py:846-929).
- **Workers**: heartbeat age vs each worker's loop interval ×3 = DEGRADED, ×10 = FAILED. UNKNOWN if never heartbeated (catches "not in Procfile").
- **Queues**: DEGRADED when oldest-pending age exceeds subsystem SLA (e.g. media 10 min, email 15 min, webhook inbox 5 min); FAILED when depth grows monotonically over N cycles.
- **Mux/Stripe (webhook-fed)**: config present + webhook recency *conditional on activity* (no live streams → no Mux webhooks expected; don't false-alarm). FAILED = activity present but webhooks silent > threshold, or signature failures spiking.
- **Agora**: config present + token-issuance route success rate (issued from bot.py:46282). True channel health is client-side; do not fake it — UNKNOWN beyond token issuance is honest.
- **R2**: periodic cheap HEAD/list probe from ops worker.
- **Security**: event-rate anomaly vs trailing baseline + active `failed_login_controls` count.
- **RECOVERING**: set by the runbook engine while a runbook is mid-flight; auto-reverts to measured state after verification.

`system_mission_control.py` (10 subsystem modules) and the per-subsystem admin health pages are the reuse base; the evaluator composes them instead of duplicating checks.

<!-- CONTINUED -->
