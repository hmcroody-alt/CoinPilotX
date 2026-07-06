# PulseSoc Native Marketplace/Seller Media Payload Contract

## Scope

This hardening pass fixes the native Marketplace/Seller media payload gap found during Seller/Store authenticated QA.

The change is additive: existing marketplace search fields remain available, and the backend remains authoritative for listing visibility, moderation, seller approval, and media authorization.

## Backend Contract Change

Updated endpoint:

- `GET /api/pulse/marketplace/search`

The endpoint now includes media fields needed by the native Marketplace, Seller/Store, and shared NativeMediaViewer surfaces:

- `cover_image_url`
- `image_url`
- `thumbnail_url`
- `gallery_json`
- `video_url`
- `media`
- `media_assets`

The `media` and `media_assets` arrays use the existing native media shape where possible:

- `id`
- `product_media_id`
- `media_type`
- `media_url`
- `thumbnail_url`
- `poster_url`
- `mime_type`
- `file_size`
- `width`
- `height`
- `duration_seconds`
- `processing_status`
- `is_cover`

## Existing Data Reused

The payload is built from existing server-owned data:

- `marketplace_listings.cover_image_url`
- `marketplace_listings.gallery_json`
- `marketplace_listings.video_url`
- `marketplace_listings.media_url`
- `marketplace_product_media`

Media URLs are normalized through the existing `pulse_media_url(...)` helper, which delegates to the existing media service.

## Safety Rules

- Listing search visibility still uses existing listing status and approval filters.
- Product media rows with rejected, removed, blocked, or blocked-review moderation status are excluded from the payload.
- No native-only media inference was added.
- No checkout, payout, tax, dispute, refund, fulfillment, or admin-review logic changed.
- Existing web marketplace payload compatibility is preserved because existing fields remain present and new fields are additive.

## Native Impact

Native code already supports these fields through `mobile-native/src/api/marketplace.ts`.

Expected improvements:

- Marketplace cards can render cover media reliably.
- Listing Detail can open shared media viewer with server-provided product media.
- Seller/Store media gallery can verify product media and NativeMediaViewer behavior.
- Search/Discovery and Activity Inbox marketplace routes can reuse the same listing payload.

## Authenticated Backend Contract QA

Required QA contract:

1. Start a local QA backend.
2. Register/sign in a local QA account through existing auth APIs.
3. Seed an approved seller and approved listing with `cover_image_url`, `gallery_json`, and related `marketplace_product_media`.
4. Request `GET /api/pulse/marketplace/search?limit=5` using the authenticated session.
5. Confirm at least one returned item includes:
   - `cover_image_url`
   - `thumbnail_url`
   - `gallery_json`
   - `media`
   - `media_assets`
   - a non-empty first `media[0].media_url`

Completed QA evidence:

- Local QA backend: temporary SQLite database on port `5107`.
- Local QA proxy: `http://localhost:5108`.
- Expo web QA: `http://localhost:8094` with `EXPO_PUBLIC_PULSE_API_BASE_URL=http://localhost:5108`.
- Seeded approved seller/listing: `QA Product Media Contract`.
- Authenticated backend contract check returned:
  - `ok=true`
  - `media_count=3`
  - `first_media_type=image`
  - `has_thumbnail=true`
  - `has_video_url=true`
- Built-in QA browser check verified:
  - Seller/Store rendered `1 Listings loaded`.
  - Seeded listing title rendered.
  - Three `Open store media` tiles rendered.
  - Opening the first tile displayed NativeMediaViewer with the listing title, close control, and image media.

QA note:

- The QA browser needed the app origin and API origin to share the same `localhost` host. `127.0.0.1` as the API host did not reliably preserve the browser session cookie from the local login request into later marketplace API calls.

## Remaining Gaps

- Physical-device product media capture/upload remains release QA.
- Provider checkout/payout flows remain web/provider fallback.
- A dedicated owned-listing seller dashboard endpoint may still be useful later, but it is not required for the current media payload contract.

## Recommended Next Action

Recommendation: Native Marketplace/Seller Media QA Hardening.

Reason:

- The backend now exposes the product-media contract native already expects.
- The next safest action is a short authenticated QA browser pass over Marketplace, Listing Detail, Seller/Store media gallery, and NativeMediaViewer opening.
- Keep payment/payout/provider flows on fallback and do not block development on physical media capture.
