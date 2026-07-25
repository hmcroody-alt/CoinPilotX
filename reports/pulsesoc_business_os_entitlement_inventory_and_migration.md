# PulseSoc Business OS — Entitlement Inventory & Migration Plan

Stage 1 foundation, entitlement consolidation slice. Prepared 2026-07-24 on
branch `release/undx-nexus-core-v4`.

Status vocabulary used throughout: **PASS / PARTIAL / BLOCKED / NOT TESTED**.

This document does step 1 (inventory) and step 2 (canonical model + precedence)
of the entitlement-consolidation mission. It is the reference the new
`services/business_os/entitlements/` service and its compatibility facade are
built against. **No legacy table is dropped or rewritten**; the plan is strictly
additive with a facade in front.

---

## 0. Executive summary

"Is this user premium?" is currently answered by **at least five simultaneous
sources of truth** with **three parallel entitlement tables**, **three different
"is premium" functions with divergent logic**, a **twice-defined `subscriptions`
table**, and denormalized paid-state columns spread across the `users` row. The
divergence is not theoretical: a Stripe-activated user (`users.plan='pro'`) is
recognized by `pro_access.has_pro_access()` but **not** by
`premium_visibility_engine.is_premium_user()` (whose plan allowlist is
`{"pulse-premium","premium","creator-pro"}` and never matches `"pro"`), so which
answer a feature sees depends on which function that feature happens to call.

The consolidation introduces one canonical grant store and a single
`has_entitlement(subject, key)` decision function with an explicit, documented
precedence order, placed **behind a compatibility facade** that falls back to the
legacy readers during migration and can run in **shadow mode** (compare
legacy vs canonical, log the diff, never change what the user sees) before any
cutover.

---

## 1. Inventory of existing entitlement systems

### 1.1 The "is premium / is pro" decision functions (readers)

| Function | File:line | Reads from | Logic |
|---|---|---|---|
| `pro_access.pro_access_type` / `has_pro_access` | `services/pro_access.py:37,54` | `users.plan`, `subscription_plan`, `subscription_status`, `trial_status`, `trial_end_date`, `pro_expires_at`, `subscription_expires_at` | paid if `plan=='pro' and status=='active'`; trial via `trial_status`/`trialing` |
| `premium_visibility_engine.is_premium_user` | `services/premium_visibility_engine.py:25` | `users.lifetime_premium`, `premium_glow_manual_grant`, `premium_status`, `subscription_plan`, `subscription_status` | premium if lifetime/manual grant, or `premium_status in {active,founder,lifetime,trial}`, or plan in `{pulse-premium,premium,creator-pro}` + status in `{active,trialing}` |
| `premium_entitlement_service.is_premium_user` | `services/premium_entitlement_service.py:740` | `user_entitlements`, `premium_entitlements`, `founder_memberships`, then `users` columns | entitlement-table first, founder second, users-columns fallback |
| `bot.has_pro_access` (wrapper) | `bot.py:4146` | delegates to `pro_access` | — |
| `bot.platform_pro_access` | `bot.py:4158` | returns `bool(user)` | **overrides all paid checks — any logged-in user "passes"**; used for core-free features |
| `billing_service.has_billable_pro_access` | `services/billing_service.py:15` | delegates to `pro_access` | `VALID_PRO_STATUSES={active,trialing}` |

**Readers of the entitlement tables specifically:** `has_entitlement(user_id,key)`
(`premium_entitlement_service.py:430`) reads `user_entitlements` first, then
`premium_entitlements`; it is **time-aware** via `_active_window()`
(`:419`). It never reads `pulse_premium_entitlements`.

### 1.2 Access/paid-state tables (the data)

Primary carrier is the **`users`** row (`bot.py:762` minimal, `bot.py:91732`
extended; paid columns added by `add_columns_if_missing` at `bot.py:91752-91850`):
`is_pro`, `pro_active`, `plan`, `subscription_plan`, `subscription_status`,
`subscription_started_at`, `subscription_expires_at`, `pro_expires_at`,
`trial_start_date`, `trial_end_date`, `trial_status`, `trial_used`,
`premium_status`, `premium_expires_at`, `lifetime_premium`,
`premium_glow_manual_grant`, `premium_mark_override`, `premium_mark_type`,
`stripe_customer_id`, `stripe_subscription_id`, `provider_customer_id`,
`provider_subscription_id`, `payment_provider`, `last_payment_status`.

Dedicated tables:

| Table | CREATE at | Role | Notable columns |
|---|---|---|---|
| `subscriptions` | `bot.py:91498` **and** `bot.py:98113` (two incompatible schemas) | Stripe sub records | v1: `plan_key`; v2: `plan`; both `status`, `current_period_end` |
| `subscription_plans` | `premium_entitlement_service.py:83` | Plan catalog | `plan_key UNIQUE`, `price_cents`, `billing_interval`, `status` |
| `user_subscriptions` | `premium_entitlement_service.py:101` | Per-user sub (Stripe) | `UNIQUE(user_id,plan_key)`, `status`, `current_period_end`, `cancel_at_period_end`, `canceled_at` |
| `user_entitlements` | `premium_entitlement_service.py:161` | **canonical-ish** entitlement | `UNIQUE(user_id,entitlement_key)`, `status`, `source`, `starts_at`, `expires_at` |
| `premium_entitlements` | `bot.py:91484,94382` | parallel entitlement | `status`, `source`, `starts_at`, **`ends_at`** (name differs) |
| `pulse_premium_entitlements` | `bot.py:95096` | third entitlement | `UNIQUE(user_id,entitlement_key)`, `status`, `granted_by`, `expires_at` — **written but never read by `has_entitlement`** |
| `founder_memberships` | `premium_entitlement_service.py:178` | founder/grandfathered price lock | `founder_number UNIQUE`, `locked_price`, `status` |
| `feature_flags` | `bot.py:96028` | flag table | `state`, `rollout_percentage`, `premium_required`, `owner_only`, `internal_only` |
| `pulse_premium_feature_flags` | `bot.py:95134` | boolean premium flags | `flag_key UNIQUE`, `enabled` |
| `stripe_events` | `premium_entitlement_service.py:143` | Stripe event log | `stripe_event_id UNIQUE`, `event_id UNIQUE`, `status` |
| `payment_webhook_events` | `bot.py:91513,94396` | webhook dedup | `provider_event_id UNIQUE`, `status` |
| `promo_codes` | `bot.py:98173` | promotional access | `code UNIQUE`, `reward_days`, `max_redemptions`, `active` |
| `referral_rewards` | `bot.py:98157` | referral grants | `reward_type`, `reward_days`, `status` |

### 1.3 Writers (who flips paid state)

- **`activate_pro()`** (`bot.py:102910`) — canonical Stripe activation:
  `UPDATE users SET is_pro=1, pro_active=?, subscription_plan='pro', plan='pro',
  subscription_status=?, ... pro_expires_at=?, subscription_expires_at=?` and
  inserts into `subscriptions`. Called from `checkout.session.completed`
  (`:86899`), `payment_intent.succeeded` (`:87125`), `sync_stripe_subscription`
  (`:102786`), `sync_stripe_invoice` (`:102874`).
- **`sync_stripe_subscription()`** (`bot.py:~102730`) — `customer.subscription.*`;
  active/trialing → `activate_pro`; otherwise `subscription_status=?, pro_active=0,
  is_pro=0`.
- **invoice.payment_failed** (`bot.py:102891`) — `subscription_status='past_due'`.
- **Admin manual grants** — `bot.py:14446`, `17458`, `17521`, `17610`
  (plan=pro/active/is_pro=1); `grant_pulse_premium()` (`bot.py:67871`,
  premium_status='active', lifetime_premium=1, glow/manual grant);
  lifetime grants (`bot.py:79456`, `79697`, `98074`).
- **Founder** — `grant_founder_membership()`
  (`premium_entitlement_service.py:575`) writes `founder_memberships`,
  `user_subscriptions`, `premium_badges`, `pulse_user_badges`,
  `founder_wall_entries`, the `users` row, **and** `grant_entitlement()` per
  `FOUNDER_ENTITLEMENTS` (which hits all three entitlement tables).
- **Trials** — start `bot.py:11708`, convert `:11681`, revoke `:11695`;
  `expire_trials()` (`bot.py:~88590`) sets `subscription_status='expired',
  is_pro=0`.
- **`grant_entitlement()`** (`premium_entitlement_service.py:459`) writes to
  **all three** of `premium_entitlements`, `user_entitlements`,
  `pulse_premium_entitlements`. `revoke_entitlement()` (`:507`) revokes across the
  three.

### 1.4 Status values and their meaning

`active`, `trialing`, `inactive`, `expired`, `canceled`/`cancelled`, `past_due`,
`unpaid`, `incomplete_expired` (subscription lifecycle);
`founder`, `lifetime`, `trial` (premium_status identity);
`revoked` (entitlement rows); `ios_core_only` (synthetic, client-payload only).
`VALID_PRO_STATUSES = {active, trialing}` (`billing_service.py:12`).
There is **no explicit `grandfathered` status** in code — the founder locked-price
concept fills that role but stores `status='active'`.

### 1.5 Expiration / cancellation / renewal / grace / revocation

- **Expiration** — trials expire in `expire_trials()` (instant on detection, **no
  grace window**). `pro_access._access_not_expired()` exists but is **not called**
  for paid users; paid expiry relies on the Stripe webhook flipping
  `subscription_status`. `_active_window()` (entitlement path) is the only
  time-aware check that gates on `starts_at`/`expires_at`.
- **Cancellation** — `cancel_at_period_end` on `user_subscriptions`; surfaced to
  client via `subscription_status_payload()` (`bot.py:10317`) as `canceling`.
  Access is expected to persist until period end.
- **Renewal** — driven by Stripe `invoice.paid` → `activate_pro()` extends
  `pro_expires_at`.
- **Grace period** — **none implemented.**
- **Revocation** — `revoke_premium_access()`
  (`premium_entitlement_service.py:680`) is the full teardown across founder,
  badges, all subscription/entitlement tables, and `users` columns.
  `revoke_entitlement()` is the per-key version.

### 1.6 App Store / Play Store receipt state

iOS in-app purchase is **intentionally blocked, not implemented**.
`ios_paid_digital_unavailable_response()` (`bot.py:4293`) returns
`{ok:false, iap_required:true, ios_core_only:true}` (HTTP 403) for any iOS-native
paid-digital request. `subscription_status_payload` masks all paid fields to
`ios_core_only` for iOS (`bot.py:10332`, `27475-27500`).
`/api/payments/entitlements` returns `[]` on iOS (`bot.py:77103`).
Native `openPremiumUrl()` (`mobile-native/src/api/premium.ts:205`) returns
`native_provider_boundary` and does not complete a purchase.
`DIGITAL_COMMERCE_ENABLED` (`mobile-native/src/api/config.ts:15`) defaults off.
`services/providers/inapp_provider.py` is a **no-op stub** — no StoreKit / Play
Billing. **Provider verification for Apple/Google: NOT IMPLEMENTED (BLOCKED on
real StoreKit/Play integration).**

### 1.7 Feature flags

Three layers: code-level `FEATURE_DEFINITIONS`
(`services/feature_flag_engine.py:16`, states incl. `premium-only`,
`owner-only`, `internal-only`); DB `feature_flags` (`bot.py:96028`, has
`premium_required`); DB `pulse_premium_feature_flags` (`bot.py:95134`, boolean).
`evaluate_flag()` (`:259`) reads a passed-in `user.is_premium` field — it does not
itself resolve entitlement, so it inherits whatever the caller computed.

### 1.8 User-facing dependencies (client readers)

Routes returning paid state to the app: `/api/subscriptions/status`
(`bot.py:10822`), `/api/premium/status` (`:10732`), `/api/payments/entitlements`
(`:77095`), `/api/account/status` dashboard payload (`:~27456`), plus
upgrade/downgrade/cancel/resume and `/api/premium/checkout|billing-portal`.
Central aggregator: `subscription_status_payload()` (`bot.py:10305`).
Native: `mobile-native/src/api/premium.ts`, `screens/PremiumScreen.tsx`,
`session/sessionStore.ts:151` (caches `premium_status`),
`normalizePremiumStatus()` (`premium.ts:113`).

### 1.9 Duplicate / conflicting sources of truth (the core problem)

1. **Three entitlement tables** (`user_entitlements`, `premium_entitlements`,
   `pulse_premium_entitlements`) with a column-name mismatch
   (`expires_at` vs `ends_at`) and one table written-but-never-read.
2. **Three "is premium" functions** with divergent plan allowlists.
3. **`subscriptions` created twice** with incompatible columns (`plan_key` vs
   `plan`).
4. **`users.plan` vs `subscription_plan` vs `premium_status`** can disagree; the
   `"pro"` vs `{pulse-premium,premium,creator-pro}` mismatch is a live bug.
5. **`users.stripe_subscription_id` vs `user_subscriptions.stripe_subscription_id`**
   can drift.
6. **`platform_pro_access()` returns True for everyone** and is used on some gates
   but not others.

### 1.10 Production data that MUST be preserved

Every row in `users` paid columns, `user_subscriptions`, `subscriptions`,
`user_entitlements`, `premium_entitlements`, `pulse_premium_entitlements`,
`founder_memberships`, `subscription_plans`, `promo_codes`, `referral_rewards`,
`stripe_events`, `payment_webhook_events`. The migration reads these; it does not
UPDATE or DROP them. Founder rows in particular encode locked pricing and public
wall placement and are irreplaceable.

---

## 2. Canonical entitlement model (additive, new tables only)

New namespace `business_os_ent_*`, engine-portable via `services.db`, never
touching legacy tables.

**`business_os_ent_products`** (catalog): `product_key` (PK), `name`,
`category` (premium|business|marketplace|advertising|creator|crypto),
`status`, `metadata_json`, timestamps. Seeded: `pulsesoc_premium`,
`pulsesoc_premium_business`, `merchant_marketplace`, `advertiser_portal`,
`creator_pro`, `crypto_intelligence_pro`.

**`business_os_ent_plans`**: `plan_key` (PK), `product_key` (FK),
`plan_type` (monthly|annual|trial|promotional|lifetime|staff|grandfathered),
`price_cents`, `currency`, `billing_interval`, `status`, `metadata_json`.

