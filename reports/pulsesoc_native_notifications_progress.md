# PulseSoc Native Notifications Progress

Date: 2026-07-04

## Scope

This milestone builds the native Notifications foundation inside `mobile-native/`. It does not touch production WebView paths, web templates, backend business logic, database logic, notification delivery jobs, moderation rules, or provider integrations.

The native app remains a faster client for the existing PulseSoc notification system. Server APIs stay authoritative for notification creation, delivery, categories, preferences, push token storage, unread counts, badge counts, target resolution, and safety fallback behavior.

## Existing Web/Backend Implementation Inspected

Native implementation was mapped from the existing PulseSoc notification surfaces:

- Web notification center: `/pulse/notifications`
- Web notification settings: `/pulse/settings/notifications`
- Web settings notification experience controls in `templates/account.html`
- Foreground web behavior in `static/notifications.js`
- Backend notification APIs in `bot.py`
- Push registration endpoint in `bot.py`
- Existing push wrapper in `mobile-native/src/api/push.ts`

## Reused API Contract

Native Notifications uses these existing endpoints:

- `GET /api/pulse/notifications`
- `GET /api/pulse/notifications/unread-count`
- `POST /api/pulse/notifications/<notification_id>/read`
- `POST /api/pulse/notifications/read-all`
- `DELETE /api/pulse/notifications/<notification_id>`
- `POST /api/pulse/notifications/<notification_id>/resolve`
- `GET /api/pulse/notifications/preferences`
- `PATCH /api/pulse/notifications/preferences`
- `GET /api/notification-preferences`
- `POST /api/notification-preferences`
- `POST /api/push/subscribe`

No native-only notification business rules were introduced.

## Implemented

- Native notification center.
- Native unread/badge count sync.
- Mark single notification read.
- Mark all notifications read.
- Delete notification.
- Native notification preferences screen.
- Push permission state display.
- Expo push token registration through the existing backend endpoint.
- Safe fallback when push permission is denied or the app is not running on a physical device.
- Foreground notification handler remains active through Expo Notifications.
- Foreground badge refresh when notifications are received.
- Badge refresh on app open/resume.
- Background notification tap routing structure through Expo notification response listeners.
- Deep-link routing for currently supported native screens:
  - Messenger conversation
  - Messenger list
  - Profile
  - Notifications
  - Notification Preferences
- Safe web fallback for Post, Reel, Status, Alert, Purchase, Premium, Marketplace, and other server-resolved targets until those native screens exist.

## Native Routing Behavior

Notification taps use server-provided or server-resolved targets first. The native router handles supported routes in-app and falls back to PulseSoc web URLs for unsupported surfaces.

Supported native routes now:

- `/pulse/messages`
- `/pulse/messages/<conversation_id>`
- `/pulse/profile`
- `/pulse/notifications`
- `/pulse/settings/notifications`

Deferred to web fallback until native screens exist:

- `/pulse/post/<post_id>`
- `/pulse/reels`
- `/pulse/status`
- `/pulse/alerts`
- `/dashboard/crypto/alerts`
- `/pulse/purchases`
- `/pulse/premium`
- `/pulse/marketplace`

## Device-Only Behavior Not Verified

The following need real iOS/Android device or simulator QA:

- OS permission prompt behavior.
- Lock-screen display.
- Sounds and vibration.
- App icon badge behavior.
- Background notification tap routing.
- Expo push token delivery to a real device.
- Native opening from killed/background app state.

Source verification is in place, but these are not marked as passed without device access.

## Current Status

Native Notifications has a reusable foundation that preserves existing PulseSoc notification behavior while adding native screens, badge sync, push permission handling, and routing structure. It is not ready for production replacement until device-level push, badge, sound, vibration, and background tap QA pass.
