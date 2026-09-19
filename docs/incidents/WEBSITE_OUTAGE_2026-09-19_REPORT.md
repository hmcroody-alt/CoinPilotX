# Website outage — 2026-09-19

pulsesoc.com stopped answering for 10 minutes 23 seconds. It returned no errors while it
was down: every request that eventually completed returned `200`. The cause was a
self-inflicted PostgreSQL lock convoy on the mobile session table, triggered by DDL that
ran on every authenticated request.

## Summary

| | |
|---|---|
| Incident start | 2026-09-19 ~02:17 UTC (progressive degradation) |
| Total silence | 02:18:39.465 → 02:29:02.215 UTC (**622.8 s**) |
| Detection | User report; confirmed externally by probe |
| Recovery | 02:29:02 UTC, self-recovered (no operator action) |
| Deployment SHA before | `469eea07` → `36f617c1` (unrelated deploy at 02:33:53 UTC, **after** recovery) |
| Deployment SHA after fix | `27ad5730` |
| Customer-visible errors | **None.** Zero 5xx. Requests hung, then succeeded. |

## Root cause

`ensure_mobile_security_session_schema()` ran three `CREATE INDEX IF NOT EXISTS`
statements against `mobile_security_sessions` on **every** authenticated request, and the
same database transaction then `UPDATE`d that table — so each in-flight request held a
`ShareLock` while asking for a `RowExclusiveLock` that every other in-flight request's
`ShareLock` blocked.

The detail that makes this a total stall rather than a slowdown:

- `CREATE INDEX IF NOT EXISTS` takes its `ShareLock` **before** checking whether the index
  exists. The statement is a no-op; the lock is not.
- `ShareLock` is **self-compatible**, so any number of concurrent requests acquire it
  happily. Nothing is slow yet.
- `ShareLock` **conflicts with `RowExclusiveLock`**. So the moment each of those
  transactions reaches its `UPDATE`, it must wait for every *other* transaction to commit.
- Every transaction is now waiting for every other one. There is no holder to blame and no
  single slow query — the pool is 100% waiters.

PostgreSQL's deadlock detector broke pairs of these apart (2,041 lifetime deadlocks,
69 in the fifteen minutes around the incident), but with requests still arriving the
convoy re-formed faster than it drained.

### Why it presented as "the website is down" and not "the website is erroring"

`gunicorn`'s `gthread` worker `--timeout 120` watches the **worker heartbeat**, not an
individual request. A thread blocked inside libpq waiting on a lock keeps its worker's
heartbeat alive, so the worker was never recycled and the request was never killed — it
just waited. With 4 workers × 8 threads = 32 request slots, all 32 filled with waiters and
the site went silent while remaining, by every liveness check Railway makes, healthy.

Requests reached **707 s** and still returned `200`. The first one through the wall was
`GET /r/cpxeqaejm5j4g`, `duration_ms=665755`, of which `db_duration_ms=665747`.

## Contributing factors

1. **A guard existed and was bypassed.** `ensure_mobile_security_session_schema_once()`
   — a `@schema_guard.run_once_per_process` wrapper around exactly this DDL — was already
   written, already tested, and already correct. Seven request-reachable call sites simply
   called the raw function sitting next to it.
2. **The regression test asserted the wrong property.** `tests/test_schema_ddl_once.py`
   checked that guarded functions carry `__wrapped_ddl__` — i.e. that the guard is
   *decorated*. The guard was decorated. The test passed throughout the outage. What was
   never checked is whether anything still *reaches around* it.
3. **The audited call-site list contained no `bot.py` entries.** All 17 entries in
   `HOT_CALL_SITES` are `services.*` modules. The monolith was outside the audit.
4. **SQLite cannot reproduce it.** `CREATE INDEX IF NOT EXISTS` is free on SQLite and a
   lock on PostgreSQL. The entire local test suite runs on SQLite, so no test could have
   failed on this regardless of coverage.
5. **No alerting on lock waits or deadlock rate.** The signal was clean and available in
   `pg_stat_database` the whole time; nothing was watching it.

## Impact

- pulsesoc.com fully unresponsive for 10 m 23 s; degraded (5–13 s responses) for roughly
  90 s before that.
- Web, mobile API, and any authenticated surface affected equally — the lock was on the
  shared session table, so the blast radius was every logged-in request.
- No data loss. No corruption. No failed writes: transactions waited and then committed.
- 30.9 % lifetime transaction rollback rate on the database, inflated by deadlock victims.

## Timeline (UTC)