**`business_os_ent_catalog`** (which entitlement keys + limits a plan confers):
`plan_key`, `entitlement_key`, `limit_value` (nullable = boolean/unlimited),
`limit_period` (nullable: day|month|cycle), PK `(plan_key, entitlement_key)`.
Entitlement keys are dotted, e.g. `premium.profile.customization`,
`premium.media.higher_quality`, `premium.undx.advanced`,
`business.team_members`, `business.analytics.advanced`,
`marketplace.sell.physical`, `marketplace.sell.digital`,
`advertising.campaign.create`, `advertising.analytics.advanced`,
`crypto.alerts.advanced`.

**`business_os_ent_grants`** (the load-bearing table): `id` PK, `subject_type`
(user|business), `subject_id`, `entitlement_key`, `source`
(stripe|apple_app_store|google_play|admin|promotion|trial|merchant_approval|business_role|legacy_migration|feature_flag|internal_testing),
`source_reference`, `status`
(active|expired|suspended|pending|revoked|grandfathered), `starts_at`,
`expires_at`, `grace_until`, `limit_value`, `limit_period`, `region`, `platform`,
`revocation_reason`, `created_by`, `audit_reference`, `metadata_json`,
`created_at`, `updated_at`. Idempotency: `UNIQUE(subject_type, subject_id,
entitlement_key, source, source_reference)` so a replayed provider event or admin
action is a no-op.

**`business_os_ent_usage`** (quota counters): `subject_type`, `subject_id`,
`entitlement_key`, `period_key` (e.g. `2026-07` or `cycle:<ref>`), `used`,
`updated_at`, PK on the first four. Atomic increment guarded by the DB.

**`business_os_ent_audit`**: append-only `id`, `subject_type`, `subject_id`,
`entitlement_key`, `action` (grant|revoke|suspend|extend|consume|reconcile|
shadow_diff), `actor`, `reason`, `before_json`, `after_json`, `created_at`.

**`business_os_ent_provider_subs`**: normalized provider subscription state
(`provider`, `provider_subscription_id UNIQUE`, `subject_id`, `plan_key`,
`status`, `current_period_end`, `cancel_at_period_end`, `raw_json`) — the landing
zone for Stripe/Apple/Google adapters, deduped and reconcilable.

---

## 3. Precedence (explicit, documented, encoded in the service)

`has_entitlement()` resolves in this fixed order; the first matching rule wins:

1. **Security / compliance suspension** — any `status='suspended'` grant for the
   key (or a global subject suspension) → **DENY**.
2. **Explicit revocation** — `status='revoked'` and no later active grant → DENY.
3. **Active canonical grant** — `status='active'`, `starts_at<=now`, and
   (`expires_at` null or `>now`) → ALLOW.
4. **Grace-period grant** — expired but `grace_until>now` → ALLOW (flagged
   `grace`).
5. **Grandfathered grant** — `status='grandfathered'` (founder/legacy locked) →
   ALLOW.
6. **Valid legacy fallback during migration** — facade consults
   `premium_entitlement_service.has_entitlement` / the legacy readers; if they say
   yes and canonical is silent → ALLOW (flagged `legacy_fallback`, logged).
7. **No access** — DENY.

Rules: feature flags do **not** bypass billing/merchant approval unless the grant
`source='internal_testing'`. Merchant approval grants only `marketplace.*` keys,
never `premium.*`. Subscription cancellation keeps access until `expires_at`/period
end unless a provider confirms immediate refund/revocation (which sets
`status='revoked'` with `source_reference` = the refund event).

---

## 4. Compatibility facade + shadow mode

`entitlements/facade.py`:
- `check(subject, key, context)` → queries canonical first; on canonical-silent,
  falls back to the mapped legacy reader; emits an observability record when
  fallback was used; detects legacy-vs-canonical conflict.
- `shadow_compare(subject, key)` → returns `{legacy, canonical, differs,
  intended_winner, migration_action}` and writes a `shadow_diff` audit row.
  **Never changes user-visible access.**
- A module-level mode switch: `off` (legacy only — default, zero behavior
  change), `shadow` (serve legacy, record canonical + diff), `canonical` (serve
  canonical with legacy fallback). Controlled by env `BUSINESS_OS_ENTITLEMENTS`.

Legacy key mapping table lives in `facade.py`: e.g.
`premium.profile.customization` ↔ legacy `is_premium_user` truthiness for the
first slice.

---

## 5. Provider integration boundaries

`entitlements/providers/`:
- `base.py` — `SubscriptionProviderAdapter` interface:
  `verify_event`, `normalize`, `map_product_to_plan`, `to_grants`.
- `stripe_adapter.py` — maps normalized Stripe subscription/checkout state to
  plan + grants (server-side amounts only; ties into the Stage-1 webhook inbox).
- `apple_adapter.py`, `google_adapter.py` — **interfaces + persistence only**.
  They raise `ProviderNotConfigured` rather than fabricating success, matching the
  real state (StoreKit/Play Billing not implemented — **BLOCKED**).

All provider events land in `business_os_ent_provider_subs` (deduped by
`provider_subscription_id` + event id via the existing webhook inbox), support
out-of-order via status/period comparison, and are reconciled by
`reconcile_entitlements()`.

---

## 6. Migration risks

- **Divergent legacy readers** mean a naive cutover could grant or revoke access
  for real users; mitigated by shadow mode + legacy fallback + slice-by-slice
  cutover.
- **Column-name mismatch** (`ends_at` vs `expires_at`) in legacy tables — the
  facade's legacy adapter normalizes both.
- **Founder rows** encode irreplaceable pricing/wall state — read-only in this
  slice.
- **iOS IAP is blocked** — Apple/Google grant sources cannot be exercised
  end-to-end until StoreKit/Play Billing exist (**BLOCKED**, interfaces only).
- **`platform_pro_access()` universal-true** — out of scope for this slice; noted
  so cutover of a gated feature does not accidentally rely on it.

---

## 7. First vertical slice (step 9)

Selected capability: **`premium.profile.customization`** (advanced profile
customization) — nonfinancial, reversible, already a Premium concept, safe to
cut over first. Wiring: seed product `pulsesoc_premium` + plan + catalog row →
canonical grant → server-side `has_entitlement` check exposed via a read endpoint
→ native lock/unlock reads that flag → expiration/revocation honored → owner admin
inspection + audit → tests → legacy fallback for users who are premium only in the
legacy tables. Native device verification is **owner-side (NOT TESTED here)**;
this environment has no simulator/device.

---

## 8. Rollback strategy

All new tables are `business_os_ent_*` with paired `.down.sql` migrations that
`DROP` only those tables. The feature is gated by `BUSINESS_OS_ENTITLEMENTS`
(default `off` = legacy behavior unchanged). Disabling the flag or running the
down-migrations returns the system to exactly the pre-slice state; no legacy row
is ever mutated by this slice.

---
---

# EXPANSION — Steps 10–14: capability access, suspension precedence, quotas, cache

Prepared 2026-07-24. This part widens the inventory beyond explicit "Premium /
subscription" terminology. Several PulseSoc capabilities already **behave as
entitlements without being named entitlements** (merchant approval, creator
status, trust ladder, ad-account roles, moderation holds). Every finding below
carries `file:line` evidence and was spot-verified against source. **This is
inventory only — no schema was designed or changed here. Findings are presented
for review so the safest first cutover is chosen from evidence, not assumption.**

## 9-bis. Executive summary of the expansion

Three findings dominate and should drive the migration order:

1. **Authorization is spread across ~9 distinct decision styles, only some of
   which are commercial entitlements.** Misclassifying all of them as "paid
   entitlement" would be wrong: merchant/creator/teacher gates are *approvals*,
   the trust ladder is a *role/capability* system, iOS paid-digital blocking is a
   *compliance* rule, and login/fraud locks are *risk* restrictions. The canonical
   service must model at least: commercial entitlement, role permission,
   merchant/business approval, compliance eligibility, risk restriction, feature
   rollout, usage quota, admin override.

2. **Suspension does not consistently override paid access (the highest-severity
   conflict).** `pro_access.pro_access_type()` denies when
   `users.account_status != 'active'` (`services/pro_access.py:38-41`), but the
   three functions that actually gate live Premium features —
   `premium_visibility_engine.is_premium_user` (`:25`),
   `premium_entitlement_service.is_premium_user` (`:740`),
   `premium_identity_engine.has_active_premium` (`:40`) — **never read
   `account_status`, `deleted_at`, or `access_enabled`** (verified: grep of all
   three files returns zero matches). Suspending an account sets
   `access_enabled=0`/`login_enabled=0` (`bot.py:11653`) which blocks *login*, but
   does not clear `premium_status`/`lifetime_premium`, so a still-valid
   session/token hitting a premium route passes the suspension-blind check.

3. **The mobile premium cache is never invalidated.** `pulsesoc.native.premium.status`
   (`mobile-native/src/api/premium.ts:5,72,76`) is written on fetch and read as an
   **offline fallback** (`screens/PremiumScreen.tsx:36-45`) but is not cleared on
   logout, revocation, cancellation, refund, or suspension (verified: the key is
   only read/written; it is absent from the logout clear paths in
   `session/auth.ts` and `session/sessionStore.ts`). Server routes re-read the DB
   every request, so this is a **stale-display / client-gate-bypass** exposure, not
   a server capability bypass — but it is unbounded in time.

The good news: the new `business_os_ent_*` service already encodes suspension as
the top precedence rule (`service._resolve` rule 1) and already has an atomic
metered-quota engine. The missing pieces are **bridges** — projecting
account-level holds into the grant model, and wiring the quota engine to real
callers.

## 10. Access-decision taxonomy (capability-based gates)

Classified into the 9 required categories. All are **server-authoritative**;
the mobile client consumes server-decided flags for display and never
independently authorizes.

### (1) Commercial entitlement
- `services/pro_access.py:37-56` — `pro_access_type` / `has_pro_access` (reads
  `users.plan`, `subscription_status`, `trial_status`, `pro_expires_at`).
- `services/premium_visibility_engine.py:25`, `services/premium_entitlement_service.py:740`,
  `services/premium_identity_engine.py:40` — the three divergent premium readers.
- `bot.py:4146` `has_pro_access` wrapper; `bot.py:4158` `platform_pro_access`
  (returns `bool(user)` — everyone passes; core tools are free by design).
- `services/business_os/entitlements/service.py:225` `has_entitlement` — the new
  canonical core (flag-gated).

### (2) Role permission
- `bot.py:15890` `ROLE_FALLBACK_PERMISSIONS` + `:15919` `admin_has_permission`;
  guards `require_admin_api` (`:16006`), `require_owner_api` (`:15984`),
  `require_admin_page` (`:15973`); backed by `role_permissions` table (`:15933`).
- `bot.py:3977-4062` owner/super-user gates (`user_is_owner_account`,
  `require_super_user_api`); `admin_is_owner_level` (`:12785`).
- `services/pulse_advertiser_portal.py:62,85,566` — ad-account team roles
  (`pulse_ad_team_members.role`, `status='active'`; `WRITE_ROLES` gate campaign
  writes).
- `services/privilege_engine.py:52` `get_user_privileges` — trust-ladder matrix
  mapping trust-score level → ~30 `can_*` capabilities (`can_go_live`, `can_sell`,
  `can_teach`, `can_create_reels`, …); owner short-circuits all True (`:54`).

### (3) Merchant / business approval
- `bot.py:76622` `approved_marketplace_seller_for_user` — seller only if
  `marketplace_sellers.status=='approved'`; enforced on listing edit/pause/delete
  (`bot.py:43266-43269,43361`).
- `bot.py:76629` `approved_teacher_for_user` — `pulse_teacher_profiles.status=='approved'`
  gate for teacher payouts (`:43673`).
- `bot.py:35836` `pulse_verification_types_for_user` — reads
  `verification_requests` where `status='approved'`, feeds seller/teacher badges
  into the privilege ladder (`:35873`).
- Public visibility filters on `marketplace_listings.approval_status`
  (`approved/review_ready`) at `bot.py:31451,42693,42875`; advertiser creative
  `moderation_status=='approved'` (`pulse_advertiser_portal.py:389,709`).

### (4) Compliance eligibility
- `bot.py:4859` `create_account` age/country capture; signup blocked unless
  `age_confirmed` (`:5402,5750`).
- Login blocked when `email_verified==0` (`bot.py:5487,5672-5675`).
- iOS paid-digital boundary — `pro_locked_response` / `ios_native_app_request`
  (`bot.py:4213`, `ios_paid_digital_unavailable_response` `:4293`): Apple-IAP
  compliance, blocks paid digital in the iOS build.
- `bot.py:3330` `account_login_restriction_message` — pre-launch/jurisdiction gate
  on `account_status=='restricted'` / `login_enabled==0`.

### (5) Risk restriction
- `services/business_os/entitlements/service.py:509` `suspend_entitlement` — top
  precedence DENY (security/compliance hold).
- Livestream denial when `livestream_status=='suspended'`, `trust_score<50`, or
  `can_go_live` false (`bot.py:38486-38496`).
- `bot.py:2451-2466` kill-switch / `security_state=feature_restricted`;
  failed-login lockouts (`bot.py:4801-4854`, IP/email/domain thresholds).
- `marketplace_sellers.risk_score` tracked at seller creation (`bot.py:43615`).

### (6) Feature rollout
- `services/feature_flag_engine.py:13,259` — `FEATURE_DEFINITIONS` with states
  `enabled/disabled/beta/internal-only/premium-only/owner-only`;
  `rollout_percentage` (`:248,306`). Reels/Live/AI = beta;
  `marketplace_checkout` = internal-only.
- `services/undx_policy.py:90` `v5_user_enabled` — QA cohort gate: requires
  `UNDX_V5_ENABLED` flag AND `user_id in UNDX_V5_QA_USER_IDS` allowlist (`:94`).

### (7) Usage quota — see §12.
### (8) Administrative override
- `bot.py:17697/17743` owner-only flag-gated entitlement grant/revoke (source
  `admin`, audited in both entitlement + admin audit tables).
- `bot.py:38463-38485` owner short-circuit auto-approves livestream access;
  `privilege_engine.get_user_privileges:54` owner grants all caps.

### (9) Legacy ad-hoc access check
- `services/pulse_dashboard_mission_control.py:125-158` — inline composite gate
  computing `is_admin/moderator/premium/creator/seller/verified` from mixed
  sources (`creator_mode`, `account_type`, post counts, `seller_status`,
  `email_verified`, `verification_status`) → widget flags. A parallel,
  uncoordinated capability composition next to the canonical engine.
