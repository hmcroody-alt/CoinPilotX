# Sentinel V1 — Foundation Inventory (Stage 0 Forensic Reality Scan)

Date: 2026-08-13 · Branch: `codex/agora-rtc-migration` · Start SHA: `10a86ec20d671670900cc636038cb9ebee520085`

Method: repository inspection (grep/read of bot.py, services/, tests/). Each primitive
is classified so Sentinel reuses instead of duplicating. Classifications:
CANONICAL / REUSABLE / PARTIAL / LEGACY / UNSAFE / DUPLICATIVE / MISSING.

| # | Primitive | Classification | Evidence / Decision |
|---|---|---|---|
| 1 | Security events | CANONICAL | `services/security_monitor.py`, `services/command_center_worker/security_engine.py`; tables `security_events`, `auth_events`. Sentinel ingests these via a bridge; does NOT create a second security event store. |
| 2 | Admin audit | CANONICAL | `services/audit_service.py`; `admin_audit_logs`, `admin_session_logs`. Reused as evidence source. |
| 3 | Authentication | CANONICAL | bot.py session/HMAC bearer path; tables `users`, `sessions`, `auth_events`. Not rewritten. |
| 4 | Password recovery | REUSABLE | `password_reset_tokens`, `account_recovery_tokens` tables. Abuse telemetry flows into Sentinel via security-event bridge. |
| 5 | Sessions + invalidation | CANONICAL | Flask session + persistent refresh with reuse-grace (`PULSESOC_REFRESH_REUSE_GRACE_SECONDS`). Observed only. |
| 6 | Device/IP classification | PARTIAL | `security_engine.py` emits `unusual_device`/`unusual_country`; no fingerprint/ip-reputation tables. Sentinel models device/network refs in the event envelope; enrichment deferred to adapter contract. |
| 7 | Rate limiting | MISSING | No limiter primitives found. Sentinel applies its own bounded-queue/ingest limits; platform-wide limiter is reported as an open risk, not built here. |
| 8 | Jobs framework | CANONICAL | `pulse_jobs`, `background_jobs`, worker binaries (`pulse_worker.py`, `alert_worker.py`, `undx_worker.py`…). Reused for future Sentinel scheduling. |
| 9 | Retry / dead letter | REUSABLE | DLQ counters in push/email services; `failed_email_queue`; webhook DLQ in payments. Patterns mirrored, not duplicated. |
| 10 | Idempotency | CANONICAL | Ledger UNIQUE `idempotency_key`; `webhook_inbox.py` persist-before-process with UNIQUE `provider_event_id`; idempotent settlement effects. Sentinel copies this DB-level pattern for events/evidence. |
| 11 | Worker heartbeats | CANONICAL | `services/command_center_worker/heartbeat.py`, `alert_worker_heartbeat`. Read by Sentinel health, unchanged. |
| 12 | Provider health | PARTIAL | `payment_provider.provider_status()` (config-level only); command-center health checks DB/Redis/push. No capability-level truthfulness → Sentinel adds provider→capability→status model. |
| 13 | Deployment SHA | REUSABLE | `RAILWAY_GIT_COMMIT` env; no table. Sentinel stamps `deployment_sha` on every event/evidence record from env. |
| 14 | Feature flags | MISSING | Env gates only. Sentinel uses env-based switches consistent with UNDX precedent; no new flag framework invented. |
| 15 | Kill switches | CANONICAL | UNDX layered switches (`UNDX_EMERGENCY_KILL_SWITCH` > write/read switches > per-capability). Sentinel adopts the identical precedence model with its own namespaced switches. |
| 16 | Command Center worker | CANONICAL | `services/command_center_worker/` (health, presence, security endpoints). Sentinel is backend fabric behind it; no second command center. |
| 17 | UNDX governance | CANONICAL | `services/undx_agent_policy.py` — deterministic authorisation, REQUIRED_WRITE_GUARDS, fail-closed, no text-to-authority path. Sentinel's authority model extends this philosophy; policy module reused as prior art and untouched. |
| 18 | UNDX tool registry + verification | CANONICAL | `undx_capability_registry.py` (CapabilitySpec: risk, confirmation, executor, verifier, undo, idempotency), `undx_verification.py`. Sentinel runbook registry follows the same spec shape; UNDX capabilities are not re-registered. |
| 19 | Financial ledgers | CANONICAL | `services/business_os/ledger/ledger.py` — double-entry, immutable, idempotent. Observed by invariants; never mutated. |
| 20 | Marketplace settlement | CANONICAL | `services/marketplace_settlement_service.py` — settlement/refund/payout state machine with ALLOWED_TRANSITIONS. Observed only. |
| 21 | Refunds | PARTIAL→CANONICAL core | Refund reversals + `REFUND_MISMATCH` incidents in `business_os/payments/incidents.py`; cumulative caps enforced in ledger. Sentinel adds a read-only invariant on top. |
| 22 | Payouts | CANONICAL | `business_os/payments/seller_payouts.py` state machine + Connect projections + protection holds. Observed only. |
| 23 | Advertising | CANONICAL | `services/business_os/advertising/` (schema, service, delivery, creatives, funding). Observed only. |
| 24 | Ad Wallet | CANONICAL | `advertising/funding.py`, ledger-backed reserve/release. Invariant: funding requires verified purchase authority — validated read-only. |
| 25 | Privacy / data classification | PARTIAL | Pulse-ID privacy tests exist (`tests/test_pulse_id_public_privacy.py`); no central classifier. Sentinel introduces the canonical classification module (Stage 3) — this is a genuine gap it fills. |
| 26 | Pulse ID | CANONICAL | `services/pulse_id_service.py`; public surfaces use `public_player_id`, never raw internal ids; test-enforced. Sentinel classifies pulse id HIGHLY sensitive and keeps it out of evidence payloads. |
| 27 | Logs / traces | PARTIAL | db query observer only; no request-id middleware. Sentinel events carry their own correlation keys; platform tracing reported as open risk. |
| 28 | DB / Redis | CANONICAL | `services/db.py` (`connect()` SQLite/PG compat layer, masked URLs). Sentinel store is built on it. Redis optional, not required by Sentinel V1. |
| 29 | Secret handling | CANONICAL | bot.py production secret enforcement; masked logging. Sentinel evidence layer additionally redacts by classification before persisting. |
| 30 | Webhooks + replay | CANONICAL | `business_os/payments/webhook_inbox.py` idempotent inbox. Pattern reused for Sentinel ingestion dedupe. |

