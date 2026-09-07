# Sentinel Server Defense — Foundation Map (Stage 0)

Baseline: `d8aaf911` on `main`. Worktree branch `feat/sentinel-server-defense-mesh`.
Every claim below was read out of the repository or out of the live Railway
project; nothing here is inferred from naming.

> **Corrections applied during Stage 2.** Two claims in the first draft were
> wrong, both from reading the package's documentation rather than its code:
> the storage entry point is `store.ensure_schema()`, not `store.init_db()`
> (no function of that name exists), and there are **22** tables, not 17. Both
> surfaced the moment Stage 2 tried to *call* the thing this map described,
> which is the argument for wiring early rather than mapping exhaustively first.

## 1. The headline finding

**Sentinel is built and almost entirely unwired.**

`grep -c sentinel bot.py` returns **0**. The 55-module, ~11k-LOC `services/sentinel/`
package has:

* no blueprint registered on the Flask app (`api.sentinel_bp` exists, nothing mounts it),
* no `store.ensure_schema()` call in the boot path, so **none of its 22 tables exist in production**,
* exactly one live caller anywhere in the product: `alert_worker.py:79`
  `sentinel_runtime.run_scheduled_ingestion()`.

So the security *foundation* is real, but the live request path emits nothing into
it. This mission is therefore mostly an **activation and bridging** job, not a
green-field build. The temptation — and the way this mission goes wrong — is to
write a second security stack next to the dormant one.

## 2. Authorities that already exist (REUSE, DO NOT DUPLICATE)

| Concern | Authority | Location |
|---|---|---|
| Security event schema | `Event`, `EVENT_VERSION = "1"`, 15 categories | `services/sentinel/events.py` |
| Event ingestion | `events.ingest(event, conn)` — idempotent on `dedupe_key`, redacts to CONFIDENTIAL, synchronous | `services/sentinel/events.py` |
| Policy / authority | `authority.check()`, 5 dimensions × 5 levels, fail-closed on unknown | `services/sentinel/authority.py` |
| Hard constraints | 15 append-only rules SC1–SC15 | `services/sentinel/constitution.py` |
| Action broker | `runbooks.register()/execute()`, 3 cascading env gates, `EXECUTED_UNVERIFIED` → independent verifier → `COMPLETED` | `services/sentinel/runbooks.py` |
| Incidents | `open_incident()`, 11-state machine, dedupe by `incident_key` | `services/sentinel/incidents.py` |
| Correlation | CR1–CR5, each requires ≥2 events AND ≥2 distinct types (SC8) | `services/sentinel/correlation.py` |
| Storage | 22 `sentinel_*` tables + 29 indexes (51 statements), `store.ensure_schema()` | `services/sentinel/store.py` |
| Evidence | append-only hash chain | `services/sentinel/evidence.py` |
| Health | freshness-decaying `HealthSnapshot`; UNKNOWN/STALE/CONFIGURED are **not** healthy | `services/sentinel/health.py` |
| Kill switches | emergency > master > domain > per-runbook | `services/sentinel/killswitches.py` |
| Prompt-injection primitives | `scan_for_injection()`, `wrap_untrusted()` | `services/sentinel/ai_security.py` |
| UNDX tool authorization | `undx_tool_gateway.execute()` 9-step chokepoint; `undx_agent_policy.Decision` | `services/undx_tool_gateway.py`, `services/undx_agent_policy.py` |
| Member login throttle | `login_security_preflight()` / `register_failed_login()` — **DB-backed**, per ip/email/domain | `bot.py:5360`, `bot.py:5491` |
| Admin login throttle | `admin_gateway.login_rate_limited(conn, …)` — **DB-backed**, counts `admin_audit_logs` | `services/admin_gateway.py:138` |
| Session authority | `mobile_security_sessions`, family rotation, reuse-detection grace | `bot.py:30424–30750` |
| Session revocation | `revoke_mobile_refresh_token()`, `revoke_all_mobile_security_sessions()` | `bot.py:30710`, `bot.py:30733` |
| Audit | `log_security_event()` → `security_events`; `log_admin_audit()` → `admin_audit_logs`; `user_security_events` | `bot.py:28797`, `bot.py:14414` |
| Private Office second lock | `_office_lock_gate()` → `validate_grant()`; locked ⇒ **423** | `services/private_office_routes.py:304`, `services/private_office/security.py:485` |

**Consequence:** Stages 2, 17, 18, 19 and 20 of the brief are largely *already
implemented*. The correct move is to extend them, not to re-found them.

## 3. Genuine gaps (what is actually missing)

1. **No request-path bridge into Sentinel.** Nothing in `bot.py` emits a
   `sentinel.Event`. Detection today is limited to whatever `alert_worker`
   scrapes on a schedule.
2. **No Sentinel schema in production.** `store.ensure_schema()` is never called,
   so ingestion would fail against a live database. *(Closed in Stage 2.)*

   Note the function's shape: handed a connection it does **not** commit, and on
   PostgreSQL the uncommitted DDL still holds catalog locks, so the next
   connection attempting the same creation blocks on it. Call it with its own
   connection, or commit for it.
