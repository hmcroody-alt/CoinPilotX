# PulseSoc Native Marketplace/Seller Media QA Hardening

Date: 2026-07-06

## Scope

This QA hardening pass verified the newly hardened marketplace media payload contract across native Marketplace, Listing Detail, Seller/Store, and the shared NativeMediaViewer.

No production WebView routes were modified. Backend marketplace business logic remains server-authoritative.

## Code Fix

Scoped native blocker fixed:

- Seller/Store media gallery tiles now pass a selected `initialIndex` into NativeMediaViewer.
- Before this fix, every Seller/Store media tile opened the viewer at index 0, which made mixed media QA ambiguous.
- The fix is native-only and does not change backend or WebView marketplace behavior.

Files changed:

- `mobile-native/App.tsx`
- `mobile-native/src/screens/SellerStoreScreen.tsx`
- `reports/pulsesoc_native_marketplace_media_qa.md`
- `reports/pulsesoc_native_progress.md`
- `scripts/pulsesoc_native_marketplace_media_qa_audit.py`

## Backend Contract Evidence

Authenticated local backend QA used a disposable SQLite database at `/tmp/pulsesoc_marketplace_media_qa.sqlite`.

Seeded cases:

- listing with 0 media
- listing with 1 image
- listing with multiple images
- listing with mixed images/videos
- listing with moderated/rejected media

Contract check result:

- login ok: true
- marketplace search ok: true
- listing count: 4
- `QA Mixed Media Listing`: media_count 4, media_assets_count 4, includes image and video
- `QA One Image Listing`: media_count 1
- `QA Empty Media Listing`: media_count 0
- `QA Moderated Media Listing`: media_count 0 because rejected product media is filtered server-side

Verified payload fields:

- `cover_image_url`
- `image_url`
- `thumbnail_url`
- `gallery_json`
- `video_url`
- `media`
- `media_assets`

## Authenticated QA browser evidence

QA browser setup:

- local backend: `http://localhost:5107`
- QA CORS/session proxy: `http://localhost:5108`
- Expo web QA build: `http://localhost:8094`
- native API base: `EXPO_PUBLIC_PULSE_API_BASE_URL=http://localhost:5108`

Verified routes:

- `/pulse/seller-store`
- `/pulse/marketplace`
- `/pulse/marketplace/1`

Verified Seller/Store:

- screen loaded signed in
- `4 Listings loaded`
- `4 Active/review ready`
- `0 Pending review`
- seeded listing rows rendered:
  - `QA Mixed Media Listing`
  - `QA Moderated Media Listing`
  - `QA Empty Media Listing`
  - `QA One Image Listing`
- Product media gallery rendered 5 media tiles
- Clicking `Open store media 2` opened NativeMediaViewer
- NativeMediaViewer showed listing context, author context, Prev/Next controls, and Share

Verified Marketplace feed cards:

- Marketplace feed rendered all four seeded listings
- cover image rendering worked where media exists
- missing media fallback worked for empty and moderated-media listings
- search surface rendered safely
- save/report controls rendered safely

Verified Listing Detail screen:

- `/pulse/marketplace/1` deep-linked to native Marketplace with the mixed-media listing detail opened
- detail displayed title, price, description, category, safety score, approval status, seller profile entry, checkout, save, report, and contact seller controls
- clicking listing media opened NativeMediaViewer from Listing Detail

Verified NativeMediaViewer:

- opened from Seller/Store gallery
- opened from Listing Detail media
- displayed selected listing context
- exposed previous/next navigation for mixed media
- showed author/profile context where available
- retained unsupported/provider fallback boundaries

## Edge-Case Coverage

| Edge case | Result |
| --- | --- |
| listing with 0 media | Passed; fallback rendered safely |
| listing with 1 image | Passed; cover and gallery entry rendered |
| listing with multiple images | Passed through normalized `media` array |
| mixed images/videos | Passed; image and video entries present in payload and viewer navigation available |
| deleted media | Not directly simulated; missing URL fallback remains covered by empty media case |
| moderated media | Passed; rejected media excluded server-side |
| broken URLs | Not fully browser-verified; NativeMediaViewer error state exists and remains release QA |
| large galleries | Partially covered; Seller/Store caps preview to 8 visible tiles and viewer receives up to 32 items |
| duplicate assets | Covered by native normalization and backend de-dupe through media URL |

## Payout/checkout boundaries

Payout and checkout behavior remains unchanged:

- checkout still calls existing backend/provider route
- payout still routes through existing provider onboarding fallback
- no native-only payment, payout, refund, dispute, or entitlement logic was added

## Loading/Error/Offline States

Verified by code and route behavior:

- Marketplace loads cached results on fetch failure where cache exists
- Seller/Store loads cached seller snapshot where cache exists
- empty media states render clear fallback copy
- unsupported media remains inside NativeMediaViewer fallback behavior

Offline/cache behavior was not force-tested in the browser during this pass.

## Console/Network Notes

No critical console errors were observed during the authenticated QA pass.

Non-critical web warnings observed:

- Expo AV deprecation warning
- React Native Web `shadow*` deprecation warning
- React Native Web image style deprecation warnings
- Expo web push listener warning

These are existing web-development warnings and are not marketplace media blockers.

## Device-Only Items Not Verified

The following remain release QA, not development blockers:

- physical device marketplace media capture
- large product media upload
- weak-network upload retry/cancel
- native video playback performance on iOS/Android
- provider checkout completion
- payout provider onboarding completion

## Critical Blocker Assessment

No critical blocker, security, data-loss, production-breaking, or future-development-blocking issue was found.

## Next Highest-Value Action

Recommended next native feature: Native Seller Listing Composer + Listing Edit Foundation.

Reason:

- Marketplace browse, Seller/Store, Media Upload, Camera Studio, NativeMediaViewer, Profile, Verification, Premium, Safety, and Activity Inbox are now in place.
- The backend already exposes seller application, media upload, and listing creation routes.
- Sellers can currently view/manage store readiness natively, but listing creation/editing remains mostly web fallback.
- A native seller listing composer would complete the core seller create/manage loop while keeping pricing, moderation, seller approval, checkout, payout, refunds, and disputes server-authoritative.
