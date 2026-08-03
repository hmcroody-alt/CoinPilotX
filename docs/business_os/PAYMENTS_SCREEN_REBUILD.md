# Mission F — Payments rebuilt as a money hub

The governing rule was that every number on this screen is a direct render of a
backend financial record, and that anything without a real source ships behind a
feature flag with the module **absent** rather than with an invented figure.
That rule did most of the design work. What follows is what shipped, what did
not, and why.

---

## 1. The finding that reshaped the mission

There are two money systems in this codebase and only one of them is reachable.

Every `BUSINESS_OS_*` flag is inert in production — the note at
`mobile-native/src/api/ordersDashboard.ts` lines 20–28 says so plainly — which
means `/api/business-os/marketplace/money*` returns 404 today. The natural move
was to bind the new screen to those routes, since they were designed for exactly
this. Doing so would have shipped a money screen that renders nothing, or worse,
one that falls back from a 404 to something plausible.

So the screen binds instead to the **live** creator-economy model:
`creator_wallets`, `creator_ledger_entries`, `seller_payout_accounts` and
`seller_payouts`, driven by `services/creator_economy_service.py` through the
Stripe webhook handler. Two new read-only GET routes expose it —
`/api/pulse/payments/seller/money` and `.../money/activity` — served by
`services/seller_money.py`, which has a test asserting it cannot move money.

Four hard facts came out of reading that model, and each one is now enforced by
a test rather than by a comment:

1. **No release path exists.** Nothing anywhere writes `entry_type="credit"` or
   `"release"`. A seller's `available_balance_cents` is structurally always
   zero; money enters as `hold` and stays there.
2. **No payout initiation exists.** No endpoint creates a payout. Rows in
   `seller_payouts` are inserted only by the Stripe Connect webhook.
3. **No bank destination is stored.** `seller_payout_accounts` holds a Stripe
   connected-account id (`acct_…`) — an identifier for a relationship, not for
   an account.
4. **No live per-order escrow.** Holds are per-wallet; per-order escrow lives
   only in the dark Business OS ledger.

Each of those turned directly into an absent module below.

---

## 2. What is absent, and what absence means here

Absent means the component does not render. Not greyed out, not showing a
placeholder, not showing `$0.00`. A disabled "Pay out now" still tells the
seller a payout is something they can nearly do; an absent one tells the truth.

| Module | Flag (default off) | Why it cannot ship |
|---|---|---|
| Pay out now | `EXPO_PUBLIC_PAYMENTS_PAYOUT_INITIATION` | Finding 2 — no endpoint initiates a payout |
| Instant payout | `EXPO_PUBLIC_PAYMENTS_INSTANT_PAYOUT` | Finding 2, plus no fee schedule to quote, and this screen computes no fees |
| Escrow balance card | `EXPO_PUBLIC_PAYMENTS_ESCROW_CARD` | Finding 4 — no held-in-escrow total is published |
| Statements | `EXPO_PUBLIC_PAYMENTS_STATEMENTS` | Nothing generates a statement |
| Tax documents | `EXPO_PUBLIC_PAYMENTS_TAX_DOCUMENTS` | Nothing issues a tax form |
| Ad wallet top-up | `EXPO_PUBLIC_PAYMENTS_AD_TOPUP` | Pulse Ads funding is gated three ways, all denying |

The tax section deserves its own sentence, because it is the case where an
empty state would have been actively harmful. "No form for 2025" is not the
absence of a claim — it asserts that a threshold determination was made and came
back negative. Nothing in this system performs that determination. A seller who
read it might reasonably conclude they have no filing obligation. So
`DocumentSection` returns `null` on an empty list, which makes absence the path
of least resistance rather than something a future caller has to remember.

The escrow card is enforced the same way structurally. `SellerMoneyOverview`
publishes no escrow total, and the screen routes that figure through a dedicated
`escrowCentsOf()` that returns `null` — specifically so nobody reaches for
`processing_cents` because it is nearby and non-null. Those are different
quantities; rendering one under the other's name is the exact fabrication the
brief rules out.

---

## 3. Where every visible number comes from

| Figure | Source | Notes |
|---|---|---|
| Hero "Available for payout" | `SellerMoneyOverview.available_cents` | Server aggregate. A test feeds a ledger that does not add up to it, to prove the hero never sums rows. |
| Processing | `SellerMoneyOverview.processing_cents` | Server aggregate over `hold` entries |
| Ad wallet | `adWalletSpendableCents(wallet)` via `fetchAdWallet` | Advertising's own `getAdWallet` call, unwrapped once |
| Ledger rows | `LedgerPage.entries` | Backend primary keys; no synthetic rows, no positional ids |
| Row sign | `LedgerEntry.sign`, decided server-side | One owner, so two screens cannot disagree |
| Payout in flight / last failed | `payout_in_flight`, `last_failed_payout` | Real `seller_payouts` records, with the provider's real reason |
| Payout destination | `payout_method.destination_masked` | Masked server-side; the full id never reaches the client |

