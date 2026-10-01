# MONEY PATH — End-to-End Payments Certification

**Verdict: NO-GO.** PulseSoc must not be opened to real customer payments in its current
state. The blocking evidence is not a code smell — it is $0.50 of real customer money,
captured in live mode on 2026-08-23, which the platform still has no record of receiving.

Measured 2026-09-29 against: the repository at `main`, the live Stripe account
`acct_1TTVo7FP8qvvGWBI`, the production Railway environment, and production Postgres. Every
number below was read, not inferred. All external probes were read-only GETs; no charge,
refund, transfer, or payout was created, and neither card-payment pause switch was touched.

---

## 0. The correction that reframes this whole audit

The prior understanding of this system — recorded in project notes and echoed in several
`*_REPORT.md` files — was that **production runs Stripe in test mode and has never taken a
real payment**. That is false, and it is the reason a real financial defect has sat
unresolved for 37 days without anyone treating it as urgent.

| Claim believed going in | What the live account actually says |
| --- | --- |
| Stripe is in test mode | `STRIPE_SECRET_KEY` is `sk_live_`; platform account `charges_enabled=True`, `payouts_enabled=True`, `card_payments`/`transfers` both `active` |
| $0 lifetime revenue | 151 lifetime charges; **29 succeeded and paid, $420.22 total** |
| No marketplace purchase has ever happened | **One has.** `ch_3U7QYgFP8qvvGWBI09HrxN7r`, $0.50, 2026-08-23, buyer 15, listing 13, seller 1 |
| Money tables empty ⇒ nothing charged | Money tables are empty **because a paid purchase failed to produce an order** |

`marketplace_orders` having 0 rows was read as "no purchases". It actually means no purchase
has ever produced an order — including the one that was paid for.

### 0.1 A second correction: Marketplace card payments are switched ON in production

An earlier pass of this audit reported Marketplace card checkout as hard-paused in code,
on the strength of `services/marketplace_payment_pause.py` returning a literal `True`. That
reading was taken from a **stale local checkout**, and it is wrong about production.

On `origin/main` the same function is an environment switch, not a constant:

```python
def marketplace_card_payments_paused() -> bool:
    return not marketplace_card_payments_enabled()   # MARKETPLACE_CARD_PAYMENTS_ENABLED, fails closed
```

Production has `MARKETPLACE_CARD_PAYMENTS_ENABLED=true`, alongside a `sk_live_` key. Verified
functionally rather than from the variable alone, against the unauthenticated availability
route, which is the same answer the checkout form is built from:

```
GET https://pulsesoc.com/api/pulse/marketplace/cart/checkout-options            → card_payments_available: true
GET https://pulsesoc.com/api/pulse/marketplace/cart/checkout-options?seller_id=1 → card_payments_available: true
```

Seller 1 owns every eligible listing in production, so the per-seller Connect gate — a
separate authority from the platform switch — is also open. **Real buyers can start real card
checkouts right now.** Every defect below that concerns money being taken without being
recorded is therefore live-exposed, not hypothetical.

### 0.2 Live reconciliation against Stripe (§85–86), executed read-only

Every unsettled Marketplace transaction holding a PaymentIntent id was fetched from the live
Stripe account and compared against the local row. 14 candidates, and the result bounds the
damage precisely:

| Stripe status | Count | Meaning |
| --- | --- | --- |
| `requires_payment_method` | 13 | Genuinely never paid. Abandoned sheets, correctly unsettled. |
| `succeeded` | **1** | Real money taken, never recorded. `tx=27`, `pi_3U7QYgFP8qvvGWBI0MGB8bL0`, $0.50, `amount_received=50`. |

So D-1 has **exactly one** financial casualty, not an unknown number — including the three
recent $59.75 / $59.75 / $40.92 attempts of 2026-09-20, all of which are genuinely unpaid.
`transfer_group` on the paid intent is `None`, which is D-2 observed on the one charge that
matters.

---

## 1. §121 Financial architecture, as built

