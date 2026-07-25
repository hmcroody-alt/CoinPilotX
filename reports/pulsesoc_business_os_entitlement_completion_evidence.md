# PulseSoc — Entitlement Consolidation: Completion Evidence

Branch: `release/undx-nexus-core-v4`
Base commit at time of writing: `8356b0165aec736ec9062ffe2016255b55976b3e`
Flag: `BUSINESS_OS_ENTITLEMENTS` (`off` | `shadow` | `canonical`), default **off** = zero behaviour change.
Status vocabulary: **PASS / PARTIAL / BLOCKED / NOT TESTED**. Nothing is marked PASS unless observed working.

---

## Headline

**PARTIAL — code-complete and fully test-green in the sandbox; commit + push BLOCKED (sandbox git is on a FUSE mount that rejects all writes to `.git`). Owner must stage/commit/push per the guidance in section 12.**

The vertical slice (advanced profile customization) is granted, checked server-side, gated in the API, expires and revokes correctly, is audited, and is covered by 26 passing tests. The one thing that keeps this from a clean end-to-end PASS is that the client-reflection leg and the commit itself cannot be exercised from this sandbox (no macOS/simulator; no git write). Those two are explicitly called out below as **NOT TESTED** and **BLOCKED** respectively — not asserted.

---

## 1. Existing entitlement sources found — **PASS (documented)**

Full inventory in `reports/pulsesoc_business_os_entitlement_inventory_and_migration.md`. Summary of the sources that currently gate access:

- Legacy premium: `services/premium_entitlement_service.py` (`is_premium_user`, `grant_entitlement`) reading the `users` table (`premium_status`, `subscription_status`, `lifetime_premium`, `premium_glow_manual_grant`) plus `premium_entitlements` / `pulse_premium_entitlements` tables (the latter two are created by `bot.py`, not by `ensure_founder_schema`).
- Premium visibility engine used in-line in `bot.py` route gates (e.g. `premium_visibility_engine.is_premium_user(user)`).
- Stripe subscription state (payments ledger slice) — the only provider whose events are verified server-side.
- Feature flags (`BUSINESS_OS_LEDGER`, and now `BUSINESS_OS_ENTITLEMENTS`).
- Ad-hoc admin/grandfathered fields on the `users` row.

Duplicate/conflicting sources, writers, readers, expiration/cancellation/renewal/grace/revocation behaviour, user-facing dependencies, and production data to preserve are catalogued in the inventory report. Key risk recorded there: legacy reads are deeply coupled to the `users` table, so the canonical service must **front** legacy, never replace it, until shadow proves parity.

## 2. Canonical schema — **PASS**

Additive only. `migrations/business_os/0003_entitlements.sql` (+ `.down.sql`) and the byte-consistent in-code `ensure_schema()` create seven `business_os_ent_*` tables — **no legacy table is read or mutated by DDL**:

- `business_os_ent_products` (6 seeded: premium, premium_business, marketplace, advertiser, creator, crypto)
- `business_os_ent_plans` (9 seeded incl. monthly/annual/trial/grandfathered/business/merchant/advertiser/creator/crypto)
- `business_os_ent_catalog` (entitlement keys per plan, with `limit_value`/`limit_period`)
- `business_os_ent_grants` — natural key `UNIQUE(subject_type, subject_id, entitlement_key, source, source_reference)` + 3 indexes
- `business_os_ent_usage` — PK `(subject_type, subject_id, entitlement_key, period_key)`
- `business_os_ent_audit` — append-only
- `business_os_ent_provider_subs` — `UNIQUE(provider_subscription_id)`

Grants record every field required by the spec: subject, entitlement key, source, source reference, start/expiry/grace, status, limit/period, region/platform, revocation reason, metadata, created_by, audit reference.

## 3. Precedence rules — **PASS**

Implemented in `service._resolve` / `_grant_phase` and unit-proven:

1. Security/compliance **suspension** → DENY (checked first, globally)
2. Explicit **revocation** → DENY (only when nothing active/grace/grandfathered supersedes)
3. **Active** canonical grant → ALLOW
4. **Grace-period** grant → ALLOW
5. **Grandfathered** grant → ALLOW
6. Valid **legacy fallback** during migration → ALLOW (facade only, when canonical is silent)
7. Otherwise → no access

