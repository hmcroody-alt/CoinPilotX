# Business OS — Advertising Slice 7 Completion Report

**Slice:** 7 — Basic Feed and Reels delivery MVP
**Flag:** `BUSINESS_OS_ADVERTISING` (dark by default)
**Branch:** `release/undx-nexus-core-v4`
**Status:** COMPLETE — every claim below observed green in the hermetic sandbox.

---

## 1. Summary

Slice 7 connects the approved canonical hierarchy (advertiser → campaign → ad set →
creative) built in slices 1–6 to real, server-authoritative placement responses. The
backend can now:

> find ONE eligible approved creative → bind it to an IMMUTABLE delivery instance →
> return a sponsored Feed/Reels payload → accept ONE idempotent impression → accept
> ONE idempotent click

with **no auction, no spend deduction, no attribution modelling, no reporting
dashboard, and no native UI changes.** Selection is deterministic per viewer behind a
replaceable strategy interface; the frequency cap derives from the immutable
impression log; events are append-only and idempotent; a self/advertiser-owned view
is flagged fraud and marked not billing-eligible; and **no money moves anywhere** —
`billing_eligible` is recorded but `billing_processed` is always `false`, left for the
next (billing) slice.

The whole surface stays dark when the flag is off (viewer routes 404, admin routes
409) and never touches the legacy `pulse_ads_service` / `pulse_ad_*` tables or the
canonical ledger balances.

---

## 2. Files changed

### New service modules (`services/business_os/advertising/`)
- `delivery_common.py` — shared primitives (privacy-safe `subject_ref`, HMAC
  `impression_token`, TTL/cap/rate constants, id minting, ISO time). Holds the shared
  layer so eligibility / selection / frequency / delivery / events never import each
  other (acyclic).
- `eligibility.py` — request-time gate evaluation reusing slice-6 hierarchy-readiness
  plus request-context gates; emits per-gate booleans + prefixed `reasons[]` and a
  `readiness_snapshot`.
- `selection.py` — `SelectionStrategy` interface + `DeterministicRotation` (stable
  `sha256(subject_ref:creative_id)` score, ties broken on `creative_id`). Replaceable;
  **no auction / ML / pacing.**
- `frequency.py` — server-side cap DERIVED from the immutable impression log
  (`frequency_state` → count / cap_max / window / remaining / cap_reached).
- `delivery.py` — `request_placement(...)` binds an immutable delivery instance and
  projects the client-safe sponsored payload; `load_delivery_row`, `is_expired`,
  admin read helpers.
- `events.py` — `record_impression(...)` / `record_click(...)` (idempotent, immutable,
  fraud-tagged, billing-flagged-not-processed) + admin read helpers.

### Extended modules
- `services/business_os/advertising/api.py` — 7 new thin controllers returning
  `(int status, dict body)`: `request_delivery`, `record_impression`, `record_click`,
  `admin_list_deliveries`, `admin_get_delivery`, `admin_list_impressions`,
  `admin_list_clicks`. All dark-404 when flag off; request/impression/click bodies pass
  a privacy allowlist (`unknown_field` 400); impression requires a token
  (`missing_token` 400).
- `bot.py` — 7 flag-gated route adapters + `_bo_ad_fold_idempotency` helper (folds the
  `Idempotency-Key` header into the payload). Viewer POSTs use
  `pulse_ads_api_user_required` + `pulse_ads_verify_write`; admin GETs use
  `require_owner_api`. Decision logic lives entirely in `api.py`.

### Migrations
- `migrations/business_os/0009_advertising_delivery.sql` — additive-only; 3 tables +
  indexes.
- `migrations/business_os/0009_advertising_delivery.down.sql` — symmetric rollback,
  drops only slice-7 objects.

