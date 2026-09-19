# Payments — Production Activation Checklist

> **SUPERSEDED by `PRODUCTION_ACTIVATION_READINESS.md`.**
>
> Four of this document's five blockers (B1, B2, B3, B5) were closed by the
> Connect foundation work. Its diagnosis is still the reason those things were
> built, and is kept for that; its *instructions* are stale, and acting on §2
> below would mean re-solving problems that no longer exist.
>
> Go to `PRODUCTION_ACTIVATION_READINESS.md` for what is actually left and for
> the ordered activation sequence.

The gate between "the marketplace payment system is built" and "PulseSoc moves
real money for real sellers".

Every line below was verified against the tree at the commit this document was
written on, not copied from an earlier report. Where a claim contradicts
`STRIPE_CONNECT_IMPLEMENTATION_REPORT.md`, this file is the later reading.

## 1. Verdict

**Production card payments cannot be switched on today, and the blockers are
not configuration.**

Two are missing *code paths*, one is a built component with no runtime, one is a
missing credential, and one is a decision only the owner can make. Only the
third is anywhere near a matter of setting variables, and it needs a service
created before the variables mean anything.

## 2. Blockers, in the order they must be cleared

### B1 — Marketplace card payments are paused in code, not by a flag

`services/marketplace_payment_pause.py:49`

```python
def marketplace_card_payments_paused() -> bool:
    return True
```

There is no `MARKETPLACE_CARD_PAYMENTS_ENABLED` environment variable. The
string does not appear anywhere in `bot.py`, `services/` or `.env.example`.
Any plan that reads "flip the flag to true" is describing a variable that does
not exist.

Turning card payments on is therefore a **code change, a review and a deploy**,
not a Railway variable edit. That is a feature, not a defect: it means card
payments cannot be enabled by someone with dashboard access and no repository
access. It does mean the activation has a deploy's blast radius and needs to be
planned as one.

### B2 — No test-mode Stripe secret key

Without an `sk_test_` key there is no end-to-end test of any money path: no
test checkout, no test transfer, no test payout, no test refund, no test
dispute, and no physical-device validation of the flows a buyer actually sees.

Everything currently green is unit and integration coverage over the mapping
and the state machine. That is real coverage and it is not the same thing as
having moved a test dollar end to end.

This blocks B4 and B5 and cannot be worked around.

### B3 — Nothing releases a settlement from `pending_fulfillment`

`marketplace_settlement_service.mark_delivered` (line 424) **has no caller.**
The only other references in the tree are its own definition and a comment at
line 254. The two same-named functions in `services/command_center_worker/`
are unrelated — messaging and notification delivery, not settlements.

Equally, nothing writes a shipped status. `"shipped"` exists only as a
read-side status group derived in `bot.py:97530`; no `UPDATE` in the codebase
sets it.

Consequence: a paid order enters `pending_fulfillment` and stays there. Money
arrives and never becomes releasable. This is the same gap that leaves
`order_shipped` unwired in `PAYMENTS_NOTIFICATION_MATRIX.md` §7.

**This is an owner decision before it is an engineering task.** What confirms
delivery — the seller marking it, the buyer confirming, a carrier webhook, or a
timer? Each answer implies a different dispute posture and a different refund
window. It should not be guessed at in code.

### B4 — The payout worker exists, is gated off, and is not hosted

`marketplace_payout_scheduler.run_once` is the only code that calls
`stripe.Transfer.create` and `stripe.Payout.create`.

`services/marketplace_payout_worker.py` is its entry point. It is deliberately
**not wired into any process** — not `bot.py`, not the Procfile, not another
service — and a test in `tests/marketplace/test_payout_worker_authority.py`
asserts that, so hosting it fails loudly rather than quietly.

Three gates gate it, each failing to the non-acting value when unset, blank or
unparseable:

```
MARKETPLACE_PAYOUT_WORKER_ENABLED           the cycle runs at all
MARKETPLACE_PAYOUT_WORKER_DRY_RUN=false     it may mutate
MARKETPLACE_PAYOUT_WORKER_OWNER_AUTHORIZED  the owner authorised it
```