Feature-flag source is treated as `internal_testing`, not a billing bypass. Merchant approval (`merchant_approval` source) is restricted to marketplace keys and cannot grant Premium. Subscription cancellation keeps access until `expires_at`/period end.

## 4. Provider interfaces — **PASS (Stripe real; Apple/Google interface-only, non-fabricating)**

`services/business_os/entitlements/providers.py`:
- **Stripe (real):** `map_stripe_subscription` (pure), `apply_stripe_subscription` (map → land in `provider_subs` → project grants), price→plan map, active/terminal status sets, epoch→ISO, idempotent by `provider_subscription_id`. Unmapped price is recorded but **not** projected.
- **Apple / Google (interface only):** `AppleAppStoreAdapter` and `GooglePlayAdapter` — every method raises `ProviderNotImplemented`. They **never fabricate a verified/active result**, by design (revenue-integrity). Tested.

## 5. Compatibility strategy — **PASS**

`facade.py` with `get_mode()` reading `BUSINESS_OS_ENTITLEMENTS` per call:
- **off:** legacy only (zero behaviour change).
- **shadow:** serve legacy to the user, evaluate canonical, record the diff (`shadow_compare` → `{legacy, canonical, differs, intended_winner, migration_action}` + shadow_diff audit row). Differences are never surfaced to users.
- **canonical:** canonical authoritative, with mapped legacy fallback when canonical is silent.

Legacy readers are mapped per key (`premium.profile.customization` → `is_premium_user`, etc.) and imported lazily so the facade has no hard dependency on `bot.py`.

## 6. Usage limits & quotas — **PASS**

`usage.py`: `check_and_consume` runs inside `BEGIN IMMEDIATE`, verifies the entitlement resolves to ALLOW, then either counts an unlimited boolean capability or enforces a metered limit atomically. Returns `{allowed, reason, used, limit, remaining, period_key}`. Period bucketing day/month/cycle. Portable UPDATE-else-INSERT (no engine-specific UPSERT).

## 7. Admin controls — **PASS (server-side); native admin UI NOT TESTED**

Owner-only routes in `bot.py`, each requiring owner auth + flag + CSRF + reason, capturing before/after via `explain_entitlement` and writing `log_admin_audit`:
- `POST /admin/business-os/entitlements/grant`
- `POST /admin/business-os/entitlements/revoke`
- `GET  /admin/business-os/entitlements/explain` (read; adds `facade_mode`)
- `POST` reconcile endpoint (`admin_business_os_reconcile`)

No silent mutation path exists. The rendered admin UI itself was not exercised (no browser/session harness in sandbox) — **NOT TESTED** at the UI layer.

## 8. First vertical slice selected — **advanced profile customization** — **PARTIAL**

Nonfinancial, reversible, real Premium capability, exactly as the spec recommends (not payouts/merchant/refunds/campaign/crypto).

- Product/plan mapping: **PASS**
- Canonical grant: **PASS**
- Server-side access check (`_profile_customization_allowed(user)` gating `pulse_premium_profile_theme_api`): **PASS**
- Expiration / revocation / suspension: **PASS** (tested)
- Admin inspection + audit history: **PASS**
- Compatibility fallback (off/shadow/canonical): **PASS**
- **Native UI lock/unlock reflected in client: NOT TESTED** — requires the Expo/RN app on a macOS simulator/device, which this Linux sandbox does not have. The server gate is proven; the on-device reflection is unverified and must be checked by the owner.

## 9. Files changed / added

New (untracked, cleanly isolable):
- `services/business_os/entitlements/` — `__init__.py`, `schema.py`, `service.py`, `facade.py`, `providers.py`, `usage.py` (~1764 LOC)
- `migrations/business_os/0003_entitlements.sql` + `.down.sql`
- `tests/business_os/test_entitlements.py`
- `reports/pulsesoc_business_os_entitlement_inventory_and_migration.md`
- `reports/pulsesoc_business_os_entitlement_completion_evidence.md` (this file)

Modified (selective staging required — see §12):
- `bot.py` — **only** these additions belong to this slice: `admin_business_os_reconcile`, `_business_os_entitlements_enabled`, `_business_os_ent_csrf_ok`, `admin_business_os_entitlement_grant` / `_revoke` / `_explain`, `_profile_customization_allowed`, and the one-line gate swap in `pulse_premium_profile_theme_api`. Everything else in the `bot.py` diff is pre-existing unrelated work (f-string refactors, etc.) and must be excluded.

