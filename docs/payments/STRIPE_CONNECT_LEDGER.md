# Stripe Connect — The Seller Ledger

Date: 2026-09-17
Implementation: `services/business_os/ledger/ledger.py` (the ledger),
`services/marketplace_settlement_service.py` (what it posts),
`services/business_os/marketplace/policy.py` (the rate it splits at).

The requirement this document answers: *"an immutable ledger recording every cent
— what the buyer paid, what PulseSoc kept, what the seller earned, what is
pending, what has been transferred, what has been paid out, and what was reversed."*

---

## 1. Rules

1. **Integer minor units only.** No floats touch money anywhere in this path.
   Every column is `*_minor` or `*_cents` and every arithmetic step is integer.
2. **Append-only.** Nothing in `creator_ledger_entries` is ever updated or
   deleted. A reversal is a new entry in the opposite direction, not an edit. The
   history of a disputed order is readable in full after the fact.
3. **Every entry has a source and a destination.** Money is never created or
   destroyed, only moved between named accounts.
4. **Every entry has an idempotency key.** Stripe redelivers; workers have
   replicas. The key is what makes a second delivery a no-op instead of a second
   payment.
5. **The snapshot, not the current policy, governs.** Fee reversals are computed
   from the rate stored on the settlement at the time of sale.

## 2. Accounts

| Account | Meaning |
| --- | --- |
| `external:stripe_marketplace` | money arriving from the buyer via Stripe |
| `external:stripe_marketplace_refunds` | money leaving back to the buyer |
| `seller_payable:<seller_id>` | **what PulseSoc owes this seller.** The core liability |
| `platform:marketplace_revenue` | PulseSoc's commission |
| `liability:marketplace_tax` | collected tax, owed onward — never PulseSoc's money |
| `seller_payout_pending:<seller_id>` | earnings fenced against an in-flight payout |
| `platform:payouts_settled` | money that has actually left via a paid Stripe payout |

## 3. What a sale posts

One paid order, buyer total 10 000 minor units, 5% policy active, no shipping or
tax:

| Entry type | Amount | Source → Destination | Idempotency key |
| --- | --- | --- | --- |
| `marketplace_seller_earning` | 9 000 | `external:stripe_marketplace` → `seller_payable:22` | `marketplace:settlement:seller:<tx>` |
| `marketplace_platform_fee` | 1 000 | `external:stripe_marketplace` → `platform:marketplace_revenue` | `marketplace:settlement:fee:<tx>` |
| `marketplace_tax_liability` | 0 (omitted) | `external:stripe_marketplace` → `liability:marketplace_tax` | `marketplace:settlement:tax:<tx>` |

Zero-valued components post no entry at all. With the fee gates closed the fee is
0 bps, so a live sale today posts one entry of 10 000 to `seller_payable`.

The ledger transaction ids are written back onto the settlement row as
`seller_ledger_ref`, `fee_ledger_ref`, `tax_ledger_ref`, so a settlement can
always name the entries that created it.

## 4. The settlement row

`marketplace_commercial_settlements`, one row per seller line per order. It is
*not* the ledger — it is the state machine plus an immutable commercial snapshot.

Snapshot columns (never change after insert): `quote_id`, `fee_policy_version`,
`payout_policy_version`, `fee_rate_bps`, `merchandise_net_minor`,
`shipping_minor`, `tax_minor`, `seller_shipping_credit_minor`,
`buyer_total_minor`, `gross_platform_fee_minor`, `gross_seller_earnings_minor`.

Running columns: `net_platform_fee_minor`, `net_seller_earnings_minor`,
`fee_reversed_minor`, `seller_reversed_minor`, `payout_state`, `blocker_code`,
`payout_ready`, `provider_payout_id`, `protection_ends_at`.

The invariant that ties them together:

```
net_seller_earnings_minor  == gross_seller_earnings_minor - seller_reversed_minor
net_platform_fee_minor     == gross_platform_fee_minor    - fee_reversed_minor
```

`net_seller_earnings_minor` is the number a Transfer is sized from. It is derived,
never accumulated, so a double-applied reversal cannot drift it.

## 5. Reversals

### The allocation problem

Stripe reports money coming back as a **cumulative total against the charge** —
`charge.amount_refunded` is a running figure, and a Dispute's `amount` is the
whole disputed sum. It never reports commercial components, and one charge can
back several sellers.

`bot.pulse_allocate_marketplace_reversal` is the single allocator. It:

1. reads what has already been reversed for each settlement,
2. subtracts it from the incoming cumulative figure to get the true delta,
3. consumes that delta against each settlement's snapshot in
   **merchandise → shipping → tax → other** order,
4. calls `marketplace_settlement_service.apply_refund` per settlement.

So a $40 refund followed by a cumulative $60 event reverses $20, not $100.

Refunds and lost disputes share this allocator deliberately. A chargeback the
platform loses is the same money leaving by a different door, and a second
allocator would be free to disagree with the first about where it came from.

### What a reversal posts

| Entry type | Source → Destination |
| --- | --- |
| `marketplace_seller_reversal` | `seller_payable:<id>` → `external:stripe_marketplace_refunds` |
| `marketplace_fee_reversal` | `platform:marketplace_revenue` → `external:stripe_marketplace_refunds` |

