# PulseSoc Notification System Audit

Date: 2026-06-19

## Current Tables

- `pulse_notifications`: primary PulseSoc in-app notifications, read state, actor/entity references, deep links, and metadata.
- `pulse_notification_deliveries`: per-channel delivery attempts and provider status.
- `pulse_notification_preferences`: per-category in-app/email/SMS/push preferences.
- `notifications`: legacy/general notification records.
- `notification_preferences`: legacy sound, vibration, email, SMS, and push preferences.
- `notification_delivery_logs`, `notification_logs`, `notification_jobs`, and `notification_failures`: provider and queue diagnostics.

## Current Routes

- `GET /pulse/notifications`: authenticated PulseSoc alerts page.
- `GET /api/pulse/notifications`: recent non-message alerts.
- `GET /api/pulse/notifications/unread-count`: alert and chat badge counts.
- `GET /api/pulse/badge-counts`: explicitly separated alert/chat counts.
- `POST /api/pulse/notifications/<id>/read`: mark one alert read.
- `POST /api/pulse/notifications/read-all`: mark alerts read.
- `DELETE /api/pulse/notifications/<id>`: delete one alert.
- `/admin/notifications`, `/admin/notification-delivery`, and email health routes: provider/admin diagnostics.

## Unread And Badge Behavior

`notification_service.pulse_badge_counts()` returns:

- `alert_unread_count`: unread non-message notifications.
- `chat_unread_count`: unread conversation/message state.
- `total_unread_count`: aggregate for diagnostics only.

`static/notifications.js` renders these separately:

- Alerts: `[data-alert-unread], [data-notification-unread]`
- Chat: `[data-chat-unread]`

General alert queries explicitly exclude message notification types, message entities, and message/chat deep links.

## Current Notification Sources

The centralized notification service and legacy `notify_user()` helper currently create real notifications for:

- post comments and reactions
- follows and friend activity
- status reactions/replies
- reel/video reactions and comments
- live and creator activity
- account/security events
- marketplace/premium/teacher events
- existing message notifications

Service 2D mirrors only non-message in-app events into the Command Center pipeline.

## Admin And Security Alerts

Admin/security surfaces already record authentication and security events and send account/security notifications through authorized existing paths. Service 2D mirrors notifications only after those local permission decisions have selected a recipient.

## Existing Delivery Infrastructure

Push, email, SMS, Telegram, Brevo, Expo, and provider logging already exist in the current app. Service 2D does not replace, enable, or expand those delivery paths.
