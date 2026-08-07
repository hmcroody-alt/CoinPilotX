# Phase 9 — money reconciliation across the advertising stack

**Scope:** the path a dollar takes through the ads product — Stripe top-up, wallet
credit, campaign spend, refund or dispute reversal — and every surface that reports
on it: the `/api/pulse/ads/portal` payload, the web advertiser portal, and the React
Native Advertising screens.

**The failure this phase exists to remove.** Every defect below produced the same
symptom: a figure rendered as a confident `0` or `$0.00` when the truth was either
unknown, broken, or a debt. A zero is the most dangerous value a money surface can
print, because it is indistinguishable from a correct answer. An advertiser looking
at `$0.00` concludes "I never funded this account." The three real causes of that
same `$0.00` were "your top-up was charged back after you spent it, so you owe us
$500", "we could not read your wallet at all", and "we could read four of your five
wallets and summed those four".

---

## What was actually wrong

### 9a — a reversed top-up never debited the wallet

`services/pulse_ad_payments.py` already had `reverse_wallet_funding`, and the
`charge.refunded` / `charge.dispute.*` branch of the Stripe webhook in `bot.py`
already recorded a row in `pulse_ad_refunds`. The two were never connected. A
refunded or disputed top-up was written down as a refund and left in the wallet as
spendable money. The advertiser kept the funds and Stripe kept the charge-back.

The webhook branch now calls `reverse_wallet_funding`, keyed on the Stripe
`event_id` so a redelivered webhook cannot debit twice. Because the money may
already be spent by the time the reversal lands, `available_balance_cents` is
allowed to go negative — that is the honest representation of a debt, and flooring
it at zero here is precisely how the fake zero was manufactured. The floor is
applied one layer up instead, at `spendable_balance_cents`, which is the figure
that governs whether delivery may continue. `wallet_summary` gained
`amount_owed_cents = max(0, -available_cents)` so the debt is *named* rather than
left to be inferred from a minus sign, and `money()` now renders negatives as
`-$5.00` rather than dropping the sign.

### 9b — spend never touched the credit buckets it was allowed to spend

`spendable_balance_cents` counted `promotional_credits_cents`,
`bonus_credits_cents` and `refund_credits_cents`, so campaigns were cleared to
deliver against them. `record_spend_event` then drew down only
`available_balance_cents`, and clamped it with `max(0, ...)`. An account funded
entirely by promotional credit could therefore spend indefinitely: the check
passed, the debit clamped to zero, and the credit buckets were never reduced.
Spend now draws down the buckets in order and the clamp is gone.

### 9c — the surfaces still reported the old, tidier world

Fixing the server produced two new values the clients had never seen: a wallet that
can owe money, and a wallet row the server admits it could not read. Every surface
had to be checked for whether it would show them or quietly flatten them back.

**Web** (`templates/pulse_advertiser_portal.html`, `static/js/pulse_advertiser_portal.js`).
An `Owed` tile was added that is `hidden` unless `amount_owed_cents > 0`, so it never
contributes a decorative zero to the grid. The wallet tile carries a partial-total
warning driven by `wallets_unavailable`, and the per-account card renders "Balance
unavailable" instead of a number when the server flagged the row.

**Mobile — the most consequential finding.** `normalizeAdWallet` in
`src/api/businessOs.ts` coerced every field with `Number(x || 0)`. The server's new
`unavailable: true` row arrives with `null` figures; the normalizer would have
turned each of those nulls into `0` and handed the screen a perfectly ordinary,
perfectly wrong `$0.00` wallet — re-manufacturing on the client the exact fake zero
the server had just been fixed to stop sending. The normalizer now returns early on
that flag, keeping the reason and leaving the numbers `undefined`.

`walletFigure()` in `src/api/adsDelivery.ts` treated `undefined` cents identically to
`0`, printing `"$0.00"` for a figure the server never sent; only an explicit `0` now
earns that string. `walletRollupAuthority()` believed a combined total the server had
explicitly marked partial; it now refuses the total outright when
`wallets_unavailable > 0`, on the grounds that a short total presented as a whole one
is the same lie as a fake zero and considerably harder to spot.

