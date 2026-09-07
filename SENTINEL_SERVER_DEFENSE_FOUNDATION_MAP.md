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

### Correction applied during Stage 6: there are four, not two

The paragraph above was written from a `grep` for bucket-shaped module globals in
`services/`. It missed the limiter that actually guards the authentication routes,
because that one lives in `bot.py`:

| # | Where | State | Scope |
|---|---|---|---|
| 1 | `services/security_guard.py:11` `BUCKETS` | process memory | paths from `request_limit_for()` |
| 2 | `services/pulse_security_core.py:22` `_RATE_BUCKETS` | process memory (+ `cache_engine`, which has no Redis to reach) | `HIGH_RISK_RATE_RULES` |
| 3 | **`bot.py:449` `RATE_LIMIT_BUCKETS`, used by `basic_abuse_guard`** | **process memory, never evicted** | **11 paths incl. `/login`, `/signup`, both `recover` routes, `/admin/login`, both checkout routes** |
| 4 | `admin_gateway.login_rate_limited`, `bot.login_security_preflight` | **PostgreSQL** | admin + member login |

How it was found is the useful part: Stage 6 wired a distributed limiter into
three auth routes, and the integration tests failed with 429s the new code could
not have produced. The log line was `Rate limit triggered
path=/api/mobile/auth/recover`, emitted by `basic_abuse_guard` — a
`before_request` already limiting all three, with its own deployed policy table.
The Stage 6 code had a comment asserting one of those routes "had no rate limit
of any kind." It was false.

**Consequence for Stage 6.** Adding a `RULES` registry inside
`services/sentinel/rate_limit.py` naming the same routes with limits of its own
would have been a duplicate rate-limit policy — Hard Rule #6, and the specific
failure where two numbers disagree and nobody can say which one production
applied. The registry was deleted. `rate_limit.py` owns the counting; the limits
stay in `bot.ABUSE_GUARD_PROTECTED`, where they were already deployed, and
`basic_abuse_guard` was extended to consult the shared counter using them. That
covers all eleven paths rather than the three the first attempt picked.

### Correction: one configured limit on `/api/mobile/auth/recover` is dead

Found while writing the Stage 6 route tests. `basic_abuse_guard` states 6-per-300s
for that path; `pulse_security_core` states 5-per-600s for the same path.
`basic_abuse_guard` is registered first and so checks first, but on request 6 its
bucket holds only 5 — under its own limit — so it allows and passes the request
to the stricter guard, which refuses. Request 7, where `basic_abuse_guard` would
finally have refused, is never reached.

So one of the two numbers configured for that route has no effect, and neither
file says which. Not changed here: altering either limit changes what real users
of a frozen client experience, which is outside this stage's remit. Recorded for
Stage 21, and pinned by
`test_the_older_guards_limit_is_unreachable_on_this_route` so it cannot drift
unnoticed.

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

### Found while wiring Stage 3

* **`account_user_id()` is not a read — it can rotate a session.** It falls
  through `session.get("account_user_id")` → `account_user_id_from_mobile_access_token()`
  (opens a DB connection, `bot.py:3243`) → `restore_account_from_persistent_cookie()`,
  which calls `rotate_mobile_refresh_token()` (`bot.py:3233`). Calling it from
  an `after_request` hook would therefore make *logging a 401* both open a
  database connection and rotate the user's refresh token — under a 401 flood,
  connection amplification plus token-family churn, on the same code path
  already known for reuse-detection revocations. Any observer must read
  `session.get("account_user_id")` directly and accept device-level attribution
  for bearer-only clients.
* **A `before_request` already resolves the account for every request**
  (`pulse_security_core_guard`, noted in the docstring at `bot.py:3222`), so by
  `after_request` the session value is populated for free — the cheap read is
  also the well-populated one.
* **`webhook_app` is assigned twice, so hook registration needs proving, not
  reading.** A decorator can attach a hook to the discarded first app object
  and every unit test still passes. `tests/sentinel_integration/` asserts
  membership in `webhook_app.after_request_funcs` and that `bot.app is
  bot.webhook_app`.
* **`log_visitor` excludes `/api/` deliberately** (a SELECT+INSERT+COMMIT per
  request would tax every native call). That exclusion must *not* be copied by
  a buffered observer — the native app drives almost all traffic through
  `/api/`, so inheriting it would blind Sentinel to nearly everything. Pinned
  by `test_api_paths_are_not_excluded_the_way_the_visitor_log_excludes_them`.

## 5b. A dedupe default that silently eats the brute-force signal

`events.Event` derives `dedupe_key` from
`source|category|event_type|subject_type|subject_id|occurred_at`, and
`occurred_at` has **one-second** resolution. For the scheduled scrapers the
package was built around, that is correct idempotency. For anything observing
the request path it is not: 50 failed logins in one second share every one of
those fields, so 49 are discarded as "duplicates" — and volume is precisely
what makes a brute force detectable. The loss is silent and is named after a
virtue.

Any request-path emitter must therefore set `dedupe_key` explicitly. The
bridge uses `reqbridge:{uuid4}`; idempotency is not weakened by this, because
`flush()` pops events off the buffer before writing, so no event is ever
offered to `ingest()` twice.

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
| A rate-limit policy table | `bot.ABUSE_GUARD_PROTECTED` (auth/checkout paths), `pulse_security_core.HIGH_RISK_RATE_RULES` — `sentinel.rate_limit` counts, callers set limits |
| A session store | `mobile_security_sessions` |
| An injection scanner | `ai_security.scan_for_injection()` |
| A UNDX tool gate | `undx_tool_gateway.execute()` |
