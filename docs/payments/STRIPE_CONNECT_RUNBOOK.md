# Stripe Connect — Operations Runbook

Date: 2026-09-17
Audience: whoever is on call when a seller says "where is my money".

---

## 1. First question: which state is the settlement in?

Everything starts here. One row per seller line per order.

```sql
SELECT seller_transaction_id, seller_id, payout_state, blocker_code,
       payout_ready, protection_ends_at, net_seller_earnings_minor,
       seller_reversed_minor, provider_payment_id, provider_payout_id
FROM marketplace_commercial_settlements
WHERE seller_transaction_id = :tx_id;
```

And the immutable history of how it got there:

```sql
SELECT id, from_state, to_state, actor, reason, idempotency_key, created_at
FROM marketplace_payout_state_events
WHERE seller_transaction_id = :tx_id
ORDER BY id;
```

The event log is append-only and is the authority. If `payout_state` and the
event log disagree, something wrote the settlement row directly — treat that as
an incident, not a data fix.

## 2. State → meaning → action

| `payout_state` | Means | Normal next step |
| --- | --- | --- |
| `pending_order` | settlement created, payment not confirmed | wait for the webhook |
| `pending_onboarding` | seller had no usable connected account when they sold | seller completes Connect; `account.updated` reconciles it automatically |
| `pending_fulfillment` | seller has been paid into the ledger, order not delivered | awaiting delivery confirmation |
| `protection_hold` | delivered; refund/dispute window running | elapses at `protection_ends_at` |
| `eligible` | window passed, no blocker | awaiting the payout run |
| `scheduled` | Transfer + Payout submitted to Stripe | awaiting `payout.paid` |
| `paid` | Stripe confirmed the payout | done |
| `failed` | provider submission failed; liability preserved | retry via a new payout run |
| `held` | a `blocker_code` is set | see §3 |
| `disputed` | a chargeback is open | see §4 |
| `reversed` | fully refunded or dispute lost | terminal |

**`blocker_code` overrides the state.** A settlement can sit in `protection_hold`
with `blocker_code='refund'` and will never become eligible, no matter how long
the window has run. Always read both columns.

## 3. Blocker codes

| Code | Set by | Cleared by |
| --- | --- | --- |
| `refund` | `charge.refunded` (partial) | owner decision — nothing clears it automatically |
| `dispute` | `charge.dispute.created` | `charge.dispute.closed` won |
| `fraud_warning` | `radar.early_fraud_warning.created` | a dispute closing in PulseSoc's favour, or an owner decision if none ever opens |
| `fraud_review` | manual risk action | manual |

A `fraud_warning` that never becomes a dispute is the case that needs a human:
Stripe sends no "the warning was wrong" event. Left alone, the settlement stays
held forever. There is no automatic expiry on purpose — silently releasing a
flagged sale after N days would be a policy decision made by a timer.

## 4. A seller says a chargeback was resolved but they still are not paid

1. Confirm Stripe's view. The dispute's `status` is what matters: `won`,
   `lost`, or `warning_closed`.
2. Check whether we received `charge.dispute.closed`. If the endpoint is not
   subscribed to it, the hold was placed by `created` and nothing will ever lift
   it. Run `python3 scripts/stripe_webhook_recovery_audit.py` — it fails if any
   required event is unhandled, and lists them.
3. If we received it and the settlement is still held, look for
   `MARKETPLACE_DISPUTE_WON_NEEDS_REVIEW` in the logs. That line means the
   settlement had already reached a state with no valid release target (usually
   the seller was already paid before the chargeback). It needs an owner
   decision, deliberately — an automatic transition there would relabel a paid
   order as unpaid.

## 5. A seller finished Stripe onboarding and their old sales are still stuck

Expected behaviour: `account.updated` with both `charges_enabled` and
`payouts_enabled` true triggers
`marketplace_settlement_service.reconcile_seller_onboarding`, which moves every
`pending_onboarding` settlement for that seller into `pending_fulfillment`.

If it did not happen:

