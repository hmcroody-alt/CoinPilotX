# Pulse Native App Architecture

Date: 2026-06-06

## Recommendation

Use React Native with Expo for the first native Pulse app phase. It gives Pulse the fastest path to iOS and Android while preserving shared JavaScript patterns, push notification workflows, and deep-link routing.

## API Inventory

- Auth: login, logout, signup, password reset, email verification
- Pulse feed: posts, comments, reactions, saves, follows
- Videos/Reels: list, detail, view, react, comment, retry, manage
- Messages: conversations, send, read, delivery/read state
- Notifications: list, unread count, mark read, delete, preferences, device registration
- Live: start, join, chat, react, end, replay
- Premium: checkout, subscription status, webhook-backed events
- Safety: report, moderation, support, security reports

## Auth Strategy

- Keep server-side auth behavior unchanged for web.
- Add native session/token strategy only after notification delivery is verified.
- Do not change OAuth callback behavior without a separate approved migration.
- Deep links should preserve both `coinpilotx.app` and `pulsesoc.com` compatibility during transition.

## Push Strategy

- Use web push for browser/PWA.
- For native, use Expo push notifications first, with a future migration path to APNs/FCM direct provider integrations.
- Store native device tokens in `pulse_notification_devices` with provider and device type metadata.
- Route notification clicks through content-specific deep links.

## Deep Links

Recommended scheme and paths:

- `pulse://notifications`
- `pulse://post/<id>`
- `pulse://videos/<id>`
- `pulse://reels/<id>`
- `pulse://messages/<conversation_id>`
- `pulse://live/<id>`
- `pulse://settings/notifications`

Web equivalents should remain under `https://pulsesoc.com/...` while `https://coinpilotx.app/...` continues to work.

## App Structure

- `app/auth`
- `app/pulse/feed`
- `app/pulse/videos`
- `app/pulse/reels`
- `app/pulse/messages`
- `app/pulse/notifications`
- `app/pulse/live`
- `app/settings`
- `app/support`

## Estimated Timeline

- Week 1: API contract review, auth/session prototype, navigation shell
- Week 2: feed, videos/reels, messages
- Week 3: notifications, web/native push bridge, preferences
- Week 4: QA hardening, device testing, accessibility, crash reporting
- Week 5: App Store/Play Store preparation after notification delivery is verified

## Stop Condition

Do not begin App Store or Play Store builds until notification delivery is verified end-to-end.
