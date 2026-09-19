# Stripe Test-Mode E2E — Report

**Status: NOT RUN — BLOCKED on owner-supplied test credentials.**

This is a report of a test that has not happened. It says so at the top because
the alternative — a document describing unit coverage in language that reads
like an end-to-end result — is the specific failure this mission was created to
avoid.

Everything downstream of the credentials is implemented. What is untested is the
round trip to Stripe's servers, and that is stated as untested rather than
inferred from unit coverage.

## 1. Why it is blocked

`services/stripe_mode.py` answers "which Stripe is this?" with one of four
values, deliberately not a boolean:

| Value | Meaning |
|---|---|
| `test` | `sk_test_` / `rk_test_` — safe to exercise |
| `live` | `sk_live_` / `rk_live_` — real money |
| `unconfigured` | No key at all |
| `unrecognized` | A key whose prefix cannot be classified |

The last two are different operational problems and collapsing them into "not
configured" hid both. `may_move_real_money()` answers **yes** for
`unrecognized` — the honest answer to a key nobody can classify, and the one
that fails in the survivable direction. A test-mode run refused because of an
unreadable key wastes an afternoon; a live transfer made under the belief it was
a test does not.

Production currently answers **`live`**.

## 2. Exactly what is missing

`stripe_mode.missing_test_mode_variables()` returns this list at runtime, so the
handoff does not depend on this table staying current.

| Variable | Current state | Required value |
|---|---|---|
| `STRIPE_SECRET_KEY` | present, `sk_live_…` | `sk_test_…` |
| `STRIPE_PUBLISHABLE_KEY` | present, `pk_live_…` | `pk_test_…` |
| `STRIPE_WEBHOOK_SECRET` | present, live-mode endpoint | test-mode `whsec_…` |
| `STRIPE_CONNECT_CLIENT_ID` | **absent entirely** | `ca_…` |

A variable that is *present but holds a live key* counts as missing. It is set
to the wrong thing, which is a different problem from being unset but the same
amount of not-ready, and calling it "present" would be the more misleading half
of the truth.

`STRIPE_CONNECT_CLIENT_ID` is in the required set because onboarding a test
seller is part of the path being tested, not an optional extra. Without it there
is no connected account to transfer to, so the transfer leg — the half of
separate-charges-and-transfers that has never run — stays untested.

These must be set in a **non-production** environment. Production has exactly one
environment (`production`) on the `CoinPilotX` service, so a second environment
or a separate service is required before this test can be run at all. That is an
infrastructure decision for the owner, not a code change.

### Which service needs which

Railway variables are **per service**, and the payout worker does not run on the
web service. Setting all four on the web service alone produces a deployment
where checkout works and nothing is ever paid out.

| Variable | `CoinPilotX` (web) | `coinpilotx-pulse-worker` |
|---|---|---|
| `STRIPE_SECRET_KEY` | yes — creates the PaymentIntent | **yes — the transfer and payout legs run here** |
| `STRIPE_WEBHOOK_SECRET` | yes — the only process that verifies a signature | no |
| `STRIPE_PUBLISHABLE_KEY` | yes | no |
| `STRIPE_CONNECT_CLIENT_ID` | yes — onboarding is a web flow | no |

Production today happens to have `STRIPE_SECRET_KEY` on both, so this is easy to
satisfy by accident and equally easy to miss when standing up a second
environment from scratch. The symptom is not an error: the worker logs
`blocked_by=stripe_not_configured` at boot and then declines every cycle
quietly. Step 9 below is where that would first show up.

No placeholder, fixture or fake credential was introduced anywhere in the
codebase to work around this.

## 3. What *is* proven, and what that is worth

| Area | Coverage | What it does **not** prove |
|---|---|---|
| Mode detection | `tests/marketplace/test_stripe_mode.py` (181 lines) | That a real `sk_test_` key authenticates |
| Card capability decision | `tests/marketplace/test_card_capability.py` | That Stripe agrees about `charges_enabled` |
| Checkout options route | `tests/marketplace/test_checkout_options_route.py` | That a PaymentIntent is created |
| Fulfillment authority | `tests/marketplace/test_order_fulfillment_authority.py` | Nothing Stripe-dependent — this one is complete |
| Settlement hold / eligibility | `tests/marketplace/test_settlement_hold_and_eligibility.py` | Nothing Stripe-dependent — complete |
| Payout worker gating | `tests/marketplace/test_payout_worker_authority.py` | That a real transfer succeeds |
| Reconciliation | `tests/marketplace/test_reconciliation_cycle.py` (48 tests) | Nothing Stripe-dependent — complete |