- `bot.py:4146-4162` legacy `has_pro_access`/`platform_pro_access` co-existing
  with the new facade.

## 11. Suspension & restriction precedence (override states)

Authoritative override states and where they live:

| Override state | Carrier | Set at | Scope |
|---|---|---|---|
| Account restricted/suspended/banned/deleted | `users.account_status` (`active/restricted/suspended/deleted`) | `bot.py:11641-11655,11898,17393-17431` | whole account (intended) |
| Login / access kill switch | `users.login_enabled`, `users.access_enabled` | `bot.py:11649-11655` | login only |
| Soft delete | `users.deleted_at` | `bot.py:5037-5073,11649` | feed rank only |
| Suspension reasons | `users.restricted_reason`, `suspended_reason` | `bot.py:11644-11655` | metadata |
| Ad-account suspension | `pulse_ad_accounts.status='suspended'` | `bot.py:16961` | ad account |
| Ad-campaign suspension | `pulse_ads_service.suspend_campaign` | `bot.py:16860,16936` | one campaign |
| Group suspension | `pulse_groups.status` (frozen/suspended/deleted) | checked `bot.py:35301,70148,70239` | one group |
| Livestream suspension | `livestream_access.status='suspended'` | `bot.py:37922,38487,81660` | live feature |
| Posting/messaging suspension | `user_privilege_profiles.moderation_status='suspended'`, `posting_restricted`, `messaging_restricted` | `bot.py:79907` | posting/messaging |
| Marketplace listing suspend | listing `status='suspended'` | `bot.py:81251-81252` | one listing |
| Payment delinquency | `users.subscription_status` in {past_due, unpaid, canceled} | read `bot.py:10320,11589,13194` | see conflict |
| Referral fraud flag | `referral_conversions.fraud_flag` | `bot.py:12523,35812` | referral counting |

**Consistency verdict: INCONSISTENT (the headline conflict).**
`pro_access.pro_access_type` (`services/pro_access.py:38-41`) honors
`account_status` and returns `"none"` when not active. But the three functions
that gate the *actual* live Premium routes do not:
`premium_visibility_engine.is_premium_user` gates
`/api/pulse/premium/identity-effects` (`bot.py:68105,68108`) and
`_profile_customization_allowed` (`bot.py:68140` — the current first-slice legacy
authority); `premium_identity_engine.user_has_premium_mark` gates lenses/camera
filters (`bot.py:42498,42644,85455`); `premium_entitlement_service.is_premium_user`
gates `bot.py:43919,10746`. None re-check suspension. Because suspending an
account does **not** cascade to clearing `premium_status`/`lifetime_premium`
(`revoke_premium_access` at `premium_entitlement_service.py:680` is a *separate*
admin action), a suspended-but-premium user still passes these gates through any
still-valid session/token.

**Canonical-service status:** `service._resolve` (`:191-212`) applies
"suspended grant → DENY" as rule 1, and `suspend_entitlement` (`:509`) exists —
but **nothing projects `users.account_status='suspended'`/`deleted_at`/`access_enabled=0`
into a suspended grant**, and `sync_subscription_entitlements` only ever writes
`STATUS_ACTIVE`. In `off`/`shadow` mode the facade serves the legacy
(suspension-blind) answer. **Required before any Premium cutover:** an
account-status precedence layer above grant resolution, plus a cascade from
account suspension into the entitlement model.

## 12. Usage & quota model inventory

| Mechanism | File:line | Storage | Reset | Atomic? | Enforced? | Plan-dep | Survives downgrade |
|---|---|---|---|---|---|---|---|
| Free AI daily limit (=5) | `bot.py:353`, `consume_ai_usage` `:88884` | `users.usage_ai_count`,`usage_reset_at` + `usage_events` | daily (local-time string compare) | **RACY** (SELECT→check→+1→UPDATE, no lock) | **LIVE** | binary (pro bypass) | counter persists |
| Business-OS metered quotas | `services/business_os/entitlements/usage.py:76` | `business_os_ent_usage` (PK 4-tuple) | day/month/cycle via `period_key` | **ATOMIC** (`BEGIN IMMEDIATE`) | **built, NOT wired** (test-only callers) | yes (catalog) | period-bucketed |
| — team members (=10) | `schema.py:catalog` | grants+usage | none | atomic | not wired | Business | — |
| — ad campaigns (=25/mo) | `schema.py:catalog` | grants+usage | month | atomic | not wired | advertiser | — |
| — crypto alerts (=50/day) | `schema.py:catalog` | grants+usage | day | atomic | not wired | crypto pro | — |
| HTTP rate limiter | `bot.py:398,2391-2404` | in-memory dict (per-process) | rolling window | racy + per-worker | live | flat by IP | n/a |
| Failed-login throttle | `bot.py:4378-4380,4801` | `failed_login_controls` | window | count≥threshold | live | no | n/a |
| Moderation caps | `user_privilege_profiles.posting_limit_per_day` etc. `bot.py:79576,97576` | table | n/a | n/a | **defined, NOT enforced** (no count comparison found) | trust-tier | — |
| Upload size caps | `bot.py:2543,37002,85028` | env constants | n/a | n/a | live | flat | n/a |
| Live guest limit | `GUEST_LIMIT_REACHED` `bot.py:39440` | live-room state | session | — | live | — | — |

Money balances (`pulse_ad_wallets`, `creator_ledger_entries`, the new
`business_os` ledger) are **financial ledgers, not usage quotas** — noted for
completeness, out of entitlement scope. **Key gap:** the only live per-user
quota (AI) is racy and non-tiered; the correct atomic tiered engine exists but
has zero production callers.

## 13. Cache & stale-state risks

Server side is authoritative and **un-cached**: `load_account_by_id`
(`bot.py:3816`) reads the `users` row fresh every call; `pro_access` /
`premium_entitlement_service.has_entitlement` (`:430`) query the DB per request;
no `lru_cache`/module dict holds premium state; the mobile access token carries
**only** `uid`/`dh`/`exp` (`bot.py:2737-2740`) — no premium/role claims, and every
request re-checks `mobile_security_sessions` for active/non-revoked. So server
enforcement reflects revocation immediately.

All staleness lives in **mobile client caches** (AsyncStorage, plaintext, no TTL):

| Cache | File:line | Key/field | Invalidation | Stale-after-revoke risk |
|---|---|---|---|---|
| Premium status | `api/premium.ts:5,72,76`; `core/cache.ts:14` | `pulsesoc.native.premium.status` (full `PremiumStatus`) | **NONE** — not cleared on logout or any event | **unbounded** — offline fallback (`PremiumScreen.tsx:36-45`) shows premium indefinitely; survives app restart + account switch |
| Session user | `session/sessionStore.ts:142-155` (`:151`) | `pulsesoc.native.session.user` → `premium_status`,`account_status` | cleared on logout (`auth.ts:228,254,285`); used as offline fallback (`auth.ts:273-281`) | window until next server round-trip |
| AuthContext | `session/auth.ts:72-84` | in-memory `user.premium_status` | until re-auth / app kill | same offline window; gates `LiveHostSessionScreen.tsx:419`, `MusicScreen.tsx:115` |

