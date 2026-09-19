# Stripe Connect — Implementation Report

Date: 2026-09-17
Platform account: COINPLOTXAI INC (`acct_1TTVo7FP8qvvGWBI`)
Repository: `/Users/hmcherie/Desktop/CoinPilotX`, branch `main`

> **Superseded in part.** This report describes the money-movement layer as
> built on 2026-09-17. Two things have changed since:
>
> - The notification and email layer it lists as absent now exists. See
>   `PAYMENTS_NOTIFICATION_MATRIX.md` and
>   `TRANSACTIONAL_EMAIL_DESIGN_SYSTEM.md`.
> - The go-live blockers in §11 were re-verified against the tree on
>   2026-09-19 and one conclusion changed: there is no
>   `MARKETPLACE_CARD_PAYMENTS_ENABLED` variable.
>   `marketplace_card_payments_paused()` returns a hardcoded `True`, so
>   enabling card payments is a code change and a deploy, not a configuration
>   edit. `PAYMENTS_PRODUCTION_ACTIVATION_CHECKLIST.md` is the current list.
>
> Everything else below still holds.

---

## 1. Headline

**Live charges: NO. Seller transfers: NO. Bank payouts: NO.**

None of the three are enabled, and none were enabled by this work. The platform
fee is 0%. No code path in this repository can initiate a live charge, transfer,
refund or payout automatically. That is the intended state pending owner
authorization, and it is enforced structurally rather than by configuration:
`marketplace_payout_scheduler.run_once` — the only function that calls
`stripe.Transfer.create` and `stripe.Payout.create` — has no production caller.

The brief's instruction was "do not declare success because the UI exists". The
corresponding statement here is: the marketplace's **money-protection** paths are
now complete and tested, and its **money-release** paths are deliberately not.

## 2. Stripe Dashboard configuration completed

**None.** No Dashboard setting was changed.

The Stage 0 audit established by read-only API probe that **Connect is already
enabled** on `acct_1TTVo7FP8qvvGWBI` — the "Continue setup" splash that every
Connect URL redirects to is the setup-guide surface, not a gate, and it persists
until the first connected account exists. The `transfers` capability is active.
There was therefore nothing to enable.

Everything still outstanding in the Dashboard is behind the owner-only material
listed in §11 and was not touched.

## 3. What was built

### Stage 4/5 — charge model and fee policy (`bed7a89c`, `3e2d7e24`)

- Confirmed and locked in **separate charges and transfers**: the buyer always
  pays the platform; no `transfer_data` or `application_fee_amount` exists in the
  payment construction path.
- One versioned fee authority (`MARKETPLACE_STANDARD_V1`, 500 bps proposed)
  replacing three sources that could disagree. Effective rate is **0 bps** until
  three separate owner gates all open.
- Per-order snapshot of policy version and rate, so a later policy change cannot
  retroactively alter an existing order's refund arithmetic.

### Stage 8 — chargebacks (`08761a35`)

The one finding from the audit that was a latent **money-loss bug**, in two
layers:

1. The webhook read `metadata["seller_transaction_id"]` off a **Dispute** object.
   Stripe does not copy a Charge's metadata onto a Dispute — its `metadata` is
   its own and empty. So a chargeback touched **no marketplace row at all**.
2. Even when it did fire, it wrote only a status string and set no
   `blocker_code`, so `transition_payout` was free to take a disputed order
   `eligible → scheduled → paid`. PulseSoc would transfer the seller their
   earnings on money Stripe was in the middle of taking back, then lose the
   dispute with nothing to claw it from.

Now: disputes resolve via the payment intent (the only identifier both object
shapes carry); `created` places a real hold on **every** settlement behind the
charge; `closed`/won releases to the state recovered from the immutable event
log; `closed`/lost reverses the ledger through the same allocator refunds use.

### Stage 7 (partial) — onboarding reconciliation (`137de0b8`)

A caller audit proved the entire payout release chain was dead. One link was
unambiguously a bug and moves no money, so it was wired: a sale made before the
seller finished Connect opens in `pending_onboarding`, and nothing ever revisited
those rows. `account.updated` now calls `reconcile_seller_onboarding`, keyed on
the Stripe account id so redeliveries dedupe.

The rest of the chain was **deliberately not** stubbed — see §9.

### Stage 9 — webhook event coverage (`c839ae2a`)

Two Connect lifecycle events that had no handler:

- **`radar.early_fraud_warning.created`** — the issuer telling Stripe a card was
  used fraudulently, days before the dispute it predicts. It is the last signal
  that arrives while the money is still recoverable. Places a `fraud_warning`
  blocker and deliberately touches no ledger: a warning is a prediction, not an
  outcome.
