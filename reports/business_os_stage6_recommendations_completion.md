# Business OS — Stage 6 Recommendations Intelligence — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **second Stage 6 vertical** — an **informational-only**, **deterministic** recommendation engine. It records an append-only log of a catalog of recommendable *items* and of implicit-feedback *interactions* (view / click / like / purchase / dismiss) between users and items, and computes a rebuildable, per-user **ranked recommendation** projection under four transparent models. Gated behind `BUSINESS_OS_RECOMMENDATIONS`.
**Hard boundary:** **no money moves, no action is taken.** A recommendation is a *suggestion* — a reporting quantity, not an instruction. Nothing here posts to the ledger, sends a notification, mutates a feed, or bills anyone. It is a lens over engagement that already happened elsewhere.
**Pattern:** strangler — a new canonical `business_os_rec_*` surface is built beside the existing legacy "recommendation/intelligence" admin dashboards; nothing legacy is read or written. The vertical mirrors the attribution and crypto modules' discipline (append-only truth + rebuildable projection, idempotent ingest, dark-404 gating, curated error codes) exactly.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 5 | Canonical `business_os_rec_*` schema (append-only item catalog + interaction log, rebuildable recommendation projection, audit) | **PASS** |
| 6 | Deterministic recommendation engine (popularity / content-based / collaborative / hybrid; seen-exclusion; idempotent recompute) | **PASS** |
| 7 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 8 | Full test matrix + attribution / crypto / entitlement / marketplace / payments / advertising / IAP regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/recommendations/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_rec_items` (append-only catalog of recommendable objects with a JSON `tags` array + `item_type`/`category` facets), `business_os_rec_interactions` (append-only implicit-feedback log; a user viewed/clicked/liked/purchased/dismissed an item at an integer `weight`), `business_os_rec_recommendations` (the rebuildable per-user ranked projection), and `business_os_rec_audit`. Text UUID PKs, SQLite/Postgres portable. An `interaction_type` CHECK constrains the enum; a UNIQUE `(source, external_ref)` on the interaction log makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(user_id, model, item_id)` makes a recommendation row exactly-once.
- `engine.py` — the ranking core. `record_item` / `record_interaction` append immutable rows and no-op on a replayed `(source, external_ref)` (and on a re-declared `item_id`). `compute_recommendations(user_id, model)` scores unseen candidate items under a named model and writes a deterministic ranked list. Models: `popularity` (global summed positive weight), `content_based` (weighted overlap between a candidate's tags and the user's engaged-tag profile), `collaborative` (item-to-item co-occurrence via Jaccard over the user sets who engaged each item), `hybrid` (a fixed normalized blend: 0.2 popularity / 0.4 content / 0.4 collaborative). Every model excludes items the user has already engaged (any interaction, **including `dismiss`**), and orders by a strict tie-break — **score descending, then `item_id` ascending** — so the output is fully reproducible. Recompute is a deterministic replace; the recommendation table is a projection, always rebuildable from the two logs. Report helpers return a user's interaction history and a global item-popularity table.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_RECOMMENDATIONS` is off; curated error codes only (`missing_fields`, `missing_payload`, `invalid_item`, `invalid_interaction`, `invalid_model`, `unauthenticated`) — never an internal exception string. The recommendations read is **compute-on-read** (it builds the user/model projection once if empty) and is **scoped to the calling user**. Catalog ingest and recompute are operator entry points; recompute is self-scoped to the authenticated user.
- `bot.py` — 6 thin authenticated routes (`POST .../recommendations/items`, `POST`/`GET .../recommendations/interactions`, `GET .../recommendations`, `GET .../recommendations/report/popularity`, `POST .../recommendations/recompute`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Ranking is deterministic.** There is no randomness anywhere. Every model produces a stable order with an explicit tie-break — score descending, then `item_id` ascending — proven by a test that gives two items identical popularity weight and asserts the lexicographically-smaller `item_id` ranks first. Two recomputes of the same inputs yield byte-identical rows.

**The logs are the authority; recommendations are a projection.** Items and interactions are immutable rows; the ranked list is recomputed deterministically from them and is always rebuildable. Recompute is a replace, so re-running after a crash yields identical rows — proven by a test that recomputes all four models twice, asserts equality, and asserts exactly one row per `(user, model, item)` (no duplication).

**Already-engaged items are never re-recommended.** Any interaction on an item — including a `dismiss`, which is explicit negative feedback — marks it "seen" and excludes it from that user's recommendations, even if it is globally popular — proven by a test that dismisses a high-popularity item and asserts it never surfaces.

**The four models behave as specified**, each proven by a focused test: popularity ranks strictly by summed positive weight; content-based recommends only candidates that share a tag with the user's engaged-tag profile (a no-overlap item is excluded, and the matched tag appears in the reason string); collaborative recommends an item co-engaged by the same users who engaged the user's items (Jaccard co-occurrence) and excludes an item with no co-occurrence; hybrid blends the normalized components and still excludes seen items.

**Ingest is idempotent.** A feed replaying the same `(source, external_ref)` returns the existing row and creates no duplicate; a re-declared `item_id` is a no-op; NULL external refs (manual entries) are exempt from the unique constraint.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Recommendations — 27/27**

| Suite | Result |
|-------|--------|
| `test_rec_schema.py` (tables, idempotency, CHECK, dedupe indexes + NULL-exempt, projection key, legacy untouched) | 9/9 |
| `test_rec_engine.py` (ingest+dedupe, each model, seen-exclusion, dismiss-exclusion, deterministic tie-break, idempotent recompute) | 10/10 |
| `test_rec_api.py` (controller contract: dark, validation, compute-on-read + user-scoped, popularity report, recompute) | 8/8 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Attribution (Stage 6 Part 1) | 3 | 27/27 |
| Crypto (Stage 5) | 5 | 38/38 |
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2) | 22 | 218/218 |
| IAP / Premium (Stage 4) | 3 | 26/26 |

