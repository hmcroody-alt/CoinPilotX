# PulseSoc Command Center Notifications Pipeline

Date: 2026-06-19

## Architecture

Service 2D adds an in-app notification event queue beside the existing PulseSoc notification system.

- Existing local notification writes remain authoritative.
- Successful non-message notifications are mirrored asynchronously when Command Center is enabled.
- Message-like notifications are rejected from the general alert pipeline.
- Existing push/email/SMS delivery is unchanged and is not invoked by the worker pipeline.

## Event Table

New table: `command_center_notification_events`

Fields:

- `id`
- `event_id`
- `recipient_id`
- `actor_id`
- `notification_type`
- `title`
- `body`
- `payload_json`
- `channel`
- `status`
- `read_at`
- `delivered_at`
- `created_at`
- `processed_at`

Indexes cover recipient, notification type, status, and creation time.

## Worker Endpoints

All require `Authorization: Bearer <COMMAND_CENTER_INTERNAL_TOKEN>`:

- `POST /internal/command-center/notifications/event`
- `GET /internal/command-center/notifications/unread/<user_id>`
- `GET /internal/command-center/notifications/recent/<user_id>`
- `POST /internal/command-center/notifications/read`

## Worker Methods

- `accept_notification_event()` validates and queues an event.
- `mark_delivered()` records in-app delivery state.
- `mark_read()` is recipient-scoped and supports one/all events.
- `get_unread_count()` returns alert-only counts.
- `get_recent_notifications()` returns a bounded recipient-only list.
- `process_pending_notifications()` marks only in-app events delivered and defers external channels.

## Current Integrations

Non-message events created through either of these paths are mirrored:

- `services.notification_service.create_pulse_notification()`
- `bot.notify_user()`

This covers current comment, reaction, follow, status, reel, video, admin/security, account, creator, and marketplace notification sources when they produce real local notifications.

Local mark-one and mark-all alert operations also mirror read state when the worker is enabled.

## Badge Separation

- Chat badge remains sourced from conversation/message unread state.
- Alerts badge remains sourced from non-message notification unread state.
- Worker notification events reject message, chat-message, voice-message, group-message, and room-message types.
- Reading alerts does not clear messages.
- Reading messages does not clear alerts.

## Disabled Worker Behavior

When `COMMAND_CENTER_ENABLED=false`:

- notification enqueue safely no-ops
- worker unread/recent helpers return `available: false`
- existing `pulse_notifications` routes and UI remain unchanged
- no user request waits for the worker

## Security

- Internal endpoints require bearer-token authentication.
- Reads and read updates are scoped by recipient ID.
- Main App mirrors only recipients already selected by existing permission logic.
- Titles/bodies are stripped of markup and bounded.
- Payload depth/size is bounded and secret/token/password/credential keys are removed.
- Tokens, database URLs, provider credentials, and filesystem paths are not returned.

## External Delivery Boundary

Push, email, and SMS channels may be represented for future work, but `process_pending_notifications()` deliberately defers them. Service 2D adds no external-provider sending.

## Next Phase Recommendation

Service 2E should introduce durable queue claiming, retry/backoff, provider receipts, dead-letter handling, and Redis/pub-sub only after the in-app pipeline is deployed and observed safely.

## QA

Validation covers compilation, endpoint authentication, accepted events, unread/recent shapes, recipient-scoped read state, message rejection from the alerts pipeline, disabled Main App behavior, badge selector separation, prior worker/messaging audits, worker HTTP smoke, Main App startup, and Alerts/Messages route behavior.
