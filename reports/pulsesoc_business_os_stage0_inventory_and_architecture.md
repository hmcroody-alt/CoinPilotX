# PulseSoc Business OS — Stage 0: Inventory & Architecture

**Date:** 2026-07-24 · **Author:** Super Master Engineer · **Branch:** `release/undx-nexus-core-v4`

This is a **read-only inventory of what already exists**, produced before any new code. Every
claim below is grounded in observed files (path + line where practical). Maturity verdicts use
the mission vocabulary — **PASS / PARTIAL / BLOCKED / NOT TESTED** — and a screen existing is
never treated as PASS. All findings were gathered from the actual repository and cross-checked
against the live SQLite schema (`coinpilotx.db`, **615 tables**) and the route surface
(**1,252 Flask routes**, verified by grep).

> **Method note.** Four parallel inventory passes read `bot.py` (~104k lines), the ~210
> `services/*.py` modules, `migrations/*.sql`, `models/`, `mobile-native/src/`, `templates/`,
> `static/`, and the sqlite schema. Route/table counts were independently re-verified by the
> author. Nothing here was assumed from naming alone; "REAL" means DB-backed and
> server-validated code was read, "mock/placeholder" means a stub or hardcoded return was read.

---

## Executive summary

PulseSoc already contains a **substantial, partially-real business layer** — this is not a
greenfield build. The strongest domain is **Advertising** (server-authoritative wallet ledger,
reserve→spend billing loop, delivery engine, moderation). **Marketplace**, **Premium**, and
**Crypto** each have a real spine but material gaps. Payments run on a genuine **Stripe Connect**
integration with signature-verified, idempotent webhooks. There is a real **RBAC + append-only
audit** admin layer. UNDX is an LLM chat router plus an approval-gated *repository-write* agent —
it is **not** yet a business/financial tool-caller.

The dominant architectural debt is **concentration and duplication**: all 1,252 routes hang off
one Flask app object in a single 104k-line `bot.py`; there are four overlapping ad services,
three overlapping market/crypto services, four parallel entitlement tables, and a dead legacy
`mobile/` app. Financial state is mostly server-authoritative and auditable, but ledger writes
and balance updates are **not atomic**, and idempotency is enforced inconsistently.

| Domain | Backend | Data | Mobile | Verdict |
|--------|---------|------|--------|---------|
| Advertising | REAL | REAL (`pulse_ad_*`, ~30 tables) | Serving only (no advertiser UI) | **PARTIAL (most mature)** |
| Marketplace | PARTIAL (seller + Stripe checkout real; orders/fulfillment placeholder) | Mixed (real listings; `*_placeholder` orders) | REAL screens | **PARTIAL** |
| Premium | REAL entitlement service | REAL but fragmented (4 entitlement tables) | REAL screen | **PARTIAL (no IAP path)** |
| Crypto | REAL (live market data, DB alerts) | REAL (`crypto_*`, alerts, portfolio) | Alerts screen only | **PARTIAL (informational only)** |
| Payments | REAL (Stripe Connect) | REAL | Opens checkout in browser | **PARTIAL** |
| Ledger | REAL (immutable, integer cents) | REAL | n/a | **PARTIAL (non-atomic writes)** |
| Admin | REAL (RBAC + audit) | REAL | n/a (web/server-rendered) | **PARTIAL** |
| UNDX business tools | Absent (repo-write agent only) | n/a | Chat client | **NOT BUILT** |

---

## 1. Existing advertising functionality — **PARTIAL (the most built-out domain)**

**Backend (REAL, DB-backed).**
- `services/pulse_ads_service.py` (1,477 lines) — 12 named placements, moderation,
  privacy-safe payloads, and a real delivery engine `select_ads()` that gates on
  `campaign_can_spend` and debits per-delivery via `record_spend_event`.
- `services/pulse_ad_payments.py` (532 lines) — server-authoritative advertiser wallet/ledger:
  `spendable_balance_cents`, idempotent `reserve_campaign_budget` (uses
  `pulse_ad_wallets.reserved_budget_cents`), typed transactions (funding/spend/refund/
  chargeback/reserve). Every write checks a UNIQUE `idempotency_key` first; spend clamps with
  `max(0, …)`. Stripe IDs stay server-side.
