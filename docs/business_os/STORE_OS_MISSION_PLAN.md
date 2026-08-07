# Store OS Mission — Gap Audit & Build Plan

**Date:** 2026-08-05
**Scope:** Transform the native Store dashboard + Marketplace Selling hub into the full
seller operating system defined in the Store OS mission spec.
**Status:** Plan only. No code changed. Repo is on `codex/emergency-live-audio-recovery`
with unrelated dirty files — Store OS work should start from a clean branch off main.

---

## 1. Ground truth: what already exists

The mission spec assumes a mostly-empty foundation. That is wrong in both directions:
more exists than the spec assumes, and some of what exists contradicts the spec.

### Backend — two parallel surfaces

| Surface | Status | Notes |
|---|---|---|
| `/api/pulse/marketplace/*`, `/api/pulse/orders` | **Live in prod** | What the app uses today. Legacy model, no audit trail, no ledger integration. |
| `/api/business-os/store/*`, `/api/business-os/marketplace/*` | **Built but dark** | ~40 routes: storefront, products, collections, orders, money, disputes, payouts, assistant. Gated by env flags `BUSINESS_OS_STORE` / `BUSINESS_OS_MARKETPLACE` (dark = 404 on every route). Controllers: `services/business_os/store/api.py`, `services/business_os/marketplace/api.py`. |

The dark surface already has what the mission's Section 16 (server authority) demands:

- **Double-entry ledger** with idempotency keys (`services/business_os/ledger/ledger.py`,
  UNIQUE on `idempotency_key`).
- **Append-only audit tables** — `business_os_store_audit`, `business_os_mkt_audit`
  (actor, action, before/after JSON, reason) written on every mutation.
- **The 4 money bugs from `business_os_ground_truth.md` are fixed in-code**
  (verified): refund idempotency via `_derive_refund_id()` (`refunds.py:65`), atomic
  capture on owned connection with `mkt_capture:{order_id}` keys (`orders.py:327`),
  ledger row-locking portable to Postgres, Stripe per-refund-delta posting.

### Mobile — screens exist, data is partly mocked

- `StoreDashboardScreen.tsx` — KPI cards (all 4 wired and tappable), status strip,
  listing tabs, quick-link grid, search. A 5-state readiness ladder
  (not_set_up → incomplete → pending_review → live → paused) already exists behind
  `EXPO_PUBLIC_STORE_READINESS` — this is most of the spec's Section 0 status fix.
- `MarketplaceManagerScreen.tsx` — Selling/Buying toggle, listing tabs
  (active/sold/drafts/hidden), offers-waiting metric, "List an item" CTA.
- Commerce inbox exists behind `EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT`; the domain
  discriminator exists server-side (`conversation_domain`, 5-value enum).
- 71 declared mock-data gaps (`docs/business_os/MOCK_DATA_TABLES.md`), length-locked
  by tests: views, seller rating, on-time %, ship-today, meetup spots, sold history,
  offer counts, etc.

### Confirmed missing (nothing to reuse)

1. **Offers** — no table, no routes, anywhere. Mobile flag `MARKETPLACE_OFFERS_ENABLED`
   is hard-false with a stub API module.
2. **Inventory reservations** — current model is check-then-atomic-decrement at
   payment. No reserve-on-checkout / release-on-expiry. No adjustment audit reasons,
   no locations/transfers.
3. **Shipping & return profiles** — orders carry `fulfillment_type` + `tracking_ref`
   only. No profile models, no policy snapshot into orders.
4. **Shared product catalog** — three independent product tables
   (`business_os_mkt_products`, `business_os_store_products`, `business_os_ent_products`)
   with no shared identifier. No barcode/UPC matching. The spec's "search PulseSoc
   catalog / sell this product" flow has zero foundation.
5. **Store Management Hub** — "Manage all" expands inline; no hub screen.
6. **Seller eligibility enforcement** — the store service checks staff role but never
   consults seller approval or category restrictions (**authorization gap**, confirmed
   in ground-truth doc: anyone with a staff role can publish).