- **`account.application.deauthorized`** — the seller revoking PulseSoc's access.
  Stripe sends no `account.updated` alongside it, so our copy kept both
  capability flags at 1 forever and every transfer to that account would fail at
  the provider with nothing explaining why. The connected account id is the
  event's own `account` field; `data.object` there is the deauthorized
  Application.

Also fixed a **false failure** in `stripe_webhook_recovery_audit.py`: it grepped
`bot.py` for `stripe.Webhook.construct_event`, which had moved into
`services/stripe_webhook_verification.py`, so the one webhook health summary a
human reads was reporting a missing safety property that is present and exercised
by its own signed fixtures.

### Documentation (`47428c43`, `fd12983f`, `78d027fd`, this commit)

All seven required documents under `docs/payments/`.

## 4. Commit hashes

| Commit | Subject |
| --- | --- |
| `47428c43` | docs(payments): audit what Stripe Connect already is before building it |
| `fd12983f` | docs(payments): Connect is enabled — the Dashboard splash was not a gate |
| `bed7a89c` | marketplace: charge the platform and transfer to the seller separately |
| `3e2d7e24` | marketplace: one versioned fee authority instead of three disagreeing numbers |
| `08761a35` | marketplace: a chargeback must freeze the payout it is taking back |
| `137de0b8` | marketplace: completed Connect onboarding must unstick the sales before it |
| `c839ae2a` | marketplace: hold the payout on a fraud warning, and stop paying a disconnected account |
| `78d027fd` | docs(payments): the architecture, the ledger, the security boundary, the runbook |

A ninth commit carries this report and `STRIPE_CONNECT_TEST_REPORT.md`. Its hash
is absent from the table above for the obvious reason: a commit cannot contain
its own hash. `git log --oneline -9` is the complete list.

Every commit was made with an explicit pathspec naming only this mission's files,
because the checkout is shared with other concurrent sessions and a bare commit
would capture whatever they had staged.

## 5. Files changed

| File | Change |
| --- | --- |
| `bot.py` | dispute/fraud-warning/deauthorization handlers; reversal id resolution and shared allocator; webhook dispatch for 3 events |
| `services/marketplace_settlement_service.py` | `settlements_for_payment`, `hold_origin_state`, `reconcile_seller_onboarding` |
| `services/business_os/marketplace/policy.py` | versioned fee authority (earlier commit) |
| `scripts/stripe_webhook_recovery_audit.py` | 6 required events added; false-failure fix |
| `tests/marketplace/test_post_settlement_finance.py` | 6 → 15 tests |
| `tests/marketplace/test_reservation_webhook_wiring.py` | 12 → 19 guards |
| `docs/payments/*.md` | 7 documents |

## 6. Migrations

**None.** No schema change was required. Every table this work uses —
`marketplace_commercial_settlements`, `marketplace_payout_state_events`,
`marketplace_commercial_refunds`, `seller_payout_accounts`,
`seller_payout_requests` — already existed in `bot.init_db()`.

## 7. Environment variables

Names only; no values appear in this document or in any commit.

| Variable | Set in production? |
| --- | --- |
| `STRIPE_SECRET_KEY` | **set — live key** |
| `STRIPE_PUBLISHABLE_KEY` | set |
| `STRIPE_WEBHOOK_SECRET` | set |
| `STRIPE_CONNECT_CLIENT_ID` | declared |
| `MARKETPLACE_STANDARD_V1_OWNER_APPROVED` | **unset** — fee stays 0 bps |
| `MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` | **unset** — fee stays 0 bps |
| `MARKETPLACE_STANDARD_V1_EFFECTIVE_AT` | **unset** — fee stays 0 bps |
| a test-mode `sk_test_…` | **does not exist** — the blocking gap |

This work introduced **zero** new environment variables. Confirmed by
`git diff | grep -c "os.getenv"` returning 0 across every changed file.

## 8. Webhook endpoints and required events

Five accepted paths, all verified present: `/stripe/webhook`,
`/api/stripe/webhook`, `/stripe-webhook`, `/webhook/stripe`, `/webhooks/stripe`.

18 required events, canonical list in `scripts/stripe_webhook_recovery_audit.py`.
Handlers are inert unless the Dashboard endpoint subscribes to them — endpoint
subscription is Dashboard-side and was **not** changed, so the six events added
to the required list this mission may or may not currently be subscribed:

`charge.refunded`, `charge.dispute.created`, `charge.dispute.updated`,
`charge.dispute.closed`, `radar.early_fraud_warning.created`, `account.updated`,
`account.application.deauthorized`, `payout.paid`, `payout.failed`.