**Charge model: separate charges and transfers.** The platform charges the buyer onto its own
account, then pays the seller later with an explicit `Transfer` correlated by
`transfer_group`. This is a deliberate, defensible choice and is *not* destination charges:
`application_fee_amount` is unused at every charge site, and no charge sets `transfer_data`
or `on_behalf_of`.

The consequence is that **the platform is the merchant of record and holds seller funds in
its own balance until it chooses to move them.** That makes the transfer leg a financial
obligation, not an implementation detail — and it is the leg that has never run.

**Connect:** Express accounts via `Account.create` + `AccountLink.create`, capabilities
`card_payments` and `transfers`. No payout schedule is specified, so connected accounts
inherit Stripe's default. Three connected accounts exist; two are fully enabled.

**Pricing authority:** `services/marketplace_web.derive_price` (variants first, seller
`price_label` as fallback) is what every buyer-facing surface renders. As of this mission it
is also what every checkout lane charges — see §4.

**Quote:** `services/marketplace_quote_service.create_quote` snapshots a
`services/business_os/marketplace/policy.MarketplaceQuote` into
`seller_transactions.metadata_json` with a 15-minute expiry. The snapshot is genuinely
immutable and genuinely complete — the one live purchase has a full, readable quote in it.

**Fee:** `policy.platform_fee_bps()` returns `PROPOSED_PLATFORM_FEE_BPS` (500 bps = 5%) only
when all three of `MARKETPLACE_STANDARD_V1_OWNER_APPROVED`,
`MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` and `MARKETPLACE_STANDARD_V1_EFFECTIVE_AT`
are set, and 0 otherwise. **None of the three is set in production**, so the canonical rate
is 0%. No percentage is invented anywhere in this report; see D-4 for the fee the legacy lane
actually recorded, which is a different number.

---

## 2. §120 Payment path matrix

| # | Path | Status | Stripe flow | Seller requirement | Order creation | Webhook | Test result | Issues | Fixed? |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Marketplace Buy Now — `POST /api/pulse/payments/checkout` | LIVE (cash lane); card **hard-paused** | PaymentIntent (native sheet) | `marketplace_sellers.status='approved'` | `pulse_upsert_marketplace_order` on `payment_intent.succeeded` | `payment_intent.succeeded` | Priced correctly, 10/10 tests green | D-1 (webhook never landed in prod), D-2 (no `transfer_group`), **D-3 priced from `price_label`** | D-3 **FIXED**; D-1/D-2 reported only |
| 2 | Marketplace cart — `services/marketplace_cart_routes.py` | LIVE (cash); card paused | PaymentIntent, multi-line | approved seller per line | same handler, per `seller_transaction_id` | `payment_intent.succeeded` | Priced correctly; stale-price re-confirm verified | D-3 | **FIXED** |
| 3 | Accepted offer — `services/marketplace_offers_routes.py` | LIVE | PaymentIntent | approved seller | singular-tx branch | `payment_intent.succeeded` | Correct by design | none — an accepted offer *is* an authoritative negotiated price | n/a (deliberately untouched) |
| 4 | Creator/product — `POST /api/payments/checkout/product/<id>` | LIVE, **orphaned** (no client calls it) | Checkout Session | none checked | **none** | `checkout.session.completed` | — | **D-5: bypassed the marketplace card pause entirely**; no quote, no inventory hold, no goods policy, no `is_public`, no fulfillment gate | pause + price authority **FIXED**; rest reported |
| 5 | Premium subscription | LIVE and **proven working** | Checkout Session + Invoices | n/a | n/a | `checkout.session.completed`, `invoice.*`, `customer.subscription.*` | 28 real paid charges, 13 refunded | none found | n/a |
| 6 | Ads wallet funding | LIVE | PaymentIntent (`purpose=pulse_ad_wallet_funding`) | n/a | wallet credit | `payment_intent.succeeded` | untested — see D-1, that branch has never fired in prod | shares D-1's blast radius | no |
| 7 | PulseDrop / feed commerce CTA | LIVE (routes to path 1) | n/a (discovery only) | approved seller | n/a | n/a | 36 tests green | **CTA suppressed on 82 of 123 live listings** | **FIXED** |
| 8 | Seller storefront / web→app deep link | LIVE (routes to path 1) | n/a | approved seller | n/a | n/a | not device-tested | `pulsesoc://payments/return` has no handler in `linking.ts` | no |
| 9 | Guest checkout | **DOES NOT EXIST** | — | — | — | — | n/a | §20-22 unimplemented | no |
| 10 | Apple Pay | **DELIBERATELY DISABLED** | PaymentSheet config exists client-side | — | — | — | n/a | entitlement `com.apple.developer.in-app-payments` removed 2026-09-02 for App Review 2.1; `_apple_pay_merchant_id()` returns `""` on purpose | n/a — correct as-is |