The only arithmetic in the screen file is `Math.max` on a list length. No
balance math, no fee computation, no netting.

---

## 4. MOCK-DATA gaps

Nine, recorded in `PAYMENTS_MOCK_DATA_GAPS` in `mobile-native/src/api/paymentsHub.ts`
so they travel with the code rather than living only here. The three that most
shaped the UI:

- **`MOCK-DATA: bank destination`** — the reference design shows
  "•••• 4321 · Checking". Rendering that from the last four characters of a
  Stripe `acct_` id would have looked right, matched the mock, and been a
  fabrication: those digits appear on nothing the seller can check. The card
  says `Connection ····9999` and labels it as a connection.
- **`MOCK-DATA: refund deadline`** — the brief asks for "respond within N days
  or it's auto-approved". There is no auto-approval rule, no deadline field and
  no timer anywhere. There is no N. `RefundActionBanner.deadlineNote` is empty
  by default and renders nothing rather than a confident invented countdown. A
  seller who believes they have three days when nothing is counting may
  deprioritise a real dispute.
- **`MOCK-DATA: available balance`** — because no release path exists (finding
  1), the honest hero figure today is zero, and `describeAvailability()` reads
  its copy from the server's own `release_path` field so it changes when that
  changes rather than being left behind.

---

## 5. Ledger integrity

Rows render only from backend records with stable ids. Pagination is keyset on
the integer `id`, **not** `created_at` — `created_at` is a second-resolution
string, so an entire webhook batch shares one value and a timestamp cursor would
silently skip or repeat rows.

A hold is never rendered as an outflow. `sign` is `"none"`, the amount renders
unsigned in violet, and the row additionally renders the literal word `held`,
because colour is never the only signal. A screen reader hears
"…$18.00 held, still yours", which is the fact an AT user most needs and the one
the violet alone cannot convey.

Failed and reversed transactions keep their real status word and stay visible.
`statusWord()` and `referenceLabel()` fall through to the raw backend token
rather than a friendly guess, so an unrecognised status shows as itself instead
of being smoothed into something wrong.

A page that fails to load is not a shorter ledger: the list and cursor are left
exactly as they were and "Load more" stays tappable. Page boundaries dedupe by
id, so a row the server repeats does not appear twice.

---

## 6. Balances, staleness and the one path that shows cached money

Balances, activity and the ad wallet settle through `Promise.allSettled`, not
`Promise.all`, so a ledger that loads cannot paper over balances that did not.

A failed *fresh* balance read shows `—` with a retry, and never a cached figure.
Cached money appears on exactly one path — offline — and always carries the time
it was true.

Writing that test found a real bug. The first implementation accepted any cached
overview and then computed an "as of" label from it; when the cache had no usable
timestamp, the label came out empty and the stale balance rendered silently as
current. The fix makes the label the *precondition*: the clock is read before the
decision, and a cache without one falls through to `—` plus retry. The rule now
holds structurally instead of depending on a later branch remembering it.

A failed ad-wallet read yields `null`, not zero, and the card is absent. A zero
would look like a measurement.

---

## 7. Cross-screen consistency

The ad wallet is fetched through `fetchAdWallet`, which calls Advertising's own
`getAdWallet`. There is one wallet object and one code path to it; the moment
Payments grows its own wallet fetch, the two screens can drift and a seller
seeing two different ad balances cannot tell which is real.

The funding gate is delegated rather than duplicated: `adTopUpIsLive(billing)`
calls Advertising's `adFundingIsLive(billing)`, so if funding is dark there it is
necessarily dark here, rather than by two files happening to agree. The billing
argument is deliberately **required** even though the upstream predicate accepts
`undefined` — a caller who forgets to fetch billing gets a compile error instead
of a plausible-looking "funding is off" that hides a control for a reason nobody
wrote down.

Refund counting comes from the same query that feeds Orders' "Returns & issues"
tile, and is not recomputed.

---

## 8. Motion

Transform and opacity only. The rule that mattered most: **no ambient animation
ever runs on an amount.** Numbers slide in once on load and then hold perfectly
still — a figure that breathes reads as a figure that is changing.

| Motion | Duration | Runs when |
|---|---|---|
| Hero arrival | 450ms | Once per mount, guarded by a `played` ref — not keyed to the balance value |
| Balance cascade | 320ms, 70ms stagger | Once, on first ready |
| Row insert | 380ms, 40ms stagger | Only for ids not previously seen this session |
| Payout dot | 2200ms ping-pong | Only while a real payout row is in flight |
| Escrow indicator | 1600ms ping-pong | Only while something is genuinely held |
| Refund shimmer | 7000ms sweep | Purely atmospheric; carries no information |

The row-entrance bookkeeping is what makes a confirmed payout animate a single
new row in without a reload: an id absent from `seenIds` is genuinely new, an id
present in it never animates again, and on first load nothing is "new" — the
whole list arriving at once is the list, not an insertion.

Under reduce-motion every driver is set to its resting value and no ambient loop
starts. Nothing in the ambient layer carries information, so removing it costs
nothing.

---