The overdrawn state was invisible on mobile for a subtle reason worth recording.
`spendable_balance_cents` correctly floors at `$0.00`, so an overdrawn wallet and an
empty one render the same figure. The existing "Ad wallet is empty" banner fired only
while a campaign was still trying to spend — reasonable for a merely empty account,
where nothing has stopped yet. But the reversal handler *pauses* every campaign it can
no longer fund, so by the time the advertiser opens the screen there is nothing
delivering and the one condition that would have surfaced the banner is already false.
The debt would have been visible nowhere at all. The banner now also fires on a debt,
and reads "Ad wallet is overdrawn" with the reversal named, because "add funds to
resume" is the wrong instruction for someone who needs to know a payment was charged
back — they would top up the debt and wonder why nothing restarted.

---

## Verification

| Gate | Result |
|---|---|
| `python3 -m py_compile` on the three changed services | exit 0 |
| `ast.parse(bot.py)` | OK |
| `node --check static/js/pulse_advertiser_portal.js` | OK |
| `tests/pulse_ads/` (3 new modules) | **24 tests, all passing** |
| Full backend `unittest discover -s tests -t .` | 1732 tests — 13 failures, 5 errors, **0 attributable to this phase** (see below) |
| `npx tsc --noEmit` | exit 0, 0 errors |
| `npm run i18n:validate` | OK — 11 locales, 4 pre-existing advisory warnings |
| Full mobile Jest (4 chunks) | **182 suites / 3,413 tests, all passing** |
| Revert-validation of the 9 new `adsDelivery` tests | all 9 fail against `HEAD` |

**New tests.** `tests/pulse_ads/test_wallet_funding_reversal.py` (8),
`test_wallet_spend_drawdown.py` (9), `test_economy_metrics_ad_scope.py` (7);
`src/api/__tests__/adsDelivery.test.ts` +9 (suite now 64),
`src/screens/__tests__/AdsManagerScreen.test.tsx` +3 (suite now 44).

Every one of the 9 new `adsDelivery` tests was run against `git show HEAD:` copies of
the source and confirmed to fail there. A test that passes before the fix proves
nothing about the fix, and this suite is the only thing standing between the codebase
and the reintroduction of a fake zero.

### The 18 non-passing backend results, itemised

None are caused by this phase, and each was checked rather than assumed:

- **12 failures**, all `tests.undx_agent.test_knowledge_map_grounding` — `bot.py:NNNN`
  citations in `services/undx_knowledge_map.py` that no longer resolve. Verified by
  writing `git show HEAD:bot.py` over the working copy and re-running: the failing
  citation set is **byte-identical** to the working-tree set, so the drift predates
  this branch's `bot.py` edits entirely. (The working copy was restored afterwards and
  its diffstat re-confirmed at `227/7`.) These are real and worth fixing, but they are
  a knowledge-map maintenance debt, not an advertising one.
- **1 failure**, `tests.undx_brain.test_knowledge` — the checked-in corpus index says
  `services/alert_engine.py` is 73,211 bytes; the file is 78,393. That file is
  unmodified in the working tree and 78,393 bytes at `HEAD` too, so the index is stale
  against committed code.
- **5 errors**, all import failures from missing sandbox dependencies: `flask`,
  `werkzeug`, `stripe`, and `pytest` (×2). `pip install` is blocked by the proxy in
  this environment, so these modules cannot be executed here at all.

---

## Leftovers this environment could not clean up

File deletion is not permitted on this mount, so three artifacts of the verification
process remain and should be removed by hand:

- `tests/__init__.py` — empty, created to make `unittest discover` work. Checked for
  harm: no test module in `tests/` imports a sibling by flat package name, and the repo
  has no `pytest.ini` / `pyproject.toml` / `setup.cfg` pytest configuration whose
  import mode it could change. Safe to delete.
- `mobile-native/.jest-out.txt`, `mobile-native/.tsc-out.txt`,
  `mobile-native/.jestcache/`, `mobile-native/tsconfig.scoped.json` — scratch output
  from the chunked Jest and typecheck runs.
- `.parity_write_test` — a write-permission probe.

## Not tested here

Stripe webhook delivery against the real endpoint, and the on-device behaviour of the
overdrawn banner, both need an environment this sandbox cannot provide. The reversal
path is covered by unit tests against the service layer, not by an end-to-end charge
and charge-back.
