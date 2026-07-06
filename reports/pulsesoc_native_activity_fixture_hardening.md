# PulseSoc Native Commerce + Activity Fixture Hardening

Date: 2026-07-06

Scope: harden cross-system commerce/activity fixture consistency without adding new user-facing features, redesigning Activity Inbox, or moving payment/provider authority into the native app.

## Executive Status

PulseSoc now has the native commerce loop and the native Activity Inbox, but event realism depends on consistent backend notifications and route targets. This pass adds a seeded audit that validates commerce events through the existing backend notification, buyer order, seller order, marketplace, and native routing contracts.

Commerce events route through the existing Marketplace lane in Activity Inbox. A separate Commerce tab was not added because that would be a new UI system and the existing native category model already classifies order/payment/refund/checkout/listing signals under Marketplace.

No production WebView flow was modified.

## Fixture Events Covered

The audit seeds realistic commerce activity for:

- purchase completed
- payment failed
- refund issued
- dispute created
- shipping updated
- order cancelled
- listing created
- listing updated
- listing removed

Each fixture is tied to existing backend-owned data:

- `seller_transactions`
- `marketplace_listings`
- `marketplace_sellers`
- `pulse_notifications`
- `pulse_notification_deliveries`

## Verified Consistency

### Activity Inbox

- commerce notifications are created through `notify_user`
- `/api/pulse/notifications` now merges notification OS rows with legacy `pulse_notifications` rows, skipping ID collisions so existing commerce notifications remain visible to the native Activity Inbox
- `/api/pulse/notifications/unread-count` now includes unread legacy non-message Pulse notifications so commerce alerts are reflected in the native badge count
- notifications list newest first
- unread counts include commerce events
- mark read works through existing notification APIs
- delete works through existing notification APIs
- category classification recognizes marketplace/order/checkout/purchase/product/listing/seller signals
- native Activity Inbox remains display/control-only, not the source of truth

### Buyer Orders

- paid, failed, refunded, cancelled, shipped, and dispute-opened transaction states are visible in buyer order history
- historical order rows preserve marketplace listing references
- support, receipt, dispute, and tracking values remain server/provider-controlled

### Seller Inventory / Seller Orders

- seller order endpoint reads the same transaction ledger
- listing-created, listing-updated, and listing-removed events are modeled as seller-facing activity
- seller inventory remains approval and moderation gated by backend listing state

### Marketplace Listing State

- listing-created and listing-updated fixtures keep normal listing references
- listing-removed fixture uses seller-deleted state
- deleted listings remain safe in historical order views

### Notification Routing

Native route coverage exists for:

- `/pulse/orders/:id`
- `/pulse/purchases`
- `/dashboard/orders`
- `/pulse/marketplace/:id`
- `/pulse/activity`
- `/pulse/inbox`

Backend target resolution may still fall back for native-only routes that do not have a production Flask route. The native Activity layer preserves the original `target_url` when backend resolution returns a safe fallback, so native-supported commerce targets still route through the app.

## Duplicate Webhook Delivery

Duplicate webhook delivery is guarded by the existing Stripe event idempotency path:

- `stripe_event_processed`
- duplicate Stripe webhook skip logging
- seller transaction metadata driven by provider webhook IDs

This pass does not simulate live Stripe webhook signature delivery. Provider-live duplicate webhook tests remain release/provider QA.

## QA Browser Checks

Built-in QA browser checks should cover:

- `/pulse/activity`
- `/pulse/activity/marketplace`
- `/pulse/orders`
- `/pulse/orders/1`
- `/pulse/marketplace`
- `/pulse/seller-store?title=Seller%20%2F%20Store`

Signed-out route checks are useful for route reachability and console stability. Data-rich read/delete checks are covered by the backend fixture audit unless a QA authenticated browser session is seeded.

## Provider/device behavior not verified

Not claimed verified:

- real APNs/FCM commerce notification taps
- physical device badge state changes
- live Stripe refund webhook delivery
- live Stripe dispute webhook delivery
- real shipping provider webhook delivery
- cross-device state sync
- offline cache restore with network disabled

## Risk Assessment

Risk level: low.

Reason:

- no payment provider logic changed
- no WebView marketplace route changed
- no new UI category or screen was introduced
- event truth remains backend/server-authoritative
- legacy commerce notification visibility is bridged through the existing notification API instead of a native-only store
- badge aggregation now includes legacy commerce/activity unread counts
- audit fixtures use local temporary database state

## Recommendation

Next highest-value system improvement: Native Real-time Event Sync Readiness.

Why:

Commerce and Activity now agree through seeded backend fixtures. The next gap is freshness: Activity Inbox, Buyer Orders, Seller Inventory, Marketplace state, calls, messages, alerts, and safety updates should eventually refresh through one real-time event stream instead of independent polling.

Safest plan:

1. Inspect existing realtime/WebSocket/SSE/event delivery code.
2. Inventory which native surfaces currently poll.
3. Define a server-authoritative event envelope for activity, commerce, calls, messages, safety, alerts, and marketplace updates.
4. Add a native event-sync service that can subscribe, refresh affected caches, and degrade to polling.
5. Keep provider events and business rules on the backend.
