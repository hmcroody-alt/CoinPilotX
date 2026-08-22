# Business OS Performance Constitution

This document is **subordinate to** `PULSESOC_PERFORMANCE_CONSTITUTION.md`. It
adds nothing that contradicts it and repeats nothing it already says. Where the
two could be read as disagreeing, the general constitution wins.

It exists because Business OS has a property no other surface has: it is a
*launcher* whose tiles are themselves full screens, and the seller's judgement
of whether "the business tile is fast" is formed by the slowest tile, not the
average one. A hub that paints in one frame and then opens onto a tile that
blanks for two seconds has not made the workspace feel fast; it has moved the
wait one tap later.

The three rules below are the ones that had to be written down because the code
violated them in more than one place, in the same way, for the same reason.

---

## Rule B1 — "Loading" means *there is nothing to show*, not *a request is running*

These are different questions and conflating them into one boolean is the single
most common defect in this subsystem. It was present at the hub, on Advertising,
on the Store and on Marketplace Manager independently.

The symptom is always the same and always worse than slowness: the user acts on
a control, and the page containing that control disappears and comes back. On
Advertising, pausing a campaign unmounted the campaign list, the ad-account form
and the new-campaign form, because every render gate read `!loading && model` and
every mutation ended with `await load()`. The seller touched a switch and the
screen answered by going away.

A screen therefore carries two separate pieces of state:

- `loading` — nothing has ever arrived. Renders a shell or a skeleton. Set once,
  cleared once, never set true again.
- `refreshing` — a request is in flight over data that is already on screen.
  Renders a *small, additive* indicator. Never removes anything.

A refresh is additive. It may add an indicator and it may replace values with
newer values. It may not subtract.

*Enforced by:* `src/screens/__tests__/businessOsTiles.perf.test.tsx` — "keeps the
campaign list mounted while the post-mutation reload runs"; and
`BusinessOsScreen.perf.test.tsx` — "keeps the numbers on screen while a refresh
runs instead of blanking them".

## Rule B2 — One write must not produce N loads

Business OS subsystems fan out. A single marketplace write invalidates
`seller_inventory`, `marketplace` and `orders`, and every screen here subscribes
to all three. Ungated, one act of publishing a listing ran three concurrent
copies of a three-request load — nine requests for one event — on each mounted
screen.

Two obligations follow:

1. **An in-flight guard.** A load already running is returned, not restarted.
2. **The guard must be keyed by the question being asked, not by a bare
   boolean.** Marketplace Manager takes a search query. Deduplicating a search
   submit onto an in-flight request for the *previous* term answers the user
   with results they did not ask for — a correctness bug bought with a
   performance win, which is the trade this constitution exists to refuse.

A related failure has the same root: a `load` callback that closes over changing
state gets a new identity on every change, and any effect listing it as a
dependency tears down and re-registers its subscriptions. Marketplace Manager
re-registered all three invalidation channels **once per keystroke** in the
search box. Read volatile inputs through a ref and keep the callback stable.

*Enforced by:* `businessOsTiles.perf.test.tsx` — "collapses concurrent sync
invalidations onto a single reload" and "registers each invalidation channel
exactly once"; `BusinessOsScreen.perf.test.tsx` — "collapses concurrent sync
invalidations onto a single reload".

## Rule B3 — A failure may only void the request that failed

The general constitution's Rule 2 says a screen must not block on an optional
dependency. Business OS needs the stronger form, because the violation here was
not blocking — it was **discarding data that had already arrived**.

The Store screen ran its store snapshot and its commercial-terms request inside
one `try`. `loadSellerStoreSnapshot` resolves even when its own requests fail, so
in practice the only thing that could reach the `catch` was the *terms* call —
and the catch overwrote listings and orders that had landed successfully with
older cached copies, then told the seller they were offline. A terms-endpoint
hiccup made a healthy store look disconnected and misreported its inventory.

So: independent requests are settled independently (`allSettled`), each result is
applied to its own slice, and a rejection touches nothing but its own slice.

The same principle governs cache writes. `loadSellerStoreSnapshot` substituted
empty arrays for failed requests and then wrote that snapshot to disk — a
network failure replaced a good cache with a picture of a business that has no
listings and no orders. Empty is indistinguishable from "genuinely has none", so
**only a fully-answered response is persisted**, and the snapshot now carries an
explicit `live` flag so callers can tell an authoritative picture from a
degraded one instead of inferring it from whether the arrays are empty.

*Enforced by:* `businessOsTiles.perf.test.tsx` — "keeps canonical listings when
the independent terms request fails"; `src/api/__tests__/sellerStoreSnapshotCache.test.ts`
— all three cases.

---

## The financial boundary in this subsystem

