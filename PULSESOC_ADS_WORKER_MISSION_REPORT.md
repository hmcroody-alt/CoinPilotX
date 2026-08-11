# PulseSoc Ads Worker — Mission Completion Report

Mission: RAILWAY ADS WORKERS — IMPLEMENT, DEPLOY, AND VERIFY (Master Advertising Delivery Engine).
Date: 2026-08-10. Status: **COMPLETE — deployed, consuming queues, E2E verified in production.**

---

## 1. Architecture

One Railway service, **pulsesoc-ads-worker** (`python pulse_ads_worker.py`), hosting five
logical domains on independent cadences inside a single restart-safe loop:

| Domain | Cadence | Entry point |
|---|---|---|
| Queue drain (events/attribution/billing/reporting jobs) | every cycle (~20s) | `engine.process_pending_jobs` |
| Operations sweep (activate/complete/pause campaigns) | every cycle | `engine.run_operations_cycle` |
| Attribution (last-click purchases, idempotent) | ~5 min | `engine.run_attribution_cycle` |
| Billing reconciliation (wallet vs ledger, report-only) | ~10 min | `engine.run_billing_cycle` |
| Reporting (daily aggregate precompute) | ~5 min | `engine.run_reporting_cycle` |

V1 is deliberately one process; each domain is a standalone `run_*_cycle(conn)` call, so
splitting into separate Railway services later means moving one call into its own
entrypoint — no rewrites. The web app boots and serves ads without the worker; the worker
only makes scheduling, attribution, reconciliation and dashboards automatic
("PulseSoc must work even when ads don't" holds in both directions).

## 2. Reuse map (existing infrastructure leveraged)

- `bot.db()` / `services/db.py` — the SQLite↔Postgres compat layer (CompatCursor,
  `_translate_create_table`, AUTO_PK_TABLES) is the only DB access path; no new drivers.
- `bot.init_db()` — worker boots through the same schema initializer as web.
- `bot.record_worker_heartbeat` — same heartbeat table used by undx/email workers.
- `services/pulse_ads_service.py` (2,195 lines, 78 functions) — the synchronous delivery
  engine (decision pipeline, delivery tokens, spend recording) was reused untouched; the
  worker never duplicates money logic.
- Procfile/nixpacks deploy pattern copied from `undx_worker` / `email_worker`.

## 3. New infrastructure

- `pulse_ads_worker.py` (entrypoint, 163 lines): signal handling, per-domain scheduling
  state, heartbeats, stdout logging fix, graceful shutdown.
- `services/pulse_ads_worker_service.py` (616 lines): `ensure_schema`, `enqueue_job`,
  `process_pending_jobs`, `recover_orphaned_jobs`, `queue_health`, and the four
  `run_*_cycle` functions.
- Queue semantics: per-job `idempotency_key` dedupe at enqueue; visibility-timeout
  recovery (jobs stuck `processing` >10 min re-pended); exponential backoff
  `min(cap, base * 2^(attempts-1))`; dead-letter after max attempts with `last_error`
  retained; `queue_health` surfaces per-status counts + dead-letter totals into heartbeat
  metadata.

## 4. Migrations / schema

No migration framework exists (schema is imperative in `bot.init_db()`), so all worker DDL
is idempotent `CREATE TABLE IF NOT EXISTS` in `engine.ensure_schema`: **pulse_ad_jobs**
(queue) and **pulse_ad_daily_aggregates** (reporting), plus indexes. Both tables are
registered in `AUTO_PK_TABLES` (services/db.py:328-329, commit 511ea46) so
`cursor.lastrowid` returns real ids on Postgres via `RETURNING`. DDL itself never depended
on that registry — `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY` is
translated unconditionally.

## 5. Routes

Delivery + management surface lives in bot.py (~40 routes): placements/serve metadata
(`/api/pulse/ads/placements`, `placement-metadata`), event ingestion (`impression`,
`viewability`, `click`, `event`, `hide` — bot.py:17011-17093), campaign/creative/audience
CRUD, targeting + estimates, wallet/funding/invoices, analytics/portal, and
`/api/promotions/*` (promote-existing-content; note `/api/promotions/content` ships fully
on branch `codex/agora-rtc-migration`). `record_click`/`record_event`
(pulse_ads_service.py:2053-2127) are the production enqueuers — they push attribution
nudges with hour-bucketed idempotency keys, wrapped in try/except so ad-event ingestion
never fails a user request if the queue is unavailable.

## 6. Workers / deploy topology

Procfile: `web` (gunicorn), `undx_worker`, `email_worker`, **`ads_worker: python
pulse_ads_worker.py`**. Railway project `111b3838…`, env production, service
`pulsesoc-ads-worker` (`bf5a0a9a…`) sharing `DATABASE_URL` with web. Tunables via env:
`ADS_WORKER_SLEEP_SECONDS` (20), `ADS_WORKER_BATCH_SIZE` (20),
`ADS_WORKER_ATTRIBUTION_SECONDS` (300), `ADS_WORKER_BILLING_SECONDS` (600),
`ADS_WORKER_REPORTING_SECONDS` (300), `LOG_LEVEL`.

## 7. Redis usage

None required. The queue is Postgres-backed (`pulse_ad_jobs`) for durability and
transactional claim semantics; `REDIS_URL` is detected and logged
(`ADS_WORKER_START … redis_url=False`) but the worker runs fully without it. Redis remains
an optional cache layer for the web tier.

## 8. Placements

