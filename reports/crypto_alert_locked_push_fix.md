# Crypto Alert Locked-Screen Push Fix

## Root Cause

Crypto alert triggers were dispatched from `services/alert_engine.py` through the older alert delivery helpers. That path could create legacy in-app records and direct push attempts, but it did not consistently enter the central PulseSoc Notification System path that messages, comments, and reactions now use for locked-screen push delivery jobs.

The working locked-screen path requires:

`crypto alert trigger -> pulsesoc_notification_system.notify_crypto_alert -> notification record -> notification_delivery_jobs push row -> provider adapter`

## Crypto Alert Source

The active evaluated alert source is `services/alert_engine.py`.

Alerts are evaluated by:

- `evaluate_alert_rule(...)`
- `trigger_alert(...)`
- `dispatch_alert_event(...)`

`alert_worker.py` runs this engine. Dashboard alert CRUD remains separate from delivery.

## What Changed

- Repaired `notify_crypto_alert(...)` in `services/pulsesoc_notification_system.py`.
- Routed `services/alert_engine.py` crypto alert dispatch through the central helper.
- Preserved Telegram as the only legacy external channel because it is not part of the central push/email/SMS adapter set.
- Added source metadata for alert type, trigger price, target price, direction, and trigger window.
- Added a trigger-window dedupe key so worker retries do not duplicate alerts, while later valid triggers can notify again.
- Added `scripts/crypto_alert_push_notification_audit.py`.

## Notification Type

Central event type:

`crypto_alert_triggered`

Supported normalized crypto alert metadata types:

- `price_target_reached`
- `large_market_movement`
- `portfolio_milestone`
- `wallet_activity`
- `bot_signal`
- `critical_market_alert`

Only currently evaluated alert-rule sources are wired from the worker.

## Push Jobs

Eligible crypto alerts now create:

- a central `notifications` row
- an `in_app` delivery job
- a `push` delivery job when user preferences allow push

Provider failures or missing provider credentials are recorded by the delivery processor as skip/failure statuses such as `config_missing`; no fake success is returned.

## Deep Link

Crypto alerts use:

`/pulse/alerts/<alert_rule_id>`

That route is already present and redirects into the dashboard crypto alert surface.

## Dedupe and Cooldown

Alert-rule cooldown remains enforced by `evaluate_alert_rule(...)`.

Central notification dedupe uses:

`crypto-alert:<user_id>:<alert_id>:<symbol>:<alert_type>:<trigger_window>`

The trigger window uses `alert_event_id` when available, which prevents duplicate push jobs on retries while allowing a later valid alert event after cooldown.

## QA Results

`scripts/crypto_alert_push_notification_audit.py` verified:

- crypto alert worker calls the central helper
- eligible crypto alert creates central notification
- eligible crypto alert creates push delivery job
- deep link is present and alert-specific
- sound/vibration metadata is present
- same alert event does not duplicate push
- later alert event can create a fresh notification
- push-disabled user gets in-app only
- provider-missing push delivery is tracked as `config_missing`
- existing locked-device audit still contains crypto helper coverage

## Provider Requirements

Locked-device delivery still depends on configured provider credentials and valid device tokens:

- Web Push: `WEB_PUSH_PUBLIC_KEY`, `WEB_PUSH_PRIVATE_KEY`, `WEB_PUSH_SUBJECT`
- APNs: `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY`, `APNS_BUNDLE_ID`
- FCM: `FCM_PROJECT_ID`, `FCM_CLIENT_EMAIL`, `FCM_PRIVATE_KEY`

Without these, push jobs are created but delivery processing records `config_missing`.

## Remaining Limits

- Non-evaluated alert types remain metadata-supported but are not fake-triggered.
- SMS delivery remains preference- and provider-gated.
- Actual locked-phone receipt requires a valid registered device token and configured push provider.