| Time | Event |
|---|---|
| ~02:17 | Response gaps grow to 5–13 s. Convoy forming. |
| 02:18:39.465 | Last log line. Site goes silent. |
| 02:18–02:29 | All 32 request slots held by lock waiters. No 5xx, no worker timeout, no pool timeout. 57/500 DB connections in use — not a capacity limit. |
| 02:29:02.215 | Convoy drains. Backlog flushes as a thundering herd. |
| 02:33:53 | Unrelated deploy `36f617c1` goes out — **after** recovery, not the cause of it. |
| 02:52 | Site verified healthy, sub-second. Root cause still live in production. |

## Evidence

1. **Deductive.** Two processes each *waiting* for `RowExclusiveLock` on the same relation
   while blocking each other is impossible for plain `UPDATE`s — `RowExclusiveLock` does
   not self-conflict. Each blocker must already hold a conflicting mode. The only
   statements in that transaction that take one are the three `CREATE INDEX`.
2. **Identity confirmed in production.** Relation `100664` = `mobile_security_sessions`;
   database `16384` = `railway`.
3. **Per-request DDL observed live.** `COLUMN_EXISTS_SKIPPED table=mobile_security_sessions`
   interleaved with every token refresh — 176 occurrences in a single minute at peak.
4. **The repo already documented the mechanism.** `services/schema_guard.py`'s docstring
   describes this exact failure, including the gunicorn heartbeat detail, from a prior
   incident of identical shape.
5. **Shape of the stall.** All 32 requests over 60 s were >90 % `db_duration`; non-DB time
   was 68–122 ms. Every one a waiter, no holder — the lock-convoy signature.

## Ruled out

DNS (consistent CNAMEs across four resolvers and the TLD authority), Cloudflare (not in the
path at all — Namecheap BasicDNS points straight at Railway), TLS, Railway crash or restart
(all services `● Online` throughout), a bad deployment, resource exhaustion (57/500
connections, no pool timeouts), WAF or rate limiting, and missing environment variables
(nothing missing or empty on the boot path).

## Fix

Seven call sites changed from `ensure_mobile_security_session_schema(cur)` to
`ensure_mobile_security_session_schema_once(cur)`:

| Function | Surface |
|---|---|
| `save_reset_password_and_verify` | password reset |
| `issue_mobile_security_tokens` | login |
| `rotate_mobile_refresh_token` | token refresh — the path in the logs |
| `revoke_mobile_refresh_token` | logout |
| `revoke_all_mobile_security_sessions` | revoke-all |
| `account_security_payload` | account security screen |
| `messenger_media_cookie_user_id` | messenger media auth |

This removes statements. It changes no transaction boundary, no commit point, no schema,
and no behaviour — the DDL now runs once per worker process instead of once per request,
which is the same guarantee, since `init_db()` creates the table at boot and workers are
replaced on deploy.

Two call sites deliberately still call the raw function: the guard wrapper itself, and
`_init_db_impl`, which runs once at boot before any request is served.

### Prevention

`tests/test_schema_ddl_once.py` gains `UnguardedSiblingIsNotReachableTest`, which parses
`bot.py` with `ast` and asserts the raw DDL function is called **only** by its own guard
and by `_init_db_impl`. This tests *reachability* rather than *decoration* — the property
whose absence let this ship. Verified non-vacuous against the pre-fix tree: 8 offenders
before, 0 after.

## Verification

- New test fails on pre-fix code, passes on fixed code.
- `tests/test_schema_ddl_once.py` — all green.
- Auth/session suites: failure sets diffed head-vs-base in an isolated worktree and found
  **byte-identical** (42 pre-existing failures both sides, 0 introduced).
- Real-time audio change gate: no protected path touched.
- Protection suite: green on a clean tree. (Two gates fail in the shared development
  checkout because of unrelated uncommitted `.env.example` and entitlements edits belonging
  to other work — they pass on the deploy candidate.)

## Follow-ups

**Done — `f653f0d6`, `services/pg_lock_health.py`.** Alerting on the
`pg_stat_database.deadlocks` rate and on `pg_stat_activity` lock waits. The signal was
present and unwatched for the entire outage. It samples from `alert_worker`, the one
process still running while every web thread was blocked in libpq, and it opens its own
connection rather than borrowing a pool a convoy would already have drained. Thresholds
are the `PG_LOCK_*` / `PG_DEADLOCKS_*` variables in `.env.example`; `PG_LOCK_ALERT_EMAIL`
falls back to `OWNER_ADMIN_EMAIL`, and with neither set the monitor still logs
`PG_LOCK_HEALTH_ALERT` at ERROR every cycle the condition holds.