Per §26: Apple Pay is **not** functional. The client code in
`mobile-native/src/api/stripePaymentSheet.ts` is real, but the server refuses to announce a
merchant id and the signed binary carries no Apple Pay entitlement. This is a coherent,
intentional disable with a documented reason, not a gap to be papered over.

---

## 3. §122 Defect report

### D-1 — A paid customer never got an order, and the seller was never credited

- **Severity:** P0 / BLOCKER. Real money, real customer, unresolved 37 days.
- **Path:** Marketplace Buy Now (path 1).
- **Evidence:**
  - Stripe: `pi_3U7QYgFP8qvvGWBI0MGB8bL0` — `status=succeeded`, `amount_received=50`,
    `livemode=True`, charge `ch_3U7QYgFP8qvvGWBI09HrxN7r` paid, never refunded.
  - Postgres: `seller_transactions.id=27` — `status='checkout_created'`,
    `payout_state='pending_checkout'`, and `updated_at` equal to `created_at` to the second
    (`2026-08-23T01:50:50`). **The row was never touched again.**
  - `marketplace_orders`: **0 rows.** `seller_payouts`: **0 rows.**
  - `stripe_events`: **not one `payment_intent.succeeded` row has ever been written**, and
    nothing in the table references that PaymentIntent.
- **Root cause:** the `payment_intent.succeeded` event was never received or recorded. The
  handler at `bot.py:110085` is correct for this metadata shape and would have done the right
  thing. The proof that this is a delivery/recording gap and not a handler bug is the
  contrast with `seller_transactions.id=16`: the *failure* event for the sibling lane
  (`payment_intent.payment_failed`, `stripe_events.id=22`) **was** received and processed, and
  that row was correctly advanced to `failed` with the Stripe event id recorded. The success
  path is the one that has never once fired in production.
- **Why it is unrecoverable automatically:** Stripe's event retention window now begins
  2026-09-07, so the 2026-08-23 event **cannot be replayed from Stripe**. And there is no
  cross-object reconciliation job anywhere in the repo, so nothing will ever notice.
- **Financial risk:** every successful payment is silently dropped. The buyer is charged, no
  order exists, inventory is never captured, the seller is never credited, and the funds are
  swept to the platform's own bank on the normal payout schedule — which is exactly what
  happened on 2026-09-21 (`po_1UHvGUFP8qvvGWBI2x6AAYnT`, $201.42, automatic; balance now $0).
- **Customer impact:** paid for listing 13 ("T4"), received no order. The listing is now
  `seller_deleted` with `quantity=0`, so there is no longer anything to fulfil.
- **Seller impact:** owed `seller_net_cents=45`. Never paid.
- **Fix:** NOT APPLIED — deliberately. Resolving it means either refunding the buyer or
  transferring to the seller. Both move real money and are outside what I will do
  autonomously. Two things are needed and both need your decision:
  1. **Settle the outstanding $0.50** (refund buyer 15, or transfer $0.45 to seller 1 and
     record the order). Recommend refund: the listing is deleted, so nothing can be fulfilled.
     Still open — it moves real money.
  2. **Build the reconciliation job** (§85-86) — **NOW BUILT**, see below. Without it the next
     dropped webhook was equally invisible; it is no longer.
