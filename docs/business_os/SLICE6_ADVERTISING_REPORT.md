# Business OS — Advertising Slice 6 Completion Report
**Ad sets and creative foundation**

Branch: `release/undx-nexus-core-v4`
Flag: `BUSINESS_OS_ADVERTISING`
Scope boundary: builds the canonical hierarchy **Advertiser account → Campaign → Ad set → Creative → (future) delivery instance**. STOPS before any delivery, impression logging, auction, billing consumption, pacing, spend, attribution, reporting, Marketplace, or native UI.

---

## 1. Summary

Slice 6 adds the ad-set and creative layers beneath the existing campaign object, plus a live-derived hierarchy-readiness view. An authenticated advertiser can build an owned campaign → governed ad set → authoritative creative → submit each for review; an admin can approve/reject with a structured reason and full audit; the backend then **derives** (never stores) whether the whole hierarchy is delivery-ready. No object in this slice delivers, auctions, spends, or reserves funds.

All new logic lives in `services/business_os/advertising/*`. `bot.py` contains only thin, flag-gated adapters that delegate to the framework-agnostic controller `api.py`. Behavior is dark when the flag is off (advertiser routes 404; admin routes 409 after the owner guard). Legacy `pulse_ads_service` / `pulse_ad_*` tables and `/api/pulse/ads/…` routes are untouched.

---

## 2. Files changed

### New service modules
- `services/business_os/advertising/ad_sets.py` — ad-set model, lifecycle transitions, ownership, draft editing, `_ad_set_public` (exposes `placement_valid` and `audience_valid` as SEPARATE booleans), admin review.
- `services/business_os/advertising/creatives.py` — creative model (image / video / reels_video), authoritative media binding, destination validation, draft editing, material-revision new-versioning, admin review.
- `services/business_os/advertising/targeting.py` — governed-audience allowlist + placement allowlist (Feed + Reels only), server-side rejection of sensitive/prohibited targeting.
- `services/business_os/advertising/readiness.py` — pure read/compose that DERIVES `hierarchy_ready` live from 7 separate inputs; never persists a composite boolean.

### Extended modules
- `services/business_os/advertising/api.py` — added slice-6 handlers (create/list/get/update ad set, ad-set lifecycle, create/list/get/update/revise creative, creative lifecycle, creative readiness, and the admin review + admin read counterparts). Unknown request keys rejected (`unknown_field`). `_dark()` → 404 body when flag off.
- `services/business_os/advertising/schema.py` — `ensure_schema()` now creates the two new tables (idempotent, mirrors migration 0008).
- `bot.py` — thin flag-gated route adapters only (advertiser routes ~L18214–18396, admin routes ~L18669–18759). No business logic added to bot.py.

### Migrations
- `migrations/business_os/0008_advertising_ad_sets_creatives.sql` — additive-only.
- `migrations/business_os/0008_advertising_ad_sets_creatives.down.sql` — symmetric rollback.

### Tests
- `tests/business_os/test_advertising_slice6_api.py` — 19 controller-level tests.
- `tests/business_os/test_advertising_slice6_routes.py` — 10 structural (AST) route-guard tests over `bot.py`.

---

## 3. Migrations + rollback evidence

`0008` is **additive-only**. Up creates two tables and their indexes; down drops exactly those and nothing else.

**`business_os_ad_sets`**: `ad_set_id` (PK), `campaign_id`, `advertiser_user_id`, `name`, `status` (DEFAULT `draft`), `placements_json`, `audience_json`, `schedule_start_at`, `schedule_end_at`, `budget_allocation_json`, `review_reason`, `version` (DEFAULT 1), `archived_at`, `created_by`, `created_at`, `updated_at` + 3 indexes.

**`business_os_ad_creatives`**: `creative_id` (PK), `ad_set_id`, `campaign_id`, `advertiser_user_id`, `creative_type`, `media_asset_id`, `thumbnail_asset_id`, `headline`, `body`, `call_to_action`, `destination_type`, `destination_ref`, `accessibility_text`, `status` (DEFAULT `draft`), `review_reason`, `version` (DEFAULT 1), `supersedes_creative_id`, `archived_at`, `created_by`, `created_at`, `updated_at` + 4 indexes.

**Observed runtime rollback check** (temp SQLite, seeded with adjacent `pulse_media_assets`, legacy `pulse_ads_service`, and slice-1..5 `business_os_ad_campaigns`):

```
NEW after up: ['business_os_ad_creatives', 'business_os_ad_sets']
after down == before: True
adjacent/legacy intact: True
residual ad_set/creative indexes after down: []
MIGRATION_0008_UPDOWN_OK
```

The migration never touches `pulse_media_assets`, the ledger, slice-1..5 tables, or any legacy `pulse_ads_*` object.

---

