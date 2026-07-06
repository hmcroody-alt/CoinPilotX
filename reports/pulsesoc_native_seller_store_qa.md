# PulseSoc Native Seller/Store Practical QA Hardening

## Scope

This QA pass covered the native Seller/Store Management foundation in the parallel `mobile-native` app. Production WebView routes were not changed.

The pass used a temporary local QA backend and authenticated QA account through the existing mobile auth API. The helper path used for browser QA remains development/local-only and still calls the normal backend login flow, so production auth is not weakened.

## Verification Environment

- Native client: `mobile-native`
- QA browser route: `http://localhost:8094`
- Local QA API proxy: `http://localhost:5108`
- Local backend: temporary SQLite database on port `5107`
- Authentication: local QA account created through `/api/mobile/auth/register`, then signed in through `/api/mobile/auth/login`
- Sensitive credentials: not written to the report

## Authenticated QA Results

Passed:

- `/pulse/seller-store` loaded signed in and rendered the native Seller/Store screen.
- `/pulse/merchant/apply` routed to the same native seller application surface.
- `/pulse/merchant/dashboard` routed to the native seller dashboard gateway.
- `/pulse/merchant/<seller>` routed to the native seller/profile gateway.
- `/pulse/marketplace/create` routed to the native create-listing gateway and safe fallback.
- `/pulse/marketplace` exposed the Seller/Store entry point.
- `/pulse/settings` exposed the Seller/Store entry point.
- `/pulse/profile` exposed the Seller/Store entry point from the About tab.
- Blank merchant application submit showed validation instead of sending incomplete data.
- Merchant application save returned the native success state.
- Seller status rendered after local seller approval was seeded.
- Storefront preview rendered with listing count.
- Orders summary rendered safely.
- Payout/connect returned the server-owned approval gate for an unapproved seller.
- Merchant route aliases preserved signed-in routing.
- Loading and error states stayed contained to the native screen.
- No production WebView route was modified.

## Scoped Fixes

- Added an accessible media tile action label and visible `Open media` overlay on Seller/Store media tiles so product media previews are easier to target during QA and clearer for assistive technologies.
- Extended the existing QA-only simulator auth helper to support local QA browser login redirects. This is guarded by `__DEV__` and a localhost API base URL and still calls the existing backend sign-in API.
- Added a safe local redirect target after QA login. Redirects must be local paths and reject protocol-relative, admin, API, and backslash paths.

## Backend Contract Finding

The local QA database was seeded with an approved seller and an approved listing containing `cover_image_url` and `gallery_json`.

Authenticated backend contract check:

- `GET /api/pulse/marketplace/search?limit=5` returned listing identity, category, seller, description, price, and safety fields.
- The response did not expose `cover_image_url`, `gallery_json`, `video_url`, or a normalized `media` array.

Impact:

- The native Seller/Store screen can render seller status and storefront/listing counts.
- The product media gallery cannot be fully verified from the current marketplace search payload.
- This is not a critical blocker because the native screen degrades safely and does not fabricate media state.

Recommended follow-up:

- Add or expose a native-safe seller/listing detail payload that includes authorized product media fields.
- Keep media authorization, moderation, storage, and processing status server-authoritative.
- Do not patch the native client to infer media URLs from unsupported fields.

## Browser/Device Verification Boundary

Browser verified:

- Authenticated route loading.
- Merchant application validation and save state.
- Payout/connect provider gate state.
- Entry points from Marketplace, Settings, and Profile/About.
- Seller status and storefront summary rendering.

Not browser verified:

- Real product media gallery opening through `NativeMediaViewer`, because the current backend payload did not expose listing media fields.
- Payment provider onboarding.
- Stripe/Connect payout flows.
- Product camera capture.
- Physical-device product upload.

Device-release blockers:

- Physical iPhone/Android marketplace media capture and upload.
- Provider payout/checkout return flows.
- Installed-app marketplace deep links.

## Critical Blocker Assessment

No critical, security, data-loss, production-breaking, or future-development-blocking issue was found.

The main remaining gap is a backend JSON payload contract gap for seller/listing media fields. That should be handled as a scoped marketplace media contract hardening task before claiming full native product media gallery parity.

## Next Highest-Value Action

Recommendation: Native Marketplace/Seller Media Payload Contract Hardening.

Reason:

- Seller/Store, Marketplace, NativeMediaViewer, Profile, Activity Inbox, and Search all depend on reliable listing media payloads.
- The current native UI is ready to consume media, but the inspected backend search response does not expose product media fields.
- A small server-authoritative payload hardening pass gives more leverage than building another unrelated screen.
