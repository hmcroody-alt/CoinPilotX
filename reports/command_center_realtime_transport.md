# Command Center Realtime Transport Layer

Generated: 2026-06-19

## Goal

Service 2G adds a realtime transport foundation for presence, typing, messages, unread badges, and notifications without removing existing polling. The browser remains authenticated through the Main App. The Command Center Worker receives internal events, tracks connection state, and exposes protected realtime status/poll/SSE primitives for future Redis/WebSocket fanout.

## Architecture

| Layer | Responsibility |
| --- | --- |
| Main App | Authenticates browser sessions, serves Messages UI, emits existing Pulse SSE events, keeps polling fallback, optionally dispatches selected realtime events to Command Center |
| Command Center Worker | Tracks connected users/sessions/devices, manages conversation subscriptions, buffers routed events, exposes protected realtime poll/SSE/status endpoints |
| Browser | Uses `EventSource` when available, listens for normalized realtime event names, falls back to existing 12 second polling with reconnect backoff |

No browser code receives the internal Command Center token. Worker endpoints remain internal/protected.

## Transport Foundation

Implemented module:

- `services/command_center_worker/realtime_transport.py`

Protected internal endpoints:

- `POST /internal/command-center/realtime/connect`
- `POST /internal/command-center/realtime/disconnect`
- `POST /internal/command-center/realtime/subscribe`
- `POST /internal/command-center/realtime/event`
- `GET /internal/command-center/realtime/poll/<user_id>`
- `GET /internal/command-center/realtime/stream/<user_id>`
- `GET /internal/command-center/realtime/status`

The current transport is SSE-first with polling fallback. WebSocket support is intentionally not introduced in this phase because the existing app already has SSE and polling paths, and the task requires additive safety.

## Event Types

Supported Command Center realtime event types:

- `presence_updated`
- `message_created`
- `message_delivered`
- `message_read`
- `typing_started`
- `typing_stopped`
- `unread_count_updated`
- `notification_created`
- `security_alert_created`

Typing events are rate-limited to reduce noisy updates.

## Routing And Permissions

- Conversation events are routed only to active conversation participants.
- Message-created and typing events exclude the actor by default.
- Users can subscribe only to conversations where they are active participants.
- Polling a user stream only returns events addressed to that user.
- Worker endpoints require the internal bearer token.
- The Main App browser stream remains session-authenticated and never exposes internal worker credentials.

## Main App Integration

Updated:

- `services/command_center_client.py`
- `bot.py`
- `static/js/pulse_messages_v2.js`

Main App now has:

- `enqueue_realtime_event(...)`
- `get_realtime_status()`

`pulse_emit_event(...)` optionally dispatches selected realtime events to Command Center when `COMMAND_CENTER_ENABLED=true`. Failures are logged at debug level and do not block user requests.

Messages v2 now listens for:

- existing Pulse event names, such as `pulse_message_sent`, `pulse_typing`, and `pulse_message_seen`
- Command Center event names, such as `message_created`, `typing_started`, `typing_stopped`, `message_read`, `presence_updated`, and `unread_count_updated`

Existing polling remains:

- `pollRealtime()`
- `scheduleRealtimePoll(12000)`

## Admin Diagnostics

`/admin/system` now includes Command Center realtime diagnostics:

- active connections
- connected users
- events per minute
- failed sends
- reconnect count
- transport mode

Values are fetched through the Command Center client and fall back to `polling_fallback` if the worker is disabled or unavailable.

## Security Notes

- No secrets, tokens, database URLs, or filesystem paths are included in realtime responses.
- Payload sanitization drops sensitive keys such as token, secret, password, and credential.
- Browser JavaScript does not reference `COMMAND_CENTER_INTERNAL_TOKEN`.
- Conversation subscription and event delivery are permission-aware.
- Invalid event types are rejected.
- Internal endpoint authentication rejects missing or invalid tokens.

## Fallback Behavior

If realtime SSE is unavailable:

- the existing Messages v2 polling loop continues
- reconnect backoff stays quiet
- no scary user-facing error is shown
- UI continues to load through the existing API routes

## QA Results

Automated audit coverage:

- auth required for realtime endpoints
- invalid token rejected
- unauthorized conversation subscription denied
- event routing respects conversation participants
- non-recipient users do not receive conversation events
- typing rate-limit works
- internal SSE endpoint is available
- disabled-worker Main App path falls back safely
- frontend hooks include required event types and polling fallback

Manual two-browser QA still needs real signed-in users in production or a seeded local session to prove visual instant delivery across two user sessions. The implementation is additive, so until that is performed the existing polling path remains the reliability fallback.

## Future Plan

Next transport upgrades can replace the in-process buffer with Redis pub/sub or streams, then add WebSocket fanout where infrastructure supports sticky sessions or shared event state. The endpoint and event shapes added here are designed to survive that migration.
