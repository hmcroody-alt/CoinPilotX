# Insights — metric contract

Every number on the Insights screen (Business OS → Sections → *Insights*) is owned by
another screen. Insights is the reconciliation point, not a second source of truth. This
document is the contract: what each metric means exactly, where it comes from, who owns
it, how often it refreshes, and — where Insights and its owner screen disagree — what
the disagreement is and which side is wrong.

The rule this document exists to enforce: **a metric with no source does not render.**
Not a zero, not a dash-with-a-spinner, not an estimate. The API names its own gaps in an
`unavailable[]` array and the client hides those modules.

- Backend: `services/business_os/insights/seller_analytics.py`
- Route: `GET /api/pulse/insights/seller/summary` (`bot.py`, `api_pulse_insights_seller_summary`)
- Client data layer: `mobile-native/src/api/insightsDashboard.ts`
- Screen: `mobile-native/src/screens/BusinessOsInsightsScreen.tsx`

---

## 1. The endpoint

```
GET /api/pulse/insights/seller/summary?period=7d&tz_offset=-300&top=5
```

| Parameter | Values | Meaning |
| --- | --- | --- |
| `period` | `today`, `7d`, `30d`, `90d` | Window length in whole seller-local days. Anything else falls back to `7d`. |
| `tz_offset` | minutes, clamped to `[-720, 840]` | Minutes to add to UTC to reach the seller's wall clock — i.e. `-new Date().getTimezoneOffset()`. Period edges land on the seller's midnight, not UTC's. |
| `top` | 1–50 (default 5) | How many ranked listings to return. Clamped server-side. |

Auth is the standard `api_account_user()` session; the seller is always the calling
user, never a parameter. `Cache-Control: no-store, max-age=0` — these figures move
whenever an order lands, and a cached response would show the seller yesterday's revenue
as today's.

Every aggregate is computed **server-side over the full table** inside an explicit
half-open window. Nothing is sampled, nothing is capped, and no total is ever derived on
the device from a cached list.

### Why not the existing orders endpoint

`GET /api/pulse/payments/seller/orders` is `LIMIT 100` **per table**, newest first, with
no date range. A 90-day total derived from it is the sum of the most recent hundred rows
— an understatement that grows the better the store does. Deriving analytics from it is
the one thing the brief forbids outright, and rightly: sellers make restock and ad-spend
decisions on these numbers. That endpoint remains the owner of the *Orders list*; it is
not, and cannot be, the source of a *total*.

---

## 2. Window and comparison arithmetic

`period_bounds(period, tz_offset_minutes, now) -> (prior_start, start, end)`

- The window is **half-open**: `start <= created_at < end`.
- `end` is *tomorrow's* local midnight, so a sale made a minute ago is inside the window
  rather than a minute outside it.
- `start = end - days`, `prior_start = start - days`.
- Both bounds are converted back to UTC before they touch SQL. `created_at` is TEXT
  holding an ISO-8601 UTC timestamp on both SQLite and PostgreSQL, so the window is a
  plain string range that compares identically on both engines and uses the same index.

| Period | Days | Window (seller local) | Prior window |
| --- | --- | --- | --- |
| `today` | 1 | today 00:00 → tomorrow 00:00 | yesterday |
| `7d` | 7 | 7 days ending tomorrow 00:00 | the 7 days before that |
| `30d` | 30 | 30 days ending tomorrow 00:00 | the 30 days before that |
| `90d` | 90 | 90 days ending tomorrow 00:00 | the 90 days before that |

**Comparison basis.** Every "▲ N% vs prior" on the screen compares the window to the
**immediately preceding stretch of equal length**, and the sub-line says which stretch in
words — "up 12% vs the prior 7 days", never a bare "+12%".

**No fake baselines.** `_has_history_before(start)` asks whether the seller has *any*
transaction older than the window. If not, `has_prior_period` is false, `prior_totals` is
omitted entirely, and the screen reads **"New — no prior period"**. A seller's first week
never reports "+100%". If there is history but the prior window happened to be empty, the
screen reads "Nothing in the prior 7 days" — a different and true statement.