One note for whoever reads this next. The first version of that probe asked
`pg_stat_activity` for `wait_start` — a column that exists in no PostgreSQL version. All
nineteen unit tests passed against it, because a recording cursor accepts any string.
Running it once against production is what found it. That is the same blind spot which
produced this incident in the first place: the test suite runs on SQLite, and SQLite has
neither the lock that caused the outage nor the catalog that reveals it.

### Alerting runbook

Delivery was wired up on 2026-09-19, after the code above had already been running blind
for several hours: it was sampling correctly and had nowhere to send anything.

**Confirmed delivered end to end on 2026-09-19** — a test alert sent from inside the
`alert_worker` container arrived in the owner's inbox. Recording that explicitly because
the two are not the same claim: Brevo returning `201` with a `messageId` only proves it
accepted the message, and an address on a suppression list produces exactly that same
`201`. Every link from the worker's own connection through to a human has now been
observed, not inferred.

| | |
| --- | --- |
| Monitor | `services/pg_lock_health.py`, called once per sweep from `alert_worker.py` (~48 s) |
| Host service | Railway `python alert_worker.py` — chosen because it is not serving requests |
| Destination | `PG_LOCK_ALERT_EMAIL`, falling back to `OWNER_ADMIN_EMAIL` |
| Provider | Brevo, via `services/email_service.send_email` → HTTPS. No database involvement |
| Sender | `BREVO_SENDER_EMAIL` (`support@pulsesoc.com`) |
| Cooldown | `PG_LOCK_ALERT_COOLDOWN_SECONDS`, default 900 s, **per alert kind** |

**Fires when** the deadlock *rate* exceeds `PG_DEADLOCKS_PER_MIN_THRESHOLD` (3/min), or
lock waiters reach `PG_LOCK_WAITERS_THRESHOLD` (5), or the longest waiter reaches
`PG_LOCK_WAIT_SECONDS_THRESHOLD` (30 s), or the probe itself cannot sample. The first
three share the kind `lock_contention`; the last is `sample_failed` and throttles
separately, because "the database is in a convoy" and "the monitor is blind" are different
findings and must not silence each other.

The ERROR-level `PG_LOCK_HEALTH_ALERT` log line is written every cycle the condition
holds and is deliberately *not* cooldown-suppressed. Only the email is throttled. The log
is the forensic record — during this incident its absence is what made the timeline hard
to rebuild.

**When an alert fires,** read the log line first: it carries the rate, waiter count,
longest wait and the top contended relations. A relation name there usually identifies the
statement. Then check whether anything has deployed recently, since the cause here was
per-request DDL taking a `ShareLock`. `waiters` climbing with `deadlocks_per_min` flat is
a convoy, not a deadlock storm — nothing will resolve it on its own, and Postgres will not
break it for you the way it breaks a deadlock.

Three traps for whoever maintains this:

- **Railway injects variables at container start.** Setting `PG_LOCK_ALERT_EMAIL` does not
  reach the running process; the service must redeploy. Verify from inside the container,
  not from the dashboard.
- **The `OWNER_ADMIN_EMAIL` fallback is inert by default.** `bot.py:545` defines
  `OWNER_ADMIN_EMAIL` as a Python literal, not an environment read, so nothing sets the
  variable that `_escalate` looks for. Treat `PG_LOCK_ALERT_EMAIL` as required.
- **The worker has no Brevo key of its own.** `BREVO_API_KEY` there is a Railway reference
  to the web service's variable, so there is exactly one key to rotate — but a rotation
  that replaces the variable rather than its value would silently unhook the alert.

To re-verify delivery without manufacturing contention, call `email_service.send_email`
directly with the resolved recipient and an obviously-marked test subject. Do not create a
deadlock in production to test a deadlock alert.

### Still outstanding

- Extend the reachability check to the other guarded DDL functions; this one was fixed
  because it caused an outage, not because it was uniquely exposed.
- `HOT_CALL_SITES` audits `services.*` only. `bot.py` holds the hot auth paths.
- `ensure_schema(conn)` has roughly 26 unswept call sites that pass a request connection,
  which suppresses the commit and can reproduce a related stall.
- `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`, `WEB_CONCURRENCY` and
  `WEB_THREADS` are unset in production, so defaults (8+8, 3 s, 4 workers, 8 threads)
  apply. Not a cause here — worth setting deliberately rather than by omission.
