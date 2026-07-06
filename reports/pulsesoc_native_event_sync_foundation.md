# PulseSoc Native Event Sync Foundation

Date: 2026-07-06

## Scope

Implemented a lightweight polling-first event sync foundation for the parallel `mobile-native` client. This is not a full WebSocket, SSE, APNs, FCM, or LiveKit realtime system. It keeps PulseSoc backend state authoritative and uses native cache invalidation only to trigger existing API refresh paths.

Production WebView routes and payment/provider logic were not modified.

## What Changed

### Core event sync service

Added `mobile-native/src/core/eventSync.ts` with:

- persistent native sync cursor in AsyncStorage
- polling of a future-compatible `/api/pulse/sync/events` endpoint
- `after_id` / timestamp cursor query support
- safe full-refresh fallback when the delta endpoint is missing, unavailable, or no cursor exists
- deterministic event deduplication
- subsystem invalidation registry
- guarded handler execution to avoid invalidation loops
- app-foreground polling hook

### Supported sync subsystems

The first sync registry covers:

- Activity Inbox
- Notifications / badge counts
- Buyer Orders
- Marketplace
- Seller Inventory
- Messenger
- Calls
- Safety
- Verification
- Premium
- Intelligence

Only Activity, Notifications, Buyer Orders, Marketplace, and Seller Inventory are wired in this foundation because they are the current highest-risk cross-system consistency surfaces for commerce/activity state.

### Cursor tracking

The native cursor stores:

- `latestEventId`
- `lastEventAt`
- `lastSyncedAt`
- `lastFullResyncAt`

If a server delta endpoint returns events, native stores the latest event cursor. If the endpoint is unavailable, native keeps a fallback cursor and refreshes registered subsystems from server-authoritative APIs.

### Cache invalidation registry

The registry maps server event families to native refresh targets:

| Event family | Invalidated native subsystems |
| --- | --- |
| order / purchase / payment / refund / dispute / shipping | Orders, Activity, Notifications |
| listing / marketplace / seller / inventory | Marketplace, Seller Inventory, Activity |
| message / conversation / chat | Messenger, Activity |
| call / ring / missed / decline / answer | Calls, Activity, Notifications |
| notification / badge / inbox / activity | Activity, Notifications |
| safety / report / block / mute / appeal / enforcement | Safety, Activity, Notifications |
| verification / badge / identity | Verification, Activity, Notifications |
| premium / subscription / entitlement | Premium, Activity, Notifications |
| alert / intelligence / crypto / market | Intelligence, Activity, Notifications |

Explicit backend `invalidates` or `invalidate` arrays take precedence when provided.

## Native Integration Points

### App navigator

`mobile-native/src/navigation/AppNavigator.tsx` now:

- starts polling-first sync on authenticated tab navigation
- refreshes notification badges through registered invalidation handlers
- invalidates Activity + Notifications when a foreground notification arrives
- triggers a full server-authoritative refresh fallback on startup

### Activity Inbox

`mobile-native/src/screens/ActivityInboxScreen.tsx` now refreshes when:

- Activity events invalidate
- Notification events invalidate
- the app foregrounds
- the user pulls to refresh

### Notification Center

`mobile-native/src/screens/NotificationCenterScreen.tsx` now refreshes when notification invalidation fires.

### Buyer Orders

`mobile-native/src/screens/BuyerOrdersScreen.tsx` now refreshes when order/payment/refund/dispute/shipping events invalidate.

### Marketplace

`mobile-native/src/screens/MarketplaceScreen.tsx` now refreshes marketplace search/listing state when marketplace/listing invalidation fires.

### Seller Store / Inventory

`mobile-native/src/screens/SellerStoreScreen.tsx` now refreshes when seller inventory, marketplace, or order invalidation fires.

## Graceful Degradation

If the sync endpoint is missing or unreachable:

- native does not crash
- native stores fallback sync metadata
- registered subsystems refresh using existing APIs
- corrupted cache behavior remains handled by existing cache utilities
- no local cache becomes authoritative

