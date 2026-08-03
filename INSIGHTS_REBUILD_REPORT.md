# Insights rebuild — completion report

Business dashboard → Sections grid → card #7 ("Insights — Delivery, spend and store
performance"). Route `BusinessOsInsights`, screen `BusinessOsInsightsScreen`.

Companion document: **`METRICS.md`** — every metric's definition, source endpoint, owner
screen, refresh cadence and reconciliation status. This report covers what was built and
what was deliberately not built; `METRICS.md` covers what the numbers mean.

---

## 1. The headline finding

The mission's defining idea is that Insights is the reconciliation point and must agree
with the screens that own each number. Holding that line changed the shape of the work
twice, and both changes are the substance of this delivery.

**First: four of the eleven metrics in the design have no source in this codebase.** Not
a slow source, not an awkward source — none. Store views, ads attribution, on-time
dispatch and reply rate cannot be measured by this platform today. The design asks for
them anyway, which is how a dashboard ends up quietly estimating. Rather than estimate,
the API returns a named list of what it cannot measure and the client renders nothing in
its place: no zero, no dash-with-a-spinner, no "—" that a seller reads as "low". The
KPI quad's third tile says "Not measured yet" in words. The fulfilment-rings module
renders one honest sentence instead of three rings, while `HealthRing` itself is built,
tested and exported so the module lights up as a data change rather than a redesign.

**Second: Insights and the Store dashboard disagree about revenue, and Store is wrong.**
This was found by reading `storeDashboard.ts` rather than assumed. Store's "Sales today"
sums order totals with *no status filter at all* — cancelled, refunded and charged-back
orders included — over a snapshot capped at the newest 100 transactions per table, and
buckets by the *device's* local day rather than the seller's timezone. Insights excludes
non-revenue statuses, reads the full window with a `WHERE` clause instead of a `LIMIT`,
and bucket-boundaries land on the seller's midnight.

Per the mission's instruction not to silently pick one, Insights was **not** changed to
match. Matching would mean adopting a bug. It is filed in `METRICS.md` §7.1 as:

> **Product bug — the Store dashboard's "Sales today" includes cancelled and refunded
> orders and is truncated to the newest 100 transactions.**

with the three-line fix stated. Until Store is fixed the two screens will differ, and
Insights is the correct one.

A third, smaller finding is recorded in `METRICS.md` §7.3: follower history is not
immutable. Unfollowing hard-deletes the `pulse_follows` row, so a past period's "new
followers" figure shrinks retroactively. The number is correct on the day it is read and
cannot be audited later. That is a schema limitation, stated rather than hidden.

---

## 2. What was built

### Backend — `services/business_os/insights/seller_analytics.py` (604 lines)

One read, one response, one set of window arithmetic. `GET /api/pulse/insights/seller/summary`
(registered in `bot.py:83198`) returns totals, prior totals, the time series, the source
split, ranked listings, follower count, currency, and the gap ledger — everything the
seven modules need, so no two modules can disagree with each other.

Why a new endpoint rather than composing from the owner screens' APIs: the existing
seller-orders endpoint applies `LIMIT 100` per table. Composing totals from it would be
exactly the forbidden pattern — analytics computed client-side from a partial cached
list and presented as a total. A seller with 250 sales in the window would have seen
100. The new module aggregates in SQL over the real window; the test suite pins this
with a 250-sale fixture that must report 250.

Window arithmetic is half-open (`start <= created_at < end`), edges land on the seller's
local midnight via a clamped `tz_offset` parameter, and the prior window is the
immediately preceding period of equal length. Buckets fold to weeks past 30 points so a
90-day axis stays readable. Runs identically on SQLite and PostgreSQL — ISO-8601 TEXT
range comparisons behave the same on both, and bucketing is done in Python.

### Client data layer

- **`src/api/insightsDashboard.ts`** (546) — typed contract, normalisation, cache,
  comparison arithmetic, and `createInsightsRequestGate`: period switches are
  cancellable via request tokens, so hammering the picker cannot race a stale response
  into the UI. Three overlapping requests resolving out of order is a pinned test.
- **`src/api/insightsRules.ts`** (285) — the two rule sets, priority-ordered, that turn
  numbers into sentences. This is the only place the screen tells a seller *what to do*,
  so it is the most heavily tested file in the delivery.

### Screen and components

`BusinessOsInsightsScreen.tsx` (906) assembles seven modules. Six new shared components,
all designed for reuse and exported from `src/components/insights/index.ts`:

| Component | Lines | Notes |
|---|---|---|
| `PeriodPicker` | 136 | Radio group; per-option `disabledReason` |
| `DualLineChart` | 260 | `react-native-svg`, two polylines, three gridlines |
| `SourceBreakdownRow` | 114 | scaleX fill anchored left via `transformOrigin` |
| `HealthRing` | 159 | Built and tested; module hidden pending data |
| `RankedListingRow` | 146 | Gold rank-1, rule-driven meta line |
| `TipCard` | 164 | One tip maximum, dismissible |