- Check `seller_payout_accounts` for that `connected_account_id`. If
  `onboarding_status` is not `complete`, Stripe has not told us they are done.
- Check for `MARKETPLACE_ONBOARDING_RECONCILE_FAILED` in the logs.
- Settlements with a `blocker_code` are intentionally skipped. Onboarding says
  nothing about a refund or chargeback.

Reconciliation is idempotent and safe to re-trigger by asking Stripe to resend
the `account.updated` event.

## 6. Transfers fail with no explanation

Almost always a deauthorized account. `account.application.deauthorized` marks
`onboarding_status='disconnected'` and zeroes both capability flags, after which
`seller_destination_account_id` returns `""` and no transfer is attempted.

```sql
SELECT user_id, connected_account_id, onboarding_status,
       charges_enabled, payouts_enabled, last_checked_at
FROM seller_payout_accounts
WHERE connected_account_id = :acct;
```

If the row still reads `complete` with both flags at 1 while Stripe reports the
account as disconnected, the endpoint is not subscribed to
`account.application.deauthorized`. Stripe sends no `account.updated` alongside a
deauthorization, so nothing else will ever correct it.

Re-onboarding is the seller's action. Settlements already past
`pending_onboarding` stay where they are and need an owner decision — this is an
open policy question, not a bug.

## 7. Required webhook endpoint subscriptions

Handlers are inert unless the Stripe Dashboard endpoint subscribes to the event.
The canonical list lives in `scripts/stripe_webhook_recovery_audit.py`
(`REQUIRED_EVENTS`) and is checked by
`tests/marketplace/test_reservation_webhook_wiring.py`.

Run the audit:

```
python3 scripts/stripe_webhook_recovery_audit.py
```

It verifies route coverage across all five accepted webhook paths, that every
required event has a handler, that raw-body and idempotency handling are intact,
and that a locally-signed fixture returns 2xx while a malformed signature returns
4xx. It prints no key material. Exit code 1 on any failure.

Optionally probe the public endpoint's health:

```
python3 scripts/stripe_webhook_recovery_audit.py --probe https://pulsesoc.com/api/stripe/webhook
```

## 8. Enabling the platform fee

The fee is 0 bps until all three are set:

```
MARKETPLACE_STANDARD_V1_OWNER_APPROVED
MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY
MARKETPLACE_STANDARD_V1_EFFECTIVE_AT
```

Check the current answer without guessing:

```python
from services.business_os.marketplace import policy
policy.effective_platform_fee_bps()   # 0 until all three gates pass
```

Orders already settled keep the rate they were sold at — the policy version and
rate are snapshotted per settlement. Turning the fee on changes new orders only.

## 9. Do not do these

- **Do not `UPDATE marketplace_commercial_settlements SET payout_state=…`
  directly.** It bypasses the transition guard and writes no event, so the
  immutable log stops matching reality and every subsequent diagnosis is wrong.
  Use `marketplace_settlement_service.transition_payout` / `place_hold` /
  `release_hold`.
- **Do not clear `blocker_code` by hand** to unstick a seller. It is the only
  thing standing between a disputed order and a transfer.
- **Do not replay a webhook by re-POSTing a captured payload.** The idempotency
  keys will dedupe the effects, which is correct, but the signature will not
  verify and you will learn nothing. Use Stripe's own "Resend" action.
- **Do not add a caller for `marketplace_payout_scheduler.run_once` without
  explicit owner authorization.** It is the function that moves real money out of
  the platform balance.

## 10. Current operational reality

As of this document, the release chain is **incomplete by design**:

- `mark_delivered` has no production caller — the delivery-confirmation
  mechanism is an undecided product question.
- `evaluate_eligibility` has no scheduled sweep.
- `run_once` has no caller, so no Transfer or Payout has ever been executed.

Money enters the ledger correctly and has no automated path out. Every blocking,
freezing and reversing path *is* wired. If a seller asks why they have not been
paid, the honest answer today is that automated payouts are not yet switched on —
not that something failed.
