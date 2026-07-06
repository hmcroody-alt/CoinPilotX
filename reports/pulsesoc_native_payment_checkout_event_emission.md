# PulseSoc Native Payment and Checkout Event Emission Hardening

Date: 2026-07-06

## Scope

This pass hardened event emission for server-authoritative payment, checkout, refund, and dispute state changes that affect native PulseSoc consistency.

No UI was added. Android-specific tooling and QA were intentionally out of scope. WebView payment and marketplace flows remain compatible.

## Roadmap Rule

Do not focus on Android right now.

Current development priority remains:

1. iPhone/iOS native app
2. server-authoritative event consistency
3. payment/checkout/order trust correctness
4. PulseSoc production parity
5. LogiNexus UI polish

Android remains a later release-readiness gap and should not receive focused effort unless the issue affects shared native code or backend correctness.

## Hardened Event Emitters

The backend now emits standardized cursor-visible commerce events for:

- `payment_pending`
- `checkout_created`
- `checkout_blocked`
- `checkout_failed`
- `checkout_expired`
- `payment_succeeded`
- `payment_failed`
- `refund_issued`
- `dispute_opened`
- `dispute_updated`
- `dispute_resolved`

The shared helper is `pulse_emit_payment_checkout_event(...)`.

## Standard Event Envelope

Events flow through `pulse_notifications` and are visible through `/api/pulse/sync/events`.

Required normalized metadata:

- `event_type`
- `entity_type`
- `entity_id`
- `actor_id`
- `timestamp`
- `sync_cursor_key`

Safe commerce metadata:

- `domain: commerce`
- `category: payments`
- `transaction_id`
- `seller_transaction_id`
- `item_type`
- `item_id`
- `seller_type`
- `status`
- `amount_cents`
- `currency`
- `invalidates`
- `recipient_role`

## Event Visibility Through Sync Cursor

Seeded backend audit coverage validates:

- checkout blocked when Stripe is not configured
- checkout failed when provider session creation fails
- checkout created when provider session creation succeeds
- checkout expired through provider webhook
- payment succeeded through provider webhook
- payment failed through provider webhook
- refund issued through provider webhook
- dispute opened through provider webhook
- dispute updated through provider webhook
- dispute resolved through provider webhook
- all emitted events appear through `/api/pulse/sync/events`

## Activity/Orders/Seller/Marketplace Consistency Impact

Native cursor invalidation now covers:

- Activity Inbox
- Buyer Orders
- Seller Store / Seller Inventory
- Marketplace
- Notifications

This closes the highest-risk stale-state gap after seller inventory events because payment state is financial-grade trust data.

## Payment/checkout event coverage %

Payment/checkout event coverage is now estimated at 82%.

Covered:

- checkout created
- checkout blocked
- checkout failed
- checkout expired
- payment pending
- payment succeeded
- payment failed
- refund issued
- dispute opened
- dispute updated
- dispute resolved

Not fully covered:

- refund requested: no dedicated native/backend mutation route was identified in this pass
- order cancelled: no dedicated seller transaction cancellation route was identified in this pass
- provider-specific partial refund states need deeper treasury/provider audit

## Remaining Silent Mutation Paths

- `refund_requested` needs a first-class server route or existing route mapping.
- `order_cancelled` needs a first-class server route or existing route mapping.
- Marketplace listing report/save events remain outside this payment pass.
- Message seen/delete/report cursor mirroring remains incomplete.
- Call lifecycle cursor mirroring remains incomplete.
- Safety and verification state changes remain partially event-covered.

## Event Producer Coverage %

Estimated system-wide event producer coverage after this pass: 81%.

Previous estimate: 76%.

## Critical Production Risk Gaps

- Refund-request and order-cancel semantics need explicit server-authoritative routes before native should expose them as complete.
- Provider webhook idempotency exists, but high-volume replay chaos should be retested after this pass.
- APNs/FCM delivery and tap routing remain release-readiness gates, not current development blockers.

## ONE Highest-Impact Fix ONLY

Message, call, and safety event emission hardening.

Reason:

- Seller inventory and payment/order trust events now have cursor-visible coverage.
- The next largest stale-state risk is communication and safety state: read/seen, message report/delete, call ended/missed, block/mute/report/appeal changes.
- These affect Activity Inbox, Messenger, Calls, Notifications, Trust/Safety, and user confidence.
