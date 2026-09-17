# Stripe Connect — Marketplace Architecture

Date: 2026-09-17
Platform account: COINPLOTXAI INC (`acct_1TTVo7FP8qvvGWBI`)
Companion documents: `STRIPE_CONNECT_AUDIT.md` (what existed before), `STRIPE_CONNECT_LEDGER.md` (the money model), `STRIPE_CONNECT_RUNBOOK.md` (operating it).

---

## 1. The one-sentence version

A buyer pays **PulseSoc**, not the seller. PulseSoc keeps the money in its own
Stripe balance, records what it owes the seller in its own ledger, and only later
makes two separate, separately-named movements: a **Transfer** from the platform
balance to the seller's connected account, and then a **Payout** from that
connected account to the seller's bank.

Everything below is a consequence of that sentence.

## 2. Charge model: separate charges and transfers

Stripe offers two shapes. We use the second.

| | Destination charge | **Separate charges and transfers (chosen)** |
| --- | --- | --- |
| How | `transfer_data.destination` + `application_fee_amount` on the PaymentIntent | Plain charge to the platform, then `stripe.Transfer.create` later |
| Who decides when the seller is paid | Stripe, at charge time | **PulseSoc**, whenever it decides |
| Refund/dispute before fulfilment | Money has already left for the seller | Money is still in the platform balance |
| Multi-seller cart | One destination per charge, so N charges | One charge, N transfers, joined by `transfer_group` |

The deciding factor is the protection window. PulseSoc holds a seller's earnings
until fulfilment and the refund/dispute window pass. A destination charge pays the
seller at checkout, which makes the entire settlement state machine decorative:
by the time a chargeback arrives the funds are in the seller's balance and the
platform absorbs the loss.

**Verification that we are not accidentally doing destination charges:** no
`transfer_data` or `application_fee_amount` appears anywhere in the payment
construction path. The only two matches in the repository are in
`services/marketplace_payment_errors.py`, where they are named in comments and in
an error-classification table.

## 3. Seller lifecycle

```
  applies ──► pulsesoc_review ──► approved ──► starts Connect onboarding
                    │                                    │
                    └──► rejected                        ▼
                                          Stripe-hosted collection of identity,
                                          bank account, tax details (never here)
                                                         │
                                            account.updated webhook
                                                         ▼
                              charges_enabled && payouts_enabled  ──► payable
```

Two independent gates, both required:

1. **PulseSoc approval.** Ours. The "Set up payouts with Stripe" affordance does
   not exist in the seller's UI until this passes.
2. **Stripe verification.** Theirs. Mirrored locally in
   `seller_payout_accounts.charges_enabled` / `payouts_enabled` /
   `onboarding_status`, written *only* by webhook.

`bot.seller_destination_account_id(payout_row)` is the single function that
answers "may a Transfer be sent here?". It returns `""` unless both flags are
truthy and `onboarding_status` is not one of
`onboarding_started|pending|restricted|disabled|rejected`. An empty answer is not
an error — it downgrades the settlement to `pending_onboarding` and defers the
transfer.

### What PulseSoc never stores

Bank account numbers, identity documents, SSN/EIN, date of birth. All of it is
collected by Stripe on Stripe's hosted onboarding, via an Account Link. What we
persist is: the `acct_…` id, three booleans, a JSON list of
`requirements.currently_due` keys (key *names*, not values), and timestamps.

### Selling before onboarding finishes

A seller can list and sell before completing Connect. Those settlements open in
`pending_onboarding`, and until 2026-09-17 nothing ever revisited them — the
`account.updated` branch refreshed `seller_payout_accounts` and stopped, so the
earliest sellers were permanently unpayable while the ledger looked healthy.
`marketplace_settlement_service.reconcile_seller_onboarding` now runs off that
webhook and moves every such settlement into the normal release chain. It is
keyed on the Stripe account id, so redeliveries dedupe.

## 4. Payment → settlement → release

```
buyer pays ──► PaymentIntent (platform) ──► webhook: payment_intent.succeeded
                                                      │
                                    settle_paid_transaction()
                                                      │
                        ┌─────────────────────────────┴───────────────────┐
                        ▼                                                 ▼
          marketplace_commercial_settlements row           immutable ledger entries
          (state machine + fee snapshot)                   (see LEDGER.md)
```

### The settlement state machine

`services/marketplace_settlement_service.py` owns it. States:

```
pending_order ─► pending_onboarding ─► pending_fulfillment ─► protection_hold ─► eligible ─► scheduled ─► paid
                                                                                                │
        held / disputed  ◄── (from any pre-paid state, and back again) ──────────────────────────┘
                                                            reversed ◄── (terminal, from anywhere)
```

Two separate mechanisms, deliberately not merged:

- **`payout_state`** — where the settlement is in its lifecycle.
- **`blocker_code`** — an independent veto. `transition_payout` refuses any move
  into `eligible`, `scheduled` or `paid` while a blocker is set, *regardless* of
  what the state graph would allow.

The blocker exists because state alone is ambiguous. A settlement in
`protection_hold` because its window has not elapsed and one in `protection_hold`
because a chargeback arrived are the same state and must not behave the same way.

### Hold origin recovery