The rightmost column is the point. The state machines are genuinely proven; the
provider boundary is not proven at all, and the two should not be reported in the
same sentence.

## 4. The test plan, ready to run

Run in order. Each step names the artefact that makes it verifiable afterwards,
because "it worked" is not a result.

| # | Step | Evidence to capture |
|---|---|---|
| 1 | Set the four variables in a non-production environment; redeploy | `stripe_mode.status()` returns `mode: "test"`, `test_mode_ready: true` |
| 2 | Onboard a test seller through Connect | `seller_payout_accounts` row with `charges_enabled` and `payouts_enabled` |
| 3 | `GET` checkout options for that seller's listing | Reason code `AVAILABLE` |
| 4 | Pay with `4242 4242 4242 4242` | PaymentIntent succeeded; settlement row in `pending_fulfillment` |
| 5 | Decline with `4000 0000 0000 0002` | No settlement row; buyer sees a decline, not a generic failure |
| 6 | Seller marks shipped with tracking | Fulfillment `shipped`; a seller attempt at `/received` returns `FULFILLMENT_ACTOR_NOT_AUTHORIZED` |
| 7 | **Buyer** confirms receipt | Fulfillment `delivered`; settlement `protection_hold` with `protection_ends_at` set |
| 8 | Set `MARKETPLACE_SETTLEMENT_HOLD_HOURS=0`; run the settlement sweep | Settlement `eligible` |
| 9 | Payout worker, stage 1 (`ENABLED` only) | Report names the settlement; `_mutation_preconditions` returns `dry_run` |
| 10 | Payout worker, stage 2 (all three switches) | Transfer **and** payout in the Stripe test dashboard; `provider_payout_id` written |
| 11 | Deliver the `payout.paid` webhook | Settlement `paid` |
| 12 | Refund the charge | `apply_refund` reverses the snapshot; `snapshot_drift` stays zero |
| 13 | Run the reconciliation sweep | Zero incidents |
| 14 | **Crash-recovery case:** kill the worker between the two legs | Sweep opens `payout_scheduled_without_provider_id` as critical after 6h |

Step 14 is the one worth the most. It is the only step that exercises the
permanent-stall bug class described in `PAYOUT_SCHEDULER_RUNBOOK.md` §7, and it
is the only one that proves the reconciliation sweep earns its cost.

Steps 9 and 10 must be done in that order and separately. Setting all three
payout switches at once skips the only cheap opportunity to see the batch before
it pays.

## 5. Automatic refusal

Until step 1 is done, `_mutation_preconditions()` returns `stripe_mode_unrecognized`
or `stripe_not_configured` and the payout worker refuses to move money on its own
account, independently of the three switches. The blocker in this document is
enforced by code, not only by this document.

## 6. Physical iPhone validation — also BLOCKED

Dependent on §2: there is nothing to validate on a device until a test-mode
checkout can complete.

What the device is needed for, and why the simulator cannot substitute:

| Check | Why a device |
|---|---|
| Apple Pay sheet in the marketplace checkout | The simulator has no Secure Element and cannot produce a real payment token |
| Universal links back from Connect onboarding | Ad-hoc signing (required for Agora) strips `associated-domains`, so links "fail" on the simulator no matter what the server does |
| Push on settlement and payout events | A dev-signed build mints sandbox APNs tokens; the production deployment leaves `APNS_USE_SANDBOX` unset, so the resulting `BadDeviceToken` is indistinguishable from a dead token and gets revoked |
| 3-D Secure challenge rendering | Presentation and dismissal differ from the simulator's |

The rig for this exists (`~/Desktop/cpx-prefetch-iso`, device `P3r7or`); the
credentials do not. Both halves of steps 3–7 above should be repeated on the
device once §2 is satisfied — the server-side assertions are the same, only the
client differs.

## 7. What must not be inferred from this document

- That card payments are ready to activate. They are not; see
  `PRODUCTION_ACTIVATION_READINESS.md`.
- That the unit coverage substitutes for the round trip. It does not, and §3's
  rightmost column is there to make that hard to forget.
- That the blocker is small because the code is finished. The untested half is
  the half that touches real money.
