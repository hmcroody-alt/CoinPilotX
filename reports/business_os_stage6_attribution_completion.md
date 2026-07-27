# Business OS — Stage 6 Attribution Intelligence — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **first Stage 6 vertical** — an **informational-only** multi-touch **attribution** engine. It records an append-only log of *touchpoints* (impressions / clicks / engagements / visits) and *conversions* (purchases / subscriptions / signups) and computes a fractional, remainder-safe **credit** split of each conversion's value across the touchpoints in the converting user's path, under four standard models. Gated behind `BUSINESS_OS_ATTRIBUTION`.
**Hard boundary:** **no money moves.** Credit is a *reporting quantity*, not a payout instruction. Nothing here posts to the ledger, bills an advertiser, or pays a creator. It is a lens over events that already happened elsewhere.
**Pattern:** strangler — a new canonical `business_os_attr_*` surface is built beside any existing analytics; nothing legacy is read or written. The vertical mirrors the crypto module's discipline (integer cents, append-only truth + rebuildable projection, idempotent ingest, dark-404 gating) exactly.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 1 | Canonical `business_os_attr_*` schema (append-only touchpoint + conversion logs, rebuildable credit projection, audit) | **PASS** |
| 2 | Multi-touch attribution engine (last / first / linear / position-based; remainder-safe cents split; lookback; idempotent recompute) | **PASS** |
| 3 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 4 | Full test matrix + entitlement / marketplace / payments / advertising / IAP / crypto regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/attribution/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_attr_touchpoints` (append-only exposure/click log — a path element), `business_os_attr_conversions` (append-only conversion log with integer-cent value + lookback window), `business_os_attr_credits` (the rebuildable per-touchpoint credit projection), and `business_os_attr_audit`. Text UUID PKs, SQLite/Postgres portable. A `touch_type` CHECK constrains the enum; a UNIQUE `(source, external_ref)` on each log makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(conversion_id, model, touchpoint_id)` makes credit exactly-once.
- `engine.py` — the attribution core. `record_touchpoint` / `record_conversion` append immutable rows and no-op on a replayed `(source, external_ref)`. `compute_credits(conversion_id, model)` gathers the converting user's eligible touchpoints inside the lookback window (at or before the conversion, oldest-first, deterministic tie-break), weights them by model, and splits the conversion's `value_cents` with the **largest-remainder method** so the per-touchpoint credits sum back to the value **exactly**. Models: `last_touch`, `first_touch`, `linear`, `position_based` (40% first / 40% last / 20% split across the middle; degrades to 100% at n=1 and 50/50 at n=2). Recompute is a deterministic replace — credit is a projection, always rebuildable from the two logs. Report helpers aggregate credit by campaign and by channel; `user_path` returns the ordered path.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_ATTRIBUTION` is off; curated error codes only (`missing_fields`, `invalid_touchpoint`, `invalid_conversion`, `invalid_model`, `unauthenticated`, `not_found`) — never an internal exception string. Recording a conversion auto-computes the requested (or default `last_touch`) model so the caller gets an immediate attributed result. A conversion's credit report is **scoped to the owning user** (a stranger gets 404).
- `bot.py` — 6 thin authenticated routes (`POST .../attribution/touchpoints`, `POST`/`GET .../attribution/conversions[/<id>]`, `GET .../attribution/path`, `GET .../attribution/report/campaigns`, `POST .../attribution/conversions/<id>/recompute`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Credit never drifts a penny.** A conversion's value is integer cents; the split floors each proportional share and hands the leftover cents to the largest fractional remainders (deterministic tie-break by index). The per-touchpoint credits therefore sum to the conversion value **exactly** for every model and every path length — proven by tests asserting `sum == value` on odd values (100 across 3 → `[34,33,33]`; 101 across 2 → `[51,50]`).

**The logs are the authority; credit is a projection.** Touchpoints and conversions are immutable rows; credit is recomputed deterministically from them and is always rebuildable. Recompute is a replace, so re-running after a crash yields byte-identical rows — proven by a test that recomputes all models twice and asserts equality plus exactly one credit row per `(conversion, model, touchpoint)`.

**Lookback is enforced.** Only touchpoints inside `[conversion_time − lookback_days, conversion_time]` are eligible; a stale touch outside the window contributes nothing — proven by a test with a 40-day-old touch excluded under a 30-day window.

**Ingest is idempotent.** A feed replaying the same `(source, external_ref)` returns the existing row and creates no duplicate; NULL external refs (manual entries) are exempt from the unique constraint.

**Verified invariants:** last-touch credits 100% to the final touch; first-touch to the first; linear splits equally and exactly; position-based is U-shaped (40/20-split/40) and degrades correctly at n≤2; a zero-touch conversion is *unattributed* (0 credit, no rows) rather than mis-credited; a conversion's credit report is user-scoped; unknown models and negative values are rejected with curated codes.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Attribution — 27/27**

| Suite | Result |
|-------|--------|
| `test_attr_schema.py` (tables, idempotency, CHECK, dedupe indexes, legacy untouched) | 9/9 |
| `test_attr_engine.py` (each model, remainder-safe sum, lookback, idempotent recompute, unattributed, reports) | 9/9 |
| `test_attr_api.py` (controller contract: dark, validation, auto-attribute, scoped report, recompute) | 9/9 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Crypto (Stage 5) | 5 | 38/38 |
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2) | 22 | 218/218 |
| IAP / Premium (Stage 4) | 3 | 26/26 |

**Total: 416 tests, 0 failures** (389 prior regression unchanged + 27 new attribution). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new attribution routes have unique endpoint function names (`api_business_os_attr_record_touchpoint`, `_record_conversion`, `_conversion_report`, `_path_report`, `_campaign_report`, `_recompute`); the GET/POST pair on `/attribution/conversions` is method-distinguished exactly as the existing crypto/marketplace routes are.

---

## 5. Honest limitations

- **Attribution is descriptive, not causal.** Multi-touch credit apportions a conversion across observed touchpoints by a chosen heuristic; it does not prove any touchpoint *caused* the conversion. The four models are industry-standard lenses, not ground truth. Data-driven / incrementality attribution is a separate, larger effort.
- **Credit ≠ money.** These credit cents are a reporting split, not a ledger entry. Wiring attribution into advertiser billing or creator payouts would be a separate, separately-reviewed integration on top of the canonical ledger.
- **Touchpoint ingest is caller-supplied.** The engine attributes whatever touchpoints are recorded for a user; wiring the existing advertising impression/click events and marketplace/IAP conversions into this log (as durable feeds keyed by `external_ref`) is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Identity is single-key.** Attribution is per `user_id`; cross-device / logged-out identity stitching is out of scope for this slice.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_ATTRIBUTION` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The attribution modules are additive; no legacy table is read or written (proven by `test_legacy_untouched`). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 remaining verticals (roadmap)

Attribution is the analytical foundation the rest of Stage 6 builds on. Still to come per §15: **recommendations**, **merchant automation**, **creator commerce**, **governed UNDX business actions**, **localization**, and **performance** — each to follow the same strangler pattern (canonical `business_os_*` tables, dedicated flag, thin `bot.py` adapters, standalone tests, full regression).