- `services/pulse_advertiser_portal.py` (778 lines) — advertiser workflows with RBAC roles
  (owner / campaign_manager / …).
- `services/ad_policy_engine.py` (73 lines) — allow/block category + phrase scanner
  (`evaluate_ad`).
- `services/dashboard_ads_command_center.py` (680 lines) — 13-section admin "Ads Command
  Center" (review board, delivery, analytics, brand deals).

**Routes.** ~59 ad routes on the single app, e.g. `/api/pulse/ads/accounts`, `/campaigns`,
`/creatives`, `/creatives/submit`, `/impression`, `/viewability`, `/click`, `/event`,
`/wallet/funding-session`, `/campaigns/<id>/reserve-budget`, `/campaigns/<id>/action`,
`/api/pulse/ads/placements`, plus admin pages `/dashboard/ads/<subsystem_key>`.

**Data.** ~30 populated `pulse_ad_*` tables including `pulse_ad_accounts`, `pulse_ad_wallets`,
`pulse_ad_wallet_transactions` (UNIQUE idempotency_key), `pulse_ad_campaigns`,
`pulse_ad_creatives`, `pulse_ad_impressions`, `pulse_ad_clicks`, `pulse_ad_placements` (12),
`pulse_ad_moderation_queue`, `pulse_ad_review_board`, plus billing/invoices/receipts/refunds/
targeting/frequency_caps (present, mostly 0 rows). A **legacy** `ad_*` generation
(`ad_campaigns`, `ad_creatives`, `ad_impressions`, `advertisers`, `brand_deals`,
`sponsorships`) exists with **0 rows — dead**.

**Mobile.** `mobile-native/src/api/ads.ts` calls real serving endpoints and renders server
`SponsoredAd` payloads with delivery tokens. **No advertiser-portal screens on mobile** — the
portal is web/template-only (`templates/pulse_advertiser_portal.html`).

**Gaps.** Billing is CPM-estimate style (`estimated_daily_spend ≈ impressions_today`), not true
auction/CPC clearing; targeting tables empty; no impression/click fraud verification beyond the
client event; no native advertiser UI; invoices/receipts unpopulated.

---

## 2. Existing marketplace functionality — **PARTIAL (seller + checkout real; orders placeholder)**

**Backend.** `services/marketplace_engine.py` is a **12-line stub** returning
`{"status": "foundation_only"}`. `market_service.py` / `market_data.py` are **crypto price**
services (misleading names), not commerce. The real marketplace logic lives **inline in
`bot.py`**: listings CRUD, merchant application, and Stripe Connect checkout.

**Routes.** ~47 in `bot.py`: seller listings (`/api/pulse/marketplace/seller/listings`,
`/pause`, `/resume`, `/delete`, `/listings/create`, `/media/upload`, `/report`, `/save`);
merchant (`/seller/apply`, `/merchant/apply|dashboard|payouts`); buyer (`/api/pulse/orders`,
`/orders/<id>`); checkout (`/api/pulse/payments/checkout`, `/payouts/connect`). Checkout is
**real Stripe Connect** — creates a `checkout.Session` with `application_fee_amount` +
`transfer_data.destination`, writes `seller_transactions`, and returns explicit
`blocked_stripe_not_configured` / `blocked_payout_onboarding_required` states when unconfigured
(no fake success).

**Data.** Real & populated: `marketplace_listings` (21), `marketplace_sellers` (2),
`marketplace_merchant_applications` (2), `marketplace_merchant_documents` (3),
`seller_transactions` (2), `checkout_attempts` (2), `creator_wallets`, `creator_ledger_entries`,
`payout_queue`. **Tell-tale placeholders (0 rows):** `marketplace_orders_placeholder`,
`creator_payouts_placeholder`, `seller_payouts`, `platform_payouts`, `marketplace_product_media`,
`marketplace_saved_products`, `pulsesoc_seller_products`, `pulsesoc_seller_stores`.

**Mobile (REAL).** `MarketplaceScreen.tsx`, `SellerStoreScreen.tsx`,
`SellerListingComposerScreen.tsx`, `BuyerOrdersScreen.tsx`; api `marketplace.ts`, `orders.ts`,
`messengerOrdering.ts` — all call real endpoints.