**No chart library was added.** Two polylines and three gridlines are drawn with
`react-native-svg`, which the app already ships (`StoreSparkline`, `BusinessLiveParts`).
Motion uses React Native's core `Animated` — native driver for transform and opacity,
`useNativeDriver: false` only for SVG `strokeDashoffset`, which the native driver cannot
touch. Every motion hook takes `reducedMotion` and `setValue`s straight to the final
state, so with animation off the lines are fully drawn, fills are at final width, rings
are at final value and the entrance is instant.

---

## 3. The rule sets

### Top-performer meta line — first match wins

| # | Rule | Trigger | Ships? |
|---|---|---|---|
| 1 | `attribution` | Server attributes the sale to a promotion | **No** — unreachable while `ads_attribution` is a gap. Code path exists and is tested against `unavailable: []` so wiring a real model is a data change. |
| 2 | `sold_out` | `stock === 0` | Yes |
| 3 | `low_stock` | `stock <= 5` | Yes |
| 4 | `unlisted` | Title missing, or status not active | Yes |
| 5 | `engagement` | Fallback — the order count that produced the ranking | Yes |

`stock: null` is not `stock: 0`. Null means the listing does not track stock; zero means
it is sold out. Reading one as the other would tell a seller to restock something that
was never counted. Pinned by a test on both sides.

The design's "sold out {day} — missed demand" phrasing cannot ship: the listing table
records no sell-out timestamp, and post-sellout views require view tracking, which does
not exist. The card says "sold out" without inventing a date.

### Tip card — first match wins, at most one, or nothing

| # | Rule | Trigger | Action |
|---|---|---|---|
| 1 | `restock_sold_out` | Top earner with `stock === 0` **and revenue > 0** in the period | Opens that listing |
| 2 | `restock_low` | Top earner with `stock <= 5` | Opens that listing |
| 3 | `first_sale` | No orders this period, but the seller existed last period | Opens Orders |
| 4 | `no_sales` | No orders and no prior period | Opens the create-listing flow |

A sold-out listing that earned nothing this period is **ignored** — zero stock on
something nobody bought is not proven demand, so there is no money being lost. If no
rule fires the card does not render; a dashboard that always has advice teaches the
seller to stop reading it.

**The estimate.** `weeklyRunRate(revenueMinor, period) = revenueMinor / days * 7`, using
*that listing's* revenue over the selected window — not the store's, which would inflate
it by every other listing. It is trailing arithmetic, not a forecast, and it **returns
null for the "Today" period**: multiplying one day by seven and printing "was earning
about $X a week" is a claim the data cannot support. When it is null the money clause is
dropped from the sentence rather than the tip being suppressed. The rule set returns
minor units and a `{rate}` token; the currency symbol is chosen by the screen's
localization utilities, never here.

**Dismissal** is keyed on rule *and* subject together, so silencing "restock the mug"
does not silence "restock the notebook". Cooldown is **7 days — a proposal, not a
product decision** (`TIP_DISMISS_COOLDOWN_MS`). Lapsed entries are pruned on write so
the store cannot grow without bound; a corrupt entry reads as "not dismissed", because a
tip that vanishes from a bad cache entry is worse than one that reappears.

---

## 4. MOCK-DATA / gap table

Returned by the API as `unavailable[]`, asserted in both test suites in exact order.
Nothing here renders as a zero.

| Key | What is missing | On-screen consequence |
|---|---|---|
| `store_views` | No view counter on storefronts or listings. `pulse_post_views` / `pulse_video_views` cover feed content only; nothing increments a listing view. | KPI tile reads "Not measured yet" |
| `ads_attribution` | The attribution engine is real (four models, a lookback window) but `campaign_report(model)` and `channel_report(model)` accept neither a `business_id` nor a date range — platform-wide and all-time, so no per-seller figure exists. | "From ads" row absent; two-row Store/Marketplace split ships instead. Followers card carries no composition note. |
| `on_time_dispatch` | No promised `ship_by`, no recorded `dispatched_at`. | Rings module replaced by one sentence |
| `reply_rate` | No first-response-latency metric in messaging. | as above |
| `offers_answered` | `marketplace_buyer_interest` has the right shape but is created and never written to or read from. | as above |

## 5. Feature-flag list

1. **Attribution** — `ads_attribution` gap key. Removing it from `UNAVAILABLE_METRICS`
   turns on the "From ads" source row and rule #1 of the meta rule set. Both paths are
   already tested.
2. **Export (INTERIM)** — this app has no Reports surface. Export shares a CSV built
   from *the same response the screen rendered*, never a second query, so file and
   screen cannot disagree. When a Reports flow exists this becomes a navigate call.
3. **Health-ring bands** — proposed as ≥90 green / 75–89 blue / <75 warn. No existing
   platform standard was found. Unexercised until the three metrics have sources.
