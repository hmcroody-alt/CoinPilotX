# PulseSoc Native Notifications + Inbox + Activity Graph Unification

Date: 2026-07-05

## Scope

This mission built the native unified Activity Inbox foundation for PulseSoc without changing production WebView routes or duplicating backend notification logic.

The native Activity Inbox is a visibility, grouping, routing, and control layer over existing PulseSoc server-authoritative systems. Delivery, notification creation, message unread state, call state, safety events, verification events, marketplace events, creator/growth events, intelligence alerts, badge counts, preferences, and read/delete mutations remain owned by the backend.

## Production Codebase Inspection

Existing production systems reused:

- `GET /api/pulse/notifications`
- `GET /api/pulse/notifications/unread-count`
- `POST /api/pulse/notifications/<notification_id>/read`
- `POST /api/pulse/notifications/read-all`
- `DELETE /api/pulse/notifications/<notification_id>`
- `POST /api/pulse/notifications/<notification_id>/resolve`
- `GET/PATCH /api/pulse/notifications/preferences`
- `GET /api/pulse/messages/conversations`
- `GET /api/calls/active`
- Existing notification tables, read/delete behavior, delivery status, deep links, badge count logic, and preference logic.
- Existing native Messenger conversation cache.
- Existing native active-call cache.
- Existing native notification/deep-link router.
- Existing native Settings, Notification Preferences, Messenger, Calls, Safety, Verification, Marketplace, Creator/Growth, Intelligence, Alert Management, Profile, and Account surfaces.

Important backend boundaries:

- Native grouping is display-only and uses server notification fields, target URLs, and existing aggregate APIs.
- Notification read/delete mutations call existing server endpoints.
- Message unread state is not marked read from Activity Inbox; opening a conversation routes to Messenger, where existing seen/read behavior remains authoritative.
- Active call state is not mutated from Activity Inbox; opening a call routes to the existing Call screen.
- Private message bodies, moderator notes, provider logs, and admin-only details are not merged into Activity Inbox.

## Native Implementation

Added:

- `mobile-native/src/api/activity.ts`
- `mobile-native/src/screens/ActivityInboxScreen.tsx`
- Native `ActivityInbox` route.
- Native tab replacement so the existing Notifications tab now opens Activity Inbox.
- Settings entry point for Activity Inbox.
- Deep-link handling for:
  - `/pulse/activity`
  - `/pulse/activity/<category>`
  - `/pulse/inbox`
  - `/dashboard/activity`
  - `/dashboard/inbox`
  - existing `/pulse/notifications` links, now routed into Activity Inbox.

Updated:

- `mobile-native/src/api/notifications.ts`
- `mobile-native/src/navigation/AppNavigator.tsx`
- `mobile-native/src/navigation/linking.ts`
- `mobile-native/src/navigation/notificationRouting.ts`
- `mobile-native/src/navigation/types.ts`
- `mobile-native/src/screens/SettingsScreen.tsx`

## Activity Categories

Implemented native categories:

- All
- Messages
- Calls
- Social
- Safety
- Verification
- Marketplace
- Creator/Growth
- Intelligence/Alerts

The category classifier is intentionally lightweight. It does not create business rules; it only decides which native display lane should show each server notification or existing aggregate signal.

## Supported Native Controls

Supported through existing backend notification APIs:

- Open activity.
- Resolve notification target and mark read.
- Mark one notification read.
- Mark all read.
- Mark category read where backend category mapping exists.
- Delete notification.
- Refresh badge counts.
- Pull to refresh.
- Cached/offline fallback.

Supported through existing native routing:

- Messenger conversation route.
- Call route.
- Profile/feed/post/reel/status/marketplace/safety/verification/premium/creator/growth/intelligence/alert routes via the existing notification router.
- Safe web fallback for unsupported targets.

## UI and Design

The native Activity Inbox uses the current PulseSoc native design system:

- Dark high-contrast control-center layout.
- Glowing unread signal accents.
- Category rail optimized for scanning.
- Server-authority copy in empty states.
- Polished spacing and non-generic native controls.

No internal design-system terminology is exposed in user-facing native UI.

## QA Notes

Static verification required:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_activity_inbox_audit.py`
- `git diff --check`

Practical QA browser route checks required:

- `/pulse/activity`
- `/pulse/activity/messages`
- `/pulse/activity/calls`
- `/pulse/activity/safety`
- `/pulse/activity/verification`
- `/pulse/activity/marketplace`
- `/pulse/activity/creator_growth`
- `/pulse/activity/intelligence_alerts`
- `/pulse/notifications`
- `/pulse/inbox`
- Settings Activity Inbox entry point.

Practical QA browser route checks completed in this mission:

- The Expo web QA server was started with `npm run web:qa`.
- `curl -I --max-time 3 http://localhost:8094/pulse/activity` returned `HTTP/1.1 200 OK` before browser navigation.
- The built-in QA browser was used, not Chrome Incognito.
- The current QA browser session was unauthenticated, so protected native routes correctly rendered the Login screen.
- Checked routes:
  - `/pulse/activity`
  - `/pulse/activity/messages`
  - `/pulse/activity/calls`
  - `/pulse/activity/safety`
  - `/pulse/activity/verification`
  - `/pulse/activity/marketplace`
  - `/pulse/activity/creator_growth`
  - `/pulse/activity/intelligence_alerts`
  - `/pulse/notifications`
  - `/pulse/inbox`
- Result: all checked routes loaded the native web bundle, remained protected behind Login, and produced zero browser console errors.
- Authenticated category rendering, read/delete mutations, and badge-refresh behavior were not claimed as verified because the QA browser did not have an authenticated local QA session during this pass.

Device/provider release blockers:

- Authenticated Activity Inbox QA with seeded notifications remains needed before release.
- Push notification tap routing must still be verified on physical devices.
- Badge synchronization must still be verified on APNs/FCM builds.
- Background notification delivery behavior remains provider/device QA.

## Recommendation Summary

Recommended next highest-value action after Activity Inbox: Native Activity Inbox practical QA hardening, followed by the next feature selected from actual repo state.

Reason: Activity Inbox now touches nearly every native surface. Before adding another large feature, a short authenticated QA pass should verify category routing, read/delete mutations, badge count refresh, notification deep links, and fallback behavior without entering another long QA loop.

Potential next build after that QA pass: native Events/Calendar if production APIs are sufficiently reusable, or Native Notification/Activity Detail hardening if the QA pass finds user-visible routing gaps.