**Timezone.** `tz_offset_minutes` is clamped to `[-720, 840]` (UTC−12 to UTC+14). The
chart's date-range label is rendered from the same bounds the query used, so the label
and the data cannot describe different days. On the client, bucket `date` strings
(`YYYY-MM-DD`) are parsed at **local noon**, so no device offset can roll a label back a
day.

---

## 3. Status vocabulary

A transaction whose lowercased `status` contains any of these fragments did not result in
money the seller keeps, and is excluded from revenue, order counts, the series, the
source split and the rankings:

```
cancel  refund  fail  expire  charge_back  chargeback  void  dispute
```

This deliberately **extends** the client-side rule the Store and Orders dashboards
already apply (`status.includes("cancel") || status.includes("refund")`) with the
terminal failure states those screens never see, because the `LIMIT 100` list endpoint
rarely returns them. See §7 — this extension is a real, deliberate divergence.

---

## 4. Source classification

Each sale is labelled `store` or `marketplace` by `_source_of(seller_type, item_type)`:

1. If `item_type` contains `listing`, `product` or `marketplace` → **marketplace**
2. else if `seller_type` contains `merchant`, `marketplace` or `seller` → **marketplace**
3. else → **store**

Item type wins over seller type, because a merchant selling a creator product is making a
creator sale. The classification lists are exported constants and echoed in the payload,
so the rule is auditable rather than a hidden opinion.

---

## 5. Metrics that ship

| Metric | Exact definition | Source | Owner screen | Refresh |
| --- | --- | --- | --- | --- |
| **Revenue** | Sum of `gross_amount_cents` (`creator_transactions`) and `amount_cents` (`seller_transactions`) for rows where `seller_user_id = me`, `start <= created_at < end`, and `_counts_as_sale(status)`. Minor units, no float. | `totals.revenue_minor` | Orders | On period change, pull-to-refresh, screen mount |
| **Orders** | Count of the same rows. One transaction = one order. | `totals.orders` | Orders | as above |
| **Revenue & orders series** | The same rows bucketed by seller-local day. `bucket_days = 1` when the span is ≤ 30 days, `7` otherwise (`MAX_DAILY_BUCKETS = 30`). Empty buckets are emitted as zero, so gaps read as "no sales" rather than as missing data. | `series[]` (`date`, `revenue_minor`, `orders`) | Orders | as above |
| **Where sales came from** | The same rows grouped by `_source_of(...)`, each with revenue and order count. Two rows: *Your store*, *Marketplace*. | `sources[]` | Store / Marketplace | as above |
| **New followers** | `SELECT COUNT(*) FROM pulse_follows WHERE followed_user_id = me AND start <= created_at < end`. | `followers.gained` | Advertising | as above |
| **Top performers** | The window's sales grouped by `item_id`, ranked by revenue desc, tie-broken on order count then `item_id`. Decorated from `marketplace_listings` with `title`, cover image, `status` and `stock` (`quantity`, when numeric). | `top_items[]` | Store (listing) | as above |
| **Currency** | The currency carrying the most revenue in the window. Every distinct currency seen is returned in `currencies[]`. | `currency`, `currencies[]` | — | as above |

**Refresh cadence, stated once:** the screen queries on mount, on every period change,
and on pull-to-refresh. There is no polling and no background timer. Every module reads
from one response, so no two modules on screen can be from different instants. Period
switches are guarded by a request token (`createInsightsRequestGate`) — a slow response
for an abandoned period is discarded, never rendered.

### Top-performer meta lines

The meta line under each ranked listing is chosen by a **prioritized rule set**
(`mobile-native/src/api/insightsRules.ts`, `itemMeta`), first match wins. Priority runs
from most actionable to least, because there is one line and the seller should get the
fact that changes their next decision.

| # | Rule | Trigger | Text | Tone |
| --- | --- | --- | --- | --- |
| 1 | `attribution` | `ads_attribution` is *not* in `unavailable[]` **and** the item carries a `promoted_by` | "Boosted by {source}" | neutral |
| 2 | `sold_out` | `stock === 0` | "Sold out — buyers can't order it" | warn |
| 3 | `low_stock` | `stock > 0 && stock <= 5` (`LOW_STOCK_THRESHOLD`, matching the Store screen) | "Only {n} left" | warn |
| 4 | `unlisted` | `title === null`, or `listing_status` is not `active` | "Listing no longer available" / "Listing is {status}" | neutral |
| 5 | `engagement` | fallback | "{n} orders" | neutral |

