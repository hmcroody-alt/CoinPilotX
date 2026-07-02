# PulseSoc Notification System Foundation

## What Was Added

Built Phase 1 of the PulseSoc Notification Operating System: a centralized event intake, rule evaluation, notification record, delivery-job, device-token, unread-count, and API foundation. Existing PulseSoc notification UI endpoints now route through the new foundation while preserving legacy fallback behavior for older Pulse notification records.

## Files Changed

- `services/pulsesoc_notification_system.py`
- `services/db.py`
- `bot.py`
- `migrations/pulsesoc_notifications_foundation.sql`
- `scripts/notification_system_foundation_audit.py`
- `reports/notification_system_foundation.md`

## Database Tables And Migration

Added PostgreSQL-compatible migration `migrations/pulsesoc_notifications_foundation.sql`.

Tables:

- `notifications` expanded into the normalized notification record layer.
- `notification_events` for event intake, idempotency, and suppression status.
- `notification_delivery_jobs` for queue-ready delivery routing.
- `notification_device_tokens` for APNs/FCM/Web Push/PWA/native token foundation.
- `notification_preferences` expanded for quiet hours, sound, vibration, lock-screen previews, muted users/chats, and category rules.

The app startup schema path also calls the foundation schema initializer so local and production boot paths create the same base tables.

## APIs Added Or Wired

- `GET /api/notifications`
- `POST /api/notifications/read`
- `POST /api/notifications/read-all`
- `POST /api/notifications/test`
- `GET|POST /api/notification-preferences`
- `GET /api/pulse/notifications`
- `GET /api/pulse/notifications/unread-count`
- `GET /api/pulse/badge-counts`
- `GET|POST /api/pulse/notifications/<id>/resolve`
- `POST /api/pulse/notifications/<id>/read`
- `POST /api/pulse/notifications/read-all`
- `DELETE /api/pulse/notifications/<id>`
- `GET|PATCH /api/pulse/notifications/preferences`
- `POST /api/push/subscribe`
- `POST /api/push/register-device`
- `POST /api/push/unsubscribe`
- `POST /api/push/revoke-device`
- `GET /api/push/status`
- `POST /api/admin/notifications/test-event`

## UI Added Or Preserved

The existing PulseSoc notification bell, unread badge, notification dropdown, notification center, settings link, mark-read, mark-all-read, delete action, empty state, loading/error handling, and mobile responsive layout now have the new backend foundation behind them. No broad redesign was introduced in this phase.

## Security Checks Completed

- Users can fetch only their own `notifications` records.
- Admin simulator route requires `system.view` admin API permission.
- Frontend self-test can only create notifications for the authenticated user.
- Notification previews are privacy-safe for urgent/security/payment categories.
- Blocked users suppress normal social notifications where block data exists.
- Muted users and muted conversations suppress noisy non-urgent notifications.
- Deep links are normalized to safe internal routes.
- Device token APIs do not return push tokens or secrets.
- APNs/FCM/Web Push/Brevo/Twilio provider secrets remain environment-only placeholders.
- Duplicate events are stopped with deterministic dedupe keys.

## QA Performed

Commands run:

- `venv/bin/python -m py_compile services/pulsesoc_notification_system.py services/db.py scripts/notification_system_foundation_audit.py bot.py`
- `node --check static/notifications.js`
- `venv/bin/python scripts/notification_system_foundation_audit.py`
- Local guard restart and health check on port `5069`
- Browser QA: desktop `/pulse/notifications`
- Browser QA: mobile `390x844` `/pulse/notifications`

Audit results:

- Notification creation works.
- Fetch notification API foundation works.
- Server unread count is accurate.
- Mark one read works.
- Mark all read works.
- Deep links are returned.
- Cross-user notification access is denied.
- Admin test route is protected.
- Muted and blocked rules suppress expected events.
- Duplicate notification prevention works.
- Device token register/disable/status works.
- Migration avoids SQLite-only syntax.
- Browser UI had no console errors in desktop or mobile smoke tests.
- No horizontal overflow in desktop or mobile notification center smoke tests.

## What Is Ready

- Central event model for notification-producing systems.
- In-app notification creation and unread badges.
- Server-side notification counts.
- User preference/rules foundation.
- Queue-ready delivery jobs.
- Device token foundation for native/PWA push.
- Admin-safe test event creation.
- Existing notification UI connected to the foundation.

## Phase 2 Remaining

- Connect APNs for iPhone lock-screen notifications.
- Connect FCM for Android notifications.
- Connect Web Push provider delivery.
- Connect Brevo email delivery routing.
- Connect Brevo SMS delivery routing.
- Add incoming-call style notification adapters.
- Add push sound/vibration delivery policies per platform.
- Add notification permission onboarding and category UI expansion.
- Connect every product feature to the central `pulsesoc_notification_system.intake_event(...)` instead of legacy scattered notification calls.