**Gaps.** **No order state machine** — orders exist only as `seller_transactions` rows; the
canonical `marketplace_orders` table is a 0-row `_placeholder`. No fulfillment/shipping/tracking,
no refund/dispute workflow, no inventory decrement on sale, no seller-payout execution, no
review/ratings workflow. Buyer order statuses are typed in `orders.ts` but not driven by a real
lifecycle engine. No distinct digital-vs-physical fulfillment.

---

## 3. Existing business tools (unified Business Area) — **PARTIAL / fragmented**

There is **no single unified Business Area**. Business capability is spread across:
server-rendered dashboards in `templates/` (`dashboard.html`, `pulse_advertiser_portal.html`,
`pulsesoc_intelligence_center.html`, several `admin_*_command_center.html`), eight
`services/dashboard_*_command_center.py` modules (account / ads / ai / creator / crypto /
economy / intelligence / network), and native screens (`CreatorStudioScreen`,
`UserDashboardScreen`, `GrowthCenterScreen`, `IntelligenceCenterScreen`,
`DashboardModuleDetailScreen`, and the domain screens listed above). These are **not** unified
behind one role-adaptive Business home, and command-center logic is duplicated between web
templates and native screens.

---

## 4. Existing payment integrations — **PARTIAL (real Stripe Connect)**

**Provider: Stripe only** (`import stripe`). `services/payment_provider.py` (178 lines) is a
genuine modular provider boundary, not a stub, covering the full lifecycle:
`create_connected_account` (Express Connect), `create_onboarding_link`, `get_account_status`,
`create_checkout_session` (with marketplace split fee), `create_payment_intent`,
`create_transfer` (payout), `create_refund`, and `verify_webhook_signature`
(`stripe.Webhook.construct_event`). Missing config returns `setup_required(...)` dicts — clean
degradation.

**No PayPal, Apple Pay, Braintree, or Square.** "Coinbase" references are read-only market-price
APIs. `connected_wallets`/`saved_wallets` are watch-only crypto-address intel, not payment rails.

**Apple IAP is deliberately BLOCKED**, not implemented: native iOS paid digital access returns a
403 `{"iap_required": true, "ios_core_only": true}` (`bot.py:4296`) — there is currently **no
working store purchase path on iOS**.

**Webhooks (REAL).** `stripe_webhook()` (`bot.py:86711`) refuses unsigned events (503 when
`STRIPE_WEBHOOK_SECRET` absent), verifies signatures, and dedups via
`payment_webhook_events(provider_event_id TEXT UNIQUE)` returning `{"duplicate": True}` on
IntegrityError, plus a legacy `stripe_event_processed` guard.

**Gap.** Dedup relies on catching a DB IntegrityError, and the event row is written status
"received" **before** handlers finish — a mid-processing crash can leave an event
received-but-unprocessed. **No replay/reconciliation worker was found.**

---

## 5. Existing subscriptions / premium — **PARTIAL (real server-side entitlements, no IAP)**

**Backend (REAL).** `services/premium_entitlement_service.py` (768 lines) is the source of
truth: DB grant/revoke to `premium_entitlements`, `user_entitlements`,
`pulse_premium_entitlements`; `get_user_entitlements` returns a boolean capability map; Stripe
price IDs from env. `premium_capability_engine.py` (232 lines) is an honest registry marking each
capability `active` / `scaffolded` / `future` / `disabled`. `premium_identity_engine.py` and
`premium_visibility_engine.py` handle badges/upsell. `billing_service.py` is a 37-line Stripe
status normalizer.

**Routes.** Full lifecycle present: `/api/premium/checkout`, `/billing-portal`, `/status`,
`/api/subscriptions/status|checklist|upgrade|downgrade|cancel|resume`, `/api/stripe/webhook`.

**Data.** `pulse_subscriptions`, `subscriptions` (Stripe customer/subscription IDs, trial
dates), `user_subscriptions`, `subscription_plans`, `payment_records`, `pulse_payment_events`,
`payment_verifications`, `payment_webhook_events`, `pulse_premium_profiles`,
`pulse_premium_feature_flags`, `premium_badges`.

