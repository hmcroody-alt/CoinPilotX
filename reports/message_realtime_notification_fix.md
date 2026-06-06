# Message Realtime Notification Fix

## Root Cause

Communications V2 created the receiver notification in the database and incremented unread counts, but it only published a conversation-scoped event. Users who were not actively refreshing the Messages V2 thread, notification bell, or notifications page did not receive a receiver-specific update.

## Fix

- Added receiver-scoped realtime events on `comm_v2:user:{user_id}` and `pulse:user:{user_id}` after message notification creation.
- Added authenticated Communications V2 realtime polling at `/api/pulse/communications/v2/realtime`.
- Included updated notification unread count and conversation metadata in the live payload.
- Rendered incoming message payloads from the receiver perspective so inbound messages align correctly.
- Updated Messages V2 to merge live conversation previews, unread counts, active thread messages, and cross-tab events.
- Updated global Pulse notification JavaScript to refresh badges, dropdown content, and notification center content without browser push permission.

## Expected Behavior

When User A sends User B a message, User B receives the notification badge, chat list preview, unread count, and active-thread message without refreshing. If realtime streaming is unavailable, visible tabs use a safe 12 second fallback poll and hidden tabs slow to 45 seconds.

## Regression Notes

The fix keeps private message payloads on authenticated user channels. Browser push permission is not required for in-app badge and list updates.