**Total: 443 tests, 0 failures** (416 prior regression unchanged + 27 new recommendations). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new recommendation routes have unique endpoint function names (`api_business_os_rec_record_item`, `_record_interaction`, `_recommendations_report`, `_interactions_report`, `_popularity_report`, `_recompute`); the GET/POST pair on `/recommendations/interactions` is method-distinguished exactly as the existing attribution/crypto/marketplace routes are.

---

## 5. Honest limitations

- **Recommendations are heuristic, not learned.** The four models are transparent, deterministic industry-standard lenses (popularity, content overlap, item-item co-occurrence, a fixed blend). They are not a trained model and do not optimize a held-out objective; there is no learning-to-rank, no embeddings, no online bandit. Data-driven / learned ranking is a separate, larger effort.
- **Recommendation ≠ action.** These rankings are a reporting projection, not a delivered feed. Wiring recommendations into an actual surfaced feed, a notification, or an ordering decision would be a separate, separately-reviewed integration on top of the product's real presentation layer.
- **Interaction and catalog ingest are caller-supplied.** The engine ranks whatever items and interactions are recorded; wiring the product's real engagement events (feed views, likes, purchases) and item catalog into this log (as durable feeds keyed by `external_ref`) is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Weights are caller-declared and transparent.** Affinity is the integer `weight` on each positive interaction (a purchase can be recorded as a heavier weight than a view); the engine applies no hidden per-type multiplier. Tuning the weighting scheme is a product decision, not baked in.
- **Identity is single-key.** Recommendations are per `user_id`; cross-device / logged-out identity stitching is out of scope for this slice.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_RECOMMENDATIONS` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The recommendation modules are additive; no legacy table is read or written (proven by `test_legacy_untouched`, which asserts only the four `business_os_rec_*` tables were created). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 remaining verticals (roadmap)

Attribution (Part 1) and Recommendations (this slice) are the two analytical foundations. Still to come per §15: **merchant automation**, **creator commerce**, **governed UNDX business actions**, **localization**, and **performance** — each to follow the same strangler pattern (canonical `business_os_*` tables, dedicated flag, thin `bot.py` adapters, standalone tests, full regression, completion report).
