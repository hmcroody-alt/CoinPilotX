# Payments — the money source map

The Payments screen's governing rule is that every number on it must be a direct
render of a backend financial record, and that anything without a real source
ships behind a flag with the module absent rather than with an invented figure.
This document is the evidence for that rule: it names, for every figure the
screen can display, the endpoint it comes from, whether that figure is a ledger
balance or a server-side sum, and what happens when there is no source at all.

It is written to be checkable. Every claim below has a test behind it, named
inline, and every "this does not exist" is asserted by a test that will fail if
someone later builds the thing without revisiting this page.

---

## 1. The foundation

All money in Business OS lives in one place: the canonical double-entry ledger at
`services/business_os/ledger/ledger.py`. Its properties matter to this screen
because they are what make "render, don't compute" safe:

Every posting moves an integer number of cents from a source account to a
destination account and writes two immutable entries plus both balance updates
inside a single transaction. Postings are idempotent at the database level via a
`UNIQUE` constraint on `idempotency_key`, so a retried write cannot move money
twice. Accounts outside the allow-negative prefixes (`platform:`, `external:`,
`stripe:`, `liability:`) are overdraft-guarded, so a balance that should never go
negative cannot. Balances are stored but always reconstructable from entries —
`recompute_balance` exists precisely so that the stored figure can be audited
against the entry history rather than trusted.

The consequence for the client is the important part. Because the ledger is the
authority, the screen never has to derive a balance from anything, and therefore
never should. There is no case in this design where the correct client behaviour
is arithmetic on money.

---

## 2. Balances — every figure, its endpoint, and its kind

`GET /api/business-os/marketplace/money` → `api.seller_money_overview` →
`money.seller_money_overview`. Flag: `BUSINESS_OS_MARKETPLACE`. Scoped to the
session user; there is no seller identifier in the request.

| Screen figure | Field | Kind | Source |
|---|---|---|---|
| Available for payout (hero) | `available_cents` | **Ledger balance** | `get_balance("seller_payable:<sid>")` |
| Held in escrow (total) | `escrow_total_cents` | **Server-side sum** of ledger balances | one `get_balance` per open order's `mkt_order_escrow:<order_id>` |
| Held, split by state | `escrow_by_status` | Server-side sum, grouped | same accounts, grouped by the order's real status |
| Per-order hold | `escrow_orders[].held_cents` | **Ledger balance** | `get_balance(mkt_order_escrow:<order_id>)` |
| Ad wallet, spendable | `wallet.spendable_balance_cents` | Backend-computed balance | `/api/pulse/ads/accounts/<id>/wallet` — **a different endpoint; see §5** |

Three things about this table are load-bearing.

**Nothing in it is composed on the client.** Where a figure is a sum, the sum
happens on the server, over ledger balances, inside one request. The mission
permits composition only if it happens server-side or in one well-tested client
layer; this implementation takes the first option, which removes the question.

**The held amount is read from the ledger, not from the order.** An order's
`total_cents` and its escrow balance are the same number right up until a partial
refund lands, at which point they differ and the ledger is the one that is right.
`test_partial_refund_moves_the_held_figure_immediately` posts a 2,500 refund
against a 10,000 order and asserts the held figure becomes 7,500 while
`total_cents` stays 10,000.

**An order holding nothing is not in the held total.** A fully refunded but still
open order contributes zero and is omitted from `escrow_orders` entirely, rather
than appearing as a 0 row that a client might sum or display
(`test_fully_refunded_open_order_holds_nothing`).

### The escrow split, and the distinction that does not exist

The design brief describes a three-balance model whose middle bucket is
"Processing". This backend does not model the concept that bucket usually means.
`fulfillment_type` is only `physical` or `digital` — there is no
pickup-versus-delivery distinction anywhere, so there is exactly one hold
concept, not two.

What does exist is the order state machine, and it draws a real line:

- `paid` — captured; the seller has not fulfilled yet
- `fulfilled` — seller has fulfilled; awaiting the buyer's completion

`escrow_by_status` reports those two states separately because the state machine
genuinely distinguishes them. Whether the product labels one of them "Processing"
is a rendering decision; the backend reports states, not labels. `ESCROW_STATUSES`
is imported from the orders module rather than re-declared, and
`test_escrow_total_is_summed_across_accounts_and_split_by_real_state` asserts the
two lists stay equal — so the money view cannot drift from the state machine.

### Truncation is reported, not hidden

An overview reads at most `MAX_ESCROW_ACCOUNTS` (500) escrow accounts. A seller
with more open orders than that gets `escrow_truncated: True` — a truthful signal
that the total is a floor — rather than a slow request or a quietly wrong number.

