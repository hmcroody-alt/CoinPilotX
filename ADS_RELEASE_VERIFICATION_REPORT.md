# Phase 10 — release verification against the advertising mission brief

**Verdict: the mission is not complete, and cannot be declared complete from this
environment.**

That is not a hedge. §37's final hard rule reads *"No release claim without
end-to-end real-device and financial verification"*, and this sandbox has no
device and no Stripe endpoint. Even if every other line of the brief were
satisfied, that one would still be open. It is not the only one open.

This document exists so the gap between what was built and what was specified is
written down by the party that did the building, rather than discovered later by
an advertiser. Every section of the brief gets a verdict. The verdicts are
deliberately unkind to my own work where the evidence warrants it.

---

## Summary of the ten phases

Phases 1–9 were not an implementation of the brief. They were a repair of the
screen the brief opens by inspecting — §0's sixteen `VISIBLE_DEFECTS` — plus the
server-side integrity work that repair kept running into. Under that heading the
work is substantially done and well evidenced. Measured against the brief as a
whole, roughly a third of the specified surface exists.

The single organising idea across all ten phases was §31's `PROHIBITED_DISPLAY`
list, and in particular the fake zero: a figure printed as a confident `0` or
`$0.00` when the truth was unknown, broken, or a debt. That idea turned out to be
load-bearing far beyond the display layer. Chasing it produced the Stripe
reversal fix (9a), the credit-bucket drawdown fix (9b) and the placement
truncation fix (8d) — all of which were server defects that a client had been
faithfully rendering as zeros and blanks.

---

## What this phase changed

Two claims from the Phase 10 audit needed checking before anything could be
written down. One was true and is now fixed; the other was false and would have
led me to damage correct code.

### The web portal printed seven zeros over its own error message — fixed

`templates/pulse_advertiser_portal.html` shipped its metric grid with `0`, `0`,
`0`, `0`, `$0.00`, `$0.00`, `$0.00` baked into the markup, so those figures were
on screen before a single byte had been fetched. `setMetric` in
`static/js/pulse_advertiser_portal.js` then defaulted a missing value to the
string `"0"` — the one branch that knows for certain a figure is unknown printed
the most confident answer available — and `renderMetrics` defeated even that with
`metrics.account_count || 0` one layer higher.

The failure path was the worst of it. `loadPortal().catch(...)` inserted an error
box above the grid and stopped, without clearing the tiles. A broken portal read
"Growth Center failed to load" directly above a full set of plausible numbers
contradicting it. An advertiser resolves that contradiction in favour of the
numbers every time, because a number looks like data and a sentence looks like
noise.

The tiles now open in `Loading…`, absence renders `Unavailable`, the failure
handler clears every tile before it renders anything, and the error box carries a
working retry — §31 forbids a generic error with no recovery action, and
"reload the page yourself" is not one. A `metric-absent` class drops those status
words out of the 30px number face so they stop reading as values.

### The campaign card's "Spent" cell was reported as a fake zero — it is not

The audit flagged `AdsManagerScreen.tsx:809` because `{ key: "spent", value:
money(spentCents) }` has no `metricState()` / `absentValueText()` branch while
its three siblings do. The asymmetry is real; the conclusion drawn from it was
wrong.

`campaignSpendCents` falls back to `campaign.spent_cents` when the analytics call
fails, and that column is not a client invention: `list_campaigns` selects `c.*`
from `pulse_ad_campaigns`, and `record_spend_event` increments exactly that
column at `services/pulse_ad_payments.py:793`. It is the same ledger the wallet
debit writes to. When analytics is down the card falls back to the authoritative
spend figure, which is why it needs no absent-state branch and why its siblings
do — impressions and clicks exist *only* in analytics.

I have left it alone. A fix here would have replaced a correct number with the
word "Unavailable" and called it an improvement.

---

## Section-by-section verdicts

Legend: **MET** — implemented and tested. **PARTIAL** — some of the section
exists and the rest is either absent or honestly labelled as absent. **NOT BUILT**
— no implementation. **UNVERIFIABLE** — cannot be tested from this environment.

### §0 Current screen inspection — **MET**

All sixteen listed defects were addressed, and this is the one section where the
work maps one-to-one onto the specification. "Spend · to da…" is no longer
clipped. The account row reads the business name over a status line and the ad
account number moved to Account details. The seven-day card takes the brief's
second option and renames itself "Account spend" whenever the backend cannot
supply per-day data — which, since `/api/pulse/ads/analytics` accepts an account
id and no date range, is always. The dash is gone in favour of §31's wording.
Audiences and Creative Library open. Post Ads no longer repeats itself.