12 registered placements (pulse_ads_service.py:22-35): feed_inline,
feed_side_ufo_desktop, feed_inline_ufo_mobile, pulse_network_hologram,
creator_sidebar_signal, marketplace_sponsor, pulse_radio_sponsor, video_pre_roll,
status_interstitial, search_sponsored_result, dashboard_sponsor, profile_sponsor — each
with device targeting, placement type, priority and frequency caps, mapped to page
contexts via `CONTEXT_PLACEMENTS` and to creative content types via
`CONTENT_CREATIVE_PLACEMENTS`.

## 9. Billing safeguards (financial safety)

- **The worker never charges wallets.** The synchronous delivery path owns money and is
  idempotent (`record_spend_event` + `pulse_ad_idempotency`). Worker billing is
  reconciliation only: recomputes ledger net (credits − spend/refund/chargeback debits)
  vs wallet balance and reports drift; it mutates nothing financial.
- Retries can never double-charge: job handlers are idempotent by design and no handler
  touches wallet balances.
- Operations sweep pauses campaigns whose wallets can no longer fund them (the spend path
  already enforces this in-line; the sweep is the async backstop).
- Delivery tokens (`make_delivery_token`/`verify_delivery_token`, HMAC + nonce, bound to
  creative/campaign/placement/viewer/session) prevent forged billable events.

## 10. Security

No secrets logged (only booleans: `database_url=True`). Admin surfaces
(`/admin/ops/status.json`) require an admin session. Event endpoints sanitize payloads
(`clean_text`) and validate delivery tokens before any billable write. Worker holds no
inbound ports — outbound DB only. Stripe keys untouched throughout the mission.

## 11. Tests

`tests/pulse_ads/` — 11 modules, ~150 tests: `test_ads_worker.py` (17 tests: queue claim,
retry/backoff, dead-letter, orphan recovery, cycle behavior), plus campaign activation,
adsets/detail, audiences policy, promote-existing-content, reports/insights/wallet,
wallet funding reversal, wallet spend drawdown, economy metrics scope, portal absent
states. Root-level: `test_pulse_ads_analytics.py`, `test_pulse_ads_os.py`, advertiser
portal accounts/activation. Audit scripts: `pulse_ads_delivery_engine_audit.py`,
`pulse_ads_foundation_audit.py`, `pulse_signal_ads_audit.py`, and the protection suite
remained green (no protected paths touched).

## 12. Performance

Cycle cost is bounded: batch claim of ≤20 jobs, single connection per cycle, aggregate
queries bucketed by day for reporting. Measured E2E latency in production: job enqueued
21:48:20 → completed 21:48:22 (2 s, within one 20 s sleep window). Attribution/billing/
reporting are time-gated so steady-state idle cycles are two cheap queries + heartbeat.

## 13. Limitations / known gaps

- Single process: one stuck domain delays the others until the cycle ends (mitigated by
  bounded batches; solved later by service split).
- Billing reconciliation is report-only — drift is surfaced, not auto-corrected (by
  design; money corrections stay human-approved).
- `/api/promotions/content` full version lands with `git push origin
  codex/agora-rtc-migration` (owner action).
- Postgres Database viewer in Railway UI hangs ("Attempting to connect…") — use the
  service Console shell instead.
- Housekeeping: repo root has stale `.fuse_hidden*` files and `.deploy_upload_21966ecc/`
  can be removed.

## 14. Deploy config

nixpacks (Python 3.11 + ffmpeg), start command `python pulse_ads_worker.py`, restart
policy on-failure. Graceful shutdown: SIGTERM/SIGINT finish the in-flight cycle then exit
between cycles ("ADS_WORKER_STOPPED gracefully"). Restart recovery: every step re-derives
work from the DB; `recover_orphaned_jobs` re-queues `processing` residue after 10 min.
Logging fix (commit 5a6b6e8): `import bot` configures a file-only root logger, so the
worker attaches an explicit stdout handler (`_configure_logging`) — without it Railway
showed nothing after DB init (the original "silent worker" mystery).

## 15. E2E evidence (production, 2026-08-10 UTC)

- Deployment **a980656a** (commit 5a6b6e8) logs: `ADS_WORKER_BOOT_BEGIN` →
  `ADS_WORKER_SCHEMA_ENSURE started` → `ADS_WORKER_SCHEMA_READY` →
  `ADS_WORKER_START database_url=True redis_url=False sleep=20s batch=20` →
  `ADS_WORKER_CYCLE {...billing: accounts_checked=1, dead_letter_total=0}`.
- Heartbeat (container shell against production DB):
  `{'worker_name': 'ads_worker', 'status': 'healthy', 'last_seen_at': '2026-08-10T21:48:02'}`.
- Queue consumption proof: job enqueued 21:48:20 (`events`/`campaign_state_sweep`,
  idempotency_key `e2e-proof-1`) → `{'id': 1, 'status': 'completed', 'attempts': 1,
  'completed_at': '2026-08-10T21:48:22+00:00', 'last_error': ''}`;
  queue state `[{'status': 'completed', 'n': 1}]`.
- Post-fix verification (deployment of 511ea46, ACTIVE): enqueue now returns
  `{'ok': True, 'job_id': 2, 'deduped': False}` (integer id on Postgres), and the worker
  consumed it: `{'id': 2, 'status': 'completed', 'attempts': 1,
  'completed_at': '2026-08-10T22:12:05+00:00', 'last_error': ''}`.
- Commits: **da30179**, **043fe38** (worker + service), **5a6b6e8** (stdout logging fix),
  **511ea46** (AUTO_PK_TABLES registration for `pulse_ad_jobs` +
  `pulse_ad_daily_aggregates` → integer `job_id` on Postgres), **94b0736** (this report).