`place_hold` overwrites `payout_state`, so the state a hold interrupted survives
only in `marketplace_payout_state_events`. `hold_origin_state()` reads it back.
This matters for won disputes: releasing everything to `pending_fulfillment`
would demand a second delivery confirmation, which `mark_delivered` would dedupe
away on the original idempotency key — stranding the seller's money permanently.

## 5. Transfer and Payout are two different things

`services/marketplace_payout_scheduler.run_once()` performs them in order and
records both:

| | Transfer | Payout |
| --- | --- | --- |
| Moves money from | PulseSoc platform balance | seller's connected account balance |
| Moves money to | seller's connected account | seller's bank account |
| Stripe call | `stripe.Transfer.create` | `stripe.Payout.create` (on the connected account) |
| Built by | `seller_payouts.build_stripe_transfer_args` | `seller_payouts.build_stripe_payout_args` |
| Recorded as | `stripe_transfer_id` | `stripe_payout_id` |
| Reconciles via | `transfer_group` (= `marketplace_order:<tx_id>`) | `payout.paid` / `payout.failed` |

Under separate charges and transfers the payout draws on nothing until the
transfer lands, so `run_once` aborts the row if the transfer returns no id rather
than attempting a payout against an empty balance.

**Provider submission is not payment.** `run_once` leaves the settlement in
`scheduled`. Only the `payout.paid` webhook, via
`marketplace_payout_scheduler.apply_provider_event`, may move it to `paid`.

## 6. Webhooks are the authority

No payment state is ever inferred from a client response, a redirect, or a
polling read. The webhook endpoint is the only writer.

| Event | What it does here |
| --- | --- |
| `checkout.session.completed`, `payment_intent.succeeded` | creates the settlement and posts the ledger entries |
| `payment_intent.payment_failed`, `payment_intent.canceled` | releases the inventory reservation |
| `charge.refunded` | allocates the cumulative refunded amount across settlements |
| `charge.dispute.created` | places a `dispute` blocker on every settlement behind the charge |
| `charge.dispute.updated` | records status movement |
| `charge.dispute.closed` | won → release to the recovered origin state; lost → reverse the ledger |
| `radar.early_fraud_warning.created` | places a `fraud_warning` blocker — holds, never reverses |
| `account.updated` | mirrors capability flags; reconciles pre-onboarding settlements |
| `account.application.deauthorized` | marks the connected account unpayable |
| `payout.paid` / `payout.failed` | the only authority for `scheduled → paid` / `failed` |
| `transfer.created` / `transfer.reversed` | Business OS payout ledger projection |

Two object-shape traps that were live defects, both now guarded by tests:

- **A Dispute carries its own `metadata`, which is empty.** Stripe does not copy
  the Charge's metadata onto it. The original handler read
  `metadata["seller_transaction_id"]` off a Dispute and therefore touched no
  marketplace row at all — a chargeback was financially invisible. Resolution now
  goes through the payment intent, which both object shapes carry.
- **`account.application.deauthorized` puts the connected account id in the
  event's `account` field, not in `data.object`.** That object is the
  deauthorized Application. A handler reading its `id` updates zero rows.

Idempotency is two-layered: Stripe-side idempotency keys on outbound calls, and
database uniqueness on inbound effects (`marketplace_payout_state_events.idempotency_key`,
`marketplace_commercial_refunds UNIQUE(seller_transaction_id, provider_refund_id)`).
Every handler is safe to redeliver.

## 7. Fee policy

`services/business_os/marketplace/policy.py`. Version `MARKETPLACE_STANDARD_V1`,
proposed rate 500 bps (5%).

The rate is **0% until three environment gates are all open**:

- `MARKETPLACE_STANDARD_V1_OWNER_APPROVED`
- `MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY`
- `MARKETPLACE_STANDARD_V1_EFFECTIVE_AT` (a timestamp that has passed)

`effective_platform_fee_bps()` returns `PROPOSED_PLATFORM_FEE_BPS` only when all
three pass, and `0` otherwise. Three gates rather than one because they are three
different facts: the owner has decided the rate, the seller-facing disclosure
describing it is live, and the date it starts applying has arrived. Any of them
being false makes charging the fee a commercial or legal problem, not a technical
one.

Per-order the effective policy is **snapshotted** into the settlement row
(`fee_policy_version`, `fee_rate_bps`) and into the ledger entry metadata. A later
policy change cannot retroactively alter what an existing order owes — refunds
years later still reverse at the rate the buyer was actually charged.

## 8. Known dead links in the release chain

Stated plainly because this is the difference between "the machine exists" and
"money can reach a seller". As of this commit:

| Link | Status |
| --- | --- |
| `reconcile_onboarding` (pending_onboarding → pending_fulfillment) | **wired** — `account.updated` |
| `mark_delivered` (pending_fulfillment → protection_hold) | **no caller** — awaiting the delivery-confirmation decision |
| `evaluate_eligibility` (protection_hold → eligible) | **no caller** — needs a scheduled sweep |
| `run_once` (eligible → scheduled → transfer → payout) | **no caller** — needs owner authorization to move money |

Money therefore enters the ledger correctly and has no automated path out. Every
blocking, reversing and freezing path *is* wired, which is the safe asymmetry to
have while the release side is incomplete: the failure mode is a seller waiting,
not a seller overpaid.
