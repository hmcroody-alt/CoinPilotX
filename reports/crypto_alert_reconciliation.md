# Crypto Alert Reconciliation Report

## Root Cause

Crypto alerts had two active data paths:

- `alert_worker.py` evaluates `services.alert_engine.evaluate_all_active_alerts()`, which reads `alert_rules`.
- Crypto Command Center `Manage My Alerts` used `services.dashboard_crypto_command_center.list_alerts()`, which read `crypto_alerts`.

That split allowed lock-screen crypto notifications to fire from `alert_rules` while the user-facing dashboard displayed only the separate `crypto_alerts` records. The result was ghost alerts: notifications without a matching alert card the user could pause, edit, duplicate, delete, or inspect.

## Tables And Models Found

- `alert_rules`: canonical alert engine table. Worker reads this table.
- `alert_events`: trigger history for alert evaluations and fired events.
- `alert_delivery_jobs`: legacy delivery job mirror used by alert delivery logging.
- `notification_delivery_logs`: per-channel delivery status for alert events.
- `notification_delivery_jobs`: central notification delivery jobs created by the notification OS.
- `notifications`: central in-app notification records.
- `crypto_alerts`: previous Crypto Command Center table. Now treated as a legacy compatibility source and imported into `alert_rules`.
- `user_alerts`: older portfolio helper table. It is not read by the current alert worker.
- `user_alert_rules`: older dashboard count table. It is not read by the current alert worker.

## Routes And Services Found

- Worker: `alert_worker.py`
- Evaluator: `services/alert_engine.py`
- Crypto dashboard service: `services/dashboard_crypto_command_center.py`
- Crypto dashboard routes and API: `bot.py`
- Notification helper: `services/pulsesoc_notification_system.py`
- Notification fallback routing: `static/notifications.js`
- Dashboard mission metadata: `services/pulse_dashboard_mission_control.py`
- AI Advisor dashboard metric: `services/pulsesoc_dashboard_centers.py`

## Source Of Truth Chosen

`alert_rules` is now the official source of truth because it is already the table used by the production worker and central crypto notification path.

`crypto_alerts` is retained only as a legacy compatibility/import table. Dashboard-created alerts no longer write to it.

## Legacy Migration / Import Behavior

`services.alert_engine.reconcile_legacy_alerts()` imports `crypto_alerts` rows into `alert_rules` with:

- `source = migrated_crypto_alerts`
- `source_ref = crypto_alerts:<legacy_id>`
- Original owner, symbol, condition, target, status, notification channels, and timestamps preserved where available.

The import is idempotent and skips rows already imported by `source_ref`.

Legacy `user_alerts` are counted and reported by the reconciliation helper, but they are not automatically activated because the current worker does not read that table. Activating old dormant records could surprise users, so only worker-relevant/dashboard-created legacy records are migrated.

## UI Changes

`Manage My Alerts` now renders mobile-safe alert cards instead of the broken table layout.

Each alert card shows:

- Asset symbol
- Condition and target value
- Status
- Last triggered
- Trigger count
- Cooldown seconds
- Source
- Notification channels
- Pause / Resume / Edit / Duplicate / History / Delete actions

The card layout avoids horizontal overflow and prevents narrow mobile columns from wrapping into single-letter headings.

## Actions Implemented

- Pause: sets canonical `alert_rules.status = paused` and `active = 0`.
- Resume: sets canonical `alert_rules.status = active` and `active = 1`.
- Edit: updates symbol, condition, target value, alert type, channels, and metadata on `alert_rules`.
- Delete: soft-deletes by setting `status = deleted`, `active = 0`, and `deleted_at`.
- Duplicate: creates a new `alert_rules` record owned by the same user with `source = duplicated`.
- History: returns `alert_events` for the canonical alert id.

All APIs require the authenticated user context and filter by `user_id`.

## Dedupe / Cooldown Fix

The worker already used `last_triggered_at` plus `cooldown_seconds` to avoid repeated triggers every worker tick. This pass strengthened notification idempotency by recording a cooldown-based trigger bucket in alert event metadata and using that bucket in the central crypto notification dedupe key.

Same alert + same cooldown bucket will not create duplicate central notifications on retry.

## Notification Integration

Crypto notifications now include:

- `source_type = crypto_alert`
- `source_id = alert_rules.id`
- `alert_rule_id`
- `alert_event_id`
- Symbol, observed value, target price, direction, and trigger bucket metadata
- Deep link: `/dashboard/crypto/alerts?alert_id=<alert_id>`

The frontend notification fallback also routes crypto alerts to the same Manage Alerts deep link.

## QA Results

Automated reconciliation audit passed:

- Legacy `crypto_alerts` row imported exactly once.
- Imported alert is visible in Manage My Alerts.
- Dashboard-created alert is inserted into `alert_rules`.
- Pause/resume update the canonical alert.
- Delete soft-deletes the canonical alert.
- Duplicate creates a separate manageable alert.
- History returns trigger events and delivery status.
- Reconciliation remains idempotent.
- Mobile card UI is present and the old alert table renderer is removed.
- Notification helper and frontend fallback use Manage Alerts deep links.

Commands run:

- `venv/bin/python -m py_compile bot.py services/alert_engine.py services/dashboard_crypto_command_center.py services/pulse_dashboard_mission_control.py services/pulsesoc_dashboard_centers.py services/pulsesoc_notification_system.py`
- `node --check static/notifications.js`
- `venv/bin/python -m py_compile scripts/crypto_alert_reconciliation_audit.py`
- `venv/bin/python scripts/crypto_alert_reconciliation_audit.py`

Browser QA:

- Mobile at 390x844 loaded `/dashboard/crypto/alerts?alert_id=1`, rendered the authenticated empty state for a user with no alerts, had no horizontal overflow, and logged no console errors.
- Desktop at 1280x900 loaded the same route, rendered the same canonical empty state, had no horizontal overflow, and logged no console errors.
- Browser QA did not create, edit, pause, resume, duplicate, or delete real user alerts. Those state transitions are covered by the isolated reconciliation audit.

## Remaining Limitations

- `user_alerts` and `user_alert_rules` remain legacy/dormant data stores. They are documented, not automatically activated.
- Volume spike and market-cap alert UI options were removed from the create form because the current live worker does not evaluate those metrics.
- True locked-device delivery still depends on a valid device token/subscription and provider configuration.