3. **Distributed rate limiting does not exist.** See §4 — this is the most
   consequential gap.
4. **`wrap_untrusted()` is never applied at context assembly.** The primitive
   exists in `ai_security.py`; `undx_policy.compile_context()` does not call it.
   Untrusted document/message/listing text reaches external LLM providers
   without boundary markers.
5. **No Sentry.** No `SENTRY_DSN` among the 228 production variables and no
   `sentry_sdk` import. The brief's "reuse existing Sentry integration" has no
   referent — this must not be papered over.
6. **No automated cross-tenant authorization invariants.** Owner scoping is
   ad-hoc per route (`WHERE user_id=?`), with no test proving user A cannot
   read user B.

## 4. The rate-limiting truth (production-critical)

`services/security_guard.py:11` — `BUCKETS = defaultdict(list)` — is **module-level
process memory**, and `services/pulse_security_core.py:22` `_RATE_BUCKETS` is the same.

The Procfile runs:

```
web: gunicorn bot:app --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8}
```

Four independent OS processes. Therefore every configured limit is effectively
**multiplied by the worker count**, and which limit an attacker hits depends on
which worker the load balancer happened to pick. `BUCKETS` is also never evicted,
so its key space grows without bound — a slow memory-exhaustion vector.

Two limiters are honest exceptions, and they are the ones that matter most:
member login and admin login are both **DB-backed** and therefore genuinely
cross-process.

### There is no Redis

Verified against the live project (`railway variables`, 228 distinct names):
**no `REDIS_URL`**. `railway list` shows no Redis service; only `Postgres`.
`services/cache_engine.py` degrades to `None` without it.

The brief says to use Redis "if the existing production architecture establishes
it as the canonical distributed limiter." It does not. It establishes
**PostgreSQL** as the canonical shared store, and `admin_gateway.login_rate_limited`
is the in-repo precedent for a DB-backed limiter that explicitly avoids "a schema
change or parallel security store."

**Decision:** build the distributed counter on PostgreSQL, following that
precedent. Requiring a new Redis service would be a paid-provider blocker; faking
distribution with process memory is forbidden by Hard Rule #4 and would be a lie.

## 5. Client compatibility constraints (see Stage 1 doc)

The shipped App Store binary is frozen. Status codes already in use:
**401** invalid/expired token · **403** account restricted / email unconfirmed /
admin permission · **423** Private Office locked · **429** rate limited.

Login additionally returns **403** with `error: "login_challenge_required"` —
which the shipped client does not read, rendering it as "identifier mismatch".
See Stage 1 §4.1.

Any new enforcement must express itself only in these codes.

## 5a. `bot.py` boot-path hazards found while wiring Stage 2

* **`init_db` is defined twice** — `bot.py:807` and `bot.py:106154`. Python keeps
  the second, so the first is dead code, exactly like the known double
  `webhook_app = Flask(...)` at 384/1130. Anything added to the first definition
  is silently discarded. The live one delegates to `_init_db_impl()`.
* **`_init_db_impl` has no exception handler**, and `init_db()` wraps it only in
  `try/finally`. It is reached from ordinary route handlers, so anything that
  raises inside it becomes a request-time 500. Boot-time additions must catch
  their own failures.
* **`db()` opens a fresh connection on every call** (`bot.py:534`); there is no
  per-request cached connection. Any per-request Sentinel write with
  `conn=None` would therefore open a new DB connection per event — meaning
  under attack, the moment the bridge matters most, it would amplify attacker
  traffic into connection-pool exhaustion. This constrains Stage 3 to buffering.
* The production boot path is `if __name__ != "__main__":
  initialize_database_for_web_startup()` (`bot.py:119686`), which runs at import
  under gunicorn.

## 6. Do not touch

* `mobile-native/**`, `ios/**`, Expo/EAS config — Hard Rule #1, expected diff 0.
* Agora / audio / call / livestream — Hard Rule #2, expected diff 0.
* `services/private_office/security.py` lock semantics — may be *observed*, not
  altered. The second lock is a shipped invariant.
* The 6 Procfile workers' contracts.
* Other agents' worktrees (`elegant-meitner`, `nostalgic-neumann`,
  `feat/document-intelligence`) and the 3 untracked files on `main`.

## 7. Duplicate-risk register

| Tempting to build | Already exists — use this instead |
|---|---|
| A security event table | `sentinel_events` + `events.ingest()` |
| An incident ledger | `sentinel_incidents` + `open_incident()` |
| A policy engine | `authority.check()` + `constitution` SC1–SC15 |
| An action broker | `runbooks.register()/execute()` |
| A login throttle | `login_security_preflight()` (member), `admin_gateway` (admin) |
| A session store | `mobile_security_sessions` |
| An injection scanner | `ai_security.scan_for_injection()` |
| A UNDX tool gate | `undx_tool_gateway.execute()` |
