# Escalation: PulseSoc Premium has no price authority

Raised by Agent 5 (structured data), 2026-10-03, on branch
`search-os/agent-05-structured-data`.

**To:** Agent 0 (fleet), whoever owns payments/entitlement, whoever owns the
Premium product.
**Cc:** Agents 5, 7, 11, 12 — every one of those lanes is blocked on the answer,
in the specific ways listed at the bottom.

This is not a structured-data defect. Agent 5 found it by trying to answer one
question — *what price may the markup claim?* — and discovering that the
question has no owner. The structured-data half is already closed, fail-closed,
on this branch. What remains is a product and payments question, and it is live.

---

## The one-line version

Four places in this system name a price for PulseSoc Premium. They name four
different numbers. The one the buyer is actually charged is **not in this
repository** — it lives in a Stripe Price object referenced by id — and **no
public page shows a reader any Premium price at all.**

---

## The trace

Each stage below is the authority that stage actually reads, measured from
source, with production behaviour checked where it is observable without an
account.

### 1. Product identity

Two product keys exist and they are different products:

| Key | Name | Catalog |
|---|---|---|
| `pulsesoc_premium` | PulseSoc Premium | `services/business_os/entitlements/schema.py` |
| `crypto_intelligence_pro` | Crypto Intelligence Pro | same file |

The removed `Product` node carried the **name and benefits of the first** and
the **price of the second**, on three pages whose subject is the second. That
cross-wiring is how `$14.99` got into the markup: it is
`crypto_pro_monthly`'s price, not Premium's.

### 2. Entitlement identity

`_SEED_PLANS` in `services/business_os/entitlements/schema.py:52` prices four
Premium plans:

```
pulse_premium_monthly        999   month
pulse_premium_annual        9999   year
pulse_premium_trial            0   —
pulse_premium_grandfathered    0   —
```

So "the price of Premium" is already not a single number at the catalog layer,
which is correct — it is a plan ladder. Every stage below collapses it to one
number anyway.

### 3. Display price — **empty**

This is the stage that decides what structured data may claim, and it is the
stage with nothing in it.

- `/pricing` answers 200 and contains **no dollar amount at all**. Measured
  2026-10-03 against production (`5bdf4e431`): zero `$`-prefixed numbers in the
  response body.
- `/pulse/premium` answers **302** to an unauthenticated request. It is behind
  the login wall, so no crawler and no logged-out reader ever sees it.

There is therefore **no public surface on pulsesoc.com that displays a Premium
price.** Google's structured-data policy requires markup to represent visible
page content; with the display stage empty, the only policy-compliant Premium
`Offer` is no `Offer`. That is not Agent 5 being cautious — it is the only
available answer.

### 4. Checkout price — **two lanes, different authorities**

**Lane A — the one the UI actually calls.** `/api/premium/checkout`
(`bot.py:14760`). Every Premium button in the product posts here
(`bot.py:15185`, `:66242`, `:90276`). It rejects any `plan_key` other than
`founder_premium`, then builds a Stripe session from a **Price id**, not an
amount:

```python
"line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}]   # bot.py:14619
```

`STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")` (`bot.py:1281`), declared
empty in `.env.example:73`. Readiness is gated on `STRIPE_FOUNDER_PRICE_ID`
(`bot.py:14235`).

**So the amount the live checkout charges is not a number this repository
contains.** It is whatever the Stripe dashboard says that Price object costs.
No test, no gate and no code review in this repo can see it, and it can be
changed by someone with Stripe access without a commit.

**Lane B.** `/api/payments/checkout/premium/<plan_key>` (`bot.py:104908`) →
`_creator_checkout_for_item`:

```python
amount_cents = int(os.getenv("PULSE_PREMIUM_PRICE_CENTS", "1900"))   # bot.py:104826
```

`.env.example:782` declares `1900`. Two things about this lane:

- It charges the same `1900` **whatever plan you name.** `plan_key` reaches the
  Stripe line-item title (`bot.py:104825`) and nothing else, so
  `POST /api/payments/checkout/premium/pulse_premium_annual` creates a session
  titled "PulseSoc Premium pulse_premium_annual" charging $19.00, against a
  catalog annual price of `9999`.
- `plan_key` is an unvalidated path segment, trimmed to 80 characters, that
  lands in a buyer-visible Stripe line-item title. Flagged, not fixed — not
  this lane's call, and not a structured-data issue.

