# Stripe Tax Architecture — PulseSoc Marketplace

Status as of 2026-09-19. Live account `acct_1TTVo7FP8qvvGWBI` (COINPLOTXAI INC).

This document records what exists, what is deliberately not built yet, and which
decisions are owner-blocked. It is written so that the next person can tell the
difference between "we chose not to" and "nobody looked."

---

## 1. Current state, in one line

Stripe Tax is **active** on the live account and **collects nothing**, because the
account holds **zero tax registrations**. PulseSoc's checkout does not call Stripe
Tax at all; `tax_minor` is a hard 0 through the entire quote → settlement chain.

Those two facts are consistent, and that consistency is the only reason the
current state is safe rather than broken. See §8.

---

## 2. Stripe-side configuration

### 2.1 Tax settings (live)

| Field | Value |
|---|---|
| `status` | `active` |
| Head office | 88-09 148th Street, 4C, New York, NY 11435, US |
| `defaults.tax_behavior` | `inferred_by_currency` (→ *exclusive* for USD) |
| `defaults.tax_code` | `txcd_99999999` — General / Tangible Goods |
| Registrations | **0** |

`defaults.tax_code` was `txcd_10000000` ("General - Electronically Supplied
Services") until 2026-09-19. That is the wrong classification for a catalog that
is ~60% apparel, and Stripe's own Tax → Locations page was flagging it
("Review your preset tax code"). It was corrected to `txcd_99999999`.

This change cannot alter any buyer's charge, because with zero registrations
every calculation returns 0 regardless of code (§2.2). Its only effect today is
on **nexus monitoring accuracy** (§3), where the old value would have measured
PulseSoc's obligations against the rules for digital services rather than goods.

### 2.2 Why zero registrations is a fail-safe, not a bug

A live calculation probe against a US address returns:

```
tax_amount_exclusive: 0
taxability_reason:    "not_collecting"
```

`not_collecting` means *"no registration in this jurisdiction"*. It is distinct
from `not_subject_to_tax`, which means *"this jurisdiction has no sales tax"*
(e.g. Oregon). Both produce 0. They must not be conflated when reading logs: the
first is a configuration state that will change, the second never will.

The practical consequence is important and slightly counter-intuitive:
**integrating Stripe Tax into checkout today would be correct and inert.** It
would charge $0 everywhere, which is exactly what PulseSoc charges now. The
integration is therefore gated on registration existing (§9), not on code being
written.

---

## 3. Monitoring is not registering

Stripe Tax monitors transaction volume per jurisdiction and raises an alert when
an economic-nexus threshold is crossed. It is enabled. It currently reports
**"No live transactions"** — consistent with the production account having taken
no real payments.

Two limits on this monitoring that must not be assumed away:

1. **Stripe does not monitor the home jurisdiction.** The dashboard states this
   outright: *"we don't track your home jurisdiction."* The head office is New
   York. Therefore **Stripe will never raise a NY obligation alert.** If PulseSoc
   has a New York collection obligation — and a NY-domiciled entity shipping to
   NY buyers very likely does — it must be determined by the owner or their
   accountant. Waiting for a Stripe alert about NY is waiting for something that
   by design cannot arrive.

2. **Monitoring confers no authority to collect.** Crossing a threshold produces
   a notification, not a registration. Collecting tax in a jurisdiction where the
   entity is not registered is a legal exposure in its own right, not a
   conservative default.

---

## 4. Marketplace model and who owes the tax

PulseSoc uses **separate charges and transfers**, and this is the fact that
determines everything downstream. Do not change it as part of a tax change.

```
buyer ──charge──▶ PLATFORM account (acct_1TTVo7FP8qvvGWBI)
                       │   transfer_group = "marketplace_order:{tx_id}"
                       └──Transfer.create()──▶ seller connected account
```

- The charge is created on the platform account (`bot.py` ≈ 94940,
  `stripe.PaymentIntent.create`). No `on_behalf_of`, no `destination` charge.
- The seller is paid later by an explicit `Transfer.create()` against the
  `transfer_group`.

Because the platform is the one charging the buyer, **PulseSoc is the merchant of
record**, and US marketplace-facilitator statutes put the collection and
remittance obligation on PulseSoc — not on individual sellers. Sellers do not
register, do not collect, and must never receive tax money.

A consequence worth stating plainly: the Connect **platform profile** currently
declares the funds flow as *"Sellers will collect payments directly."* That does
not describe the implementation above. It should be reconciled, because Stripe
uses that declaration to reason about facilitator obligations.

---

## 5. Product tax codes

### 5.1 The finding

New York exempts clothing and footwear **under $110 per item** from both the 4%
state tax and NYC's 4.5% local tax. Stripe implements this threshold
automatically — but only if the line item carries a clothing tax code.

Measured in sandbox against 11435 (NYC), registration `taxreg_1UHcZs2VN9kXLx8vqRGWKFsv`:

| Tax code | $38.00 | $150.00 |
|---|---|---|
| `txcd_99999999` General / Tangible Goods | **$3.37** `standard_rated` | $13.31 `standard_rated` |
| `txcd_30011000` Clothing & Footwear | **$0.00** `product_exempt` | $13.31 `standard_rated` |
| `txcd_30011200` Children's clothing/footwear | $0.00 `product_exempt` | — |
| `txcd_30021000` Athletic Activity Clothing | $0.00 `product_exempt` | — |
| `txcd_30060001` Purses and Handbags | $3.37 `standard_rated` | — |
| `txcd_30060007` Jewelry | $3.37 `standard_rated` | — |
| `txcd_30060010` Non-Clothing Accessories | $3.37 `standard_rated` | — |
| `txcd_34020005` Consumer Electronics Accessories | $3.37 `standard_rated` | — |

Two things this proves:

- Sending everything as the generic code **over-collects on every sub-$110
  apparel sale** — the majority of the catalog. Over-collection is not the safe
  side of the error; it is money taken from buyers that the platform has no
  authority to take.
- Accessories are correctly *not* exempt. The exemption covers clothing and
  footwear, not handbags or jewelry. So a blanket "it's all clothing" mapping is
  wrong in the opposite direction.

The $150 row is the control: the same clothing code taxes at the full 8.875%
above the threshold, confirming Stripe is applying the rule rather than the code
simply being untaxed.

### 5.2 Classification key: `product_type`, not `category`

`marketplace_listings.category` is free text sourced from dropshipping feeds and
is **not safe to parse as a taxonomy**. Observed in the 44 live listings:

- Two different separators — `Women's Clothing > Bottoms > Woman Jeans` and
  `Jewelry & Watches / Fashion Jewelry / Rings`.
- A full-width comma: `Toys， Kids & Baby > Boys Clothing > Boy Accessories`.
- Bare single tokens with no hierarchy: `Education`, `Sports`, `Beauty`.
- A misleading path: `Women's Clothing > Accessories > Woman Socks`. Socks are
  clothing in NY. A mapping that keyed on the `Accessories` segment would tax an
  exempt item.

The reliable key is `marketplace_listings.product_type`, which is a closed set:
`physical`, `digital`, `service`, `event`, `booking`. The four `Education`
listings carry four *different* product types — they are not one tax situation.

Proposed mapping, to be applied when §9 unblocks:

| product_type | category signal | Tax code |
|---|---|---|
| physical | Clothing / T-Shirts / Bottoms / Outerwear / Shoes / Slippers / Sandals / Socks | `txcd_30011000` |
| physical | Boys/Girls/Kids clothing | `txcd_30011200` |
| physical | Sportswear / athletic | `txcd_30021000` |
| physical | Bags / Purses | `txcd_30060001` |
| physical | Jewelry & Watches | `txcd_30060007` |
| physical | Phones & Accessories / Cases | `txcd_34020005` |
| physical | Cosmetics / Nail / Beauty | `txcd_32050025` |
| physical | anything else | `txcd_99999999` |
| digital / service / event / booking | — | **unresolved, see §5.3** |

### 5.3 Ambiguous — reported, not guessed

Per the mission rule that ambiguous classification is reported rather than
guessed, these are **not** assigned:

- **`Education` / `product_type=service`** — `txcd_20060052` (Educational
  Services) and `txcd_20060044` (Training) are both plausible and differ by
  state.
- **`Education` / `product_type=digital`** — depends on delivery medium, which
  the schema does not record: `txcd_20060158` (pre-recorded streamed),
  `txcd_20060258` (streamed + downloadable), `txcd_20060358` (written material),
  or `txcd_10305000/1` (digital textbook). These are taxed differently.
- **`Education` / `product_type=event`** — an admission is a different category
  from a course; may be exempt, may be an amusement tax.
- **`Education` / `product_type=booking`** — unclear whether the taxable supply
  is the booking or the thing booked.
- **`Toys， Kids & Baby > Boys Clothing > Boy Accessories`** — the path says both
  "Boys Clothing" (exempt under $110) and "Accessories" (not exempt).

All five are currently test listings. They must be resolved before any digital or
service listing is sold into a registered jurisdiction.

---

## 6. Calculation API — the constraint that shapes the integration

**`automatic_tax` is not available to PulseSoc.** It works only with Checkout
Sessions, Invoices, Subscriptions and Payment Links. PulseSoc creates
PaymentIntents directly, so tax must be computed explicitly:

```
stripe.tax.Calculation.create(...)              # quote time, authoritative
stripe.tax.Transaction.create_from_calculation(...)   # on payment success
stripe.tax.Transaction.create_reversal(...)           # on refund
```

This is load-bearing for a test in the repo:
`tests/test_marketplace_cart_lifecycle.py:224` asserts `"automatic_tax" not in code`.
The Calculation API does not trip that assertion, so the correct integration and
the existing guard do not conflict. If someone later reaches for `automatic_tax`,
that test is the tripwire and it should be respected, not edited.

Sandbox evidence that the full lifecycle works (NY registration active):

| Step | Result |
|---|---|
| Item $38.00, NY | tax 337 (8.875%), `standard_rated` |
| Item + $9.95 shipping | tax 426 = 338 item + 88 shipping — **shipping is taxable in NY** |
| CA / TX | 0, `not_collecting` |
| OR | 0, `not_subject_to_tax` |
| Transaction | `tax_1UHcaE2VN9kXLx8vU2KwQo9G`; shipping auto-coded `txcd_92010001` |
| Full reversal | `tax_1UHcaF2VN9kXLx8vpgpbg2dT` |
| Partial reversal | `tax_1UHcff2VN9kXLx8vCR6nBKq9` — −3800 / −337, `shipping_cost: null` (shipping correctly not refunded), original transaction intact |

Note the shipping result: it is not a constant. NY taxes shipping on a taxable
sale; other states do not, and some prorate it against the taxable fraction of a
mixed cart. Shipping tax must come from Stripe, never from a local rule.

---

## 7. Where tax lives in the PulseSoc data model

The plumbing already exists end to end. It is carrying zero.

| Layer | Field | File |
|---|---|---|
| Quote | `tax_minor`, `tax_snapshot` | `services/marketplace_quote_service.py` |
| Settlement | `tax_minor` | `services/marketplace_settlement_service.py:110` |
| Refund | `tax_refund_minor`, `tax_ledger_ref` | `services/marketplace_settlement_service.py:122` |
| Refund bounds check | `totals["tax"] + tax_refund_minor > settlement["tax_minor"]` | `…:281` |
| Ledger posting | `entry_type="marketplace_tax_liability"` → `destination="liability:marketplace_tax"` | `…:241-247` |

The ledger posting is worth calling out, because it is already right. Settlement
posts three separate entries, and tax goes to a **liability** account:

```python
seller_earnings_minor →  destination=f"seller_payable:{seller_id}"
platform_fee_minor    →  destination="platform:marketplace_revenue"
tax_minor             →  destination="liability:marketplace_tax"   # not revenue
```

Tax is therefore never commingled with platform revenue in the ledger, and the
remittance liability is queryable as its own balance. Whoever wrote this
anticipated tax correctly; the integration needs to feed it, not redesign it.

`tax_snapshot` currently defaults to `{"source": "not_calculated", "amount_minor": 0}`.
When Stripe Tax is wired in, this is where the `taxcalc_…` / `tax_…` ids belong —
the snapshot is the audit record that ties a PulseSoc settlement to a Stripe Tax
Transaction, and reconciliation (§10) depends on it.

**Gaps that must be closed before collection is enabled:**

- `seller_transactions` (`bot.py` ≈ 116345) has `amount_cents`,
  `platform_fee_cents`, `seller_net_cents` — and **no tax column**.
- `marketplace_orders` (`bot.py` ≈ 116009) has **no tax column**.

Both are per-order money records. Without a tax column, tax collected on an order
is unrepresentable in them.

---

## 8. Tax must never become seller earnings

This is the single most dangerous failure mode in the whole design, so it is
stated separately.

`services/marketplace_quote_service.py`:

```python
platform_fee   = (base["merchandise_net_cents"] * rate) // 10_000
seller_earnings = base["merchandise_net_cents"] + base["seller_shipping_credit_cents"] - platform_fee
```

`seller_earnings` is built from *merchandise net* plus a shipping credit. Tax is
not an input. That is correct — and it is correct **by construction**, not by
accident, which is why it is safe today.

But the invariant is currently unguarded and untested, because `tax_minor` is
always 0 and a test asserting "tax is excluded" would pass against any
implementation. The moment tax becomes non-zero, this needs a real test: a
calculation where tax > 0, asserting `seller_earnings_minor` is unchanged from
the tax-free case.

The buyer total, by contrast, **must** include tax:

```
ITEM + SHIPPING(where taxable) + TAX + PULSESOC FEE = TOTAL CHARGED
SELLER EARNINGS = merchandise net + shipping credit − platform fee   (no tax)
PLATFORM RETAINS = platform fee + tax   (tax is held for remittance, not revenue)
```

Tax sitting in the platform balance is a **liability**, not income. The PulseSoc
ledger already models this correctly (§7): tax posts to `liability:marketplace_tax`
while the fee posts to `platform:marketplace_revenue`. The residual risk is in
reporting that reads the *Stripe* balance rather than the ledger — there, tax and
revenue are the same pile of money, and anything summing it without subtracting
collected tax will overstate earnings.

---

## 9. Checkout integration — why it is not built

Mission gate: integrate *once a valid tax registration exists*. None does.

If it were built today it would be inert (§2.2), but it would add a synchronous
network call to the checkout hot path in exchange for a guaranteed $0. That is
pure downside: a Stripe Tax outage would become a checkout outage, for no
collected tax. The correct sequencing is registration first, integration second.

When it is built, the required shape is:

1. **Backend authoritative.** The client never supplies a tax amount. The mobile
   client currently shows "Taxes and fees: Added at payment"
   (`mobile-native/src/screens/MarketplaceCartScreen.tsx` ≈ 370) while nothing is
   ever added — that copy becomes true at the same moment the calculation lands,
   and must show the real number, not a promise.
2. **Calculate at quote time**, persist the `taxcalc_…` id into `tax_snapshot`.
3. **Re-calculate at charge time** and charge the recalculated total. Quotes have
   a 15-minute TTL (`QUOTE_TTL_SECONDS`) and rates change.
4. **Commit the Transaction only on payment success**, never before.
5. **Fail closed on calculation error** — refuse the checkout rather than
   charging an unknown-tax total.
6. Buyer sees the identical breakdown at checkout and on the receipt (§9 of the
   mission): Subtotal / Shipping / Tax / PulseSoc fee / Total.

---

## 10. Refunds and reconciliation

The refund path is already tax-shaped (§7); it simply always receives 0 today.

- **Full refund** → `create_reversal(mode="full")`, and `tax_refund_minor` equals
  the settlement's `tax_minor`.
- **Partial refund** → `create_reversal(mode="partial")` with explicit line
  amounts. Sandbox proves shipping is *not* implicitly refunded
  (`shipping_cost: null`) — if shipping is being refunded it must be named.
- The bounds check at `marketplace_settlement_service.py:281` already prevents
  cumulative tax refunds exceeding tax collected.

Reconciliation is **`tax_snapshot` ↔ Stripe Tax Transaction id**. Without storing
the id, a PulseSoc settlement and a Stripe Tax Transaction cannot be tied
together, and the Stripe-side filing report will not be provably the same money
as the PulseSoc ledger.

---

## 11. Admin notification (not built)

Required surfaces, for authorized admins only:

- New economic-nexus obligation detected by Stripe monitoring.
- Registration expiring / invalid.
- **Calculation failure rate** — the one that matters operationally, since
  fail-closed (§9.5) turns a Stripe Tax outage into refused checkouts.

Sellers must **not** be notified of platform tax-registration issues. Under the
facilitator model (§4) the seller has no action to take, and telling them
otherwise invites them to register and collect on their own — which would
double-collect.

---

## 12. Owner-required decisions

None of the following may be inferred, defaulted, or guessed.

1. **Legal entity name mismatch.** The live account has
   `company.name = "SZ1804 CORP"` but `business_profile.name = "COINPLOTXAI INC"`.
   A tax registration is held by a legal entity. Until it is established which of
   these is the registrant, no registration can be filed correctly. **This blocks
   everything else in this list.**

2. **Which jurisdictions is the entity actually registered in?** Requires, per
   jurisdiction: the state, the registration/permit number, and the effective
   date. Stripe requires all three. An effective date that predates the real
   permit creates a false record of collection authority.

3. **New York specifically.** NY is the head office and Stripe will not monitor
   it (§3). Whether a NY collection obligation exists is an accountant question.

4. **Connect platform profile funds-flow declaration** currently says "Sellers
   will collect payments directly", contradicting §4. Correcting it is a
   representation to Stripe about the business model.

5. **Ongoing seller compliance acknowledgement** — still pending on the platform
   profile page. It is a legal acceptance and requires the owner.

6. **The five ambiguous product classifications** in §5.3.

---

## 13. Change log

| Date | Change |
|---|---|
| 2026-09-19 | Live `defaults.tax_code` corrected `txcd_10000000` → `txcd_99999999` (§2.1). Monitoring-only effect; zero registrations means zero collection either way. |
| 2026-09-19 | Sandbox NY registration `taxreg_1UHcZs2VN9kXLx8vqRGWKFsv` created to exercise calculation / transaction / reversal. Test-mode registrations carry no legal representation. |
| 2026-09-19 | This document created. |
