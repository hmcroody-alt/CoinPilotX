# Stripe Connect — Stage 0 Audit

Date: 2026-09-17
Stripe account: COINPLOTXAI INC (`acct_1TTVo7FP8qvvGWBI`)
Scope: what exists **today**, before any Connect work. No code was changed to produce this.

## Executive summary

The brief assumes a greenfield build. It is not. PulseSoc already has a
substantially complete Connect marketplace: a seller application state machine, a
Connect onboarding route gated on PulseSoc approval, a versioned fee policy, an
immutable double-entry ledger, a settlement state machine with a protection
window, and a payout scheduler.

Four things are genuinely missing or wrong, and they are the real work:

1. **There is no test-mode Stripe key.** The deployment has exactly one
   `STRIPE_SECRET_KEY` and it is a live key. Every stage of this mission that
   requires test-mode verification is blocked until a test key is provisioned.
2. **There is no platform→connected-account `Transfer` step.** The payout engine
   jumps straight to `stripe.Payout.create` on the connected account, which under
   separate charges and transfers would draw on an empty balance.
3. **The platform fee is 0%.** The 5% rate is named `PROPOSED_` and is gated
   behind three owner-approval env vars that are all blank.
4. **Disputes are financially inert.** A `charge.dispute.created` sets a status
   string but posts no ledger entry and places no hold, so a disputed order can
   still reach `eligible` and pay the seller. This is the one finding that is a
   latent money-loss bug rather than an unbuilt feature.

## 1. Stripe account state

Verified 2026-09-17 by read-only API probe against the live key
(`stripe.Account.retrieve()` plus four deliberately-invalid calls that cannot
create anything).

| Item | State |
| --- | --- |
| Platform account | `acct_1TTVo7FP8qvvGWBI`, `type: standard`, `controller: {"type":"account"}`, country `US` |
| `charges_enabled` / `payouts_enabled` | `true` / `true` |
| Platform `requirements` | `null` — no currently_due, past_due or eventually_due |
| **Connect** | **Enabled.** See below. |
| Connected accounts | **Zero.** `Account.list()` returns an empty page — it does not error. |
| Platform capabilities | `active`: acss_debit, afterpay_clearpay, amazon_pay, bancontact, blik, card_payments, cashapp, eps, klarna, link, mb_way, pix, **transfers**, us_bank_account_ach. `pending`: cartes_bancaires. |
| API version | `2026-04-22.dahlia` (account default; nothing pinned in code) |

### Connect is enabled — the Dashboard is misleading

Every Connect URL (`/connect`, `/connect/accounts/overview`,
`/settings/connect`, and their `/test/` equivalents) redirects to
`/connect/onboarding`, which renders a "Power your platform with Connect ·
Continue setup" splash. That splash is the **setup-guide surface**, not a gate.
It persists until the first connected account exists.

The API says otherwise. Four probes, each crafted so it cannot create anything:

| Call | Result |
| --- | --- |
| `Account.create(type="express", country="ZZ")` | `InvalidRequestError`, `param=country` — "Country 'ZZ' is unknown" |
| `Account.retrieve("acct_000…")` | `PermissionError` — no access / does not exist |
| `AccountLink.create(account="acct_000…")` | `InvalidRequestError` — "No such account" |
| `Transfer.create(destination="acct_000…")` | `InvalidRequestError` — "No such destination" |
| `Payout.create(stripe_account="acct_000…")` | `PermissionError` — no access / does not exist |

All five reach **parameter validation**. None returns the platform gate
(`"signed up for Connect"` / `"only Stripe Connect platforms"`) that
`payment_provider._PLATFORM_MARKERS` watches for. A non-platform account fails
that gate *before* validating params. So `Account.create`, `AccountLink.create`,
`Transfer.create` and `Payout.create` are all live-callable today.

**Correction:** an earlier draft of this audit stated Connect was never enabled,
inferred from the Dashboard redirects alone. That inference was wrong. The
`CONNECT_PLATFORM_NOT_ENABLED` code in `payment_provider.py` is defensive cover
for a state the account is not in.

What remains true: **no Connect code path has ever executed successfully against
Stripe**, because there are zero connected accounts. Every existing Connect test
is a monkeypatched unit test, not an integration test. Enablement is no longer
the blocker; the absence of a test-mode key is.

### The Dashboard "Action required" banner

The banner reads "We need some information for your account. Provide it to keep
capabilities enabled." The API reports `requirements: null` on the platform
account, so this is **not** an account-level requirement — most likely the
`cartes_bancaires_payments` capability sitting at `pending`. Owner-only either
way; see §11.