**Mobile (REAL).** `PremiumScreen.tsx` + `api/premium.ts` call `/api/premium/status` and open a
Stripe `checkout_url` in the browser.

**Gaps.** (1) **No Apple/Google IAP receipt validation** — the actual iOS monetization path is
blocked/stubbed; premium is unlocked server-side via Stripe webhook + admin grants only.
(2) **Entitlement state is fragmented across four tables** (`premium_entitlements`,
`user_entitlements`, `pulse_premium_entitlements`, `dashboard_entitlements`) with no single
canonical ledger — reconciliation risk. (3) Stripe path depends on env price IDs /
`PAYMENT_PROVIDER_ENABLED` likely unset in dev.

---

## 6. Existing crypto systems — **PARTIAL (informational only)**

**Backend (REAL).** `services/market_data.py` uses live CoinGecko (`/coins/markets`) with a
Coinbase ticker fallback and 60s cache. `dashboard_crypto_command_center.py` (822 lines) is an
owner-scoped dashboard that emits truthful PARTIAL states when live integrations are unavailable
and redacts secrets; its "AI" is **deterministic templating, not an LLM**.
`services/alert_engine.py` (1,574 lines) is a production, DB-backed alert engine (rules stored,
evaluated by `alert_worker.py`, dispatched with delivery logs). `auto_signals_service.py` runs
persistent BTC/ETH/SOL monitoring with cooldowns. `portfolio_service.py` provides DB
holdings/watchlist/alerts with free-tier limits; explicitly "educational only… does not hold
funds." `wallet_intel.py` does **read-only** address analysis (regex chain detection + explorer
links) — no signing.

**Routes.** ~93 crypto route matches: `/api/crypto/summary|alerts|watchlists|token-scan|trending
|gainers|losers|news`, `/api/dashboard/crypto/state`, `/api/live/market`, `/api/markets`,
`/api/quote/crypto/<symbol>[/chart|/news|/signals]`, `/api/wallet-intel`, `/api/day-signal`.

**Data.** `crypto_watchlists`, `crypto_watchlist_assets`, `crypto_alerts`,
`crypto_favorite_assets`, `crypto_recent_assets`, `crypto_news_cache`, `alert_rules`,
`alert_events`, `alert_delivery_jobs`, `alerts_history`, `manual_portfolio` (no cost basis),
`portfolio_items` (has `average_buy_price`), `portfolio_snapshots`, `connected_wallets` /
`saved_wallets` (read-only refs), `last_prices`, `price_history`, `whale_alerts`, `scam_alerts`,
`day_signal_results`.

**Scope (verified).** A grep for `private_key|sign_tx|execute_trade|place_order|swap|broadcast
|withdraw|seed_phrase|mnemonic|custody` found **zero** custody/trading/signing code (only a
secret-redaction denylist and a scam-keyword list). Crypto is **informational only** — exactly
the safe Phase-1 scope the mission prescribes.

**Mobile.** `AlertManagementScreen.tsx` + `api/alerts.ts` are real; no dedicated native
watchlist/portfolio screens (web dashboard handles those).

**Gaps.** Inconsistent cost-basis (`portfolio_items` has `average_buy_price`, `manual_portfolio`
does not); no realized-gain / P&L / tax-basis pipeline; premium providers (CoinMarketCap /
CryptoPanic / WhaleAlert) gated on unset env keys.

---

## 7. Existing financial database models — **PARTIAL (immutable, integer-cents, non-atomic)**

**Ledgers (append-only):** `creator_ledger_entries` (entry_type credit/debit/fee/refund/payout/
hold/release, `amount_cents`, `status`, `provider_reference`, `trace_id`, `metadata_json`),
`fee_ledger` (UNIQUE(source_type, source_id, fee_type)), `pulse_growth_ledger` (UNIQUE
idempotency_key), `reputation_ledger`, `pulse_ad_wallet_transactions` (UNIQUE idempotency_key).