### Tests (`tests/business_os/`)
- `test_advertising_slice7_delivery.py` — 17 service-layer tests (end-to-end flow).
- `test_advertising_slice7_api.py` — 11 controller-contract tests.
- `test_advertising_slice7_routes.py` — 10 bot.py route-wiring structural tests.
- `test_advertising_slice7_migration.py` — 7 migration additivity/symmetry tests.

---

## 3. Migrations + rollback evidence

`0009_advertising_delivery.sql` creates exactly three tables, all additive:

- `business_os_ad_delivery_instances` — one row per server-authorized opportunity to
  display ONE approved creative version. Binds the exact hierarchy + `creative_version`
  eligible at decision time, a privacy-safe `subject_ref`, an `impression_token`
  secret, an `eligibility_snapshot_json`, and `expires_at`. The creative can never be
  substituted because it is read back from this row, never from client input.
- `business_os_ad_impression_events` — immutable append-only; `dedup_key` UNIQUE makes
  duplicates idempotent; index `idx_ad_impr_freq (campaign_id, subject_ref, event_at)`
  backs the frequency cap; `billing_eligible` / `billing_processed` carry the canonical
  reference for the next slice WITHOUT moving money.
- `business_os_ad_click_events` — immutable append-only; requires an accepted
  impression; destination is server-resolved; `dedup_key` UNIQUE.

Rollback (`.down.sql`) drops only those 3 tables + their indexes, symmetric with the
up migration; it references nothing from slices 1–6, the ledger, or legacy tables.

**Observed** (`test_advertising_slice7_migration.py`, 7/7): up creates only the three
slice-7 tables; the frequency index is exactly `(campaign_id, subject_ref, event_at)`;
both event logs enforce `UNIQUE(dedup_key)`; the up direction contains no
DROP/DELETE/ALTER/TRUNCATE; re-running up is idempotent; down is symmetric and leaves
the DB empty.

---

## 4. API contracts (canonical namespace)

### Viewer (dark 404 when flag off; auth from session; CSRF on writes)
- `POST /api/business-os/advertising/delivery/<placement>` → one sponsored placement or
  `sponsored: null`. Body accepts only non-PII request signals
  (`country, region, language, locale, device_class, viewer_age, request_id`);
  unknown fields → 400 `unknown_field`. `placement ∈ {feed, reels}`, else 400
  `bad_placement`.
- `POST /api/business-os/advertising/deliveries/<delivery_id>/impression` → records one
  impression. Body: `token` (required, else 400 `missing_token`), `placement`,
  `idempotency_key`, `request_meta`. `Idempotency-Key` header is folded into the body.
- `POST /api/business-os/advertising/deliveries/<delivery_id>/click` → records one
  click. Body: `idempotency_key`, `request_meta` only. Destination is
  **server-resolved**; the client cannot supply one.

### Admin (owner-guarded; 409 when flag off; strictly READ-ONLY GET)
- `GET /admin/business-os/advertising/deliveries` (+ `/<delivery_id>`)
- `GET /admin/business-os/advertising/impressions`
- `GET /admin/business-os/advertising/clicks`

Curated error codes surfaced by the controller: `bad_placement` (400),
`rate_limited` (429), `bad_token` (403), `expired` (409), `placement_mismatch` (409),
`impression_required` (409), `unknown_field` (400), `missing_token` (400).

---

## 5. Permissions & security

- Viewer identity is resolved from the authenticated session, **never** the body.
- Writes require CSRF (`pulse_ads_verify_write`); admin reads require the owner RBAC
  guard (`require_owner_api`).
- `impression_token` = HMAC-SHA256(secret, delivery_id), verified with
  `hmac.compare_digest`. A wrong token writes nothing (403).
- Rate limit: `REQUEST_RATE_MAX` = 60 requests / `REQUEST_RATE_WINDOW` (env-tunable) →
  429 `rate_limited`.
- The admin delivery view excludes `impression_token`.

---

## 6. Privacy (spec §9)

