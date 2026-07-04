# PulseSoc Lightspeed Fixes

Date: 2026-07-03

## Applied fixes

### Mission Control

- Added summary/detail modes to `build_mission_control_dashboard()`.
- `/dashboard` now loads summary data only.
- `/api/dashboard/mission-control` returns summary mode by default; `?detail=1` preserves full diagnostic data.
- Replaced repeated table-existence probes with one schema inventory per dashboard build.
- Added `content-visibility` containment for below-fold dashboard sections.

Result:

- Before: 277 ms, 819 queries.
- After: 23-34 ms, 6-7 queries.
- Query reduction: over 99%.

### Growth Center

- Existing Growth Engine records are read directly.
- Full provisioning now runs only when the user is genuinely missing a Growth Engine.
- Removed foreground schema/provisioning churn from normal page views.

Result:

- Before: 86 queries.
- After: 29 queries.

### Calls Command Center

- Removed notification schema initialization from the read-only dashboard summary.
- Existing delivery tables are queried directly with graceful fallback if unavailable.

Result:

- Before: 107 warm-request queries.
- After: 41 queries.

### Realtime clients

- Messenger fallback polling is now:
  - 3 seconds while realtime is degraded.
  - 15 seconds as a reconciliation check while realtime is connected.
  - 60 seconds while the page is hidden.
- Call incoming-state fallback polling is now:
  - 6.5 seconds while realtime is unavailable.
  - 30 seconds while realtime is connected.
- Realtime reconnect/fallback events trigger immediate reconciliation.

### Frontend loading

- Deferred Messenger's shared media renderer.
- Preserved ordered deferred loading for realtime, notifications, LiveKit, calls, and Messenger.
- Kept LiveKit page-scoped instead of loading it globally.

### Database

- Added `idx_notification_delivery_jobs_user_created`.
- Added `idx_admin_audit_logs_created`.
- Added `idx_admin_audit_logs_admin_created`.
- Added PostgreSQL-compatible migration `migrations/pulsesoc_lightspeed_indexes.sql`.

### Performance gates

- Added authenticated route latency/query audit.
- Added static asset/cache/defer audit.
- Added database index/query-budget audit.
- Added worker queue/retry/dead-letter audit.
- Added master Lightspeed release audit.

## Security preservation

- Authentication and ownership checks were not bypassed.
- Admin routes remain admin-protected.
- Private pages remain `no-store`.
- Static cache changes were not broadened to private data.
- No provider keys, call tokens, or user content are emitted by the audits.
