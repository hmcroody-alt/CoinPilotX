# Stripe Connect — Foundation Completion

What was missing between "Connect is ~70% built" and "a card payment could be
switched on", what now exists, and what still blocks activation.

This is the index. Each numbered area has its own document; this one says how
the pieces join up and which of them are load-bearing for the activation
decision.

## 1. The nine blockers, and where each one stands

| # | Blocker | Status | Where |
|---|---|---|---|
| 1 | Card payments paused by a hard-coded constant | **Cleared** | §2 |
| 2 | No real environment feature flag | **Cleared** | §2 |
| 3 | No Stripe test credentials | **BLOCKED — owner** | §7 |
| 4 | No production fulfillment completion path | **Cleared** | `FULFILLMENT_AND_SETTLEMENT_MODEL.md` |
| 5 | No production caller for settlement / payout execution | **Cleared** | `PAYOUT_SCHEDULER_RUNBOOK.md` |
| 6 | No full Stripe test-mode E2E | **BLOCKED — depends on 3** | `STRIPE_TEST_E2E_REPORT.md` |
| 7 | No physical iPhone validation | **BLOCKED — depends on 3** | `STRIPE_TEST_E2E_REPORT.md` §6 |
| 8 | No real visual email-client validation | **BLOCKED — owner** | `EMAIL_CLIENT_VALIDATION.md` |
| 9 | Branch divergence from `origin/main` | **Cleared** | §6 |

Six of nine are code blockers and all six are closed. The remaining three are
not code — they need credentials, a device, and a set of real mailboxes. None of
them can be honestly closed by writing more software, so none of them were.

## 2. The flag

`MARKETPLACE_CARD_PAYMENTS_ENABLED`. Off when unset, blank, `false`, or anything
unrecognised. Only `1`, `true`, `yes`, `on`, `enabled` turn it on.

Before this, `services/marketplace_payment_pause.py` held a constant. A constant
cannot be switched off in an incident without a deploy, and — more to the point
— it cannot be switched *on* in a staging environment without also being on in
production, which is why no end-to-end test of the card rail had ever been run.

Two properties were built rather than assumed:

- **Fail closed.** The default is the pause. A deployment with a typo'd value,
  a missing variable, or a variable set in the wrong service gets the pause. The
  only way to a live card rail is an explicit, recognised, affirmative value.
- **One authority.** The flag is not the answer to "can this buyer pay by card".
  It is one input to `services/marketplace_card_capability.py`, which is the
  single place that decides, for one seller and one listing:

  ```
  FEATURE_DISABLED  ->  STRIPE_UNAVAILABLE  ->  SELLER_NOT_APPROVED
  ->  STRIPE_NOT_CONNECTED  ->  CARD_CAPABILITY_DISABLED
  ->  STRIPE_REQUIREMENTS_DUE  ->  PAYOUTS_DISABLED
  ->  LISTING_INELIGIBLE  ->  INVENTORY_UNAVAILABLE  ->  AVAILABLE
  ```

  Evaluated in that order, first hit wins. The order is the point: a seller who
  has not onboarded and whose listing is also ineligible is told about
  onboarding, because that is the one they can act on first.

  Some reasons are the seller's business and not the buyer's —
  `SELLER_PRIVATE_REASONS` maps those to a single neutral buyer-facing message,
  so a buyer never learns that a particular seller's Stripe requirements are
  outstanding.

The mobile client previously carried its own opinion about the pause and could
disagree with the server. It no longer does: `MarketplaceCheckoutScreen` renders
the server's decision and reason code. A client that has not been updated cannot
present a card option the server would refuse, because the option is not in the
payload.

## 3. Fulfillment

Full detail in `FULFILLMENT_AND_SETTLEMENT_MODEL.md`. The one sentence that
matters here: **a seller cannot release their own money.** The transition into
`delivered` is authorised for buyer, carrier, admin and system — not seller —
and that single omission is what the module exists to enforce.

## 4. Settlement and payout

