# Funds Segregation — keeping other people's money out of PulseSoc's bank

Date: 2026-09-17
Audience: the owner, and whoever sets the Stripe platform payout schedule.
Status: **the code half is done; the decision half is open and is the owner's.**

---

## 1. The problem separate charges and transfers creates

PulseSoc uses separate charges and transfers. That was the right call — it is what
keeps a seller's money freezable during the protection window — but it has a direct
consequence that no other charge model has:

**The buyer always pays PulseSoc.** Every cent of every marketplace order, every ad
top-up and every event ticket lands in one Stripe balance owned by COINPLOTXAI INC.
That balance simultaneously holds:

| Kind of money | Whose it is | Ledger account |
|---|---|---|
| Marketplace / ad / events revenue | PulseSoc's | `platform:*_revenue` |
| Seller earnings, cleared | the seller's | `seller_payable:<uid>` |
| Seller earnings, payout requested | the seller's | `seller_payout_pending:<uid>` |
| Captured order total, pre-settlement | **the buyer's**, refundable | `mkt_order_escrow:<order_id>` |
| Advertiser prepaid balance | the advertiser's, refundable | `advertiser:<uid>:wallet` |
| Advertiser reserved budget | the advertiser's | `ad_campaign_escrow:<cid>` |
| Sales tax collected | the tax authority's | `liability:marketplace_tax` |

Stripe does not distinguish between them. There is one number.

A platform payout — PulseSoc's own Stripe balance going to COINPLOTXAI's bank —
draws on that same number. So does every seller transfer. Nothing at Stripe stops a
platform payout from consuming money that a seller is about to be sent. The seller
transfer does not fail at the moment the money is spent; it fails later, when the
transfer is attempted, and by then the money is in a different bank.

This is the single most expensive failure mode in the marketplace, and until this
change nothing in the repository could even observe it. `ops.reconcile` checks that
the commercial snapshots agree with themselves — internal consistency, which says
nothing about whether the money is actually there — and there was no reader for the
Stripe balance at all.

---

## 2. The rule

```
withdrawable = available − designated
```

- **`available`** — the platform account's *available* balance at Stripe. Pending
  funds are excluded deliberately: a transfer cannot draw on them, so they cannot
  count toward solvency. They are reported separately so a near-term shortfall is
  visible before it bites.
- **`designated`** — money PulseSoc holds and does not own. Every row in the table
  above except the revenue line.
- **`withdrawable`** — the most PulseSoc may ever move to its own bank.

If `available < designated`, PulseSoc has **already** spent other people's money.
The transfers have not failed yet. They will.

---

## 3. What the code does

`services/marketplace_funds_segregation.py` — a reporting module. It reads and it
reports. It moves nothing and it blocks nothing.

```python
from services import marketplace_funds_segregation as segregation

segregation.assess("usd")                          # reads the balance from Stripe
segregation.assess("usd", available_minor=125000)  # or evaluates a figure you have
```

Returns `status` of `solvent`, `shortfall`, or `unknown`, plus `must_retain_minor`
(the floor), `withdrawable_minor` and `shortfall_minor`.

Three design decisions worth knowing, because each one is the difference between a
number you can act on and a number that flatters you:

**The classification is inverted.** The module does not list designated accounts
and treat the rest as PulseSoc's — that fails in the dangerous direction, because an
account type added next year would be silently counted as withdrawable. Instead it
lists what PulseSoc *owns* (`PLATFORM_OWNED`, by exact name) and treats everything
else as designated. An unrecognised account raises the reserve. The cost of being
wrong is a withdrawal PulseSoc could have made, not a seller who cannot be paid.

> This is not hypothetical. The first version of the module used the other polarity
> and missed `mkt_order_escrow:`, `advertiser:<uid>:wallet` and
> `ad_campaign_escrow:` — captured buyer money and prepaid advertiser funds. It
> reported all of it as withdrawable. `tests/marketplace/test_funds_segregation.py`
> now walks the AST of every ledger posting in the repository and fails CI if any
> account prefix is unclassified, so the next one is caught by a test rather than by
> a failed transfer. It found `platform:advertising_revenue` on its first run.

**Only positive balances count toward the reserve.** A negative
`seller_payable:<uid>` means that seller was overpaid. Netting it against another
seller's credit would fund seller A's payout out of seller B's debt, which is the
exact thing segregation exists to prevent. Overdrawn accounts are reported
separately as `overdrawn_accounts` — they are a real receivable, and **nothing in
this system recovers them** (see §6).

