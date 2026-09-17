# PulseSoc Marketplace Seller Agreement — WORKING DRAFT

> ## ⚠️ THIS IS NOT A PUBLISHED AGREEMENT AND MUST NOT BE SHOWN TO A SELLER
>
> **Status:** unreviewed draft, written by an engineer, not a lawyer.
> **This document is not legal advice and has had no legal review.**
>
> It exists for one purpose: to write down, in plain language, the commercial
> terms that `services/business_os/marketplace/policy.py` **already enforces in
> code**, so that a qualified lawyer can review real terms rather than invent
> them, and so that whatever is eventually published matches what the software
> actually does.
>
> Do not deploy this. Do not link it. Do not set
> `MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` on the strength of it.
> See §10 for what is missing and §11 for why those switches are not mine to flip.

Source of truth for every number below: `services/business_os/marketplace/policy.py`
at `POLICY_VERSION = "MARKETPLACE_STANDARD_V1"`. Where this document and the code
disagree, the code is what will happen, and this document is wrong.

---

## 1. Who the parties are

The platform is **COINPLOTXAI INC** ("PulseSoc"), operating the PulseSoc
Marketplace. "You" / "Seller" is the person or business listing items for sale.

**Open — legal:** PulseSoc's role must be characterised precisely. It is not a
cosmetic choice: it determines marketplace-facilitator tax obligations, consumer
protection duties, and whether holding seller funds is money transmission. The
code's own compliance list (`LEGAL_COMPLIANCE_REVIEW_REQUIRED`) names
`marketplace_facilitator` and `consumer_protection` as unresolved.

---

## 2. Commission

PulseSoc charges a commission of **5.00%** (500 basis points).

It is calculated on the **merchandise total after any discount you offer**, and
**not** on shipping and **not** on tax. In the code this base is named
`merchandise_net_after_seller_discount`.

There are no other platform charges under this version:

| Charge | Amount |
|---|---|
| Listing fee | $0.00 |
| Monthly seller fee | $0.00 |
| Buyer service fee | $0.00 |

**The commission is 5% or it is 0%. There is no third value.** `platform_fee_bps()`
returns the proposed rate only when all three owner gates are open and returns zero
otherwise, and the rate cannot be set per-seller or edited in a database row — a
commission has to be the rate the seller was actually shown, so it cannot change
under a live checkout.

**As of today all three gates are closed, so the commission being charged is 0%.**

### Worked example

Item $100.00, seller discount $10.00, shipping $5.00, tax $8.00:

| | |
|---|---|
| Merchandise gross | $100.00 |
| Less your discount | −$10.00 |
| **Merchandise net (commission base)** | **$90.00** |
| Commission at 5% | −$4.50 |
| Shipping credited to you | +$5.00 |
| **Your earnings** | **$90.50** |
| Buyer pays | $103.00 (90 + 5 shipping + 8 tax) |

Tax is collected from the buyer and is not yours or PulseSoc's — see §7.

---

## 3. Refunds and commission reversal

When an order is refunded, commission is returned to you **in proportion to what
was refunded**.

- Full refund of the merchandise → the **entire** commission is returned to you.
- Partial refund → commission is returned pro-rata, and never more than the
  commission originally charged.

So PulseSoc does not keep a commission on a sale that did not stand. Rounding is
by integer division on minor units and is capped at the original fee
(`platform_fee_reversal`).

**Open — commercial:** Stripe's processing fee on the original charge is **not**
returned by Stripe on a refund. Nothing in this agreement currently says who
absorbs it. Today it falls on PulseSoc by default. This is a real cost and needs a
decision before volume.

---

## 4. When you get paid

1. **Order completes.** Your earnings are recorded to your account.
2. **Protection window: 2 days** (`STANDARD_PAYOUT_PROTECTION_DAYS`). During this
   period the funds are recorded as yours but are not yet releasable, so a dispute,
   chargeback or refund can still be resolved against the order rather than clawed
   back from you afterwards.