## 2. SDK and client versions

| Component | Version | Source |
| --- | --- | --- |
| Python `stripe` | `15.1.0` | `requirements.txt:28` |
| `@stripe/stripe-react-native` | `0.61.0` | `mobile-native/package.json:39` |
| Pinned `stripe.api_version` | **None set anywhere.** The account's default API version applies. | — |

`stripe==15.1.0` supports the modern controller-based `Account.create`. The code
currently uses `type="express"`, which is the documented equivalent of
`controller.fees.payer=application` + `controller.losses.payments=application` +
`controller.stripe_dashboard.type=express` — i.e. it already matches the target
architecture and does not need to be rewritten.

## 3. Connect implementation that already exists

### Provider boundary — `services/payment_provider.py`

All Stripe calls are correctly isolated here. Functions:

| Function | Line | Notes |
| --- | --- | --- |
| `create_connected_account` | 129 | `type="express"`, requests `card_payments` + `transfers`, idempotency key `connect-account:{user_id}:{seller_type}` |
| `create_onboarding_link` | 152 | `type="account_onboarding"`, server-side only |
| `get_account_status` | 169 | reads Stripe's own `charges_enabled` / `payouts_enabled` / `requirements` |
| `create_checkout_session` | 193 | **destination charge** — see §5 |
| `create_payment_intent` | 250 | |
| `create_transfer` | 261 | **exists but has no caller** |
| `create_payout` | 272 | payout on the connected account |
| `create_refund` | 289 | **exists but has no marketplace caller** |
| `verify_webhook_signature` | 303 | delegates to the shared verifier |

`connect_failure()` (line 87) already classifies "platform never signed up for
Connect" into `CONNECT_PLATFORM_NOT_ENABLED` with honest seller copy that does
**not** tell the seller to retry — because until §1 is fixed, retrying fails
forever.

### Onboarding route — `bot.py:94073` `POST /api/pulse/payouts/connect`

Already satisfies most of the Stage 2 requirements:

- PulseSoc approval is checked **before** Stripe onboarding is offered
  (`approved_marketplace_seller_for_user` / `approved_teacher_for_user`, bot.py:94084).
- Reuses an existing `connected_account_id` instead of creating a duplicate.
- `seller_payout_accounts` has `ON CONFLICT(user_id, seller_type)` — one account per seller.
- The Account Link is generated server-side and returned only to the authenticated seller.
- When `STRIPE_SECRET_KEY` is absent the row is written as `stripe_not_configured` rather than crashing.

`seller_destination_account_id` (bot.py:94044) is a notable piece of defensive
design: a payout row exists from the moment onboarding *starts*, long before
Stripe will accept money for it, so this function returns `""` unless
`charges_enabled` **and** `payouts_enabled` are both true. That stops an
unfinished seller onboarding from turning into a **buyer-facing** checkout
failure.

### Seller state machine — `services/seller_lifecycle.py:48-62`

States: `DRAFT`, `SUBMITTED`, `UNDER_REVIEW`, `INFORMATION_REQUESTED`,
`RESUBMITTED`, `APPROVED`, `REJECTED`, `WITHDRAWN`, `EXPIRED`, `SUSPENDED`.
Transitions are a data table at line 107 with actor gating — admin-only
approvals, applicant-only submission, system-only expiry. An admin transition
with no admin id is refused rather than recorded as actor 0 (line 194).

Tables: `marketplace_merchant_applications` (bot.py:115359),
`marketplace_sellers` (bot.py:115334), plus
`seller_application_status_history` / `_notes` / `_assignments`.

**Gap vs the brief:** the brief's states `APPROVED_AWAITING_STRIPE`,
`STRIPE_ONBOARDING`, `STRIPE_INFORMATION_REQUIRED`, `STRIPE_RESTRICTED`,
`PAYOUTS_READY` do not exist as application states. They exist instead as
`seller_payout_accounts.onboarding_status` values, i.e. the Stripe half of the
lifecycle is modelled on a *different table* from the PulseSoc half. That is a
defensible split, not a defect, but the two must be read together to answer "may
this seller sell?".

## 4. Money model

All amounts are **integer minor units** throughout. No floats found in any money
column.