Rule 1 is **unreachable today** — no field carries the link, because `ads_attribution` is
a documented gap (§6). It is written rather than omitted so that wiring attribution later
is a data change, not a redesign.

`stock === null` means the listing does not track stock at all and is never read as zero.
Each line is a fact from the payload; none is computed from a heuristic.

---

## 6. Metrics that do **not** ship

The API returns these in `unavailable[]`, each naming the concrete change that would fix
it. The client renders nothing in their place.

| Key | Label | What is missing |
| --- | --- | --- |
| `store_views` | Store and listing views | No view-tracking table for storefronts or listings. `pulse_post_views` / `pulse_video_views` cover feed content only; `marketplace_listings` has no view counter and nothing increments one. |
| `ads_attribution` | Revenue attributed to ads | The attribution engine is real (four models, a lookback window) but `campaign_report(model)` and `channel_report(model)` take neither a `business_id` nor a date range — they are platform-wide and all-time. No per-seller, per-period "From ads" figure can be taken from them. |
| `on_time_dispatch` | On-time dispatch rate | Needs a promised `ship_by` and a recorded `dispatched_at` on the order. Neither column exists. |
| `reply_rate` | Replies under the response threshold | Needs a messaging metric recording first-response latency per conversation. |
| `offers_answered` | Offers answered | Needs a live offers table. `marketplace_buyer_interest` has the right shape but is created and never written to or read from. |

Consequences on screen:

- **Store views KPI** renders as a tile reading "Not measured yet" rather than "0".
  A zero here would be a measurement claim the platform cannot make.
- **"From ads" source row does not ship.** Per the brief, the breakdown falls back to the
  honest two-row Store/Marketplace split. No client-side attribution heuristic was
  invented; sellers make spend decisions on that number.
- **Fulfillment health rings do not render.** All three ring metrics are gaps, so the
  module renders one sentence explaining that dispatch and response times are not
  measured yet, rather than three em-dashes that would read as a load failure. The
  `HealthRing` component is built, tested and exported, ready for the day a source
  exists.

---

## 7. Reconciliation status

The brief requires Insights to agree with the screens that own each number, and requires
any genuine backend-level disagreement to be flagged as a product bug rather than
silently resolved. Three disagreements exist.

### 7.1 Revenue vs. Store's "Sales today" — **discrepancy, Store is wrong**

`deriveKpis` in `mobile-native/src/api/storeDashboard.ts` computes `salesTodayMinor` by
summing `orderMinorAmount` over **every** cached order in today's bucket, with **no
status filter at all** — the `cancel`/`refund` exclusion is applied in `isOpenOrder` and
`unitsSoldByListing`, but not in the revenue sum. It also sums over the `LIMIT 100`
cached snapshot and buckets by **device** local day.

So for the same day, Store can show a *larger* figure than Insights (it counts refunds)
and, for a busy store, a *smaller* one (it only sees the newest hundred rows). Insights
is the correct number on both counts.

> **Product bug — Store dashboard "Sales today" includes cancelled and refunded orders
> and is truncated to the newest 100 transactions.** Fix: filter the sum with the same
> exclusion the rest of that file already uses, and read the total from
> `/api/pulse/insights/seller/summary?period=today` instead of from the cached list.

Insights was not changed to match. Matching would mean adopting a bug.

### 7.2 Status vocabulary — **deliberate extension, documented**

Insights excludes eight status fragments; Store and Orders exclude two. The extra six
(`fail`, `expire`, `charge_back`, `chargeback`, `void`, `dispute`) are terminal failure
states, none of which is money the seller keeps. The owner screens do not exclude them
only because their `LIMIT 100` newest-first list rarely surfaces them.

This is a superset, not a contradiction: any order Store counts as a sale, Insights also
counts, except the six failure states — which Store would also exclude if it saw them.
The Store fix in 7.1 should adopt the same eight-fragment list.

### 7.3 New followers vs. Advertising — **no owner metric exists**