- **Detection gap now closed:** `bot.pulse_reconcile_missed_marketplace_payments` asks Stripe
  for the true status of every unsettled Marketplace transaction, files a `critical`
  `MISSING_WEBHOOK_EVENT` incident for any `succeeded` intent whose row is not `paid`, and can
  complete the interrupted settlement when an operator passes `dry_run=False`. It reports by
  default, following the rule `services/business_os/payments/reconciliation.py` sets out for
  every check in it — *detect and report, never repair*.
  - It is **not a second payment engine** (§19). The webhook's settlement body was lifted into
    `pulse_settle_marketplace_payment_intent`, and both the webhook and the sweep now call that
    one function. `marketplace_orders` keeps its single writer, as asserted by
    `tests/dropshipping/test_supplier_obligations.py`.
  - Not wired to any worker, route or timer. Running it is a deliberate operator action.
  - 10 tests in `tests/marketplace/test_missed_payment_reconciliation.py`, each asserting a
    stored outcome rather than the summary the sweep returns about itself. All 10 were
    mutation-verified: suppressing the order row, the incident, the status transition, or the
    ledger schema each fails the test that covers it.
- **Tests:** none possible without a live charge. This defect is *only* observable against
  production, which is precisely why it survived a green test suite.

### D-2 — Marketplace charges carry no `transfer_group`, so the seller cannot be paid

- **Severity:** P0 / BLOCKER.
- **Evidence:** `pi_3U7QYgFP8qvvGWBI0MGB8bL0` has `transfer_group=None`, `transfer_data=None`,
  `on_behalf_of=None`, `application_fee_amount=None`. **Zero `Transfer` objects exist in the
  account, ever.** `transfers` capability is `active`, so nothing is stopping them.
- **Root cause:** the separate-charges-and-transfers model correlates the payout leg to the
  charge leg by `transfer_group`. The code sets one at three charge sites and
  `pulse_finalize_marketplace_settlement` consumes it — but it runs *inside* the
  `payment_intent.succeeded` branch, so D-1 means it has never executed. The live charge was
  created without a group at all.
- **Financial risk:** the seller-payment half of the marketplace has never run in production
  and cannot be assumed to work. Treat it as unexercised code on the money path.
- **Fix:** NOT APPLIED. This needs a live test transaction to verify end to end, which per
  §117 I will not create autonomously.

### D-3 — Checkout charged from `price_label` while every screen showed the variant price — **FIXED**

- **Severity:** P0. Buyer-visible price disagreed with the charged amount.
- **Measured over 123 production listings (117 with live variants):**
  - **82 rows** have an empty `price_label` while their variants carry a real price. The card
    showed $117.75 (listing 45), $68.53 (56), $56.67 (44); checkout parsed `""` to zero and
    answered "This item is currently free or not priced for checkout." **82 priced, published,
    approved products could not be bought at all.**
  - **3 rows** parsed a label that disagreed with the displayed amount: listing 35 charged
    $35.00 against a $14.33 shelf, 36 charged $38.00 against $2.29, 112 charged $29.31 against
    $27.84.
  - **17 rows** have variants spanning more than one price (widest $6.25..$49.40) and the
    request body had no `variant_id` field at all — so picking Large could not move the
    charged amount **by construction** (§12).
  - The two answers therefore disagreed on **85 of 123 listings**.
- **Fix:** new `services/marketplace_price_authority.py` — one authority, delegating to the
  same `derive_price` every buyer surface uses, that resolves to a single unit price or
  refuses. Two distinct refusals because they are different facts with different fixes:
  `NO_AUTHORITATIVE_PRICE` (nothing prices it) and `VARIANT_SELECTION_REQUIRED` (variants
  price it at more than one price and no variant was named — charging the low bound
  undercharges the seller, the high bound overcharges the buyer, and the buyer's screen showed
  a range, so no amount here is one both parties agreed to). Wired into all four lanes: buy
  now (`bot.py`), cart add/serialize/confirm (`marketplace_cart_routes.py`), the orphaned
  creator lane, and the PulseDrop CTA gate. `variant_id` now flows from the client as
  *identity*, never as money (§10), into the quote snapshot.