**Balances/wallets (derived):** `creator_balances`, `creator_wallets`, `platform_wallets`,
`pulse_ad_wallets`, `pulse_growth_wallets`. **Transactions:** `transactions` (legacy `amount`
REAL — float), `creator_transactions`, `seller_transactions`, `treasury_transactions`.
**Payouts:** `creator_payouts`, `platform_payouts`, `seller_payouts`, `payout_history`,
`payout_queue`, `payout_failures`, `seller_payout_accounts`. Also `escrow_holds`, `ad_revenue`,
`revenue_breakdown`, `pulse_ad_invoices`, `pulse_ad_refunds`, `payment_records`,
`unmatched_payments`.

**Assessment.** This is a genuine immutable, server-authoritative ledger (integer cents,
idempotency keys, derived balances via `reconcile_wallet()` SUM-CASE over
`creator_ledger_entries`). **Gaps that must be fixed before scaling money movement:**
(1) ledger insert and balance update are **not atomic** — `post_ledger_entry` commits/closes its
own connection separately from `reconcile_wallet`, so balances can transiently drift; (2)
`creator_ledger_entries` lacks a UNIQUE idempotency constraint (unlike the ad/growth ledgers), so
creator-side dedup depends on caller discipline; (3) legacy `transactions.amount` is a float.

---

## 8. Existing admin controls — **PARTIAL (real RBAC + append-only audit)**

**RBAC (REAL).** `admin_has_permission()` (`bot.py:15919`) grants `owner` full access, else checks
`role_permissions` / `admin_role_permissions` / `admin_user_roles`. Decorators
`require_admin_page`, `require_admin_api`, `require_owner_api`, `require_owner_admin_page` return
401/403 and log denials. RBAC tables: `admin_users`, `admin_roles`, `admin_role_permissions`,
`admin_user_roles`, `role_permissions`, `admin_permissions`.

**Surface.** **208 `/admin` routes.** Capabilities include user management + CSV export, Stripe
repair/retry, Stripe health / Connect dashboards, transaction viewing, moderation
(`moderation_cases`, `comm_v2_moderation_events`), payouts, refunds. Eight
`dashboard_*_command_center.py` modules back the consoles.

**Audit (REAL, append-only).** `audit_service.log_admin_action` INSERTs into
`admin_audit_logs(admin_user_id, action, target_type, target_id, metadata, ip_hash, created_at)`;
`bot.py` logs 84 times including on permission denials. A grep for `UPDATE`/`DELETE` against the
audit tables returned **zero** hits — immutable in practice.

**Gaps.** ~41 of 208 `/admin` routes have **no visible `require_admin_*` guard** (some may be
public login pages — needs a per-route audit); `ip_hash` is written as an empty string (no source
attribution); immutability is convention-only (no DB trigger / WORM). No step-up auth or dual
approval on high-risk financial actions yet.

---

## 9. Existing UNDX business tools — **NOT BUILT (repo-write agent, not a business tool-caller)**

Two real UNDX subsystems exist, **neither of which is a business/financial tool layer**:
- **`undx_router.py`** — LLM provider fan-out (OpenAI / Claude / Gemini / DeepSeek / Groq) with
  intent classification. Server holds keys; untrusted input is HTML-stripped and truncated. **No
  tool registry, no actions** — pure text generation.
- **`undx_execution_kernel.py`** — an approval-gated **repository-write agent**: `scan_repository`,
  `generate_proposal`, `apply_approved_changes` (writes files), `run_safe_validation`
  (allowlisted subprocess), `git_gateway`. Two-phase draft→approve requiring the exact phrase
  `"APPROVE UNDX WRITE"`; `PROTECTED_PATTERNS` blocks `.env`/secrets/DB/`.git`; path-traversal
  refusal; super-user-only routes (`require_super_user_api`). It can *target* files containing
  `payments`/`stripe`/`premium`/`wallet` for code edits but **does not call** any payments/ads/
  crypto API. There is **no confirmation-gated business action registry** of the kind the mission
  requires (spend money, publish ad, issue refund, change price, etc.).

**Implication.** The entire "UNDX Business Operating Layer" (versioned tool registry with
auth/confirmation/idempotency/canonical-verification per tool) is **greenfield**.

---

## 10. Existing web and native routes — **inventory**

