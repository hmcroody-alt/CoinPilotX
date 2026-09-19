# Marketplace — Fulfillment and Settlement Model

Two state machines. The first tracks whether the buyer got the thing. The second
tracks whether the seller may be paid for it. They are separate on purpose, and
the join between them is one function.

Authority: `services/marketplace_order_fulfillment.py` and
`services/marketplace_settlement_service.py`.

## 1. The rule the model exists for

**A seller acting alone cannot release money to themselves.**

Before this work, the only path to `delivered` was a seller-triggered
`mark_delivered`. That is a seller pressing a button that pays the seller. Every
design decision below follows from removing that.

## 2. Fulfillment states

```
                     ┌──────────────┐
          ┌─────────►│  processing  │─────────┐
          │          └──────────────┘         │
          │                                   │
        ┌───┐                        ┌────────▼────────┐    ┌───────────┐
        │paid│───────────────────────►│     shipped     │───►│ delivered │──┐
        └───┘                        └─────────────────┘    └───────────┘  │
          │                                                                 │
          │          ┌──────────────────┐     ┌───────────┐                 ▼
          └─────────►│ ready_for_pickup │────►│ picked_up │────────►┌───────────┐
                     └──────────────────┘     └───────────┘         │ completed │
                                                                    └───────────┘
```

`cancelled` is reachable from `paid`, `processing`, `ready_for_pickup` (any of
seller, buyer, admin) and from `shipped` (**admin only** — once it is in transit
neither party can unilaterally undo it).

### Who may make each move

`TRANSITION_AUTHORITY` is the authority; `ALLOWED_TRANSITIONS` is *derived* from
it, never hand-written, so a move with no authority row simply does not exist.

| Transition | Permitted actors | Why |
|---|---|---|
| `paid → processing / shipped / ready_for_pickup` | seller, admin | The seller is the only one who knows |
| **`shipped → delivered`** | **buyer, carrier, admin, system** | **No seller.** This is the whole point of the file |
| **`ready_for_pickup → picked_up`** | **buyer, admin** | **No seller and no system.** A pickup has no carrier to corroborate it, so an unattended timeout here would be the seller's word by another name |
| `delivered / picked_up → completed` | buyer, admin, system | Closes the order record |
| `shipped → cancelled` | admin | In transit; neither party acts alone |

Three of the four routes to `delivered` are third parties. The fourth, `system`,
is a clock — and it is only fitted to the shipped lane, for the reason in §3.

### Lanes

`SHIPPING_KINDS = {"shipping"}`. Only a shipping order may take the shipped
lane, because only it has a carrier and a tracking reference — and the shipped
lane is the one with an unattended timeout on the end of it. A digital download
sent down that lane would collect a made-up tracking number and then auto-confirm
its own delivery on a clock. Everything else hands over in person or online and
waits for the buyer to say so.

`shipped` requires a tracking reference (`FULFILLMENT_TRACKING_REQUIRED`).
Taking the wrong lane for the order's kind is `FULFILLMENT_LANE_MISMATCH`.

## 3. Timeouts

| Variable | Applies to | Unset / blank / 0 / unparseable |
|---|---|---|
| `MARKETPLACE_AUTO_DELIVER_AFTER_SHIPPED_HOURS` | `shipped → delivered` | **No auto-advance.** A human must move it |
| `MARKETPLACE_AUTO_COMPLETE_AFTER_DELIVERY_HOURS` | `delivered → completed` | **No auto-advance** |

Both fall back to *doing nothing*. These are the only two transitions with nobody
behind them, so a misconfigured deployment must fall back to the option that
strands an order rather than the one that advances it unattended.

Auto-deliver closes the real gap where a buyer receives their order and simply
never says so, which would otherwise strand the seller's earnings forever.
Auto-complete is about closing the order record, **not** about releasing money —
the settlement protection window is separately configured and runs after it.

Local pickup deliberately has no timeout at all.

Sweeper: `sweep_auto_advance()`, hosted by the release cycle (§5).

## 4. Settlement states

`PAYOUT_STATES` — the money-side machine, one row per seller transaction in
`marketplace_commercial_settlements`.

```
pending_order ──► pending_onboarding ──► pending_fulfillment ──► protection_hold
                                                                       │
                                                                       ▼
                        paid ◄── scheduled ◄────────────────────── eligible
                                    │
                                    └──► failed ──► scheduled
```

