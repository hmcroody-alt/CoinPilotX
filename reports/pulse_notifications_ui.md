# Pulse Notifications UI

Status: implemented.

Implemented:
- Pulse header notification control with unread badge.
- Desktop notification dropdown shell with links to the center and settings.
- Full notification center at `/pulse/notifications`.
- Filters: All, Unread, Messages, Social, Security, Premium.
- Mark read, mark all read, delete, and deep-link open actions.
- Existing friend request accept/decline actions remain available in the notification center.

Mobile:
- Pulse pages load the notification badge poller.
- The mobile notification entry links to `/pulse/notifications`.

Deep links:
- Notification cards open `deep_link`, falling back to `target_url` and then `/pulse`.