- **1,252 Flask routes**, all declared `@webhook_app.route` on **one app object** (no blueprints;
  grep for `@app.route`/`@bp.route` = 0). Route logic is concentrated in `bot.py`; services
  expose logic, not routes.
- **Web/admin:** 17 Jinja templates in `templates/` (dashboards + admin consoles) and PWA assets
  in `static/` (`service-worker.js`, `manifest.json`, `offline.html`). **No separate SPA
  frontend.**
- **Native:** `mobile-native/src/` — 43 TS api modules (incl. `ads.ts`, `marketplace.ts`,
  `premium.ts`, `alerts.ts`, `orders.ts`), 52 screens, feature dirs (`feed/`, `live/`, `reels/`,
  `calls/`, `undx/`). **No WebView anywhere** (`react-native-webview` = 0 matches) — business
  surfaces are native API clients, satisfying the mission's native-first rule.

---

## 11. Duplicate / dead implementations

- **Dead legacy app:** `mobile/` is a parallel Expo app with an empty `mobile/src/` — superseded
  by `mobile-native/`. Safe-to-archive.
- **Dead legacy ad schema:** the `ad_*` table generation (`ad_campaigns`, `ad_creatives`,
  `ad_impressions`, `advertisers`, `brand_deals`, `sponsorships`) is 0-row and superseded by
  `pulse_ad_*`.
- **Duplicate ad services (4–5):** `pulse_ads_service`, `pulse_advertiser_portal`,
  `dashboard_ads_command_center`, `pulse_ad_payments`, `ad_policy_engine` — overlapping.
- **Duplicate market/crypto services (3+):** `market_data`, `market_service`,
  `live_market_service`, plus `dashboard_crypto_command_center` and ~20 `live_*` services.
- **Four parallel entitlement tables** (see §5) — reconciliation hazard.
- **Placeholder tables** never wired: `marketplace_orders_placeholder`,
  `creator_payouts_placeholder`, plus empty `pulsesoc_seller_products`/`pulsesoc_seller_stores`
  duplicating `marketplace_listings`.
- **Audit-script sprawl:** `scripts/` holds hundreds of one-off `*_audit.py` files. Note a
  **production coupling risk**: the UNDX kernel's `SAFE_VALIDATION_COMMANDS` hard-references some
  of these audit scripts, so prod validation depends on experiment scripts.
- **210 service modules** total — high fragmentation; many single-purpose engines.

---

## 12. Security & compliance risks

1. **No hardcoded secrets found.** All `sk-` grep hits are hyphenated slug false positives;
   secrets load via `os.getenv`. (Good.)
2. **Auth is session-based** (`webhook_app.secret_key`) with custom guards (`require_account` 151
   uses, `login_required` 42, `require_super_user_api` for admin/UNDX). With 1,252 routes on one
   app object, **unauthenticated sensitive routes must be audited individually** — some
   `@webhook_app.route`s lack a visible `require_account`.
3. **Rate limiting is in-memory** (`RATE_LIMIT_BUCKETS` dict) — **per-process**, so it does not
   survive restarts or work across multiple gunicorn workers. Weak under real deployment.
4. **Webhook processing** writes the event row as "received" before handlers finish and has **no
   reconciliation worker** — replay/mid-crash recovery gap (§4).
5. **Financial atomicity:** ledger write + balance update are not transactional (§7).
6. **Admin gaps:** ~41 unguarded-looking `/admin` routes; empty `ip_hash`; audit immutability not
   DB-enforced; no step-up auth / dual approval on high-risk money actions.
7. **UNDX write-agent surface:** an approved super-user directive can author arbitrary repo file
   *content*; `DEFAULT_REPOSITORY_PATH` is a hardcoded developer home path (non-portable); prod
   validation shells out to `scripts/*_audit.py` (tampering = code-exec vector, though
   allowlisted).
8. **Compliance not verified.** No PCI/KYC/AML/tax attestations exist and none should be claimed
   until reviewed by qualified legal, tax, security, and payments specialists. Raw card data is
   correctly **not** stored (Stripe-tokenized).

---

## 13. Proposed architecture

Keep the smallest durable structure that can scale without a rewrite. **Do not** add more logic
to `bot.py`; **do** carve bounded modules behind stable interfaces, strangler-fig style.