## 4. API contracts (canonical namespace)

All under flag `BUSINESS_OS_ADVERTISING`. Advertiser namespace `/api/business-os/advertising/…`; admin namespace `/admin/business-os/advertising/…`. Controller returns `(status_code, body)`; every body carries `ok`.

### Advertiser — ad sets
- `POST   …/ad-sets` → 201 create draft (campaign must be owned; parent-archived → 409).
- `GET    …/ad-sets` / `GET …/ad-sets/<id>` → owned reads (404 for non-owned; existence not leaked).
- `POST   …/ad-sets/<id>/update` → draft edit only (`not_editable` 409 once submitted).
- `POST   …/ad-sets/<id>/<action>` → lifecycle: submit / withdraw / pause / resume / archive / restore (`illegal_transition` 409).

### Advertiser — creatives
- `POST   …/creatives` → 201 create draft (ad set must be owned; media must be owned + ready; destination valid).
- `GET    …/creatives` / `GET …/creatives/<id>` → owned reads.
- `POST   …/creatives/<id>/update` → draft edit only.
- `POST   …/creatives/<id>/revise` → 201 new version on material change (`supersedes_creative_id` set; `no_material_change` 400 otherwise).
- `POST   …/creatives/<id>/<action>` → submit / withdraw / archive / restore.
- `GET    …/creatives/<id>/readiness` → live-derived readiness (separate inputs, `delivering:false`).

### Admin
- `GET    …/ad-sets` (queue, default status `submitted`) / `GET …/ad-sets/<id>`
- `POST   …/ad-sets/<id>/review` → decision `approve`|`reject` + structured `reason` (required on reject). Returns before/after status. Audited.
- `GET    …/creatives` (queue) / `GET …/creatives/<id>` / `GET …/creatives/<id>/readiness`
- `POST   …/creatives/<id>/review` → same shape. Audited.

Werkzeug routing note: static segments (`/update`, `/revise`, `/readiness`, `/review`) are registered so they outrank the dynamic `<action>` catch-all.

---

## 5. Permissions & security