Rule 1 of the general constitution — cache may be displayed, never authority —
has specific teeth here, because Business OS is where PulseSoc's money is. The
following are **never** served from cache as authority, regardless of what it
costs in perceived latency:

| Value | Where | Why |
|---|---|---|
| `overview.available_cents` | Payments | Prefills and bounds the withdraw amount |
| `connect.payouts_enabled` | Payments | Gates whether the withdraw control exists |
| ad wallet balance / `fundingLive` | Advertising | Spendable money |
| `account.status` → `needsVerification` | Advertising | An entitlement gate |
| commercial terms `acceptance` | Store | An entitlement gate |
| `owner.verification.state`, `owner.locks` | Business Profile | Entitlement / field locks |

The Store screen's cache hydration deliberately paints listings and orders and
deliberately does **not** touch terms acceptance: an entitlement read from cache
is an entitlement granted by a stale file. Likewise a failed terms read leaves
the previous value alone rather than defaulting — a failure never grants.

Payments was audited and **deliberately left alone**. Its cold-start gate does
remove the whole content region, but every number behind that gate is in the
table above. The only safe fix there is shaped placeholders that show no
figures, which is a design change and outside this mission's scope. It is
recorded as debt below rather than "optimised" into a hazard.

---

## Rule B4 — A measurement is reported with the conditions that produced it

The backend fix in this mission removed an N+1 in the seller-orders endpoint:
one `SELECT` per order against `marketplace_commercial_settlements`, replaced by
a single batched `IN (...)` and an in-memory join. Business OS home calls that
endpoint just to display an order count, which made the N+1 the most expensive
thing behind a number the seller reads in one glance.

**What is certain:** the query count per load went from *N + 1* to *2*,
independent of network conditions. That is a structural property of the code and
it is the claim worth making.

**What is not certain** is any latency figure attached to it. The round-trip
measurement was taken with `scripts`-style probes running *locally* through
Railway's public proxy (`yamabiko.proxy.rlwy.net`), not from inside the
production network. Two runs of the identical probe returned p50s of **138 ms**
and **532 ms**. Those numbers bound the problem from above; they are not the
production number and must never be reported as one. In-network, a round trip is
a small fraction of that.

**What is also true and easy to overstate:** at the time of measurement
`marketplace_commercial_settlements` held **0 rows**. The eliminated queries were
therefore costing round trips, not data transfer. The saving scales with the
number of *orders* a seller has, not with the size of that table, and it will
grow as the table fills.

State all three of these, or state none of them. A latency number without its
conditions is the kind of claim this constitution exists to prevent — see the
general constitution's anti-goals.

---

## Known debt — measured, not fixed

Recorded here so the next mission starts from evidence instead of re-auditing.

1. **Count-only reads fetch whole objects.** The hub renders four integers.
   Reaching them pulls `listMarketplaceSellerListings({ limit: 80 })` — 23+
   columns per row including `description` and `gallery_json` — plus the full
   seller-orders list. A `?count_only=1` projection, or a single
   `/api/business-os/summary` endpoint, would cut this payload substantially.
   Not done here because it needs a backend contract change and this mission's
   scope lock forbade widening past performance work already justified by
   evidence.

2. **Unvirtualised lists.** `MarketplaceManagerScreen` renders up to 80 seller
   rows through `.map()` inside a `ScrollView` (the Selling pane, once
   expanded); Advertising renders up to ~100 `CampaignCard`s the same way;
   Payments' ledger and payouts lists grow unbounded through "Load more". The
   Buying pane and `MarketplaceScreen` already use `FlatList` and are the model
   to follow.

3. **Advertising has no cached first paint** and its loader is a serial cascade:
   `getAdsPortal()` must fully fail before the fan-out starts, and the fan-out is
   itself two serial rounds. Adding a cached paint is safe for campaign names,
   objectives and lifecycle — but the wallet must stay uncached (`adsDashboard.ts`
   already refuses it; keep that), and a cached `account.status` must never be
   allowed to *hide* the verification banner.

4. **Insights' cache is failure-only.** Safe to make read-always — it is
   reporting data, not a gate — provided it is labelled with an "as of" time.
   Note that its pre-fetch AsyncStorage read is **deliberate**, not a defect: it
   restores the seller's saved period so a 30-day user never sees 7-day numbers
   flash first. Removing that read would trade a correctness property for a
   fraction of one bridge hop. Do not "fix" it.

5. **`MarketplaceManagerScreen` never writes the seller cache.** It calls
   `listMarketplaceSellerListings` and `listMarketplaceSellerOrders` directly
   rather than going through `loadSellerStoreSnapshot`, so `cacheSellerStore` is
   never invoked from this screen. Its own offline fallback therefore only works
   if the Store screen happened to run first.