---

## 3. Activity — the ledger feed

`GET /api/business-os/marketplace/money/activity` → `api.seller_activity` →
`money.seller_activity` → `ledger.list_account_transactions`.
Query parameters: `currency`, `cursor`, `limit`, `types`.

**Composition.** The account set is `seller_payable:<sid>` plus every open order's
escrow account, unioned in a single SQL statement inside the ledger. The client
receives one ordered feed and never interleaves lists.

**Stable IDs.** Every row carries `transaction_id` from `ledger_transactions` and
a `cursor` derived from the entry's primary key. There are no synthetic rows and
no client-generated identifiers.
`test_newest_first_with_stable_transaction_ids` asserts uniqueness and presence.

**Signs belong to the account, not the entry type.** A settlement posting is
`+900` to the payable account and `−900` to escrow *with the same `entry_type`*.
`signed_amount_cents` is computed from the requested account's point of view and
the client renders it directly. Inferring direction from `entry_type` is wrong,
and `test_sign_is_relative_to_the_requested_account` asserts exactly why by
checking that both sides carry the identical type.

This is also why an escrow hold must not render as a negative. A hold is money
arriving in an escrow account — positive from escrow's point of view — and the
design's "unsigned violet with 'held'" is the correct presentation of it. Money
held is not money lost.

**Pagination.** Keyset on `ledger_entries.id`, a monotonic unique primary key.
Timestamps are deliberately not used as a cursor: two postings inside the same
millisecond would make a timestamp cursor silently skip rows.
`test_pagination_covers_every_row_exactly_once` walks 25 rows at limit 7 and
asserts every row is visited exactly once, with `has_more` and `next_cursor`
agreeing at the boundary. A non-numeric cursor is rejected with a `LedgerError`
that the controller maps to `400 bad_cursor`, not a 500.

**Failed and reversed transactions stay visible.** `status` is reported verbatim
from `ledger_transactions` and never filtered to make the list look tidier.
`test_non_posted_transactions_keep_their_real_status` voids a transaction and
asserts it is still in the feed, wearing `void`.

**A bad row does not take down the feed.** Malformed `metadata_json` degrades to
`None` while the amount on the row stays correct
(`test_malformed_metadata_degrades_to_none`).

**One documented limitation.** Escrow accounts for orders that have already
settled are not in the account set, because the order is no longer open. The
settlement itself remains visible — it credited the payable account, which is
always in the set. `accounts_scanned` is returned so this is auditable rather
than mysterious.

---

## 4. Disputes and the refund banner

`GET /api/business-os/marketplace/money/disputes` → `api.seller_disputes` →
`money.seller_disputes`. Default filter `open`; widening requires `status=all`
spelled explicitly, so a dropped query string cannot silently widen the result
set.

Seller scoping is a SQL join through the order
(`business_os_mkt_disputes JOIN business_os_mkt_orders ON order_id`), because the
disputes table stores `buyer_user_id` and not `seller_user_id`. Doing the scoping
in the query rather than narrowing an admin-shaped list in the caller is the
point: an admin query narrowed later is one forgotten filter away from showing a
seller somebody else's case. `test_seller_sees_only_their_own_disputes` asserts it.

Each case carries `amount_cents` (the order total the dispute is about) and
`held_cents` (what is still in escrow right now, from the ledger). Those differ
after a partial refund and the second one is the money actually at stake
(`test_dispute_reports_money_at_stake_from_the_ledger`).

### What the banner must not say

Two fields are returned as explicit absences rather than omitted, so a client
branches on them instead of inventing them:

- **`response_deadline: null` and `auto_approval_policy: "none_defined"`.** There
  is no deadline column and no timer anywhere in the dispute lifecycle. There is
  no N. A banner must therefore not render "respond within N days or it is
  auto-approved", because nothing in this backend will auto-approve anything.
  The truthful copy is that the case is open and awaiting resolution.
- **`seller_can_resolve: false`.** `resolve_dispute` takes an admin actor. A
  seller can read their cases; they cannot decide them. A resolve button on this
  screen would 403.

`test_dispute_read_does_not_invent_a_deadline_or_authority` asserts all three.

---

## 5. The ad wallet — two systems, and which one the card must read

**This section corrects an earlier draft of this document.** The first version
claimed the Payments ad-wallet card should render the Business OS advertising
wallet. That was wrong, and the way it was wrong is exactly the failure mode the
mission names: it would have put two cards labelled "ad wallet" on two screens,
both sourced from a real backend, showing different numbers, with nothing on
screen to explain why.