- **Ceiling preserved:** `derive_price` has no maximum clamp while the label parser does, so
  routing checkout onto the variant column would have silently removed a ceiling that a
  downstream `int` column and Stripe's own maximum both depend on. The authority clamps at
  `MAX_UNIT_PRICE_MINOR = 99_999_999` and a test pins it equal to `bot.MAX_PRICE_LABEL_CENTS`.
- **Tests:** `tests/test_money_path_price_authority.py`, 10 tests, all green, registered in
  `config/ci_test_manifest.json`. **Mutation-verified:** reverting the authority to label-only
  fails 7 of the 10. The tests post real requests to the real route and assert the amount
  actually charged and recorded — a test that asserted `resolve_unit_price` in isolation would
  have stayed green through the entire defect, because the authority was never the broken
  part; the checkout lane simply did not ask it.

### D-4 — Three fee authorities disagree, and production recorded the highest

- **Severity:** P1.
- **Evidence:** the one live purchase's quote snapshot records `platform_fee_bps: 1000` (10%)
  and `platform_fee_minor: 5`. Meanwhile `policy.PROPOSED_PLATFORM_FEE_BPS` is 500 (5%) and
  `policy.platform_fee_bps()` returns **0** because none of the three owner gates is set in
  production. `bot.py:117973` seeds a third value from
  `PLATFORM_FEE_MERCHANT_PERCENT` (default `"10"`).
- **Risk:** the commission a seller is shown, the commission recorded on their transaction,
  and the commission the canonical policy would charge are three different numbers. §42 —
  no percentage is invented here; all three are quoted from source.
- **Fix:** NOT APPLIED. Which rate is correct is an owner decision, not an engineering one.

### D-5 — An orphaned checkout route bypassed the marketplace card pause — **FIXED**

- **Severity:** P1.
- **Evidence:** `_creator_checkout_for_item` (`bot.py:~96255`) serves
  `POST /api/payments/checkout/{product,course,live-class,premium}/...`. No client references
  it — only `scripts/creator_economy_audit.py` lists the paths — but it is registered and
  session-authenticated, and for `item_type == "product"` it could start a Stripe Checkout
  Session for a marketplace listing **while the marketplace card pause was on**, with no
  quote, no inventory reservation, no goods policy and no fulfillment gate.
- **Fix:** added the pause check and the price authority. This *enforces* an existing safety
  control rather than bypassing one, so it is within §119. The remaining gaps (no
  `is_public`, no quote, no inventory hold, no fulfillment gate) are reported, not fixed:
  adding `is_public()` here would return False for every row, because the route's plain
  `SELECT *` carries no `seller_status`, and would kill the lane outright.

### D-6 — PulseDrop suppressed the buy CTA on 82 live listings — **FIXED**

- **Severity:** P2 (discovery, not money).
- **Evidence:** `services/pulsedrop/hydration._price_minor` decided `NOT_PRICED` from
  `price_label` alone, so the 82 variant-priced listings rendered with no CTA. After D-3 it
  would have become the strictest surface in the product.
- **Fix:** routed through the same authority, with the variant read batched
  (`_variants(cur, listing_ids)` — one statement per page, not per row, which matters against
  a pool of 8 with a 3-second checkout timeout). A range keeps its CTA using the lower bound,
  since the card's job is to get the buyer to the picker.
- **Tests:** `tests/pulsedrop/test_hydration.py` — 36 green. Added a behavioural test that a
  label-less variant-priced listing gets a live CTA *through the real read*, because an
  `overlay()`-level test with hand-built variants would have stayed green while every card in
  the feed said "not priced yet". Mutation-verified: stubbing the variant read to `{}` fails
  it. The suite's "one statement per page" assertion was updated to the property it actually
  means — a bounded count per table, never one per row — and pinned by counting a page twice
  the size.

