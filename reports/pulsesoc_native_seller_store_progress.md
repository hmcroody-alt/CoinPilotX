# PulseSoc Native Seller/Store Management Foundation

## Scope

Built the native Seller/Store Management foundation as a control and gateway layer over the existing PulseSoc marketplace platform. The native app does not own seller eligibility, marketplace moderation, pricing, checkout, payouts, tax, refunds, disputes, fulfillment, or admin review. Those remain server/provider-authoritative.

## Existing PulseSoc Systems Reused

- `POST /api/pulse/marketplace/seller/apply`
- `GET /api/pulse/payments/seller/orders`
- `POST /api/pulse/payouts/connect`
- Existing marketplace search/listing payloads from `GET /api/pulse/marketplace/search`
- Existing marketplace listing detail, save, report, chat, and checkout wrappers
- Existing marketplace media payloads and shared `NativeMediaViewer`
- Existing merchant web routes:
  - `/pulse/merchant/apply`
  - `/pulse/merchant/dashboard`
  - `/pulse/merchant/<username>`
  - `/pulse/merchant/payouts`
  - `/pulse/marketplace/create`
- Existing seller/listing/media/order/payout/verification/trust database and business rules in production PulseSoc.

## Native Work Completed

- Added `SellerStoreScreen`.
- Added seller/store snapshot loading and offline cache helpers.
- Added seller application wrapper using the existing seller apply API.
- Added seller orders wrapper using the existing seller orders API.
- Added payout/connect wrapper using the existing payout onboarding API.
- Added safe web fallbacks for protected merchant application, product creation, merchant dashboard, merchant payouts, and merchant profile routes.
- Added product media gallery using marketplace media payloads and the shared native media viewer.
- Added seller/store route aliases:
  - `/pulse/seller-store`
  - `/pulse/merchant/apply`
  - `/pulse/merchant/dashboard`
  - `/pulse/merchant/<sellerId>`
  - `/pulse/marketplace/create`
- Added notification/deep-link routing into the native Seller/Store gateway.
- Added entry points from Marketplace, Profile, and Settings.

## Native UX Boundary

Native now provides:

- Seller status/readiness overview from existing marketplace/order data.
- Merchant application quick-save path.
- Product media capture gateway.
- Product listing web gateway.
- Product media gallery preview.
- Seller orders summary where the API supports it.
- Payout onboarding gateway.
- Trust, verification, premium, and safety navigation.

Native intentionally does not implement:

- Private document upload review.
- Admin merchant review.
- Stripe Connect provider ownership.
- Tax forms.
- Refunds/disputes.
- Fulfillment management.
- Full product editor.
- Seller analytics beyond existing exposed API data.

## QA Notes

Static verification and route checks are required for this foundation. Payment, payout provider onboarding, private document upload, checkout, large product media upload, and physical-device seller camera flows remain release QA blockers, not development blockers.

## QA Evidence

Static checks:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed.
- `npm run --prefix mobile-native typecheck`: passed.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: passed, 17/17 checks.
- `venv/bin/python scripts/pulsesoc_native_seller_store_audit.py`: passed.
- `git diff --check`: passed.

Built-in QA browser checks:

- Local API health on `http://127.0.0.1:5107/health`: passed.
- Expo web server on `http://localhost:8094/pulse/seller-store`: HTTP 200 before browser navigation.
- Signed-out route checks confirmed the native auth gate for:
  - `/pulse/seller-store`
  - `/pulse/merchant/apply`
  - `/pulse/merchant/dashboard`
  - `/pulse/merchant/demo-seller`
  - `/pulse/marketplace/create`
  - `/pulse/marketplace`
  - `/pulse/settings`
  - `/pulse/profile`

Authenticated backend contract checks against the temporary local QA database:

- `POST /api/pulse/marketplace/seller/apply`: `200`, `ok=true`.
- `GET /api/pulse/payments/seller/orders`: `200`, `ok=true`, empty orders handled safely.
- `POST /api/pulse/payouts/connect` for an unapproved merchant: `403` with the expected server-owned approval gate.

Browser QA limitation:

- Authenticated native browser route rendering could not be completed in this pass because React Native Web login input/click automation did not trigger the submit flow, and the browser automation page scope is read-only for local storage seeding. This is a QA automation limitation, not a seller/store implementation failure.
- Do not mark seller/store as authenticated-browser verified until a reliable local browser auth seed or manual authenticated browser pass is available.

## Next Recommended Action

Native Seller/Store Practical QA Hardening.

Reason:

- This foundation touches checkout, seller approval, product creation, media upload, and payout onboarding surfaces.
- A short authenticated QA browser pass should verify route reachability, application validation, provider fallback states, and entry point behavior before moving to another major native module.

Recommended QA focus:

- `/pulse/seller-store`
- `/pulse/merchant/apply`
- `/pulse/merchant/dashboard`
- `/pulse/merchant/<seller>`
- `/pulse/marketplace/create`
- Seller application validation and success/error messaging.
- Payout/connect approved-vs-unapproved state.
- Product media gallery fallback.
- Settings/Marketplace/Profile entry points.
- Notification/deep-link routing.
- Provider-only gaps documented separately from browser-verified behavior.