3. **Transfer.** PulseSoc transfers the funds to your connected Stripe account.
4. **Payout.** Stripe pays out from your connected account to your bank on your
   account's own schedule.

Steps 3 and 4 are different events. PulseSoc controls the timing of step 3. Step 4
is between you and Stripe.

**Open — commercial:** the protection window is 2 days and the return window is
**unbounded** (§5 — the declared 14- and 30-day constants are both dead code). So a
return can always be accepted after your money has been released, and no choice of
protection window would change that: a card chargeback can arrive up to 120 days
after the charge regardless.

What happens then is described in §8 and is worth reading before you sign: your
balance goes negative, you cannot withdraw until it clears, and it clears out of
your later sales.

### Payment processing

Payments are processed by **Stripe**. You must complete Stripe's onboarding and
identity verification before you can be paid. PulseSoc never sees or stores your
bank details, government ID, or tax identification number — those go directly to
Stripe. Stripe's own terms apply to your connected account in addition to this
agreement.

---

## 5. Returns

> **⚠️ Do not state a return deadline here until one is actually enforced.**
>
> An earlier version of this section said "within 14 days", citing
> `STANDARD_RETURN_WINDOW_DAYS`. That was wrong and it is the exact failure this
> document exists to prevent — publishing a number the software does not honour.

**No return deadline is enforced anywhere in the codebase.** Two constants declare
one, they disagree with each other, and neither is read by any code:

| Declared | Where | Read by |
|---|---|---|
| 14 days | `policy.py::STANDARD_RETURN_WINDOW_DAYS` | nothing |
| 30 days | `marketplace_returns_routes.py::OPEN_WINDOW_DAYS` | nothing |

There are also **two separate returns implementations**, and the one wired to
order state (`services/business_os/marketplace/returns.py::request_return`) gates
only on order *status* — `paid`, `fulfilled` or `completed` — with no time
component at all. A buyer can open a return on a completed order indefinitely.
`tests/business_os/test_returns_core.py` exercises precisely that and expects it
to succeed.

So a seller signing this today would be agreeing to an unbounded return liability.

**Open — must be decided before publication:**

1. **What the window actually is**, and then make one constant real and delete the
   other. Whatever is published here has to be the number the code enforces.
2. **Which of the two returns implementations is the real one.** Two systems with
   two schemas is a defect independent of this agreement.
3. **Who pays return shipping.** Not encoded anywhere and not stated here. A common
   source of seller disputes.
4. How the window interacts with the 2-day payout protection and §4's gap — a
   return accepted after funds are released creates the negative balance described
   in §8.

---

## 6. What you may not sell

Listings in these categories are **rejected outright**:

illegal goods · stolen goods · counterfeit goods · weapons · explosives · illegal
drugs · controlled substances · prescription drugs · tobacco & nicotine · alcohol ·
human body parts · hazardous materials · recalled products · wildlife trafficking ·
sexual exploitation · personal data & credentials · malware · surveillance abuse ·
extremist merchandise · financial fraud tools · gambling · age-restricted goods

These require **manual review before going live**:

high-value collectibles · luxury goods · medical devices · items needing
authenticity review · otherwise regulated goods

This is enforced server-side by `listing_category_decision`, not by an honour
system.

### Other seller obligations

You are responsible for: the accuracy of your listings, having the legal right to
sell what you list, shipping what you sold within the timeframe you stated, and
complying with the laws that apply to you and your products.

---

## 7. Tax

Sales tax may be collected from the buyer at checkout and is held by PulseSoc in a
designated account (`liability:marketplace_tax`); it is neither your earnings nor
PulseSoc's revenue.