There are **two advertiser wallets in this codebase**, and they are separate
systems rather than two views of one:

| | Pulse Ads wallet | Business OS advertising wallet |
|---|---|---|
| Module | `services/pulse_ad_payments.py` | `services/business_os/advertising/funding.py` |
| Endpoint | `/api/pulse/ads/accounts/<id>/wallet` | `/api/business-os/advertising/wallet` |
| Storage | own tables: `pulse_ad_wallet_transactions`, `pulse_ad_receipts` | canonical ledger, `advertiser:<uid>:wallet` |
| Funding | Stripe checkout session (gated, see below) | none |
| Flag | `PULSE_ADS_BILLING_ENABLED` | `BUSINESS_OS_ADVERTISING` |
| **Rendered by the Advertising screen** | **yes** (`adsDashboard.walletSummary`) | no |

**The Payments card must read the Pulse Ads wallet**, because that is the one
`BusinessOsAdvertisingScreen` and `AdsManagerScreen` render. The Payments screen
calls the same endpoint Advertising calls, and there stays exactly one source
per card.

`money.seller_money_overview` therefore returns **no ad-wallet balance at all** —
only `ad_wallet_source: "pulse_ads_wallet_endpoint"`, a pointer saying which
endpoint owns the card. `test_overview_carries_no_ad_wallet_balance_of_its_own`
fails if a balance-shaped ad-wallet field is ever added back.

Render `spendable_balance_cents`, falling back to `available_balance_cents`, in
line with `adsDashboard.walletSummary` — matching the fallback matters, because a
different fallback is a slower way of producing the same divergence. Note the
existing rule in that module: a failed wallet call shows **no chip**, never a
stale or zero one.

### Top-up: exists in code, unreachable in the app

The Pulse Ads wallet does have a funding path — `POST .../wallet/funding-session`
creating a Stripe checkout session. Three separate gates stand in front of it:

1. `PULSE_ADS_BILLING_ENABLED` must be set, and `STRIPE_SECRET_KEY` plus
   `APP_BASE_URL` must both be present, or the route returns 503.
2. `live_charging` is **hardcoded `False`** in `services/pulse_advertiser_portal.py`
   (lines 485 and 774), so `adFundingIsLive(billing)` is always false.
3. Native iOS requests are rejected outright with a 403 while billing compliance
   is under review.

So the balance is real and must be rendered; the top-up affordance is not
reachable from this app and ships **absent**, which is what the current screen
already does and what the rebuilt screen must keep doing. Auto top-up does not
exist on either wallet.

### The Business OS wallet, for completeness

`GET /api/business-os/advertising/wallet` → `funding.wallet_view` is real,
ledger-backed, and tested (10 tests), and is the correct wallet read for the
Business OS advertising vertical. It is documented here so nobody later mistakes
it for a duplicate of the Pulse Ads wallet or wires it into a payments card.

Its `balance_cents` is **already net of reservations** — reserving a campaign
budget moves cents out of the wallet into `ad_campaign_escrow:<campaign_id>`, so
a client must not subtract `reserved_cents` from it
(`test_reserving_moves_money_out_of_the_wallet_not_alongside_it` asserts the two
figures sum back to the original deposit). The account name is exported as
`funding.wallet_account` so two surfaces cannot build the string independently
and drift apart. It has **no funding path of any kind**:
`test_no_product_path_credits_the_wallet` parses `funding.py` and fails if a
second posting ever starts crediting the wallet.

---

## 6. Absent by design — what this environment cannot source

Each of these is reported as an explicit machine-readable absence, with a test
that fails if the field ever appears without a real source behind it.

### Payout execution — absent

`payout_execution: "provider_side_out_of_scope"`.

Moving money to a seller's bank is a provider-side transfer this environment does
not perform. There is no disbursement engine, so there is no schedule, no
destination, no arrival estimate, and no instant-payout quote.
`test_disbursement_is_reported_as_absent_not_invented` asserts that
`next_payout_date`, `payout_method`, `instant_payout_fee_cents` and
`estimated_arrival` never appear in the overview.

**Client consequence, per the brief's own rule:** the "Pay out now" button is
**absent, not disabled**. The hero's sub-line cannot name a next payout day or a
masked destination, because neither exists. There is no payout-method card to
render, and the "no payout method" empty state — which the brief says outranks
other prompts — is the *correct permanent state* of this screen today, not an
edge case.

### Instant payout — absent

There is no quote endpoint, no fee table, and no transfer primitive. The mission
forbids computing a fee client-side, and there is nothing to quote it from. The
module ships absent.