`held`, `disputed` and `reversed` are reachable from almost everywhere;
`reversed` is terminal and `paid` may only go to `reversed`.

`pending_fulfillment → protection_hold` is the join between the two machines: it
happens when fulfillment reaches `delivered` or `picked_up`, via
`settle_delivery` → `settlements.mark_delivered`.

### The hold

`MARKETPLACE_SETTLEMENT_HOLD_HOURS` — whole hours between confirmed delivery and
the earnings becoming eligible. **Blank, unparseable or negative all mean the
documented default of 48 hours.**

Note that this fallback runs the *opposite* way to the fulfillment timeouts in
§3, on purpose. There, a bad value leaves an order sitting where it is. Here,
falling back to zero would release the money the instant delivery was confirmed
— so a typo in a deployment variable must not be able to pay a seller early. An
explicit `0` *is* honoured: waiving the hold is the owner's decision to make, and
a variable that silently overrode it would be a lie.

### Eligibility blockers

`eligibility_blockers()` returns **every** reason, not the first — a seller told
to fix one thing and then refused again for a second has been told the truth
twice and helped neither time.

| Code | Meaning |
|---|---|
| `NOT_IN_PROTECTION_HOLD` | Wrong state for release |
| `PAYOUT_BLOCKED` | A `blocker_code` is set on the row |
| `SELLER_NOT_PAYOUT_READY` | Connect account not payout-capable |
| `HOLD_END_UNKNOWN` | `protection_ends_at` missing or unparseable |
| `HOLD_NOT_ELAPSED` | The window has not closed yet |

`HOLD_END_UNKNOWN` is its own code rather than being treated as elapsed. A
settlement can reach `protection_hold` without a delivery — releasing a hold
targets that state directly — and the previous code read the missing timestamp
straight into `fromisoformat`, so one such row raised `ValueError` out of the
scheduler and stopped **every settlement behind it**. Unknown is not elapsed: the
money stays put and says why.

## 5. The release cycle

`services/marketplace_release_cycle.py`, hosted by `pulse_worker`. Two unattended
steps, neither of which moves money.

| Flag | Step |
|---|---|
| `MARKETPLACE_FULFILLMENT_SWEEP_ENABLED` | Applies the two `AUTO_` timeouts |
| `MARKETPLACE_SETTLEMENT_SWEEP_ENABLED` | `protection_hold → eligible` once the hold elapses |
| `MARKETPLACE_RELEASE_CYCLE_SECONDS` | Clamped 60–3600, default 300 |

Both flags are off when unset, blank or unparseable.

The fulfillment flag is the second of two conditions rather than a duplicate of
the first: with both `AUTO_` variables blank the sweep has nothing to do even
when enabled.

**Before the settlement sweep existed, nothing performed
`protection_hold → eligible`, so every delivered order stayed in
`protection_hold` permanently.** That is a found bug, not a new feature.

Both payout-worker switches still stand between `eligible` and a transfer —
see `PAYOUT_SCHEDULER_RUNBOOK.md`.

## 6. API

`/api/pulse/marketplace/orders/<transaction_id>/…`

| Method | Path | Actor |
|---|---|---|
| `GET` | `/fulfillment` | buyer or seller of the order |
| `POST` | `/processing` | seller, admin |
| `POST` | `/shipped` | seller, admin — requires tracking |
| `POST` | `/ready` | seller, admin |
| `POST` | `/received` | **buyer**, admin |
| `POST` | `/complete` | buyer, admin |

The actor's role is derived server-side from the order (`role_of`), never read
from the request body. A seller calling `/received` gets
`FULFILLMENT_ACTOR_NOT_AUTHORIZED`, which is the rule in §1 expressed as an HTTP
response.

## 7. Tests

| File | Covers |
|---|---|
| `tests/marketplace/test_order_fulfillment_authority.py` | Transition authority, lanes, tracking, timeouts |
| `tests/marketplace/test_settlement_hold_and_eligibility.py` | Hold configuration, blockers, sweep |
| `tests/marketplace/test_release_cycle_wiring.py` | Worker seam, flags, clamping |
| `tests/marketplace/test_card_capability.py` | Reason-code ordering and buyer redaction |

Run one file per pytest process — `tests/marketplace/` files cannot share one.
