# Apple Pay — Stage A–I Verification Report

**Date:** 2026-08-31
**Scope:** PulseSoc Marketplace, physical / real-world commerce only
**Mission:** Verify the existing Apple Pay implementation. Do not rebuild it.
**Supersedes:** `APPLE_PAY_STAGE_0_1_AUDIT.md` on Stage A (that file recorded the
pause reason as unknown; Stage A below answers it).

**FINAL VERDICT: PARTIAL — one payment invariant fails. Payments stay paused.**

---

## Headline

Apple Pay is real, correctly built, and structurally sound. It is not a stub and
it does not need rebuilding. Two things stand between it and production, and
neither is "write more Apple Pay code."

The first is a genuine defect I found while auditing invariant 14: **a buyer who
dismisses the payment sheet permanently leaks the reserved stock.** This is
latent today because the card lane is paused. It activates the moment the pause
lifts. Stage E's own rule applies — a failing invariant means payments stay
paused — so this drives the Stage H recommendation to NO-GO.

The second is owner-only account verification that no amount of repository
reading can settle.

---

## Stage A — why the card pause exists

The pause was introduced by exactly one commit:

```
d14cebbb78ab913e3852f830865eb5239cad9766
PulseSoc Engineer — Wed Aug 26 21:20:46 2026 -0700
fix(marketplace): pause card payments and enable fee-free cash checkout
```

Ten files, 502 insertions. The message explains *what* and *how* — the gate
returns before the PaymentIntent call in each of the three lanes, the pause is
scoped to `item_type == "marketplace_product"`, Premium and ads and payouts are
untouched — but it never states *why*. No accompanying report or doc exists.
Per the directive I am not inferring from the filename, so here is the evidence
chain instead, labelled for what it is.

> **CORRECTED 2026-08-31, after this report was first issued.** The original
> Stage A named a missing `STRIPE_CONNECT_CLIENT_ID` as the pause's root cause.
> A later full trace of the Connect implementation (Stage 10 of the hardening
> mission, "Do NOT guess") disproved that mechanism. **The root cause is now
> reported as UNKNOWN.** The retraction and the corrected payout facts are
> below. Nothing else in this report depended on the retracted claim.

### Retracted: the `STRIPE_CONNECT_CLIENT_ID` explanation

I originally wrote that the pause exists because the absent
`STRIPE_CONNECT_CLIENT_ID` leaves sellers with no payout rail. **That is wrong,
and I am withdrawing it.**

`STRIPE_CONNECT_CLIENT_ID` is an **OAuth parameter for Connect *Standard***.
This codebase does not use Standard. `services/payment_provider.py` creates
accounts server-side:

```python
account = stripe.Account.create(
    type="express",
    capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
    idempotency_key=f"connect-account:{user_id}:{seller_type}",
)
```

followed by `stripe.AccountLink.create(type="account_onboarding")`. Both calls
authenticate with the platform `STRIPE_SECRET_KEY` alone — **which is present in
production** — and neither reads a client id. Grep finds exactly **one** runtime
reference to the variable, and it is purely diagnostic
(`payment_provider.py:40`, `"connect_client_id_loaded": bool(os.getenv(...))`).