So even with B3 cleared and settlements reaching `eligible`, no transfer would
execute today: the worker has no runtime. Clearing B4 is two separate owner
actions — giving it a Railway service with a start command (a Procfile line is
not a deployment; see the CJ reconciliation precedent), and setting all three
gates. It is the component that actually sends money, and neither leg can be
taken back by this platform: money can only be *requested* back from a seller.

### B5 — No live transaction has ever been executed

Production has taken zero real payments. There is no live charge, no live
transfer, no live payout and no live refund to reconcile against.

The first one is not a test. It is a real charge on a real card belonging to a
real person, and it should be planned as an owner-executed transaction with a
known amount, a known seller and a decided refund path.

## 3. What is ready

Clearing the above does not mean starting from nothing. These are done and
covered:

- **Charge model decided and documented.** Separate charges and transfers.
  `STRIPE_CONNECT_ARCHITECTURE.md` §2.
- **Ledger.** Double-entry, with reversals distinguished from holds.
  `STRIPE_CONNECT_LEDGER.md`.
- **Settlement state machine**, with blocker codes, so a disputed order cannot
  transition to `paid`. Chargebacks place a real hold.
- **Webhook idempotency.** `stripe_events.stripe_event_id` is `UNIQUE`; a
  redelivered event is recorded and not reprocessed.
- **Refunds and disputes** allocate per order rather than per charge, so a
  charge backing several orders reverses each one's own share.
- **Key isolation.** `STRIPE_CONNECT_SECURITY.md`.
- **Notifications and email.** Sixteen events across in-app, email and push,
  idempotent, with the context restricted by allowlist.
  `PAYMENTS_NOTIFICATION_MATRIX.md`.
- **Fee policy: 0%.** No fee was invented. `STRIPE_CONNECT_ARCHITECTURE.md` §7
  describes how to enable one when there is a decision to.

## 4. Ordered activation sequence

Do not reorder. Each step's verification depends on the one before it.

1. **Owner decides the delivery-confirmation mechanism** (B3). Nothing else is
   worth building until this is answered.
2. **Provision an `sk_test_` key** (B2).
3. Implement the delivery-confirmation call site; wire `order_shipped` to it.
4. Run a full test-mode end-to-end: apply → approve → onboard → verify →
   checkout → pay → fulfill → settle → transfer → payout → refund → dispute.
   Confirm every notification in the matrix fires exactly once, on a device.
5. **Owner authorizes the payout worker** (B4) and it is given a runtime — a
   Procfile line is not a deployment (see the CJ reconciliation precedent:
   a service must be created, given a start command, and connected to the repo).
6. Confirm the live webhook endpoint is subscribed to every event in
   `STRIPE_CONNECT_IMPLEMENTATION_REPORT.md` §8 and is receiving deliveries.
7. Confirm at least one real seller has completed Connect onboarding and shows
   `charges_enabled` and `payouts_enabled`.
8. **Code change to lift the pause** (B1), reviewed and deployed.
9. **Owner executes one controlled live transaction** (B5), then one live
   refund against it.
10. Reconcile: Stripe balance transactions against
    `marketplace_commercial_settlements` and the ledger.
11. Monitoring and alerting on webhook failures, transfer failures and payout
    failures before the second transaction.

## 5. Rollback

Restoring `marketplace_card_payments_paused()` to `True` and deploying stops
new Marketplace card checkouts. It does **not** reverse in-flight money:
charges already taken, transfers already sent and payouts already initiated
continue.

A rollback plan that only covers stopping new checkouts is incomplete. Before
step 9, decide in advance how an in-flight charge is refunded and how an
in-flight transfer is reversed, and confirm someone has the access to do both.

## 6. Do not

- Do not enable live card payments to satisfy a milestone. The system is
  measured by whether a seller is paid correctly, not by whether a switch is on.
- Do not use Stripe test card numbers in live mode. They fail, and the failures
  look like real decline problems.
- Do not run the payout worker against production before B3 is cleared.
  Transfers against settlements that cannot be marked fulfilled are transfers
  against an unfinished state machine.
- Do not treat a green test suite as evidence that money moves. No money has
  moved.
