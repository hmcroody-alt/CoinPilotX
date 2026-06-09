# Realtime Notification System Repair

Date: 2026-06-09

- Pulse notifications have explicit categories for chat, group, room, comments, replies, reactions, follows, statuses, live invites, marketplace, teachers, premium, and security.
- `create_pulse_notification` now attempts immediate push delivery through stored web/Expo subscriptions.
- Legacy `notify_user(...)` paths also attempt immediate push delivery and write a `pulse_notification_deliveries` row, so older feature code no longer waits for the user to reopen the app before push is attempted.
- Invalid push tokens are cleaned up by the push service when providers report them.
- Deep links are preserved for web and mobile notification routing.

Second-pass validation:

- `pulse_notifications_web_push_audit.py`
- `pulse_notification_event_coverage_audit.py`
- `pulse_native_push_delivery_audit.py`
- `mobile_notification_reply_audit.py`

Production QA requires physical-device push delivery and browser permission tests.