**Event-by-event client invalidation:** purchase, renewal, cancellation, refund,
revocation, suspension, merchant approval, merchant suspension, admin grant — the
server updates correctly for **all**; the client premium-status cache (#1) is
invalidated for **none** (only overwritten by a later *successful* status fetch).
The migration must add explicit client-cache invalidation on these events (and,
minimally, clear `pulsesoc.native.premium.status` on logout).

## 14. Deliverables

### Source-of-truth matrix

| Capability | Current authority | Secondary authority | Writers | Readers | Conflict risk | Migration destination |
|---|---|---|---|---|---|---|
| Premium (profile/media/UNDX) | `premium_visibility_engine.is_premium_user` | `pro_access`, `premium_entitlement_service`, `premium_identity_engine` | `activate_pro`, admin grants, founder, trials | premium routes, `subscription_status_payload` | **HIGH** (4 divergent readers; plan-allowlist mismatch `pro`≠`pulse-premium`; suspension-blind) | Commercial entitlement `premium.*` |
| Pro/paid tier | `pro_access.pro_access_type` | `users.plan/subscription_status` | Stripe webhooks | billing routes | MED (only reader honoring suspension) | Commercial entitlement |
| Marketplace selling | `marketplace_sellers.status='approved'` | `verification_requests` | admin approval | listing routes | MED (approval ≠ premium; must not confer premium) | Merchant approval `marketplace.*` |
| Teaching/payouts | `pulse_teacher_profiles.status='approved'` | verification | admin approval | payout routes | LOW | Merchant/creator approval |
| Advertiser | `pulse_ad_accounts.status` + team role | `pulse_ad_team_members` | admin, portal | campaign routes | MED (account suspension separate from role) | Role + approval `advertising.*` |
| Creator capabilities | `privilege_engine` trust ladder | `creator_mode`, mission_control composite | trust score, admin | live/sell/reels gates | MED (parallel ad-hoc composite) | Role permission + rollout |
| Admin/owner | `role_permissions` + owner-email | `ROLE_FALLBACK_PERMISSIONS` | admin UI | all admin routes | LOW | Role permission |
| AI usage | `consume_ai_usage` (`users.usage_ai_count`) | `usage_events` | itself | AI routes | MED (racy; not tiered) | Usage quota `*.usage` |
| Business quotas (team/campaign/alert) | none live (engine unwired) | `business_os_ent_usage` | — | tests only | LOW (not enforced yet) | Usage quota |
| Account hold | `users.account_status` | `login_enabled/access_enabled/deleted_at` | admin | login gate, `pro_access` | **HIGH** (ignored by 3 premium readers) | Risk/compliance precedence layer |
| iOS paid-digital | `ios_native_app_request` boundary | `DIGITAL_COMMERCE_ENABLED` | code/env | paid routes | LOW | Compliance eligibility |
| Feature rollout | `feature_flag_engine` states | `feature_flags.rollout_percentage` | admin | flagged features | LOW | Feature rollout (not entitlement) |
| Grandfathered/founder | `founder_memberships` | `premium_status='founder'` | `grant_founder_membership` | premium routes | MED (status stored as `active`, not `grandfathered`) | Grandfathered grant |

### Conflict matrix (concrete disagreements)

| # | System A says | System B says | Concrete case | Consequence |
|---|---|---|---|---|
| C1 | `pro_access`: NOT pro | `premium_visibility_engine`: premium | user `premium_status='active'` but `plan!='pro'` | feature works or not depending on which fn the route calls |
| C2 | `pro_access.has_pro_access`: pro (`plan='pro'`) | `premium_visibility_engine`: not premium (allowlist `{pulse-premium,premium,creator-pro}`) | Stripe-activated `plan='pro'` user | premium cosmetics denied though billing active |
| C3 | `account_status='suspended'` → login blocked | `premium_visibility_engine`: still premium | suspended user with live mobile session | passes premium gates (`bot.py:68108`) despite suspension |
| C4 | Server: entitlement revoked | Mobile cache: premium active | admin revokes, user offline | UI shows premium indefinitely; client gates bypassed |
| C5 | `user_entitlements`: has key | `pulse_premium_entitlements`: written but never read | grant written to 3 tables, reader uses 1 | third table silently ignored; audits disagree |
| C6 | `subscriptions` v1 (`plan_key`) | `subscriptions` v2 (`plan`) | table created twice incompatibly (`bot.py:91498,98113`) | whichever CREATE ran wins; column drift |
| C7 | merchant approved (`marketplace.*`) | premium reader consulted for a marketplace gate | approval mistaken for premium | risk of granting unrelated premium; canonical rule forbids |

### Mobile dependencies
`mobile-native/src/api/premium.ts` (fetch/normalize/cache),
`session/sessionStore.ts:151` + `session/auth.ts:72-285` (cached `premium_status`),
`screens/PremiumScreen.tsx:36-45` (offline fallback),
`LiveHostSessionScreen.tsx:419`, `MusicScreen.tsx:115-116` (client premium gates).
All consume server flags; the risk is stale cache #1, not client authority.

### Migration hazards
(a) suspension-blind premium readers — must add account-status precedence before
any cutover; (b) never-invalidated mobile premium cache — must clear on
logout/revocation; (c) three entitlement tables + column-name mismatch
(`ends_at`≠`expires_at`) + one write-only table; (d) `subscriptions` created
twice; (e) plan-allowlist mismatch (`pro`); (f) `platform_pro_access` universal-true;
(g) founder stored as `status='active'` not `grandfathered`; (h) atomic quota engine
unwired; (i) racy AI counter; (j) iOS IAP blocked (Apple/Google grant sources
unexercisable end-to-end).

### Proposed canonical boundaries
Model distinct decision types, not one "entitlement" blob:
**commercial entitlement** (`premium.*`, `business.*`, `crypto.*`),
**merchant/business approval** (`marketplace.*`, teacher/advertiser onboarding —
never confers premium), **role permission** (admin RBAC + advertiser team roles +
trust-ladder capabilities), **compliance eligibility** (age/geo/iOS-IAP/verification),
**risk restriction** (suspension/fraud/kill-switch — precedence layer above all
grants), **feature rollout** (flags/cohorts/QA allowlist — must not bypass billing
unless `source=internal_testing`), **usage quota** (metered consumption).

### Recommended first vertical slice (evidence-based)
Keep **`premium.profile.customization`** as the first cutover — it is
nonfinancial, reversible, and already wired through the facade — **but add the
account-status precedence bridge before flipping shadow→canonical**, because C3
proves the current legacy authority for that very slice
(`_profile_customization_allowed` → `premium_visibility_engine.is_premium_user`)
is suspension-blind. Concretely: (1) add an account-hold check in the facade path
so a suspended/deleted account is denied even when the legacy reader says premium;
(2) run shadow mode to quantify how many live users differ; (3) then cut over. Do
**not** pick marketplace/advertiser/creator as first — they are approvals/roles
with their own suspension surfaces (C7) and are higher-blast-radius.

### Exact files likely to change (next slice)
`services/business_os/entitlements/facade.py` (add account-hold precedence +
map the suspension check), `services/business_os/entitlements/service.py` (project
account suspension into a hold; optionally a `resolve_account_hold` helper),
`bot.py` around `_profile_customization_allowed` (`:68140`) and the premium
routes it fronts, and — for the stale-cache hazard —
`mobile-native/src/session/auth.ts` + `mobile-native/src/api/premium.ts` (clear
`pulsesoc.native.premium.status` on logout and on a revocation signal). No legacy
table is dropped or rewritten.

### Rollback approach
Unchanged from §8: everything additive and behind `BUSINESS_OS_ENTITLEMENTS`
(default `off`). The account-hold bridge, when added, is evaluated only inside the
flag-gated facade path, so flag-off remains exact legacy behavior; the mobile
cache-clear is a pure safety improvement (clears stale data, never grants access).

**STOP POINT (as requested):** inventory complete; presenting for review before
any schema or migration change. The two conflicts to decide on first are **C3
(suspension-blind premium)** and **C4 (never-invalidated mobile cache)**.

---
---

# EXPANSION II — Deep Search 3 (quotas/credits) & Search 4 (cache) + matrices

Prepared 2026-07-24. Two focused read-only audits went deeper than §12–13 on the
danger patterns (retry double-consume, deduct-then-fail, credits-without-ledger,
missing reset jobs, timezone drift) and cache leaks (account-switch, offline
actions, push payloads, token claims, per-event invalidation). Every claim below
was spot-verified against source. Evidence markers: **[C]** confirmed by reading
the code, **[I]** inferred from code, **[M]** missing enforcement, **[X]** ruled out.

## S3. Usage limits, quotas & credits — deep findings

**Confirmed live enforcement is thin.** Only the AI daily limit and the
security/HTTP rate limiters actually enforce at runtime. The purpose-built atomic
quota engine (`business_os_ent_*`) has **zero production callers** [C] (grep: only
`tests/business_os/test_entitlements.py` calls `check_and_consume`). The
per-user moderation caps (`user_privilege_profiles.posting_limit_per_day`,
`bot.py:79576,97576`) are stored and shown in admin but **never compared against an
actual post count** [M].

New, higher-severity discoveries beyond §12:

- **Q-DANGER-1 — Ad/creator wallets are mutable balances with a lost-update race
  [C].** `record_spend_event` (`services/pulse_ad_payments.py:432`) reads the
  wallet, computes the new balance in Python, and writes it back with an absolute
  `UPDATE pulse_ad_wallets SET available_balance_cents=<value>` (`:468-481`), on a
  plain connection with **no row lock / no `BEGIN IMMEDIATE`**. Two concurrent
  spends (or fundings, `:356-368`) both read the same balance and the second write
  overwrites the first — a lost deduction (over-spend) or lost credit. A correct
  append-only ledger exists (`services/business_os/ledger/ledger.py`, idempotent,
  `BEGIN IMMEDIATE`, balance re-derived) but the ad/creator/platform wallets do
  **not** use it — they are a parallel, unbacked balance. `max(0, …)` clamps hide
  overspend rather than prevent it (`:475`, `:198`). Classification: **Purchased
  credit** blended with **Promotional credit** (`promotional_credits_cents`,
  `bonus_credits_cents` summed into one spendable at `:191-197`).
- **Q-DANGER-2 — Retry double-spend on the default idempotency key [C].** When a
  caller does not pass a key, `record_spend_event` builds
  `key = f"spend:{campaign_id}:{creative_id}:{placement_key}:{now_iso()}"`
  (`:453`) — timestamp-embedded, so a retried request gets a **different** key and
  is not deduped, spending twice. Callers that pass a stable token (impression
  spend `impression-token:{hash}:{nonce}`, Stripe funding `stripe:{event}:{session}`)
  are safe; the default path is not.
- **Q-DANGER-3 — AI usage is deduct-then-fail [C].** `bot.py:21000` calls
  `consume_ai_usage(...)` (increments the counter) and only *then* `bot.py:21003`
  calls `intelligence_service.assistant_response(...)`. If the model call raises,
  the user's daily allowance was already spent with **no decrement/refund**. Same
  shape at the Telegram path (`bot.py:100076`).
- **Q-DANGER-4 — "Daily" ad budget never resets [C].** `pulse_ad_campaigns` has
  `daily_budget_cents`, but the only write to spend is
  `spent_cents = COALESCE(spent_cents,0)+amount` (`pulse_ad_payments.py:482`);
  grep for any `spent_cents=0`/reset returns nothing. A daily budget is compared
  against an ever-accumulating lifetime total — daily pacing is effectively
  one-shot [M]. The AI counter's reset is **lazy-on-read only** (`if reset_at !=
  today: count = 0`, `bot.py:88898`) — there is no scheduled reset job (the only
  `run_repeating` jobs are trial maintenance and market tickers, `bot.py:104209`).
- **Q-DANGER-5 — Timezone drift [C].** The AI limit buckets and stamps on **local**
  `datetime.now()` (`bot.py:88887`); every financial subsystem uses **UTC**
  `datetime.now(timezone.utc)` (`pulse_ad_payments.py:35`, `ledger.py`). On a
  non-UTC host the AI day flips at local midnight, out of step with the rest.
- **Q-DANGER-6 — Magic-number limits [C].** Reserve cap `50_000` appears twice in
  one function, spend clamp `10_000`, bypass sentinel `100_000_000`
  (`pulse_ad_payments.py:189,409,411,447`). `internal_promotion` accounts bypass
  all spend limits with the hardcoded `100_000_000` spendable (`:189,445`).
- **Ruled out [X]:** UNDX message/token quotas, scheduled-post caps, product/listing
  count caps, export limits, live-guest counters, API-call quotas, and
  `messages_remaining` — grep finds only docs/reports, no enforced counter. Trial
  is time-based expiry (`expire_trials`, `bot.py:88878`), correctly scheduled, not a
  consumption counter.

## S4. Cache / session / stale authorization — deep findings

Server remains authoritative and effectively un-cached: `load_account_by_id`
reads the `users` row per request; the mobile access token payload is
`{uid, dh, iat, exp, jti}` **only** (`bot.py:21502-21508`) [C]; the refresh token
is an opaque `psr_ + token_urlsafe(36)` stored as a hash in
`mobile_security_sessions` and DB-revocable (`bot.py:21521`) [C]; Flask
`session[...]` holds only `account_user_id`/`admin_user_id`/`csrf_token`/
`visitor_session_id` — **no premium/role/merchant** [C]. So no server token or
session outlives revocation. All staleness is client-side, and it is worse than
§13 showed:

- **CACHE-DANGER-1 — Severe account-switch leak [C].** Logout (`signOut`,
  `session/auth.ts:216`; `signOutEverywhere:252`) clears access state only via
  `clearUserScopedMediaState()`, which removes **only** the prefixes
  `feed.`, `post.`, `reels.`, `status.`, `messenger.`
  (`media/mediaSessionCleanup.ts:4-10`). It does **not** touch
  `pulsesoc.native.premium.status`, `…creator.state`, `…account.state`,
  `…marketplace.seller_store`, `…verification.state`, `…account.health.state`,
  `…intelligence.state`, and ~15 more. There is **no `AsyncStorage.clear()`** on
  logout, and `core/cache.ts` keys are **static, not user-id-scoped** (`:1-16`).
  Result: user B signing in on user A's device reads A's premium/creator/seller/
  verification caches until each screen's next successful network fetch overwrites
  them — and offline, indefinitely.
- **CACHE-DANGER-2 — Offline actions the server later rejects [C].** In-memory/
  cached flags gate UI in `LiveHostSessionScreen.tsx:419`
  (`authState.user?.premium_status`), `MusicScreen.tsx:113-118`,
  `AppNavigator.tsx:188`, `ProfileScreen.tsx:113`, and the
  `PremiumScreen.tsx:36-45` offline fallback renders the full entitlement list from
  stale cache. These are presentation gates; the mutations POST to the server which
  re-authorizes — but offline a revoked/free user sees unlocked tools and can start
  flows that fail server-side.
- **CACHE-DANGER-3 — Field-name drift across endpoints [C].** The same "is-premium"
  fact ships under different names: `/api/premium/status` → `premium_active`/
  `founder_active`/`plan`; `/api/account/status` → `plan`/`subscription_plan`/
  `subscription_status`/`access_label`; `/api/dashboard/creator/state` →
  `intelligence.monetization_status`; session payload → `premium_status`. Each
  client store normalizes independently (`premium.ts:114`, `verification.ts:201`),
  so two caches can disagree. All cached with **no TTL** (`core/cache.ts`) [C].
- **Ruled out [X]:** push-notification payloads carry no plan/entitlement/role
  (push is device-registration only, `bot.py:29000-29069`); navigation/route params
  carry no access flags; no JWT/embed/telegram token embeds entitlement claims.

## Required matrices

### Matrix A — Effective-access source

| Capability | Commercial source | Role source | Approval source | Compliance source | Risk override | Rollout source | Quota source | Cache copies |
|---|---|---|---|---|---|---|---|---|
| Premium profile/media/UNDX | `premium_visibility_engine.is_premium_user:25` (+3 divergent readers) | — | — | iOS-IAP boundary `bot.py:4213` | `account_status` (**not checked here**) | `feature_flag_engine` premium-only | — (boolean) | mobile `premium.status`, `account.state` |
| Pro/paid tier | `pro_access.pro_access_type:37` | — | — | — | `account_status` (**checked here** `:38`) | — | — | `account.state` |
| Marketplace sell | — | — | `marketplace_sellers.status='approved'` `bot.py:76622` | seller verification | listing `status='suspended'` | `marketplace_checkout` internal-only | — | `marketplace.seller_store` |
| Teaching/payout | — | — | `pulse_teacher_profiles.status='approved'` `bot.py:76629` | teacher verification | — | — | — | `verification.state` |
| Advertising | — | `pulse_ad_team_members.role` `pulse_advertiser_portal.py:62` | `pulse_ad_accounts.status` | — | ad-account `status='suspended'` | — | campaign count (unwired) / wallet balance | `account.state` |
| Creator tools | — | `privilege_engine` trust ladder `:52` | verification badges | — | trust_score / suspension | beta flags | — | `creator.state` |
| Admin/owner | — | `role_permissions` + owner-email `bot.py:15919` | — | — | — | — | — | — |
| AI assistant | `is_pro` bypass | — | — | — | — | — | `users.usage_ai_count` (=5/day) | — |
| Business quotas | plan catalog (unwired) | — | — | — | — | — | `business_os_ent_usage` (unwired) | — |
| Account hold | — | — | — | — | `users.account_status`/`access_enabled`/`deleted_at` | — | — | `account.health.state`, `account.state` |

### Matrix B — Override-precedence

| Override state | Stored in | Checked by | NOT checked by | Severity | Migration action |
|---|---|---|---|---|---|
| Account suspended/restricted/banned | `users.account_status` | `pro_access:38`, login gate | `premium_visibility_engine:25`, `premium_entitlement_service:740`, `premium_identity_engine:40` | **CRITICAL** | Add account-hold precedence above grant resolution; cascade to entitlement model |
| Account deleted (soft) | `users.deleted_at` | feed rank | premium readers | HIGH | Same hold layer |
| Login/access kill switch | `login_enabled`,`access_enabled` | login | premium readers, server routes post-login | HIGH | Include in hold layer |
| Ad-account suspension | `pulse_ad_accounts.status` | campaign write path | premium/creator readers | MED | Scope to `advertising.*` |
| Livestream suspension | `livestream_access.status` | live start `bot.py:38487` | unrelated features | MED | Scope to `live.*` |
| Posting/messaging suspension | `user_privilege_profiles.moderation_status` | (partial) | post/message enforcement absent | MED | Wire enforcement + scope |
| Payment delinquency | `subscription_status` past_due/unpaid | billing reads | premium readers keep access | MED | Grace vs revoke policy |
| Referral fraud flag | `referral_conversions.fraud_flag` | referral counting | — | LOW | Risk restriction on referral quota |
| Explicit revocation | entitlement rows `status='revoked'` | `service._resolve:191` | legacy readers (facade off/shadow) | HIGH | Ensure precedence in facade |
| Exhausted quota | `usage`/`usage_ai_count` | `consume_ai_usage`, `check_and_consume` | client UI (shows action available) | MED | Server deny + client reflect |

### Matrix C — Quota

| Capability | Limit source | Consumption source | Reset rule | Atomic | Plan-dependent | Known race |
|---|---|---|---|---|---|---|
| AI assistant | `FREE_AI_DAILY_LIMIT=5` `bot.py:353` | `consume_ai_usage:88884` | daily, lazy-on-read, **local TZ** | **NO** (read→check→+1→write) | binary (pro bypass) | **YES** — concurrent double, deduct-then-fail |
| Business team seats | catalog =10 | `check_and_consume` (unwired) | none | YES (`BEGIN IMMEDIATE`) | Business | none (but not enforced) |
| Ad campaigns | catalog =25/mo (unwired) | — | month | YES | advertiser | none (not enforced) |
| Crypto alerts | catalog =50/day (unwired) | — | day | YES | crypto pro | none (not enforced) |
| Ad wallet spend | `spendable_balance_cents:191` | `record_spend_event:432` | n/a (balance) | **NO** (read-then-write, no lock) | account | **YES** — lost update; retry double-spend on default key |
| Campaign daily budget | `daily_budget_cents` | `spent_cents+=` `:482` | **none (never resets)** | NO | account | daily pacing broken |
| HTTP rate limit | `bot.py:398` | in-memory dict | rolling window | NO (per-worker) | flat/IP | multi-worker multiplies limit |
| Failed-login | `bot.py:4378` | `failed_login_controls` | window | count≥threshold | no | low |
| Posting cap | `posting_limit_per_day` `:97576` | — | n/a | n/a | trust-tier | **not enforced** |

### Matrix D — Cache

| Cached field | Storage | Authority | TTL | Invalidation | Stale-access risk |
|---|---|---|---|---|---|
| `premium.status` (premium_active, plan, entitlements) | AsyncStorage `premium.ts:5` | `/api/premium/status` | **none** | **none** (overwrite-on-fetch only) | **HIGH** — offline unlock; survives logout + account switch |
| `account.state` (plan, subscription_status, access_label) | AsyncStorage `account.ts:4` | `/api/account/status` | none | none | HIGH — stale plan/status |
| `marketplace.seller_store` | AsyncStorage `marketplace.ts:7` | seller endpoints | none | none | HIGH — merchant status leaks across accounts |
| `verification.state` (status, premiumBadges) | AsyncStorage `verification.ts:5` | account/premium endpoints | none | none | MED — expired verification shows valid |
| `creator.state` (monetization_status) | AsyncStorage `creator.ts:5` | creator dashboard | none | none | MED |
| `account.health.state` (strikes/restrictions) | AsyncStorage `accountHealth.ts:8` | account health endpoint | none | none | MED — suspension not reflected offline |
| in-memory `user.premium_status` | React `auth.ts:72` | session payload | until re-auth | logout clears memory (not disk) | MED — gates live/music UI |
| `session.user` (premium_status, account_status) | AsyncStorage `sessionStore.ts:151` | session payload | none | **logout clears** | LOW |
| access token | mobile secure store | server | `exp` | server re-checks `mobile_security_sessions` | LOW — no access claims |
| Flask `session[...]` | server cookie | server | session | server | **none** (no access state cached) |

### Matrix E — Conflict (concrete disagreements)

| # | Case | System A | System B | Consequence |
|---|---|---|---|---|
| C1 | Premium fact split | `pro_access`: not pro | `premium_visibility_engine`: premium | feature works or not by which fn the route calls |
| C2 | Plan allowlist mismatch | `has_pro_access`: pro (`plan='pro'`) | `premium_visibility_engine`: not premium (`{pulse-premium,premium,creator-pro}`) | Stripe-active user denied cosmetics |
| C3 | Suspended but premium | `account_status='suspended'` (login blocked) | premium readers: still premium | passes premium gates on live session |
| C4 | Revoked but cached | server: revoked | mobile `premium.status`: active | UI unlocked indefinitely offline |
| C5 | Premium col vs sub row | `users.premium_status` true | `user_subscriptions` expired | grant persists past paid period |
| C6 | Merchant server vs cache | server: seller not approved | mobile `marketplace.seller_store`: approved (leaked from prev account) | seller UI shown to wrong user |
| C7 | Flag vs entitlement | feature flag enabled | user not commercially entitled | flag must not bypass billing unless `internal_testing` |
| C8 | Trial expired but cached | server: trial expired | mobile UI: unlocked | actions started, server rejects |
| C9 | Quota exhausted but shown | server: `usage_ai_count>=5` | client: action available | 429 after attempt; poor UX, no client reflect |
| C10 | Approval ≠ premium | merchant approved (`marketplace.*`) | a marketplace gate consults a premium reader | risk of granting unrelated premium |
| C11 | Concurrent spend | wallet read balance X | second concurrent spend read same X | lost update / over-spend (Q-DANGER-1) |

## Normalized policy categories (must stay separate)

The evaluator may combine these at decision time, but the **data model must not
collapse them into one generic grant**. A capability decision should be
explainable as, e.g.:

```
Commercial entitlement: active
Role permission:        allowed
Merchant approval:      approved
Compliance eligibility: eligible
Risk restriction:       none
Feature rollout:        enabled
Quota:                  7 of 10 remaining
Effective decision:     allowed
```

Non-negotiable rule for the future evaluator: **a paid commercial grant must never
override** account suspension, merchant suspension, compliance ineligibility,
regional restriction, fraud restriction, explicit revocation, or an exhausted
quota on a quantity-limited capability. Today, per Matrix B, the live premium
readers violate the first of these (C3) — which is why the account-hold precedence
layer is the prerequisite for any cutover.

## Updated priority ordering for the first slice

Evidence now points to sequencing the *safety fixes* alongside the first slice,
not after it:

1. **Account-hold precedence** in the facade (fixes C3) — prerequisite.
2. **Mobile cache hygiene** (fixes C4/C6): add the access prefixes to the logout
   cleanup or `AsyncStorage.clear()`; namespace `core/cache.ts` by user id; add a
   TTL to `readJsonCache`. Pure safety, grants no access.
3. Then cut over **`premium.profile.customization`** shadow→canonical.
4. Independently (not entitlement, but adjacent revenue-integrity): route the ad/
   creator wallets through the existing `ledger.py` and fix the AI deduct-then-fail
   + default-idempotency-key issues. Flag these to the payments track.

**STOP POINT (as requested):** all four audit dimensions complete; matrices
produced; no canonical tables, migrations, or compatibility code written in this
pass. Awaiting review of C3/C4 and the wallet/AI danger patterns before
implementation.

---

# EXPANSION III — Verified deep audit (Search 3 & 4, re-run with strict evidence)

This pass re-ran the quota/credit and cache audits under stricter rules: every
line number below was **read directly in the current working tree** (not taken
from agent output), mutable balances are separated into five value classes
(never one generic "quota"), and cached UI state is distinguished from confirmed
runtime authority. Where this section disagrees with EXPANSION II it **supersedes**
it. Two framings changed materially:

- **Cache leak downgraded from "security" to "stale presentation" (Bucket 1).**
  Verified: no mobile screen performs a privileged action off a cached flag. The
  cached premium/creator/verification values drive **badges and labels only**;
  every gated action re-hits the server. So this is a cross-account *presentation*
  leak, not an access-control bypass. (Still worth fixing — see below.)
- **A new Critical surfaced that EXPANSION II missed:** the *creator/seller*
  wallet, not the ad wallet, is the most double-credit-prone financial path.

## Evidence legend
[C] = confirmed by reading current code · sev = Critical/High/Medium/Low ·
dest = remediation destination (1 canonical-entitlement · 2 quota-service ·
3 financial-ledger · 4 risk/restriction · 5 client-cache-invalidation ·
6 user-scoped-storage · 7 server-reauthorization · 8 idempotency-enforcement ·
9 compensation-workflow · 10 remove-dead/misleading-state).

## SEARCH 3 — Mutable balances, separated by value class (do NOT co-model)

The single most important instruction here: these five classes must **not** share
one table or one consumption path. Financial value needs a real double-entry
ledger; a usage allowance does not; an HTTP rate limit needs neither.

| Class | Meaning | Instances found | Correct destination |
|---|---|---|---|
| **A — Financial value** | real money owed/owned | ad wallet, creator/seller wallet, platform treasury | 3 financial-ledger + 8 idempotency |
| **B — Promotional value** | granted credits, no cash-out | ad promo/bonus credits; 30-day referral Pro grant | 3 ledger (segregated) + 8 |
| **C — Usage allowance** | metered feature use | AI free daily (5/day) | 2 quota-service |
| **D — Abuse prevention** | anti-spam/fraud counters | login throttle, abuse guard | 4 risk/restriction |
| **E — Technical resource limit** | rate/size/pagination caps | `security_guard` buckets, upload MB, per_page | (leave in web tier) |
| *(excluded)* | virtual play-money | paper-sim `cash_balance`, arena `fake_balance`/XP | **do NOT migrate to a financial ledger** |

### Class A — Financial value

**A1 — Creator / seller wallet: application-only dedup → double-credit. [C] sev Critical · dest 3+8**
`services/creator_economy_service.py:267-297` `mark_transaction_paid` guards only
with an app-level status read:
```python
267 def mark_transaction_paid(transaction_id, provider_payment_id="", provider_reference=""):
277     if tx.get("status") == "paid":   # <-- SELECT-then-UPDATE, no DB uniqueness
281     "UPDATE creator_transactions SET status='paid' ... WHERE id=?"
```
`creator_ledger_entries` (schema bot.py:91556-91573) has **no UNIQUE constraint**
and no idempotency column — only non-unique indexes on `(source_type, source_id)`
(bot.py:99657). Two concurrent webhook deliveries for the same payment can both
pass line 277 and each post a credit pair. Mitigant: balance **is** reconstructable
via `reconcile_wallet` SUM (services/creator_economy_service.py:153-174), so a
duplicate is auditable after the fact — but nothing prevents it at write time.
8-property: history YES · idempotency **NO** · unique-ref **NO (app-only)** ·
atomic **NO** · neg-prevent `max(0,…)` · reversal YES (refund entry) · reconcile
YES · admin-audit partial.

**A2 — Ad wallet: non-idempotent default spend key + balance-column drift. [C] sev High · dest 3+8**
`services/pulse_ad_payments.py:432-484` `record_spend_event`:
```python
455 key = clean_text(idempotency_key or f"spend:{campaign_id}:{creative_id}:{placement_key}:{now_iso()}", 180)
...
472 UPDATE pulse_ad_wallets SET available_balance_cents=?  # = max(0, available - amount)
```
The transactions table *does* enforce `idempotency_key TEXT UNIQUE`
(bot.py:95846), so a caller that passes an explicit key is safe. But the **default**
key embeds `now_iso()` — every retry mints a new key → double-spend on retry. The
balance is a **mutable column updated in a separate statement** from the ledger
insert (not reconstructed from entries), and `max(0, …)` silently zeroes an
overspend rather than rejecting. The pre-check `spendable_balance_cents(...) <
amount` (line 448) is a separate SELECT → check-then-act race.
8-property: history YES · idempotency YES-if-key-passed / **NO on default** ·
unique-ref YES (DB) · atomic **NO** · neg-prevent silent-zero · reversal YES ·
reconcile **partial (column, not sum)** · admin-audit YES.

**A3 — Platform treasury: the correct pattern (reference, not a defect). [C] sev Low · dest 3**
`treasury_transactions` has `UNIQUE(transaction_type, source_type, source_id)`
(bot.py:91739) and the wallet update is a single atomic `available_balance_cents =
available_balance_cents + ?`. This is the model the other two should copy.

### Class B — Promotional value
**B1 — Ad promo/bonus/refund credits** share A2's table + UNIQUE protection; same
column-vs-ledger caveat. sev Medium · dest 3 (segregated from cash).
**B2 — Referral 30-day Pro grant. [C] sev Medium · dest 1+8** `grant_referral_reward`
(bot.py ~12559) extends `pro_expires_at`; the `referral_rewards` insert is
`INSERT OR IGNORE` but the Pro-extension UPDATE has no idempotency guard of its own,
and the caller's `status='granted'` pre-check is app-only.

### Class C — Usage allowance (keep OUT of the financial ledger)
**C1 — AI free daily limit: racy + uncompensated. [C] sev Low(money=none)/Medium(correctness) · dest 2+9**
`consume_ai_usage` (bot.py:88884-88914): `SELECT usage_ai_count → if reset!=today
count=0 → if count>=5 deny → count+=1 → UPDATE users` with **no row lock**
(check-then-act race, both directions: overspend or lost increment). `usage_events`
is a decorative append (no unique key); the authoritative counter is the `users`
column, so the event log **cannot reconstruct** the count. Deduct-before-op:
```
bot.py:21000  consume_ai_usage(...)      # quota burned
bot.py:21003  intelligence_service.assistant_response(...)   # may throw
```
Sequence: Request accepted → entitlement checked → **quota consumed** → external
work (AI) started → success/fail → **no compensation** → response. Classification:
**At-most-once but potentially under-delivering (Uncompensated)** — a failed AI
call still burns one of the 5.

### Class D / E — do not co-model with A/B/C
D: `basic_abuse_guard` (bot.py ~2380), login throttles — in-memory, ephemeral,
belong to the **risk/restriction evaluator (4)**, not a quota table.
E: `services/security_guard.py` sliding-window `BUCKETS`, `max_upload_mb`,
`per_page` — stay in the web tier; not entitlements.

## SEARCH 4 — Persisted client state (verified)

**Mechanism [C]:** `mobile-native/src/core/cache.ts:3-16` `readJsonCache`/
`writeJsonCache` write raw `AsyncStorage` under **global literal keys — no user-id
scoping, no TTL**. `mobile-native/src/media/mediaSessionCleanup.ts:4-16` clears
**only 5 prefixes** (`feed. post. reels. status. messenger.`). `signOut`
(mobile-native/src/session/auth.ts) calls `clearUserScopedMediaState` +
`clearNativeSessionCredentials` + `setCachedSessionUser(null)` — **none of which
touch the access-bearing caches**. `signIn`/`createAccount` do **no** purge, so an
account switch on the same device inherits the prior account's global caches.

**Biometric-retain early return [C] — new finding.** In `signOut`, when
`shouldRetainBiometricLogin()` is true the function calls
`clearActiveSessionKeepBiometric()` and **`return`s before `setCachedSessionUser(null)`**
(auth.ts:216-224). So on any Face-ID-enrolled device, ordinary logout leaves
`CACHED_USER_KEY` (holds `premium_status`, `account_status`) in place.

### Persisted access-bearing keys that survive logout (the leaks — all [C])
| Key | writer | reader | clearer | user-scoped | logout clears | sev |
|---|---|---|---|---|---|---|
| `pulsesoc.native.premium.status` | premium.ts:76 | premium.ts:72 | none | Global | No | Medium |
| `pulsesoc.native.creator.state` | creator.ts:96 | creator.ts:92 | none | Global | No | Medium |
| `pulsesoc.native.verification.state` | verification.ts:105 | :110 | none | Global | No | Medium |
| `pulsesoc.native.account.state` (plan/access_label) | account.ts | :193 | none | Global | No | Medium |
| `pulsesoc.native.session.user` (premium/account_status) | sessionStore.ts:154 | :135 | :143 | Global | **No on biometric path** | Medium |
| growth / intelligence / buyer.orders / alert.mgmt / learning(access_level) | various | various | none | Global | No | Low |

~15 global caches total, invalidated reliably only by reinstall.

### Offline / stale-behavior classification — all Bucket 1 [C]
Verified call sites: `PremiumScreen.tsx:105-110,138` use `status?.premium_active`
for **badges/labels only**; the buttons (`:137-143`) are `disabled={Boolean(busyAction)}`
and `onPress={() => runAction(...)}`, and `runAction` (`:63-81`) calls
`startPremiumCheckout()` / billing / web — all server round-trips.
`VerificationCenterScreen.tsx:179-180` likewise badges-only. Grep for any
`disabled`/`if`/`navigate`/`onPress` gated on a cached `premium_active`/
`founder_active` across `screens/*.tsx` returned **zero** privileged-action hits.
→ **Bucket 1 (stale presentation, low security risk). No Bucket 4 found.**
Draft/submit surfaces (content planner, verification upload) are Bucket 3 (client
submits, server authoritative — rejects if unauthorized).

### Push / nav / JWT verdict [C] — NOT an access-control risk
`navigation/notificationRouting.ts` uses push/route targets only to
`navigationRef.navigate(...)`; the destination screen then fetches server state.
No branch reads a plan/role/entitlement from a push payload or nav param to grant
access. Deep-link hosts are whitelisted to `pulsesoc.com`; `?token=` is stripped.
Plan text in notifications is display-only.

## Nine required outputs (verified)

1. **Confirmed source-of-truth conflicts.**
   - **C3 (unchanged, still the top conflict):** the live premium readers
     (`is_premium_user`, profile-customization gate bot.py:68140) ignore
     `account_status`; only `pro_access.pro_access_type` honors it. Suspension does
     not beat Premium. dest 4→1.
2. **Confirmed quota races.** AI counter check-then-act with no lock
   (bot.py:88891→88907); ad `spendable_balance_cents` pre-check vs UPDATE race
   (pulse_ad_payments.py:448 vs 472); creator wallet status-check vs write
   (creator_economy_service.py:277).
3. **Confirmed double-consumption risks.** *Creator wallet* — no DB uniqueness,
   app-only dedup (Critical). *Ad wallet* — non-idempotent **default** spend key on
   retry. *Referral* — Pro-extension UPDATE unguarded.
4. **Confirmed uncompensated deductions.** AI: quota burned at bot.py:21000 before
   the AI call at 21003, no refund on failure (Uncompensated / under-delivering).
5. **Confirmed cache / account-switch leaks.** ~15 global, non-user-scoped caches
   survive logout; premium/creator/verification/account + biometric-path
   `CACHED_USER_KEY` are the access-bearing ones. Presentation-level (Bucket 1).
6. **Confirmed stale-access presentation paths.** PremiumScreen /
   VerificationCenter badges render from cache before the server refresh
   overwrites the global key. No privileged action rides on them.
7. **Proposed precedence model (unchanged, now evidence-backed):**
   Account suspension > Merchant/seller suspension > Compliance ineligibility >
   Regional restriction > Fraud restriction > Explicit revocation > Exhausted
   financial balance/quota > **Paid grant (Premium/Business/etc.)** > Promotional
   grant > Grandfathered > Legacy fallback > None. A paid grant never overrides any
   item to its left. Financial and promotional balances are checked by the
   quota/ledger services, not the entitlement evaluator.
8. **Recommended first vertical slice — chosen from this evidence.**
   The safest, highest-signal first cutover is **the account-hold precedence bridge
   for `premium.profile.customization`**: it fixes the one confirmed *entitlement*
   conflict (C3) at a single legacy authority (bot.py:68140), is server-only (no
   money, no client trust), is already shadow-wired, and cannot regress revenue.
   The financial-ledger work (creator/ad wallets) is higher severity but belongs to
   the **payments track**, not the entitlement cutover — do not fold real money into
   the first entitlement slice.
9. **Recommended implementation ordering.**
   1. Account-hold precedence in the facade (fixes C3) — entitlement track,
      prerequisite for any cutover.
   2. Cut over `premium.profile.customization` shadow→canonical behind the flag.
   3. Mobile cache hygiene (Bucket-1 fix): add access prefixes to logout cleanup,
      move `setCachedSessionUser(null)` before the biometric early-return, namespace
      `core/cache.ts` by user id, add TTL. Safety only; grants no access. dest 5+6.
   4. **Payments track, separate:** creator wallet — add DB UNIQUE / idempotency
      (Critical, dest 3+8); ad wallet — require explicit idempotency key + route
      through `ledger.py` (dest 3+8); AI — consume-on-success + atomic counter
      (dest 2+9). These are revenue-integrity, not entitlement-model, work.

**STOP POINT (as requested):** both audits complete and independently verified in
the working tree; five value classes kept separate; no canonical entitlement
tables, migrations, services, or compatibility code written in this pass. Awaiting
your decision on which slice to implement first.

---

# EXPANSION IV — Required synthesis (formatted to spec)

This is the canonical, spec-formatted version of the verified findings. Every line
number below was re-confirmed with `grep -n` / `sed` in the current working tree
this pass. Scope note: this section exists only to support the Business OS
entitlement architecture. Items unrelated to Business OS / Advertising /
Marketplace / Premium / Payments / Crypto are tagged **[future work]** and are not
expanded further.

## 1. Mutable-value inventory

### 1a. Financial value (real money) — 8-property
| Balance | File:line | Imm. history | DB idempotency | Unique op ref | Atomic mutation | Neg-balance prevention | Reversal | Reconciliation | Admin audit |
|---|---|---|---|---|---|---|---|---|---|
| Creator/seller wallet | services/creator_economy_service.py:267-297; schema bot.py:91556 | Yes (ledger entries) | **No** | **No (app-only status)** | **No** | `max(0,…)` silent | Yes (refund entry) | Yes (SUM) | Partial |
| Ad wallet | services/pulse_ad_payments.py:432-484; UNIQUE bot.py:95846 | Yes | Yes if key passed / **No default** | Yes (DB) | **No (2 stmts)** | `max(0,…)` silent | Yes | Partial (column≠sum) | Yes (`_audit`) |
| Platform treasury | bot.py:91739 (UNIQUE); update `+= ?` | Yes | **Yes** | Yes (DB) | **Yes** | `MAX(0,…)` on refund | Yes | Yes | Partial |

### 1b. Promotional value (no cash-out) — 8-property
| Balance | File:line | Imm. history | DB idempotency | Unique op ref | Atomic | Neg prevention | Reversal | Reconcile | Admin audit |
|---|---|---|---|---|---|---|---|---|---|
| Ad promo/bonus/refund credits | pulse_ad_payments.py:168-196 | Yes | Yes | Yes (DB) | No | `max(0,…)` | Yes | Partial | Yes |
| Referral 30-day Pro grant | bot.py ~12559 (`grant_referral_reward`) | Partial (`referral_rewards`) | **No** | referral_code (not unique on grant) | **No** | N/A | No | No | `log_product_event` |

### 1c. Usage allowances (keep OUT of financial ledger)
| Allowance | File:line | Limit | Reset | Atomic | Reconstructable from events |
|---|---|---|---|---|---|
| AI free daily | bot.py:88884-88914; limit `FREE_AI_DAILY_LIMIT=5` bot.py:353 | 5/day | lazy, on next call vs `usage_reset_at` | **No (no lock)** | **No** (`usage_events` decorative; `users.usage_ai_count` authoritative) |

### 1d. Abuse-prevention counters → risk/restriction evaluator, not quota
| Counter | File:line | Persistence |
|---|---|---|
| Login/abuse throttle | bot.py ~2380 (`basic_abuse_guard`), ~5655 login gates | in-memory / security-core, ephemeral |

### 1e. Technical limits → stay in web tier
| Limit | File:line | Persistence |
|---|---|---|
| Sliding-window rate buckets | services/security_guard.py:30-41 | in-memory `BUCKETS` |
| Upload size / pagination | bot.py:2556 (`max_upload_mb`), 13263/14928 (`per_page`) | request-scoped |

## 2. Consumption safety matrix
| Operation | Value consumed | Consumption timing | External work | Idempotent | Compensation | Classification | Severity |
|---|---|---|---|---|---|---|---|
| Creator wallet credit (`mark_transaction_paid`) | Financial (A) | on payment webhook | none (recording) | **No (app-only status check :277)** | manual refund entry only | **At-least-once, double-creditable** | Critical |
| Ad wallet spend (`record_spend_event`) | Financial (A) | after ad already delivered | delivery precedes debit | Yes w/ explicit key / **No on default key :455** | refund types exist, not auto | **At-least-once on retry (default key)** | High |
| Referral Pro grant (`grant_referral_reward`) | Promotional (B) | on referral event | none | **No (unguarded UPDATE)** | none | At-least-once, double-grantable | Medium |
| AI assistant (`consume_ai_usage`) | Usage (C) | **before** AI call (bot.py:21000 → 21003) | AI generation may throw | **No (racy, no lock)** | **none** | **At-most-once, under-delivering (Uncompensated)** | Medium |
| Platform treasury fee | Financial (A) | on transaction | none | **Yes (DB UNIQUE :91739)** | refund path | **Safe / Compensated** | Low |

## 3. Persisted-client-state inventory (all keys)
| Key | Writer | Reader | Cleared on logout | Survives logout | Survives failed-login / account-switch | Reinstall-only invalidation | Scope |
|---|---|---|---|---|---|---|---|
| `pulsesoc.native.premium.status` | premium.ts:76 | premium.ts:72 | No | **Yes** | **Yes** | Yes | Global |
| `pulsesoc.native.creator.state` | creator.ts:96 | creator.ts:92 | No | **Yes** | **Yes** | Yes | Global |
| `pulsesoc.native.verification.state` | verification.ts:105 | verification.ts:110 | No | **Yes** | **Yes** | Yes | Global |
| `pulsesoc.native.account.state` | account.ts:198 | account.ts:194 | No | **Yes** | **Yes** | Yes | Global |
| `pulsesoc.native.session.user` (CACHED_USER_KEY) | auth.ts:107/152/193 | session store | cleared :228 **only on non-biometric path** | **Yes on biometric path** (early return :222) | **Yes on biometric path** | No | Global |
| `feed./post./reels./status./messenger.*` (5 media prefixes) | media modules | media modules | **Yes** (mediaSessionCleanup.ts:4-16) | No | No | No | User media |
| growth / intelligence / buyer.orders / alert.mgmt / learning(access_level) | various api/*.ts | various | No | **Yes** | **Yes** | Yes | Global |

Mechanism: `core/cache.ts:3-16` writes raw AsyncStorage under global literals, no
user-id namespacing, no TTL. Logout (`auth.ts signOut/signOutEverywhere`) clears
only the 5 media prefixes + native credentials; the access-bearing caches above are
never cleared.

## 4. Stale-access classification (per gated surface)
| Surface | File:line | Bucket |
|---|---|---|
| PremiumScreen badges/labels | PremiumScreen.tsx:105-110,138 | **1 — stale UI only; server authoritative** |
| VerificationCenter badges | VerificationCenterScreen.tsx:179-180 | **1 — stale UI only** |
| Premium checkout / billing / web actions | PremiumScreen.tsx:63-81,137-143 (`runAction`→`startPremiumCheckout`) | **1** (action is a server round-trip) |
| Content planner / verification upload (draft→submit) | planner/verification screens | **3 — submission attempted; server rejects** |
| — | — | **4 — none found** |

Buckets 1 and 3 are **not** security bypasses. No Bucket-4 (privileged op without
fresh server authorization) exists in the mobile client — verified by grepping
`screens/*.tsx` for any `disabled`/branch/`navigate`/privileged `onPress` gated on
a cached `premium_active`/`founder_active`: zero hits.

## 5. Verified risk register
Migration destinations: 1 canonical-entitlement · 2 quota-service · 3 financial-ledger ·
4 risk/restriction · 5 client-cache-invalidation · 6 user-scoped-storage ·
7 server-reauthorization · 8 idempotency-enforcement · 9 compensation · 10 remove-dead-state.

**R1 — Creator/seller wallet double-credit.** creator_economy_service.py:277 ·
`if tx.get("status") == "paid": return` (app-only; `creator_ledger_entries`
schema bot.py:91556 has no UNIQUE) · **Confirmed** · **Critical** · affected:
creators/sellers + platform liability · scenario: two concurrent payment-webhook
deliveries both pass the status check and each post a credit pair → seller paid
twice · dest **3+8** · stage: **Payments track (separate from first entitlement
slice)**.

**R2 — Ad wallet retry double-spend.** pulse_ad_payments.py:455 ·
`key = clean_text(idempotency_key or f"spend:{…}:{now_iso()}", 180)` · **Confirmed**
· **High** · affected: advertisers · scenario: delivery retry with no explicit key
mints a new timestamped key → same spend debited twice; separate balance-column
UPDATE (line 472) can also drift from ledger · dest **3+8** · stage: Payments track.

**R3 — Premium ignores account suspension (C3).** premium_visibility_engine.py:25-35
(no `account_status`; grep = 0 matches) gating `_profile_customization_allowed`
bot.py:68124/68174, while `pro_access.pro_access_type` honors it · **Confirmed** ·
**High** · affected: suspended/at-risk accounts, compliance · scenario: a suspended
user still passes premium gates because the live reader never checks
`account_status` · dest **4→1** · stage: **First entitlement slice (this is the one)**.

**R4 — AI quota racy + uncompensated.** bot.py:88891→88907 (check-then-act, no
lock); consume at 21000 before AI at 21003 · **Confirmed** · **Medium** · affected:
free-tier users · scenario: parallel requests overshoot 5/day, or a failed AI call
still burns a use with no refund · dest **2+9** · stage: Payments/quota track.

**R5 — Referral Pro-grant unguarded UPDATE.** bot.py ~12569 · **Confirmed** ·
**Medium** · affected: referrers · scenario: concurrent triggers extend Pro twice
(caller pre-check is app-only) · dest **1+8** · stage: entitlement track (after R3).

**R6 — Global mobile caches survive logout / account switch.** core/cache.ts:3-16
+ mediaSessionCleanup.ts:4-16 + auth.ts:222-228 biometric early-return ·
**Confirmed** · **Medium** (presentation, Bucket 1 — not a bypass) · affected:
shared-device / multi-account users · scenario: account B briefly sees account A's
premium/creator/verification badges until server refresh; on Face-ID devices
`CACHED_USER_KEY` persists through logout · dest **5+6** · stage: mobile cache
hygiene (own stage, after the first slice).

**Ruled out (investigated, no action):**
- **Push/nav/JWT as access carriers** — navigation/notificationRouting only calls
  `navigate(...)`; destination screens fetch server state; deep-links whitelisted to
  `pulsesoc.com`, `?token=` stripped. Plan text is display-only. **Not a risk.**
- **Bucket-4 client privileged ops** — none found in `screens/*.tsx`.
- **Paper-sim `cash_balance`, arena `fake_balance`/XP** — virtual play-money;
  **[future work]**, must NOT be migrated to a financial ledger.
- **security_guard buckets / upload / pagination** — technical limits, stay in web
  tier; not entitlements. **[future work]** if ever centralized.

## Stop-conditions presentation
- **Verified financial-integrity risks:** R1 (Critical), R2 (High). Treasury is the
  safe reference pattern.
- **Verified quota races:** AI counter (R4); ad `spendable` pre-check vs UPDATE;
  creator status-check vs write (R1).
- **Verified double-consumption paths:** R1 (creator, app-only dedup), R2 (ad
  default key), R5 (referral).
- **Verified uncompensated deductions:** R4 (AI consume-before-op, no refund).
- **Verified global cache leaks:** R6 (~15 global keys, no TTL/namespace).
- **Verified account-switch leaks:** R6 (no purge on signIn/createAccount; biometric
  logout retains CACHED_USER_KEY).
- **Verified authorization bypasses:** **none** (no Bucket-4).
- **Ruled out:** push/nav/JWT carriers, Bucket-4 ops, virtual play-money, web-tier
  limits.

## Recommended first vertical slice (single risk class, from evidence)
**Account-hold precedence for `premium.profile.customization`** — R3 only.
It satisfies every constraint you set for a first slice:
- **Clear authority:** one legacy gate (bot.py:68124/68174).
- **Low financial risk:** server-only, no money, no wallet, no quota in the path.
- **Reversible rollout:** already behind `BUSINESS_OS_ENTITLEMENTS` shadow→canonical;
  flag-off = zero behavior change.
- **Compatibility fallback:** the existing facade falls back to `is_premium_user`.
- **Testable expiration/revocation:** suspension and revocation are directly
  assertable against the evaluator.
- **Minimal legacy dependency:** touches the entitlement facade + one gate; does
  **not** mix in financial balances, quotas, or the mobile cache.

Explicitly **not** in the first slice: creator/ad wallets (R1/R2), AI quota (R4),
mobile cache (R6). Each is its own later stage.

## Recommended remediation ordering
1. **First slice — R3:** add account-hold/revocation precedence in the entitlement
   facade so suspension beats Premium; cut `premium.profile.customization`
   shadow→canonical behind the flag. (Business OS entitlement foundation.)
2. **R5:** fold the referral Pro-grant into the same entitlement path with an
   idempotent grant. (Entitlement track.)
3. **R6:** mobile cache hygiene — clear access prefixes on logout, move
   `setCachedSessionUser(null)` before the biometric early-return, namespace
   `core/cache.ts` by user id, add TTL. (Safety only; grants no access.)
4. **Payments track (separate program):** R1 creator wallet (DB UNIQUE +
   idempotency), R2 ad wallet (require explicit key, route through `ledger.py`),
   R4 AI (consume-on-success + atomic counter). Real money — never folded into the
   entitlement slice.

**STOP — do not create or modify canonical tables until this report is reviewed and
the first slice (R3) is confirmed.** On your go-ahead I will implement R3 as the
shared entitlement foundation's first vertical slice, behind the flag, with tests
for expiration and revocation — and nothing else in that change.

---

# EXPANSION V — R3 IMPLEMENTED: account-hold precedence for `premium.profile.customization`

Status: **PASS.** The real server-side gate now denies a suspended Premium account
while continuing to allow an active eligible Premium account, and the flag-off path
is byte-for-byte the prior legacy behaviour. Evidence below.

## V.1 Files changed (only two — narrow slice)

| File | Change | Nature |
|---|---|---|
| `services/business_os/entitlements/facade.py` | Added `_account_hold()` resolver; rewrote `check()` as thin wrapper over new `explain()`; added `explain()`; extended `_record_shadow_diff()` and `shadow_compare()` with additive hold fields | Core precedence logic — single authoritative suspension resolver |
| `bot.py` (`_profile_customization_allowed`, ~line 68124; gate consumed at `pulse_premium_profile_theme_api` ~line 68174) | Pass fresh in-memory `ent_context={account_status, access_enabled}` into `shadow_compare`/`check` | Route wiring only; legacy fallback preserved |

No migration, no new tables, no changes to wallets/payments/AI quotas/referral grants/
mobile caches, and no other Premium capability touched.

## V.2 Exact precedence rule

Resolved centrally in `facade._account_hold(subject_id, context)` and applied by
`explain()`. For the canonical (flag-on) path the order is:

1. **Account hold overrides everything.** If `account_status` normalises to anything
   other than `active` (suspended / banned / restricted / disabled / any non-active
   string) → **deny**, `decision_source='account_hold'`, `reason='account_<status>'`.
2. Else if `access_enabled == 0` → **deny**, `reason='account_access_disabled'`.
3. Else fall to the entitlement decision: an active canonical grant allows; an
   expired or revoked grant denies; if canonical is *silent*, fall back to the legacy
   `is_premium_user` opinion.

`_account_hold` prefers the passed context and only reads
`SELECT account_status, access_enabled FROM users WHERE user_id=?` when context is
absent. Any exception **fails safe** (`on_hold=False`, `reason='account_status_unavailable'`)
so the new system can never harden a user out of a feature it shouldn't.

The eligible status set is exactly `{"active"}` — everything else is a hold. This is
the same authority `services/pro_access.py` already honours, so the two agree.

## V.3 Flag-off behaviour (zero change — proven)

Under `BUSINESS_OS_ENTITLEMENTS=off` (the default), `explain()` returns the pure
legacy result with **no** hold overlay (`reason='flag_off_legacy'`,
`account_hold=False`). `test_flag_off_legacy_unchanged` asserts that a **suspended**
Premium account is **still allowed** under off (preserving the pre-existing
suspension-blind legacy result exactly) and a free user is still denied. Byte-for-byte
prior behaviour.

## V.4 Flag-on behaviour (the fix — proven)

Under `BUSINESS_OS_ENTITLEMENTS=canonical`:
- active eligible Premium → **allowed**
- suspended / banned / restricted / access-disabled Premium → **denied** via
  `account_hold`
- expired grant / revoked grant → denied
- non-Premium → denied

Under `shadow`, access is unchanged (legacy served) but a `shadow_diff` row is written
recording `account_hold` + `account_suspended` for telemetry.

## V.5 Tests + results

New suite `tests/business_os/test_entitlement_account_hold.py` — **11/11 PASS**:
`test_active_premium_active_account_allowed`, `test_non_premium_denied`,
`test_premium_suspended_denied`, `test_premium_disabled_access_denied`,
`test_premium_expired_grant_denied`, `test_premium_revoked_grant_denied`,
`test_flag_off_legacy_unchanged`, `test_flag_on_canonical_precedence_enforced`,
`test_shadow_records_hold_disagreement`, `test_repeated_checks_idempotent` (reads
create no grant rows), `test_pro_access_not_regressed`.

Regression suites (all green):
- `tests/business_os/test_entitlements.py` — **26/26 PASS** (incl. existing shadow test
  #22 asserting `canonical is False`, and canonical-fallback test #23 — both preserved).
- `tests/business_os/test_ledger_and_webhook_inbox.py` — **6/6 PASS**.
- `tests/business_os/test_stripe_ledger_handler.py` — **7/7 PASS** (payments matrix).
- `tests/protection/test_core_platform_contract.py` — contract **OK**.
- Byte-compile: `python3 -m py_compile bot.py services/business_os/entitlements/facade.py` → **OK**.

(Sandbox note: pytest is not installable here; suites run via `python3 <file>` standalone
runners, which is how they are authored. No TS checks run — no native/mobile code changed.)

## V.6 Remaining legacy `is_premium_user` callers (intentionally out of scope)

The R3-wired gate is `bot.py:68140` (inside `_profile_customization_allowed`). Every
other caller is a **distinct Premium capability** and, per the narrow-scope instruction,
was left on legacy in this slice:

| Location | Capability | Status |
|---|---|---|
| `bot.py:10746`, `10759` | Premium-status API text (badge/message) | legacy — presentation |
| `bot.py:43919` | premium flag in feed/profile route | legacy — separate capability |
| `bot.py:68105`, `68108` | Premium **identity effects** (aura) | legacy — separate capability |
| `bot.py:68305` | Creator-studio right-panel copy | legacy — presentation |
| `bot.py:68470` | premium flag in another route | legacy — separate capability |
| `services/premium_visibility_engine.py:42,56` | internal helpers | legacy |
| `services/premium_entitlement_service.py:740` | `is_premium_user` definition | authority |

The C3/R3 suspension-blind conflict therefore still exists **latently** in those other
capabilities. That is expected: R3's scope is only `premium.profile.customization`. Each
remaining capability is a candidate for its own future slice using the same facade
pattern (no new suspension logic to write — just wire the gate + context).

## V.7 Rollback

Instant and config-only: set `BUSINESS_OS_ENTITLEMENTS=off` (or unset it). The gate
returns to pure legacy with no code change or redeploy of logic. Full code rollback =
revert the two files in V.1; there is no schema to unwind.

## V.8 Commit status

**Not committed by me.** The repo `.git` is on a FUSE mount that rejects writes from
this sandbox, so commits remain an owner-side action. The two changed files
(`services/business_os/entitlements/facade.py`, `bot.py`) and the new test file are in
the working tree ready to stage. Suggested message: `feat(business-os): R3 account-hold
precedence for premium.profile.customization behind BUSINESS_OS_ENTITLEMENTS`.

---

# EXPANSION VI — R3.1 IMPLEMENTED: account-hold precedence for `premium.identity.effects`

Status: **PASS.** The identity-effects (aura) gate now denies suspended / disabled /
banned / restricted Premium accounts while active eligible Premium accounts keep the
effect; flag-off is byte-for-byte legacy. Same facade, no redesign.

## VI.1 Files changed (slice-isolated)

| File | Change | Tracked in git? |
|---|---|---|
| `services/business_os/entitlements/facade.py` | +1 mapping line: `"premium.identity.effects": _legacy_premium_customization` in `_LEGACY_READERS` (line 98) so canonical mode's legacy fallback recognises an eligible premium user for this key. No other facade logic touched — account-hold precedence in `explain()` is key-agnostic and reused as-is. | **Untracked** (new package) |
| `bot.py` | New contiguous helper `_identity_effects_allowed(user)` (def at line 68092), plus both identity-effects call sites rewired: GET premium flag (68146) and POST gate (68149). | Tracked (modified) |
| `tests/business_os/test_entitlement_identity_effects.py` | New 11-test suite for this capability. | **Untracked** (new) |

No changes to wallets, quotas, payments, caches, schemas, or `_profile_customization_allowed`
(the R3 helper is left exactly as committed-ready).

## VI.2 Precedence rule (unchanged, reused)

Identity effects uses its own key `premium.identity.effects`, resolved by the same
`facade.explain()` path: account hold (`account_status != 'active'`, or
`access_enabled == 0`) overrides any grant; otherwise a canonical grant decides, with
legacy `is_premium_user` fallback when canonical is silent (which it always is until a
canonical grant exists). The two capabilities never share a helper and a grant for one
key does not leak into the other (proven by
`test_identity_effects_key_independent_of_profile_key`).

## VI.3 Both call sites made consistent

Previously the GET response returned raw `is_premium_user` while the POST enforced it —
so a suspended premium user could be told `premium: true` and then get 403 on apply.
Both now flow through `_identity_effects_allowed`, so the advertised flag matches the
enforced decision. Under flag-off both return the identical legacy boolean, so the JSON
(`true`/`false`) is byte-for-byte unchanged.

## VI.4 Tests + results (all green)

- `tests/business_os/test_entitlement_identity_effects.py` — **11/11 PASS** (active-allowed,
  non-premium-denied, suspended-denied, disabled/restricted-denied, expired-grant-denied,
  revoked-grant-denied, flag-off-legacy-unchanged, flag-on-canonical, shadow-diff-recorded,
  idempotent-no-grant-rows, capability-isolation).
- `tests/business_os/test_entitlement_account_hold.py` (R3) — **11/11 PASS** (no regression).
- `tests/business_os/test_entitlements.py` — **26/26 PASS**.
- `tests/business_os/test_ledger_and_webhook_inbox.py` — **6/6**; `test_stripe_ledger_handler.py` — **7/7** (payments regression).
- `python3 -m py_compile bot.py services/business_os/entitlements/facade.py` — **OK**.

## VI.5 Owner-side staging guide (R3.1 as its own commit)

`facade.py` and the new test file are **untracked new files**, so per-hunk splitting does
not apply to them — they are added whole. The entitlements package is a single new file
tree; its R3 and R3.1 content will land in whichever commit first adds the package (they
cannot be split at the file level because the file was never previously committed). Only
`bot.py` is tracked and needs `git add -p`.

bot.py R3.1 hunks (verified headers): `@@ -67927,6 +68089,47 @@` (the
`_identity_effects_allowed` helper) and `@@ -67940,10 +68143,10 @@` (the two call sites).
These are distinct from the R3 hunks `@@ -67959,6 +68162,53 @@` and `@@ -67971,7 +68221,7 @@`.

```bash
cd ~/Desktop/CoinPilotX
rm -f .git/index.lock && git reset            # clear lock + polluted index
git add services/business_os/entitlements/facade.py \
        tests/business_os/test_entitlement_identity_effects.py
git add -p bot.py                              # stage ONLY the two R3.1 hunks above; 'n' to all others
git commit -m "feat(business-os): enforce account holds in premium identity-effects gate"
```

(If committing R3 and R3.1 together, add both test files and answer 'y' to all four
entitlement hunks in bot.py.)

## VI.6 Rollback

`BUSINESS_OS_ENTITLEMENTS=off` restores pure legacy for identity effects too — same
config-only switch, no schema.

## VI.7 Commit status

**Not committed** (sandbox `.git` is read-only / index locked, as in V.8). Files are in
the working tree ready for the owner-side recipe in VI.5.

---

# EXPANSION VII — R3.2: effective (currently-usable) Premium in status/display payloads

## VII.1 What R3.2 does

R3.1 hardened the identity-effects *gate*. R3.2 does the parallel work for the Premium
*status / presentation* callers so the API and UI never advertise **usable** Premium to
an account that is on hold — while never describing an owner as having lost the
underlying subscription. It keeps two ideas strictly separate:

- **Ownership** — the user holds a Premium subscription / grant. Reported in the
  existing fields (`premium_active`, `plan`, `subscription_status`) **verbatim**.
- **Effective access** — the user may *currently exercise* Premium: ownership AND not on
  hold. Exposed through **new, additive** fields; never overwrites an ownership field.

The single authority is `facade.account_hold(subject_id, context)` — the same resolver
the R3/R3.1 gates use. A thin bot.py wrapper `_effective_premium_access(user, owns)`
flag-gates it: `off`/`shadow` return ownership unchanged (byte-for-byte legacy);
`canonical` returns `effective = owns AND NOT account_hold`, with a denial reason set on
hold. Ownership is passed in and never mutated.

## VII.2 The five rewired call sites (all contiguous, single helper)

1. **`/api/premium/status`** (`api_premium_status`) — computes `owns_premium` from the
   existing ownership readers, then `effective_premium, access_denial_reason =
   _effective_premium_access(...)`. The response **keeps** `premium_active = owns_premium`
   and **adds** `effective_premium_access` and `access_denial_reason`. A held owner gets a
   distinct message ("Premium is paused while your account is under review.") instead of
   being told Premium is simply active or that verification is pending.
2. **`/pulse/premium`** (`pulse_premium_page`) — `premium`/`level`/`renewal` remain
   ownership; only the human status label becomes hold-aware ("Premium paused").
3. **Creator studio panel** (`pulse_creator_dashboard_page`) — the "Premium active." claim
   now reflects `_studio_premium_effective` (usable), not raw ownership.
4. **Creator analytics** (`pulse_creator_analytics_page`) — the `premium` flag that gates
   the usable/locked view is made effective-aware.

All five reuse the one `_effective_premium_access` helper; no suspension logic is
duplicated per route.

## VII.3 Ownership is never described as gone

Per the isolation rule, a suspended owner still shows `subscription_status: active` /
`premium_active: true`; only `effective_premium_access` flips to `false` with
`access_denial_reason: account_<status>`. `facade.account_hold()` returns a *descriptive*
`account_status` (e.g. `"suspended"`), never "no subscription" — proven by
`test_ownership_separate_from_effective`.

## VII.4 Why the test targets the facade authority directly

`bot.py` is not importable in this hermetic sandbox (it pulls `stripe`/`flask`/`telegram`
and the full services import block; PyPI is unavailable), so the wrapper cannot be
exercised in-process here. The suite proves the **shared authority** `facade.account_hold`
directly and asserts the exact decision rule via a faithful reference mirror
(`_effective_reference`) kept in lock-step with the shipped helper across off/shadow/
canonical, owner/non-owner, and ownership-not-mutated cases. The wrapper's flag-gating and
the five rewired call sites are additionally verified by byte-compilation and code
inspection (this section + VII.6).

## VII.5 Tests + results (all green)

- `tests/business_os/test_entitlement_effective_access.py` — **11/11 PASS** (account-hold
  active/nonactive-statuses/access-disabled/DB-read/unknown-fails-safe; effective off &
  shadow present ownership; canonical active-owner-usable; canonical suspended-owner &
  access-disabled denied; non-owner never granted / no reason; ownership-not-mutated).
- `tests/business_os/test_entitlement_identity_effects.py` (R3.1) — **11/11 PASS** (no regression).
- `tests/business_os/test_entitlement_account_hold.py` (R3) — **11/11 PASS** (no regression).
- `tests/business_os/test_entitlements.py` — **26/26 PASS**.
- `tests/business_os/test_ledger_and_webhook_inbox.py` — **6/6**;
  `tests/business_os/test_stripe_ledger_handler.py` — **7/7** (payments regression).
- `python3 -m py_compile bot.py services/business_os/entitlements/facade.py` — **OK**.

## VII.6 Owner-side staging guide (R3.2 as its own commit)

`facade.py` gained one public accessor `account_hold()` (untracked new-file tree, added
whole). Only `bot.py` needs `git add -p`. The **R3.2 hunks** (verified `git diff` headers):

- `@@ -10728,6 +10728,40 @@ def api_premium_billing_portal():` — `_effective_premium_access` helper
- `@@ -10740,10 +10774,26 @@ def api_premium_status():` — owns/effective compute + hold-aware message
- `@@ -10756,7 +10806,7 @@ def api_premium_status():` — additive `effective_premium_access` / `access_denial_reason`
- `@@ -43755,13 +43967,16 @@ def pulse_premium_page():` — hold-aware status label
- `@@ -68076,6 +68379,8 @@ def pulse_creator_dashboard_page():` — `_studio_premium_effective`
- `@@ -68093,7 +68398,7 @@ def pulse_creator_dashboard_page():` — studio claim uses effective
- `@@ -68258,7 +68563,9 @@ def pulse_creator_analytics_page():` — analytics gate uses effective

**Exclude** all other bot.py hunks (`15748`, `16825`, `17347`, `17649`, `17693`, `30932`,
`37801`, `42563`, `43594`, `70322`, `70387`, `79553`, `86723`, …) — these are pre-existing
unrelated working-tree changes, not part of this slice.

```bash
cd ~/Desktop/CoinPilotX
rm -f .git/index.lock && git reset            # clear lock + polluted index
git add tests/business_os/test_entitlement_effective_access.py
git add -p services/business_os/entitlements/facade.py   # stage the account_hold() accessor
git add -p bot.py                              # stage ONLY the seven R3.2 hunks above; 'n' to all others
git commit -m "feat(business-os): expose effective premium access in status/display payloads"
```

## VII.7 Rollback

`BUSINESS_OS_ENTITLEMENTS=off` returns every rewired caller to legacy ownership
presentation (effective == ownership, reason `None`), so the JSON and labels are
byte-for-byte the pre-R3.2 output. Config-only; no schema change.

## VII.8 Commit status

**Not committed** (sandbox `.git` is read-only / index locked, as in V.8 / VI.7). All
R3.2 changes are in the working tree ready for the VII.6 recipe.

## VII.9 Honest status

- **R3.2 — PASS** (implemented; 11/11 new + 61/61 regression green; byte-compile clean;
  authority verified directly, wrapper + call sites verified by inspection since bot.py is
  not importable in-sandbox).
- **Broader Premium entitlement foundation — PARTIAL, not PASS.** Migrated so far: profile
  customization (R3), identity effects (R3.1), and the five status/display payloads (R3.2).
  Remaining `is_premium_user` / premium-visibility callers elsewhere in bot.py have **not**
  all been migrated and are **not** yet documented as intentionally excluded, so the
  foundation stays PARTIAL until that caller inventory is completed.
  *(Superseded by EXPANSION VIII below — that inventory is now complete.)*

---

# EXPANSION VIII — R3.3: caller-closure inventory + PARTIAL→PASS determination

## VIII.1 Purpose & scope

Not a new forensic audit — the single goal is to close the known Premium-caller
migration surface and decide whether the foundation can move from PARTIAL to PASS.
Searched exactly: `is_premium_user`, direct Premium-visibility checks
(`premium_visibility_engine.*`, `prompt_html`, `contextual_prompt`), premium capability
gates that could still ignore `account_status`/`access_enabled`, and direct premium-flag
reads (`lifetime_premium`, `premium_glow_manual_grant`, `premium_status`,
`subscription_plan`, `has_premium_access`, `has_entitlement`).

## VIII.2 Authority readers are ownership-only by design (not a defect)

Both `premium_visibility_engine.is_premium_user(user)` and
`premium_entitlement_service.is_premium_user(uid)` read only subscription/premium flags —
they never consult `account_status`/`access_enabled`. This is **correct and intentional**:
they are the *ownership* authorities. Account-hold precedence is layered on top by the
facade (`facade.account_hold` / `facade.explain`) at each gate/effective site, keeping
ownership and usability strictly separate. Neither reader was changed.

## VIII.3 Complete caller-closure matrix (bot.py)

Class legend: **G** = authorization gate, **P-eff** = effective-access presentation,
**P-own** = ownership/prestige presentation, **O** = subscription-ownership field/report,
**A** = administrative/internal, **D** = dead/unreachable.

| # | Site (function) | Line(s)* | Class | Capability | Suspension precedence req'd? | Disposition |
|---|---|---|---|---|---|---|
| 1 | `pulse_premium_profile_theme_api` | `_profile_customization_allowed` (68238/68281) | G | profile customization | Yes | **Migrated R3** (facade `premium.profile.customization`) |
| 2 | `pulse_premium_identity_effects_api` GET+POST | `_identity_effects_allowed` (68166/68203/68206) | G | identity effects (aura) | Yes | **Migrated R3.1** (facade `premium.identity.effects`) |
| 3 | `api_premium_status` | 10781–10787,10799+ | P-eff + O | premium status payload | Yes (effective field only) | **Migrated R3.2** (`effective_premium_access`/`access_denial_reason` added; `premium_active`/`plan`/`subscription_status` kept as ownership) |
| 4 | `pulse_premium_page` | 43973/43976 | P-eff + O | /pulse/premium status label | Yes (label only) | **Migrated R3.2** (hold-aware label; `premium`/`level`/`renewal` stay ownership) |
| 5 | creator studio panel (`pulse_creator_dashboard_page`) | 68387 | P-eff | studio "Premium active." claim | Yes | **Migrated R3.2** (`_studio_premium_effective`) |
| 6 | creator analytics (`pulse_creator_analytics_page`) | 68572 | G/P-eff | locked/unlocked previews | Yes | **Migrated R3.2** (effective `premium` gates unlock) |
| 7 | dashboard shell upsell card (`pulse_social_shell`) | 36177/36178 | P-eff | promo copy across all shell pages | Yes | **Migrated R3.3** (effective override — no false "enabled across PulseSoc" for a held owner) |
| 8 | creator analytics upsell card (`pulse_creator_analytics_page`) | 68582 | P-eff | promo copy | Yes | **Migrated R3.3** (effective override, reuses the page's effective flag) |
| 9 | `create_founder_checkout_session` | 10209 (+10211) | G | founder checkout initiation | Yes — **already enforced** | **Excluded (already compliant):** the very next lines reject `account_status != 'active'`, so a held account cannot start checkout. No change. |
| 10 | founder checkout-return page | 11034 | P-own / O | "Founder Premium is active." confirmation | No | **Excluded (ownership presentation):** post-payment confirmation of the *subscription just created*; converting it to effective would risk telling a just-paid user they are paused. Ownership by design. |
| 11 | `premium_user_is_grantable` | 68047 | A | admin grant eligibility helper | No (admin) | **Excluded (administrative):** used only in the owner/admin grant path. |
| 12 | premium-mark / glow badges (`user_has_premium_mark`, `pulse_premium_mark_html`) | 32643,42548,42694,70510,70639,70689,70810,… | P-own | prestige badge rendering | No | **Excluded (prestige/ownership presentation):** a badge denotes the subscription/prestige mark, not a claim of currently-usable capability. Suppressing badges on hold is a separate product decision, out of gate-correctness scope. |
| 13 | plan/label payload fields (`subscription_status_payload`, various dict builds) | 10313,10799,11648,14477,19476,27649–50,… | O | plan/subscription labels | No | **Excluded (ownership fields):** report the subscription/plan that exists; must remain ownership per the mission's separation rule. |
| 14 | admin grant/revoke + admin SQL selects | 68050,68613,68634,79689+,79727+,79965+,80125,80596,86815+,103316+ | A | admin/webhook subscription lifecycle | No (admin/server) | **Excluded (administrative/server-authoritative):** grant/revoke and reporting, not end-user capability gates. |

\* Lines are current post-R3.3 working-tree numbers and will drift with further edits.

**Result of the sweep:** every authorization gate (G) and every misleading effective-access
presentation (P-eff) is migrated. All remaining sites are ownership (O/P-own),
administrative (A), or already-suspension-aware gates — each documented above with a
concrete reason. No unexplained suspension-blind authorization caller remains.

## VIII.4 R3.3 change set (isolated + additive)

- `services/premium_visibility_engine.py` — one backward-compatible optional param
  `is_premium_override=None` threaded through `contextual_prompt` → `prompt_html`. When
  `None` (every pre-existing caller), behaviour is byte-for-byte legacy.
- `bot.py` — two call sites now pass the effective flag: the dashboard shell card
  (`pulse_social_shell`, via `_effective_premium_access(shell_user, …)`) and the creator
  analytics card (reusing the page's already-computed effective `premium`).
- No facade redesign, no new tables/migrations, no payments/quota/cache changes.

## VIII.5 Tests + results (all green)

- `tests/business_os/test_premium_visibility_effective_override.py` — **6/6 PASS**
  (no-override legacy owner/free; override-False downgrades a held owner; override-True
  upgrades; `prompt_html` reflects override in HTML; `None` == omitted, byte-for-byte).
- Regression, all green: `test_entitlement_account_hold` 11/11, `test_entitlement_identity_effects`
  11/11, `test_entitlement_effective_access` 11/11, `test_entitlements` 26/26,
  `test_ledger_and_webhook_inbox` 6/6, `test_stripe_ledger_handler` 7/7 — **78 total**.
- `python3 -m py_compile bot.py services/business_os/entitlements/facade.py services/premium_visibility_engine.py` — **OK**.
- Direct grep confirms every `is_premium_user` / `prompt_html` site in bot.py resolves to a
  migrated effective/gate helper or a documented ownership/admin exclusion (VIII.3).

## VIII.6 Owner-side staging guide (R3.3)

R3.3 hunks (verified `git diff` headers; numbers drift with the tree):

- `services/premium_visibility_engine.py`: `@@ -36,10 +36,14 @@ def is_premium_user` (contextual_prompt override) and `@@ -68,8 +72,8 @@ def creator_card_context` (prompt_html signature + passthrough).
- `bot.py`: `@@ …+36171,11 @@ def pulse_social_shell` (dashboard shell card) and `@@ …+68577,9 @@ def pulse_creator_analytics_page` (creator card).

```bash
cd ~/Desktop/CoinPilotX
rm -f .git/index.lock && git reset
git add tests/business_os/test_premium_visibility_effective_override.py
git add -p services/premium_visibility_engine.py   # stage the two override hunks
git add -p bot.py                                   # stage ONLY the two R3.3 promo-card hunks; 'n' to all others
git commit -m "feat(business-os): make premium upsell cards reflect effective (hold-aware) access"
```

Exclude the same unrelated pre-existing bot.py hunks noted in VII.6.

## VIII.7 Rollback

`BUSINESS_OS_ENTITLEMENTS=off` makes `_effective_premium_access` return ownership, so the
override equals ownership and the promo HTML is byte-for-byte legacy. Config-only.

## VIII.8 Commit status

**Not committed** (sandbox `.git` read-only / index locked). Working tree ready for VIII.6.

## VIII.9 Honest status — foundation determination

- **R3.3 — PASS** (6/6 new + 72/72 regression; compile clean; engine contract verified
  directly, two bot.py call sites verified by inspection + byte-compile since bot.py is not
  importable in-sandbox).
- **Broader Premium entitlement foundation — PASS.** The known Premium-caller surface is
  now closed: R3 (profile customization), R3.1 (identity effects), R3.2 (status/display
  payloads), R3.3 (upsell presentation) migrate every authorization gate and every
  effective-access presentation, and the caller-closure matrix (VIII.3) documents every
  remaining site as ownership-only, prestige/presentation, administrative, or an
  already-suspension-aware gate. Standing caveats: (a) all commits remain owner-side
  (sandbox `.git` is read-only); (b) the bot.py wrappers are verified by byte-compilation +
  inspection rather than in-process import, because bot.py's third-party import block is
  unavailable in this hermetic sandbox; (c) scope is the Python backend — any parallel
  Premium checks in `mobile-native/` are a separate client surface, not part of this
  server-authoritative foundation.