| Table | Location | Role |
| --- | --- | --- |
| `seller_transactions` | bot.py:115881 | per-order record; `amount_cents`, `platform_fee_cents`, `seller_net_cents` |
| `seller_payout_accounts` | bot.py:112908 | Connect account metadata only — **no bank details stored** |
| `marketplace_commercial_settlements` | `marketplace_settlement_service.py:77` | immutable settlement + payout state machine |
| `marketplace_commercial_refunds` | `marketplace_settlement_service.py:91` | refund ledger |
| `seller_payout_requests` / `seller_payout_events` | `business_os/payments/seller_payouts.py` | payout lifecycle |
| `creator_wallets` / `creator_ledger_entries` | bot.py:112872 / 112888 | **legacy, read-only** |

### Two money systems coexist

- **Legacy** (`creator_wallets` + `creator_ledger_entries`): balances are derived
  by summing ledger entries, not by reading a mutable field — but nothing ever
  writes a `release` or `credit` entry, so a held balance can never become
  available. `services/seller_money.py:38` documents this explicitly. It is inert.
- **Wave B** (`business_os.ledger` + `marketplace_commercial_settlements`): a real
  immutable double-entry ledger. `settle_paid_transaction()`
  (`marketplace_settlement_service.py:120`) posts three entries per paid
  transaction — seller earning to `seller_payable:{id}`, platform fee to
  `platform:marketplace_revenue`, tax to `liability:marketplace_tax`. Refunds
  post compensating entries via `apply_refund()` (line 176). This is the system
  to build on.

Settlement payout states: `pending_onboarding` → `pending_fulfillment` →
`protection_hold` → `eligible` → `scheduled` → `paid`, plus hold/dispute/reversed.

**No bank-account or identity-document data is stored anywhere.** Confirmed.

## 5. The architecture conflict: destination charges vs separate charges and transfers

**The brief requires separate charges and transfers. The code implements
destination charges.**

`payment_provider.create_checkout_session` (line 222-225):

```python
payment_intent_data: dict[str, Any] = {"metadata": metadata}
if connected_account_id:
    payment_intent_data["application_fee_amount"] = int(platform_fee_cents or 0)
    payment_intent_data["transfer_data"] = {"destination": connected_account_id}
```

That is a destination charge with an application fee — Stripe moves the money to
the seller automatically at charge time, and PulseSoc never controls transfer
timing.

The brief's stated reason for requiring separate charges and transfers is
multi-seller carts. **That reason does not hold here.** A cart may contain items
from several sellers, but checkout is already split per seller:
`marketplace_cart_routes.py:672` filters the cart to one `seller_user_id`, and
`cart_checkout()` (line 623) requires `seller_user_id` in the payload. One charge
never spans two sellers today.

However, destination charges are still wrong for this product, for a different
reason: **they settle to the seller at charge time**, which is incompatible with
the entire protection-window / delivery-gated release model that
`marketplace_commercial_settlements` already implements. The settlement state
machine assumes PulseSoc holds the funds and releases them later. Destination
charges contradict it.

There is a partial acknowledgement of this in the code already — when the seller
is not yet chargeable, checkout falls back to a plain platform charge and records
earnings as `ledger_pending_onboarding`. That fallback path *is* the
separate-charges-and-transfers model. It just isn't the default.

**Recommendation:** make the platform charge the only path, drop
`transfer_data`/`application_fee_amount`, and add the missing `Transfer` step.
This is an owner decision because it changes live money movement — see §9.

## 6. The missing Transfer step

`services/business_os/payments/seller_payouts.py:578` `build_stripe_payout_args`
shapes arguments for:

```python
stripe.Payout.create(**args["kwargs"], stripe_account=args["stripe_account"], ...)
```

— a payout **from the connected account's Stripe balance to its bank**.

Under separate charges and transfers the connected account's balance is empty
until the platform creates a `stripe.Transfer`. So this call would fail with
insufficient funds. `mark_payout_submitted` already accepts a `stripe_transfer_id`
parameter (line 603), so the shape was anticipated, but nothing populates it and
`payment_provider.create_transfer` (line 261) has **no caller anywhere in the
repo**.

Additionally, Express accounts default to an *automatic* payout schedule — Stripe
pays the seller's bank on its own, and the platform should not call
`Payout.create` at all unless the connected account is explicitly set to manual.

`services/marketplace_payout_scheduler.py` injects `provider_create` as a
callable; tests pass a fixture and **there is no production caller**. It is also
not in the `Procfile`. So no money moves automatically today, by anything.

The asymmetry is stark: the webhook handler already processes `transfer.created`
and `transfer.reversed` (bot.py:108540) via
`seller_payouts.apply_stripe_transfer_event`. The *inbound* half of the transfer
lifecycle is built. Only the outbound call is missing — `transfer_group`,
`source_transaction` and `on_behalf_of` appear nowhere in the repo.