Both are posted with `allow_negative=True`: a seller whose earnings have already
been transferred can legitimately go negative, and refusing the entry would leave
the ledger claiming PulseSoc still owes money it has already clawed back. A
negative `seller_payable` balance is a real, reportable state — see §7.

### Fee reversal is proportional, not linear

`policy.platform_fee_reversal()` computes the fee that *should* have been charged
on the un-refunded remainder and reverses the difference. On a 10% legacy order
with a 40% merchandise refund, 400 of the 1 000 fee comes back, leaving 600. The
computation is against the **original** `gross_platform_fee_minor`, so partial
refunds in any order converge on the same total.

### Guards

`apply_refund` refuses to:

- reverse more merchandise, shipping or tax than the snapshot contains,
- reverse more in total than `buyer_total_minor`,
- apply the same `provider_refund_id` twice (returns `duplicate: True`).

When cumulative reversals reach `buyer_total_minor` the settlement moves to
`reversed`, which is terminal. Otherwise it moves to `held` with
`blocker_code='refund'` — a partially refunded order is not automatically
payable again.

## 6. Holds are not reversals

The distinction that the dispute work turned on:

| Signal | Ledger effect | Settlement effect |
| --- | --- | --- |
| `radar.early_fraud_warning.created` | **none** | `blocker_code='fraud_warning'`, state `held` |
| `charge.dispute.created` | **none** | `blocker_code='dispute'`, state `disputed` |
| `charge.dispute.closed` won | **none** | blocker cleared, released to the recovered origin state |
| `charge.dispute.closed` lost | reversal entries | state `reversed` |
| `charge.refunded` | reversal entries | `held` or `reversed` |

A warning and an open dispute are predictions. Reversing them would take money
from a seller who has done nothing wrong and may win. Freezing them costs the
seller time and costs PulseSoc nothing. Only an outcome moves the ledger.

## 7. Negative balances

A seller can end up owing PulseSoc: a chargeback lost after the transfer already
went out leaves `seller_payable:<id>` negative.

What is built: the entry posts (`allow_negative=True`), the balance is readable
via `seller_payouts.seller_balance_summary`, and `request_payout` refuses any
amount exceeding the available balance — so a negative seller can request
nothing.

What is **not** built and needs an owner decision: recovery. Stripe will not pull
money back out of a connected account's bank automatically. The options are
netting it against the seller's future sales, requesting repayment, or writing it
off. Nothing here chooses one, and nothing here will silently net it — a seller
discovering their next sale paid nothing is a support incident, not a policy.

## 8. The payout leg

Requesting a payout fences the money rather than assuming it:

| Entry type | Source → Destination | Posted by |
| --- | --- | --- |
| `payout_request` | `seller_payable:<id>` → `seller_payout_pending:<id>` | `request_payout` |
| `payout_settled` | `seller_payout_pending:<id>` → `platform:payouts_settled` | `payout.paid` webhook |
| `payout_returned` | `platform:payouts_settled` → `seller_payout_pending:<id>` | payout reversal |
| `payout_reversal` | `seller_payout_pending:<id>` → `seller_payable:<id>` | `payout.failed`, so the seller can request again |

`request_payout` reads `seller_payable:<id>` and refuses with
`insufficient_balance` if the request exceeds it; the ledger's own overdraft
guard re-checks under a row lock, so a concurrent double-spend is rejected there
even if the read raced. `payout_key` (`marketplace:payout:<tx_id>`) is the
idempotency key on both the ledger entry and the `seller_payout_requests` row, so
a duplicate request returns the original rather than fencing the money twice.

Note what this chain does *not* separate: these entries model the **Payout**
(connected account → bank). The **Transfer** (platform balance → connected
account) is executed by `run_once` and recorded as `stripe_transfer_id` on the
payout row, but posts no ledger entry of its own. The two legs are distinct in
Stripe and distinct in the payout row; in the ledger they are currently collapsed
into the payout entries above. That is acceptable while both legs are executed
back-to-back in one function, and would need splitting if they ever diverge in
time.

## 9. Reconciliation

Three identifiers make the whole chain joinable end to end:

| Identifier | Joins |
| --- | --- |
| `provider_payment_id` (PaymentIntent) | Stripe charge ⇄ every settlement it funded |
| `transfer_group` (`marketplace_order:<tx_id>`) | one charge ⇄ its N Transfers |
| `related_object` (`marketplace_order:<tx_id>`) | settlement ⇄ its ledger entries |

`provider_payment_id` is also the *only* way back from a Dispute to the sellers
it affects, because Stripe puts no metadata on a Dispute. That is not a
convenience — it is the reason the field is load-bearing.

## 10. What the ledger cannot currently tell you

Honest gaps, all downstream of §8 in the architecture doc:

- **No payout entry has ever been posted in production**, because nothing calls
  `run_once`. The accounting shape in §8 is built and tested; it has simply never
  been reached with real money.
- **The Transfer leg has no entry of its own.** See §8.
- **Stripe's own processing fees are not modelled.** The ledger currently splits
  the buyer total between seller, platform and tax without deducting what Stripe
  charged PulseSoc for accepting the card. Whoever absorbs that (§ owner
  decisions) determines whether `platform:marketplace_revenue` is gross or net,
  and today it is gross — i.e. it overstates PulseSoc's real margin.