Two earlier in-repo reports already said so:
`STRIPE_ADS_BILLING_MISSION3_FINAL_REPORT.md:62` ("unset — fine; Express flow")
and `PULSESOC_STOREKIT_STRIPE_UNIFIED_PAYMENTS_FINAL_REPORT.md:221` ("optional
(Express flow doesn't need it) — MISSING (ok)"). The source I relied on,
`docs/provider_api_purchase_report.md:82`, is **stale** and should be corrected.

Setting `STRIPE_CONNECT_CLIENT_ID` in production would change nothing. Acting on
my original text would have wasted owner effort on a no-op.

### What is actually true about seller payouts

Sellers **do** have a working onboarding path (Express, server-created), and
unconnected sellers are handled deliberately rather than accidentally.
`services/marketplace_cart_routes.py:721-724` makes the intent explicit:

```python
approved = bot.approved_marketplace_seller_for_user(cur, seller_user_id)
if not approved:
    # Seller *approval* is a marketplace-eligibility gate and is a real
    # reason to stop. Seller *Connect onboarding* is not: that routes to
    # a platform charge below rather than blocking the buyer.
```

When `seller_destination_account_id()` (bot.py:87583) returns empty — it demands
both `charges_enabled` and `payouts_enabled` — the charge is created as a plain
**platform charge** with no `transfer_data.destination` and no
`application_fee_amount`, and the seller's earnings are posted to an internal
double-entry ledger at `seller_payable:{seller_id}` with idempotency key
`marketplace:settlement:seller:{tx_id}`. The settlement row records
`payout_state='pending_onboarding'`, `payout_ready=0`.

So the funds are **tracked, attributed, idempotent and auditable** — safe, but
**deferred and manual**, not automated. See Stage 11 of the hardening report for
the full trace, including the finding that `reconcile_onboarding()` — the
function that would promote `pending_onboarding → pending_fulfillment` once a
seller finishes onboarding — **has no production caller.**

That is a real gap. It is **not** the same claim as the retracted one, and it is
not established as the pause's cause.

### Ruled out, unchanged

Webhook reliability (multi-secret verification and two independent replay guards
are in place and tested — 64 tests pass), missing fulfillment (the
shipping/pickup lane is complete and gated), incorrect fees (the cash lane's $0
fee is a deliberate feature of the same commit, not a bug fix), schema migration
(the commit adds no schema), and Apple review work (the commit predates any
Apple Pay mission).

**Status: ROOT CAUSE UNKNOWN.** The commit author recorded no reason, and my
one candidate explanation did not survive verification. Only you can state why
the pause was applied on Aug 26.

---

## Stage B — production Apple Pay configuration

| Check | Result | Basis |
|---|---|---|
| MERCHANT ID | **PASS** | `merchant.com.pulsesoc.app` declared identically in `PulseSoc.entitlements`, `app.json:69-71`, and `stripePaymentSheet.ts:54`. `CODE_SIGN_ENTITLEMENTS` is set on both build configs. |
| RAILWAY VALUE NON-EMPTY | **OWNER VERIFICATION REQUIRED** | `APPLE_PAY_MERCHANT_ID` is present by name on the production `CoinPilotX` service. The OAuth connection returns `valuesRedacted: true`, so I cannot distinguish an empty string from a correct one. |
| NATIVE ↔ BACKEND MATCH | **PASS** | The backend supplies the id as `apple_pay_merchant_id`; the client compares it against its own constant and refuses with `merchant_id_mismatch` rather than presenting a sheet it cannot honour. |
| STRIPE APPLE PAY PROCESSING CONFIG | **OWNER VERIFICATION REQUIRED** | Lives in the Stripe Dashboard and Apple Developer portal. Not representable in the repository. No certificate or private key was read, requested, or printed. |

One structural note worth keeping: an **empty** merchant id is handled
correctly. The backend returns it as-is and the client simply omits Apple Pay
from `initPaymentSheet`, leaving the card form working. An empty value therefore
produces no error — just a silently missing Apple Pay button. That is the most
likely failure mode if item 2 above turns out to be unset.

Also confirmed absent from production: any `MARKETPLACE_CARD_PAYMENTS_ENABLED`
variable. The pause has no configuration surface at all today.

---

## Stage C — Apple documentation

**APPLE DOCUMENTATION CONSULTED: NO.**
**PRIMARY PAGE ATTEMPTED:** `developer.apple.com/documentation/passkit/offering-apple-pay-in-your-app`
**ADDITIONAL APPLE PAGES CONSULTED:** none.

Five routes were attempted across this and prior sessions. Automated fetch
returned only Apple's JavaScript shell. The Chrome browser tools refused both
`developer.apple.com` and `docs.developer.apple.com` with "Navigation to this
domain is not allowed." Computer-use access, which would have let me read the
tab you already have open, timed out. Your open tab is not in the browser tool's
tab group and is unreachable without it.

Per your instruction I did not block the mission on this, and per your earlier
instruction I am not paraphrasing Apple's requirements from memory and calling
that documentation-verified. Everything below rests on repository source I read
directly.

The one place this genuinely costs us is Stage D. Apple's availability semantics
(`canMakePayments` versus `canMakePaymentsUsingNetworks`) determine what the
correct pre-flight check should be, and that gap is called out there.

---

## Stage D — does the sheet actually offer Apple Pay?

**NOT VERIFIED. This is the honest answer, and passing `merchantIdentifier` is
not evidence.**

`stripePaymentSheet.ts:105` checks only whether the SDK module resolves:

```ts
export function isPaymentSheetAvailable(): boolean {
  return loadStripe() !== null;
}
```

That answers "is Stripe in this binary." It does not answer "can this device
present Apple Pay" — which depends on the hardware, the signed entitlement
matching a real merchant id, and whether the user has a card in Wallet.
**`isPlatformPaySupported` / `canMakePayments` is never called anywhere in the
codebase.** The checkout screen consequently cannot distinguish "this device has
no cards" from "this build has no SDK," and neither can I from source.

Verifying this requires a physical iPhone plus a local pause bypass. I have not
built the bypass, because the only useful bypass is one that runs on a real
device against a real backend, and that decision is downstream of Stage H. No
fake or hand-drawn Apple Pay button exists in the repo, and none was added —
the button is drawn by Stripe's `PaymentSheet`, which renders Apple's own
`PKPaymentButton`.

---

## Stage E — real money safety audit

Sixteen of eighteen invariants hold. One fails. One is partial.

| # | Invariant | Result | Evidence |
|---|---|---|---|
| 1 | Server-side pricing authority | PASS | `marketplace_quote_service.create_quote` recomputes every total server-side; client amounts are never trusted. |
| 2 | Currency integrity | PASS | Currency derives from the listing, mixed-currency carts are refused with `MIXED_CURRENCY` (line 661-664), stale snapshots are refused with `PRICE_CHANGED`. |
| 3 | Inventory validation | PASS | Reserved before Stripe, conditional `WHERE quantity>=?` with sold-out rollback (788-806). |
| 4 | Physical-vs-digital eligibility | PASS | Line 645: an iOS client cannot check out a digital line through Stripe at all. Structural, not conventional. |
| 5 | Seller ownership | PASS | `seller_user_id` filters the buyer's own cart lines, so a forged id yields 404. `approved_marketplace_seller_for_user` gates. Buying your own listing is blocked (`OWN_LISTING`). |
| 6 | Buyer ownership | PASS | `buyer_id` comes from the session, never the payload. Idempotency lookup is scoped `WHERE user_id=?`. Cart-line deletion on settlement is scoped `AND user_id=?`. |
| 7 | Idempotency | PASS | Two layers — `marketplace_cart_checkout_keys UNIQUE(user_id, idempotency_key)` and Stripe's own `idempotency_key=marketplace-cart-sheet:{buyer}:{key}`. The replay check also compares payment mode, so a cash replay cannot satisfy a card request. |
| 8 | Webhook signature validation | PASS | Multi-secret verification with distinct status codes per failure mode; unsigned payloads are refused. |
| 9 | Webhook replay protection | PASS | Two independent guards: `record_webhook_event` duplicate detection and `stripe_event_processed(event_id)`. |
| 10 | Canonical paid-state settlement | PASS | One table, `seller_transactions`. No Apple-Pay-specific table exists or is needed. |
| 11 | No client-side paid claim | PASS | A `completed` sheet moves the UI to `processing`, never paid; the screen polls until the webhook settles. |
| 12 | Refund / cancellation | **PARTIAL** | See below. |
| 13 | Failed / declined handling | PASS | `payment_intent.payment_failed` releases the reservation and sets `status='failed'` guarded by `WHERE status NOT IN ('paid','refunded')`. |
| 14 | Abandoned PaymentSheet | **FAIL** | See below. |
| 15 | Duplicate tap protection | PASS | Both idempotency layers plus `ON CONFLICT(seller_transaction_id) DO NOTHING` on the reservation. |
| 16 | Order/payment reconciliation | PASS | Settlement is guarded so a replayed or late success cannot walk a refunded order back to paid; `pulse_finalize_marketplace_settlement` upserts on `seller_transaction_id`. |
| 17 | Seller transaction accuracy | PASS | Per-line quotes; fee, buyer total and seller net are all computed server-side and summed. Cash lanes carry a $0 fee by policy. |
| 18 | Production Stripe environment | PASS (config present) | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` all present in production. Live-vs-test mode is derived from the key prefix (`payment_provider.py:42`). Values redacted, so live-mode itself is unverified from here. |

### Invariant 14 — the failure

`services/marketplace_cart_routes.py:781-783` promises:

```python
# Reserve physical inventory before handing the buyer to Stripe. The
# reservation is keyed to the transaction, so duplicate taps cannot
# decrement twice; expiry/failure restores it.
```

**There is no expiry.** `marketplace_inventory_reservations` has no `expires_at`
column and no sweeper job anywhere in the codebase. `release_inventory_reservation`
is called from exactly three places: the sold-out rollback and the Stripe-error
path, both inside the same request, and the `payment_intent.payment_failed`
webhook.

A buyer who opens the Apple Pay sheet and dismisses it triggers none of those.
Stripe fires no webhook for a dismissal. The PaymentIntent sits in
`requires_payment_method`, the `seller_transactions` row stays `created`, and the
reservation stays `held` — permanently. The listing quantity was already
decremented. There is also no `payment_intent.canceled` handler, so even an
explicitly cancelled intent would not restore stock.

The practical effect once the pause lifts: every abandoned checkout silently
destroys inventory. A seller with one item can be taken to zero stock by a
single buyer who opens the sheet and changes their mind, and nothing ever
restores it. Given that Apple Pay's whole value is a one-tap sheet that is
trivially easy to dismiss, this defect is *more* likely to bite on the Apple Pay
path than on the card path.

Note this affects the cash lane too, which is live today — cash orders reserve
through the same loop. Out of Apple Pay scope, but worth your attention.

### Invariant 12 — the partial

There is a complete returns workflow (`marketplace_returns_routes.py`) with
states, dispute escalation, and a `resolved_refund` decision, and `charge.refunded`
correctly reconciles the local transaction back to `refunded`. But
`return_resolve` deliberately records the decision without moving money:

```python
# Money movement (the actual Stripe refund) is an admin/payments
# concern recorded on the transaction; this records the decision.
```

A governed, idempotent refund primitive does exist —
`services/business_os/marketplace/refunds.py:92` — but it operates on the
BusinessOS `orders` table, not on `seller_transactions`, and is gated behind a
rollout flag that is off unless explicitly enabled. So for the lane Apple Pay
actually uses, **refund execution is manual through the Stripe Dashboard**, with
the webhook reconciling state afterwards. That works and is safe. It is not
automated, and you should know that before enabling real charges.

---

## Stage F — controlled acceptance testing

**NOT RUN.** All ten cases require either a pause bypass or a physical device,
both of which are downstream of the Stage H decision.

What did run, and passed, is the static and contract layer — **175 tests, zero
genuine failures:**

- pause behaviour + native payment sheet contract — **19 passed**
- webhook verification, Connect onboarding, minimum charge, Stripe response — **64 passed**
- buy-now contract, cart lifecycle, fulfillment, listing lifecycle, store identity — **92 passed**

Two environment caveats, both artifacts of my sandbox rather than defects in
your code. One earlier failure was caused by my own dependency drift — a bare
install pulled `stripe` 15.6.0 against your pinned 15.1.0, and a version canary
correctly fired; pinning to 15.1.0 gave 64/64. And four test files
(`test_marketplace_listing_types.py`, `tests/marketplace/*`) cannot run here at
all: they import `bot`, which imports `services/feature_flag_engine.py:10`'s
`from datetime import UTC, datetime`. `datetime.UTC` requires Python 3.11; this
sandbox has 3.10. Your production runtime is `python311` per `nixpacks.toml`, so
this is my environment, not your code. **I have not verified those four files.**

---

## Stage G — physical device

**NOT RUN — and by your rule, not mine to run.** Any real Apple Pay
authorization must be performed by you on your own device with your own card. I
do not initiate or approve real charges.

---

## Stage H — production pause decision

### RECOMMENDATION: **NO-GO**

I have not touched `marketplace_card_payments_paused()`. It still returns `True`.

Three independent reasons, in order of how much they should weigh:

1. **Invariant 14 fails.** Stage E's own rule is explicit: a failing critical
   invariant means payments stay paused. Enabling card checkout today ships a
   silent inventory-destruction bug straight into the path Apple Pay makes
   easiest to trigger.
2. **The Stage A root cause is UNKNOWN.** My one candidate explanation was
   retracted (see Stage A). What remains verified is narrower: seller onboarding
   works via Connect Express, but nothing in production calls
   `reconcile_onboarding()`, so a settlement that lands in `pending_onboarding`
   is not automatically promoted when the seller later completes onboarding.
   That is a payout-reconciliation gap, not proof of why the pause was applied.
   `STRIPE_CONNECT_CLIENT_ID` is irrelevant here and setting it changes nothing.
3. **Stage B items 2 and 4 are unverified**, and both are owner-only.

### When you do lift it, lift it as a flag

Your instruction was to prefer configuration over another hard-coded boolean,
and I agree. The shape I would suggest:

```python
def marketplace_card_payments_paused() -> bool:
    raw = (os.environ.get("MARKETPLACE_CARD_PAYMENTS_ENABLED") or "").strip().lower()
    return raw not in {"1", "true", "on", "yes", "enabled"}
```

Default OFF when unset, so the safe state survives a config wipe, a new
environment, or a forgotten variable. This mirrors `business_os.is_enabled()`,
which is already the established pattern in this codebase — worth matching
rather than inventing a second convention. It is a named, greppable, single
owner-controlled variable, not a hidden kill switch: one place in code, one place
in Railway, and the existing "Temporarily Unavailable" badge continues to tell
buyers the truth whenever it is off.

**I have not written this change.** You asked for the recommendation first.

---

## Stage I — App Review instructions

**CANNOT BE WRITTEN YET**, and writing them anyway would be the actual risk here.

While `marketplace_card_payments_paused()` returns `True`, a reviewer following
any Apple Pay instructions reaches a disabled "Card / Stripe" row with a
*Temporarily Unavailable* badge (`MarketplaceCheckoutScreen.tsx:570-579`).
Submitting instructions that claim a working Apple Pay flow would put a reviewer
on a path that visibly dead-ends — a worse outcome than not mentioning Apple Pay
at all.

Per your rule: I will not claim Apple Pay exists in App Review material while
production payments remain paused. Once Stage H flips to GO and Stage G passes
on a real device, these instructions are a short and easy deliverable.

---

## FINAL REPORT

```
APPLE PAY IMPLEMENTATION EXISTS ............................ PASS
SECOND IMPLEMENTATION CREATED .............................. NO
PAUSE ROOT CAUSE IDENTIFIED ................................ FAIL (candidate retracted — UNKNOWN)
MERCHANT ID DECLARED CONSISTENTLY .......................... PASS
RAILWAY MERCHANT ID VALUE NON-EMPTY ........................ OWNER VERIFICATION REQUIRED
NATIVE ↔ BACKEND MERCHANT MATCH ............................ PASS
STRIPE APPLE PAY PROCESSING CERTIFICATE .................... OWNER VERIFICATION REQUIRED
APPLE DOCUMENTATION CONSULTED .............................. NO (all five access routes blocked)
APPLE PAY BUTTON CONFIRMED ON DEVICE ....................... NOT VERIFIED
DEVICE AVAILABILITY CHECK IMPLEMENTED ...................... FAIL (isPlatformPaySupported never called)
PAYMENT SAFETY INVARIANTS (18) ............................. 16 PASS / 1 PARTIAL / 1 FAIL
  └─ #14 abandoned sheet leaks inventory ................... FAIL
  └─ #12 refund execution is manual ........................ PARTIAL
CONTRACT + STATIC TEST SUITE ............................... PASS (175 tests, 0 genuine failures)
CONTROLLED ACCEPTANCE (10 cases) ........................... NOT RUN
PHYSICAL DEVICE AUTHORIZATION .............................. NOT RUN (owner-only)
PAUSE CHANGED BY CLAUDE .................................... NO
PRODUCTION PAUSE RECOMMENDATION ............................ NO-GO
APP REVIEW INSTRUCTIONS .................................... WITHHELD (payments paused)

FINAL VERDICT: PARTIAL
```

---

## What I need from you

**Owner-only, blocking:**

1. Confirm `merchant.com.pulsesoc.app` exists under Apple Developer →
   Identifiers → Merchant IDs, and that Apple Pay capability is enabled on App
   ID `com.pulsesoc.app`.
2. Confirm the Apple Pay Payment Processing Certificate is registered via
   Stripe Dashboard → Settings → Payments → Apple Pay. Do not send me the
   certificate or its private key.
3. Confirm production `APPLE_PAY_MERCHANT_ID` is exactly
   `merchant.com.pulsesoc.app` and not empty.
4. Tell me the real reason for the August 26 pause. I could not determine it,
   and my one candidate turned out to be wrong.
5. ~~Decide whether `STRIPE_CONNECT_CLIENT_ID` should be configured.~~
   **Withdrawn.** The variable is not used by the Express flow this codebase
   runs; configuring it would change nothing. Do not spend time on it.

**Work I can do next, on your word:**

6. Fix invariant 14 — add reservation expiry plus a sweeper, and handle
   `payment_intent.canceled`. This is the one real code defect found, and it is
   small.
7. Add `isPlatformPaySupported` so the checkout screen can tell "no cards in
   Wallet" from "no SDK in build."
8. Convert the pause to `MARKETPLACE_CARD_PAYMENTS_ENABLED` with a safe-off
   default, once you say GO.

Items 6 and 7 are hours of work and would move the verdict from PARTIAL toward
READY without touching a single line of the Apple Pay integration itself —
which, to restate the headline, does not need rebuilding.