Full detail in `FULFILLMENT_AND_SETTLEMENT_MODEL.md` §4 and
`PAYOUT_SCHEDULER_RUNBOOK.md`.

Money moves only when **three** independent switches agree, on **Postgres**,
under a leader lock:

```
MARKETPLACE_PAYOUT_WORKER_ENABLED=true
MARKETPLACE_PAYOUT_WORKER_DRY_RUN=false
MARKETPLACE_PAYOUT_WORKER_OWNER_AUTHORIZED=true
```

`ENABLED` alone is safe and useful: the cycle runs read-only and reports what it
*would* pay. That report is the evidence for deciding to set the other two.

## 5. Reconciliation

`PAYMENTS_RECONCILIATION_RUNBOOK.md`. The engine existed; nothing unattended
called it. It now has a caller, and a new check that can see a settlement
stranded mid-chain — a condition the ledger cannot see, because to the ledger
the money is exactly where it belongs.

## 6. Git integration

Local `main` had diverged from `origin/main` and was not a fast-forward. It was
preserved first, at `backup/local-main-before-payments-integration`
(`8ae1c2a27`), and then left alone. This mission's work was built in a clean
worktree on a branch taken from current `origin/main` (`f5483bb81`), so nothing
from the stale local lineage was merged in blindly and nothing from it was lost.

Six commits, 37 files, +4750 / −94:

| Commit | Subject |
|---|---|
| `d291efc17` | the marketplace card pause becomes a flag, and one place answers it |
| `498c6b0ec` | an order has to reach the buyer before the seller is paid |
| `295c6dc01` | the hold becomes configuration, and something finally releases it |
| `a607446c6` | the chain gets a caller, and the caller is not allowed to pay anyone |
| `12d007478` | one answer to which Stripe this is, and it is not "not configured" |
| `f3c80b76c` | the reconciler gets a caller, and something to find with it |

## 7. What is still blocked

`services/stripe_mode.py` answers "which Stripe is this?" with one of `test`,
`live`, `unconfigured`, `unrecognized` — deliberately four values and not a
boolean, because "no key at all" and "a key I do not recognise" are different
operational problems and collapsing them hid both.

Production currently answers **`live`**. To run the mandated test-mode E2E the
owner must supply, in a non-production environment:

| Variable | Current | Needed |
|---|---|---|
| `STRIPE_SECRET_KEY` | `sk_live_…` | `sk_test_…` |
| `STRIPE_PUBLISHABLE_KEY` | `pk_live_…` | `pk_test_…` |
| `STRIPE_WEBHOOK_SECRET` | set (live endpoint) | test-mode `whsec_…` |
| `STRIPE_CONNECT_CLIENT_ID` | **absent** | `ca_…` |

`stripe_mode.missing_test_mode_variables()` returns exactly this list at
runtime, so the handoff does not depend on this table staying current.

No placeholder, fixture or fake credential was introduced anywhere. Everything
downstream of the credentials is implemented and tested against the code paths
that will run; what is untested is the round trip to Stripe's servers, and that
is stated as untested rather than inferred from unit coverage.

## 8. Production status at mission end

Verified against the single `production` environment on the `CoinPilotX` service:

```
UNSET   MARKETPLACE_CARD_PAYMENTS_ENABLED     -> card rail OFF
UNSET   MARKETPLACE_PAYOUT_WORKER_ENABLED     -> no payout cycle
UNSET   PAYMENTS_RECONCILIATION_ENABLED       -> sweep dormant
UNSET   MARKETPLACE_SETTLEMENT_HOLD_HOURS     -> documented 48h default
UNSET   STRIPE_CONNECT_CLIENT_ID
```

Every one of them is off *by absence*, which is the design: the fail-closed
default means the safe state needs no configuration and the unsafe state needs
a deliberate one. **Live card payments were not switched on during this
mission.**

Readiness criteria for the activation decision: `PRODUCTION_ACTIVATION_READINESS.md`.
