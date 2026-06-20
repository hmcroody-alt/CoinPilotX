# PulseSoc Full Communication Delivery System

## Scope

This phase hardens the existing Communications V2 path so a committed message can move through:

1. database commit
2. renderable message payload
3. realtime fanout
4. inbox/badge update
5. in-app notification creation
6. native/Web Push dispatch when policy allows
7. exact conversation deep link

No voice/video calling was added. Existing polling remains as fallback.

## Changes

- Added a scoped Communications V2 SSE stream at `/api/pulse/communications/v2/realtime/stream`.
- Kept existing `/api/pulse/communications/v2/realtime` polling fallback.
- Made the local realtime bus waitable using a condition variable instead of a busy loop.
- Added full message metadata to message payloads:
  - `message_id`
  - `client_message_id`
  - `client_temp_id`
  - `sender_id`
  - `sender_display_name`
  - `sender_avatar`
  - `delivery_state`
- Added optimistic client-side send bubbles keyed by `client_message_id`.
- Added duplicate replacement for server and realtime responses.
- Added realtime fanout for:
  - `message_created`
  - `message_read`
  - `typing_started`
  - `typing_stopped`
  - `unread_count_updated`
- Added `cc:user:<id>` and `cc:conversation:<id>` fanout topics alongside legacy realtime topics.
- Added native/PWA push registration aliases:
  - `/api/push/register-device`
  - `/api/push/revoke-device`
  - `/api/push/status`
- Added `user_device_tokens` compatibility storage while preserving `push_subscriptions`.
- Wired message side effects to call the existing push provider path when allowed by policy.
- Redesigned the Messages V2 surface into a command-center UI:
  - signal radar inbox
  - scoped Shield filter
  - live signal strip
  - reachability state
  - delivery-state labels
  - Pulse Shield warning treatment
  - Signal Route detail card
  - dark sci-fi mission deck styling
- Routed `/pulse/messages` and `/pulse/messages/<conversation_id>` to the V2 command center when Communications V2 is enabled.
- Fixed minimized desktop layout so the Messages V2 shell stays viewport-height and the inbox/thread scroll independently.
- Updated service worker runtime asset handling so JS/CSS are network-first with cache fallback, preventing stale chat assets after deploy.
- Added contextual empty states for search, Shield, and unread filters.
- Hardened realtime fanout for typing and read receipts by publishing to participant user channels, not only conversation channels.
- Deduplicated active participant IDs before message side effects, preventing duplicate notification/push attempts when local data has duplicate participant rows.
- Removed the redundant Communications V2 push dispatch and now relies on the existing `create_pulse_notification` push result, preventing duplicate provider calls.

## Security And Privacy

- Blocked users are skipped before notification or push delivery.
- Muted conversations suppress native push.
- Active-chat recipients suppress native push.
- Private preview mode sends a generic notification body.
- Pulse Shield flagged messages use a generic warning body and do not expose suspicious content in lock-screen previews.
- Push trace logging avoids raw tokens, secrets, and subscription credentials.
- Message realtime badge payloads use Communications V2 chat unread counts, not general Alerts counts.

## Deep Links

- Web: `/pulse/messages/<conversation_id>`
- Mobile: `pulse://messages/<conversation_id>`

Both are included in message push metadata.

## Audits

Added:

- `scripts/command_center_full_delivery_audit.py`
- `scripts/push_notification_delivery_audit.py`
- `scripts/apple_review_compliance_audit.py`
- `scripts/pulse_messages_command_center_ui_audit.py`
- `scripts/pulse_messages_two_user_delivery_audit.py`

These audits verify route wiring, payload completeness, realtime behavior, push aliases, deep links, badge separation, report/block hooks, and App Store moderation evidence hooks.

## Validation Run

Completed locally:

