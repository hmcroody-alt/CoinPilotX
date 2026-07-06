# PulseSoc Native Seller Listing Composer Practical QA

Date: 2026-07-05

Scope:
- Native `/pulse/marketplace/create` seller listing composer.
- Native Seller/Store create-listing entry.
- Existing PulseSoc marketplace listing, seller approval, product media, moderation, and review contracts.

## Result

Status: passed with scoped hardening.

Critical/security/data-loss/production-breaking blockers: none found.

Scoped blocker fixed:
- Native Seller/Store previously loaded listing metadata from public marketplace search. That endpoint correctly hides pending-review seller-created listings, which meant a successful native listing create could be visible in the web merchant dashboard but not visible in the native Seller/Store surface.
- Added protected `GET /api/pulse/marketplace/seller/listings` so the signed-in seller can see their own active, approved, review-ready, and pending-review listings without weakening public marketplace visibility.
- Updated native Seller/Store to use the seller-owned listings endpoint.
- Updated the composer success handoff to return to Seller/Store after review submission, because newly-created products are not public until marketplace review allows visibility.

No production WebView routes were modified.

## Backend Contract Checks

Local QA backend:
- Backend: `http://localhost:5107`
- Native QA proxy: `http://localhost:5108`
- Disposable SQLite database: `/tmp/pulsesoc_seller_listing_composer_qa.sqlite`
- Disposable QA account: approved merchant with seeded product media.

Verified:
- Missing media: `POST /api/pulse/marketplace/listings/create` returned `400` with `Upload or capture a cover photo before creating a listing.`
- Missing title/description: returned `400` with `Add a title and description for the listing.`
- Merchant approval error: pending merchant returned `403` with `Merchant approval is required before creating listings.`
- Successful create: approved seller with draft cover media returned `200` and a new `listing_id`.
- Public search after create: `GET /api/pulse/marketplace/search?q=<created title>` returned `0` items, which is correct because the listing remains under review.
- Seller-owned listings after create: `GET /api/pulse/marketplace/seller/listings` returned the newly-created listing with media payload.
- Web merchant dashboard still showed the created listing, preserving existing web behavior.

## QA Browser Checks

Built-in QA browser:
- `http://localhost:8094/pulse/marketplace/create?title=Create%20Listing`
- `http://localhost:8094/pulse/seller-store?title=Seller%20%2F%20Store`

Verified:
- `/pulse/marketplace/create` rendered native `Create Listing`.
- Title, description, category, product type, price label, and product media ID inputs rendered.
- `Capture Media`, `Web Uploader`, `Submit for Review`, and `Back to Store` rendered.
- Seller/Store route rendered signed in.
- Created QA listing appeared in Seller/Store after the backend create flow.
- Existing approved QA listing still appeared.
- Product media gallery rendered media tiles.
- `NativeMediaViewer` opened from Seller/Store media gallery and displayed listing title, seller identity, navigation, and share controls.

## Media and Fallback Coverage

Verified:
- Product media payload includes `media`, `media_assets`, `cover_image_url`, `thumbnail_url`, `gallery_json`, and `video_url` where available.
- Empty/missing media remains handled by existing Seller/Store empty states.
- Advanced upload/provider/edit tools remain on safe web fallback.
- Payout/checkout boundaries were not changed.

Not verified in this pass:
- Real physical image/video upload through the Marketplace-specific uploader.
- Provider payout onboarding.
- Payment checkout success.
- Admin approval/rejection workflow.

These remain release/provider QA items, not development blockers.

## Design QA

The native composer and Seller/Store surfaces keep the PulseSoc premium, futuristic control-center direction:
- Strong command-style sections.
- Glowing marketplace accents.
- Clear seller trust and review language.
- Server-authoritative copy for payouts, disputes, checkout, refunds, and moderation.

No user-facing internal design-system name was added to source code.

## Fix Summary

Files changed:
- `bot.py`
- `mobile-native/src/api/marketplace.ts`
- `mobile-native/src/screens/SellerListingComposerScreen.tsx`
- `scripts/pulsesoc_native_seller_listing_composer_audit.py`
- `scripts/pulsesoc_native_seller_listing_composer_qa_audit.py`
- `reports/pulsesoc_native_seller_listing_composer_qa.md`
- `reports/pulsesoc_native_progress.md`

## Recommendation

Next highest-value action: Marketplace Listing Edit + Seller Inventory Controls foundation.

Reason:
- The native create flow now works and sellers can see their own newly-created listings.
- The next commerce gap is lifecycle management: edit draft/review listings, update media, remove listings, and manage inventory status while keeping approval, moderation, checkout, payouts, refunds, and disputes server-authoritative.