### D-7 — Webhook subscription and handler branches do not match

- **Severity:** P1.
- **Live endpoint config (read from the Stripe API):**
  - `https://pulsesoc.com/stripe-webhook` — **enabled**, 30 events, api_version
    `2026-04-22.dahlia`
  - `https://pulsesoc.com/stripe-webhook` — disabled, 24 events (duplicate)
  - `https://pulsesoc.com/api/stripe/webhook` — disabled, 6 events
  - `https://coinpilotx.app/stripe/webhook` — disabled, 3 events (old product domain)
- **No Connect webhook endpoint exists** — every endpoint has `application=None`. So
  connected-account events never arrive, and the `account.updated` handler at `bot.py:110407`
  can only ever see the *platform's* own account. **Seller onboarding completion is never
  observed by webhook** (§113).
- **Dead handler branches** — subscribed nowhere, so they cannot fire:
  `payment_intent.canceled` (`bot.py:110365`), `radar.early_fraud_warning.created`
  (`bot.py:110539`).
- **Unhandled delivered events** — Stripe sends them, no branch exists: `capability.updated`,
  `charge.dispute.funds_withdrawn`, `charge.dispute.funds_reinstated`, `charge.refund.updated`.
- **Never handled anywhere:** `payment_intent.processing`, `payment_intent.requires_action`,
  `payment_intent.amount_capturable_updated`. ACH and 3DS payments would sit in limbo.
- **Fix:** NOT APPLIED. Changing live webhook subscriptions is a production configuration
  change on a shared system and needs your sign-off.

### D-8 — No Stripe API version is pinned in code

- **Severity:** P1. §114.
- **Evidence:** `grep -rn "api_version" services/ bot.py` returns **nothing**. The webhook
  endpoints deliver `2026-04-22.dahlia`; the library sends whatever its default is.
- **Risk:** a Stripe library upgrade can silently change request and response shapes on the
  money path, and inbound payload shape is already decoupled from outbound.

### D-9 — Sales tax is structurally absent

- **Severity:** BLOCKER for physical goods. §63.
- **Evidence:** no `automatic_tax`, no `tax_behavior`, no `tax_rates` anywhere in `services/`
  or `bot.py`. `MarketplaceQuote` has a `tax_cents` field and `create_quote` accepts
  `tax_minor`, but **no call site anywhere passes it** — every quote is created with tax = 0.
  The one live purchase's snapshot says it in as many words:
  `"tax_snapshot": {"amount_minor": 0, "source": "not_calculated"}`.
- **Fix:** NOT APPLIED. Nothing is invented. Reported as a release blocker exactly as §63
  requires.

### D-10 — The settlement path posts into a ledger it never creates — **FIXED**

- **Severity:** was P0 for any fresh database; **not production-exposed.**
- **Evidence:** `marketplace_settlement_service.settle_paid_transaction` calls
  `ledger.post_entry`, and `post_entry` creates no tables. The only callers of
  `ledger.ensure_schema()` are seller payouts, reconciliation and the Stripe ledger handler —
  none at boot, none on the webhook settlement path. Found because two of the new
  reconciliation tests failed with `no such table: ledger_transactions` on a clean database,
  *after* the transaction had been marked paid and the order row written.
- **Blast radius if it had fired:** buyer charged, order recorded, seller never credited — the
  same end state as D-1, reached by a different route.
- **Why production is safe:** the three `ledger_*` tables **do exist** in production (0 rows
  each), created by one of the other callers at some point. So the first real settlement will
  not hit it. This was verified directly rather than assumed, and it downgrades the defect from
  "the next sale breaks" to "any fresh deployment breaks".
- **Fix:** APPLIED. `ensure_schema` now creates the ledger it posts into, and only when it owns
  its connection — doing it for a caller-supplied connection could block the ledger's DDL
  behind that caller's open transaction on Postgres.

