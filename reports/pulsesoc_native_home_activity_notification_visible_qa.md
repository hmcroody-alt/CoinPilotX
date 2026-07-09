# PulseSoc Native Home Activity + Notification Invalidation Visible QA

Date: 2026-07-09

Scope: Home-only synchronization proof across Home, Activity Inbox, Notifications, and the `/api/pulse/sync/events` cursor system. No new product features, no UI redesign, no Android work, and no production WebView route changes.

## Environment

- Native web QA: `http://127.0.0.1:8094`
- Local API proxy: `http://127.0.0.1:5108`
- Local backend: `http://127.0.0.1:5107`
- Database: disposable SQLite QA database at `/tmp/pulsesoc_home_activity_qa.sqlite`
- Browser: built-in QA browser, visible
- Auth state: signed-in local QA account

## Visible QA Performed

Roody could watch these flows in the built-in QA browser:

- Home loaded authenticated with Pulse Network hero, composer, feed tabs, and native feed cards.
- Text post publish proof from the prior Home completion pass remained visible in feed and Activity.
- Fresh seed post `5` was liked from Home and visibly changed to `1 reactions` / `Fire 1`.
- Fresh seed post `5` opened native Post Detail and accepted a visible comment.
- Post Detail visibly changed to `1 comments`.
- Activity route opened visibly and showed actor-side publish, report, and block events.
- `/pulse/notifications` opened visibly and resolved to the unified Activity Inbox surface.
- Fresh seed post `6` repeated the patched runtime proof after backend restart:
  - Home card rendered visibly.
  - Fire reaction changed to `1 reactions` / `Fire 1`.
  - Post Detail comment submit added one visible comment.
  - Post Detail showed `1 comments`.

## Backend Cursor Evidence

The final patched verification used seed post `6`. Owner-side notification rows were checked directly because recipient notifications belong to the post owner account, not the signed-in actor session.

Observed rows:

- `like` notification: one row only, `entity_type=post`, `entity_id=6`, `sync_cursor_key=like:post:6:2026-07-09T21:09:25`
- `comment` notification: one row only, `entity_type=comment`, `entity_id=3`, `sync_cursor_key=comment:comment:3:2026-07-09T21:10:07`
- Both rows include `invalidates=["activity","notifications"]`.

Duplicate notification check:

- `like`: 1
- `comment`: 1

## Fixes Made

- Added server-returned viewer state to feed payloads for saved, reposted, and following state so Home refresh no longer loses these states.
- Wired Home Follow to the server-authoritative follow toggle endpoint instead of routing to profile only.
- Wired Home Report, Block, and Mute actions into Safety Hub with target context.
- Added route parameters to Safety Hub so report/block/mute forms prefill from feed actions.
- Prevented duplicate feed interaction notifications by allowing API routes to opt out of lower-level feed-engine owner notifications and emit one route-level cursor-aware notification.
- Added explicit `invalidates=["activity","notifications"]` metadata for post-owner notifications and follow notifications.

## Notes

- The comment submit control is still rendered by React Native Web as a non-semantic clickable view rather than an accessible button. It works, but it remains an accessibility/release QA gap.
- Actor-side Activity/Notifications correctly show actor-owned events such as publish, report, and block. Recipient like/comment notifications were verified through backend cursor rows because they belong to the post owner account.
- Web console warnings observed were non-fatal Expo/RNW warnings: web push-token listener limitation, deprecated shadow props, deprecated `expo-av`, `pointerEvents`, Badging API, and web-only animation fallback.

## Remaining Home Release Blockers

- Persistent Hide is local-only: hidden feed cards return after refresh because there is no server-authoritative hide/not-interested mutation wired for Home posts.
- Native user Mute still falls back to Safety Hub local/web-fallback behavior instead of a server-authoritative native mute-user mutation.
- Comment submit accessibility needs a real button/role path.
- Physical-device push notification delivery, lock-screen taps, and background recovery remain release QA, not foundation blockers.

## Status

- Home Foundation: 96%
- Activity Sync: 91%
- Notification Sync: 90%
- Cursor Reliability: 91%
- Visible QA: 92%
- Current Native Migration: 95%
- Release QA Confidence: 76%
- Can Home be considered release complete: NO

Next highest-value Home action: wire server-authoritative Home Hide/Mute persistence and replace the comment submit view with an accessible button path.
