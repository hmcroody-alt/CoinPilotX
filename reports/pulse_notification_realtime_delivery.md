# Pulse Notification Realtime Delivery

## Delivery Model

1. `send_message` persists the message.
2. Receiver unread counts are incremented.
3. A `pulse_notifications` row is created for each receiver.
4. A receiver-scoped realtime payload is published.
5. Active clients consume the event through the Communications V2 realtime endpoint or the shared Pulse realtime client.
6. Cross-tab listeners mirror the update through `BroadcastChannel` and a storage-event fallback.

## Updated Surfaces

- Notification bell and unread badge
- Desktop notification dropdown
- `/pulse/notifications`
- Communications V2 conversation list
- Active Communications V2 thread
- Conversation preview and unread badge
- Other open tabs for the same user

## Polling Fallback

Visible tabs poll every 12 seconds. Hidden tabs poll every 45 seconds. This keeps the app responsive while avoiding aggressive background network activity.

## Validation Targets

- `scripts/message_realtime_notification_audit.py`
- `scripts/pulse_notification_live_update_audit.py`
- `scripts/pulse_message_unread_badge_audit.py`
