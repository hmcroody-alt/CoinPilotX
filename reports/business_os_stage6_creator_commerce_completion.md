# Business OS — Stage 6 Creator Commerce — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **fourth Stage 6 vertical** — an **informational-only**, **deterministic** creator-commerce earnings/tier engine. A creator declares *offerings* (a named support option — a membership tier, a subscription, a tip jar, a one-off product); an append-only log records *contribution* facts (a supporter contributed an amount toward an offering at a point in time); the engine computes a rebuildable, per-creator projection: summed support per supporter and per offering, a deterministic supporter **tier** by cumulative-support threshold, and a ranked top-supporter list. Gated behind `BUSINESS_OS_CREATOR_COMMERCE`.
**Hard boundary:** **no money moves, no payout is made, no one is charged.** Earnings here are a *reporting quantity* summarizing contributions that already happened elsewhere; a tier is a *label*, not an entitlement grant. Nothing posts to the ledger, pays out, bills, or unlocks anything.
**Pattern:** strangler — a new canonical `business_os_creator_*` surface is built beside any existing creator/monetization surfaces; nothing legacy is read or written. The vertical mirrors the attribution / recommendations / merchant-automation / crypto modules' discipline (append-only truth + rebuildable projection, idempotent ingest, dark-404 gating, curated error codes) exactly.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 13 | Canonical `business_os_creator_*` schema (append-only offering + contribution logs, rebuildable supporter/tier projection, audit) | **PASS** |
| 14 | Deterministic earnings/tier engine (per-supporter totals; fixed tier thresholds; ranked; idempotent recompute; per-offering rollup) | **PASS** |
| 15 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 16 | Full test matrix + attribution / recommendations / merchant / crypto / entitlement / marketplace / payments / advertising / IAP regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/creator_commerce/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_creator_offerings` (append-only offering catalog: `offering_type`, optional transparent `unit_amount`, `active` toggle), `business_os_creator_contributions` (append-only fact log; a supporter contributed an amount toward an offering at `occurred_at`), `business_os_creator_supporters` (the rebuildable per-(creator, supporter) rollup projection: `total_amount`, `contribution_count`, `tier`, `rank`), and `business_os_creator_audit`. Text UUID PKs, SQLite/Postgres portable. An `offering_type` CHECK constrains the enum (`membership/subscription/tip/product`); a UNIQUE `(source, external_ref)` on both input logs makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(creator_id, supporter_id)` makes a projection row exactly-once.
- `engine.py` — the projection core. `record_offering` / `record_contribution` append immutable rows and no-op on a replayed `(source, external_ref)`. `compute_creator(creator_id)` sums every contribution per supporter, counts contributions, assigns a deterministic **tier** by cumulative support against fixed transparent thresholds (`bronze >= 0`, `silver >= 25`, `gold >= 100`, `platinum >= 500`), and writes a ranked supporter list. All amounts are `Decimal` (transparent, engine-portable) quantized to `0.01`. Ordering is a strict tie-break — **total support descending, then `supporter_id` ascending** — so the output is fully reproducible. Recompute is a deterministic replace (delete-then-insert); the supporter table is a projection, always rebuildable from the two logs. `earnings_report` rolls up total support and per-offering totals on the fly from the same contribution log.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_CREATOR_COMMERCE` is off; curated error codes only (`missing_fields`, `missing_payload`, `invalid_offering`, `invalid_contribution`, `invalid_request`) — never an internal exception string. The supporters read is **compute-on-read** (it recomputes the creator once if the projection is empty). Offering/contribution ingest and recompute are operator entry points; earnings/offerings reports are read-only.
- `bot.py` — 6 thin authenticated routes (`POST`/`GET .../creator/offerings`, `POST .../creator/contributions`, `GET .../creator/supporters`, `GET .../creator/earnings`, `POST .../creator/recompute`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Projection is deterministic.** There is no randomness anywhere. The supporter order is a stable tie-break — total support descending, then `supporter_id` ascending — proven by a test that gives two supporters equal totals and asserts `alpha` ranks before `zebra`, plus a distinct-totals test asserting `big`(300) > `mid`(60) > `small`(5). Two recomputes of the same inputs yield identical supporter lists.

**The logs are the authority; supporters are a projection.** Offerings and contributions are immutable rows; the supporter/tier list is recomputed deterministically from them and is always rebuildable. Recompute is a replace, so re-running after a crash yields identical rows — proven by a test that computes twice, asserts equality, and asserts exactly one row per `(creator, supporter)` (no duplication).

**Tiers are assigned by fixed transparent thresholds.** `bronze >= 0`, `silver >= 25`, `gold >= 100`, `platinum >= 500` on cumulative support — proven at and around every boundary by a matrix test (e.g. 24.99 is bronze but 25.00 is silver; 99.99 is silver but 100.00 is gold; 499.99 is gold but 500.00 is platinum).

**Contribution amounts sum correctly per supporter.** A supporter's multiple contributions are summed into one total and counted — proven by a test where `mid` contributes 50.00 then 10.00 and is asserted at 60.00 with `contribution_count == 2`.

**Ingest is idempotent.** A feed replaying the same `(source, external_ref)` on either the offering log or the contribution log returns the existing row and creates no duplicate; NULL external refs (manual entries) are exempt from the unique constraint.

**Bad input is curated, never raw.** A non-numeric or negative amount, and an unknown `offering_type`, raise the module's curated `CreatorCommerceError`; the controller maps these to `invalid_contribution` / `invalid_offering` — never an internal exception string.

**No money moves.** Earnings are summed contributions and a tier is a label — proven by a test asserting that after ingest and recompute the only `business_os_creator_*` tables that exist are the four canonical ones; nothing pays out, charges, or unlocks.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Creator commerce — 25/25**

| Suite | Result |
|-------|--------|
| `test_creator_schema.py` (tables, idempotency, offering_type CHECK, offering+contribution dedupe indexes + NULL-exempt, projection key, legacy untouched) | 7/7 |
| `test_creator_engine.py` (ingest+dedupe, curated bad amount/type, totals+ranking, tie-break, tier boundaries, idempotent recompute, per-offering rollup, no side effects) | 9/9 |
| `test_creator_api.py` (controller contract: dark, validation, compute-on-read supporters, offerings/earnings reports, recompute) | 9/9 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Merchant automation (Stage 6 Part 3) | 3 | 28/28 |
| Recommendations (Stage 6 Part 2) | 3 | 27/27 |
| Attribution (Stage 6 Part 1) | 3 | 27/27 |
| Crypto (Stage 5) | 5 | 38/38 |
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2) | 22 | 218/218 |
| IAP / Premium (Stage 4) | 3 | 26/26 |

**Total: 496 tests, 0 failures** (471 prior regression unchanged + 25 new creator commerce). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new creator routes have unique endpoint function names (`api_business_os_creator_record_offering`, `_record_contribution`, `_offerings_report`, `_supporters_report`, `_earnings_report`, `_recompute`); the GET/POST pair on `/creator/offerings` is method-distinguished exactly as the existing attribution/recommendations/merchant/marketplace routes are.

---

## 5. Honest limitations

- **Earnings ≠ payout.** These totals are a reporting projection summarizing contributions that already happened elsewhere. Wiring an earnings figure into an actual creator payout, a ledger credit, or a payment run would be a separate, separately-reviewed integration on top of the product's real financial systems.
- **A tier is a label, not an entitlement.** The engine assigns a tier by cumulative-support threshold; it does not unlock any capability, grant premium access, or gate content. Turning a tier into an entitlement would be a deliberate wiring into the entitlement system, not something this vertical does.
- **Contributions are caller-supplied.** The engine computes over whatever offerings and contributions are recorded; wiring the product's real support telemetry (memberships, tips, subscription renewals) into this log as durable feeds keyed by `external_ref` is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Tier thresholds are fixed and transparent.** `bronze/silver/gold/platinum` cut at 0/25/100/500 cumulative support, applied verbatim with no hidden weighting. Making thresholds per-creator-configurable is a larger, separate effort; the current engine is a transparent fixed-threshold lens.
- **State is cumulative lifetime support.** A supporter's tier reflects all-time summed contributions; time-windowed tiers (e.g. "gold this month"), decay, or refund-adjusted balances are out of scope for this slice. Corrections are new contribution rows, not edits.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_CREATOR_COMMERCE` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The creator-commerce modules are additive; no legacy table is read or written (proven by `test_legacy_untouched`, which asserts only the four `business_os_creator_*` tables were created). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 remaining verticals (roadmap)

Attribution (Part 1), Recommendations (Part 2), Merchant Automation (Part 3), and Creator Commerce (this slice) are now delivered. Still to come per §15: **governed UNDX business actions**, **localization**, and **performance** — each to follow the same strangler pattern (canonical `business_os_*` tables, dedicated flag, thin `bot.py` adapters, standalone tests, full regression, completion report).