7. **Full add-listing engine** — `SellerListingComposerScreen` is a minimal gateway
   (title/description/category/price/type/media-IDs). No variants, media upload UI,
   fulfillment, compliance, preview, drafts, scheduling, barcode.
8. **Commerce-scoped notifications badge** — categories exist server-side
   (`marketplace/notifications.py`) but the unread badge sums across domains without
   a predicate (the "99+ everywhere" problem).

### Spec's screenshot fixes — current wording confirmed in code

| Spec fix | Where it lives today |
|---|---|
| "Open for orders" with zero listings | `StoreDashboardScreen.tsx:494`; honest ladder already built behind `EXPO_PUBLIC_STORE_READINESS` (`storeDashboard.ts:489+`) |
| "0 items · all stocked" | `StoreDashboardScreen.tsx:370` |
| "0 categories" (should be collections) | `StoreDashboardScreen.tsx` quick-link grid (~:380) |
| Storefront subtitle duplicates buyer preview | `StoreDashboardScreen.tsx:397` |
| Shipping/Returns inert locked cards | `StoreDashboardScreen.tsx:408,415` |

---

## 2. Decision: build on the dark `/api/business-os/*` surface

**Recommendation: canonical business-os surface, strangler pattern over pulse.**

Rationale: the mission's hard rules — server-authoritative money/inventory/states,
audit on every mutation, idempotent financial writes, honest state models — are
already implemented on the business-os side and structurally absent from the pulse
side. Extending pulse means re-building the ledger, audit, and idempotency machinery
the dark surface already has, then migrating anyway.

Consequences to manage:

- **Migration, not big-bang.** The app ships against pulse today. Each mobile domain
  (dashboard → products → orders → inbox → money) flips to business-os behind its
  existing `EXPO_PUBLIC_*` flag, with a data backfill from pulse marketplace listings
  into `business_os_store_products` / `business_os_mkt_products`.
- **Flag rollout is env-var based** (`os.environ`, per-service). Enable per-surface on
  Railway staging first; when a flag is off the surface is fully dark, so partial
  enablement is per-vertical, not per-route.
- **State models must be extended before the app depends on them** — see Phase 0.

---

## 3. Phased build plan

Ordering follows the spec's Section 20 but re-baselined against what exists.
Every phase ends green: `npm run verify` (mobile), pytest + protection suite (backend),
and the realtime-audio gate (`scripts/realtime_audio_change_gate.py`) since `bot.py`
edits are content-gated.

### Phase 0 — Foundation (backend, ~all in `services/business_os/`)

The dark surface's state machines are narrower than the spec's Section 15. Close that
first, since everything downstream renders these states.

1. **Extend listing states** in `store/service.py` (now: draft/active/archived) and
   `marketplace/service.py` (now: draft/active/paused/archived) to the spec set:
   `+ incomplete, pending_review, scheduled, out_of_stock, suppressed, rejected,
   expired, removed`. Migrations are hand-rolled — must be idempotent
   (see `bot.init_db()` pattern).
2. **Extend store states** (now: draft/published/suspended/archived) with
   `setup_incomplete, ready, open, paused, vacation, restricted, closed`.
3. **Extend order states** (now: created/paid/fulfilled/completed/cancelled/refunded)
   with the spec's pickup/shipping/return/dispute states.
4. **Close the authorization gap**: store publish path must consult seller approval
   (`business_os_mkt_sellers.status`) and a new category-restriction table.
5. **Inventory reservations**: new `business_os_inventory_reservations` table +
   reserve-on-checkout / commit-on-capture / release-on-expiry, reusing the existing
   idempotency-key pattern. Add adjustment records (reason enum, before/after, actor)
   to the existing audit helper.
6. **Fix the unread-badge domain predicate** so commerce notifications stop inflating
   the social badge (the "99+" fix is a backend query change first).

### Phase 1 — Store dashboard truth pass (mobile, small diffs)

Cheapest visible wins; most are one-line label/wiring fixes in
`StoreDashboardScreen.tsx` + `storeDashboard.ts`:

1. Enable/ship the `EXPO_PUBLIC_STORE_READINESS` ladder (already built) → kills
   "Open for orders" over an empty catalog.