**Bounded modules (target `services/business_os/<domain>/`):** business identity, merchant
onboarding, catalog, inventory, cart, checkout, order, fulfillment, returns, review, advertising
account, campaign, creative, ad delivery, attribution, billing, **ledger**, payout,
**subscription/entitlement**, crypto market-data, crypto alert, portfolio, compliance, risk,
notification, analytics, **UNDX business-tool** services.

**Foundation-first principles.**
- **One canonical ledger.** Consolidate money movement onto a single immutable, integer-cents
  ledger with a UNIQUE idempotency key on *every* entry and **atomic** write+balance-derivation
  (single transaction or event-sourced projection). Migrate ad/creator/seller ledgers onto it or
  behind one interface. Never trust client-calculated amounts.
- **One entitlement service.** Collapse the four entitlement tables into one canonical
  entitlement ledger queried by every premium gate; keep the others as read-through views during
  migration.
- **Modular payment-provider interface** (already started in `payment_provider.py`) — formalize
  the capability set (customer / method / intent / capture / refund / connected account /
  onboarding / transfer / payout / subscription / webhook-verify / reconcile) so PulseSoc is not
  processor-locked.
- **Durable, verified, idempotent webhook ingestion** with a persisted inbox table + a
  **reconciliation worker** that replays unprocessed events and diffs against provider APIs. No
  client callback may declare a payment successful.
- **Domain events** (`OrderPaid`, `RefundIssued`, `PayoutCreated`, `CampaignApproved`,
  `BudgetExhausted`, `SubscriptionActivated`, `CryptoAlertTriggered`, …) — idempotent, traceable,
  persisted; drive notifications/analytics off events, not inline writes.
- **Route de-monolithing:** introduce Flask **blueprints per domain** and move new business
  routes there; leave existing routes in place behind the same app until incrementally migrated.
- **Unified Business Area** in `mobile-native/` — one role-adaptive home that composes existing
  native api clients (`ads`, `marketplace`, `premium`, `alerts`, `orders`) plus new order/payout
  clients, with server-driven role/entitlement gating (no client-trusted unlocks).
- **UNDX business-tool layer:** a **versioned tool registry** (name, input schema, authz,
  confirmation requirement, risk level, idempotency, canonical-verification method, reversibility,
  audit requirement). Financial/destructive tools require explicit confirmation and
  **verify against canonical backend state before reporting success.**

**Cross-cutting (every domain):** server-side validation, RBAC, audit entries, analytics/
observability, notifications, error/empty/loading/retry/offline states, fraud/abuse controls,
feature flags, and tests — as acceptance criteria, not add-ons.

---

## 14. Proposed database migration plan

The repo currently creates schema **inline via `CREATE TABLE IF NOT EXISTS` in `bot.py`** with
only 8 hand-written `migrations/*.sql`. Before scaling money features:

1. **Adopt versioned, ordered migrations** (numbered SQL under `migrations/business_os/`, applied
   by a runner recorded in a `schema_migrations` table). Freeze new inline `CREATE TABLE` in
   `bot.py` for business tables.
2. **Ledger consolidation migration** — introduce the canonical `ledger_entries` table (UNIQUE
   idempotency key, integer cents, immutable), backfill from `creator_ledger_entries` /
   `pulse_ad_wallet_transactions` / `fee_ledger`, and add derived-balance projections. Keep old
   tables as read-only during transition; add a reconciliation job.
3. **Entitlement consolidation** — canonical `entitlements` table; backfill from the four existing
   tables; repoint `get_user_entitlements` to it; retain old tables as views.
4. **Order state machine** — promote `marketplace_orders_placeholder` to a real `orders` table
   with an explicit status column + `order_events` audit, plus `order_line_items`,
   `fulfillments`, `shipments`, `returns`, `refunds`. Migrate existing `seller_transactions` into
   order+payment rows.
5. **Payout execution** — populate/activate `seller_payouts`/`platform_payouts` with a payout
   worker; add `payout_events`.
6. **Webhook inbox** — `provider_webhook_events` inbox with status + retry columns and a UNIQUE
   provider-event-id; migrate current dedup onto `INSERT OR IGNORE` + explicit state.