## Additional findings

- Incident/alert model: `business_os/payments/incidents.py` is CANONICAL for financial
  discrepancies (append-only, idempotent by key, resolution requires note). Sentinel's
  cross-domain incident engine ingests these as observations rather than replacing them.
- Circuit breakers: MISSING (only unrelated game code matches). Contract created (Stage 13), not enforced.
- Metrics: MISSING. Sentinel self-metrics created (Stage 28), platform metrics out of scope.
- Journeys: MISSING. Canonical journey model created (Stage 10).
- Generic graph/edges: MISSING (social graph is feature-specific). Relational edge abstraction created (Stage 9); no graph DB — event volume does not justify one.
- Existing protection tests: CANONICAL (`tests/protection/` 20+ contracts, `tests/undx_agent/test_safety_precedence.py`). Sentinel test suite added alongside; nothing replaced.
- No existing module named "sentinel" — namespace clear; this is V1, not a duplicate.

## Duplicate systems explicitly avoided

Second security-event store; second admin audit; second command center; second UNDX
governance stack; second job framework; second financial-incident engine; second
kill-switch convention (namespaced, same precedence semantics instead).

## Storage decision (Stage 25)

Existing Postgres (prod) / SQLite (dev) via `services/db.py` is sufficient: current
security/ops event volume is low (single-digit tables, no evidence of >10⁶ rows/day),
retention is modest, and queries are key/lookup shaped. No Kafka/ClickHouse/Neo4j/ES.
Revisit only with measured volume in a future phase.
