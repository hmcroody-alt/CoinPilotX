# PulseAnalytics — attribution, privacy, performance, observability

Companion to `PULSEANALYTICS_EVENT_SOURCE_INVENTORY.md`, which established the
constraint this package was built under: production holds 111 event tables and
none of them joins exposure to outcome. This records the four decisions that
shaped the read layer, and — more usefully — where each one is *enforced*, so a
future change breaks a test rather than a promise.

Code: `services/pulse_analytics/`, `services/pulse_analytics_routes.py`
Tests: `tests/pulse_analytics/` — 64 tests, 29 mutations written, 29 caught.

---

## 1. Attribution — a path is not a cause

The evidence available is a sequence: an impression exists, later an order
exists, both name the same listing. That path is equally consistent with

* the exposure having caused the sale,
* the buyer having already decided and the placement being incidental,
* the buyer having arrived from a search engine and never seeing the placement.

Nothing in `commerce_discovery_impression_events` separates these, because the
impression row is written when a product is **rendered**, not when it is read.

So the payload does not contain the phrase "conversion rate". The terminal
figures are `orders_with_prior_exposure` and `orders_without_exposure`, the
ratio is `exposed_order_share`, and every report carries
`attribution_basis: "path_only"`. A caller shipping this to a seller as a
causal claim has to type over the disclaimer to do it.

`client_server_purchase_gap` is published rather than reconciled. When the
client reports three purchases and the ledger confirms one, which of the two is
wrong is not knowable from here, and averaging them would produce a third
number that is wrong in a new way.

| Decision | Enforced by |
|---|---|
| Outcome comes from `seller_metrics`, never the event log | `test_the_outcome_comes_from_seller_metrics_not_from_the_event_log` |
| Unconfirmed orders are excluded from both sides of the split | `test_unconfirmed_orders_are_excluded_from_both_sides_of_the_split` |
| Every report carries the disclaimer | `test_every_report_carries_the_path_only_disclaimer` |
| Funnel steps are counted independently and need not descend | `test_steps_are_counted_independently_and_need_not_descend` |

### The unmeasured/zero distinction

A rate with no denominator is `None`, never `0.0`. A listing that has never
been shown has not got a 0% click-through rate — it has not been measured, and
rendering "0%" is how a dashboard tells a seller their listing is failing when
in fact it has never appeared. The same reasoning governs `read.orders`, which
returns `None` rather than `[]` when the table cannot be read: an empty list
flows through `seller_metrics.compute` and arrives as `confirmed_orders: 0`,
which is a **claim**, and indistinguishable on the wire from a seller who
genuinely sold nothing.

Both directions are tested, because a distinction asserted in only one
direction is not a distinction.

---

## 2. Privacy — `subject_ref` does not leave the read layer

`services/commerce_discovery/subject.py` is explicit that these tables hold a
behavioural profile, and that `subject_ref` is a salted hash: not reversible,
but **stable**, which is enough to count how many times one person came back
and to join a viewer across every listing in a store.

So the `SELECT` lists in `read.py` name their columns and never use `*`, and
`subject_ref` and `session_id` are projected out at the read layer rather than
stripped at the edge. A seller-facing payload then cannot carry them even by
accident.

The operational log obeys the same rule for a different reason: it records the
*shape* of the answer (window, row counts, whether the outcome block resolved)
and no viewer identifier, no listing id and no money. An operational log that
accretes commercial detail becomes a second analytics store that nobody
declared — which is how this codebase arrived at 111 event tables.

| Decision | Enforced by |
|---|---|
| Neither identifier reaches a seller payload | `test_subject_ref_is_never_returned`, `test_no_viewer_identifier_appears_anywhere_in_the_payload` |
| The log carries no viewer or commercial detail | `test_the_operational_log_carries_no_viewer_or_commercial_detail` |

### Seller scope

The seller identity is an argument, never a filter a caller can widen. There is
deliberately no "all sellers" mode and no `seller_user_id` read off a request —
an operator view would be a different endpoint with a different authorisation
check, and the cheapest way never to ship a horizontal escalation is not to
write the code path that could become one.