## 10. Exact test results

- `tests/business_os/test_entitlements.py` — **26/26 PASS** (`python3 tests/business_os/test_entitlements.py`)
- `tests/business_os/test_ledger_and_webhook_inbox.py` — **6/6 PASS** (payments/ledger regression; no regression)
- `tests/business_os/test_stripe_ledger_handler.py` — **7/7 PASS** (Stripe ledger; no regression)
- `python3 -c "import ast; ast.parse(open('bot.py').read())"` — **bot.py parses OK**

Required-test coverage (step 10): active / expired / future-dated / revoked / suspension-overrides / trial expiry / grace / cancel-at-period-end / refund revocation / duplicate provider event / unmapped-price recorded-not-projected / legacy fallback / legacy-vs-canonical shadow diff / admin grant+audit / product-to-plan mapping / quota consume / quota reset (period key) / over-limit deny / unlimited boolean / not-entitled deny / reconciliation repair / idempotent grant. Cross-device restoration and true concurrent-writer contention are represented at the unit level (idempotency + `BEGIN IMMEDIATE`) but **not** under a live multi-process load test — **PARTIAL** on those two.

## 11. Working tree protection — **PASS (documented)**

The tree contains unrelated changes that must NOT be committed with this slice: pre-existing `bot.py` f-string refactors, `mobile-native/*`, `.env.example`, `services/pulse_ai_provider_router.py`, security reports, store artifacts, device-observation reports, and the separately-scoped ledger/payments files. Staging guidance in §12 stages only the entitlement slice.

## 12. Commit / push — **BLOCKED (sandbox) → owner action required**

Sandbox git cannot write to `.git` (FUSE mount rejects unlink/rename → commit impossible here). Run these **on your machine**:

```bash
cd ~/Desktop/CoinPilotX
git checkout release/undx-nexus-core-v4

# 1) Stage the clean, self-contained new files wholesale:
git add services/business_os/entitlements/ \
        migrations/business_os/0003_entitlements.sql \
        migrations/business_os/0003_entitlements.down.sql \
        tests/business_os/test_entitlements.py \
        reports/pulsesoc_business_os_entitlement_inventory_and_migration.md \
        reports/pulsesoc_business_os_entitlement_completion_evidence.md

# 2) Stage ONLY the entitlement hunks of bot.py, interactively:
git add -p bot.py
#   Accept (y) ONLY the hunks that add:
#     - admin_business_os_reconcile
#     - _business_os_entitlements_enabled / _business_os_ent_csrf_ok
#     - admin_business_os_entitlement_grant / _revoke / _explain
#     - _profile_customization_allowed  (near pulse_premium_profile_theme_api)
#     - the one-line gate swap: `if not _profile_customization_allowed(user):`
#   Reject (n) every other hunk — the f-string refactors in pulse_live_page,
#   pulse_marketplace_page, pulse_merchant_profile_page, pulse_group_detail_page,
#   the admin_* page tweaks, and the stripe_webhook hunk are NOT part of this slice.

# 3) Verify you staged only the slice, then commit:
git diff --cached --stat
git commit -m "business_os: server-authoritative entitlement service (Stage 2) + profile-customization vertical slice behind BUSINESS_OS_ENTITLEMENTS"
git push origin release/undx-nexus-core-v4
```

**Commit SHA: BLOCKED (not yet committed — owner to run the above).**
**Push status: BLOCKED (pending commit).**

## Rollback strategy

Flag-gated: set/leave `BUSINESS_OS_ENTITLEMENTS=off` (default) and every new code path is inert — the profile-customization gate falls straight back to `premium_visibility_engine.is_premium_user`, identical to today. Data rollback: `0003_entitlements.down.sql` drops only the additive `business_os_ent_*` tables; no legacy table is touched. Code rollback: revert the single entitlement commit. Migration is staged shadow → canonical, so parity can be observed before any user-visible cutover.

## Remaining legacy callers / next steps

Every `premium_visibility_engine.is_premium_user` / `premium_entitlement_service` call site other than the profile-theme gate is still legacy. Migrate them incrementally behind shadow mode after this slice is proven in production, in this order: advanced media quality → advanced UNDX → business team/analytics → marketplace sell → advertising → crypto alerts. Also to build before those cutovers: real Apple/Google verification (currently interface-only), and a live concurrent-writer load test for the quota path.