4. **Tip rules lacking data** — "stale high-value listing" (needs a listing-age signal
   the ranking query does not carry) and "low offers-answered rate" (needs the offers
   table) are specified in `METRICS.md` §8 and not implemented.
5. **Tip dismissal cooldown** — 7 days, proposed.

## 6. Deviations from the brief

- **Dual y-scales**, as specified: two series normalised independently to the plot
  height. This is a trend-shape comparison, and the chart's text alternative says so.
- **Orders replaces "views" as the second line.** The brief asks for revenue-and-views;
  views are unmeasurable, so the second series is orders — a real number from the same
  rows, labelled "Orders (count)" in the legend and in the accessibility summary.
- **"Sold out {day} — missed demand"** not implemented; no sell-out timestamp, no view
  tracking. Reason above.
- **Listing deep links open the store surface** (`SellerStore`, dashboard mode) rather
  than a listing-detail route, because this app has no standalone listing-detail screen.
  A ranked row opens the place the seller can actually act on it.
- **Analytics events are not fired.** No screen-view or interaction event schema was
  found to fire into; the brief scopes this to "only where schemas exist".

## 7. Verification

| Suite | Result |
|---|---|
| `mobile-native` full jest tree, 139 suites | **2508 passed, 0 failed** |
| `src/api/__tests__/insightsDashboard.test.ts` | 30 passed |
| `src/api/__tests__/insightsRules.test.ts` | 26 passed |
| `tests/business_os/test_seller_analytics.py` | 32 passed |
| `tests/business_os/test_insights_core.py` | 9 passed |
| `tsc --noEmit` across `mobile-native` | **clean, 0 errors** |

The Python suite is standalone-runnable per house convention (`python3
tests/business_os/test_seller_analytics.py`; pytest is not installed). It builds its own
minimal tables against a temp SQLite file, so it is offline and does not depend on which
bootstrap path created `creator_transactions`.

**Two defects were found by writing the tests, and both were fixed in the source rather
than papered over in the test:**

1. `isGap` treated an *empty* `unavailable` array as "no answer" and fell back to the
   client's local ledger — contradicting `normalizeSummary`'s own documented rule that
   the client never decides what the platform can measure. It would have kept a
   now-measurable module hidden until somebody shipped a new build. Changed to a null
   check.
2. `METRICS.md` §9 documented Export as blocked offline; the code did not do it. The
   code now matches the document: the pill dims and says why. An exported CSV outlives
   the "saved {time} ago" banner — it gets attached to an email and read months later as
   a record — so shipping a stale one that looks identical to a fresh one was the
   dishonest option.

**One unrelated red test was repaired** (`StoreDashboardScreen.test.tsx`). It is not part
of this mission: an in-flight Events/Activity change repointed the header bell at the
shared unread store and at the new `BusinessOsActivity` route, and the committed test
still mocked neither. Fixed by mocking `core/unreadCounts` in that suite and adding the
now-registered route to its known-routes set. Flagged here so it is not mistaken for
Insights work.

## 8. Files

**Created:** `METRICS.md`, `INSIGHTS_REBUILD_REPORT.md`,
`services/business_os/insights/seller_analytics.py`,
`tests/business_os/test_seller_analytics.py`,
`mobile-native/src/api/insightsDashboard.ts`, `mobile-native/src/api/insightsRules.ts`,
`mobile-native/src/api/__tests__/insightsDashboard.test.ts`,
`mobile-native/src/api/__tests__/insightsRules.test.ts`,
`mobile-native/src/components/insights/` (6 components + index),
`mobile-native/src/theme/insightsLight.ts`, `mobile-native/src/theme/insightsMotion.ts`

**Modified:** `bot.py` (one route, local imports inside the handler per house
convention), `mobile-native/src/screens/BusinessOsInsightsScreen.tsx` (rebuilt),
`mobile-native/src/screens/__tests__/StoreDashboardScreen.test.tsx` (unrelated repair,
§7)

**Reused, not rebuilt:** navy `StoreHeader` chrome, `StoreKpiSkeleton` /
`StoreRowSkeleton` / `StoreSkeletonBlock`, `StoreSectionError` (inline retry),
`StoreOfflineNote`, `useStoreEntrance`, `useLogiNexusReducedMotion`, the localization
formatter bundle, the shared JSON cache helpers.

## 9. Open questions

1. **`insights-live.html` was never attached.** The brief calls it "the visual and motion
   source of truth" and instructs that it be studied before coding. It is not in the
   uploads directory and a repo-wide search does not find it. Everything visual here was
   built from the brief's prose — the token list, the seven-module structure and the
   motion spec — and from the conventions of the Store and Advertising screens. If the
   file exists, a visual pass against it is the one piece of this mission that has not
   been done.
2. **Who fixes Store's "Sales today"?** Until it is fixed the two screens disagree, and
   the seller sees it.
3. **Health-ring bands and the tip cooldown** are proposals awaiting a product decision.
4. **Screen recording** — the definition of done asks for one. It needs a simulator and
   a seller account with data; not producible from this environment.