2. "0 items · all stocked" → "No inventory yet" when `allRows.length === 0`.
3. "N categories" → "N collections" (and back it with real collections once Phase 5).
4. Storefront card subtitle → "Design and publish your storefront"; keep buyer
   preview as the separate footer action.
5. Shipping/Returns cards: route to new lightweight **Shipping Setup** and
   **Return Settings** screens (origin, handling time, flat rates, return window —
   the spec's "available_now" subset) instead of inert disabled tiles. Label-gated
   features stay gated.
6. Distinguish loading / zero / unavailable in metric cards per Section 15 display
   rules (the derivation layer already separates these; render them).

### Phase 2 — Add-listing engine (mobile + backend)

Replace the composer gateway with the canonical multi-step engine (spec Section 5),
built as one flow with two entry presets (Store full / Marketplace short — spec's
final distinction):

- Steps: category+eligibility check first (fail fast per spec), identity, media
  (real multi-upload with per-file retry, reusing the existing R2 upload path),
  attributes, variants, offer, fulfillment, inventory, channels, compliance,
  preview, publish/draft/schedule.
- Backend: draft persistence + review queue on business-os products; barcode and
  catalog search deferred to Phase 2b (needs the catalog, below).
- **Catalog (2b):** new shared `business_os_catalog_products` table keyed by
  GTIN/UPC/EAN with per-seller offers referencing it. This is a large schema
  decision — write an ADR (docs/business_os/adr/) before building.

### Phase 3 — Product management

Manage Products screen with the full tab set (all listing states), row actions,
bulk actions, listing-quality detection (server-computed, returned with the listing
row). Store Management Hub screen aggregating action center + health + navigation
(spec Section 2) — this replaces the inline "Manage all" expansion.

### Phase 4 — Inventory

Inventory Overview tabs over the reservation-aware model, adjustment flow with
reason codes, history view reading the audit table. Locations/transfers last.

### Phase 5 — Collections & storefront

Business-os collections + storefront already have schema and public projection.
Add: rule-based collections, storefront draft-vs-published versioning (new version
table; current model is status-only), buyer preview as a dedicated route that renders
the public projection with a preview banner and no-op commerce actions.

### Phase 6 — Commerce operations

- Flip Commerce Inbox to fully server-joined threads (close the 11
  `INBOX_MOCK_DATA_GAPS`): backend join of conversations ↔ listing/order/return.
- Orders manager against business-os order states; returns workflow
  (authorize/decline/refund partial/full) on top of the existing refunds module.
- **Offers system** (backend from scratch): offers table, states
  (needs_response/countered/accepted/declined/expired/withdrawn/converted),
  accept → reservation → protected checkout session with expiry. Enables the
  Marketplace Selling metrics and flips `MARKETPLACE_OFFERS_ENABLED`.

### Phase 7 — Reporting & finance

Reports screens over the ledger (freshness timestamps, zero-vs-unavailable), payout
states (pending/available/held/paid) from the ledger only. The money bugs are fixed;
add reconciliation tests to the protection suite so they stay fixed.

### Phase 8 — Marketplace Selling completion

Seller reputation (verified-transaction ratings only), meetup safety setup
(approximate-location privacy rules), sold history with payout linkage — all
consuming infra from Phases 0–7.

### Phase 9 — Hardening

Accessibility (Dynamic Type on all store cards — spec Section 18), device matrix,
multi-seller/multi-buyer end-to-end on staging with business-os flags on, then
production flag rollout per-vertical.

---

## 4. Risks & rules

- **Dirty emergency branch.** Do not start Store OS work on
  `codex/emergency-live-audio-recovery`. Branch from main.
- **`bot.py` audio gate** triggers on diff *content* — new business-os route
  registrations in bot.py must avoid audio-pattern lines; run the gate locally
  before every push.
- **No migration framework.** Every schema change idempotent; test against both
  SQLite and Postgres (the ledger overdraft bug was a SQLite/Postgres divergence —
  the exact class of bug hand-rolled DDL invites).
- **Mock-gap constants are length-locked by tests.** Closing a gap means updating
  `MOCK_DATA_TABLES.md` and the constant together, or CI fails.
- **Optional route packs register inside `except Exception`** — a broken business-os
  registration disappears silently. Check boot logs on every deploy during rollout.
- **Financial actions**: all payment/refund/payout mutations keep the existing
  idempotency-key pattern; the client never computes balances (spec Section 16 —
  already the ledger's contract).

## 4b. Progress log

**2026-08-05 — Phase 1 started (working-tree edits, uncommitted):**

- `StoreDashboardScreen.tsx`: Inventory tile now says "No inventory yet" at zero
  items (was "0 items · all stocked"); Collections tile renamed **Categories**
  with a "No categories yet" zero state — it counts listing categories, and the
  spec's "collections" wording waits for the real Phase 5 feature.
- `eas.json`: `EXPO_PUBLIC_STORE_READINESS=1` enabled for **development,
  development-simulator, and preview** profiles — ships the existing honest
  status ladder (kills "Open for orders" over an empty catalog). Production
  stays off pending preview QA.
- Deliberately deferred: Storefront tile subtitle (needs a Storefront Manager
  to point at first), Shipping/Returns tiles (need backend settings routes).
- **Verification pending**: `npm run verify` must run locally/CI — the agent
  sandbox caps processes at ~45s. Flag-off paths are unchanged; no pinned test
  strings were altered (grep-verified).

**2026-08-05/06 — Offers engine (canonical, dark) shipped + tested:**

- NEW `services/business_os/marketplace/offers.py`: negotiation state machine
  (needs_response / countered / accepted / declined / expired / withdrawn /
  converted), verb-based transitions, turn-taking enforcement, per-unit
  `amount_cents`, duplicate-open-offer refusal, self-offer refusal, account-hold
  gate, full `business_os_mkt_audit` + offer-event trail. **Accept moves NO
  money** — it takes a hard inventory hold (guarded atomic decrement, same SQL
  shape as `pay_order`) backed by `business_os_mkt_offer_reservations` with an
  expiry. `convert_offer` consumes the reservation and creates a CANONICAL
  `business_os_mkt_orders` row at the agreed price; payment then rides the one
  existing engine (`orders.pay_order` → shared ledger escrow). Expiry: sweep
  (`expire_offers`) + expire-on-touch. All gated by `BUSINESS_OS_MARKETPLACE`
  (dark by default). Additive only: new tables, no edits to schema/orders/money.
- NEW `tests/business_os/test_offers_core.py`: **11/11 passing in-sandbox**
  (system python3, standalone runner, pytest-compatible), including proofs that
  acceptance creates zero ledger transactions and that the full
  offer→counter→accept→convert→pay path settles 850×2=1700 into escrow at the
  agreed (not list) price. Existing suites re-run green: orders_core 10/10,
  marketplace_core 9/9, refund_idempotency 14/14.
- **Coexistence note (concurrent agent)**: another agent has since added
  `services/marketplace_offers_routes.py` — a LEGACY-surface offers pack
  (`/api/pulse/marketplace/offers`, table `marketplace_offers`, counters as
  chained rows, expiry computed at read time, checkout via
  `seller_transactions`/Stripe Connect) mirroring
  `mobile-native/src/api/marketplaceOffers.ts`. Zero file/table/route overlap
  with the canonical module (which is dark and unregistered). This is exactly
  the strangler shape §3 planned for: legacy serves the app today; the
  canonical, ledger-integrated engine is the migration target. RECONCILIATION
  TASK for the migration phase: map legacy states (open→needs_response,
  `counter_of` chains→countered turns), port the 24h checkout-window semantics
  onto `business_os_mkt_offer_reservations.expires_at`, and cut the app's
  `marketplaceOffers.ts` over per-domain behind its flag.
- Deferred on purpose: bot.py route registration for the canonical offers
  module (bot.py is dirty on the emergency branch + content-gated; register a
  blueprint when the repo is quiet).

**2026-08-06 — Shipping/Returns settings backend shipped + tested:**

- NEW `services/business_os/store/policies.py`: the backend the dashboard's
  inert Shipping and Returns tiles never had. Shipping profiles
  (`business_os_store_shipping_profiles`: flat/free rates in integer cents,
  regions, delivery window; first active profile auto-defaults; the default
  cannot be archived while default — 409) and return policy
  (`business_os_store_return_policy`: one per business; accepted-without-window
  refused; fee in bps 0..10000; absent policy is `None`, never a fabricated
  default). `policies_summary()` is the dashboard projection with honest
  `configured` booleans so the tiles render "Not set up" instead of fake
  zeros. Gated by `BUSINESS_OS_STORE`, S1 RBAC (`store.read`/`store.manage`),
  account-hold gate, `business_os_store_audit` on every mutation. Additive
  only.
- NEW `tests/business_os/test_store_policies_core.py`: **8/8 passing
  in-sandbox**. Existing suites re-run green: store_core 14/14,
  store_seller_eligibility 19/19.
- Unblocks the Phase 1 deferred item: the mobile Shipping/Returns tiles can now
  point at real settings once routes are registered (same bot.py deferral as
  offers).

**2026-08-06 — Offers HTTP controller shipped + tested:**

- NEW `services/business_os/marketplace/offers_api.py`: framework-agnostic
  `(status, body)` controller over the offers engine, same contract as
  `marketplace/api.py` (dark 404 when flag off, field allowlists → 400
  `unknown_field`, verb dispatch → 400 `bad_action`, curated errors only).
  Intended mount documented in the module docstring
  (`/api/business-os/marketplace/offers*` + admin expiry sweep). Registration
  in bot.py stays a thin adapter + one `offers.ensure_schema()` call.
- NEW `tests/business_os/test_offers_api.py`: **4/4 passing in-sandbox**
  (dark, rejection paths, full verb flow with converted order_id surfaced,
  stranger 404, repeatable sweep).
- NEW `services/business_os/store/policies_api.py`: matching controller for the
  shipping/returns settings (`/api/business-os/store/<biz>/policies`,
  `/shipping-profiles*`, `/return-policy`; contract of `store/api.py`, loud
  unknown-field rejection). NEW `tests/business_os/test_store_policies_api.py`:
  **3/3 passing in-sandbox**. Both controllers are mount-ready: bot.py
  registration is a thin adapter + one `ensure_schema()` call per pack.

**2026-08-06 — Returns workflow (canonical, dark) shipped + tested (Phase 6):**

- NEW `services/business_os/marketplace/returns.py`: buyer-initiated return
  lifecycle (requested → approved/declined/cancelled → received →
  refunded/closed), verb-mapped, one open return per order, line-item
  validation, account-hold gate, event + `business_os_mkt_audit` trails.
  Distinct from disputes (admin-resolved) — this is the seller-operated
  merchandise flow. **Moves no money itself**: the refund verb calls the ONE
  governed primitive `refunds.refund_order` keyed `return:{return_id}`
  (at-most-once per return under retries, same derived-key shape as dispute
  resolution). Escrow physics honest: refund on a COMPLETED order surfaces the
  engine's 409 and the return is closed without money via `close_return`.
  Additive only — new tables, no edits to refunds/orders.
- NEW `services/business_os/marketplace/returns_api.py`: mount-ready
  `(status, body)` controller (`/api/business-os/marketplace/returns*`), same
  contract as offers_api (dark 404, unknown_field/bad_action/bad_role 400s,
  wrong-party verbs → 404 role-not-leaked).
- NEW `tests/business_os/test_returns_core.py` **5/5** and
  `tests/business_os/test_returns_api.py` **3/3** in-sandbox, incl. proofs that
  the full flow zeroes escrow at the refunded amount, the refund replays (not
  duplicates) on retry, and the completed-order path refuses money honestly.
  Re-run green: offers 11/11+4/4, policies 8/8+3/3, marketplace_core 9/9,
  orders_core 10/10, store_core 14/14, refund_idempotency 14/14,
  messages_core 12/12, seller_eligibility 19/19.
- Coexistence: another agent's `services/marketplace_returns_routes.py` is a
  LEGACY-surface pack (`marketplace_returns` tables, message/escalate verbs,
  integer ids). Zero file/table/route overlap with this canonical module —
  same strangler shape as offers; reconcile at migration time.
- Same bot.py registration deferral as offers/policies.

**2026-08-06 — Inventory adjustments + overview (canonical, dark) shipped +
tested (Phase 0 #5 adjustment records + Phase 4 backend):**

- NEW `services/business_os/marketplace/inventory.py`: governed inventory
  mutations with a REQUIRED reason enum (recount/found/damaged/lost/
  returned_to_stock/correction), exactly-one-of delta|set_qty, append-only
  `business_os_mkt_inventory_adjustments` records (before/after/actor/note) +
  `business_os_mkt_audit` rows. Relative deltas are guarded-atomic (same SQL
  shape as `pay_order` — cannot race below zero, loser gets 409); unlimited
  (NULL) inventory refuses deltas honestly (409 `unlimited_inventory`) but a
  recount can start finite tracking. `inventory_overview()` is the Inventory
  Overview tabs' projection: honest buckets (in_stock/low_stock/out_of_stock/
  unlimited), per-product `held_qty` joined from ACTIVE offer reservations
  (reservation-aware, answers "where did my stock go"), empty catalog = empty
  list. Existing `service.set_inventory` and the two money/hold decrement
  paths untouched — additive only.
- NEW `services/business_os/marketplace/inventory_api.py`: mount-ready
  controller (`/api/business-os/marketplace/inventory*`), same contract as
  sibling controllers.
- NEW `tests/business_os/test_inventory_core.py` **4/4** and
  `tests/business_os/test_inventory_api.py` **2/2** in-sandbox, incl. the
  below-zero race guard, foreign-product 404s, and the accepted-offer hold
  surfacing as `held_qty` with the decremented on-hand count.
- Same bot.py registration deferral as the other packs.

**2026-08-06 — Seller dashboard projections + reconciliation groundwork:**

- NEW `services/business_os/marketplace/seller_dashboard.py`: read-only
  projections for the Store Management Hub action center (Phase 3) and seller
  home tiles. `action_center()` — five real queues (to_fulfill,
  returns_to_answer, returns_received, offers_to_answer via turn-taking,
  open_disputes) with capped previews; a subsystem missing from a deployment
  reports `count: None` (UNAVAILABLE), never a fake zero — the Section 15
  zero-vs-unavailable rule enforced at the data layer. `sales_summary()` —
  order-state counts + gross/refunded sums off the order rows and
  `payable_cents` straight off the shared ledger (client never computes
  balances). NEW `tests/business_os/test_seller_dashboard_core.py` **4/4**
  in-sandbox, incl. fill-and-drain of every queue and payable reconciling to
  `seller_net_cents` after completion.
- NEW `docs/business_os/COMMERCE_RECONCILIATION.md`: the migration-phase
  mapping doc for task "reconcile legacy vs canonical" — offers AND returns
  state/verb/table mappings, money invariants during backfill, and a 5-step
  reversible cutover sequence. No code; zero collisions with the concurrent
  agents' legacy packs.

**2026-08-06 — Add-listing engine backend, reputation, dashboard controller,
catalog ADR:**

- NEW `services/business_os/marketplace/listing_drafts.py` (Phase 2 backend):
  server-side draft persistence for the multi-step composer. Eligibility
  checked FIRST (unapproved/held sellers cannot even start — spec's fail-fast
  rule); per-section writes with field allowlists and write-time validation;
  server-computed `completeness` checklist the client renders verbatim
  (incl. the conditional rule that a PHYSICAL listing needs inventory before
  publish — the checklist says so up front instead of letting publish fail);
  publish routes through the ONE catalog engine
  (`create_product` + `transition_product('publish')`) so every catalog
  invariant applies unchanged; publish-at-most-once (409), incomplete 409
  names every gap, discarded drafts refuse everything, foreign drafts 404.
  NEW `listing_drafts_api.py` mount-ready controller
  (`/api/business-os/marketplace/listing-drafts*`). Tests:
  `test_listing_drafts_core.py` **5/5**, `test_listing_drafts_api.py` **2/2**.
- NEW `services/business_os/marketplace/reputation.py` (Phase 8):
  verified-transaction ratings ONLY — structurally: a rating requires the
  order row, must be written by that order's buyer, and only once the order
  reached fulfilled/completed/refunded (a refunded order is still a real —
  possibly negative — experience). One rating per order (UNIQUE, append-only),
  honest empty state (`average: None`, never fake stars), public listing
  never exposes buyer identity, audit per rating.
  `test_reputation_core.py` **3/3**.
- NEW `services/business_os/marketplace/seller_dashboard_api.py`: mount-ready
  read-only controller over `action_center`/`sales_summary`
  (`/api/business-os/marketplace/seller/*`); zero-vs-unavailable semantics
  pass through untouched. `test_seller_dashboard_api.py` **2/2**.
- NEW `docs/business_os/adr/ADR-001-shared-catalog.md` (Phase 2b): the
  required decision doc before any catalog schema lands — catalog as
  identity (GTIN-keyed `business_os_catalog_products`), per-seller rows stay
  the offers; GTIN-only merging, append-mostly with merge-by-pointer,
  privacy rules (no cross-seller price leaks), additive migration posture.
  No code yet, per plan.
- Full verification: all **19** business_os suites green in-sandbox (12 new
  + 7 pre-existing incl. refund_idempotency, messages, eligibility);
  `git status` confirms strictly additive work, zero overlap with concurrent
  agents' files. Same bot.py registration deferral as all packs (now six
  mount-ready controllers: offers, policies, returns, inventory,
  seller_dashboard, listing_drafts).

**2026-08-06 — Reports (Phase 7) + Commerce Inbox join (Phase 6) shipped:**

- NEW `services/business_os/marketplace/reports.py` + `reports_api.py`
  (Phase 7): read-only, ledger-backed. `finance_report()` — money BY STATE:
  gross/refunded/fees off order rows, `in_escrow_cents` summed from live
  per-order escrow ledger balances, `payable_cents` off the shared ledger,
  and `paid_out_cents` honestly **None** (disbursement is provider-side —
  never a fabricated figure); every report carries `generated_at` freshness.
  `sales_by_day()` — UTC-day grouping with validated YYYY-MM-DD bounds.
  Tests: `test_reports_core.py` **4/4** (full-lifecycle reconciliation:
  capture→escrow, complete→payable+fees, return-refund reduces consistently),
  `test_reports_api.py` **2/2**.
- NEW `services/business_os/messages/commerce_links.py` (Phase 6, the
  server-side join behind the 11 INBOX_MOCK_DATA_GAPS): one link table
  mapping canonical business threads to canonical commerce objects
  (order/return/offer/product). Linking is double-gated — thread WRITE
  access AND party status on the object (someone else's order answers 404,
  existence not leaked); idempotent per (thread, type, ref).
  `thread_context()` returns CURATED projections only (status/money ids —
  no buyer identity re-exposure); a subsystem missing from the deployment is
  409 `unavailable` on link and `context: None` on read — never silent,
  never faked. pulse_* and marketplace tables untouched.
  `test_commerce_links_core.py` **4/4**.
- Full re-verification: all **22** business_os suites green in-sandbox;
  `git status` strictly additive, zero overlap with concurrent agents.
  Seven mount-ready controller packs now await the bot.py registration
  window (offers, policies, returns, inventory, seller_dashboard,
  listing_drafts, reports).

**2026-08-06 — Storefront versioning (Phase 5) shipped:**

- NEW `services/business_os/store/versions.py` + `versions_api.py`: the
  storefront row becomes the WORKING DRAFT; an append-only
  `business_os_store_storefront_versions` table holds immutable published
  snapshots (exactly one live per business). `publish_version()` freezes the
  draft (unchanged re-publish returns the live row flagged `unchanged` — no
  junk history); `restore_version()` never edits history — it creates a NEW
  live version from an old snapshot and rewrites the draft to match, so
  publish-after-restore is a no-op instead of a silent revert;
  `draft_status()` names the exact unpublished fields. The shopper read
  (`published_storefront` / `GET .../storefront/published`) serves the live
  SNAPSHOT only — draft edits never leak — and still honors lifecycle status
  (suspend = dark) and live seller approval (revocation immediate). Existing
  storefront/product/collection tables untouched; RBAC unchanged
  (`store.read` / `store.publish`); flag `BUSINESS_OS_STORE`.
  Tests: `test_storefront_versions_core.py` **5/5**,
  `test_storefront_versions_api.py` **2/2**.
- Full re-verification: all **24** business_os suites green in-sandbox;
  `git status` strictly additive, zero overlap with concurrent agents.
  Controller packs awaiting the bot.py window now number EIGHT
  (+ store versions).

**2026-08-06 — Commerce web gateway shipped (web-parity mission bridge):**

- NEW `services/business_os/commerce_gateway.py`: framework-agnostic route
  table (**37 routes**) exposing all eight mount-ready controller packs —
  offers, returns, inventory, listing drafts, seller dashboard, reports,
  store policies, storefront versions — under `/api/business-os/*`. One
  `ROUTES` tuple declares method/rule/auth tier (`public` | `user` |
  `admin`) per endpoint; `dispatch()` maps HTTP inputs onto each
  controller's signature; `ensure_schemas()` is idempotent init;
  `context_from_user()` maps bot.py session rows onto engine context.
  Exactly ONE public route (shopper storefront read); the offers expiry
  sweep is admin-tier (`require_admin_api("marketplace.manage")`).
- NEW `services/business_os_commerce_routes.py`: thin Flask adapter
  mirroring `presence_routes.py` conventions (Blueprint, lazy `_bot()`,
  `register(app)`). Zero business logic. **Mount line for the bot.py
  owner** (next to the other packs, ~line 1240):
  `_load_route_pack("business_os_commerce", "services.business_os_commerce_routes")`
  Safe to mount anytime — every controller is DARK (404) while its
  `BUSINESS_OS_*` flag is off (proven by the dark sweep test).
- Tests: `test_commerce_gateway.py` **5/5** (table sanity, dark sweep over
  all 37 routes, end-to-end versions+policies+inventory flow through
  `dispatch`, RBAC/allowlist still bite, context mapping, AST audit of the
  Flask adapter — sandbox has no Flask). Regression: 12 neighbouring
  business_os suites re-run green; git state strictly additive.

**2026-08-06 — Seller Commerce Console page shipped (web face of the gateway):**

- NEW `templates/business_os_commerce.html` + page route
  `GET /business-os/commerce` (in the same route pack; login-required, same
  idiom as `/business-os`). Eight sections — Dashboard, Offers, Returns,
  Inventory, Listing Drafts, Reports, Storefront Versions, Policies —
  every read card and every form (per-verb) wired to a real gateway route.
  Business-scoped sections take a business ID (bar at the top); PATCH/PUT
  supported via `data-method`; section JSON editor for the guided listing
  flow. Styling consumes `pulsesoc-tokens.css` (parity milestone 1) and
  mirrors the concurrent agent's `/business-os` shell.
- Route pack hardened: default-deny CSRF gate on all cookie-authenticated
  POST/PATCH/PUT (`X-CSRF-Token` header ≡ session token via
  `hmac.compare_digest`; native bearer requests exempt via
  `g.mobile_access_user_id`, mirroring `pulse_ads_verify_write`).
- Tests: `test_commerce_console_page.py` **4/4** — NO DEAD BUTTONS proven
  statically: every `data-endpoint`/form target (verbs expanded from each
  form's `<select>`) must match a gateway route with the right method; no
  admin-tier route on the user page; CSRF header + gate asserted.
  `test_commerce_gateway.py` re-run **5/5** after the adapter change.

## 5. Definition of done (delta from spec Section 21)

The spec's seller journey stands. Additional repo-specific gates:

- All 71 mock-data gaps either closed or explicitly re-declared with a phase tag.
- `BUSINESS_OS_STORE`, `BUSINESS_OS_MARKETPLACE`, `BUSINESS_OS_MESSAGES`,
  `EXPO_PUBLIC_STORE_READINESS`, `EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT` enabled in
  production; pulse marketplace surface serving reads only (or retired).
- Protection suite extended with commerce golden paths (listing publish, checkout
  reservation, refund idempotency, inbox domain separation).
- Real-device QA evidence for checkout, uploads, and push per existing policy.