## 9. Accessibility

The hero announces in one utterance, with the stale label first when there is
one: "As of 09:14, Available for payout, $42.00, …, Payout in progress." The
amount `Text` is explicitly hidden from AT so the balance is not read twice, and
it uses `adjustsFontSizeToFit` rather than truncation — an ellipsis could turn
`$1,240.00` into `$1,24…`, which is a different number.

Escrow semantics are carried in text, not colour. The refund banner is an
`alert` with an assertive live region. Every tap target is at least 44pt and
every text node allows font scaling. Loading skeletons are bars, never `$0.00`
and never `—`: a currency figure in a skeleton is a number the seller may read
before it resolves.

---

## 10. Security notes

- Both new routes are GET-only and read-only, with a test asserting the module
  cannot move money.
- The seller is the session user and nothing else — no id is accepted from the
  client. A test pins this.
- The payout destination arrives already masked; the full identifier never
  reaches the device, so there is nothing here to accidentally log or screenshot.
- No new payment paths, no client-side balance math, no client-side fee
  computation.
- The service import sits inside the handler, so a failure to import cannot take
  the app down at boot.

---

## 11. Trade dress

The reference design's yellow CTA is Amazon's. It lives as a single swappable
token: `PAYMENTS_CTA_REFERENCE` holds the original values, `PAYMENTS_CTA_PULSESOC`
holds the PulseSoc green that actually ships, and `PAYMENTS_CTA` points at one of
them. Swapping is a one-line change in `mobile-native/src/theme/paymentsLight.ts`.
Flagged rather than shipped, since the mission asked for it to be called out.

---

## 12. Files

**Backend (new):** `services/seller_money.py`; two GET routes in `bot.py`
(~line 83130) plus a comment correction at ~18406; `tests/test_seller_money_read.py`;
`tests/test_seller_money_routes.py`.

**Native (new):** `src/api/paymentsHub.ts`; `src/theme/paymentsLight.ts`;
`src/theme/paymentsMotion.ts`; `src/components/payments/` — `BalanceHero`,
`BalanceCard`, `LedgerRow`, `LedgerDayGroup`, `PayoutMethodCard`,
`RefundActionBanner`, `DocumentTile`, `PaymentsStates`, `index.ts`.

**Native (rewritten):** `src/screens/BusinessOsPaymentsScreen.tsx`;
`src/screens/__tests__/BusinessOsPaymentsScreen.test.tsx`.

**Docs:** this file; `docs/business_os/PAYMENTS_MONEY_SOURCE_MAP.md` updated so
its endpoint table distinguishes the live routes from the dark Business OS ones.

Seven shared components, as specified, all reused rather than re-implemented —
the barrel exists so there is one implementation of "how money looks" and in
particular one implementation of the escrow-is-not-a-loss rule.

---

## 13. Verification

- `tsc --noEmit` on the whole `mobile-native` project — clean.
- Full Jest suite — 2,526 tests across 140 files, all passing, run in four
  shards.
- `BusinessOsPaymentsScreen.test.tsx` — 16 tests, all passing, covering every
  rule in sections 3, 5 and 6 above. One of them found a real bug (section 6).
- `tests/test_seller_money_read.py` — 25 passing.
- `tests/test_seller_money_routes.py` — 9 passing.

---

## 14. Deviations and open questions

**`payments-live.html` was never attached.** The brief named it the visual and
motion source of truth and said to study it before coding. It was not in the
uploads and no `*-live.html` exists anywhere in the repo. This was flagged across
four sessions; the direction given was to build from the written spec. Every
value below is therefore an inference the HTML would have settled, and each is a
one-line change if the file turns up:

- Exact easing curves and the shimmer's sweep width. Durations came from the
  brief; the curves are reused from `storeMotion` for consistency with Store and
  Ads rather than invented.
- Card proportions, gutter rhythm and the hero's vertical spacing.
- The ledger day-header treatment (rendered inline, not sticky).
- The exact icon glyphs per ledger kind.

**Other deviations:**

- **The bank mask** — the design's "•••• 4321 · Checking" is not rendered.
  Section 4 explains why.
- **The refund deadline** — no N is shown, because there is no N. Section 4.
- **The CTA colour** — PulseSoc green ships instead of the reference yellow, as
  a swappable token. Section 11.

**Open questions for whoever picks this up:**

1. When a release path is built, `describeAvailability()` and the hero copy
   follow the server's `release_path` field automatically — but someone should
   confirm the intended wording for a partially-released balance.
2. `RefundActionBanner` needs no code change when a real dispute policy lands;
   `deadlineNote` becomes the real deadline and `urgent` starts meaning
   something. The policy itself is the missing piece.
3. `DocumentTile` is built and renders nothing. It refuses to invent a name, a
   year or an availability date, so statement generation can adopt it as-is.

**Housekeeping:** `mobile-native/tsconfig.payments.json` is a scratch config I
used to typecheck the payments subtree quickly. It is untracked and not part of
the commit; the sandbox would not let me unlink it, so it needs deleting by hand.
