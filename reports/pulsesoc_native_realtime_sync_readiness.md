# PulseSoc Native Real-time Event Sync Readiness

Date: 2026-07-06

Scope: readiness audit for live state propagation across native Activity Inbox, Buyer Orders, Seller Inventory, Marketplace, Notifications, Messenger, Calls, Safety/Trust, and Verification. This pass does not implement a full WebSocket/SSE client, does not change payment logic, and does not modify production WebView flows.

## Executive Status

PulseSoc is ready for a native real-time sync foundation, but it is not yet a fully real-time synchronized native app.

Current state:

- Server authority is intact across commerce, notifications, messages, calls, safety, and verification.
- Command Center already exposes passive realtime worker hooks and a polling fallback model.
- Native app surfaces have reliable API wrappers and offline caches, but most surfaces refresh independently.
- Activity Inbox already composes notifications, Messenger unread summaries, and active calls into one native activity model.
- Commerce fixture hardening validated seeded event consistency across Buyer Orders, Seller Inventory, Marketplace state, and Activity Inbox.

The next implementation should be a small native event-sync service that consumes server-authoritative event envelopes, invalidates affected caches, refreshes active screens, and degrades to deterministic polling. It should not duplicate business logic.

## Existing Server-authoritative Event Sources

| Domain | Current authoritative source | Current sync behavior | Readiness |
| --- | --- | --- | --- |
| Activity/notifications | `pulse_notifications`, notification OS tables, notification delivery jobs | Native Activity Inbox calls `/api/pulse/notifications` and unread-count endpoints | Ready for event-triggered refresh |
| Buyer orders | `seller_transactions`, creator transaction records | Native Buyer Orders calls `/api/pulse/orders` and order detail endpoints | Ready for event-triggered refresh |
| Seller inventory/orders | marketplace seller listing tables and `seller_transactions` | Seller Store loads seller listings and seller order summary | Ready for event-triggered refresh |
| Marketplace listings | marketplace listing/media tables | Marketplace search/list/detail APIs are server authoritative | Ready for listing-state invalidation |
| Messenger | Pulse message conversations/messages and Communications V2 | Conversation list and chat screens poll/refresh existing message APIs | Partially ready; event envelope should target conversation cache keys |
| Calls | Call active/status/event APIs and LiveKit backend | Incoming layer and Call screen poll active/status endpoints | Partially ready; call state needs event-triggered refresh |
| Safety/Trust | block, mute, report, account health, appeal, enforcement state | Native screens refresh from server APIs and cache fallback | Ready for event-triggered refresh |
| Verification | verification request/status tables and admin review state | Native Verification Center refreshes server state and caches | Ready for event-triggered refresh |

## Current Native Refresh Model

Native screens already support manual, focus, foreground, or interval-based refresh:

- Activity Inbox: loads notifications, badge counts, conversations, and active calls; cache fallback exists.
- Notifications tab badge: refreshes on app foreground and notification receipt.
- Buyer Orders: loads order history and cached orders.
- Seller Store: loads seller listings plus seller orders and caches the snapshot.
- Marketplace: loads search/listing results and caches them.
- Messenger: caches conversation list and messages; chat sync uses `/api/pulse/messages/<conversation_id>/sync`.
- Calls: Incoming call layer and Call screen poll active/status endpoints.
- Trust/Safety and Verification: app-foreground refresh and cache fallback exist on key screens.

This is stable enough for current native foundations. The gap is that cache invalidation is not yet centralized, so related screens may remain stale until their own refresh path runs.

## Backend Realtime Readiness

Existing backend infrastructure:

- `services/command_center_client.py` defines `enqueue_realtime_event(...)`, `get_realtime_events(...)`, and `get_realtime_status()`.
- Command Center realtime worker routes exist for:
  - `/internal/command-center/realtime/event`
  - `/internal/command-center/realtime/poll/<user_id>`
  - `/internal/command-center/realtime/stream/<user_id>`
  - `/internal/command-center/realtime/status`
- Existing Command Center client is disabled by default and degrades to `polling_fallback`.
- Idempotency is already designed around event IDs and `X-Idempotency-Key`.
- Existing notification and Stripe paths have duplicate suppression/idempotency protections.

Readiness conclusion: backend contracts are ready to support a native event-sync layer, but native should begin with polling/event-envelope readiness before depending on always-on sockets.

## Minimal Native Event-sync Trigger Map

Recommended event envelope:

```json
{
  "event_id": 12345,
  "event_type": "commerce.order.updated",
  "domain": "commerce",
  "entity_type": "order",
  "entity_id": "789",
  "target_url": "/pulse/orders/789",
  "created_at": "2026-07-06T00:00:00Z",
  "invalidate": ["activity", "orders", "seller_inventory", "marketplace"],
  "metadata": {}
}
```

Recommended native invalidation map:

