# PulseSoc Native Buyer Orders Foundation

Date: 2026-07-05

## Scope

Built the native buyer-side commerce layer as the counterpart to Seller/Store and Marketplace inventory. This is a read-only control and visibility layer over the existing PulseSoc payment ledgers.

## Reuse-First Findings

Existing production code already provides:

- Marketplace checkout creation through `/api/pulse/payments/checkout`.
- Seller transaction records in `seller_transactions`.
- Creator/course transaction records in `creator_transactions`.
- Existing purchase verification through `/api/pulse/payments/orders/<transaction_id>`.
- Existing purchase listing through `/api/pulse/payments/purchases`.
- Existing seller order listing through `/api/pulse/payments/seller/orders`.
- Existing Stripe/provider checkout, refund, dispute, payout, and webhook authority.
- Existing Marketplace listing, seller profile, Activity Inbox, and notification routing surfaces.

## Implemented

- Added native-safe read-only buyer order API aliases:
  - `GET /api/pulse/orders`
  - `GET /api/pulse/orders/<transaction_id>`
  - `GET /api/pulse/purchases`
- Normalized buyer order payloads with:
  - order id / transaction id
  - source ledger
  - item title/type/id
  - seller identity
  - marketplace listing relationship
  - status group
  - read-only receipt/support/dispute fallback URLs
  - provider-controlled shipping/tracking placeholder
- Added `mobile-native/src/api/orders.ts` for buyer order list/detail loading, offline cache, status normalization, currency formatting, and provider fallback opening.
- Added native `BuyerOrdersScreen` with:
  - Purchase History timeline
  - Order Detail screen
  - status visualization for pending/paid/processing/shipped/delivered/cancelled/refunded/failed
  - seller navigation
  - marketplace listing navigation
  - receipt and support fallback actions
  - loading/error/offline cache states
  - premium financial-grade PulseSoc visual treatment
- Added native deep-link aliases:
  - `/pulse/orders`
  - `/pulse/orders/<id>`
  - `/pulse/purchases`
  - `/dashboard/orders`
- Added entry points from Settings and Marketplace.
- Added notification/deep-link routing into the native buyer order screens.

## Server Authority Boundary

Native does not create payment, refund, dispute, fulfillment, shipping, payout, or receipt business logic. Those remain controlled by existing backend/provider flows.

The native app only reads existing transaction state and routes unsupported buyer actions to existing safe web/provider surfaces.

## QA Status

- Static implementation complete.
- Backend contract smoke passed against a temporary local SQLite database with a seeded buyer, seller, marketplace listing, and paid seller transaction:
  - `/api/pulse/orders` returned one paid order.
  - `/api/pulse/orders/<transaction_id>` returned the order detail with the marketplace listing relationship.
  - `/api/pulse/purchases` returned the same order with receipt fallback metadata.
- Built-in QA browser route checks passed for signed-out routing:
  - `/pulse/orders`
  - `/pulse/orders/<id>`
  - `/pulse/purchases`
  - `/dashboard/orders?order_id=<id>`
- Signed-out QA browser checks confirmed each route stays auth-gated and produced no console errors.
- Authenticated browser click-through with real order fixtures remains a follow-up because a valid authenticated buyer QA session with seeded purchases was not available in this pass.
- Payment provider behavior, real receipts, refund/dispute handling, shipping provider pages, and post-purchase notification tap behavior remain release QA items.
- No production WebView marketplace route was modified.

## Recommended Next Action

Run a short authenticated buyer order QA hardening pass with seeded buyer transactions. Verify purchase list/detail, status states, receipt/support fallback, Marketplace listing navigation, seller navigation, Activity Inbox order links, and empty/offline states before expanding commerce further.
