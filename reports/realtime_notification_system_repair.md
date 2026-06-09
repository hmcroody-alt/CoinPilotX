# Realtime Notification System Repair

Date: 2026-06-09

- Pulse notifications have explicit categories for chat, group, room, comments, replies, reactions, follows, statuses, live invites, marketplace, teachers, premium, and security.
- `create_pulse_notification` now attempts immediate push delivery through stored web/Expo subscriptions.
- Invalid push tokens are cleaned up by the push service when providers report them.
- Deep links are preserved for web and mobile notification routing.

Production QA requires physical-device push delivery and browser permission tests.