| Event family | Refresh/invalidate |
| --- | --- |
| `notification.*`, `activity.*` | Activity Inbox, Notification Center, badge counts |
| `commerce.order.*`, `payment.*`, `refund.*`, `dispute.*`, `shipping.*` | Activity Inbox, Buyer Orders, Seller Store, Marketplace |
| `marketplace.listing.*` | Marketplace, Seller Store, Activity Inbox |
| `message.*`, `conversation.*`, `typing.*`, `presence.*` | Messenger list, active Chat screen, Activity Inbox |
| `call.*`, `incoming_call.*`, `missed_call.*` | Incoming call layer, Call screen, Activity Inbox, Notifications |
| `safety.*`, `report.*`, `appeal.*`, `enforcement.*` | Safety Hub, Account Health, Activity Inbox |
| `verification.*`, `badge.*` | Verification Center, Profile, Activity Inbox |
| `intelligence.*`, `alert.*` | Intelligence Center, Alert Management, Activity Inbox |

## Consistency Assessment

Fully consistent already:

- Commerce event fixtures across Buyer Orders, Seller Orders, Activity Inbox, and Marketplace listing references.
- Notification route normalization and safe fallback behavior for commerce/activity routes.
- Badge counts include the notification paths needed by native Activity Inbox.
- Existing cache reads safely discard corrupted JSON.
- Payment/order truth remains backend-owned.

Partially synced:

- Activity Inbox can aggregate notifications, messages, and calls, but it only updates when loaded, foregrounded, manually refreshed, or receiving a push event.
- Buyer Orders, Seller Store, and Marketplace have correct caches and refresh functions, but no shared invalidation bus yet.
- Messenger has sync/polling endpoints, but native Activity Inbox does not receive a shared event that invalidates both message list and activity counts.
- Calls poll active/status state, but the same state is not yet shared through a unified event-sync service.

Stale or inconsistent risk:

- Commerce or marketplace changes can be correct on the backend while an already-open native screen shows cached data until refresh.
- Activity Inbox and tab badge can update before Buyer Orders or Seller Store refreshes.
- Call and message state can update through their own polling loops before Activity Inbox refreshes.
- Offline cache restore is safe but not yet timestamp-aware enough to label stale data consistently across all screens.

Missing for full real-time readiness:

- Native event-sync service with one cursor per signed-in user.
- Shared cache invalidation registry.
- Event cursor persistence and replay on app resume.
- Server endpoint for native to poll Command Center events, or a main-app proxy to the worker with normal user auth.
- Foreground/background lifecycle integration for event polling.
- Cross-device provider QA for push, badge, and notification tap timing.

## QA Browser Checks

Built-in QA browser route checks should cover:

- `/pulse/activity`
- `/pulse/orders`
- `/pulse/orders/1`
- `/pulse/marketplace`
- `/pulse/seller-store?title=Seller%20%2F%20Store`

Signed-out checks prove route reachability, auth-gate safety, and console stability. Data-rich consistency remains covered by backend fixture audits until an authenticated QA session is seeded.

Performed QA browser check:

- `/pulse/activity`: rendered native Login/auth gate; no console errors.
- `/pulse/orders`: rendered native Login/auth gate; no console errors.
- `/pulse/orders/1`: rendered native Login/auth gate; no console errors.
- `/pulse/marketplace`: rendered native Login/auth gate; no console errors.
- `/pulse/seller-store?title=Seller%20%2F%20Store`: rendered native Login/auth gate; no console errors.

This was not an authenticated data-rich QA pass. It verifies local web route reachability, native auth fallback, and console safety only.

## Device/provider behavior not verified

Not claimed verified:

- APNs/FCM push delivery timing.
- Lock-screen notification tap routing.
- Cross-device read/unread propagation.
- Physical badge sync.
- Live Stripe refund/dispute/shipping webhooks.
- Real-time worker deployment health in production.

## Completion Snapshot

| Subsystem | Completion | Sync readiness | Notes |
| --- | ---: | ---: | --- |
| Activity + Notifications | 86% | 78% | Aggregates core domains; needs event cursor/invalidation |
| Buyer Orders | 91% | 75% | Server authoritative; needs event-triggered refresh |
| Seller Inventory | 92% | 75% | Lifecycle stable; needs listing/order invalidation |
| Marketplace | 91% | 72% | Media and listing contracts stable; needs listing event refresh |
| Messenger | 76% | 65% | Poll/sync exists; needs shared event bridge |
| Calls | 62% | 58% | Active-call polling exists; LiveKit/device QA remains release blocker |
| Safety/Trust | 84% | 72% | Server-owned state; refresh/invalidation needed |
| Verification | 84% | 72% | Server-owned state; refresh/invalidation needed |
| Overall native migration | 82% | 69% release QA confidence | Feature foundations are broad; live sync and device QA remain major finish-line work |

## Recommendation

Next highest-value action: Native Event Sync Foundation.

Safest plan:

1. Do not build a full WebSocket stack first.
2. Add a small native `sync` service that stores `latest_event_id`, polls a server-authoritative event endpoint when available, and maps event families to cache invalidation callbacks.
3. If Command Center is unavailable, degrade to current foreground/manual refresh behavior.
4. Wire first to Activity Inbox, badge counts, Buyer Orders, Seller Store, Marketplace, Messenger, and Calls.
5. Add fixture audits for event replay, duplicate event suppression, and reconnect determinism.
6. Keep provider push and physical cross-device timing as release QA blockers, not development blockers.
