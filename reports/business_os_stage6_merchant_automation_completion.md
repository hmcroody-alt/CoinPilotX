# Business OS — Stage 6 Merchant Automation — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **third Stage 6 vertical** — an **informational-only**, **deterministic** merchant-automation rule engine. A merchant declares rules (`signal_type <operator> threshold → suggest action_type`); the engine records an append-only log of rule definitions and of measured merchant *signals*, and computes a rebuildable, per-merchant projection of **proposed actions** by evaluating every active rule against the latest signal per subject. Gated behind `BUSINESS_OS_MERCHANT_AUTOMATION`.
**Hard boundary:** **no money moves, no action is taken.** A proposal is a *suggestion* — a reporting quantity, not an instruction. Nothing here reorders stock, places an order, adjusts a price, sends a notification, or bills anyone. It is a lens over merchant state that surfaces what a human (or a separately-reviewed integration) *could* choose to do.
**Pattern:** strangler — a new canonical `business_os_merchant_*` surface is built beside any existing merchant/automation admin surfaces; nothing legacy is read or written. The vertical mirrors the attribution / recommendations / crypto modules' discipline (append-only truth + rebuildable projection, idempotent ingest, dark-404 gating, curated error codes) exactly.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 9 | Canonical `business_os_merchant_*` schema (append-only rule + signal logs, rebuildable proposal projection, audit) | **PASS** |
| 10 | Deterministic rule engine (six numeric operators; latest-signal state; active-only; idempotent re-evaluate) | **PASS** |
| 11 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 12 | Full test matrix + attribution / recommendations / crypto / entitlement / marketplace / payments / advertising / IAP regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/merchant_automation/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_merchant_rules` (append-only rule defs: `signal_type`, `operator`, numeric `threshold`, suggested `action_type`, `active` toggle, `priority`), `business_os_merchant_signals` (append-only fact log; the latest row per `(merchant_id, subject_ref, signal_type)` is the current state), `business_os_merchant_proposals` (the rebuildable per-merchant proposed-action projection), and `business_os_merchant_audit`. Text UUID PKs, SQLite/Postgres portable. An `operator` CHECK constrains the enum (`lt/lte/gt/gte/eq/ne`); a UNIQUE `(source, external_ref)` on both the rule and signal logs makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(merchant_id, rule_id, subject_ref)` makes a proposal row exactly-once.
- `engine.py` — the evaluation core. `record_rule` / `record_signal` append immutable rows and no-op on a replayed `(source, external_ref)`. `evaluate_merchant(merchant_id)` reads every **active** rule and the **latest** signal per `(subject_ref, signal_type)`, compares each observed value against the rule's threshold under the named operator, and writes a deterministic ranked list of proposed actions. Operators are the six numeric comparisons applied over `Decimal` values (transparent, engine-portable). Ordering is a strict tie-break — **priority descending, then `rule_id` ascending, then `subject_ref` ascending** — so the output is fully reproducible. Re-evaluation is a deterministic replace; the proposals table is a projection, always rebuildable from the two logs. Report helpers return a merchant's declared rules, current (latest-per-key) signal state, and stored proposals.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_MERCHANT_AUTOMATION` is off; curated error codes only (`missing_fields`, `missing_payload`, `invalid_rule`, `invalid_signal`, `invalid_request`) — never an internal exception string. The proposals read is **compute-on-read** (it evaluates the merchant once if the projection is empty). Rule/signal ingest and evaluate are operator entry points.
- `bot.py` — 6 thin authenticated routes (`POST`/`GET .../merchant/rules`, `POST`/`GET .../merchant/signals`, `GET .../merchant/proposals`, `POST .../merchant/evaluate`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Evaluation is deterministic.** There is no randomness anywhere. The output is a stable order with an explicit tie-break — priority descending, then `rule_id` ascending, then `subject_ref` ascending — proven by a test that gives one rule two matching subjects and a higher-priority rule and asserts high-priority proposals rank first and, within a rule, subjects sort ascending. Two evaluations of the same inputs yield identical proposal lists.

**The logs are the authority; proposals are a projection.** Rules and signals are immutable rows; the proposal list is recomputed deterministically from them and is always rebuildable. Re-evaluation is a replace, so re-running after a crash yields identical rows — proven by a test that evaluates twice, asserts equality, and asserts exactly one row per `(merchant, rule, subject)` (no duplication).

**Latest signal wins.** The engine compares against the most recent observed value per `(subject, signal_type)` — proven by a test where an early low reading matches, a later high reading supersedes it (no proposal), and a still-later low reading brings the proposal back.

**Only active rules evaluate.** An `active=false` rule is skipped even when a signal would satisfy it — proven by a focused test.

**The six operators behave as specified.** `lt / lte / gt / gte / eq / ne` are each proven at and around their boundary (e.g. `gte 10` matches value 10 but `gte 11` does not; `ne 10` excludes value 10) by a matrix test.

**Ingest is idempotent.** A feed replaying the same `(source, external_ref)` on either the rule log or the signal log returns the existing row and creates no duplicate; NULL external refs (manual entries) are exempt from the unique constraint.

**No action is taken.** A proposal only records a *suggested* `action_type` — proven by a test asserting that after evaluation the only `business_os_merchant_*` tables that exist are the four canonical ones and the proposal row carries the suggested action, nothing more.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Merchant automation — 28/28**

| Suite | Result |
|-------|--------|
| `test_merchant_schema.py` (tables, idempotency, CHECK, rule+signal dedupe indexes + NULL-exempt, projection key, legacy untouched) | 10/10 |
| `test_merchant_engine.py` (ingest+dedupe, each operator, latest-wins, active-only, deterministic ordering, idempotent re-evaluate, no side effects) | 9/9 |
| `test_merchant_api.py` (controller contract: dark, validation, compute-on-read, rules/signals reports, evaluate) | 9/9 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Recommendations (Stage 6 Part 2) | 3 | 27/27 |
| Attribution (Stage 6 Part 1) | 3 | 27/27 |
| Crypto (Stage 5) | 5 | 38/38 |
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2) | 22 | 218/218 |
| IAP / Premium (Stage 4) | 3 | 26/26 |

**Total: 471 tests, 0 failures** (443 prior regression unchanged + 28 new merchant automation). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new merchant routes have unique endpoint function names (`api_business_os_merchant_record_rule`, `_record_signal`, `_rules_report`, `_signals_report`, `_proposals_report`, `_evaluate`); the GET/POST pairs on `/merchant/rules` and `/merchant/signals` are method-distinguished exactly as the existing attribution/recommendations/marketplace routes are.

---

## 5. Honest limitations

- **Rules are threshold comparisons, not a workflow language.** A rule is a single `signal_type <operator> numeric-threshold` test. There is no compound boolean logic (AND/OR across signals), no time-window aggregation, no rate-of-change trigger. Those are a larger, separate effort; the current engine is a transparent per-signal threshold lens.
- **Proposal ≠ action.** These proposals are a reporting projection, not a queued job. Wiring a proposal into an actual reorder, a price change, a notification, or any side-effecting workflow would be a separate, separately-reviewed integration on top of the product's real operational systems.
- **Signals are caller-supplied.** The engine evaluates whatever rules and signals are recorded; wiring the product's real merchant telemetry (inventory counts, sales velocity, review scores) into this log as durable feeds keyed by `external_ref` is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Thresholds and priorities are caller-declared and transparent.** The engine applies no hidden weighting; a rule's `priority` orders proposals and its `threshold` is compared verbatim. Tuning is a merchant/product decision, not baked in.
- **State is single latest value per key.** Current state is the most recent signal per `(subject, signal_type)`; multi-signal correlation or historical trend evaluation is out of scope for this slice.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_MERCHANT_AUTOMATION` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The merchant-automation modules are additive; no legacy table is read or written (proven by `test_legacy_untouched`, which asserts only the four `business_os_merchant_*` tables were created). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 remaining verticals (roadmap)

Attribution (Part 1), Recommendations (Part 2), and Merchant Automation (this slice) are now delivered. Still to come per §15: **creator commerce**, **governed UNDX business actions**, **localization**, and **performance** — each to follow the same strangler pattern (canonical `business_os_*` tables, dedicated flag, thin `bot.py` adapters, standalone tests, full regression, completion report).
