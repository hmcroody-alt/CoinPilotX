# PulseSoc Native Commerce Polish + Provider Boundary QA

Date: 2026-07-06

Scope: stabilize the completed native marketplace commerce loop without adding new commerce features or moving payment authority into the native client.

## Executive Status

PulseSoc native commerce remains server-authoritative. The native app can browse marketplace listings, hand off checkout, show buyer purchase history, show order detail, expose seller inventory, and route to receipt/support/dispute fallbacks. Payment creation, Stripe session creation, seller approval, payouts, refunds, disputes, chargebacks, fulfillment, and shipping remain controlled by the existing backend/provider system.

No production WebView marketplace routes were changed in this pass.

## Boundary Findings

### Checkout And Payment

- `POST /api/pulse/payments/checkout` is still the single native checkout entry point.
- Unauthenticated checkout is blocked.
- Self-purchase is blocked.
- free or unpriced checkout is blocked.
- unapproved seller checkout is blocked.
- if Stripe is not configured, the backend creates a server transaction with `blocked_stripe_not_configured`, returns no `checkout_url`, and says no card was charged.
- successful checkout URL creation still depends on the existing Stripe/backend provider flow.
- no native-only payment provider, card collection, payment status mutation, or order-id generation was added.

No duplicate charge risk was introduced by native retry behavior in the repo-verified path: retrying checkout without Stripe config creates separate blocked server transactions and returns no provider checkout URL.

### Receipt And Order Integrity

- Buyer Orders read from `seller_transactions` and `creator_transactions`.
- Order IDs are server transaction IDs.
- Buyer order detail keeps `source_table`, `transaction_id`, `marketplace_listing_id`, receipt fallback URL, support fallback URL, and dispute fallback URL.
- Deleted or seller-deleted listings remain safely linked from historical buyer order records.
- Seller order summaries use the same server transaction ledger.

### Refunds And Disputes

- Existing Stripe webhook handling recognizes `charge.refunded` and `charge.dispute.created`.
- Seller transactions normalize refund/dispute-related webhook states server-side.
- Native Buyer Orders does not implement refund or dispute business logic.
- Native support/dispute controls open existing PulseSoc support/provider fallback routes.

### Shipping And Fulfillment

- Native Order Detail displays provider-controlled tracking copy and does not fabricate tracking data.
- No native shipping provider integration was added.
- Absent shipping provider data does not break the order UI contract.

### Activity Inbox And Notification Routing

- Activity classification includes marketplace/order/checkout/purchase/product signals.
- Native notification routing supports:
  - `/pulse/orders`
  - `/pulse/orders/<id>`
  - `/pulse/purchases`
  - `/dashboard/orders`
- Stripe checkout completion still calls existing seller and buyer notifications for completed purchases.

Provider-delivered purchase, refund, dispute, and shipping notifications still require a configured provider test flow before release.

## Backend Contract Verification

The audit seeds a local temporary database with buyer, seller, unapproved seller, approved marketplace listing, free listing, self-owned listing, and a historical refunded order linked to a seller-deleted listing.

Verified:

- checkout requires authentication
- self-purchase is rejected
- free/unpriced checkout is rejected
- unapproved seller checkout is rejected
- no-Stripe checkout creates blocked server transactions with no checkout URL
- blocked no-provider transactions appear in buyer order history as failed payment state
- refunded deleted-listing order remains visible and linked
- buyer order detail includes provider-controlled tracking/fallback fields
- seller order endpoint sees the same linked transaction

## QA Browser Route Checks

Practical route checks were performed in the built-in QA browser against the native web QA build where possible.

Checked:

- `/pulse/orders`
- `/pulse/purchases`
- `/dashboard/orders?order_id=1`
- `/pulse/marketplace`
- `/pulse/seller-store?title=Seller%20%2F%20Store`

Result:

- Routes remained reachable through the native router/auth gate.
- No production WebView marketplace route was modified for this pass.
- Authenticated data-rich provider checks remain backend-contract verified rather than provider-live verified.

## Provider-Only Release Blockers

These were not claimed as verified:

- live Stripe checkout success with a real test card
- expired checkout session recovery
- duplicate-click Stripe session behavior under a configured provider account
- Stripe receipt page rendering
- refund provider event delivery
- dispute provider event delivery
- shipping/tracking provider event delivery
- push notification tap behavior for purchase/refund/shipping/dispute events
- weak-network checkout browser handoff on physical devices

## Risk Assessment

Risk level: low for this pass.

Reason:

- no payment provider code was moved into native
- no order/refund/dispute logic was duplicated in native
- no production WebView route was changed
- backend contract checks cover the local boundary states most likely to regress

## Recommendation

Next highest-value action: Native Commerce Activity Fixture Hardening.

Why:

The commerce transaction loop is now complete and provider boundaries are documented. The next stabilization layer should verify purchase, refund, dispute, shipping, and seller-payment notification fixtures through Activity Inbox routing so commerce feels alive without weakening payment authority.

Safest plan:

1. Seed activity/notification fixtures for purchase complete, seller payment, refund, dispute, shipping update, failed payment, and checkout abandoned.
2. Verify Activity Inbox category grouping and deep links into Buyer Orders, Seller/Store, Marketplace Detail, or safe web fallback.
3. Keep provider event creation server-side.
4. Fix only routing, copy, and fallback issues found.
5. Continue to defer live Stripe/provider and push-tap behavior to release QA.