### §1 Global advertising architecture — **PARTIAL**

Of the seven named subsystems, `financial` and `trust_and_policy` have real
server backing. `campaign_system` has Campaigns, Placements, Budgets and
Schedules but no Ad Groups, no Ads-as-entities and no Optimization.
`measurement` has impressions, clicks and spend; it has no reach, frequency,
saves, add-to-cart, checkout, purchases or ROAS. `administration` has roles
server-side and no staff surface.

### §2 Back button — **PARTIAL / UNVERIFIABLE**

Standard React Navigation back. Scroll position, filter state and draft
preservation across the sub-page boundary were never explicitly implemented and
are not covered by any test.

### §3 Advertising title interaction — **NOT BUILT**

No single-tap-to-top, no double-tap refresh, no haptic.

### §4 Ad wallet header control — **PARTIAL**

Tapping the wallet opens `BusinessOsPayments` with an `accountId`. Available,
pending, reserved and credits are all present in `wallet_summary`, and Phase 9
added `amount_owed_cents` on top of them. `WALLET_BALANCE_RULES` is the strongest
part of this build: balance is server-owned, never computed on the client, the
reversal path is keyed on the Stripe `event_id`, and `walletRollupAuthority`
refuses to present a partial total as a whole one. Absent from the overview:
spend today, spend this month, automatic refill status, wallet health.

### §5 Add funds — **PARTIAL / UNVERIFIABLE**

Top-up exists through Stripe. The five-step flow with its amount presets, fee and
tax review, and six distinct result states was not built. Idempotency on the
deposit path is implemented and unit-tested; it has not been exercised against a
real Stripe endpoint.

### §6 Automatic refill — **NOT BUILT**

Zero references anywhere in the repository — no service, no route, no column, no
screen.

### §7 Spending limits — **NOT BUILT**

Same: no account-level daily/monthly/lifetime caps, no ad-group caps, none of the
five alert types. Campaign daily and lifetime budgets exist and are enforced by
`_campaign_budget_available`, which is the nearest thing present.

### §8 Wallet transaction history — **PARTIAL**

Ledger rows exist and Phase 9 populated `pulse_ad_refunds` properly for the first
time. Eleven event types are specified; the ledger writes a subset. No transaction
detail page.

### §9 Marketplace ads tab — **PARTIAL**

The tab works and is the primary surface. Of nine specified dashboard sections,
Overview, Campaigns, Creatives and Policy Center exist; Products, Audiences (as a
dashboard section), Reports, Billing and Settings do not. No double-tap refresh.

### §10 Post ads tab — **MET**

The brief asks for a temporary state with real actions and forbids duplicate
banners, an empty page, and an active-looking tab with no purpose. Phase 1
removed the duplicate notice and gave the tab content. This is met at the level
the brief asks for it.

### §11 Advertising account row — **PARTIAL**

Opens Account details. Of seventeen specified sections roughly six exist. Nine
account states are specified; the server models a narrower set.
`STATUS_INDICATOR_RULE` — status must include text, not colour alone — is
observed.

### §12 Staff and permissions — **PARTIAL, server only**

`ACCOUNT_ROLES` and `WRITE_ROLES` exist and are enforced server-side; Phase 3b's
`resumeCheck` exists precisely because `reserve_campaign_budget` is owner-only and
a campaign manager tapping resume was told "Campaign not found." about a campaign
in front of them. But there is no staff management screen, none of the sixteen
named permissions exists as a discrete grant, and permission changes are not
audited. The three `RULES` clauses are not enforceable because the permissions
they refer to do not exist.

### §13 Spend to date, §14 Clicks to date, §15 Cost per click — **NOT BUILT (destination)**

This is the most substantial honest failure in the client and it should not be
buried. §15's display standard is met exactly — confirmed value, "No clicks yet",
"Loading…", "Unavailable" all render correctly, and that was Phase 6's work. But
all three tiles call `openReports`, which navigates to `BusinessOsInsights` under
the title **"Ad reports"**, and `BusinessOsInsightsScreen` is the *seller revenue*
screen: it calls `GET /api/pulse/insights/seller/summary` and reports orders and
revenue.

None of the Lifetime Spend Report, Click Performance Report or Cost Efficiency
Report exists. §37 requires that no visible control be a dead end; these are not
dead ends, which is arguably worse. They lead somewhere real, that is titled as
though it answers the question, and does not.

### §16 Spend last 7 days — **MET, by the brief's second option**

`SPEND_REPORT` was not built. But §0 explicitly permits renaming the card instead
of implementing daily data, and §37's "no fake seven-day report" is satisfied:
`spendWindowed` is false whenever there is no per-day source, the card titles
itself "Account spend", and the summary says "to date". Heading and figure agree.