7. **Constraints & types** — add UNIQUE idempotency constraints to all ledgers; migrate legacy
   `transactions.amount` REAL → integer cents.
8. **Every migration ships with a rollback script** and is tested against a copy of
   `coinpilotx.db` before deploy. (Production DB engine/target to be confirmed — dev is SQLite.)

---

## 15. Proposed implementation sequence

Map to the mission's staging; **build the shared foundation before product surfaces.**

- **Stage 1 — Shared foundation:** canonical ledger (atomic, idempotent) + entitlement service +
  payment-provider interface hardening + verified idempotent webhook inbox & reconciliation worker
  + domain-event bus + audit/RBAC hardening + unified Business Area shell (role-adaptive, native) +
  feature-flag + notification plumbing. *Refactor over rewrite — wrap existing ad/premium ledgers.*
- **Stage 2 — Advertising MVP hardening:** it is already the most mature; close gaps
  (auction/CPC clearing, targeting, fraud filtering, native advertiser UI, invoices/receipts,
  review/appeal workflow) on top of the consolidated ledger.
- **Stage 3 — Marketplace MVP:** real order state machine, physical vs digital fulfillment,
  payout execution, refunds/returns/disputes, inventory decrement, reviews; locked-but-attractive
  pre-approval experience.
- **Stage 4 — Premium:** single entitlement ledger + **server-side IAP receipt validation**
  (Apple/Google) to unblock iOS monetization + full lifecycle (trial/upgrade/downgrade/grace/
  restore) cross-device.
- **Stage 5 — Crypto intelligence:** keep informational-only; add cost-basis/P&L, unify the three
  market services, formalize durable alerts. **No custody/trading without separate approval.**
- **Stage 6 — Advanced:** attribution, recommendations, merchant automation, creator commerce,
  governed UNDX business actions, localization, performance.

---

## 16. Exact first implementation slice

**Slice: the canonical financial ledger + idempotent webhook inbox — the load-bearing
foundation everything else depends on.** Scoped to be small, durable, and independently testable
without touching UI.

Deliverables:
1. `services/business_os/ledger/ledger.py` — one interface: `post_entry(idempotency_key, actor,
   amount_cents, currency, entry_type, source, destination, reason, related_object, metadata)`
   writing an **immutable** row and deriving balances **atomically** (single transaction). UNIQUE
   idempotency key enforced at the DB level.
2. Migration `migrations/business_os/0001_ledger_entries.sql` (+ rollback) creating
   `ledger_entries` and `ledger_balances`, with a read-through shim so existing ad/creator/seller
   balance reads keep working.
3. `services/business_os/payments/webhook_inbox.py` + migration `0002_provider_webhook_events.sql`
   — persist-before-process inbox (UNIQUE provider event id, status, retry_count) and a
   `reconcile_pending()` routine; repoint `stripe_webhook()` to enqueue then process.
4. **Tests (must pass before any PASS claim):** duplicate submission is a no-op; concurrent
   double-post yields one entry; webhook replay/out-of-order/delayed are idempotent; balance =
   sum(entries) after N randomized ops; mid-processing crash leaves the event replayable;
   unauthorized ledger write rejected.
5. **Feature-flagged** and wired behind existing code paths without removing them (strangler
   pattern), so rollback is a flag flip.

**Why this first:** advertising billing, marketplace payouts, refunds, subscriptions, and any
future crypto payments all require a trustworthy, idempotent, atomic ledger. Fixing the two
concrete foundation defects found in this inventory — non-atomic ledger writes (§7) and
received-but-unprocessed webhooks (§4) — de-risks every subsequent stage.

---

## Evidence status & honesty note

This report is a **code inventory**, not a validation of running behavior. Nothing here is marked
PASS as a working end-to-end business flow; domain verdicts describe **code/data maturity**
observed by reading files and the live schema. Per the mission's rules, a feature will only be
called PASS with backend persistence, correct permissions, real data, successful primary + failure
flows, automated regression coverage, device observation, and deployment verification. Runtime,
audible, and on-device validation remain owner-side on the Mac (see the companion device-
observation report). Compliance (PCI/KYC/AML/tax) is **not** asserted and requires qualified
specialist review before any financial release.
