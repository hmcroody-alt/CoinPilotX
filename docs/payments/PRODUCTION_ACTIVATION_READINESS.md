# Production Activation — Readiness

The gate between "the foundation is complete" and "PulseSoc takes a real card
payment". This supersedes `PAYMENTS_PRODUCTION_ACTIVATION_CHECKLIST.md`, which
was written before the foundation work and whose five blockers are the subject
of this document.

## 1. Verdict

**NOT READY. Do not switch on.**

Every blocker that was a missing code path is closed. What remains cannot be
closed by writing software: three test credentials, one Connect client id, one
non-production environment, a physical device, and a set of real mailboxes.

That the code is finished is not an argument for activating. The untested half
is the half that touches real money.

## 2. Where the old checklist's blockers stand

| Old | Was | Now |
|---|---|---|
| B1 | Card payments paused in code, not by a flag | **Closed** — `MARKETPLACE_CARD_PAYMENTS_ENABLED`, fail-closed |
| B2 | No production fulfillment completion path | **Closed** — `marketplace_order_fulfillment`, buyer/carrier authority |
| B3 | Payout scheduler built with no runtime | **Closed** — governed worker, three switches, leader lock |
| B4 | No Stripe test credentials | **OPEN — owner** |
| B5 | Owner decision on the settlement model | **Closed** — decided, and built as configuration rather than as a constant |

A sixth, not on the old list and found during this work: **nothing performed
`protection_hold → eligible`**, so every delivered order would have stayed in
`protection_hold` permanently. Closed by the settlement sweep. It is recorded
here because it would have presented, in production, as sellers never being paid
at all — and nothing on the old checklist would have caught it.

## 3. Must — activation is unsafe without these

| # | Requirement | Owner | Verified by |
|---|---|---|---|
| M1 | A **non-production environment or service**. There is currently exactly one (`production`) | Owner | `railway status` shows a second environment |
| M2 | `STRIPE_SECRET_KEY=sk_test_…` there | Owner | `stripe_mode.status()["mode"] == "test"` |
| M3 | `STRIPE_PUBLISHABLE_KEY=pk_test_…` | Owner | same |
| M4 | Test-mode `STRIPE_WEBHOOK_SECRET` | Owner | test webhook delivers |
| M5 | `STRIPE_CONNECT_CLIENT_ID=ca_…` — **absent entirely today** | Owner | test seller onboards |
| M6 | Steps 1–13 of the E2E plan pass | Engineering | `STRIPE_TEST_E2E_REPORT.md` §4 |
| M7 | **Step 14** — crash between the two payout legs is detected as critical | Engineering | An incident opens |
| M8 | Physical iPhone: Apple Pay, universal links back from onboarding, push | Engineering + device | `STRIPE_TEST_E2E_REPORT.md` §6 |
| M9 | Reconciliation sweep enabled and clean for 7 consecutive days **before** activation | Owner + engineering | `open_critical_incidents() == 0` across the window |
| M10 | Payout worker run at stage 1 (read-only) in production, and its report read by a human | Owner | Heartbeat shows `dry_run` and a non-empty would-pay list |

M9 and M10 are the two most often skipped and the two that pay for themselves.
M9 establishes what clean looks like on a quiet system, so the first finding
after activation means something. M10 is the last point at which the batch can
be inspected for free.

M7 is singled out from M6 because it is the only step that exercises the
permanent-stall bug class, and it is the only one that proves the reconciliation
sweep earns its cost.

## 4. Should — activation is survivable without these

| # | Requirement | Why it is not a *must* |
|---|---|---|
| S1 | Real visual email validation in Outlook, Gmail, Apple Mail, light and dark | A payment can be taken, fulfilled, settled and paid out with an email that renders badly. A seller reads an ugly email; no money moves wrongly |
| S2 | Load testing the reconciliation sweep against production row counts | The scan limit bounds it; truncation is reported |
| S3 | An admin UI for the stuck-payout recovery in `PAYOUT_SCHEDULER_RUNBOOK.md` §7 | The procedure is manual but documented, and the volume at 11 MAU does not justify building it first |

S1 is a judgement the owner may overrule in either direction. The one thing that
should not happen is it being recorded as done because the test suite is green —
see `EMAIL_CLIENT_VALIDATION.md` §3.

## 5. The activation sequence, when the musts are met

Not all at once. Each step is reversible and each produces evidence for the next.

| Step | Action | Stop if |
|---|---|---|
| 1 | `PAYMENTS_RECONCILIATION_ENABLED=true` in production, card payments still off | Any critical incident |
| 2 | Wait 7 days (M9) | Any critical incident |
| 3 | `MARKETPLACE_SETTLEMENT_HOLD_HOURS` set explicitly | — |
| 4 | `MARKETPLACE_FULFILLMENT_SWEEP_ENABLED` + `MARKETPLACE_SETTLEMENT_SWEEP_ENABLED` | Settlements move unexpectedly |
| 5 | `MARKETPLACE_PAYOUT_WORKER_ENABLED=true` — read-only (M10) | The would-pay list contains anything unexplained |
| 6 | **`MARKETPLACE_CARD_PAYMENTS_ENABLED=true`** — first real charge | Any card failure not explained by a reason code |
| 7 | Let the first real order complete fulfillment and reach `eligible` | Anything stalls |
| 8 | `…_DRY_RUN=false` **and** `…_OWNER_AUTHORIZED=true` — first real payout | Anything at all |

Steps 5 and 8 must be separate deploys. Step 6 before step 8 on purpose: taking
a payment is reversible through Stripe, paying a seller is not.

Railway variables reach a container **only at boot**. Every step above is a
redeploy, and `railway variables` describes intent, not the running process —
verify with `railway ssh`.

### Rolling back

| Step reached | Fastest reversal |
|---|---|
| 8 | `MARKETPLACE_PAYOUT_WORKER_DRY_RUN=true` — keeps the reporting, stops the money |
| 6 | `MARKETPLACE_CARD_PAYMENTS_ENABLED=false` — new checkouts lose the card option; in-flight orders continue |
| 4 | Both sweep flags false — orders stop auto-advancing and wait for a human |

None of these unwind what already happened. Rolling back step 6 does not refund
a charge, and rolling back step 8 does not recall a transfer.

## 6. Current production state

Verified on the `CoinPilotX` service, `production` environment, at mission end:

```
UNSET   MARKETPLACE_CARD_PAYMENTS_ENABLED
UNSET   MARKETPLACE_PAYOUT_WORKER_ENABLED
UNSET   PAYMENTS_RECONCILIATION_ENABLED
UNSET   MARKETPLACE_SETTLEMENT_HOLD_HOURS
UNSET   STRIPE_CONNECT_CLIENT_ID
SET     STRIPE_SECRET_KEY = sk_live_…
```

Every flag is off **by absence**. That is the design: the fail-closed default
means the safe state needs no configuration and the unsafe state needs a
deliberate one. A deployment that forgets these variables is a deployment that
takes no card payments, which is the correct thing to forget.

**Live card payments were not switched on during this mission.**

## 7. What would make this document wrong

- A change to any `_env_flag` default. Every one of them passes the non-acting
  value as `default`; the fail-closed property in §6 is that convention and
  nothing else.
- Removing `marketplace_settlements` from `run_all`. Pinned by an exact
  assertion, so it cannot happen silently.
- A settlement writer that bypasses `transition_payout`. The sweep would report
  `payout_state_not_in_state_machine`, but only after the fact.
- Activating out of order. Step 8 before step 6 pays sellers for orders that
  never had a card charge behind them.
