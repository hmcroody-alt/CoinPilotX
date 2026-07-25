# Business OS — Stage 3 Marketplace MVP — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** real order state machine, physical vs digital fulfillment, payout execution, refunds/returns/disputes, inventory decrement, reviews; locked-but-attractive pre-approval experience.
**Pattern:** strangler — a canonical `business_os_mkt_*` surface built *beside* the untouched legacy inline-`bot.py` marketplace, gated behind `BUSINESS_OS_MARKETPLACE`, and settled on the shared canonical double-entry ledger. No legacy marketplace table was read or written.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 1 | Schema + migration (`business_os_mkt_*`, 9 tables) | **PASS** |
| 2 | Seller service (approval gate + product/inventory CRUD + lifecycle) | **PASS** |
| 3 | Order state machine + ledger settlement | **PASS** |
| 4 | Refunds / returns / disputes / reviews / payout accrual | **PASS** |
| 5 | Governed Marketplace Assistant (plan/execute) | **PASS** |
| 6 | Admin governance surface (inspect/refund/dispute/restrict/appeal/payout) | **PASS** |
| 7 | Framework-agnostic API controllers + thin `bot.py` routes + notifications | **PASS** |
| 8 | Full marketplace test matrix + advertising/ledger/entitlement regression | **PASS** |
| 9 | This consolidated report | **PASS** |

**Payout disbursement** (moving money OUT to a seller's bank/Stripe) is honestly **OUT OF SCOPE / NOT EXECUTED** — see §5. Everything up to and including the *accrual* of what a seller is owed is canonical and tested.

---

## 2. What was built

Nine modules under `services/business_os/marketplace/` (2,974 lines):

- `schema.py` — idempotent `ensure_schema()` creating `business_os_mkt_sellers`, `_products`, `_orders`, `_order_items`, `_order_events`, `_refunds`, `_disputes`, `_reviews`, `_audit`. Text UUID PKs (SQLite/Postgres portable). Never touches legacy tables.
- `service.py` — seller approval (input #2) and the product catalog. Composed eligibility gate `require_active_seller` = flag on ∧ not account-held ∧ seller `approved`, mirroring the advertising vertical's three-input separation. Product validation, lifecycle verbs (publish/pause/resume/archive/restore), ownership non-leak, public projection.
- `orders.py` — the canonical order state machine (`created→paid→fulfilled→completed`, plus `cancelled`/`refunded`) with every money movement on the shared ledger. Atomic inventory decrement with compensation on capture failure.
- `refunds.py` — governed refund primitive (escrow→intake reversal), disputes, verified-purchase reviews, and the seller payout-balance read.
- `assistant.py` — two-phase governed plan/execute with SHA-256 confirmation tokens, constant-time compare, read-after-write verification, and a write kill switch.
- `admin.py` — consolidated owner governance: cross-owner order inspection, governed refund + audit, dispute resolution, seller restrict/lift (idempotency-guarded), appeals (grant lifts restriction), payout-balance read, and audit-only payout settlement notes.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; dark 404 when the flag is off; unknown-field allowlist; only curated `MarketplaceError`s surfaced.
- `notifications.py` — canonical marketplace event adapters (best-effort, never a precondition).
- `bot.py` — ~35 thin route adapters (`/api/business-os/marketplace/*` buyer/seller, `/admin/business-os/marketplace/*` owner) that lazily import `api.py` and reuse the existing auth/CSRF/audit helpers. Dark 404 when the flag is off.

---

## 3. Money integrity (the part that must be right)

All amounts are integer cents; no floats. Ledger accounts:

- `platform:marketplace_intake` — external buyer money (allow-negative liability)
- `mkt_order_escrow:<order_id>` — per-order hold, **overdraft-guarded** (not allow-negative)
- `seller_payable:<seller_id>` — accrual of what the seller is owed
- `platform:marketplace_revenue` — platform fee accrual

Flows: **capture** intake→escrow (on pay); **settle** escrow→revenue (fee) + escrow→seller_payable (net) (on complete, computed from the *current* escrow balance); **refund** escrow→intake (while in escrow).

Verified invariants (a $40 order = 2 × $20, 10% fee):

- After settlement: `escrow == 0`, `seller_payable == 3600`, `marketplace_revenue == 400`.
- Over-refund is **impossible** — a refund larger than remaining escrow is refused by the ledger overdraft guard (`refund_exceeds_escrow`, 409).
- Settlement nets a prior partial refund: pay $40, refund $10, complete → settled from the current $30 escrow → `seller_payable == 2700`, `escrow == 0`.
- Reviews are verified-purchase only (buyer must own a `completed` order containing the product); one per (buyer, order, product).

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Marketplace — 29/29**

| Suite | Result |
|-------|--------|
| `test_marketplace_core.py` (Parts 1–4 primitives, direct) | 9/9 |
| `test_marketplace_assistant.py` (Part 5) | 7/7 |
| `test_marketplace_admin.py` (Part 6) | 6/6 |
| `test_marketplace_api.py` (Part 7 controller) | 7/7 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2, slices 1–7 + billing/reporting/admin/assistant/notifications/feed) | 22 | 218/218 |
| Entitlement + premium visibility | 5 | 65/65 |

**Total: 325 tests, 0 failures.** `python -m py_compile bot.py` → **COMPILE OK**. No duplicate marketplace route paths (GET/POST splits verified) and no duplicate endpoint function names among the 37 new `business_os_marketplace` handlers.

The `test_marketplace_core.py` suite was added this sprint to pin Parts 1–4 at the primitive layer (seller gate, product lifecycle, order state machine, ledger settlement, refund guard, dispute refund, verified review, payout read) — previously those modules were exercised only end-to-end through the controller.

---

## 5. Honest limitations

- **Payout disbursement is not executed.** `complete_order` accrues the seller's net into `seller_payable:<seller_id>` on the ledger — that accrual is canonical and tested. The actual bank/Stripe transfer that moves money OUT is a provider-side action that this environment does not perform and that is prohibited here. `admin_record_payout_note` writes an audit-only record with `moved_money: False` and moves zero money (verified: ledger balance unchanged after a note).
- **Payment capture is modeled post-provider.** `pay_order` records captured funds moving into escrow; collecting the card payment (Stripe PaymentIntent) happens before this call on the provider side.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The new routes are verified structurally via `py_compile` and duplicate-endpoint scanning, not by booting the app. Runtime route verification remains an owner-side step.
- **The native/iOS marketplace client** is a separate contract (see `pulsesoc_native_marketplace_*`), not part of this backend sprint.

---

## 6. Reversibility

With `BUSINESS_OS_MARKETPLACE` unset the entire surface is inert: every service entrypoint raises `disabled`, and both the API controllers and `bot.py` routes return a dark 404 — proven by `test_dark_when_disabled`. The legacy marketplace is untouched and continues to serve. Rolling back is flag-off; nothing to migrate down.