### Statements and tax documents — absent

There is no statement generator and no 1099-K issuance anywhere in this codebase.
The brief is explicit that document names, years, and availability must never be
fabricated. The documents module therefore ships absent behind its flag. It is
worth noting that the brief's suggested fallback copy — "No 1099-K for 2025 —
under the reporting threshold" — would itself be a fabrication here, because it
asserts a threshold determination that nothing in this backend performs. The
truthful state is that the feature does not exist yet.

### Ad wallet top-up and auto top-up — absent

Both wallets reach the same conclusion by different routes, so both have to be
stated. The card the Payments screen renders is the Pulse Ads one (§5), so it is
the one that decides the affordance — but the other must be described too, because
a reader who checks only `funding.py` would draw the right conclusion for the
wrong wallet.

**Pulse Ads wallet — a funding path exists in code and is unreachable in the app.**
`services/pulse_ad_payments.py` has a real Stripe top-up. It is gated three
independent ways, and all three currently deny:

| Gate | Where | State |
| --- | --- | --- |
| `PULSE_ADS_BILLING_ENABLED` and `stripe_ready()` | billing service | off by default in this environment |
| `live_charging` | `services/pulse_advertiser_portal.py` lines 485, 774 | hardcoded `False` |
| native-iOS caller | portal | `403` |

The client already encodes this: `adFundingIsLive()` in
`mobile-native/src/api/businessOs.ts` is `billing_enabled && live_charging`, and
because the second term is a literal `False` in the source, the function cannot
return true. So the **balance is renderable and real; the top-up button is not**.
An "Add funds" or auto-top-up control on the Payments screen would either fail on
tap or, worse, appear to arm a charge that no code path can execute.