---

## 4. §123 Test report

| Suite | Result |
| --- | --- |
| `tests/test_money_path_price_authority.py` (new, 10 tests) | **10 passed** |
| Mutation check: authority reverted to label-only | **7 of 10 fail** — guard proven real |
| `tests/pulsedrop/test_hydration.py` | **36 passed** (was 35; +1 behavioural) |
| Mutation check: variant read stubbed to `{}` | **2 fail** — both new assertions proven real |
| `tests/pulsedrop/test_ops.py` | **47 passed** (after fixing a pre-existing time bomb, below) |
| `tests/pulsedrop/` — curator, profile_payload, read_path, reel_composer | **83 passed** |
| 4 adjacent marketplace suites | **128 passed** |
| `tests/marketplace/test_missed_payment_reconciliation.py` (new, 10 tests) | **10 passed** |
| Mutation checks on the sweep — order row / incident / status transition / ledger schema each suppressed | **each fails the test that covers it** — all 4 guards proven real |
| All of `tests/marketplace/` + `tests/business_os_finance/`, one file per process, on the `origin/main` base | **0 files with failures** |
| `tests/protection/test_every_test_file_is_run_by_ci.py` | **9 passed**; fails with the new file undeclared, so the manifest entry is load-bearing |

Run one file per pytest process, as this repo requires.

### A staleness trap worth recording

This work was begun on a local checkout **22,000 lines of `bot.py` behind `origin/main`**, and
two of the three files it touches had moved there. Verified rather than assumed, and it changed
the result twice:

1. `config/ci_test_manifest.json` in the working tree was **41 declarations shorter** than
   `origin/main`'s. Committing it would have deleted other people's test declarations and turned
   the CI gate red. Restored from `origin/main` with only the one new line added.
2. The webhook settlement body on `origin/main` now calls `emit_marketplace_paid_order_emails`,
   which the stale base did not. A "faithful lift" of the old body would have **silently dropped
   buyer and seller order emails** on every marketplace sale. The lift was redone from
   `origin/main`'s body.

Everything above was therefore re-applied and re-verified in a worktree pinned to
`origin/main`, not on the stale base.

**Pre-existing failure found and fixed, unrelated to payments:**
`test_run_now_makes_the_curator_due_without_publishing_anything` compared `schedule_view`
against a hardcoded `2026-09-27` while `apply_action("run_now")` — which takes no clock —
writes `utcnow()`. It passed until real time crossed that date, then began failing for every
session. Anchored to the real clock, preserving the assertion exactly.

**Not tested, and cannot be without moving real money:**

- Live card E2E on web, iOS Simulator and physical P3R7OR (§72-75). **Not because the path is
  closed — see §0.1, it is open in production.** Walking it to completion means creating a real
  charge against a `sk_live_` key, which §117 forbids me doing autonomously. The pause claim
  that previously sat here was read off a stale checkout and was wrong.
- The `payment_intent.succeeded` branch. It has never executed in production (D-1) and
  exercising it needs a real charge, which §117 forbids me creating autonomously.
- The transfer/payout leg. Zero transfers exist to observe.

---

## 5. §124 Release decision — **NO-GO**

Not a judgement call. Five mandatory gates fail on measured evidence. The reconciliation gate
moved to a qualified pass during this mission; the other five are unchanged.

**And the decision is now uncomfortable rather than academic.** §0.1 establishes that
Marketplace card checkout is *already live in production* on a `sk_live_` key. This is not a
pre-launch audit of a closed path — it is a NO-GO verdict on a rail that is currently open. The
five failing gates below are live exposures. In particular a buyer can be charged sales tax that
is never collected (D-9), at a commission rate three authorities disagree about (D-4), with no
`transfer_group`, so the seller cannot be paid (D-2).

