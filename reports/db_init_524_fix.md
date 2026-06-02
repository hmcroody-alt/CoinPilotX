# DB Init 524 Fix

Date: 2026-06-02

## Production Root Cause

Railway evidence showed the 524 was caused by application startup blocking live
requests, not an OOM, platform crash, failed health check, or Cloudflare rule.

Confirmed symptoms:

- Container started normally at `2026-06-02 19:19:48 UTC`.
- Multiple `DB init started` logs appeared from `19:20:29` through `19:21:30`.
- `GET /pulse/reels?tab=for_you` hung for about 125 seconds.
- Postgres logged connection resets and open transaction EOF events.
- Postgres logged `skipping analyze of "pulse_posts" --- lock not available`.
- Boot logs also showed `SESSION_SECRET loaded=False` in production.

## Why DB Init Started Repeatedly

The repository still had many legacy `init_db()` calls inside page routes, API
routes, admin routes, and helper flows. Even though `init_db()` had an in-memory
completion guard, each Gunicorn worker has its own process memory. With multiple
workers/threads and request-triggered calls, production could start schema/index
checks more than once during traffic.

The prior partial fix made `init_db()` skip during request context, but Gunicorn
import still ran DB initialization synchronously. That meant a worker could still
block request readiness while startup schema work was running.

## Fix Applied

### Request-Time DB Init Blocked

`init_db()` now immediately returns during Flask request context unless
`FORCE_INIT_DB=true`. It logs:

`INIT_DB_REQUEST_SKIPPED`

This means route-level legacy calls no longer run migrations during user traffic.

### Startup Init Runs Once Per Worker, Non-Blocking In Production

Web startup now defaults to async DB initialization in deployment environments.
The app starts a background daemon thread and continues serving requests instead
of blocking Gunicorn import/request readiness.

Production default:

`COINPILOTX_DB_INIT_STARTUP_MODE=async`

Local/audit default:

`COINPILOTX_DB_INIT_STARTUP_MODE=sync`

The mode can be overridden safely.

### Global Init Lock

A process-level `threading.Lock()` prevents concurrent initialization inside one
worker process.

For Postgres, DB init also uses non-blocking advisory locking:

`pg_try_advisory_lock(620260524)`

If another worker already owns the init lock, the current worker logs that DB init
is already running and returns instead of waiting.

### Timeout Protection

DB initialization now applies Postgres statement and lock timeouts:

- `DB_INIT_TIMEOUT_SECONDS`, default `45`
- `DB_INIT_STATEMENT_TIMEOUT_MS`, derived from timeout and capped
- `DB_INIT_LOCK_TIMEOUT_MS`, default `2500`

If init exceeds the timeout target, it logs:

`DB_INIT_EXCEEDED_TIMEOUT`

### Required Boot Logs Added

The app now emits:

- `DB_INIT_STARTED_ONCE`
- `DB_INIT_COMPLETE_ONCE`
- `DB_INIT_SKIPPED_ALREADY_DONE`

Async startup also logs:

- `DB_INIT_BACKGROUND_STARTED`
- `DB_INIT_BACKGROUND_SKIPPED_ALREADY_STARTED`
- `DB_INIT_BACKGROUND_FAILED` if startup init fails

### Hot Pulse/Reels Paths Cleaned

Removed direct `init_db()` calls from:

- `/api/pulse/feed`
- `/api/pulse/reels/feed`
- `/pulse/reels/<id>`

The media stream route already avoided per-request schema initialization.

### Request Timing Diagnostics Kept

Request logs include:

- route
- total duration
- DB duration
- R2 duration
- DB query count
- DB connection count
- rows returned
- slowest query fingerprint

## SESSION_SECRET Warning

The app already used `SECRET_KEY` / `FLASK_SECRET_KEY` fallback for Flask session
signing. Boot logs now explicitly state when `SESSION_SECRET` is absent but the
secret fallback is present:

`SESSION_SECRET missing; using SECRET_KEY/FLASK_SECRET_KEY fallback`

Production recommendation:

Set `SESSION_SECRET` in Railway to the same stable value family as
`SECRET_KEY`/`FLASK_SECRET_KEY`, or keep the documented fallback intentionally.
Do not use a random per-boot secret in production.

## ANALYZE Lock Warning

No application request path was found issuing explicit `ANALYZE` or `VACUUM`.
The `pulse_posts` warning is consistent with Postgres autovacuum/analyze or
platform maintenance encountering app-held locks. The app-side fix is to stop
request-time schema/index work from competing with Postgres maintenance.

## Validation

- Python compile: PASS
- Site functional audit: PASS
- Performance audit: PASS with one pre-existing polling warning for
  `static/js/pulse_live_studio.js`
- `/pulse`: PASS locally, 22 ms in performance audit
- `/pulse/reels`: PASS locally, 14 ms in performance audit
- Hot path scan: `/api/pulse/feed`, `/api/pulse/reels/feed`,
  `/pulse/reels/<id>`, and media stream route do not contain `init_db()`
- `git diff --check`: PASS

## Production Verification Plan

After deployment:

1. Refresh `/pulse` repeatedly.
2. Refresh `/pulse/reels?tab=for_you` repeatedly.
3. Confirm no 524.
4. Confirm Railway logs show one DB init start/complete sequence per worker, not
   repeated request-time `DB init started` logs.
5. Confirm any remaining request-time legacy calls show only
   `INIT_DB_REQUEST_SKIPPED` and do not block.
6. Watch `PERF_REQUEST` logs for `/pulse`, `/pulse/reels`,
   `/api/pulse/reels/feed`, and `/api/pulse/media/*/stream`.