> **⚠️ Open — this is the largest gap in this draft.**
>
> Tax is **recorded but not remitted**. Nothing in the codebase files or pays it to
> any authority. `LEGAL_COMPLIANCE_REVIEW_REQUIRED` lists `sales_tax`,
> `marketplace_facilitator` and `tax_reporting` as unresolved.
>
> Marketplace-facilitator laws in most US states place the collection and
> remittance obligation on the **platform**, not the seller. If that applies to
> PulseSoc, this section is currently describing an obligation nobody is
> discharging. This needs a tax professional before the first taxable sale, not
> after.
>
> Seller income reporting (1099-K or local equivalent) is also unaddressed. Stripe
> may handle some of this for connected accounts; that must be confirmed rather
> than assumed.

---

## 8. Suspension and termination

**Open — not drafted.** The grounds on which PulseSoc may suspend a seller or
withhold a payout, the notice given, and the appeal route are not written and are
not consistently enforced in code. Withholding someone's money needs stated
grounds and a defined process.

Note a related gap. If a refund or chargeback lands after you have already been
paid, your balance goes negative: you cannot withdraw while it is, and the debt is
repaid automatically out of your next sales (`docs/payments/FUNDS_SEGREGATION.md`
§6). That mechanism works and is tested, but **it is not disclosed anywhere a
seller would see, and this agreement does not yet say it.** A seller can currently
be in debt to PulseSoc, and blocked from withdrawing, on terms they were never
shown. That has to be stated plainly here before the agreement is published.

The case the mechanism does not cover — a seller who goes negative and never sells
again — has no collection or write-off policy at all. Whatever is decided has to
be stated here and then built.

---

## 9. Changes to this agreement

**Open — not drafted.** Needs: notice period, how sellers are told, and whether
continuing to sell constitutes acceptance. Note the code already supports this
correctly — the policy is versioned (`MARKETPLACE_STANDARD_V1`) and
`MARKETPLACE_STANDARD_V1_EFFECTIVE_AT` sets a future effective date — so the
mechanism exists and only the terms are missing.

---

## 10. Everything missing from this draft

A lawyer will need to add, at minimum: governing law and jurisdiction; dispute
resolution and any arbitration clause; limitation of liability; indemnification;
warranty disclaimers; intellectual property and the licence you grant PulseSoc
over your listing content; data protection and privacy (GDPR/CCPA as applicable);
the INFORM Consumers Act disclosures for high-volume sellers (already flagged in
the code as `inform_act` and `high_volume_seller`); prohibited conduct; and the
open items marked throughout §§1–9.

**This draft covers the commercial mechanics only** — the numbers the software
enforces. It is roughly the smaller half of a seller agreement.

---

## 11. The three switches, and why they are not flipped

Activating this policy requires three environment variables:

```
MARKETPLACE_STANDARD_V1_OWNER_APPROVED
MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY
MARKETPLACE_STANDARD_V1_EFFECTIVE_AT
```

All three are unset. The commission is 0% and the gate is fail-safe: `all()` over
three conditions, so it cannot be opened by accident or by a partial deployment.

**I have not set them and will not.**

`MARKETPLACE_STANDARD_V1_OWNER_APPROVED` is not a feature flag. It is an
**attestation that the owner approved** — a named human agreeing to charge sellers
5% and to stand behind a contract. If I set it, the system records the owner's
approval of a commercial decision the owner never made. That is forging a
signature, and the fact that it is a string in an environment variable rather than
ink does not change what it asserts.

`MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY` attests that sellers have been
properly told the terms. Given §10, that is not currently true.

The remaining work — legal review, deciding the open items, publishing the agreed
terms, and setting these three values — is the owner's, and requires a lawyer for
the parts that are legal rather than commercial.

---

## 12. Related

- `services/business_os/marketplace/policy.py` — the enforced terms.
- `docs/payments/FUNDS_SEGREGATION.md` — how seller money is kept separate, and the
  open banking/regulatory question.
- `docs/payments/STRIPE_CONNECT_ARCHITECTURE.md` — transfer and payout mechanics.