- No raw PII is stored. The viewer reference is `subject_ref` = salted SHA-256 of the
  user id, truncated — derived from `BUSINESS_OS_AD_SUBJECT_SALT` (dev default when
  unset). The frequency cap and all events key on `subject_ref`, never a raw user id.
- The sponsored payload leaks no internal ledger / targeting / reviewer / hierarchy
  fields. **Observed** (`test_payload_hides_internal_fields`): none of
  `advertiser_user_id, campaign_id, ad_set_id, subject_ref, eligibility_snapshot_json,
  request_ref, review_reason, reviewer, audience, targeting, price, budget_cents,
  escrow` appear; the advertiser object carries no account internals.
- The request allowlist rejects any extra field (e.g. an injected `ssn`) with 400
  `unknown_field`.

---

## 7. Spend / billing boundary (spec §10) — NO money moves

Every impression and click records `billing_eligible` (a boolean signal:
`true` for a clean view, `false` for a self-view) and `billing_processed = false`.
**No ledger entry is posted, no escrow is consumed, no funds are deducted.**
**Observed** (`test_delivery_flow_no_spend`): wallet and escrow balances are byte-for-
byte unchanged across a full request → impression → click flow.

---

## 8. Fraud / abuse (spec §8)

- Rate limiting (429), duplicate rejection (idempotent replay returns `duplicate:true`
  with the same `event_id`, no second row), expired-delivery rejection (409),
  placement-mismatch rejection (409), and a self-view signal.