### §17 Campaign empty state — **MET**

Title, body and both actions match. The rule that verification must not block
draft creation is implemented and tested.

### §18 Campaign list — **PARTIAL**

Four tabs (Active, Paused, Ended, Drafts) against eleven specified.
`in_review` folds into Active deliberately. Of ten quick actions, pause and
resume exist.

### §19 Business verification — **PARTIAL**

Phase 7b's fix is real and mattered: the button used to post to
`/api/dashboard/account/verification/request`, which decides a profile badge and
never touches `pulse_ad_accounts.status` — the column `select_ads` actually reads.
An advertiser could complete everything the Verification Center asked, be
approved, and still never deliver an impression. It now writes the record the
selector reads. The nine-step flow, the six advertiser types and the document
upload are not built. `CUSTOMER_COPY_RULE` is observed — server refusals surface
as sentences, not API paths.

### §20 Wallet & billing card — **PARTIAL**

Wallet overview and add funds exist. Automatic refill, payment method management,
spending limits, invoices, tax information, promotional credit management and
billing contacts do not. No invoice model exists in the ads schema.

### §21 Reports card — **NOT BUILT**

Eleven sections, seventeen metrics, nine filters, five export formats, six
reporting requirements. None of it. The card routes to the seller insights screen
described under §13.

### §22 Audiences card — **PARTIAL**

Phase 8b replaced a fabricated audience page with the targeting the server
actually enforces — country, language, device, premium and contextual category.
`TARGETING_SAFETY` is met: protected traits are not targetable because they are
not modelled, validation is server-side, and unavailable options say why. The
full library, saved audiences, retargeting, customer lists and lookalikes are not
built, and the screen says so rather than implying otherwise.

### §23 Creative library card — **PARTIAL**

Phase 2c built a real library over `pulse_ad_creatives` and
`pulse_ad_media_assets` with rights and moderation status. Format, resolution,
duration and music-rights validation are not implemented client-side.

### §24 Create campaign — **NOT BUILT as specified**

Fourteen steps are specified. What exists is the classic screen's single form:
account, objective, name, budget type, budgets, schedule, placements. There are
no Ad Groups, no policy precheck with field-level deep links, no placement
preview, no estimated results, no billing-source step. `insufficient_funds` is
partially honoured — draft saving is always allowed and activation is blocked —
which is the most important of the three sub-rules.

### §25 Campaign states — **PARTIAL**

Fourteen states specified, roughly seven modelled. `STATE_TRANSITION_RULE` is
where this build is genuinely strong: transitions are server-authoritative, the
client cannot mark a campaign active, and Phase 3b split the green pill in two so
"Delivering" appears only once `spent_cents` proves money moved rather than when
`status='active'` forecasts it. Actor/timestamp/reason/previous-state auditing is
not implemented on every transition.

### §26 Campaign rejection and appeal — **NOT BUILT**

The Policy Center shows review decisions. There is no appeal flow, no evidence
submission, no appeal tracking. §37 lists "no inaccessible policy reason or
appeal path" as a hard rule; the reason is accessible, the appeal path does not
exist.

### §27 Campaign detail — **NOT BUILT**

Twelve tabs specified. No campaign detail screen exists; campaigns are cards in a
list. `EDITING_RULES` — which edits are safe, which reset optimization, which
force re-review — is not implemented.

### §28 Policy Center — **PARTIAL**

Phase 2b built it over `review_board`. Review history and account status are
present; appeals and support cases are not. The rule "never hide whether funds
are safe" is met by Phase 9's overdrawn banner, which names the reversal rather
than saying "add funds to resume" — the wrong instruction for someone whose
payment was charged back, who would top up the debt and wonder why nothing
restarted.

### §29 Attribution — **NOT BUILT**

Nine funnel stages, seven settings, five rules. Impressions and clicks are
recorded. There is no conversion attribution, no click-through or view-through
window, and therefore nothing for refunds to adjust. §37's "no attributed revenue
left unadjusted after refunds" is *vacuously* satisfied, which is not the same as
satisfied, and should not be counted as a pass.

### §30 Advertising notifications — **PARTIAL**

`pulse_ad_notifications` exists and the portal returns unread counts. Twelve
notification types are specified and a minority are emitted. Deep links to the
exact campaign, creative, billing issue or policy decision are not built.

### §31 Error, empty, loading and restricted states — **MET on mobile, now MET on web**

This is the section the whole mission was really about, and it is the one to
hold to the highest standard.

