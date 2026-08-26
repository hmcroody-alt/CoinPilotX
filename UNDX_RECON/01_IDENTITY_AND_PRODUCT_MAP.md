# 01 — Identity, Roles & Complete Product Map

Read-only reconnaissance of the PulseSoc codebase (repo folder `CoinPilotX`).
Every claim below is anchored to a file path, and to `file:line` where the exact
line matters. Where a document describes something no code implements, this file
says so explicitly rather than reporting it as a feature.

STATUS vocabulary used in PART C is fixed:
`PRODUCTION READY` / `PARTIALLY READY` / `UNDER DEVELOPMENT` / `PLANNED` /
`GATED-OFF-FOR-LAUNCH` / `BROKEN` / `DEPRECATED`.

---

# PART A — IDENTITY

## A.1 What PulseSoc is, in the product's own words

The canonical self-description is not in a README. It is a **runtime constant**,
because the AI layer is required to ground its answers in it —
`services/undx_company_identity.py`.

`services/undx_company_identity.py:60-67` (`CANONICAL_PULSESOC_DEFINITION`):

> "PulseSoc is an intelligent digital ecosystem designed to connect social
> interaction, creator tools, business operations, communication, commerce,
> advertising, safety, and artificial intelligence through a shared identity and
> platform infrastructure."

The same constant continues with the sentence that governs how every other part
of this map should be read:

> "Social, marketplace, messaging, advertising, crypto, and the AI layer are
> subsystems of that broader ecosystem, not the whole of it."

Company facts, from the `COMPANY` dict at `services/undx_company_identity.py:36-51`:

| Field | Value |
|---|---|
| `legal_name` | `CoinPlotXAI Inc.` |
| `primary_product` | `PulseSoc` |
| `founder` | `Roody Cherie`, `Founder & CEO` |
| `product_category` | social platform, creator economy, business platform, marketplace, advertising platform, communications ecosystem, artificial intelligence platform |

`COMPANY_IDENTITY_REQUIRED_PHRASE = "CoinPlotXAI Inc."`
(`services/undx_company_identity.py`) — the AI layer fails closed if a generated
answer about the company omits it. `COMPANY_IDENTITY_VERSION = 1`.

Note the repo/product mismatch called out in `CLAUDE.md`: the folder is
`CoinPilotX` (the original crypto-bot product); the live product is **PulseSoc**
(pulsesoc.com). Crypto survives as a subsystem, not as the product.

### A.1.1 What the product refuses to claim about itself

`UNVERIFIABLE_WITHOUT_SOURCE` at `services/undx_company_identity.py:70-87` is a
list of things the AI layer must not assert without a source. Two entries are
directly relevant to this recon:

- "production-readiness of any specific feature"
- "Android availability"

The product's own identity module treats feature readiness as **not knowable
without evidence**. PART C therefore attaches an evidence line to every STATUS.

## A.2 Core mission and philosophy

There is no single "mission statement" file. The philosophy is expressed as
repeated, enforced invariants in the architecture docs. The four that recur:

1. **One shared identity, many surfaces.** The definition above ("through a
   shared identity and platform infrastructure") is implemented as the Page OS:
   one identity object that Marketplace, Ads, Business OS and the feed all
   *point at* rather than duplicate. `docs/pages/page_marketplace.md` and
   `docs/pages/page_advertising.md` both state that a page links to the
   canonical system and never replaces it.

2. **Never fabricate the absence or presence of data.** `docs/pages/page_modules.md:99-102`:
   a catalogue failure returns `PageError(503)`, and "A failure is never
   converted into an empty discography, which is a different and false
   statement." The same principle drives `services/presence_service.py`
   (ambiguity resolves to offline) and the crypto premium gate.

3. **Fail closed on permission, fail honest on capability.** `services/crypto_premium_gate.py`
   is a single fail-closed gate; `services/admin_gateway.py` returns a generic
   "Access denied." for every credential failure so the failure mode leaks
   nothing.

4. **Don't ship a dead end.** `docs/pages/page_modules.md:16-19` describes the
   `services` tab defect — a tab visible to owners that opened onto a blank
   screen — and the fix (`RENDERABLE_TABS`, which raises rather than records an
   unavailable module). The same instinct produced the launch readiness gate
   documented in PART C and section A.5.

## A.3 Target users and major ecosystems

Derived from the role ladder (`services/privilege_engine.py`), the page type set
(`docs/pages/page_os.md:24-26`) and the route-prefix census of `bot.py`:

- **Everyday social users** — feed, reels, stories, messaging, calls.
- **Creators and artists** — creator tools, ARTIST pages, music linkage,
  livestream eligibility, monetization.
- **Businesses and organizations** — Business OS, BUSINESS / PROFESSIONAL_SERVICE
  / LOCAL_BUSINESS pages, events, customers.
- **Sellers and buyers** — Marketplace, store dashboard, orders, payments.
- **Advertisers** — ads manager, ad accounts, ad spend.
- **Crypto users** — portfolio, watchlists, alerts, simulator (the original
  product, now a subsystem).
- **Platform staff** — admin, moderation, trust & safety, verification review.

Major ecosystems, by backend route volume in `bot.py` (~1,538 routes):
`/api/pulse` (323) > `/api/business-os` (199) > `/api/arena` (120) >
`/admin/business-os` (49) > `/api/admin` (37) > `/api/dashboard` (29), then
crypto, messages, account, mobile, reels, undx, payments.

## A.4 "Presence" — the concept

**Warning for anyone reading this codebase: "Presence" means two unrelated
things.** Conflating them produces wrong answers. They are documented separately
below.

### A.4.1 Presence (1) — the identity surface. This is the product meaning.

`docs/pages/page_os.md:3-6`:

> "Naming: the product surface is called **Presence** … The code, tables and
> routes are named `page`/`pages`."

So: **Presence is the user-facing name for Page OS.** Every table, route and
service says `page`; every screen and label says Presence. `mobile-native/src/screens/PresenceHubScreen.tsx:67-77`
states it for the client side:

> "'Presence' is PulseSoc's word for an artist, business, brand or organization
> identity controlled by one or more authorized members. Underneath it is the
> canonical Page OS (`/api/pages/*`)."

**How Presence relates to profiles.** The governing invariant is
`docs/pages/page_os.md:12-17`: **PERSON ≠ PAGE ≠ STORE.**

- A **person/profile** is a `users` row — an auth principal. It logs in.
- A **Presence/page** is a `pulse_pages` row — a *presentation actor*, not an
  auth principal. `docs/pages/page_identity.md` is explicit: a page cannot log
  in; a human acts *as* a page. `GET /api/pages/identities` returns the set of
  pages the caller may post as, and only those where their role carries the
  `post` capability.
- A **store** is a marketplace seller. A page links to one
  (`store` link `ref_id` = marketplace `seller_user_id`, surfaced as
  `shop_seller_id` — `docs/pages/page_modules.md:51-56`); it never contains one.

Structure:
- **16 page types** (`docs/pages/page_os.md:24-26`) — ARTIST, BUSINESS,
  PROFESSIONAL_SERVICE, LOCAL_BUSINESS, and others.
- **Lifecycle** `ACTIVE → PAUSED → UNPUBLISHED → DEACTIVATED`, with **no hard
  delete** (`docs/pages/page_os.md:32-34`). PAUSED stays public.
- **One enforcement point**, `_load_visible_page`, for whether a viewer may see
  a page at all.
- **16 routes** under `/api/pages/*` in `bot.py`.
- Page posts are not a second post system: they are ordinary `pulse_posts` rows
  carrying a `page_id`, authored via `POST /api/pages/:id/posts` and rendered by
  `pulse_feed_engine._page_author`.
- **Tabs/modules** are decided server-side. `module_availability()` in
  `services/pulsesoc_pages.py` classifies each tab as always-backed
  (`home`, `posts`, `about`), link-backed (`music`, `shop`, `merch`, `menu`),
  content-backed (`videos`), or link-and-flag-backed (`events`) —
  `docs/pages/page_modules.md:29-40`. `public_view` returns both `tabs` and
  `modules`, and "The client renders `tabs` as delivered and never widens the
  set" (`docs/pages/page_modules.md:76-78`).
- **A link is a pointer, singular per kind** (`docs/pages/page_modules.md:154-171`):
  `set_link` is set, not add — it deletes rows for `(page_id, link_type)` and
  writes one, because every reader takes `[0]`.

Mobile surface: `mobile-native/src/screens/PresenceHubScreen.tsx`, route
`Presence` (`AppNavigator` :547) and `PagesHub` (:546), plus `PageScreen.tsx`,
`PageConnectionsScreen`.

### A.4.2 Presence (2) — online / last-seen presence. A different subsystem.

`docs/mission8_presence_evidence.md` documents a server-authoritative liveness
system with no relationship to Page OS beyond the word.

Key property: **liveness is derived at read time and never stored.** There is no
`is_online` column and no reaper process. `services/presence_service.py` (927
lines) exposes `connect / heartbeat / set_activity / disconnect / disconnect_all /
presence_for / presence_of / is_online / active_sessions / set_privacy /
get_privacy / format_last_seen / sweep / health_snapshot`, with 9 HTTP endpoints
in `services/presence_routes.py` under `/api/pulse/presence/*`.

Timings: 45s heartbeat, 90s grace, 300s → away, 12s transient TTL. Activities
split into transient (`typing`, `recording_voice`, `uploading_media`,
`sending_files`) and session-bound (`in_audio_call`, `in_video_call`,
`live_hosting`, `live_guest`, `live_watching`). **Ambiguity always resolves to
offline** — mirrored client-side in `mobile-native/src/api/presence.ts`, where
`PresenceStatus = "online" | "away" | "offline"` and every normaliser defaults to
`offline`.

## A.5 The launch readiness gate (the "gated for launch" mechanism)

The most recent commit — `feat(launch): gate unfinished Business and Presence
features` — introduced `mobile-native/src/launch/`. This is **not** the env-flag
system; it is a separate, smaller mechanism, and the two must not be confused.

`mobile-native/src/launch/readiness.ts` is a single frozen **deny-list table**.
Its header states it "is not a feature flag system and it does not delete
anything… An id that is not in the table is READY."

```ts
export type ReadinessState = "READY" | "BUILDING" | "COMING_SOON";

export const LAUNCH_READINESS: Readonly<Record<LaunchModuleId, ReadinessState>> =
  Object.freeze({
    "business:events":     "BUILDING",
    "business:customers":  "COMING_SOON",
    "business:team":       "COMING_SOON",
    "presence:businessOs": "BUILDING",
  });

export const GATED_ROUTES: Readonly<Record<string, LaunchModuleId>> =
  Object.freeze({ BusinessOsEvents: "business:events" });
```
(`mobile-native/src/launch/readiness.ts:54-97`)

**Exactly four modules are gated off for launch.** Nothing else is.

Why each one is gated — the evidence recorded alongside the table:

- **`business:events` (BUILDING)** — `EventsManagerScreen` → `loadEventsModel` →
  `listScheduledLiveEvents` → `listLiveNow` → `GET /api/pulse/live-now`. That
  response has no `scheduled` or `events` key, and `pulse_live_now_cards`
  excludes scheduled rows at the SQL level. The three tabs can only ever produce
  `[]`.
- **`business:customers` (COMING_SOON)** — no screen, no route, no
  `/api/pulse/*` endpoint. Previously hidden by `backed: false` in
  `mobile-native/src/api/businessOs.ts:197`.
- **`business:team` (COMING_SOON)** — same, `businessOs.ts:240`.
- **`presence:businessOs` (BUILDING)** — `PresenceHubScreen.tsx:305-311`
  navigates to `BusinessOs` with `{ title: page.name }` and **no page id**, so
  `resolveRouteProfileContext(undefined, viewer)` sets `isOwnProfile = true`.
  Result: the presence's name in the header, the *viewer's own* listings, orders
  and ad spend in the body. The `BusinessOs` route itself remains READY; only
  this entry point is gated.

Enforcement is two-layered:
- **At the tap** — `useLaunchGate()` in `mobile-native/src/launch/useLaunchGate.tsx`.
  `open(id, label, run)` executes the navigation only if `isLaunchReady(id)`;
  otherwise it sets a ComingSoon target and calls
  `AccessibilityInfo.announceForAccessibility`.
- **At the route** — `mobile-native/src/screens/EventsRoute.tsx`:
  `if (routeReadiness("BusinessOsEvents") !== "READY") return <ComingSoonScreen … />`.
  Its comment notes the card-level check "is a convention, not a gate".

Copy discipline: `useLaunchCopy()` maps `BUILDING → commerce:launch.statusBuilding`,
otherwise `statusComingSoon`. It never says "unavailable" or "not implemented".
`mobile-native/src/launch/LaunchTile.tsx` keeps a gated tile the same size, icon
and position, adds a teal edge/halo/drift plus a text badge, and deliberately
does **not** set `accessibilityState.disabled`.

## A.6 The second gating system — build-time env flags

Distinct from the launch gate and much larger. `docs/business_os/FLAG_REGISTRY.md`
documents **24 `EXPO_PUBLIC_*` flags, all default off**, read through one helper,
`envFlagOn` in `mobile-native/src/core/envFlag.ts` (accepts `1 true on yes`).

These are the reason several areas in PART C are `PARTIALLY READY` rather than
`PRODUCTION READY`. The consequential ones:

- **Payments (6 flags off)** — `PAYOUT_INITIATION`, `INSTANT_PAYOUT`,
  `STATEMENTS`, `TAX_DOCUMENTS`, `ESCROW`, `AD_TOPUP`. The registry states:
  "No endpoint initiates a payout anywhere in the codebase."
- **Orders (2 off)** — `ORDERS_ESCROW`, `ORDERS_FULFILLMENT`; fulfil/complete
  transitions 404 in production.
- **Ads** — `ADS_POST_MODE` off.
- **Events (3 off)** — `EVENTS_LIVE_STATS`, `EVENTS_ATTRIBUTION`, `EVENTS_MOCK`.
- **App-level** — `EXPO_PUBLIC_DIGITAL_COMMERCE_ENABLED` off (Apple guideline
  3.1.1; StoreKit not implemented for those paths);
  `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED` off (no pods, no VoIP cert).
- Others off: `STORE_READINESS`, `MARKETPLACE_LOCATION_HONESTY`,
  `INSIGHTS_ERROR_CAUSES`, `SCOPED_BADGES`, and 6 Messages flags.

**Hard-coded `false` constants that are not flags** (cannot be switched on at
runtime): `HUB_LIVE_CARDS` at `mobile-native/src/api/businessOs.ts:117`;
`MARKETPLACE_OFFERS_ENABLED`, `MARKETPLACE_CART_ENABLED`,
`MARKETPLACE_BOOST_ENABLED` at `mobile-native/src/api/marketplaceOffers.ts:80-103`.

Three flags were deleted rather than shipped: `EXPO_PUBLIC_MESSAGES_REALTIME`,
`EXPO_PUBLIC_ACCOUNT_NAME_FIRST`, `EXPO_PUBLIC_STATE_LANGUAGE`.

---

# PART B — ROLES & PERMISSIONS

There is no single role system. There are **four independent ones**, and a given
human can hold a position in all four simultaneously:

| System | Storage | Authority module |
|---|---|---|
| **Account / privilege ladder** | `users` columns + trust score | `services/privilege_engine.py` |
| **Premium entitlements** | `business_os_ent_grants` (canonical) + legacy tables | `services/business_os/entitlements/premium.py` |
| **Page (Presence) roles** | `pulse_page_members` | `PERMISSIONS` matrix in `services/pulsesoc_pages.py` |
| **Admin RBAC** | `admin_users`, `role_permissions`, `admin_role_permissions`, `admin_user_roles` | `bot.py:17409-17530` |

Where a role below is a *decision record* rather than shipped code, that is
stated explicitly.

## B.1 Free user

- **Stored:** the `users` row itself. `bot.py:815` creates a minimal `users`
  table; `bot.py:104936-105035` calls `add_columns_if_missing(cur, "users", [...])`
  with ~100 columns. The relevant defaults are the *absence* of `is_pro`,
  `plan`, `premium_status`.
- **Granted:** by signup. `bot.py:4412` `load_account_by_id` →
  `SELECT * FROM users WHERE user_id=?`; `bot.py:4914` `require_account()` is
  the session gate.
- **Unlocks:** `PRIVILEGE_LEVELS` "Member" in `services/privilege_engine.py` —
  feed, posting, messaging, marketplace browsing.
- **Enforced:** `services/privilege_engine.py` `get_user_privileges(...)`, which
  returns ~25 `can_*` booleans. Free crypto users get 5 basic alert rules
  (`PREMIUM_CRYPTO_INTELLIGENCE_FINAL_REPORT.md`).

## B.2 Premium user

- **Stored:** four places at once, and the codebase says so. `services/business_os/entitlements/premium.py`
  names the four authorities explicitly: **(A)** legacy tables
  (`user_entitlements`, `premium_entitlements`), **(B)** canonical
  `business_os_ent_grants`, **(C)** `users` identity columns (`is_pro`, `plan`,
  `subscription_plan/status/expires_at`, `premium_status`, `premium_expires_at`,
  `lifetime_premium` — `bot.py:104936-105035`), **(D)** the session flag
  `has_premium_access`.
- **Granted:** purchase via the unified StoreKit/Stripe router
  (`PULSESOC_STOREKIT_STRIPE_UNIFIED_PAYMENTS_FINAL_REPORT.md`), or admin grant.
- **Unlocks:** `PREMIUM_ACCESS = "premium.access"` plus `PREMIUM_CAPABILITIES` —
  `premium.profile.customization`, `premium.media.higher_quality`,
  `premium.undx.advanced`, `premium.crypto.advanced_alerts`,
  `premium.crypto.portfolio_intelligence`.
- **Enforced:** `resolve()` in `services/business_os/entitlements/premium.py`,
  whose behaviour mirrors the `BUSINESS_OS_ENTITLEMENTS` mode
  (`off` / `shadow` / `canonical`) — an acknowledged split-brain during
  migration. An **account hold beats any paid grant**.
  `services/pro_access.py` `pro_access_type(row)` returns `"paid" | "trial" | "none"`,
  requires `account_status == "active"`, and applies
  `_STALE_EXPIRY_GRACE = timedelta(days=3)`.
  `services/crypto_premium_gate.py` is the fail-closed crypto gate — denials are
  HTTP-200 `premium_required`, never 403; owner bypass reads
  `PULSESOC_OWNER_USER_IDS`.
- **Note:** `services/premium_entitlement_service.py` declares in its own header
  that it is **no longer** the premium authority. It retains founder identity
  (`founder_memberships`, permanent founder number), `FOUNDER_PRICE_CENTS = 499`,
  `PREMIUM_VALUE_CENTS = 999`, `FOUNDER_ENTITLEMENTS` (12),
  `PREMIUM_ENTITLEMENTS` (6), and `PLAN_DEFINITIONS` (`free`, `founder_premium`,
  `premium_plus`).

## B.3 Creator

- **Stored:** derived, not a column. Trust score + verification types in `users`.
- **Granted:** `level_for_trust(score, verification_types, referral_count)` in
  `services/privilege_engine.py`. `LEVEL_THRESHOLDS` are trust-score cutoffs
  (96/82/74/66/58/50/40/28/15/0) across the ladder: Visitor, Member, Trusted
  Member, Verified User, **Creator**, Teacher, Marketplace Seller, Livestream
  Eligible, Partner Creator, Platform Ambassador, Founder/Owner.
- **Unlocks:** creator `can_*` flags; `creator_ai` capability (status **ACTIVE**)
  in `services/premium_capability_engine.py`.
- **Enforced:** `get_user_privileges()`. Livestream specifically unlocks at
  `referral_count >= 30` **or** level ≥ Livestream Eligible.
- **Partly aspirational:** `premium_capability_engine.CAPABILITIES` marks
  `creator_acceleration`, `premium_studio`, `discovery_boosts`,
  `advanced_analytics` and `livestream_prestige` as **SCAFFOLDED** — each lists
  `required_tables` / `required_routes` / `required_services` it does not yet
  have. Only `premium_identity` and `creator_ai` are ACTIVE.

## B.4 Artist

- **Stored:** as a **page type**, not a user flag. `ARTIST` is one of the 16
  types in `docs/pages/page_os.md:24-26`; documented in `docs/pages/artist_pages.md`.
- **Granted:** by creating an ARTIST Presence (`POST /api/pages`).
- **Unlocks:** the ARTIST tab ceiling — `posts / music / videos / merch / about`.
  Music is link-backed: `page_music()` reads the `music_artist` link and queries
  `services/music_service` for that artist's tracks. The presence stores a
  pointer, never a copy (`docs/pages/page_modules.md:91-102`).
- **Enforced:** `TYPE_TABS` and `module_availability()` in
  `services/pulsesoc_pages.py`; `RENDERABLE_TABS` makes an undrawable tab a
  raise rather than a silent blank.
- **Honesty rule worth noting:** with no link, `page_music()` returns
  `{"linked": false, "tracks": []}` and **does not** guess by page name — that
  would attribute a stranger's songs to this presence.

## B.5 Business

- **Stored:** page types `BUSINESS`, `PROFESSIONAL_SERVICE`, `LOCAL_BUSINESS`
  (`docs/pages/business_pages.md`), plus `business_os_business` rows with an
  `owner_user_id`.
- **Granted:** creating a business Presence, and/or a Business OS business.
- **Unlocks:** Business OS (`/api/business-os`, 199 routes), the `events` tab
  (link-and-flag-backed: needs the `business_os` link **and** `events_enabled()`).
- **Enforced:** the `business_os` link points at
  `business_os_business.owner_user_id` — **the owner only**, because
  "pointing a presence at a business is an identity claim, not an operational
  task" (`docs/pages/page_modules.md:107-110`).
- **Business roles are a documented gap, not code.**
  `docs/business_os/adr/0006-roles-and-permissions.md` (Accepted 2026-08-01)
  specifies exactly two roles — **Owner** and **Admin** — with Admin excluded
  from money actions and verification documents entirely ("Not redacted, not
  summarised — excluded"), fail-closed. The ADR itself frames this as a *spec
  gap* ("no mission described roles, so none was violated"). Treat it as a
  decision record; do not report it as shipped.

## B.6 Seller

- **Stored:** `marketplace_merchant_applications` and `marketplace_sellers`.
- **Granted:** `services/seller_lifecycle.py` — one pipeline, two front doors
  (web `/pulse/merchant/apply`, plus the native multi-step flow). States:
  `draft, submitted, under_review, information_requested, resubmitted, approved,
  rejected, withdrawn, expired, suspended`, with `LEGACY_STATUS_ALIASES` for
  older rows.
- **Unlocks:** listings, the store dashboard, orders, the page `store` link
  whose `ref_id` is the marketplace `seller_user_id`.
- **Enforced:** `apply_transition` **refuses an approval without an admin actor
  id**. `applicant_view` is a whitelist, and nothing sensitive is logged.
  `docs/business_os/adr/0004-seller-eligibility-and-entitlements.md` sets the
  policy: one entitlement source; five capabilities (operate a store, sell in
  marketplace, advertise, receive payouts, manage business identity/verification);
  four possible answers (`granted` / `not yet eligible` / `blocked` / `unknown`);
  "Environment flags stop being entitlement"; and a client may not complete a
  money action on a cached entitlement.
- Also a rung on the ladder: "Marketplace Seller" in
  `services/privilege_engine.py`.

## B.7 Buyer

- **Stored:** no distinct column. Any `users` row is a buyer.
- **Granted:** implicitly.
- **Unlocks:** browse, order, pay. Note `MARKETPLACE_CART_ENABLED` and
  `MARKETPLACE_OFFERS_ENABLED` are hard-coded `false`
  (`mobile-native/src/api/marketplaceOffers.ts:80-103`), so the buyer path is
  single-item, no cart, no offers.
- **Enforced:** ordinary session auth (`require_account()`, `bot.py:4914`).

## B.8 Advertiser

- **Stored:** ad account rows in the Business OS ads domain; on a Presence, the
  `ADVERTISING_MANAGER` page role.
- **Granted:** by holding an ad account, or by page-role assignment.
- **Unlocks:** the ads manager; the page advertising module —
  which per `docs/pages/page_advertising.md` **links to** the canonical ads
  system and never replaces it.
- **Enforced:** page-side by the `PERMISSIONS` matrix in
  `services/pulsesoc_pages.py`; `EXPO_PUBLIC_ADS_POST_MODE` is off, and
  `AD_TOPUP` is one of the six off payments flags.

## B.9 Admin

- **Stored:** `admin_users` (`bot.py:111636`) with `role TEXT DEFAULT 'admin'`
  and `status TEXT DEFAULT 'active'`; permissions in `role_permissions`,
  `admin_role_permissions`, `admin_user_roles`.
- **Granted:** by an existing owner/admin writing those tables.
- **Unlocks:** whatever permission strings the role carries.
  `ROLE_FALLBACK_PERMISSIONS` at `bot.py:17409-17435` defines **25 admin roles**:
  `owner` and `super_admin` hold `{"*"}`; the rest are `admin`,
  `department_manager`, `social_manager`, `pulse_moderator`,
  `trust_safety_agent`, `senior_moderator`, `creator_manager`, `arena_operator`,
  `roast_operator`, `alerts_operator`, `marketing_manager`, `seo_manager`,
  `monetization_manager`, `security_analyst`, `analytics_viewer`,
  `developer_ops`, `billing_manager`, `support_manager`, `support_agent`,
  `analyst`, `content_manager`, `developer`, `read_only`.
- **Enforced:** `admin_has_permission()` at `bot.py:17438-17468` — `owner`
  short-circuits to true, otherwise the DB tables union with the fallback map.
  Decorators: `require_admin_page` (`bot.py:17491`), `require_owner_api`
  (:17502), `require_owner_admin_page` (:17514),
  `require_admin_api(permission="users.view")` (:17525). **All four call
  `log_admin_audit` on denial.**
- **Pre-auth:** `services/admin_gateway.py` throttles by hashed source IP and
  hashed identifier (`RATE_LIMIT_WINDOW_MINUTES = 10`,
  `RATE_LIMIT_MAX_FAILURES = 10`, `RATE_LIMIT_MAX_IDENTIFIER_FAILURES = 6`),
  returns a generic "Access denied." for every credential failure, and holds
  `INTERNAL_NAV_LABELS` that must never leak.

## B.10 Moderator

- **Stored:** as admin roles — `pulse_moderator`, `senior_moderator`,
  `trust_safety_agent` in `ROLE_FALLBACK_PERMISSIONS` (`bot.py:17409-17435`).
- **Granted:** admin role assignment.
- **Unlocks:** moderation queues, trust & safety tooling.
- **Enforced:** `require_admin_api(<permission>)`.
- **Verification review is a separate role map:**
  `services/pulsesoc_dashboard_centers.py:40-75` defines `ADMIN_ROLES` (8
  reviewer roles), `READONLY_ADMIN_ROLES = {"support_readonly", "support_agent"}`,
  and `TRACK_ROLE_MAP` binding 8 verification tracks to specific roles, plus
  `BADGE_TYPES` (13 badges: `blue_check`, `identity_verified`,
  `creator_verified`, `business_verified`, `seller_verified`,
  `advertiser_verified`, `music_partner_verified`, `media_partner_verified`,
  `organization_verified`, `trusted_account`, `official_account`,
  `founder_badge`, `premium_badge`).

## B.11 Developer

- **Stored:** admin roles `developer` and `developer_ops`
  (`bot.py:17409-17435`).
- **Granted:** admin role assignment.
- **Unlocks:** mission control. `services/pulse_dashboard_mission_control.py:27`
  restricts to `ADMIN_ROLES = {"owner", "super_admin", "admin", "developer_ops",
  "developer"}`. Also the UNDX execution kernel surface.
- **Enforced:** that set plus `require_admin_api`. The UNDX kernel additionally
  requires the literal approval phrase `APPROVE UNDX WRITE` before writing, and
  blocks `.env`, `.git`, venv, secrets and sqlite paths
  (`undx_execution_kernel.py`, logged to `undx_execution_log.jsonl`).

## B.12 Owner

Three different owners exist; keep them apart.

- **Platform owner** — admin role `owner` / `super_admin`, permissions `{"*"}`,
  short-circuited to true in `admin_has_permission()` (`bot.py:17438-17468`).
  Separately, `PULSESOC_OWNER_USER_IDS` is the env allow-list used for the
  premium/crypto owner bypass (moved there per
  `APP_REVIEW_FINAL_GO_NO_GO_REPORT.md`).
- **Page owner** — `OWNER` in the closed page-role set
  (`docs/pages/page_roles.md`): OWNER, ADMIN, MANAGER, CONTENT_MANAGER,
  ADVERTISING_MANAGER, MARKETPLACE_MANAGER, ANALYST. `ASSIGNABLE_ROLES`
  **excludes OWNER** — you cannot hand out ownership as a role. Ownership
  transfer requires the literal phrase `TRANSFER`. `INVITE_TTL_DAYS = 7`. Nine
  capabilities in the matrix; `role_for()` vs `_seat()` distinguish effective
  role from the stored seat. `page_audit_log` is append-only.
  OWNER-only actions include `manage_status` (verification —
  `docs/pages/page_verification.md`).
- **Business owner** — `business_os_business.owner_user_id`; the only identity a
  `business_os` page link may point at.

Founder/Owner is also the top rung of `services/privilege_engine.py`, returning
all-true privileges with `max_video_duration = 600` and `max_upload_mb = 250`.

---

# PART C — COMPLETE PRODUCT MAP

32 product areas. Each STATUS carries its evidence on the line beneath it.

Scale context: `bot.py` ~111k lines / ~1,538 routes; `services/` 293 modules;
`mobile-native/src/screens/` 108 screens (+18 settings screens); `mobile-native/src/api/`
98 modules. Optional route packs register inside `except Exception` blocks, so a
404 may mean a subsystem failed to register rather than a routing bug
(`CLAUDE.md`).

---

## 1. Home Feed

NAME: Home Feed
PURPOSE: The primary social surface — ranked posts from people, communities and Presences the viewer follows.
USER ACTIONS: scroll, post, react, comment, share, follow/unfollow, report, post *as* a Presence identity.
BACKEND (service modules + route prefix): `services/pulse_feed_engine.py` (incl. `_page_author` for page-authored posts), `services/pulsesoc_pages.py`; `/api/pulse/*` (323 routes overall), `pulse_posts` table.
MOBILE SCREENS: `HomeScreen.tsx`, `PostComposerScreen`, `PostDetailScreen`, `CommentsScreen`; tab `Tabs` (`AppNavigator` :355).
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Page posts are ordinary `pulse_posts` rows with a `page_id`, so feed ranking treats a Presence and a person identically. Posting as a Presence requires the `post` capability, surfaced via `GET /api/pages/identities` (`docs/pages/page_identity.md`).
Evidence: no launch-gate entry, no off-by-default flag, screens and routes both present.

---

## 2. Reels

NAME: Reels
PURPOSE: Short-form vertical video.
USER ACTIONS: watch, swipe, like, comment, share, record and upload.
BACKEND (service modules + route prefix): `/reels` and `/api/pulse/reels/*` route family in `bot.py`; media pipeline via `media_worker.py`, Cloudflare R2 (boto3).
MOBILE SCREENS: `ReelsScreen.tsx`, plus the camera/record path.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Reels is one of the 21 subsystems in the protection suite (`scripts/protection/run_protection_suite.py`), and `CLAUDE.md` states static checks "don't replace device QA for livestream, push, checkout, or uploads".
Evidence: covered by the protection suite as a live subsystem; not gated, no off flag.

---

## 3. Stories / Status

NAME: Stories / Status
PURPOSE: Ephemeral posts that expire.
USER ACTIONS: view, post a story, reply to a story.
BACKEND (service modules + route prefix): `/api/pulse/stories/*` family within the `/api/pulse` route block.
MOBILE SCREENS: story rail on `HomeScreen.tsx`; story viewer / composer screens.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Expiry is a read-time/scheduled concern; the repo has no dedicated stories worker in the Procfile (Procfile runs `web`, `undx_worker`, `email_worker`, `alert_worker` — `media_worker`, `pulse_worker`, `telegram_worker` exist but are not started).
Evidence: routes and client surface both present; no gate, no flag.

---

## 4. Messaging

NAME: Messaging (Direct, Groups, Rooms, Community channels, Commerce inbox)
PURPOSE: One-to-one and group communication, plus a separate commerce inbox.
USER ACTIONS: send/receive text, media, voice; create groups and rooms; filter the inbox; message a seller.
BACKEND (service modules + route prefix): `services/pulse_communications_v2/service.py`; `/api/messages/*` and `/api/pulse/*` messaging routes. Conversation types are constrained to `direct | group | room | community_channel` at `pulse_communications_v2/service.py:20` and enforced at `:1070`. A 5-value `conversation_domain` discriminator separates social from commerce.
MOBILE SCREENS: `MessengerScreen.tsx` (inbox filters `all|direct|groups|rooms|ai|unread` at `:35`), `ConversationScreen`, `CommerceInboxScreen.tsx`, `MessagesRoute.tsx:16-18`.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: Six `EXPO_PUBLIC_MESSAGES_*` flags are off by default (`docs/business_os/FLAG_REGISTRY.md`). `EXPO_PUBLIC_MESSAGES_REALTIME` was **deleted** rather than shipped, so realtime delivery is not behind a switch you can flip. `docs/business_os_ground_truth.md` records that Commerce Inbox already exists but is among seven completed features shipping dark behind off-by-default flags.
Evidence: FLAG_REGISTRY (6 Messages flags off) + deleted realtime flag.

---

## 5. Voice messages

NAME: Voice messages
PURPOSE: Record and send audio clips inside a conversation.
USER ACTIONS: hold to record, review, send, play back.
BACKEND (service modules + route prefix): message media path under `/api/messages/*`; storage via R2.
MOBILE SCREENS: recorder control inside `ConversationScreen`.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: This is inside the **hard-locked realtime-audio perimeter**. `docs/realtime_audio_change_policy.md` and `config/realtime-audio-protected-paths.json` forbid screen-level `AVAudioSession` setup, a second microphone track, a second LiveKit/Agora publication path, or a new global audio singleton. The `expo-av` legacy allowlist is capped at six files — a seventh call site fails CI. `recording_voice` is one of the transient presence activities (12s TTL) in `services/presence_service.py`.
Evidence: protected-path policy + capped allowlist; feature works but cannot be extended without the audio gate.

---

## 6. Calls (audio & video)

NAME: Calls
PURPOSE: One-to-one and group audio/video calling.
USER ACTIONS: place, receive, mute, switch camera, hang up.
BACKEND (service modules + route prefix): **Agora RTC** (migrated off LiveKit); token/session routes under `/api/pulse/*` and `/api/messages/*`.
MOBILE SCREENS: `useAgoraCallRoom.ts`, `callSessionStore.ts`, call UI screens.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED` is **off** — the registry's reason is no CallKit pods and no VoIP push certificate, so there is no native incoming-call UI. `in_audio_call` / `in_video_call` are session-bound presence activities. The whole area is inside the realtime-audio hard lock; `CLAUDE.md` names the characteristic failure: an unrelated screen calling `Audio.setAudioModeAsync` steals the session and "production goes silent".
Evidence: `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED` off in `docs/business_os/FLAG_REGISTRY.md`.

---

## 7. Livestream

NAME: Livestream
PURPOSE: Live broadcasting with viewers, guests and chat.
USER ACTIONS: go live, invite a guest, watch, chat, end.
BACKEND (service modules + route prefix): Agora live broadcast; Mux for streaming; `GET /api/pulse/live-now` serving `pulse_live_now_cards`; live routes under `/api/pulse/*`.
MOBILE SCREENS: `useAgoraLiveBroadcastRoom.ts`, `src/live-audio/*`, live host/viewer screens.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: Eligibility is gated — `services/privilege_engine.py` unlocks Live only at `referral_count >= 30` or privilege level ≥ "Livestream Eligible". **`pulse_live_now_cards` excludes scheduled rows at the SQL level and `GET /api/pulse/live-now` returns no `scheduled`/`events` key** — this is the direct cause of the `business:events` launch gate (§14, §11). `livestream_prestige` is SCAFFOLDED in `services/premium_capability_engine.py`. `CLAUDE.md`: livestream needs device QA that static checks can't provide.
Evidence: privilege threshold in code + the live-now SQL omission recorded against `readiness.ts`.

---

## 8. Communities / Groups

NAME: Communities / Groups
PURPOSE: Topic and interest spaces with channels.
USER ACTIONS: join, leave, post, browse channels, moderate.
BACKEND (service modules + route prefix): `/api/pulse/communities/*`; `community_channel` is one of the four allowed conversation types (`pulse_communications_v2/service.py:20`).
MOBILE SCREENS: communities/groups screens; groups filter in `MessengerScreen.tsx:35`.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Community channels ride the messaging stack, so they inherit the Messages flag posture (§4). A dedicated QA worktree exists (`.claude/worktrees/qa-groups-rooms`), indicating recent active work.
Evidence: routes + enforced conversation type + shipped screens; no gate entry.

---

## 9. Pages

NAME: Pages (Page OS — the engine behind "Presence")
PURPOSE: The canonical non-person identity object: artist, business, brand, organization.
USER ACTIONS: create a page, edit, assign roles, invite team, connect a shop/music/business, transfer ownership, pause/unpublish.
BACKEND (service modules + route prefix): `services/pulsesoc_pages.py` (`TYPE_TABS`, `RENDERABLE_TABS`, `module_availability()`, `PERMISSIONS`, `set_link`, `clear_link`, `list_links`); 16 routes under `/api/pages/*` in `bot.py`; tables `pulse_pages`, `pulse_page_links`, `pulse_page_members`, `page_audit_log`.
MOBILE SCREENS: `PagesHub` (`AppNavigator` :546), `PageScreen.tsx`, `PageConnectionsScreen`, page editor/manage screens.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: No hard delete — lifecycle ends at `DEACTIVATED` (`docs/pages/page_os.md:32-34`). PAUSED pages stay public. `set_link` is *set*, not add — connecting a shop replaces the previous pointer, because every reader takes `[0]` (`docs/pages/page_modules.md:154-171`). `DELETE /api/pages/:id/links` carries `link_type` in the **body** (with `?type=` accepted only as a transit fallback).
Evidence: full route set, service, docs and tests (`TabCeilingTests`) all present.

---

## 10. Presence

NAME: Presence — two distinct systems sharing one word (see PART A.4)
PURPOSE: (1) the product-facing name for Page OS identity; (2) server-authoritative online/away/offline liveness.
USER ACTIONS: (1) switch identity, manage a Presence, view a public Presence; (2) appear online, show typing/recording, set presence privacy.
BACKEND (service modules + route prefix): (1) as §9. (2) `services/presence_service.py` (927 lines: `connect/heartbeat/set_activity/disconnect/disconnect_all/presence_for/presence_of/is_online/active_sessions/set_privacy/get_privacy/format_last_seen/sweep/health_snapshot`) + 9 endpoints in `services/presence_routes.py` under `/api/pulse/presence/*`.
MOBILE SCREENS: `PresenceHubScreen.tsx` (route `Presence`, `AppNavigator` :547); `mobile-native/src/api/presence.ts`.
STATUS: PRODUCTION READY (with one gated entry point — see §14)
KNOWN LIMITATIONS: Liveness is never stored: no `is_online` column, no reaper (`docs/mission8_presence_evidence.md`). Timings 45s heartbeat / 90s grace / 300s→away / 12s transient TTL; ambiguity always resolves to **offline**. The **`presence:businessOs` tile is launch-gated** because `PresenceHubScreen.tsx:305-311` navigates to `BusinessOs` without a page id, which makes `resolveRouteProfileContext` set `isOwnProfile = true` — the presence's name over the viewer's own data.
Evidence: `mobile-native/src/launch/readiness.ts:54-97`; test tables in `docs/mission8_presence_evidence.md` (48/48, 43/43, 86/86, 22, 27 passing).

---

## 11. Events

NAME: Events
PURPOSE: Public event listings for a Presence, and event management for a business.
USER ACTIONS: (visitor) view upcoming events and ticket tiers; (team) manage events.
BACKEND (service modules + route prefix): `page_events()` in `services/pulsesoc_pages.py` → `events_service.list_public_events()`; `business_os_events` table; Business OS event routes under `/api/business-os/*`.
MOBILE SCREENS: events tab inside `PageScreen.tsx`; `EventsManagerScreen` behind `EventsRoute.tsx` (route `BusinessOsEvents`, `AppNavigator` :427).
STATUS: GATED-OFF-FOR-LAUNCH (management) / PARTIALLY READY (public view)
KNOWN LIMITATIONS: **Management is hard-gated at the route**: `GATED_ROUTES = { BusinessOsEvents: "business:events" }` and `EventsRoute.tsx` returns `<ComingSoonScreen/>` unless `routeReadiness("BusinessOsEvents") === "READY"`. Cause: the three tabs resolve through `listLiveNow` → `GET /api/pulse/live-now`, which has no `scheduled`/`events` key, so they can only produce `[]`. Three event flags are off: `EVENTS_LIVE_STATS`, `EVENTS_ATTRIBUTION`, `EVENTS_MOCK`. The public path is real but conditional: the `events` tab is link-and-flag-backed — it needs the `business_os` link **and** `events_enabled()`, since with `BUSINESS_OS_EVENTS` off the domain raises 503. `starts_at`/`ends_at` are unvalidated free text, so "has it ended" is decided in Python over a 500-row window (`PUBLIC_EVENT_SCAN_CAP`); unparseable dates count as not-ended and are shown back verbatim. `_event_visitor` is a field **allowlist** — organiser identity, business id, attendees and sales counts are absent, and `business_id` is deliberately never returned to the client. Event rows render as plain views, not pressables: **there is no event detail screen in this build**.
Evidence: `readiness.ts:54-97` + `EventsRoute.tsx` + `docs/pages/page_modules.md:104-148`.

---

## 12. Music / Audio

NAME: Music / Audio
PURPOSE: Artist catalogues surfaced on Presences, and audio playback.
USER ACTIONS: browse an artist's tracks on their Presence, play, control from the lock screen.
BACKEND (service modules + route prefix): `services/music_service` (canonical catalogue); `page_music()` reads the `music_artist` page link.
MOBILE SCREENS: music tab in `PageScreen.tsx`; native module `mobile-native/modules/pulse-now-playing/` (iOS lock-screen controls, Swift).
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: There is **no standalone music browse screen** in the mobile app — music is reachable only through an ARTIST Presence that has a `music_artist` link. No link means `{"linked": false, "tracks": []}` and the catalogue is not queried; the page name is never used as a guess. A catalogue failure returns `PageError(503)` surfaced as "We couldn't load this section." with Try Again, never an empty discography (`docs/pages/page_modules.md:91-102`). Playback sits inside the realtime-audio protected perimeter.
Evidence: link-backed module only, no dedicated route/screen in `AppNavigator`.

---

## 13. Camera / Filters

NAME: Camera / Filters
PURPOSE: Capture photos and video for posts, reels and stories.
USER ACTIONS: record, capture, switch camera, apply effects, upload.
BACKEND (service modules + route prefix): upload endpoints under `/api/pulse/*`; R2 storage; `media_worker.py`.
MOBILE SCREENS: camera screens in `mobile-native/src/screens/`.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Camera is one of the 21 protection-suite subsystems and is load-bearing for the audio lock — `mobile-native/patches/` contains a LiveKit WebRTC patch specifically to stop the camera reconfiguring the shared `AVAudioSession`. `CLAUDE.md` marks both patches (Hermes build fix + WebRTC audio patch) as load-bearing, applied by `patch-package` postinstall. Uploads need device QA.
Evidence: shipped screens + protection-suite coverage; the patch exists because the failure mode is real.


---

## 14. Business OS

NAME: Business OS
PURPOSE: The operations layer for a business — listings, orders, ads, insights, verification, payments, events, customers, team.
USER ACTIONS: open the hub, view sections, manage store/orders/ads, review insights.
BACKEND (service modules + route prefix): `services/business_os/*` (entitlements, ledger, and more); `/api/business-os/*` (199 routes) and `/admin/business-os/*` (49 routes).
MOBILE SCREENS: route `BusinessOs` (`AppNavigator` :387); `mobile-native/src/api/businessOs.ts` defines `BUSINESS_OS_SECTIONS` at `:136` — **14 sections**.
STATUS: PARTIALLY READY (2 sections GATED-OFF-FOR-LAUNCH)
KNOWN LIMITATIONS: `customers` (`businessOs.ts:197`) and `team` (`:240`) carry `backed: false` and have no route and no `/api/pulse/*` endpoint; both are `COMING_SOON` in `mobile-native/src/launch/readiness.ts`. `businessOsLaunchSections()` (`:296`) now includes those routeless sections *only when* `isLaunchGated(...)` — i.e. they are shown as locked tiles rather than hidden, reversing the earlier `backed: false` invisibility. `HUB_LIVE_CARDS = false` at `businessOs.ts:117` is a **hard-coded constant, not an env flag** — live cards on the hub cannot be switched on at runtime. `docs/business_os/adr/0006-roles-and-permissions.md` defines Owner/Admin roles but is an accepted *spec gap*, not implemented RBAC. **Money bug on record**: `services/business_os/ledger/ledger.py:59-67` — `_begin()` takes a write lock only when `db.ENGINE_NAME == "sqlite"`, so the overdraft guard is a **no-op on Postgres**, which is production (`docs/business_os_ground_truth.md` lists this as the first of four money bugs).
Evidence: `readiness.ts:54-97`, `businessOs.ts:117/197/240/296`, `ledger.py:59-67`.

---

## 15. Marketplace

NAME: Marketplace
PURPOSE: The single canonical catalogue of listings — products, services and bookings.
USER ACTIONS: browse, search, filter, view a listing, contact a seller, buy.
BACKEND (service modules + route prefix): `/api/pulse/marketplace/*` (incl. `search?seller_user_id=`, which is how a Presence shop tab renders its listings — `docs/pages/page_modules.md:51-56`). Listing types include `service` and `booking`.
MOBILE SCREENS: `MarketplaceScreen` (untouched by the rebuild), `MarketplaceManager` (new), listing detail screens; `mobile-native/src/api/marketplaceOffers.ts`.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: **Three capabilities are hard-coded `false`**, not flags: `MARKETPLACE_OFFERS_ENABLED`, `MARKETPLACE_CART_ENABLED`, `MARKETPLACE_BOOST_ENABLED` (`marketplaceOffers.ts:80-103`) — so no cart, no offers, no boosting. `MARKETPLACE_REBUILD_REPORT.md` states the offer state machine is "complete and tested but has nothing to talk to" — client-side logic with no server counterpart. `EXPO_PUBLIC_MARKETPLACE_LOCATION_HONESTY` is off. `normalizeMarketplaceListing` has a documented quantity-collapse bug (`STORE_SCREEN_REBUILD_REPORT.md`). Design decision recorded in `docs/pages/page_modules.md:42-45`: the `services` tab was **removed** from BUSINESS/PROFESSIONAL_SERVICE/LOCAL_BUSINESS in favour of `shop`, because a separate services module would be a second commerce backend to keep in sync.
Evidence: three hard-coded `false` constants + the rebuild report.

---

## 16. Store / Seller

NAME: Store / Seller
PURPOSE: Becoming a seller and running a storefront.
USER ACTIONS: apply to sell, upload documents, respond to information requests, manage listings, view the store dashboard.
BACKEND (service modules + route prefix): `services/seller_lifecycle.py`; tables `marketplace_merchant_applications`, `marketplace_sellers`; web door `/pulse/merchant/apply` plus the native multi-step flow.
MOBILE SCREENS: `StoreDashboardScreen`, `SellerStoreRoute`, the seller application steps.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: `EXPO_PUBLIC_STORE_READINESS` is off. The lifecycle itself is solid — ten states (`draft, submitted, under_review, information_requested, resubmitted, approved, rejected, withdrawn, expired, suspended`), `LEGACY_STATUS_ALIASES` for old rows, `apply_transition` **refuses approval without an admin actor id**, and `applicant_view` is a whitelist with nothing sensitive logged. But payouts, the thing a seller ultimately needs, do not exist (§18). `docs/business_os/adr/0004-seller-eligibility-and-entitlements.md` is the governing policy: one entitlement source, five capabilities, four answers, and no money action on a cached entitlement. `STORE_SCREEN_REBUILD_REPORT.md` records 119 suites / 2,041 tests.
Evidence: `STORE_READINESS` flag off + payouts absent.

---

## 17. Orders

NAME: Orders
PURPOSE: Order records for buyer and seller.
USER ACTIONS: place an order, view order status, (intended) fulfil and complete.
BACKEND (service modules + route prefix): Business OS orders domain under `/api/business-os/*`.
MOBILE SCREENS: orders screens inside the Business OS hub and the store dashboard.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: Two flags off — `EXPO_PUBLIC_ORDERS_ESCROW` and `EXPO_PUBLIC_ORDERS_FULFILLMENT`. The registry states plainly that **the fulfil/complete transitions 404 in production**. So an order can be created and viewed but not advanced through its lifecycle by the app.
Evidence: `docs/business_os/FLAG_REGISTRY.md` — Orders flags off, transitions 404.

---

## 18. Payments

NAME: Payments
PURPOSE: Taking money in (purchases, subscriptions) and paying money out (seller payouts).
USER ACTIONS: pay for premium, pay for a listing; (intended) request a payout, view statements, download tax documents.
BACKEND (service modules + route prefix): Stripe integration; Apple StoreKit; unified payment router; `/api/payments/*`; `services/business_os/ledger/ledger.py`.
MOBILE SCREENS: checkout / paywall screens; premium purchase; payments sections in Business OS.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: **Six payments flags are off** — `PAYOUT_INITIATION`, `INSTANT_PAYOUT`, `STATEMENTS`, `TAX_DOCUMENTS`, `ESCROW`, `AD_TOPUP` — and the registry's justification is categorical: **"No endpoint initiates a payout anywhere in the codebase."** Money can come in; it cannot go out. `EXPO_PUBLIC_DIGITAL_COMMERCE_ENABLED` is off for Apple guideline 3.1.1 (StoreKit is not implemented for those paths). `PULSESOC_STOREKIT_STRIPE_UNIFIED_PAYMENTS_FINAL_REPORT.md` is marked **PARTIAL** with owner-only App Store Connect items outstanding. The ledger overdraft guard is a no-op on Postgres (§14). ADR-0004: a client may not complete a money action on a cached entitlement. `CLAUDE.md`: checkout needs device QA.
Evidence: six off flags + "no endpoint initiates a payout" + PARTIAL report verdict.

---

## 19. Premium

NAME: Premium
PURPOSE: Paid subscription tier, plus a permanent founder tier.
USER ACTIONS: subscribe, restore purchase, view entitlements and benefits.
BACKEND (service modules + route prefix): canonical `services/business_os/entitlements/premium.py`; legacy `services/premium_entitlement_service.py`; `services/pro_access.py`; `services/premium_capability_engine.py`; `services/crypto_premium_gate.py`.
MOBILE SCREENS: route `Premium` (`AppNavigator` :557) and its paywall/benefits screens.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: **Four competing authorities**, named as such in `premium.py`: legacy tables, canonical `business_os_ent_grants`, `users` identity columns, and the session flag `has_premium_access`. Resolution depends on the `BUSINESS_OS_ENTITLEMENTS` mode (`off` / `shadow` / `canonical`) — an in-flight migration, not a settled system. Of the seven entries in `services/premium_capability_engine.CAPABILITIES`, only **`premium_identity` and `creator_ai` are ACTIVE**; `advanced_analytics`, `premium_studio`, `discovery_boosts`, `livestream_prestige` and `creator_acceleration` are **SCAFFOLDED**, each listing `required_tables`/`required_routes`/`required_services` it lacks. Pricing constants: `FOUNDER_PRICE_CENTS = 499`, `PREMIUM_VALUE_CENTS = 999`. An account hold beats any paid grant. Denials are HTTP-200 `premium_required`, never 403.
Evidence: SCAFFOLDED statuses in the capability registry + the acknowledged split-brain in `premium.py`.

---

## 20. Crypto (portfolio / alerts / watchlists / simulator)

NAME: Crypto
PURPOSE: The original CoinPilotX product, retained as a subsystem — portfolio tracking, price alerts, watchlists, trade simulation.
USER ACTIONS: track holdings, create alerts, build watchlists, run simulated trades.
BACKEND (service modules + route prefix): crypto route family in `bot.py`; `services/crypto_premium_gate.py`; `services/market_observations.py`; CoinGecko integration; `alert_worker.py`.
MOBILE SCREENS: `Portfolio` (`AppNavigator` :578), `Watchlists` (:577), `CryptoAlertCenter` (:589).
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: **Realized P/L is explicitly NOT IMPLEMENTED** — `PREMIUM_CRYPTO_INTELLIGENCE_FINAL_REPORT.md` records "no transaction ledger exists — correctly refused", i.e. the feature was declined rather than faked. That report's overall verdict is **PARTIAL**. Alert quotas: free = 5 basic rules, premium = 100, across 16 alert conditions. `services/market_observations.py` keeps only **7 days** of retention. Advanced alerts and portfolio intelligence sit behind `premium.crypto.advanced_alerts` / `premium.crypto.portfolio_intelligence` through the fail-closed gate, with owner bypass via `PULSESOC_OWNER_USER_IDS`. Premium capabilities were seeded onto existing plans with **no new IAP SKU**. `alert_worker` was added to the Procfile per `APP_REVIEW_FINAL_GO_NO_GO_REPORT.md`.
Evidence: PARTIAL report verdict + explicit NOT IMPLEMENTED for realized P/L.

---

## 21. Ads / Advertising

NAME: Ads / Advertising
PURPOSE: Campaign creation, ad accounts, spend and delivery.
USER ACTIONS: create a campaign, fund an account, view spend and performance.
BACKEND (service modules + route prefix): Business OS ads domain under `/api/business-os/*`; the canonical ads system that pages link to (`docs/pages/page_advertising.md`).
MOBILE SCREENS: ads sections inside the Business OS hub; ad spend surfaces.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: `EXPO_PUBLIC_ADS_POST_MODE` is off, and `EXPO_PUBLIC_PAYMENTS_AD_TOPUP` is off — so the funding path for ads is disabled along with the rest of payouts/top-ups (§18). Page-side, advertising is a **link into** the canonical system, never a second ads backend (`docs/pages/page_advertising.md`). Page role `ADVERTISING_MANAGER` gates who may act.
Evidence: `ADS_POST_MODE` and `AD_TOPUP` off in `docs/business_os/FLAG_REGISTRY.md`.

---

## 22. Creator tools

NAME: Creator tools
PURPOSE: Tools for people who publish — AI assistance, analytics, studio, growth.
USER ACTIONS: use creator AI, view analytics, access studio features.
BACKEND (service modules + route prefix): `services/premium_capability_engine.py`; `services/pulse_ai_service.py`; `services/privilege_engine.py` for eligibility.
MOBILE SCREENS: creator surfaces reached from profile/premium; UNDX/AI screens.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: Of the creator-facing capabilities, only **`creator_ai` is ACTIVE**. `premium_studio`, `discovery_boosts`, `advanced_analytics`, `creator_acceleration` and `livestream_prestige` are all **SCAFFOLDED** in `services/premium_capability_engine.py` — the registry names the tables, routes and services each one still needs. Creator standing is derived from trust score via `level_for_trust()`, not from a column, so it can move without an explicit grant.
Evidence: SCAFFOLDED status literals in the capability registry.

---

## 23. Progress / Rewards / Referral

NAME: Progress, Rewards & Referral
PURPOSE: Onboarding missions, milestones, credits and invite-based growth.
USER ACTIONS: complete missions, hit milestones, redeem credits, invite friends, claim a referral.
BACKEND (service modules + route prefix): `/api/progress/{,activity,faq,how-it-works,invite,milestones,missions,rewards,tile}`; redemption at `/api/pulse/rewards/credits/redeem`; referral claim at `/api/mobile/referral/claim`. `users` carries `referral_code` and `referred_by` (`bot.py:104936-105035`).
MOBILE SCREENS: progress/rewards/invite screens.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Referrals are load-bearing for an unrelated feature: `services/privilege_engine.py` unlocks Livestream at `referral_count >= 30`, so the growth loop is also an eligibility gate. Credit redemption terminates in the payments subsystem, which cannot pay out (§18).
Evidence: full route set present, no gate entry, no off flag.

---

## 24. Security & Trust / Safety

NAME: Security & Trust / Safety
PURPOSE: Account protection, moderation, reporting and platform integrity.
USER ACTIONS: report content, review security, manage account protection; (staff) work moderation and trust queues.
BACKEND (service modules + route prefix): `services/admin_gateway.py`; moderation roles in `ROLE_FALLBACK_PERMISSIONS` (`bot.py:17409-17435`); `users` columns `trust_level`, `security_score`, `account_status`, `access_enabled`, `login_enabled`, `hidden_from_discovery`, `restricted_reason`, `suspended_reason` (`bot.py:104936-105035`).
MOBILE SCREENS: `SafetyHub` (`AppNavigator` :601); report flows.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Enforcement is spread across four role systems (PART B), so "who can act" depends on which surface you are on. Every admin denial writes `log_admin_audit` (`bot.py:17491-17530`), and `services/admin_gateway.py` returns a uniform "Access denied." so failures leak nothing — good for security, harder for support triage. Trust score silently changes privilege level via `level_for_trust()`.
Evidence: decorators, audit logging, throttling and the trust columns are all in code.

---

## 25. Account management

NAME: Account management
PURPOSE: Identity, session, profile and account state.
USER ACTIONS: sign up, log in, refresh session, edit profile, manage account status.
BACKEND (service modules + route prefix): `/api/account/*` and `/api/mobile/auth/*`; `bot.py:4412` `load_account_by_id`, `bot.py:4914` `require_account()`; `users` table at `bot.py:815` widened by `add_columns_if_missing` at `bot.py:104936-105035` (~100 columns).
MOBILE SCREENS: auth screens; profile edit; `mobile-native/src/api/` auth layer — bearer token + session cookie, refresh via `POST /api/mobile/auth/refresh`, tokens in expo-secure-store.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: The schema is hand-rolled: `CLAUDE.md` notes there is no real migration framework, schema is created imperatively in `bot.init_db()` with ~170 tables in `AUTO_PK_TABLES`, and every change must be idempotent. `EXPO_PUBLIC_ACCOUNT_NAME_FIRST` was deleted rather than shipped. Auth is one of the 21 protection-suite subsystems.
Evidence: shipped auth path + protection-suite coverage.

---

## 26. Notifications

NAME: Notifications
PURPOSE: Push, in-app and email notification delivery.
USER ACTIONS: receive pushes, view the notification list, manage preferences.
BACKEND (service modules + route prefix): Firebase/FCM + APNs + web push; `email_worker.py` (in the Procfile) and Brevo for email/SMS; notification routes under `/api/pulse/*`.
MOBILE SCREENS: notifications screen; notification settings.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: `CLAUDE.md` names push as one of four areas where "static checks don't replace device QA". No VoIP push certificate exists, which is part of why `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED` is off (§6) — call notifications cannot use the native path. `pulse_worker` and `telegram_worker` exist but are **not in the Procfile**, so any delivery depending on them does not run.
Evidence: workers absent from Procfile + no VoIP cert + device-QA caveat.

---

## 27. Search

NAME: Search
PURPOSE: One search surface across people, posts, communities, listings and Presences.
USER ACTIONS: search, filter by result group, open a result.
BACKEND (service modules + route prefix): `/api/pulse/search`; Presences are returned as a `presences` group with `type: "presence"` **inside the existing search endpoint**, not as a second search API (`docs/pages/page_search_and_admin.md`).
MOBILE SCREENS: search screen and result tabs.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: `hidden_from_discovery` on `users` removes accounts from results. Marketplace search doubles as the shop-tab data source via `?seller_user_id=`, so a change to search ranking touches Presence shop pages too.
Evidence: single endpoint, groups implemented, no gate or flag.

---

## 28. Settings

NAME: Settings
PURPOSE: Preferences across privacy, notifications, appearance, language, accessibility and account.
USER ACTIONS: change preferences, manage privacy, toggle reduce-motion, switch language.
BACKEND (service modules + route prefix): `/api/account/*` and per-domain preference routes (e.g. presence privacy via `/api/pulse/presence/*` `set_privacy`/`get_privacy`).
MOBILE SCREENS: **18 dedicated settings screens** under `mobile-native/src/screens/`.
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: `EXPO_PUBLIC_STATE_LANGUAGE` was deleted rather than shipped. i18n is CI-gated — `CLAUDE.md` states hardcoded strings fail CI, verified by `npm run verify` (typecheck + i18n + jest). In-app reduce-motion is combined with the OS setting by `useLaunchMotionEnabled()` in `mobile-native/src/launch/useLaunchGate.tsx`.
Evidence: 18 screens shipped; no gate entry.

---

## 29. Verification

NAME: Verification
PURPOSE: Badge review for people, creators, businesses, sellers, advertisers, organizations and partners.
USER ACTIONS: submit a verification request, upload documents, track status; (staff) review a track.
BACKEND (service modules + route prefix): `services/pulsesoc_dashboard_centers.py:40-75` — `BADGE_TYPES` (13), `ADMIN_ROLES` (8 reviewer roles), `READONLY_ADMIN_ROLES = {"support_readonly","support_agent"}`, `TRACK_ROLE_MAP` (8 tracks → roles). Page-side: `docs/pages/page_verification.md`.
MOBILE SCREENS: `VerificationCenter` (`AppNavigator` :609).
STATUS: PRODUCTION READY
KNOWN LIMITATIONS: Page verification is `unverified → pending → verified | rejected`, **never auto-granted**, and `manage_status` is **OWNER-only** — no delegated role can verify a Presence. Verification has **no env flag** (`docs/business_os/FLAG_REGISTRY.md` notes the repair is live). ADR-0006 would exclude a business Admin from verification documents entirely, but that ADR is a spec gap, not shipped code (§B.5). `EXPO_PUBLIC_SCOPED_BADGES` is off, so badge scoping is not active.
Evidence: role maps, badge types and screens all present; `SCOPED_BADGES` off.

---

## 30. Education / Courses

NAME: Education / Courses
PURPOSE: Lessons, quizzes and an AI tutor.
USER ACTIONS: browse categories, take a lesson, submit a quiz, ask the tutor.
BACKEND (service modules + route prefix): `/api/education/{categories,lessons,quiz/submit,tutor}`.
MOBILE SCREENS: education screens reached from the app; "Teacher" is a rung on the `services/privilege_engine.py` ladder.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: A compact four-endpoint surface — categories, lessons, quiz submission and tutor — with no authoring, enrolment, progress-tracking or certification endpoints. There is no course-creation path in the API, so content is not user-generated. "Teacher" exists as a privilege level without a matching authoring surface.
Evidence: only four `/api/education/*` endpoints exist; no authoring routes.

---

## 31. Arena

NAME: Arena (and Roast Battle)
PURPOSE: Competitive/entertainment format.
USER ACTIONS: (web) participate in Arena; Roast Battle is the promoted Pulse entertainment feature.
BACKEND (service modules + route prefix): `/api/arena` (**120 routes**) and `/arena` (37 routes) in `bot.py`; **11 `services/arena_*.py` modules**; admin roles `arena_operator` and `roast_operator` in `ROLE_FALLBACK_PERMISSIONS` (`bot.py:17409-17435`).
MOBILE SCREENS: **none.** The only occurrence in the mobile app is a string in `mobile-native/src/screens/HomeScreen.tsx`; there is no Arena screen and no Arena route in `AppNavigator`.
STATUS: DEPRECATED (on mobile) — backend still live
KNOWN LIMITATIONS: `reports/arena_pause_strategy.md` states: "Arena is removed from Pulse primary navigation and public primary navigation. Roast Battle is the promoted Pulse entertainment feature." So a 157-route backend and 11 services are running with no native client. This is the largest orphaned surface in the codebase and the biggest single source of confusion when reading route counts.
Evidence: `reports/arena_pause_strategy.md` + zero mobile screens against 157 backend routes.

---

## 32. UNDX (AI layer)

NAME: UNDX
PURPOSE: The AI mission/execution layer — assistant, agent council, and a kernel that can propose repository changes.
USER ACTIONS: chat with the assistant, review agent actions and receipts, grant/revoke tool permissions, trigger an emergency stop, draft and publish marketplace listings via AI.
BACKEND (service modules + route prefix): root modules `undx_router.py`, `undx_execution_kernel.py`, `undx_brain_layer.py`, `undx_desktop_connector.py` plus ~25 `services/undx_*.py`; `services/undx_company_identity.py` for grounding; `undx_worker.py` in the Procfile. Routes: `/api/undx/{chat,agent-council,kernel/{apply,git,propose,scan,validate},desktop-connector/}`; mobile surface `/api/business-os/undx/{permissions,policies,tools,requests,receipts,confirmations,emergency-stop,marketplace/listings/{draft,publish/plan,publish/execute}}`; plus `/api/pulse-ai/conversation` (`services/pulse_ai_service.py`).
MOBILE SCREENS: `UndxActionCenterScreen.tsx`, `UndxCapabilitiesScreen.tsx`; `ai` filter in `MessengerScreen.tsx:35`.
STATUS: PARTIALLY READY
KNOWN LIMITATIONS: The kernel writes only after the literal approval phrase **`APPROVE UNDX WRITE`**, and blocks `.env`, `.git`, venv, secrets and sqlite paths, logging to `undx_execution_log.jsonl` (`CLAUDE.md`, `docs/undx_manual.md`). `undx_router` selects between OpenAI/Claude/Gemini/DeepSeek/Groq **server-side** so keys never reach the browser. Identity grounding is fail-closed: an answer about the company must contain `COMPANY_IDENTITY_REQUIRED_PHRASE`, and `UNVERIFIABLE_WITHOUT_SOURCE` forbids claiming feature production-readiness or Android availability without a source. `premium.undx.advanced` gates the advanced tier. `CLAUDE.md` records this area as actively in flux — untracked `services/undx_mission_runtime.py` and `tests/undx_agent/test_safety_precedence.py` on the working branch.
Evidence: shipped routes and screens, but uncommitted runtime files and an approval-phrase-gated write path.

---

# Appendix — what is gated off for launch, in one place

**Launch readiness gate** (`mobile-native/src/launch/readiness.ts:54-97`) — exactly four:

| Module id | State | Why |
|---|---|---|
| `business:events` | BUILDING | `GET /api/pulse/live-now` returns no `scheduled`/`events` key; `pulse_live_now_cards` excludes scheduled rows in SQL. All three tabs resolve to `[]`. Also hard-blocked at the route via `GATED_ROUTES` + `EventsRoute.tsx`. |
| `business:customers` | COMING_SOON | No screen, no route, no endpoint (`businessOs.ts:197`, `backed: false`). |
| `business:team` | COMING_SOON | No screen, no route, no endpoint (`businessOs.ts:240`, `backed: false`). |
| `presence:businessOs` | BUILDING | `PresenceHubScreen.tsx:305-311` navigates without a page id → `isOwnProfile = true` → presence's name over the viewer's own data. The `BusinessOs` route itself stays READY. |

An id absent from that table is READY. The gate does not delete anything: locked
tiles keep their size, icon and position, gain a teal edge and a badge, and do
**not** set `accessibilityState.disabled` (`LaunchTile.tsx`).

**Separately**, 24 `EXPO_PUBLIC_*` flags default off (`docs/business_os/FLAG_REGISTRY.md`)
and four constants are hard-coded `false` (`businessOs.ts:117`;
`marketplaceOffers.ts:80-103`). Those are the reason Payments, Orders,
Marketplace, Messages, Ads and Store read PARTIALLY READY above, and they are a
different mechanism from the launch gate — do not conflate the two.

---

*End of file. Reconnaissance was read-only; this is the only file written.*
