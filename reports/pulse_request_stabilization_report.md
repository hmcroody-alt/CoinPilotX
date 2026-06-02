# Pulse Request Stabilization Report

Date: 2026-06-02

## Objective

Eliminate web-request blocking during startup and reduce `/pulse/reels` dependency
on expensive query/media paths after the production 524 investigation identified:

- Cloudflare 524 on `GET /pulse/reels?tab=for_you`.
- Database initialization/schema checks occurring close to request handling.
- Postgres connection reset/open transaction EOF events.
- `pulse_posts` lock warnings during Postgres maintenance.
- Repeated media stream requests immediately before the timeout window.

## Changes Applied

### Startup DB Initialization Guard

`bot.init_db()` is now guarded so normal Flask request handlers cannot run full
schema/index initialization. If a route still calls `init_db()` during a request,
the call logs `INIT_DB_REQUEST_SKIPPED` and returns immediately unless
`FORCE_INIT_DB=true`.

Gunicorn web workers now run database initialization during module import/startup
through `initialize_database_for_web_startup()`. This keeps migration work in the
startup path instead of user request paths.

Postgres startup initialization also uses an advisory lock so simultaneous web
workers do not run schema/index work over each other.

### Request Timing Diagnostics

The existing request performance log now includes:

- `duration_ms`
- `db_duration_ms`
- `r2_duration_ms`
- `db_queries`
- `db_connections`
- `rows_returned`
- `slowest_query_ms`
- `slowest_query`

Response headers now include:

- `X-Response-Time-Ms`
- `X-DB-Query-Count`
- `X-DB-Duration-Ms`
- `X-R2-Duration-Ms`
- `X-Rows-Returned`

Media stream requests measure R2 HEAD and GET duration separately.

### Reels Initial Load Reduction

The `/pulse/reels` browser shell now asks the feed API for 12 initial reels
instead of 18.

`pulse_reel_feed_payload()` no longer pulls a minimum of 40 posts for every Reels
feed request. It caps the initial request at 20 and uses 12 for the default
`for_you` load.

Audio metadata enrichment now uses one bulk query for selected audio tracks
instead of attempting per-reel lookups after the DB connection was already closed.

## Local Timing Evidence

Local authenticated test-client probe after the patch:

| Route | Avg | Last Status | Last Header Time | Rows | DB Queries |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/pulse` | 50 ms | 200 | 11 ms | 0 | 6 |
| `/pulse/reels?tab=for_you` | 8 ms | 200 | 9 ms | 0 | 5 |
| `/pulse/status` | 9 ms | 200 | 9 ms | 0 | 6 |
| `/api/pulse/reels/feed?limit=12&tab=for_you` | 39 ms | 200 | 37 ms | 12 | 10 |

The local shell route is light; the Reels API is the meaningful data path and is
now constrained to a small initial batch.

## Slowest Route

From the local probe, the slowest measured route was:

`/pulse` at 50 ms average, mostly from a first warm sample. The stable warm
sample was 11 ms.

The Reels page shell was 8-9 ms locally.

## Slowest Query

The new production logs will emit `slowest_query_ms` and `slowest_query` on each
request. Local SQLite queries were too fast for millisecond granularity in the
sample and recorded `0 ms`, but query count and row count headers are present.

## ANALYZE / Maintenance Finding

No explicit `ANALYZE` command was found in the application request path. The
production message `skipping analyze of "pulse_posts" --- lock not available`
is consistent with Postgres autovacuum/analyze or platform maintenance colliding
with application locks, not a direct app-issued `ANALYZE`.

The app-side risk was repeated `CREATE INDEX IF NOT EXISTS` / schema initialization
from request-triggered `init_db()` calls. Those calls are now prevented from
running during normal requests.

## Recommended Follow-Up Fix List

1. Monitor production `PERF_REQUEST` logs for `/pulse/reels`, `/api/pulse/reels/feed`,
   `/pulse/status`, `/pulse`, and `/api/pulse/media/*/stream`.
2. If Postgres still logs `pulse_posts` lock warnings, inspect autovacuum timing
   and long-running transactions around `pulse_posts`.
3. Consider moving migrations to a dedicated Railway release command or manual
   maintenance job so web workers only verify readiness.
4. Add persisted columns to `performance_traces` for `db_duration_ms`,
   `r2_duration_ms`, `rows_returned`, and `slowest_query` if admin history needs
   more than live log evidence.
5. Add cursor-based pagination for Reels feed once infinite-scroll paging is added.

## Validation

- Python compile: PASS
- JavaScript parse: PASS
- `scripts/pulse_reels_media_audit.py`: PASS
- `scripts/pulse_performance_audit.py`: PASS
- `scripts/performance_audit.py`: PASS with one pre-existing warning for
  `static/js/pulse_live_studio.js` polling every 2000 ms.
- `scripts/site_functional_audit.py`: PASS with expected owner-only 403 warnings
  for Pulse Labs and UNDX.
- `git diff --check`: PASS