- **Advertiser reads/writes**: require `_business_os_advertising_enabled` + `pulse_ads_api_user_required`; writes additionally require `pulse_ads_verify_write`. Non-owned objects → 404 (existence never leaked).
- **Admin**: `require_owner_api`; writes additionally require `_business_os_ent_csrf_ok` and emit `log_admin_audit` with `admin["id"]`, `request_ref`, decision, before/after status, version, and reason.
- **Dark-when-off**: advertiser routes 404 when flag off; admin routes 409 (after the owner guard, so unauthorized callers still can't probe).
- **Media isolation**: a creative referencing another user's media asset returns `media_not_found` (same code as a genuinely absent asset — cross-user existence not leaked).
- **Destination validation**: internal destinations verified against canonical tables (profile→users, post→pulse_posts, reel→pulse_reels, marketplace_product→marketplace_listings); external destinations must be HTTPS and are normalized (lowercased scheme+host, fragment dropped).
- **Governed audience**: strict allowlist (countries, languages, min_age, max_age, device_classes, connections, exclusions). Unknown keys → `unknown_targeting_field`. Sensitive/precise-location/uploaded-lists/lookalikes/retargeting/interests/health/religion/politics/race/ethnicity/orientation/financial-hardship/children → `prohibited_targeting`. Invalid age range → `bad_age_range`.
- **Placements**: strict allowlist Feed + Reels only (`unsupported_placement`).
- **Concurrency**: `version` column; material creative revision creates a new row (`version+1`, `supersedes_creative_id`) rather than mutating a submitted object.

---

## 6. Separation-of-concerns invariant

Readiness is DERIVED live on every call from 7 SEPARATE authoritative inputs and is never stored as one boolean:

1. campaign review status (`approved`)
2. campaign funding status (`funded`)
3. campaign operational status (`active`)
4. ad-set review status (`approved`)
5. creative review status (`approved`)
6. placement validity
7. audience validity

`hierarchy_ready` is the live AND of these; `denial_reasons[]` names each specific blocker (e.g. `campaign_not_funded`, `ad_set_not_approved`, `placement_invalid`). The response also carries `delivering: false` — this slice authorizes FUTURE delivery only and never delivers. Review history of every object is left fully intact even when a parent is archived/rejected.

---

## 7. Tests + observed results

All tests are standalone-runnable (no pytest) and were executed to completion.

- `test_advertising_slice6_api.py` — **19/19 PASS**. Covers: flag-off dark, owned creation, non-owner child 404, cross-owner parent rejected, unknown/sensitive/invalid-age targeting rejection, placement allowlist, creative media ownership (cross-user → media_not_found), destination validation, advertiser-cannot-approve, submitted-creative immutability, material-revision new version, admin review audited + owner-visible reason, parent-archival blocks submission, readiness keeps inputs separate (proves not-ready → ready transition), approved-creative-no-delivery-no-spend, admin queue + combined read, unknown-field rejection.
- `test_advertising_slice6_routes.py` — **10/10 PASS**. Structural AST checks over `bot.py`: advertiser read/write guard sets, admin read/write guard sets, dark 404/409, canonical namespace paths, delegation to the `advertising import api` controller, single review route per object, and absence of delivery/spend tokens (`post_entry`, `impression`, `auction`, `reserve_funds`, `release_funds`, `deduct`).

**Full business_os regression — ALL GREEN** (run in small batches): advertising slices 1–6, ledger (6), stripe handler (7), entitlements (26), entitlement_account_hold (11), entitlement_effective_access (11), entitlement_identity_effects (11), premium_visibility (6). Byte-compile of `bot.py` + all advertising services + both new test files: COMPILE_OK.

**Migration 0008 up/down runtime check**: MIGRATION_0008_UPDOWN_OK (see §3).

---

## 8. Risks & known issues

- **Reels media-playback contract (pre-existing, NOT fixed in this slice)**: the Reels media-playback contract failure that predates slice 6 remains open and is documented as **separate later work**. It was intentionally NOT touched or falsely marked fixed here. It does not gate slice-6 acceptance because slice 6 stops before delivery/playback.
- **AST route verification is structural, not runtime**: `bot.py` is not importable in the sandbox (missing stripe/flask/telegram, no PyPI). Route guards are therefore verified by parsing `bot.py`'s source rather than by executing the Flask app. Controller behavior itself is fully exercised at runtime against `api.py`.
- **Events deferred**: domain-event emission is intentionally deferred (event_bus is ephemeral). Audit logging via `log_admin_audit` is in place; a durable event stream is future work.

---

## 9. Native API contract (§12 — DEFINED, not built)

The mobile-native client is out of scope to build in slice 6, but the backend contract it will consume is defined here so the native app can be built against a stable surface later:

- **Draft autosave**: `POST …/ad-sets/<id>/update` and `…/creatives/<id>/update` are idempotent draft writes; the client may autosave on field blur. Only `draft` objects accept updates (`not_editable` 409 signals the client to switch to the revise flow).
- **Field-level validation errors**: every rejection returns a machine-readable `code` (`unknown_field`, `prohibited_targeting`, `bad_age_range`, `unsupported_placement`, `media_not_found`, `media_type_mismatch`, `media_not_ready`, `bad_destination`, `bad_destination_scheme`, `missing_media`, `missing_destination`) so the client can map a code to a specific form field.
- **Media selection metadata**: creatives bind to an authoritative `media_asset_id` from `pulse_media_assets`; the client must present only assets owned by the current user with `processing_status = ready`.
- **Placement preview metadata**: allowed placements are Feed + Reels only; the client renders previews strictly from that allowlist.
- **Review state + rejection reason**: object `status` plus admin-supplied `review_reason` are returned on owned reads so the client can show "in review / approved / rejected: <reason>".
- **Version conflict**: `version` is returned on every object; material revision produces a new object with `supersedes_creative_id`. The client should treat a submitted object as immutable and offer "revise" (which creates a new version).
- **Loading / retry / offline-safe drafts**: draft writes are idempotent and safe to retry; the client can hold drafts locally and replay `update` calls without creating duplicates.
- **Readiness**: `GET …/creatives/<id>/readiness` returns the separate inputs + `denial_reasons[]` + `delivering:false`, letting the client show a per-blocker checklist without implying delivery has begun.

---

## 10. Staging deploy guide

1. Apply migration `0008_advertising_ad_sets_creatives.sql` (additive; safe to run before enabling the flag). Rollback via `0008_…down.sql` is proven clean.
2. Deploy the updated `bot.py` + `services/business_os/advertising/*` with `BUSINESS_OS_ADVERTISING` **off**. Behavior is dark: advertiser routes 404, admin routes 409 — no user-visible change.
3. Smoke the dark state, then flip `BUSINESS_OS_ADVERTISING` on for a staging/owner cohort.
4. Walk the completion path in staging: authenticated advertiser builds owned campaign → governed ad set → authoritative creative → submit each → admin approve/reject with reason → confirm `…/creatives/<id>/readiness` flips to `hierarchy_ready:true` only when all 7 inputs are satisfied, and `denial_reasons[]` is accurate otherwise. Confirm `delivering:false` throughout.
5. Confirm no ledger entries, impressions, or auctions are produced by any slice-6 action.
6. To roll back: flip the flag off (instant dark), then optionally apply the down migration.

**Completion boundary reached.** Delivery, impression logging, auctions, billing consumption, pacing, spend, attribution, reporting, Marketplace, and native UI are explicitly NOT started and remain future slices.
