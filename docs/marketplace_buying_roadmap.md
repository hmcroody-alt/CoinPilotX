# Marketplace Buying Experience — Gap Analysis & Phased Roadmap

Mission: turn the Buying screen from a discovery shell into a complete buyer
marketplace (discovery, search, location, favorites, cart, listing detail,
offers, checkout, orders, commerce messaging, returns, disputes, safety,
recommendations). This document maps every mission section to what the codebase
actually has, and sequences the rest.

Audit date: 2026-08-05. Sources: `mobile-native/src/api/marketplaceScreen.ts`
(`MARKETPLACE_MOCK_DATA_GAPS`), `mobile-native/src/api/commerceInbox.ts`
(`INBOX_MOCK_DATA_GAPS`), `bot.py` route grep, `services/marketplace_engine.py`.

## Where things stand

### Real today (backend + mobile wired)

- Search & browse: `GET /api/pulse/marketplace/search` (bot.py ~48668); mobile
  `searchMarketplace()` with AsyncStorage cache and offline fallback.
- Listing browse/detail modal, save/unsave (`/api/pulse/saved` family, optimistic
  with rollback via `social/useSaveAction.ts`), report listing.
- Single-item checkout: `POST /api/pulse/payments/checkout` (Stripe) — no basket.
- Buyer purchase history: `GET /api/pulse/payments/purchases`, order detail route.
- Commerce messaging: `POST /api/pulse/messages/start`, routed to the Commerce
  Inbox (`BusinessOsMessages`) when the conversation split flag is on.
- Seller side: listing CRUD, pause/resume/delete, media upload, seller orders,
  Business OS order fulfil/complete/cancel/dispute routes.
- Listing badges NEW/FEATURED and the cart-vs-offer fulfillment split — driven by
  real `created_at`, `featured`, `delivery_type` columns.

### Phase 1 (this branch, mobile-only) — location honesty + discovery

Implemented in `MarketplaceManagerScreen.tsx` + `marketplaceScreen.ts`:

- One derivation (`marketplaceLocation({city, categoryFiltered})`) feeds the
  heading, location strip, footer and empty state, so "near you" can never
  appear without a confirmed location. Contradictory fallback copy and its
  feature flag are deleted.
- Self-reported city preference (AsyncStorage), LocationSheet to set/change/
  clear it, strip is a working control. No device geo is read; nothing is
  shared with sellers.
- Empty states carry only actions that exist: Set/Change location, Show all
  categories. Category chip rail derived from returned listings ("For you" +
  real categories, no dead chips).
- Tests: `marketplaceLocation.test.ts` asserts no proximity claim without a
  city, copy pairs per state, and that every empty-state action maps to a real
  control. `MARKETPLACE_MOCK_DATA_GAPS` count is asserted so faked data fails.

### Phases 2, 3, 4, 6 (Aug 2026 mission) — SHIPPED on this branch

- **Phase 2 cart**: `services/marketplace_cart_routes.py` route pack (add/list/
  update/remove/validate/confirm-price/checkout-group), `marketplace_cart_items`
  idempotent schema, per-line states, per-seller checkout with idempotency key.
  Mobile: `MARKETPLACE_CART_ENABLED = true`, server cart in
  `marketplaceCommerce.ts`, new `MarketplaceCartScreen` (seller/fulfillment
  grouping, qty stepper, price-change confirmation, validate-before-checkout),
  `screens.cart` in all 11 i18n catalogs. Checkout reports the created session
  honestly — the app never opens payment pages natively (existing boundary).
- **Phase 3 offers**: `services/marketplace_offers_routes.py` pack (create/
  counter/accept/decline/withdraw, 72h TTL, accepted-until window, permission
  guards, buyer/seller hydration). Mobile: `MARKETPLACE_OFFERS_ENABLED = true`,
  Buying screen syncs server offers with optimistic action + full-replace
  reconciliation; inbox expiry banner now live. Gap ledger 12 → 10 entries.
- **Phase 4 (buyer-side slice)**: BuyerOrders screen has status tabs (All /
  Processing / Shipped / Delivered / Cancelled / Returns) filtering the
  server's `status_group`, plus a Returns tab reading the returns pack
  (fail-soft). Cart checkout carries an idempotency key minted per confirm
  intent. NOT done from the Phase 4 spec: buy-now reservation, network-loss
  reconciliation after authorization, meetup confirmation codes — those live
  in the payments core of bot.py, out of route-pack scope.
- **Phase 6 (buyer flow)**: `services/marketplace_returns_routes.py` pack —
  `marketplace_returns` + events tables, full state machine (opened →
  awaiting_seller/awaiting_buyer → under_review → resolved_*/closed), evidence
  snapshot, marketplace-product-only guard. Mobile: Returns tab rows + a
  "Request a return" panel on eligible order detail (reason chips mirroring
  the accepted reason set, explanation, refund default). NOT done: separate
  disputes table, evidence media upload UI, seller-side mobile surface.

All three route packs are registered in `bot.py` behind the existing
fail-soft pattern. **Deploy ordering rule: backend must be live before an app
build with these flags reaches users** — fail-soft empty states are a safety
net, not a release strategy.

### Still mock / missing (the honest ledger)

From `MARKETPLACE_MOCK_DATA_GAPS` (10 entries): boost purchase, per-listing
views/saves/offer counts, saved searches, location radius + per-item distance,
seller rating aggregate, buyer rating flow, meetup spots, sold-history
revenue, price history (strikethrough), unread counts for bell/buyer messages.
From `INBOX_MOCK_DATA_GAPS`: conversation→offer/order/listing join, away mode,
reply-time stats.