Mobile: `normalizeAdWallet` returns early on the server's `unavailable` flag
rather than coercing its nulls to zero — without that, the client would have
re-manufactured the exact fake zero the server had just been fixed to stop
sending. `walletFigure` prints `"$0.00"` only for an explicit `0`.
`walletRollupAuthority` refuses a total the server marked partial.
`absentValueText` supplies §31's literals unconditionally, not behind
`EXPO_PUBLIC_STATE_LANGUAGE` — an earlier version shipped the correct wording
switched off, so production rendered the prohibited dash. No full-screen blanking
when cached data exists; no active-looking disabled controls.

Web: fixed this phase, described above.

Residual: the campaign card's "Spent" cell is asymmetric with its siblings. I have
argued above that it is correct. It is worth a second opinion, because the
argument depends on `pulse_ad_campaigns.spent_cents` being reliable, and its
reliability depends on `record_spend_event` being the only writer.

### §32 Backend and financial integrity — **PARTIAL, with the money paths MET**

`SERVER_AUTHORITY` is met for all twelve owned values. Of the ten
`FINANCIAL_REQUIREMENTS`: idempotent deposits, idempotent spend posting,
idempotent refunds, and reservation/release accounting are implemented and
unit-tested. Phase 9 closed the two defects that mattered most — a reversed
top-up that never debited the wallet, and spend that never touched the credit
buckets it was cleared to spend against. Double-entry, immutable posted entries,
compensating entries and support references are not implemented. Of nine
`ASYNC_JOBS`, none exists as a scheduled reconciliation job.

**One latent hazard, not a live defect.** `services/pulse_ad_payments.py:756`
builds a default idempotency key containing `now_iso()`:

```python
key = clean_text(idempotency_key or f"spend:{campaign_id}:{creative_id}:{placement_key}:{now_iso()}", 180)
```

A caller that omits a key gets one that can never collide, silently defeating the
idempotency §32 requires. The sole production caller — `record_impression` at
`services/pulse_ads_service.py:1557` — does supply a key, so nothing is broken
today. But the default is a trap: it fails open, and it fails silently. It should
raise instead.

### §33 Shared native components — **PARTIAL**

Of twenty-two named components roughly eight have equivalents. `VISUAL_STANDARD`
is largely met — clipped text fixed, status carries text and icon rather than
colour alone, duplicate messages removed, no empty dead-space pages.

### §34 Accessibility and localization — **PARTIAL, with a definite localization failure**

Accessibility: the three advertising screens carry 24 `accessibilityLabel`, 22
`accessibilityRole` and 8 `accessibilityState` props between them. Status is never
colour-only. Dynamic Type, reduced motion and RTL are not specifically verified on
these screens, and `accessibilityBaseline.test.ts` does not cover them.

Localization: **the four advertising screens contain no i18n at all.** Every
user-visible string is hardcoded English. The catalogs *do* contain 23
`commerce.ads.*` keys across eleven locales — the audit's claim that there are
zero ads keys was wrong — but not one of them is referenced anywhere in `src/`.
They are dead entries; the translators' work is shipped and unused.

Worth recording precisely because it contradicts the project's own documentation:
`CLAUDE.md` states "i18n is gated — hardcoded strings fail CI." It is not.
`npm run verify` runs `typecheck && i18n:validate && test`, and
`i18n:validate` checks catalog *consistency* — placeholder parity, plural
categories, version drift. The hardcoded-string scanner is a separate script,
`npm run i18n:hardcoded`, and nothing runs it. That is how four fully hardcoded
screens shipped through a green gate.

Currency formatting does go through `money()` and `formatters`, so the numbers
localize even though the words do not.

### §35 Test requirements — **PARTIAL**

Nine categories are specified. Wallet, campaigns and audiences have real coverage.
Reporting, permissions and accessibility have essentially none — largely because
the features they would test do not exist. Three of the four `navigation`
requirements are met; "no control reaches a placeholder-only page" is met only in
the narrow sense discussed under §13.

### §36 Implementation order — **PARTIAL**

The ten phases executed did not follow the brief's phase numbering. They followed
the defect list in §0 and then the integrity problems that repair uncovered. That
was the right call for the codebase and it does mean §36 was not followed as
written.

### §37 Definition of done — **NOT MET**

Of the thirteen `HARD_COMPLETION_RULES`:

Met: no clipped text; no duplicate unavailable notices; no fake seven-day report;
no prominent internal account ID; no client-authoritative wallet balance; no
blocked draft creation while verification pends; no sensitive audience targeting.