Two clauses are required together — `listing_id IN (…)` **and**
`seller_user_id=?` — because the event row's seller id is a denormalised
snapshot taken at write time. If a listing changes hands, history still names
the previous owner. Requiring both means a new owner cannot read the old
owner's traffic and an old owner cannot read rows for a listing they no longer
hold; either clause alone leaks in one of those two directions, and both
directions are tested.

`_seller_id()` refuses rather than coerces. `int(1.5)` is `1` and `int(True)` is
`1`, so a float or a bool arriving on an authorisation path would not error —
it would silently address **a different seller**. This was found by its own
test, which failed against the first implementation.

---

## 3. Performance — one indexed path, two scans, stated as such

There is no index leading with `seller_user_id` on the event tables. The only
one that mentions it is `idx_cd_impr_seller_freq (subject_ref, seller_user_id,
event_at)`, which leads with the viewer because every question the frequency
cap asks is about one viewer — so it cannot serve "everything for this seller".

The scope is therefore resolved to the seller's listing ids first, and the
event query runs against `idx_cd_impr_listing (listing_id, event_at)`, which
exists. **No index was added and no shared schema was touched.**

Verified with `EXPLAIN QUERY PLAN`, pinned in `test_query_plans.py`:

| Query | Plan |
|---|---|
| impressions, as written | `SEARCH … USING idx_cd_impr_listing (listing_id=? AND event_at>?)` |
| engagements, as written | `SEARCH … USING idx_cd_engage_listing (listing_id=?)` |
| impressions scoped by seller only — *the rejected design* | `SCAN` |
| `marketplace_listings` by seller | `SCAN` |
| `seller_transactions` by seller | `SCAN` |

The last two are the honest hole, and the test asserts the defect on purpose.
Confirmed against production on 2026-09-22: **neither `marketplace_listings`
nor `seller_transactions` carries an index on `seller_user_id`.** Both are
small — 47 listings and 32 orders across the entire platform — so both scans
are free today and stop being free silently. The existing
`/api/pulse/marketplace/seller/metrics` route has always read orders this way,
so nothing here is a new scan shape.

Not fixed under this mission because the fix is a `CREATE INDEX` on two tables
every other feature also reads, which is a schema change with its own review
rather than a line smuggled in under an analytics change. The test is written
to fail when the index lands, so this note cannot go stale unnoticed.

`MAX_ROWS = 20000` bounds the event scan. The requested window is clamped to
90 days and the **measured** window is echoed back as `window_days`, never the
requested one — answering a 400-day request with 90 days of rows under the
label "400" would be this module's own headline defect.

---

## 4. Observability — a degraded answer must be countable

One line per served funnel:

```
PULSE_ANALYTICS_FUNNEL_SERVED seller_user_id=… window_days=… surface=…
  listings=… impressions=… engagements=… outcome=…
```

`outcome` is `seller_metrics.compute` or `unavailable`. That single field is
what separates "this seller had a quiet week" from "the order table would not
read", which are identical on the wire and, without this line, identical in the
logs. `listings=0` separates a seller who owns nothing from a seller whose
scope failed to resolve.

The reads fail soft and independently: an unreadable impression log yields zero
impressions and `None` rates while the money still renders, and an unreadable
order table yields `outcome.source == "unavailable"` while the traffic still
renders. Both halves are tested, in both directions.

---

## What was deliberately not built

* **No ingest, and no new table.** A 112th would not have been an analytics
  pipeline, it would have been a 112th.
* **No competing envelope.** Steps come from
  `commerce_discovery.ENGAGEMENT_ACTIONS`; a sale is
  `seller_metrics.is_confirmed_order`. Both already existed and both are
  authoritative.
* **No cache.** Recomputed per request, so there is no stale value to
  invalidate after a publish, a pause, a payment or a refund.
* **No causal claim.** See §1.