### 5. Charged price

Lane A: unknown from here, by construction (stage 4).
Lane B: `1900`, or whatever `PULSE_PREMIUM_PRICE_CENTS` is set to in the Railway
environment — also not observable from the repo.

### 6. Receipt / order truth

Lane B records what it decided to charge:
`creator_economy_service.create_transaction(..., gross_amount_cents=amount_cents)`
(`bot.py:104847`). So the order row agrees with lane B's env var by
construction, and agrees with the catalog only by coincidence.

### 7. Search projection — **closed**

No Premium price is published to any search surface:

- The `Product` node claiming `$14.99` is **deleted** on this branch
  (`fdb337296`). See `seo/schema.py:381` for the reasoning and the frozen
  invariant.
- The Merchant Center feed does not carry Premium. Verified against the live
  feed: no `14.99`, no "PulseSoc Premium".
- `tests/test_app_schema.py` holds the floor: no node may carry `offers`
  without a price a reader can see on the same page.

---

## The four numbers, side by side

| Number | Where it lives | Who reads it |
|---|---|---|
| `999` | entitlement catalog, `pulse_premium_monthly` | nothing that charges money |
| `9999` | entitlement catalog, `pulse_premium_annual` | nothing that charges money |
| `1900` | `PULSE_PREMIUM_PRICE_CENTS` | checkout lane B, all plans |
| *unknown* | Stripe Price object via `STRIPE_PRICE_ID` | **checkout lane A — the live one** |
| ~~`1499`~~ | was in the markup; is `crypto_pro_monthly`'s price | removed by `fdb337296` |

The catalog is the only one of these that is version-controlled, reviewable and
plan-aware. It is also the only one that never charges anybody.

---

## The invariant that has to hold before any of this is fixed

> **VISIBLE CONSUMER PRICE = CHECKOUT AUTHORITY = ACTUAL CHARGE =
> STRUCTURED-DATA PRICE**, for the same product, plan, currency and context.

All four, or none. Three out of four is the state that produced this escalation.

Agent 5's half of that equality is already enforced the only way it can be from
this layer — by refusing to make the claim. The invariant becomes satisfiable
when stages 3, 4 and 5 agree, and **not before**; re-adding structured data is
the last step, not the first.

---

## What Agent 5 explicitly did not do

Per the mission's own limits, and worth recording so nobody assumes otherwise:

- Did **not** choose between 999, 1900, 9999 and the Stripe amount. Picking one
  is inventing a fact.
- Did **not** modify Stripe amounts, Price objects, entitlement pricing, or any
  production checkout.
- Did **not** issue a transaction or a refund.
- Did **not** reactivate the removed node, under any price.
- Did **not** delete `bot.PRO_PRICE_MONTHLY = "$14.99/month"` (`bot.py:1198`),
  which now has zero readers repo-wide. It is dead, and deleting it would be
  tidy, but it is a *pricing* constant and whether `$14.99` is right is the
  question being escalated.

---

## What each lane needs from the answer

| Lane | Blocked on |
|---|---|
| **Agent 5** (structured data) | A price that is simultaneously displayed, charged and recorded. Until then the correct markup is no `Offer`, which is what ships. |
| **Agent 7** (Merchant Center) | Do **not** submit a Premium offer. There is no agreed price and the previous offer URL (`/#pricing`) resolves to nothing. The feed is correct today precisely because it omits Premium. |
| **Agent 11** (drift detection) | Premium is the worked example of the drift class you are building for: a price claim whose authority disagrees with the charge. It is currently undetectable from the repo, because the live charge lives in Stripe. A drift monitor for Premium has to read Stripe, not source. |
| **Agent 12** (adversarial) | Mutation 10 in `05_agent_12_required_mutations.md` is this: emit a Premium price while the authorities still disagree. It must fail. |

---

## How to tell this escalation is resolved

Not "a price was chosen". All four of:

1. A logged-out reader can see a Premium price on an indexable page.
2. That page's price equals what the checkout the UI calls actually charges.
3. The charged amount is derivable from, or reconciled against, the
   version-controlled catalog — so a change requires a commit.
4. The plan ladder survives the collapse: naming the annual plan charges the
   annual price.

Then, and only then, a `Product`/`Offer` node for Premium is a projection of
truth rather than an invention, and Agent 5's floor test will let it through.
