# Pulse Notification QA Results

Date: 2026-06-06

## Local QA Completed

- Python syntax compile passed for notification wiring.
- Notification event coverage audit added for required event groups.
- Notification APIs from the foundation remain present:
  - list notifications
  - unread count
  - mark read
  - mark all read
  - delete
  - preferences

## Functional Coverage

- Notification creation now includes owner/actor IDs, entity type, entity ID, deep link, delivery status, and metadata where available.
- Unread count and notification center can consume the same `pulse_notifications` rows.
- Deep links are set for posts, comments, videos, reels, statuses, messages, live sessions, settings, and Arena inbox events.
- Delete and mark-read behavior is unchanged from the foundation commit.
- Preferences are preserved through the existing Pulse notification preference model.

## Browser QA Status

The in-app browser control tool was unavailable in this turn, so the following remain pending for a human-controlled browser/device pass:

- Multiple real test users
- Dropdown live update verification
- Notification center live update verification
- Deep-link click-through in an authenticated browser
- Chrome desktop push permission flow
- Safari desktop push behavior
- iPhone Safari PWA push behavior
- Android Chrome push behavior

## Recommended Manual QA Script

1. Log in as User A and User B.
2. From User A, follow User B, comment on User B content, react, save, send a message, mention User B, and start a live session.
3. Confirm User B sees unread count updates and notification center rows.
4. Open each notification and confirm the deep link lands on the correct content.
5. Mark individual notifications read, mark all read, and delete one notification.
6. Disable a category in preferences and confirm future delivery respects the setting.
