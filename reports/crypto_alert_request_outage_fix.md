# Crypto Alert Request Outage Fix

## Incident

On 2026-07-03, `pulsesoc.com` stopped returning responses twice. Railway accepted DNS/TLS connections but returned no application bytes; the custom domain recovered after a clean redeploy. The second outage occurred immediately after a production crypto alert creation attempt.

## Root Cause

The crypto API route called the full `init_db()` application migration path inside normal user requests. Alert creation then repeated focused schema DDL, reconciled legacy alert tables, and opened additional database connections before completing the write. This made a simple alert request capable of occupying a Gunicorn worker with migration and lock work. With multiple workers sharing PostgreSQL, a stalled migration/lock path could exhaust the web service and remove all healthy upstream responses.

Railway CLI authentication was expired during the incident, so the final provider termination signal (worker timeout, memory kill, or platform restart) could not be read. The application defect and trigger path were confirmed directly in code and removed; the exact Railway kill reason remains unavailable until CLI access is renewed.

## Fix

- Removed full `init_db()` calls from every `/api/crypto/*` request route.
- Added focused per-worker schema readiness caches for crypto dashboard and canonical alert tables.
- Made PostgreSQL table checks use `information_schema` directly instead of first issuing SQLite SQL.
- Made column upgrades inspect existing columns before running `ALTER TABLE`.
- Removed legacy alert reconciliation from the create-alert request path.
- Reused one database connection and transaction for canonical alert insert plus audit log.
- Added rollback, connection cleanup, structured `503` handling, and correlation IDs for unexpected crypto API failures.
- Preserved the canonical `alert_rules` source of truth and notification-channel metadata.

## Verification

- Python compile passed for `bot.py`, `services/alert_engine.py`, and `services/dashboard_crypto_command_center.py`.
- Crypto Command Center audit passed.
- Crypto alert reconciliation audit passed.
- Crypto locked-screen push audit passed, including dedupe and delivery-job creation.
- Request safety audit created 20 canonical alerts in one bounded run and verified no full migration call remains in crypto API routes.
- Production health, liveness, readiness, homepage, login, offline fallback, and service worker checks returned expected responses after recovery.

## Operational Follow-up

Renew Railway CLI authentication and inspect the two deployment windows to record the provider-level worker termination reason. Application health no longer depends on that evidence because the unsafe request-path migration and nested transaction behavior have been removed.