- `venv/bin/python -m py_compile bot.py pulse_communications_v2/routes.py pulse_communications_v2/service.py services/realtime_engine.py services/push_service.py services/notification_service.py services/command_center_worker/realtime_transport.py scripts/command_center_full_delivery_audit.py scripts/push_notification_delivery_audit.py scripts/apple_review_compliance_audit.py`
- `node --check static/js/pulse_realtime.js`
- `node --check static/js/pulse_messages_v2.js`
- `node --check static/sw.js`
- `node --check static/service-worker.js`
- `git diff --check`
- `venv/bin/python scripts/command_center_full_delivery_audit.py`
- `venv/bin/python scripts/push_notification_delivery_audit.py`
- `venv/bin/python scripts/apple_review_compliance_audit.py`
- `venv/bin/python scripts/pulse_messages_command_center_ui_audit.py`
- `venv/bin/python scripts/pulse_messages_two_user_delivery_audit.py`
- `venv/bin/python scripts/message_realtime_notification_audit.py`
- `venv/bin/python scripts/pulse_native_push_delivery_audit.py`
- `venv/bin/python scripts/messenger_push_notification_audit.py`
- `venv/bin/python scripts/command_center_realtime_transport_audit.py`
- `venv/bin/python scripts/command_center_service2_worker_audit.py`
- `venv/bin/python scripts/command_center_messaging_core_audit.py`
- `venv/bin/python scripts/command_center_notifications_audit.py`
- `venv/bin/python scripts/command_center_security_audit.py`
- `venv/bin/python scripts/command_center_presence_audit.py`
- `venv/bin/python scripts/command_center_ai_messaging_audit.py`
- `venv/bin/python scripts/command_center_redis_audit.py`
- `venv/bin/python scripts/command_center_service1_audit.py`
- `venv/bin/python scripts/pulse_service_worker_audit.py`
- `venv/bin/python scripts/pulse_pwa_capacity_audit.py`
- `venv/bin/python scripts/pulse_pwa_install_prompt_audit.py`
- `venv/bin/python scripts/mobile_pwa_audit.py`

Local HTTP smoke on temporary port `5077`:

- `/api/pulse/communications/v2/health` returned ready.
- `/pulse/messages` redirected unauthenticated users to login.
- `/api/push/status` returned `401` for unauthenticated users.
- Authenticated Flask route rendering verified:
  - `/pulse/messages` renders the V2 command-center shell.
  - `/pulse/messages?conversation=123` preserves `data-initial-conversation-id="123"`.
  - `/pulse/messages/123` preserves `data-initial-conversation-id="123"`.
  - `/pulse/messages-v2?conversation=123` preserves `data-initial-conversation-id="123"`.

Browser QA:

- On `http://127.0.0.1:5062/pulse/messages`, Messages V2 loaded with no console warnings/errors after restarting the stale local dev process.
- Responsive checks passed at 390x844, 900x760, and 1440x900:
  - no horizontal overflow
  - mobile list mode fits the viewport
  - mobile thread mode keeps the composer fixed at the bottom
  - minimized desktop no longer stretches into a long page
  - wide desktop keeps the shell viewport-height
- Local browser send QA passed:
  - message appears immediately
  - input clears after send
  - delivery state renders as sent
  - signal and Shield status strips remain visible
- Search and Shield filter controls are wired and no longer show misleading generic empty copy.
- The in-app browser automation blocked the temporary local port with `ERR_BLOCKED_BY_CLIENT`, so browser visual QA was completed on the configured local app port `5062` instead.

Two-client local delivery QA:

- User A (`user_id=1`) sent a direct message to User B (`user_id=2`) through Communications V2.
- User B loaded the thread and saw the message.
- User B realtime polling received message notification/message-created/unread-count events.
- User B typing generated sender-visible `typing_started` events for User A.
- User B opening/reading the thread generated sender-visible `message_read` events for User A.
- Reaction endpoint remained wired for the delivered message.
- Push trace showed `recipient_count: 1`, one recipient policy evaluation, and one push-provider attempt for the notification path.
- Local push provider returned `send_push_no_tokens`, which is expected in this local database because User B has no registered push token.

## Remaining Required QA

This implementation still requires physical-device verification before it should be considered complete:

- User A sends to User B.
- User B receives realtime message while chat is open.
- User B sees inbox preview and chat badge while elsewhere in app.
- iPhone locked-screen push appears with sound/vibration.
- Android locked-screen push appears with sound/vibration.
- Notification tap opens the exact conversation.
- Muted conversation suppresses sound/vibration.
- Blocked sender produces no push.
- Private preview mode hides message content.

## Current Limitation

Local audits can verify wiring, but they cannot prove APNs/FCM/Expo delivery on locked physical devices. Do not mark Messenger delivery complete until real-device QA passes.