## 7. Platform fee

`services/business_os/marketplace/policy.py`:

- `POLICY_VERSION = "MARKETPLACE_STANDARD_V1"` (line 11)
- `PROPOSED_PLATFORM_FEE_BPS = 500` — 5% (line 20)
- `FEE_BASE = "merchandise_net_after_seller_discount"` (line 12)
- `BUYER_SERVICE_FEE_CENTS = 0`, `LISTING_FEE_CENTS = 0`, `STANDARD_MONTHLY_SELLER_FEE_CENTS = 0`
- `STANDARD_PAYOUT_PROTECTION_DAYS = 2`, `STANDARD_RETURN_WINDOW_DAYS = 14`

`fee_policy_active()` (line 68) requires **all three**:

```
MARKETPLACE_STANDARD_V1_OWNER_APPROVED
MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY
MARKETPLACE_STANDARD_V1_EFFECTIVE_AT   (must be a past ISO timestamp)
```

All three are present but **blank** in `.env.example:1322-1324`. When inactive,
`fee_bps` is `0` (line 125), so **the platform fee is currently zero**.

The quote is versioned and snapshotted per settlement (`fee_policy_version`,
`fee_rate_bps` stored on the settlement row), so past orders are already immune
to future fee changes. Stage 5's versioning requirement is effectively met.

The legacy `platform_fee_rules` table (bot.py:116060) seeds
`PLATFORM_FEE_MERCHANT_PERCENT=10` / `PLATFORM_FEE_TEACHER_PERCENT=15` from env.
These are a *different*, older fee system and disagree with the 5% policy. Which
one governs must be settled before launch.

## 8. Webhooks

One handler, `stripe_webhook()` at bot.py:107778, served on five URL aliases
(bot.py:107773-107777): `/stripe-webhook`, `/stripe/webhook`,
`/api/stripe/webhook`, `/webhook/stripe`, `/webhooks/stripe`.

`services/stripe_webhook_verification.py` is mature and worth preserving as-is:

- Always verifies the HMAC via `stripe.Webhook.construct_event`; no relaxation.
- Supports **multiple** signing secrets — `STRIPE_WEBHOOK_SECRET` plus a
  comma/whitespace-separated `STRIPE_WEBHOOK_SECRETS` — because Stripe issues a
  separate secret per event destination. The module docstring records a real
  incident: the live destination `pulsesoc-ads-billing-live` was **disabled by
  Stripe after nine days of 100%-failing deliveries** because the deployed secret
  belonged to a different destination.
- Never logs or returns secret material.

Deduplication: `stripe_events` (bot.py:120054) with `stripe_event_id TEXT UNIQUE`
and `event_id TEXT UNIQUE`. `stripe_event_processed()` (bot.py:107322) is checked
before processing at bot.py:107856. Business OS tables carry their own
`provider_event_id TEXT UNIQUE` (bot.py:113011, 116032) and
`UNIQUE(provider, provider_event_id)` (bot.py:116382).

Event coverage is **much better than the brief assumes**. Handled today:

| Event | Line |
| --- | --- |
| `checkout.session.completed` / `.expired` | 107862 / 108088 |
| `invoice.paid` / `.payment_succeeded` / `.payment_failed` | 107709, 108177 |
| `payment_intent.succeeded` / `.payment_failed` / `.canceled` | 108224 / 108399 / 108504 |
| `account.updated` | 108546 |
| `transfer.created` / `transfer.reversed` | 108540 |
| `payout.paid` / `payout.failed` | 108568 |
| `charge.refunded` | 108617 |
| `charge.dispute.created` / `.updated` / `.closed` | 108619 / 108625 / 108553 |

`account.updated` is handled **twice**, deliberately: `connect_accounts.apply_account_updated_event`
(bot.py:108546) projects it into the Business OS with attribution by
`metadata.user_id` → projection row → legacy mapping, opening an
`ORPHAN_STRIPE_OBJECT` incident when unattributable; and bot.py:108557 updates
the legacy `seller_payout_accounts` row's `onboarding_status`,
`payouts_enabled`, `charges_enabled` and `missing_requirements_json`.

So a seller who finishes Stripe onboarding **is** marked eligible server-side
already. Transfer events are handled too — even though nothing yet creates a
transfer (§6).

**Not handled:** `capability.updated`, `charge.refund.updated`,
`account.application.deauthorized`, `balance.available`,
`transfer.updated`/`transfer.failed`, `payout.canceled`.

## 9. Environment variables