**Business OS wallet — no funding path at all.**
`funding_source: "none_in_product"`, `auto_topup: "unsupported"`. Every posting in
`funding.py` that touches a wallet account has a campaign escrow on the other
side: reserve moves wallet → escrow, release moves escrow → wallet. **Nothing
credits the wallet from an external source**, and the wallet is overdraft-guarded,
so an advertiser's balance is exactly zero unless cents arrive by some non-product
means (the test suite's own seed helper being the only example).

That finding is enforced, not merely written down.
`test_no_product_path_credits_the_wallet` parses `funding.py`, strips docstrings
and comments so prose about funding cannot satisfy it, and fails if the number of
ledger postings changes or if a second posting starts crediting the wallet. If
someone builds a top-up, that test fails and this page has to be revisited —
which is the intent.

**Client consequence, both wallets:** the top-up control and the auto-top-up
switch ship **absent**, not disabled. The brief's rule that "the switch alone must
not silently arm charges without the configuration step" is satisfied trivially
here, because there is no configuration step to reach. Building either funding
path is a payment-path decision, and the mission forbids new payment paths.

**When this changes:** the switch becomes buildable only when a top-up *quote and
confirm* flow exists server-side. Flipping `live_charging` alone is not enough —
the auto-top-up threshold and amount have no storage on either side today, so the
switch would have nothing to persist. Both would need to become one setting read
by Advertising and Payments alike, per the brief's "one setting, two surfaces".

---

## 7. Read-only, structurally

`money.py` contains no write primitive and must never contain one — a read module
that could also move money would be a second, ungoverned payment path. This is
not a convention; it is enforced. `test_module_cannot_move_money` scans the
module source (with docstrings and comments stripped, so the file is free to
*explain* the write side) and fails on `post_entry`, `refund_order`, `pay_order`,
`complete_order`, `INSERT `, `UPDATE `, or `DELETE `.

The same guarantee is enforced at the route layer.
`tests/business_os/test_marketplace_money_routes.py` parses `bot.py` and asserts,
for all three money endpoints:

1. registered at the canonical path and **GET-only** — a money read that also
   answers POST is a write surface waiting to happen
2. flag gate first, returning **404 not 403** — a disabled surface should not
   confirm it exists
3. authentication before any money access, with the denial returned
4. **the seller identity is `user.get("user_id")` from the session, never a query
   parameter** — the single mutation that matters, and the test was verified
   against a deliberately introduced `request.args.get("seller_id")` to confirm
   it fails when it should
5. no write primitive reachable from any route
6. each route reaches exactly its own controller and no other

---

## 8. Security notes

Full account numbers are not a risk on this surface for the simple reason that
there are none: no payout destination is stored anywhere in this backend, so
there is nothing to mask and nothing to leak. That changes the moment a
disbursement provider is integrated, and masking must be server-side when it does.

**Step-up authentication does not exist in this codebase** as a reusable
primitive. The brief asks for it on instant payout, payout-method change, and tax
document access — all three of which are absent, so nothing ships unprotected
today. It is recorded here as a **flagged product gap** that must be built
before any of those three modules is enabled.

No financial figures may appear in analytics payloads. Nothing in the backend
emits them; the constraint applies to the client layer when it is built.

---

## 9. Endpoint summary

Two families, and the distinction between them is the single most important fact
in this document.

**Reachable in production — what the Payments screen actually renders.** These
sit on the live creator-economy tables (`creator_wallets`,
`creator_ledger_entries`, `seller_payout_accounts`, `seller_payouts`) and are
served by `services/seller_money.py`. They carry no `BUSINESS_OS_*` gate.

| Endpoint | Method | Flag | Returns |
|---|---|---|---|
| `/api/pulse/payments/seller/money` | GET | none — live | available/processing totals, wallet parts, payout method, in-flight and last-failed payout, explicit absences |
| `/api/pulse/payments/seller/money/activity` | GET | none — live | keyset-paginated ledger feed, server-decided signs, stable row ids |
| `/api/pulse/ads/accounts/<id>/wallet` | GET | none — live | the one ad wallet the Advertising screen reads |

**Dark in production.** Every `BUSINESS_OS_*` flag is inert in this environment
— see the note at `mobile-native/src/api/ordersDashboard.ts` lines 20–28 — so
these routes return 404 today. They are documented because they are the intended
destination once Business OS ships, not because anything renders from them. The
Payments screen deliberately does **not** bind to them: a screen wired to a 404
is a screen that shows nothing, and a screen that falls back from a 404 to a
guess is worse.

| Endpoint | Method | Flag | Returns |
|---|---|---|---|
| `/api/business-os/marketplace/money` | GET | `BUSINESS_OS_MARKETPLACE` | balances, escrow split, ad wallet, explicit absences |
| `/api/business-os/marketplace/money/activity` | GET | `BUSINESS_OS_MARKETPLACE` | one paginated ledger feed, seller-relative signs |
| `/api/business-os/marketplace/money/disputes` | GET | `BUSINESS_OS_MARKETPLACE` | seller-scoped cases, money at stake, explicit absences |
| `/api/business-os/advertising/wallet` | GET | `BUSINESS_OS_ADVERTISING` | wallet balance, reserved total, top-up absence |
| `/api/business-os/marketplace/payouts` | GET | `BUSINESS_OS_MARKETPLACE` | pre-existing payable balance (superseded by `/money`) |

The consequence for the escrow card: per-order escrow exists only in the dark
Business OS ledger, so `SellerMoneyOverview` publishes no held-in-escrow total
and the card ships absent behind `EXPO_PUBLIC_PAYMENTS_ESCROW_CARD`. The screen
routes that figure through a dedicated `escrowCentsOf()` that returns null, so
nobody can reach for `processing_cents` because it happens to be nearby — those
are different quantities and rendering one under the other's name is precisely
the fabrication this mission forbids.

## 10. Test inventory

**Live path (what ships):**

| Suite | Tests | Covers |
|---|---|---|
| `tests/test_seller_money_read.py` | 25 | totals unioned server-side, credit/debit/hold classification, holds never negative, title fallbacks that invent no counterparty, keyset pagination on integer id, module cannot move money |
| `tests/test_seller_money_routes.py` | 9 | GET-only, seller identity is the session user and nothing else, service import inside the handler, auth ordering |
| `mobile-native/.../BusinessOsPaymentsScreen.test.tsx` | 16 | balance never derived from the ledger, failed read shows "—" and never a cache, cached money always carries its time, absent-not-disabled modules, holds unsigned and spoken as "held", failed rows stay visible, page dedupe by id, Stripe connection never dressed as a bank mask |

**Business OS path (dark, documented):**

| Suite | Tests | Covers |
|---|---|---|
| `test_ledger_account_history.py` | 11 | ordering, signs, union, pagination, filters, failed rows, bad metadata |
| `test_marketplace_money_read.py` | 14 | balances, escrow split, refunds, isolation, disputes, delegation, read-only |
| `test_marketplace_money_routes.py` | 10 | route wiring, flag darkness, auth ordering, session-scoped identity |
| `test_advertising_wallet_read.py` | 10 | wallet balance, reservations, isolation, release, top-up absence |

**Total: 95 tests across the two paths, all passing.** Alongside them the full
`mobile-native` Jest suite is green — 2,526 tests across 140 files — and
`tsc --noEmit` on the whole project exits clean.