## Phased plan

Each phase is shippable alone; later phases assume earlier ones. Backend work
lands as new tables in `bot.init_db()` (idempotent, `AUTO_PK_TABLES`) plus
routes — run `scripts/realtime_audio_change_gate.py` before any bot.py commit,
and the protection suite (`scripts/protection/run_protection_suite.py`) since
payments/auth/uploads are protected subsystems.

### Phase 2 — Cart (mission §8, §12 partial)

Backend: `marketplace_cart_items` table (user_id, listing_id, variant, qty,
price_snapshot_minor, added_at); routes add/list/update/remove; validation
endpoint returning per-line state (available / price_changed / sold / removed /
low_stock / restricted). Extend checkout to accept a cart group per seller;
reuse the existing Stripe surface. Mobile: flip `MARKETPLACE_CART_ENABLED`,
replace local `cartIds` with server cart, cart screen grouped by seller and
fulfillment (digital / pickup / shipping separated), price-change confirmation
before pay, idempotent duplicate-tap handling. Tests: sold item blocks
checkout, price change requires confirmation, multi-seller grouping, duplicate
taps don't duplicate lines.

### Phase 3 — Offers (mission §11)

Backend: `marketplace_offers` table + CRUD (states: draft/sent/viewed/
countered/accepted/declined/expired/withdrawn/converted/cancelled — the mobile
state machine in `marketplaceOffers.ts` is already written and tested, 72h
TTL); accept → reserve inventory + time-limited checkout + push; expiry job in
a worker releases reservations; seller minimum never serialized to buyers.
Mobile: flip `MARKETPLACE_OFFERS_ENABLED`, offer composer sheet (amount, qty,
delivery, message, expiration), offer state on listing detail and inbox chips.
An accepted offer writes an order row on completion — this also unblocks
sold-history revenue (gap ledger). Tests: reservation on accept, release on
expiry, no auto-charge without explicit authorization.

### Phase 4 — Checkout hardening + buyer orders (mission §12–13)

Reservation on buy-now, idempotency keys on payment, reconcile network-loss
after authorization, failure states (price changed, sold, reservation expired,
declined, invalid address). Buyer order list tabs (processing / ready /
shipped / delivered / returns / cancelled / disputes) over the existing
purchases + Business OS order routes; order timeline, receipt, track/confirm
pickup, meetup confirmation code. Tests: payment idempotency, no oversell,
confirmation only after server confirmation.

### Phase 5 — Commerce messaging completion (mission §10) — REMAINING

Deliberately not attempted in the Aug 2026 mission: the work is a
`conversation_domain` column plus scoped unread counts inside bot.py's
messenger core (protected, high-traffic), not a bolt-on route pack. The
mobile derivation (`conversationDomain.ts`) and chip resolver already exist
and will light up when the column lands.

Backend conversation_domain column + scoped unread counts; conversation →
offer/order/listing join so context chips resolve server-side (mobile resolver
already built behind `EXPO_PUBLIC_MESSAGES_MOCK_CHIPS`). Thread reuse on
repeated "Message seller" taps (server-side dedupe). Off-platform-payment
warning, link scanning, evidence preservation flags. Tests: commerce thread
never in social Messages, repeated taps reuse thread.

### Phase 6 — Returns & disputes (mission §14) — PARTIALLY SHIPPED (see above)

Buyer return flow + state machine shipped as a route pack. Remaining:
disputes escalation table, evidence media upload UI, seller-side mobile
surface, inbox returns-tab join.

Net-new domain: `marketplace_returns` + `marketplace_disputes` tables, state
machines (opened → awaiting_buyer/awaiting_seller → under_review → resolved_*/
appealed/closed), evidence bundle (listing snapshot, order, payment events,
conversation, policy snapshot). Buyer flow: reason, explanation, evidence
upload (existing R2 upload path), desired resolution. Surfaces in order detail
and inbox returns tab (already stubbed).

### Phase 7 — Real location & distance (mission §3) — REMAINING

Not attempted: geocoding + distance ranking must live in the bot.py search
route (protected diff-content gate territory) and needs a geocoding
integration decision. Phase 1's honesty layer means the UI makes no
proximity claims meanwhile.

Coarse lat/lon on listings (geocode seller's self-reported area server-side —
never store buyer device coords), account radius preference (5/10/25/50/100/
anywhere), distance in search ranking and on cards (distance only, never
coordinates), "Nearby" tab + expand-radius empty-state action. Privacy tests:
exact coordinates never serialized.

### Phase 8 — Trust & personalization (mission §2, §7, §15) — REMAINING

Not attempted. Saved searches is the best next route-pack candidate (own
table, no core edits); ratings/reviews and recommendations touch feed
ranking and need product decisions first.

Seller rating aggregate + buyer review write path (verified reviews), saved
searches with new-match counts, price-drop alerts (needs price history
column), meetup spots + Safety Center content, recommendations sections
(recently viewed, from sellers you follow, price drops) ranked only from the
mission's allowed inputs, sponsored labeling.

## Standing rules for every phase

- No UI claim without a data source; extend `MARKETPLACE_MOCK_DATA_GAPS` /
  `INBOX_MOCK_DATA_GAPS` instead of faking, and keep the count assertions.
- Feature flags stay off until the backend is live; flipping a flag must be a
  data change, not a UI change.
- bot.py edits: run the realtime-audio gate + protection suite; schema changes
  idempotent; no screen-level audio/session code ever.
- `npm run verify` green (typecheck, i18n catalogs, jest) before any handoff;
  device QA for checkout and uploads per protection policy.