That does not mean the rail should be slammed shut by reflex — 13 of the 14 unsettled
transactions are simply abandoned checkouts, and the detection gap that let D-1 hide for 37 days
is now closed. It means the remaining five gates are the price of leaving it open, and that is
an owner's decision, not mine.

| Gate | Status |
| --- | --- |
| A successful payment produces an order | **FAIL** — D-1, proven against production |
| The seller receives their proceeds | **FAIL** — D-2, zero transfers ever |
| Every succeeded charge is reconciled | **PASS (detection)** — sweep built, tested, mutation-verified; D-1's backlog reconciled against live Stripe and bounded to one $0.50 charge. Still **FAIL on remediation**: that one charge is unsettled, and the sweep is not scheduled |
| Sales tax is collected on physical goods | **FAIL** — D-9, structurally absent |
| One commission rate | **FAIL** — D-4, three disagree; prod recorded 10%, canon says 0% |
| Webhook subscription matches the handlers | **FAIL** — D-7, no Connect endpoint; branches dead both ways |
| Buyer-visible price equals the charged amount | **PASS** — D-3 fixed, mutation-verified |
| Client-supplied amounts are never authoritative | **PASS** — `variant_id` is identity only |
| Integer minor units throughout | **PASS** |
| No raw card data touches PulseSoc | **PASS** — PaymentIntents/Checkout only |
| Quote is immutable and snapshotted | **PASS** — verified on the live purchase |
| Apple Pay is not claimed falsely | **PASS** — disabled coherently and on purpose |

### What to do first, in order

1. **Settle the outstanding $0.50** (`seller_transactions.id=27`, buyer 15, seller 1).
   Recommend refunding buyer 15 — listing 13 is `seller_deleted` with `quantity=0`, so
   nothing can be fulfilled. **This moves real money; it needs you to do it or to tell me to.**
2. **Find out why `payment_intent.succeeded` has never been recorded.** Start with the live
   endpoint's delivery attempts in the Stripe dashboard — the API does not expose them, and
   the 2026-08-23 event is past Stripe's retention window (which now begins 2026-09-07), so
   the dashboard is the only remaining source.
3. ~~Build the reconciliation sweep~~ — **done.** What remains is to *schedule* it. It is
   deliberately wired to nothing, so today it only runs when a human runs it. Decide where it
   belongs (a worker line, an admin route, or a cron) and whether it reports or repairs there.
   Report-only on a timer is the safe first step.
4. **Decide about the live card rail.** `MARKETPLACE_CARD_PAYMENTS_ENABLED=true` in production
   today. The choice is: leave it on and accept D-2/D-4/D-9 as live exposures, or set it to
   `false` and redeploy while they are closed. Clearing the variable is a one-line change with no
   code deploy, and it fails closed — but it is a production configuration change on a revenue
   path, so it is yours to make, not mine. Note a redeploy is required either way: Railway
   variables only reach a container at boot.
5. Then resolve D-4 (pick one commission rate), D-9 (tax), D-7 (webhook config incl. a Connect
   endpoint), D-8 (pin the API version).

### What changed in this mission

Fixed and verified: D-3 (the price authority, across four lanes), D-5 (the orphaned route's
pause bypass), D-6 (the PulseDrop CTA), **D-10 (the settlement path's missing ledger schema)**,
plus a pre-existing unrelated time-bomb test.

Built: the §85-86 reconciliation sweep and its 10 mutation-verified tests, with the webhook's
settlement body extracted so the sweep reuses it instead of duplicating it.

Corrected: the claim that Marketplace card payments are paused. They are live (§0.1). The
earlier reading came from a stale local checkout.

Reported and deliberately not fixed: D-1's outstanding $0.50, D-2, D-4, D-7, D-8, D-9 — each
either moves real money, changes live production configuration, or requires an owner decision
about rates and policy.

**Not deployed.** Everything above is local and uncommitted. The code changes are staged in a
worktree pinned to `origin/main`, because the session's base checkout was 22k lines of `bot.py`
behind it and landing from there would have regressed main (see §4). None of it is live.
