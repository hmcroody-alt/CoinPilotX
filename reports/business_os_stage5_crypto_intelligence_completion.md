# Business OS — Stage 5 Crypto Intelligence — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up an **informational-only** crypto-intelligence vertical — lot-based cost-basis / realized+unrealized P&L over an append-only transaction log, one **unified market/quote read layer** over the three existing fragmented market services, and **durable, restart-safe price alerts** with per-crossing dedupe. Gated behind `BUSINESS_OS_CRYPTO`.
**Hard boundary:** **no custody, no trading.** Nothing in this stage places an order, moves funds, or takes custody of an asset. Recording a transaction is bookkeeping of something that already happened elsewhere; an alert is a notification trigger. Any future custody/trading capability remains out of scope and would require separate approval.
**Pattern:** strangler — a new canonical `business_os_crypto_*` surface is built beside the legacy `portfolio_items` / `manual_portfolio` / `user_alerts` / `watchlist_items` / `watchlists` tables, which are **never read or written**. The three legacy market modules (`market_data`, `market_service`, `live_market_service`) are **composed, not modified**.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 1 | Canonical `business_os_crypto_*` schema (append-only txn log, holdings/lots projection, durable alerts + fired-log, audit) | **PASS** |
| 2 | Cost-basis / P&L engine (FIFO + average, realized/unrealized, Decimal qty + integer-cents money) | **PASS** |
| 3 | Unified market/quote read layer over the three existing providers (no modification) | **PASS** |
| 4 | Durable, restart-safe alert evaluation (edge-detect + per-crossing dedupe) | **PASS** |
| 5 | Framework-agnostic API controllers + thin `bot.py` routes | **PASS** |
| 6 | Full test matrix + entitlement / marketplace / payments / advertising / IAP regression + this report | **PASS** |

---

## 2. What was built

