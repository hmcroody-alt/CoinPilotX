# PulseSoc Native Seller Listing Composer

Date: 2026-07-06

## Scope

Built the Native Seller Listing Composer foundation as the next highest-value action after Marketplace/Seller media QA.

This is a native client layer over the existing PulseSoc marketplace backend. No backend business logic was duplicated.

No production WebView routes were modified.

## Reused PulseSoc Backend Logic

Reused existing server-authoritative systems:

- `POST /api/pulse/marketplace/listings/create`
- existing merchant approval checks
- existing marketplace media ID requirements
- existing cover photo validation
- existing listing safety review
- existing risk scoring and moderation flags
- existing marketplace listing tables
- existing marketplace product media tables
- existing checkout, payout, refund, dispute, and fulfillment fallback boundaries

## Native Work Implemented

Added:

- Native `SellerListingComposerScreen`
- `createMarketplaceListing(...)` API wrapper
- native route wiring for `MarketplaceCreateGateway`
- `/pulse/marketplace/create` deep-link route now opens the native composer
- Seller/Store `Create Listing` entry point now opens the native composer
- listing title, short description, full description, category, price label, and product type controls
- product media ID handoff field for existing marketplace draft media
- Camera Studio handoff for marketplace media capture
- safe web fallback to existing protected marketplace create/upload flow
- backend validation/error display
- submit-for-review action
- navigation to Listing Detail after successful create

## Server-Authoritative Boundaries

Native does not decide:

- seller approval
- listing approval
- media moderation
- risk score
- safety flags
- payout readiness
- checkout eligibility
- refunds
- disputes
- fulfillment
- payment provider state

Those remain backend/provider-owned.

## Safe Web Fallback

Advanced seller creation and edit behavior remains on safe web fallback where native support is not complete:

- product media upload details
- listing edit if no safe JSON update endpoint exists
- tax forms
- bank onboarding
- payout setup
- disputes/refunds
- provider checkout and payment flows

## Verification

Run verification:

- `npm run --prefix mobile-native typecheck`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_seller_listing_composer_audit.py`
- `git diff --check`
- authenticated QA browser route check for `/pulse/marketplace/create`
- authenticated backend contract check for `POST /api/pulse/marketplace/listings/create`

QA browser evidence:

- `/pulse/marketplace/create` renders native `Create Listing`.
- Composer displays `Marketplace Forge`, listing details, product type options, product media handoff, Camera Studio handoff, Web Uploader fallback, Submit for Review, and Back to Store.
- `/pulse/marketplace/create` deep-link routing was corrected to open `MarketplaceCreateGateway` instead of the older Seller/Store mode gateway.
- `@egjs/hammerjs` was added to the mobile-native dependency lock because `react-native-gesture-handler` web requires it after clean `npm ci`.

Backend contract evidence:

- Authenticated local backend login succeeded.
- Seeded draft marketplace media ID: 5.
- `POST /api/pulse/marketplace/listings/create` returned `ok=true`, `listing_id=5`, and `Listing saved for safety review.`
- This confirms the native composer payload shape matches the existing server-authoritative endpoint.

## Remaining Gaps

- Direct native upload to `/api/pulse/marketplace/media/upload` should be added only after the shared upload service can safely target marketplace-specific upload endpoints.
- Listing edit remains fallback unless a safe JSON update endpoint is confirmed.
- Physical media capture and large product upload remain release QA.

## Critical Blocker Assessment

No critical, security, data-loss, production-breaking, or future-development-blocking issue is expected from this native composer foundation because server authority remains unchanged.