**`withdrawable` is never derived from what the ledger thinks PulseSoc earned.**
Those two numbers disagree the moment a Stripe processing fee is deducted: the fee
comes out of the real balance and the ledger never sees it. `platform_owned()` is
reported for context only and is not part of the arithmetic.

---

## 4. What the code does NOT do — and why the decision is yours

**The withdrawal this document is about does not happen in this codebase.**

PulseSoc's own payouts are Stripe's *platform payout schedule*, configured in the
Stripe Dashboard under Settings → Payouts. By default a Stripe account pays out
automatically on a rolling basis. Nothing in this repository calls
`stripe.Payout.create` for the platform account, so there is no code path for this
module to gate. Adding a blocking check here would guard a door nobody uses while
the real door stands open.

That makes segregation an **owner action**, and there are three ways to take it.
They are listed weakest to strongest and they are not mutually exclusive:

### Option A — Manual platform payout schedule (recommended first step)

Set the platform payout schedule to **manual** in the Stripe Dashboard. Stripe then
never moves money to COINPLOTXAI's bank on its own. Before each manual payout, run
`assess()` and withdraw no more than `withdrawable_minor`.

- **Protects against:** the automatic schedule silently draining seller funds.
- **Does not protect against:** a person withdrawing more than `withdrawable_minor`
  anyway. It is a discipline, not a control.
- **Cost:** PulseSoc's own cash stops arriving automatically.

### Option B — Keep a standing buffer

Hold a float in the Stripe balance above `designated` so ordinary timing noise
cannot create a shortfall. Option A decides *whether* to withdraw; this decides
*how much cushion* is left behind.

- **Does not protect against:** anything Option A does not, on its own. It reduces
  the chance of an accidental shortfall, not the ability to cause one.

### Option C — A segregated / FBO account structure

Hold customer funds in a separate account "for benefit of" the users, so the money
is legally not PulseSoc's general-purpose cash.

- **This is a legal and banking question, not an engineering one.** It touches
  money-transmission licensing, which varies by US state and by country, and
  whether PulseSoc is holding funds as an agent of the seller. Holding other
  people's money can constitute money transmission depending on structure and
  jurisdiction.
- **I am not able to make this call and this document does not advise on it.** It
  needs a lawyer who does payments regulation. It is flagged here because the
  arithmetic in §2 is a *monitoring* control, and monitoring is not the same as
  segregation — if the question "is PulseSoc allowed to pool these funds at all?"
  has a bad answer, no amount of reporting fixes it.

---

## 5. Owner checklist

- [ ] Decide the platform payout schedule: **manual** (Option A) or automatic.
- [ ] If manual: decide who runs `assess()` before each withdrawal, and where the
      result is recorded.
- [ ] Decide the standing buffer, if any (Option B).
- [ ] Take Option C to a payments lawyer — before volume, not after.
- [ ] Decide who is alerted on `status == "shortfall"`, and how. Nothing currently
      alerts; the module only answers when asked.
- [ ] Decide how overdrawn seller balances are recovered (§6).

None of these are set by this change. All of them are open.

---

## 6. Known gaps

**Nothing calls `assess()` on a schedule.** It is a function, not a monitor. A
shortfall is only visible if somebody asks. Wiring it to an alert is a follow-up and
it is not done.

**Nothing recovers an overdrawn seller balance.** A negative `seller_payable:<uid>`
is reported and then sits there. `services/business_os/payments/reconciliation.py`
raises an incident for it; no code collects the money.

**Tax is recorded but not remitted.** `liability:marketplace_tax` accrues and is
correctly held in the reserve, but nothing in this repository files or pays it. The
reserve keeps the cash available; it does not discharge the obligation.

**The reserve is per-currency.** Stripe holds a separate balance per currency and
they do not cross-fund. `assess()` must be run per currency; a solvent USD balance
says nothing about EUR.

**`available` is read from Stripe at one instant.** It is not a lock. Between the
read and a withdrawal, transfers can settle. Option A plus a buffer is what covers
the gap, not the freshness of the number.

---

## 7. Related

- `services/marketplace_funds_segregation.py` — the module.
- `tests/marketplace/test_funds_segregation.py` — including the AST drift guard that
  fails CI when an unclassified ledger account appears.
- `docs/payments/STRIPE_CONNECT_LEDGER.md` — the account taxonomy in full.
- `docs/payments/STRIPE_CONNECT_RUNBOOK.md` — what to do when a seller asks where
  their money is.