Five modules under `services/business_os/crypto/` (plus five thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_crypto_transactions` (append-only lot log — the source of truth), `business_os_crypto_holdings` (rebuildable projection), `business_os_crypto_lots` (FIFO open-lot ledger), `business_os_crypto_alerts` (durable definitions + edge-detect state), `business_os_crypto_alert_events` (append-only fired-log), and `business_os_crypto_audit`. Text UUID PKs, SQLite/Postgres portable. A UNIQUE `(source, external_ref)` index makes external-feed ingest idempotent; a UNIQUE `(alert_id, crossing_key)` index makes alert firing idempotent.
- `engine.py` — the accounting core. A **buy** opens a lot at an all-in per-unit cost (unit price plus its allocated share of the fee) in integer cents; a **sell** consumes open lots — **FIFO** (oldest first) or **AVERAGE** (blended cost) — realizing `proceeds − consumed cost`. Quantities are `decimal.Decimal` throughout (satoshi-scale precision, never float); money is integer cents everywhere. `portfolio_summary` folds in a `symbol → price_cents` lookup for unrealized P&L, treating a missing price as *unknown* (never zero). Oversell is rejected; replayed `(source, external_ref)` is a no-op.
- `market.py` — the **one** canonical quote source. Composes the existing `market_data.get_symbol` (CoinGecko primary + Coinbase fallback) behind a single normalized contract that emits **integer-cent** prices, a 30s cache, and graceful degradation (a raising or empty upstream yields an `ok=False` stale quote, never a fabricated price). Exposes `price_cents_lookup()` for the engine and the alert sweeper. Does not touch the three underlying modules.
- `alerts.py` — durable price alerts. Comparators `above` / `below` / `crosses_above` / `crosses_below` fire only on an **edge** (state change), not on every tick in-region; the first observation *arms* state without firing. Each edge derives a deterministic `crossing_key` inserted under the UNIQUE index **before** the event is declared new, so a mid-sweep restart re-run is an idempotent no-op — a crossing notifies exactly once. `repeat_mode` (`once` deactivates / `always` re-arms) and `cooldown_seconds` are honored. `sweep()` is the operator/cron entry point.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_CRYPTO` is off; curated error codes only (`missing_fields`, `invalid_transaction`, `invalid_alert`, `unauthenticated`, `not_found`) — never an internal exception string. Alert delete is scoped to the owning user.
- `bot.py` — 5 thin authenticated routes (`POST /api/business-os/crypto/transactions`, `GET .../portfolio`, `POST`/`GET .../alerts`, `POST .../alerts/<id>/delete`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Money never touches a float.** Every USD amount is an integer number of cents from ingest to report. Per-unit cost is stored in cents; a fractional-lot sell computes consumed cost with `Decimal` rounding, so cents never drift.

**Quantity never loses precision.** Crypto amounts are stored as canonical decimal strings and parsed with `Decimal` — an `0.00000001` BTC buy round-trips exactly (proven by test), which a REAL column would silently mangle.

**The log is the authority; holdings are a projection.** Buys/sells are immutable rows; the holdings/lots tables are recomputed from them and are always rebuildable. Corrections are new rows, never in-place edits.

**Two classic alert bugs are designed out:**
- *Chatter* — a naive `price > threshold` fires every tick while above. Edge detection via persisted `last_state` fires only on the transition into the region.
- *Double-paging across a restart* — the `(alert_id, crossing_key)` UNIQUE index makes a replayed crossing a no-op, so a crash-and-rerun mid-sweep never re-notifies. Proven by a test that re-evaluates the same edge with stale (un-persisted) state and asserts exactly one event row.

**Verified invariants:** FIFO sell realizes against the *oldest* lot's cost; AVERAGE blends all open lots; fees fold into cost basis and net proceeds; unrealized P&L = `qty × price − cost basis`; an oversell is rejected; a replayed external transaction leaves the position unchanged; a missing quote contributes cost basis but no market value; `repeat_mode='always'` re-fires on a genuine second crossing while `'once'` deactivates.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Crypto — 38/38**

| Suite | Result |
|-------|--------|
| `test_crypto_schema.py` (tables, idempotency, dedupe indexes, legacy untouched) | 8/8 |
| `test_crypto_engine.py` (FIFO/average, realized/unrealized, oversell, idempotent, precision) | 9/9 |
| `test_crypto_market.py` (normalized cents, stale-not-zero, degradation, cache, lookup) | 5/5 |
| `test_crypto_alerts.py` (CRUD, arm-no-fire, crossing-once, replay-dedupe, repeat, sweep) | 8/8 |
| `test_crypto_api.py` (controller contract: dark, validation, round-trip, scoped CRUD, sweep) | 8/8 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2, slices 1–7 + billing/reporting/admin/assistant/notifications/feed) | 22 | 218/218 |
| IAP / Premium (Stage 4) | 3 | 26/26 |

**Total: 389 tests, 0 failures** (351 prior regression unchanged + 38 new crypto). `python -m py_compile bot.py` → **COMPILE OK**. The 5 new crypto routes have unique endpoint function names (`api_business_os_crypto_record_transaction`, `_portfolio`, `_create_alert`, `_list_alerts`, `_delete_alert`); the GET/POST pair on `/api/business-os/crypto/alerts` is method-distinguished exactly as the existing marketplace routes are.

---

## 5. Honest limitations

- **No custody, no trading — by design, not by omission.** This vertical records and reports; it never executes. A user's "buy" row is a statement that they bought elsewhere, not an instruction for us to buy. Adding execution would be a separate, separately-approved effort.
- **Live prices depend on the existing upstreams.** Unrealized P&L and alert evaluation are only as fresh as `market_data`'s CoinGecko/Coinbase feed. When the feed is unavailable the quote is marked `stale`/`ok=False` and the affected holding shows cost basis with no market value rather than a fabricated number — correct, but it means unrealized figures can be temporarily unavailable.
- **Alert delivery is decoupled from firing.** The sweeper records a deduped, undelivered event row; wiring those rows to the actual notification transport (push/email) reuses the canonical notification surface and is the remaining integration step. The `delivered` flag exists for exactly this.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 5 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.
- **Cross-symbol / tax-lot reporting** (wash sales, per-jurisdiction tax treatment) is out of scope; this is position accounting, not tax preparation.

---

## 6. Reversibility

With `BUSINESS_OS_CRYPTO` unset the entire surface is inert: all five routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The crypto modules are additive; the legacy portfolio/alert tables and the three market services are untouched (proven by `test_legacy_tables_untouched` and by the market layer composing rather than importing-over the providers). Rolling back is flag-off; nothing to migrate down.
