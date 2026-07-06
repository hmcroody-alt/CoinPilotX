# PulseSoc Native Seller Inventory Foundation

Date: 2026-07-05

Status: foundation built.

## Scope

Built native seller inventory controls inside the Seller/Store surface using existing PulseSoc marketplace rules and data.

This is a seller control layer only. The backend remains server-authoritative for:
- seller approval
- marketplace review
- moderation
- public visibility
- checkout
- payout
- refunds
- disputes
- fulfillment

No production WebView routes were modified.

## Reused PulseSoc Backend Logic

Reused:
- Protected seller-owned listings endpoint.
- Existing marketplace listing table.
- Existing marketplace media payload builder.
- Existing marketplace media table.
- Existing seller approval check.
- Existing marketplace listing review/risk scoring.
- Existing public marketplace search approval filters.
- Existing NativeMediaViewer payload shape.
- Existing Seller/Store and Camera Studio navigation.
- Existing safe web/provider fallbacks for advanced edit, media, payout, checkout, tax, fulfillment, refunds, and disputes.

## Backend Additions

Added narrow seller-owned JSON endpoints:

- `PATCH/POST /api/pulse/marketplace/seller/listings/<listing_id>`
  - Updates title, description, category, price label, and quantity.
  - Requires login.
  - Requires approved merchant status.
  - Requires seller ownership.
  - Re-runs marketplace listing review and sets the listing back to review state.

- `POST /api/pulse/marketplace/seller/listings/<listing_id>/pause`
  - Soft-pauses the seller-owned listing.
- Public marketplace search remains approval/status filtered.

- `POST /api/pulse/marketplace/seller/listings/<listing_id>/resume`
  - Requires approved merchant status.
  - Re-runs marketplace listing review before restoring visibility eligibility.

- `POST/DELETE /api/pulse/marketplace/seller/listings/<listing_id>/delete`
- Soft-removes the listing with `seller_deleted`.
- Does not physically delete marketplace data.
- This is a soft removal path, not a destructive database delete.

## Native Implementation

Added to `SellerStoreScreen`:

- Seller inventory section.
- Listing status labels:
  - draft
  - pending review
  - approved/live
  - rejected
  - paused
  - sold/out of stock where surfaced by backend status
  - removed
- Listing selection.
- Edit gateway for:
  - title
  - description
  - category
  - price label
  - inventory quantity
- Save and review action.
- Pause/resume/delete action coverage through server-owned endpoints.
- Pause action.
- Resume review action.
- Remove action.
- Add Media handoff to Camera Studio.
- Advanced Edit Web fallback.
- Server-authoritative status messaging.
- Loading/error/offline behavior inherited from Seller/Store.

## Verification Completed

Completed checks:
- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` passed.
- `npm run --prefix mobile-native typecheck` passed.
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` passed.
- `venv/bin/python scripts/pulsesoc_native_seller_inventory_audit.py` passed.
- `git diff --check` passed.
- Authenticated backend contract checks passed against a local QA database:
  - seller login
  - seller-owned listing load
  - title/description/category/price/quantity update
  - review-state response
  - pause
  - public marketplace exclusion while paused
  - resume back through marketplace review
  - soft removal through `seller_deleted`
  - public marketplace exclusion after removal
- QA browser route check was practical but limited:
  - `npm run web:qa` launched and served `http://127.0.0.1:8094`.
  - `/pulse/seller-store?title=Seller%20%2F%20Store` remained behind the native auth boundary.
  - Browser login interaction did not complete in this pass, so authenticated Seller/Store UI interaction remains a QA-hardening follow-up.
  - Existing Expo web warnings were observed; no seller-inventory-specific browser crash was observed.

## Risk

Risk level: medium.

Reason:
- Seller inventory touches commerce and trust surfaces.
- Risk is controlled by keeping seller ownership, merchant approval, review, moderation, and public visibility server-authoritative.

## Remaining Gaps

Release/provider QA gaps:
- Physical marketplace media upload.
- Provider checkout completion.
- Stripe Connect payout onboarding.
- Admin approval/rejection lifecycle.
- Fulfillment/refund/dispute tooling.

Future native gaps:
- Dedicated listing detail editor route.
- Native media reorder/remove controls.
- Native inventory history.
- Buyer-facing order management.

## Next Recommendation

Recommended next highest-value action: Marketplace Seller Inventory Practical QA Hardening.

Reason:
- The foundation adds commerce lifecycle controls and new seller-owned mutation APIs.
- A short authenticated backend/browser QA pass should verify edit, pause, resume, soft-delete, status labels, public search filtering, and Seller/Store refresh before moving to another commerce or payments feature.