**Owner action:** confirm all 18 are subscribed on the live endpoint. Run
`python3 scripts/stripe_webhook_recovery_audit.py` for the current list.

## 9. Test results

See `STRIPE_CONNECT_TEST_REPORT.md` for the full table. Summary: 32 marketplace
files green (one file per process — they cannot share a pytest process), except 3
pre-existing failures in `test_seller_listing_readiness_route.py` belonging to
another session's in-flight work. `bot.py` imports clean with 2118 routes. Both
new webhook-wiring tests were verified by mutating the dispatch and confirming
failure, with `bot.py` restored byte-identically afterwards (SHA-256 matched).

**Device evidence: none.** No physical iPhone 16 Pro verification was performed,
because a checkout E2E is only meaningful in test mode and no test key exists.

## 10. Deployment status

**Deployed on owner authorization.** The nine commits were rebased onto
`origin/main` and pushed as `e429a6cf`; Railway auto-deployed all 13 services
from that commit. Production verified: `pulsesoc.com` returns 200, and
`/api/stripe/webhook` returns 400 to both an unsigned request and a
bad-signature request, so signature verification is intact on the live endpoint.

They were **not** pushed as the local `main` branch. Local `main` was a parallel
lineage 44 commits behind the remote, and carried one commit belonging to another
session — already on `origin/main` under a different SHA, proven by identical
`git patch-id`. Only this mission's nine commits were rebased and pushed; the
shared checkout's 35 uncommitted foreign files were left untouched.

**Deploying this did not enable any money movement.** The three fee gates are
still unset, so the effective rate stays 0 bps, and `run_once` still has no
production caller. What went live is the money-*protection* half: the chargeback
freeze, the fraud-warning hold, the deauthorization guard, and the onboarding
reconciliation. The chargeback defect described in §3 was live in production
before this deploy and is now fixed.

## 11. Owner actions required

Ordered by how much they block.

1. **Provision a test-mode Stripe key** (`sk_test_…` + `pk_test_…` + a test-mode
   webhook endpoint with its own signing secret). This blocks all E2E
   verification, the device test, and any Connect onboarding rehearsal. It is the
   single highest-value action.
2. **Clear the "Action required" banner** on the Stripe account. Behind it are
   items only the owner can supply.
3. **Legal-representative identity, beneficial ownership, SSN/EIN, DOB, company
   bank account, Stripe agreement acceptance, business attestations.** Not
   requested, not collected, not stored — and must be entered by the owner
   directly into Stripe.
4. **Decide the delivery-confirmation mechanism.** `mark_delivered` has no caller
   because the trigger is a product decision: seller marks shipped, buyer
   confirms receipt, or a carrier webhook. Guessing would fabricate the trigger
   for every real payout. Until this is decided, no settlement can leave
   `pending_fulfillment`.
5. **Authorize the payout worker.** Adding a caller for `run_once` is the commit
   that turns on real money leaving the platform balance.
6. **Decide who absorbs Stripe's processing fees.** Today the ledger splits the
   buyer total without deducting them, so `platform:marketplace_revenue`
   overstates real margin.
7. **Decide negative-balance recovery.** A dispute lost after a transfer leaves
   the seller owing PulseSoc. Netting against future sales, requesting repayment,
   or writing off — nothing is chosen, and nothing will silently net.
8. **Decide what happens to `eligible` settlements when a seller deauthorizes.**
9. **Confirm the platform fee**, then open all three gates together.
10. **Confirm launch jurisdiction and tax handling.** `liability:marketplace_tax`
    is collected and recorded but nothing remits it.

## 12. Blockers and residual risks

| Risk | Severity | Note |
| --- | --- | --- |
| No test key | **blocking** | nothing is provable against Stripe's real behaviour |
| Release chain has no caller | **high** | money enters the ledger with no automated path out |
| Postgres dialect unverified | medium | SQLite tests structurally cannot see text-vs-integer and reserved-word crashes; the local Postgres VM does not boot |
| Endpoint subscriptions unconfirmed | medium | a correct handler subscribed to nothing is inert; the audit lists what is required but cannot read the Dashboard |
| Stripe fees unmodelled | medium | reported platform revenue is gross, not net |
| Shared dirty checkout | low | `.env.example` and 25 other files carry other sessions' uncommitted work; every commit here used an explicit pathspec |

### The one asymmetry worth stating plainly

Every path that **blocks, freezes or reverses** money is wired and tested. Every
path that **releases** money is not. That is the correct direction for the gap to
point while the system is incomplete: the failure mode is a seller waiting for a
payout that has not been switched on, rather than a seller being overpaid on
money the platform is about to lose.