The Advertising screen has no per-period follower metric; `adsDashboard.ts` already tags
the post-ads KPI trio (reach, new followers, engagements) as a documented gap. The only
follower figure anywhere is `profile.follower_count`, a lifetime `COUNT(*)` over
`pulse_follows`.

Insights' "New followers" is therefore the first per-period measurement of it, and there
is nothing to contradict. One caveat worth knowing:

> **Known limitation — follower history is not immutable.** An unfollow **deletes** the
> `pulse_follows` row; there is no tombstone. A follow gained in June and undone in
> August disappears from June's count retroactively. Historical "new followers" figures
> can therefore shrink. Fixing this needs a soft-delete or an event log on
> `pulse_follows`.

### 7.4 Everything else — **no conflict**

Store views, ads attribution, on-time dispatch, reply rate and offers answered do not
render on Insights at all (§6), so there is nothing to reconcile. Orders count and the
series come from the same rows and the same predicate as Revenue, so they cannot drift
from it. The source split is a partition of exactly those rows: the two source revenues
sum to total revenue by construction.

---

## 8. Tip card

At most **one** recommendation renders, chosen by a documented priority rule set
(`insightsRules.ts`, `selectTip`), first match wins. **If no rule fires, the card does
not render** — a dashboard that always has advice teaches the seller to stop reading it,
so the bar for appearing is "there is a specific thing to do, about a specific thing".

| # | Rule | Trigger | Action |
| --- | --- | --- | --- |
| 1 | `restock_sold_out` | a ranked listing with `revenue_minor > 0` and `stock === 0` | opens that listing |
| 2 | `restock_low` | a ranked listing with `0 < stock <= 5` | opens that listing |
| 3 | `first_sale` | `totals.orders === 0`, nothing ranked, and the seller **has** prior history | opens Orders |
| 4 | `no_sales` | `totals.orders === 0` and the seller has **no** prior history (new store) | opens the listing composer |

**The only number a tip quotes**, and its calculation:

```
weeklyRunRate(revenueMinor, period) = round(revenueMinor / periodDays * 7)
```

where `revenueMinor` is **that listing's** revenue in the window — not the store's. It is
trailing arithmetic on what already happened, so the copy says "was earning about {rate}
a week", never "will earn". It returns `null` for `today`, because one day extrapolated
to a week would multiply a single sale by seven and print it as a weekly rate; when it is
`null` the tip drops the clause rather than showing a figure. Anything cleverer — a
growth curve, a seasonality factor — would be a forecast this platform has no business
making from one period of one seller's sales. The figure renders through the same
currency formatter as every other number on the screen.

**Rules the design asks for that are not implemented:** "stale high-value listing" needs a
last-sold-at per listing, which this endpoint does not query; "low offers-answered" needs
the offers table that exists but is never written to. Both are named here rather than
approximated.

A dismissed tip stays dismissed **for that rule + subject pair** for
`TIP_DISMISS_COOLDOWN_MS` (7 days), persisted under `pulse.insights.tipDismissals.v1`.
Dismissing "restock *Blue Mug*" does not suppress "restock *Red Mug*", and neither
suppresses a different rule. `recordDismissal` prunes lapsed entries as it writes, so the
store cannot grow without bound.

> The 7-day cooldown is a **default, not a product decision** — nothing in the product
> defines one. Flagged for confirmation.

---

## 9. Export

The Export pill shares a CSV built from **the same `summary` object the screen just
rendered** — never a second query. The file and the screen therefore cannot disagree. It
carries the window, the currency, the date/revenue/orders series, the source rows and the
ranked listings.

> `INTERIM`: the brief routes Export to a Reports surface. No Reports surface exists in
> this app, so Export uses the platform's existing share sheet. When Reports ships, this
> should become a deep link into it.

Export is disabled — with a stated reason, not silently — when the screen is showing
cached data offline, because an export is a document the seller keeps and it must not be
quietly stale.

---

## 10. Offline and cache behaviour

Responses are cached per period. When the screen is served from cache it shows a
"last updated {time}" note, and the period picker disables the periods that are not in
the cache, each with a reason on the disabled pill rather than a silent no-op. A period
the backend cannot serve is disabled the same way. No period is ever faked.