Declared in `.env.example` and used: `STRIPE_SECRET_KEY`,
`STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_CLIENT_ID`,
plus the subscription price/product ids.

Used in code but **not declared** in `.env.example`: `STRIPE_WEBHOOK_SECRETS`.
`tests/test_environment_contract.py` gates new `os.getenv` names against
`.env.example`; this one predates the current tree but should be declared.

`provider_status()` (payment_provider.py:34) derives mode from the key prefix
(`sk_live_` / `sk_test_`) and reports only booleans — no key material. Good.

**No live/test key separation exists, and no test key exists at all.** Verified
2026-09-17 against the deployed Railway service `CoinPilotX` (names and key
*prefix class* only — no values read or logged):

| Variable | Class |
| --- | --- |
| `STRIPE_SECRET_KEY` | **live** secret |
| `STRIPE_PUBLISHABLE_KEY` | **live** publishable |
| `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` | **live** publishable |
| `STRIPE_WEBHOOK_SECRET` | set |
| `STRIPE_*_PRICE_ID` (4) | set |

`STRIPE_CONNECT_CLIENT_ID` and `STRIPE_WEBHOOK_SECRETS` are **not set** on the
service despite being read by code.

This is the mission's hard blocker. The brief requires test mode first and
forbids live money movement without owner authorization, but the only key
available *is* the live key. Stage 11 (isolation) and Stage 13 (test-mode E2E)
cannot start until a `sk_test_…` key is provisioned. Owner action — see §11.

## 10. Other gaps

| Gap | Impact |
| --- | --- |
| No delivery-confirmed signal from the buyer/courier side | `protection_hold` → `eligible` cannot be driven by real delivery; only by elapsed time |
| No marketplace refund path calls `stripe.Refund.create` | returns are recorded but nothing initiates the money going back to the buyer. The *inbound* side is wired: `charge.refunded` runs `pulse_apply_marketplace_charge_refund` (bot.py:108643) → `settlements.apply_refund()`, posting compensating ledger entries |
| Disputes tracked but financially inert | `charge.dispute.*` sets `seller_transactions.status` to `dispute_opened`/`_updated`/`_resolved` and emits a checkout event (bot.py:108623-108640), but posts **no ledger entry**, places **no settlement hold**, and records **no seller liability**. A disputed order can still reach `eligible` and pay out |
| No dispute-fee accounting | Stripe's dispute fee is a platform cost with no ledger account |
| USD only | `marketplace_cart_routes.py:691` rejects mixed-currency checkout; `MARKETPLACE_SHIPPING_COUNTRIES=US` |
| Payout scheduler not in `Procfile` | nothing runs it |
| No negative-balance / reserve handling | |

## 11. Decisions required from the owner

These block Stages 11, 13 and 14. None can be answered by reading the code.

1. **Provision a test-mode Stripe key.** *Blocking.* Create a restricted or
   secret `sk_test_…` key and set it on the Railway service under a distinct
   name (`STRIPE_SECRET_KEY_TEST`). Without it, nothing in this mission can be
   verified anywhere except against live money. Owner-only — key creation.
2. **Clear the Dashboard "Action required" banner.** The platform account's API
   `requirements` are `null`, so this is most likely the `pending`
   `cartes_bancaires_payments` capability. Owner-only to confirm and resolve.
3. ~~**Confirm the platform commission.**~~ **Answered 2026-09-17:** 5%
   (`PROPOSED_PLATFORM_FEE_BPS = 500`) is the real number, wired as the single
   fee source, with the three owner gates left **unset** so the effective rate
   stays 0% until the owner flips them. Legacy 10%/15% `platform_fee_rules` is
   to be retired.
4. **Who absorbs Stripe processing fees** — PulseSoc or the seller? Recommended
   default is PulseSoc, but it must be stated in the seller agreement before the
   disclosure gate can be set.
5. ~~**Approve the architecture change**~~ **Approved 2026-09-17:** platform
   charge + explicit `stripe.Transfer`, dropping `transfer_data` and
   `application_fee_amount` from `create_checkout_session`.
6. **Confirm the launch jurisdiction.** The code implies US-only
   (`MARKETPLACE_SHIPPING_COUNTRIES=US`). Confirm before claiming broader
   seller availability to Stripe.

## 12. What must not be broken

Per `docs/realtime_audio_change_policy.md` and the protection suite, this work
must not touch calls, livestream, Agora, Mux, messaging, notifications or music.
None of the files identified above are in
`config/realtime-audio-protected-paths.json`. New routes must declare their auth
or `test_new_routes_must_declare_their_auth` fails the protection suite.
