# Legacy ↔ Canonical Commerce Reconciliation Plan

Groundwork for the migration-phase task recorded in `STORE_OS_MISSION_PLAN.md`
§4b (strangler pattern, §3). Written while both surfaces coexist: the LEGACY
`/api/pulse/marketplace/*` packs serve the shipping app today; the CANONICAL
`services/business_os/*` engines are dark (flag-gated, unregistered) and are
the migration target. **No code in this document — mapping and sequencing
only.** Zero collisions exist today: different files, tables, and routes.

## 1. Offers

| | Legacy (`services/marketplace_offers_routes.py`) | Canonical (`services/business_os/marketplace/offers.py`) |
|---|---|---|
| Routes | `/api/pulse/marketplace/offers*` (Flask blueprint) | `offers_api.py` controller, mount `/api/business-os/marketplace/offers*` |
| Table | `marketplace_offers` (integer ids) | `business_os_mkt_offers` (+ `_events`, `_offer_reservations`; text UUIDs) |
| Counters | NEW ROW chained via `counter_of` | same row, `status='countered'`, `current_proposer` flips |
| Expiry | computed at READ time (`_effective_state`) | expire-on-touch + `expire_offers()` sweep |
| Accept | 24h checkout window; checkout via `seller_transactions`/Stripe Connect | hard inventory hold (reservation row), convert → canonical order → `pay_order` → shared ledger escrow |
| Client | `mobile-native/src/api/marketplaceOffers.ts` | cut over per-domain behind `BUSINESS_OS_MARKETPLACE` |

**State mapping (legacy → canonical):** `open → needs_response`; a chain of
`counter_of` rows → ONE canonical offer whose event trail replays the chain in
order (each legacy counter row becomes an `offer.counter` event, proposer
alternating); `accepted → accepted` + a reservation row whose `expires_at`
ports the legacy 24h checkout window; `declined/expired/withdrawn` map 1:1.
Terminal legacy chains migrate only their FINAL row's state; the chain is
history, not live state.

**Money invariant preserved:** legacy accept also moves no money (checkout is a
separate call). Migration must NOT create ledger entries for historical
accepted offers — only live `accepted` offers get reservations, and only if
inventory can still be held; otherwise migrate as `expired` with an audit note.

## 2. Returns

| | Legacy (`services/marketplace_returns_routes.py`) | Canonical (`services/business_os/marketplace/returns.py`) |
|---|---|---|
| Routes | `/api/pulse/marketplace/returns*` (+ `message`, `escalate`, `resolve`) | `returns_api.py`, mount `/api/business-os/marketplace/returns*` |
| Tables | `marketplace_returns`, `marketplace_return_events` (integer ids) | `business_os_mkt_returns`, `business_os_mkt_return_events` (text UUIDs) |
| Refund | provider-side via seller_transactions | governed `refunds.refund_order`, keyed `return:{return_id}` (idempotent, escrow-bounded) |

**Verb mapping:** legacy `resolve` fans out to canonical
`approve/decline/refund/close` depending on resolution payload; legacy
`escalate` maps to `refunds.open_dispute` (a dispute IS the escalation object
in the canonical model); legacy `message` belongs to the commerce-messages
domain (`services/business_os/messages/`), not the return row — migrate
message bodies into a business thread linked by `order_id`.

## 3. Cutover sequencing (per-domain, reversible)

1. Register canonical blueprints in bot.py (thin adapters + `ensure_schema()`
   per pack) with flags still off — dark, zero behavior change. Requires the
   repo quiet: bot.py is currently dirty on the emergency branch and content-
   gated (`scripts/realtime_audio_change_gate.py` before every push).
2. Enable flags on staging; run both surfaces side by side. The app still
   points at legacy.
3. Backfill migration script (idempotent, re-runnable): legacy rows →
   canonical tables per the mappings above, with an audit row per migrated
   entity (`action="migrated_from_legacy"`, before=legacy snapshot).
4. Cut the mobile API modules (`marketplaceOffers.ts`, returns calls) over
   per-domain behind their env flags; legacy routes flip to read-only.
5. Retire legacy routes after one release of parity metrics.

**Invariants that must hold at every step:** accept never moves money; refunds
only from escrow, idempotent by derived key; no client-written statuses;
existence not leaked across parties; honest zero-vs-unavailable in every
projection.