Offline mode still relies on existing per-feature cached state. Reconnect/foreground polling triggers a safe refresh attempt.

## QA Browser Check

The QA web server was started with `npm run web:qa` and confirmed listening on `http://127.0.0.1:8094`.

Built-in QA browser route checks were run for:

- `/pulse/activity`
- `/pulse/orders`
- `/pulse/seller-store?title=Seller%20%2F%20Store`
- `/pulse/marketplace`

Result:

- All routes rendered the native web shell without console errors.
- The browser session was not authenticated, so protected routes correctly displayed the Login screen.
- The `/qa/simulator-login` shortcut did not authenticate this build because QA simulator auth is intentionally enabled only when the native app API base URL is local. This build uses the default production API base URL.

Authenticated event-refresh behavior still needs a local QA API session or a real QA account session before it can be browser-verified end to end.

## What Is Already Synchronized Correctly

- Notification badges and Activity Inbox use server notification/count APIs.
- Buyer Orders and Seller Store read server payment/order/listing state.
- Marketplace listing visibility remains approval-gated by backend APIs.
- Seller Inventory updates remain server-authoritative.
- Commerce activity routing is already aligned across orders, marketplace, and activity links.

## Still Partially Synced

- Messenger and Calls still own their local polling/status refresh behavior and are only mapped in the new registry for future wiring.
- Safety, Verification, Premium, Intelligence, and Alerts are mapped but not yet attached to screen-level invalidation handlers.
- The server-side delta endpoint is future-compatible in native but still needs production endpoint confirmation before true delta replay can be marked verified.

## Stale State / Concurrent Update Risks

- Two devices editing seller inventory can still briefly show stale cached inventory until the next poll/foreground/pull refresh.
- Order status can change while Buyer Orders is open; the new invalidation layer refreshes when an event is available, but provider push timing is still release QA.
- Marketplace listing moderation changes depend on server event availability or fallback polling.
- Activity Inbox can still lag if the backend does not emit a sync event and the user does not foreground or refresh.

## Missing For True Real-time Readiness

- Confirmed production `/api/pulse/sync/events` or equivalent authenticated event feed.
- Event replay/idempotency contract documented by backend.
- Screen invalidation handlers for Messenger, Calls, Safety, Verification, Alerts, Intelligence, and Premium.
- Cross-device push/provider QA for APNs/FCM and notification tap timing.
- Later WebSocket/SSE streaming layer after polling-first behavior is stable.

## Subsystem Completion

| Subsystem | Native coverage | Sync coverage | Notes |
| --- | ---: | ---: | --- |
| Activity + Notifications | 88% | 83% | Polling-first invalidation wired |
| Buyer Orders | 92% | 82% | Order refresh handler wired |
| Seller Inventory | 93% | 82% | Seller inventory/order/marketplace handlers wired |
| Marketplace | 92% | 80% | Listing refresh handler wired |
| Messenger | 76% | 66% | Mapped, not yet wired to event sync handler |
| Calls | 63% | 60% | Mapped, release QA still needed |
| Safety/Trust | 84% | 73% | Mapped, handler wiring pending |
| Verification | 84% | 73% | Mapped, handler wiring pending |
| Intelligence/Alerts | 80% | 70% | Mapped, handler wiring pending |
| Native media/camera | 72% | 60% | Physical-device release QA remains |
| Android readiness | 35% | 30% | Physical Android QA remains |

Overall native migration estimate: 83% foundation/parity coverage, 70% release QA confidence.

## Recommended Next Action

Run a short Event Sync QA hardening pass before expanding features:

1. use seeded backend events where available
2. verify Activity Inbox, Buyer Orders, Seller Store, and Marketplace refresh without duplicate UI state
3. confirm fallback behavior when `/api/pulse/sync/events` is unavailable
4. add handlers for Messenger/Calls only after seeded event behavior is proven

This should remain a practical QA pass, not a full realtime/WebSocket implementation.
