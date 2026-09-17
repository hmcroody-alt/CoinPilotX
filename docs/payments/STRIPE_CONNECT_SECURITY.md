# Stripe Connect — Security & Key Isolation

Date: 2026-09-17

This document names environment variables. It contains **no values** — no secret
keys, restricted keys, webhook signing secrets, bank details, tax identifiers or
personal data, and none were printed, logged, screenshotted or committed at any
point in this work.

---

## 1. The current key situation is the top security finding

The deployment has exactly **one** `STRIPE_SECRET_KEY`, and it is a **live** key.
There is no test-mode key anywhere in the environment.

Consequences, all of them bad:

- No stage of this work can be exercised end to end without touching real money.
- Any developer or agent running the test suite against a populated environment
  is one mistake away from a live charge.
- The webhook endpoint verifies against a single `STRIPE_WEBHOOK_SECRET`, so
  test-mode events cannot be delivered to a separate handler even if a test key
  existed.

**This is the blocking owner action.** Everything in §2 is the design that
applies once a test key exists; until then, isolation is enforced by the absence
of a second key rather than by configuration, which is not a control.

## 2. Key isolation design

| Variable | Purpose | Mode |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | server-side API calls | must be `sk_test_…` in every non-production environment |
| `STRIPE_PUBLISHABLE_KEY` | client-side | `pk_test_…` alongside it — a test publishable key with a live secret key produces confusing, half-working checkouts |
| `STRIPE_WEBHOOK_SECRET` | signature verification | **per endpoint, not per account.** A test endpoint has its own secret |
| `STRIPE_CONNECT_CLIENT_ID` | OAuth/Connect identity | distinct between test and live |

Rules:

1. **Never a live key outside the production Railway service.** Not in
   `.env.local`, not in CI, not on a developer machine, not in a worker service
   that "only reads".
2. **Test suites must be structurally incapable of using a live key.** Tests in
   this repository set their own throwaway secrets at module import
   (`whsec_…_tests_only`, `sk_test_…_tests_only`) before importing `bot`, so a
   populated ambient environment cannot leak into them.
3. **`stripe_webhook_recovery_audit.py` never prints key material.** It sets a
   local throwaway signing secret, exercises the real verification path against
   locally-signed fixtures, and reports only route coverage, event coverage, and
   HTTP status codes.

## 3. Webhook security

Signature verification lives in `services/stripe_webhook_verification.py` and
uses `stripe.Webhook.construct_event` against the raw request body.

Three properties that must not regress:

- **Raw body, not parsed JSON.** `request.data`, never `request.get_json()`. Any
  re-serialisation changes the bytes and invalidates every signature.
- **Invalid signatures are rejected with 4xx before any handler runs.** Verified
  continuously by the audit script's deliberately-malformed fixture, which must
  return 400.
- **Multiple candidate secrets are supported** (rotation), and the number tried
  is logged — but never the secrets themselves. The rejection log line reports
  `secrets_tried=<n>` and the exception class, nothing more.

Replay protection is two-layered: `stripe_event_processed(event_id)` short-circuits
a redelivered event before any handler, and every individual effect carries its
own database-enforced idempotency key. The second layer matters because the first
is per-event-id and Stripe can legitimately send two different events describing
the same underlying money movement.

## 4. What PulseSoc deliberately does not hold

| Data | Where it lives |
| --- | --- |
| Bank account / routing numbers | Stripe only, collected on Stripe-hosted onboarding |
| Identity documents, SSN, EIN, DOB | Stripe only |
| Card numbers | Stripe only — PulseSoc never sees a PAN |
| Tax identification numbers | Stripe only |

What PulseSoc stores about a connected account, in `seller_payout_accounts`: the
`acct_…` identifier, `charges_enabled`, `payouts_enabled`, `onboarding_status`,
a JSON array of `requirements.currently_due` **key names**, and timestamps.

The `requirements` array is the one place a mistake could leak PII, because
Stripe's requirement keys look like field paths
(`individual.id_number`, `company.tax_id`). Only the key names are stored; the
values are never fetched. A code change that stored `requirements` verbatim from
an expanded Account object would be a PII incident.

`tests/test_seller_money_read.py::test_the_full_stripe_identifier_never_leaves_the_server`
guards the account id itself from reaching a client payload.

## 5. Authorization boundaries

- **Onboarding** — a seller may only create or retrieve an Account Link for their
  own `user_id`, and only after PulseSoc approval. There is no route that accepts
  a seller id from the client.
- **Owner/admin payout actions** — behind the owner admin surface, which is a
  separate authentication tier from seller auth.
- **The webhook endpoint is unauthenticated by design** and defended solely by
  signature verification. It is therefore the highest-value target in this
  subsystem: every money-moving effect in the marketplace is reachable from it.
  This is why the raw-body property in §3 is treated as a hard invariant rather
  than a style preference.

## 6. Money-movement authorization

No code path in this repository currently initiates a live charge, transfer,
refund or payout automatically:

- The platform fee is 0 bps until three separate owner gates open (§7).
- `marketplace_payout_scheduler.run_once` — the only function that calls
  `stripe.Transfer.create` and `stripe.Payout.create` — has **no production
  caller**. It cannot fire.

That is the intended state until the owner explicitly authorizes live movement.
Any commit that adds a caller for `run_once` is a commit that turns on real
money leaving the platform balance, and should be reviewed on that basis alone.

## 7. Owner-gated flags

| Variable | Set? | Effect while unset |
| --- | --- | --- |
| `MARKETPLACE_STANDARD_V1_OWNER_APPROVED` | unset | platform fee stays 0 bps |
| `MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` | unset | platform fee stays 0 bps |
| `MARKETPLACE_STANDARD_V1_EFFECTIVE_AT` | unset | platform fee stays 0 bps |

All three must pass simultaneously. They are three variables rather than one
because they encode three distinct facts — the rate is decided, the seller-facing
disclosure is published, and the start date has arrived — and any of them being
false makes charging the fee a commercial or legal problem rather than a
technical one.

## 8. Logging discipline

Every log line added in this work names identifiers and never amounts-plus-PII:
`MARKETPLACE_DISPUTE_HOLD_FAILED`, `MARKETPLACE_FRAUD_WARNING_SKIPPED`,
`MARKETPLACE_CONNECT_DEAUTHORIZED`, `MARKETPLACE_ONBOARDING_RECONCILE_FAILED`,
`MARKETPLACE_DISPUTE_WON_NEEDS_REVIEW`.

Each of these is `logging.warning` or `logging.exception` rather than `info`,
because each marks a case where money is stuck or at risk. Failures on the
money-reversal paths are never swallowed silently — the `except` blocks around
the dispute, fraud-warning and onboarding-reconciliation handlers exist to keep a
webhook returning 200 (so Stripe does not retry into a partial state), not to
hide the error.
