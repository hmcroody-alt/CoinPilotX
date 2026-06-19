# PulseSoc Command Center Presence Engine

Date: 2026-06-19

## Architecture

Service 2B adds a production-safe presence foundation while leaving current messaging behavior in the Main App.

- Main App records lightweight authenticated activity on selected PulseSoc routes.
- Main App stores local `user_presence` rows so presence works with PostgreSQL/SQLite first.
- Main App optionally dispatches presence events through `services/command_center_client.py` when `COMMAND_CENTER_ENABLED=true`.
- Command Center Worker accepts protected presence updates and persists them to the same `user_presence` model.
- Redis is not required in this phase.

## Persistence

New table:

`user_presence`

Columns:

- `user_id`
- `status`
- `last_seen_at`
- `last_active_at`
- `source`
- `device_label`
- `updated_at`

Indexes:

- `idx_user_presence_user_id`
- `idx_user_presence_status`

Creation is migration-safe via `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`. Existing data is not deleted or rewritten.

## Worker Endpoints

Protected with `Authorization: Bearer <COMMAND_CENTER_INTERNAL_TOKEN>`:

- `POST /internal/command-center/presence/update`
  - Accepts `user_id`, `status`, `source`, and `device_label`.
  - Valid statuses: `online`, `away`, `offline`.
  - Rejects invalid user IDs and invalid statuses.

- `GET /internal/command-center/presence/<user_id>`
  - Returns `user_id`, `status`, `last_seen_at`, `last_active_at`, `source`, `device_label`, and `updated_at`.

Existing worker health remains:

- `GET /internal/command-center/health`

## Main App Hooks

Authenticated GET activity is tracked on:

- `/pulse`
- `/pulse/messages`
- `/pulse/messages-v2`
- `/pulse/profile`
- `/pulse/reels`
- `/pulse/videos`
- `/pulse/status`

Activity is throttled per session for 60 seconds. Static assets and broad APIs are not tracked by this hook.

When Command Center dispatch is disabled, `enqueue_presence_event()` safely logs/no-ops and does not slow down user requests.

## UI Integration

Messages V2 conversation rows now receive real `user_presence` data when available for the visible direct-message peer.

Presence dots use existing classes:

- `presence-online`
- `presence-away`
- `presence-offline`

If no presence row exists, the UI remains neutral/offline rather than showing fake online states.

## Security Notes

- Worker presence endpoints require internal bearer token auth.
- Invalid statuses are rejected.
- User IDs are validated as positive integers.
- Health and presence payloads do not expose tokens, database URLs, Redis URLs, filesystem paths, or secrets.
- UI presence remains constrained by current conversation visibility and block/access checks in the Messages V2 service.
- No private/admin-only presence data is exposed publicly.

## Stale Cleanup

Implemented in `services/command_center_worker/presence.py`:

- No activity for 5 minutes transitions `online` to `away`.
- No activity for 15 minutes transitions `online` or `away` to `offline`.

The cleanup function is callable by audits/manual operations. A scheduler can be added in a later worker phase.

## Redis Future Plan

Redis can later accelerate:

- high-frequency online heartbeats
- websocket fanout
- typing indicators
- ephemeral device/session presence
- unread/presence pub-sub

PostgreSQL remains the source of truth for durable `last_seen` and `last_active` state.

## QA Results

Expected validation:

- `python -m py_compile bot.py services/command_center_client.py services/command_center_worker/app.py services/command_center_worker/presence.py scripts/command_center_presence_audit.py`
- `python scripts/command_center_presence_audit.py`
- `git diff --check`
- Main App startup smoke test.
- Worker startup smoke test.
- Worker health endpoint test.
- Presence update rejects missing token.
- Presence update accepts valid token.
- Messages page route still loads or auth-redirects normally.

## Current Scope Boundary

No messaging delivery, websocket fanout, push notification behavior, AI summaries, media processing, or current Messenger logic has moved to the worker yet.