- Self-view: when the viewer equals the served delivery's advertiser, `fraud_status =
  self_view` and `billing_eligible = false`. **Observed**
  (`test_self_view_fraud_not_billable`).

---

## 9. Tests + observed results

| Suite | Layer | Result |
|---|---|---|
| `test_advertising_slice7_delivery.py` | service end-to-end | **17/17 PASS** |
| `test_advertising_slice7_api.py` | controller contract | **11/11 PASS** |
| `test_advertising_slice7_routes.py` | bot.py route wiring (AST) | **10/10 PASS** |
| `test_advertising_slice7_migration.py` | migration additivity/symmetry | **7/7 PASS** |
| **Slice 7 total** | | **45/45 PASS** |

Full advertising regression (all slices, small batches):

| Slice | Suites | Result |
|---|---|---|
| 1 | slice1 | 11/11 |
| 2 | api + routes | 8 + 6 |
| 3 | api + routes | 13 + 6 |
| 4 | api + routes | 15 + 8 |
| 5 | api + routes | 15 + 9 |
| 6 | api + routes | 19 + 10 |
| 7 | delivery + api + routes + migration | 17 + 11 + 10 + 7 |
| **Advertising total** | | **165/165 PASS** |

Foundation regression (no cross-contamination): ledger+webhook 6/6, Stripe→ledger
handler 7/7, entitlements 26/26.

bot.py structural verification (not importable in sandbox): `python -m py_compile
bot.py` clean + `ast.parse` OK; all 7 new routes present under the canonical namespace.

---

## 10. Admin read-only visibility (spec §13)

Admins can search delivery instances and inspect one with hierarchy context, and can
search impression + click events, all through owner-guarded GET endpoints. The surface
is strictly read — no handler can fabricate an event or mutate a delivery, the routes
carry no `log_admin_audit` state write, and the client `impression_token` is never
exposed. **Observed** (`test_admin_visibility_read_only`, `test_admin_read_shapes`).

---

## 11. Observability status — **PARTIAL**

There is no metrics/telemetry backend wired in this repo, so per-spec §14 observability
is reported as **PARTIAL**: the immutable delivery/impression/click logs plus the
structured `fraud_status` and `billing_eligible` fields provide full audit-trail
observability via the admin read endpoints, but there are no counters, histograms, or
dashboards. Adding a metrics sink is deferred.

---

## 12. Native client contract (spec §12 — DEFINED, not built)

No native UI was modified. The contract the Expo/React Native client will implement:

1. **Request a placement** when a Feed/Reels slot is about to render:
   `POST /api/business-os/advertising/delivery/{feed|reels}` with only non-PII signals.
   If `sponsored` is `null`, render nothing (organic slot). Otherwise render the
   sponsored card using `headline`, `body`, `media`, `call_to_action`,
   `accessibility_text`, and the mandatory `disclosure` (`sponsored_label` = "Sponsored",
   `kind` = "paid_advertisement"). The client must always show the disclosure.
2. **Report the impression** once the card is actually displayed:
   `POST /api/business-os/advertising/deliveries/{delivery_id}/impression` echoing the
   opaque `impression_token` from the payload. Send an `Idempotency-Key` (or
   `idempotency_key`) so retries are safe. Do NOT send any hierarchy or price.
3. **Report the click** when the user taps the card:
   `POST /api/business-os/advertising/deliveries/{delivery_id}/click`. The client does
   **not** send a destination — it deep-links to the `destination` object already in the
   payload (display-only), and the server independently resolves the authoritative
   destination from the bound creative. A click before an impression is rejected (409).
4. **Expiry:** honor `expires_at`; a stale delivery's impression/click is rejected
   (409 `expired`) — request a fresh placement instead.

`delivery_id` and `impression_token` are the only advertising identifiers the client
holds; both are opaque. This section is a specification only — no client code ships in
this slice.

---

## 13. Known risks & non-goals

- **Not built (by design):** auctions/bidding, pacing/budget-optimization, attribution
  modelling, billing-event generation + escrow consumption, reporting dashboards,
  native UI. Billing is the explicit next slice; the `billing_eligible` flag is the
  handoff point.
- **Deterministic rotation** gives a stable per-viewer creative but is not
  yield-optimizing; that is intentional and swappable behind `SelectionStrategy`.
- **Observability is PARTIAL** (see §11).
- **Frequency cap default** `FREQ_CAP_MAX = 3` per viewer per campaign per rolling
  `86400s` window; delivery TTL `1800s`; rate limit `60`/window — all env-tunable.
- **Unrelated pre-existing issue (documented separately, NOT touched here):** the Reels
  media-playback / preload protection failure remains open and is out of scope for this
  slice. See §14.

---

## 14. Unrelated Reels preload protection failure (carried forward)

This is a pre-existing, unrelated defect tracked independently of the advertising work.
It concerns Reels media playback/preload protection in the native client and is **not**
a delivery/advertising regression — slice 7 adds no Reels UI and modifies no Reels
playback code. It is recorded here only so it is not lost; it should be addressed in the
Reels media-playback workstream, not the advertising slices.

---

## 15. Owner-side staging guide

1. Review the diff for `bot.py`, `services/business_os/advertising/*.py`,
   `migrations/business_os/0009_advertising_delivery.sql(.down.sql)`, and the four
   `tests/business_os/test_advertising_slice7_*.py` files.
2. Apply migration `0009_advertising_delivery.sql` (additive; safe rollback available
   via the paired `.down.sql`).
3. Deploy with `BUSINESS_OS_ADVERTISING` **off** first — the entire surface stays dark
   (viewer 404 / admin 409), so the deploy is inert until you flip it.
4. Optionally set `BUSINESS_OS_AD_SUBJECT_SALT` and `BUSINESS_OS_AD_TOKEN_SECRET` to
   production secrets (dev defaults are used otherwise). Tune `BUSINESS_OS_AD_FREQ_CAP`,
   `BUSINESS_OS_AD_FREQ_WINDOW`, `BUSINESS_OS_AD_DELIVERY_TTL`,
   `BUSINESS_OS_AD_REQ_RATE_MAX` as desired.
5. Flip the flag on in staging, exercise `delivery → impression → click`, and confirm
   via the admin read endpoints. Wallet/escrow balances must remain unchanged (no spend
   in this slice).
6. Commit owner-side (the sandbox never writes `.git`).
