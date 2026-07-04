# PulseSoc Native Marketplace Progress

Date: 2026-07-04

## Scope

The native Marketplace foundation lives only under `mobile-native/`. It does not touch production WebView paths, production templates, backend marketplace routes, payment/order/payout code, seller approval logic, moderation, reporting, saved-products storage, or marketplace database rules.

Server APIs stay authoritative. Native Marketplace is a new client for existing PulseSoc marketplace browsing, listing detail, save/report, seller contact, and checkout-routing contracts.

## Existing Web/Backend Implementation Inspected

Current PulseSoc marketplace surfaces inspected before implementation:

- Web marketplace route: `/pulse/marketplace`.
- Web marketplace create route: `/pulse/marketplace/create`.
- Merchant dashboard route: `/pulse/merchant/dashboard`.
- Search API: `GET /api/pulse/marketplace/search`.
- Seller apply API: `POST /api/pulse/marketplace/seller/apply`.
- Listing create API: `POST /api/pulse/marketplace/listings/create`.
- Marketplace media upload API: `POST /api/pulse/marketplace/media/upload`.
- Listing save API: `POST /api/pulse/marketplace/listings/save`.
- Listing report API: `POST /api/pulse/marketplace/listings/report`.
- Seller contact API: `POST /api/pulse/messages/start`.
- Checkout API: `POST /api/pulse/payments/checkout`.
- Payment/order APIs: `/api/pulse/payments/orders/<transaction_id>`, `/api/pulse/payments/purchases`, `/api/pulse/payments/entitlements`.
- Marketplace tables: `marketplace_listings`, `marketplace_product_media`, `marketplace_sellers`, `marketplace_saved_products`, `marketplace_reports`, `marketplace_orders`, and seller transaction/payout tables.

## Implemented Native Foundation

- Native Marketplace browse tab.
- Native Marketplace stack/detail route.
- Search hook through existing `/api/pulse/marketplace/search`.
- Offline cache for last marketplace results.
- Listing cards with category, price, safety score, seller name, and media cover where the existing API payload provides media fields.
- Listing detail modal/screen using the existing listing payload.
- Image/media gallery support through shared `NativeMediaViewer`.
- Save listing action through existing save API.
- Report listing action through existing report API.
- Seller contact hook through existing Messenger start API.
- Purchase/checkout routing through existing `/api/pulse/payments/checkout` provider flow.
- Marketplace notification/deep-link routing for `/pulse/marketplace`, `/pulse/marketplace/<id>`, and `/pulse/marketplace?listing=<id>`.
- Loading, empty, error, offline, retry, and unsupported-media states.
- Web fallback for full marketplace, seller tooling, and unsupported listing fields.

## Reuse-First Boundaries

Native Marketplace does not implement its own:

- Listing approval logic.
- Marketplace moderation or safety scoring.
- Seller trust/readiness rules.
- Payment, checkout, order, refund, dispute, escrow, payout, or entitlement logic.
- Listing save/report persistence.
- Seller contact authorization.
- Marketplace media authorization or storage decisions.
- Server-side validation.

Those remain owned by the existing PulseSoc backend, database, marketplace services, payment provider flow, and moderation/revenue safety logic.

## Native-Only Layer

The rebuilt native layer is limited to:

- Browse/search UI.
- Listing card UI.
- Listing detail UI.
- Media gallery presentation through the shared native media viewer.
- Save/report/contact/checkout button states.
- Loading, empty, offline, and error states.
- Native navigation and deep-link routing.

## Known Gaps

- The current marketplace search API returns basic listing fields. Native media gallery and seller profile navigation are ready for richer fields but degrade safely when the payload does not include them.
- Dedicated listing detail API was not found in the current backend scan; the first native detail slice hydrates from the search/listing payload.
- Unsave is not exposed by the existing marketplace listing save API, so native does not invent an unsave endpoint.
- Seller onboarding, listing creation/editing, merchant dashboard, inventory, orders, refunds, disputes, and payouts remain web/provider flows.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real-device marketplace search typing latency.
- Real-device listing detail modal feel.
- Real-device media gallery performance for listing images/videos.
- Real-device checkout provider handoff.
- Real-device seller contact route recovery.
- Marketplace notification tap routing on physical iOS and Android devices.

## Next Recommendation

Recommended next native feature: Native Search + Discovery Foundation.

Reason: the backend already exposes `/api/pulse/search`, and the current native app now has enough native destinations to route results for posts, profiles, reels, status, marketplace, messages, and media without relying on broad WebView fallbacks.