Partially met: no campaign activation without verification, policy, eligibility
and funding — the funding and verification gates are enforced; policy and
eligibility are enforced at review rather than at activation. No creative delivery
without rights checks — moderation status is enforced; music advertising rights
are not.

Not met: no attributed revenue left unadjusted after refunds — vacuous, since
there is no attribution. No inaccessible policy reason or appeal path — the appeal
path does not exist. No empty locked card without a useful destination — the
Reports, Spend, Clicks and CPC destinations lead to a seller revenue screen.

**Not met and not meetable here: no release claim without end-to-end real-device
and financial verification.** No device, no Stripe endpoint, no production data.

The `advertiser_journey` narrative requires twenty-three steps in sequence. It
breaks at "User creates an Ad Group", and again at appeals, invoices and
attribution.

---

## Verification evidence for this phase

| Gate | Result |
|---|---|
| `node --check static/js/pulse_advertiser_portal.js` | OK |
| `tests.pulse_ads.*` (4 modules) | **38 tests, all passing** (24 → 38) |
| New `test_web_portal_absent_states.py` | 14 tests, all passing |
| Revert-validation of those 14 against `HEAD` | **10 failures, 3 errors** |
| Full backend `unittest discover -s tests -t .` | 1770 tests — 13 failures, 5 errors, **0 attributable to this phase** |
| `npx tsc --noEmit` | exit 0 |
| `npm run i18n:validate` | OK — 11 locales, 4 pre-existing advisory warnings |
| Full mobile Jest (5 chunks) | **185 suites / 3,430 tests, all passing** |
| Advertising-surface Jest subset | 10 suites / 281 tests, all passing |

The backend count moved 1732 → 1770 because `tests/pulse_ads/` previously had no
`__init__.py` and `unittest discover` was silently skipping all of it. Those 38
tests had been written, were passing when invoked directly, and were absent from
every full-suite number reported in earlier phases. Adding the file was the fix;
the discrepancy is worth stating plainly rather than presenting 1770 as growth.

The 13 failures and 5 errors are the same set itemised in
`ADS_MONEY_RECONCILIATION_REPORT.md`: twelve stale `bot.py:NNNN` citations in
`services/undx_knowledge_map.py` (proved pre-existing at `HEAD` by writing
`git show HEAD:bot.py` over the working copy and confirming the failing citation
set is byte-identical), one stale corpus byte-count for `services/alert_engine.py`,
and five import errors from `flask` / `werkzeug` / `stripe` / `pytest` being
absent and `pip install` being proxy-blocked.

---

## What I would do next, in order

1. **Build the three report destinations** (§13, §14, §15, §21). This is the
   largest gap between what the screen promises and what it delivers, and unlike
   attribution or ad groups it needs no new data model — spend, clicks and CPC
   are already recorded.
2. **Make the default idempotency key raise** instead of embedding `now_iso()`
   (§32). Small, and it closes a trap that fails open and silent.
3. **Localize the four advertising screens** and wire the 23 dead
   `commerce.ads.*` keys (§34) — or delete them, but do not leave translated
   strings shipping unused.
4. **Add `npm run i18n:hardcoded` to `npm run verify`**, so the gate `CLAUDE.md`
   claims exists actually exists.
5. **Appeals** (§26), then **campaign detail** (§27), then **ad groups** (§24) —
   in that order, because appeals is the one §37 names as a hard rule.
6. **Attribution** (§29) last, because it is the largest and everything above it
   is cheaper per unit of advertiser harm removed.

## Leftovers this environment could not clean up

File deletion is not permitted on this mount. These are verification artifacts,
not product code, and should be removed by hand:

- `tests/__init__.py` and `tests/pulse_ads/__init__.py` — both empty, both created
  to make `unittest discover` work. **Keep `tests/pulse_ads/__init__.py`**: without
  it 38 real tests do not run. `tests/__init__.py` was checked for harm — no test
  module in `tests/` imports a sibling by flat package name, and the repo has no
  `pytest.ini` / `pyproject.toml` / `setup.cfg` pytest configuration whose import
  mode it could change.
- `mobile-native/.jest-out.txt`, `mobile-native/.tsc-out.txt`,
  `mobile-native/.jestcache/`, `mobile-native/tsconfig.scoped.json` — scratch
  output from the chunked Jest and typecheck runs.
- `.parity_write_test` — a write-permission probe.

## Not tested here, and not testable here

Stripe webhook delivery against a real endpoint. The on-device behaviour of the
overdrawn banner, the retry button, and every accessibility requirement in §34.
Multi-role testing, since the roles exist server-side but have no surface to test
through. Any financial reconciliation against production data.

§37 requires all of these before a release claim. This document is not one.
