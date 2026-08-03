# Business OS v2 — MOCK-DATA tables

Nine surfaces keep a ledger of what they draw that no backend can source. Each
ledger is an exported constant, each lives next to the code that would render
the missing field, and each is now pinned by a test so the count cannot drift
without somebody noticing. Nothing had ever collected them into one place, so
the honest total was unknown. This document is that collection.

The tables are reproduced from the constants as they stand at `61e320ce`. The
mission brief named four of them; there are nine, holding seventy-one rows
between them.

## Why these exist at all

The convention predates the v2 review. A screen that needs a number the API
cannot supply has three options: invent one, omit the module, or draw it and
label it. This codebase chose the second in almost every case and wrote down
why in a constant that a test can read, so that a completion report is
generated from the code rather than from memory. `api/storeDashboard.ts` puts it
plainly: exported "so it can be asserted in a test: if someone later fakes one
of these, the count changes and the test says so."

That mechanism is why the verdict record could quote `api/commerceInbox.ts`'s
own gap note back as the implementation spec for Tier 0.4. The ledger was
right; it just had no owner.

One structural detail worth knowing before reading further. The gap constants
are explicitly exempted from the user-facing copy audit
(`src/__tests__/userFacingCopy.test.ts:64–80`), because their `field`,
`needs`, `backendWork`, `perspective` and `gatedBy` are engineering notes under
field names no component reads. Developer vocabulary is correct in these rows
and only in these rows.

## The tables at a glance

| Constant | File | Rows | Length-locked | Locking assertion |
| --- | --- | --- | --- | --- |
| `INBOX_MOCK_DATA_GAPS` | `api/commerceInbox.ts` | 11 | yes | `api/__tests__/commerceInbox.test.ts` against the literal `INBOX_MOCK_DATA_GAP_COUNT = 11` |
| `ACTIVITY_MOCK_DATA_GAPS` | `api/activityFeed.ts` | 4 | yes | `api/__tests__/activityFeed.test.ts`, `toHaveLength(4)` against the literal `ACTIVITY_MOCK_DATA_GAP_COUNT = 4` |
| `ORDERS_MOCK_DATA_GAPS` | `api/ordersDashboard.ts` | 7 | yes | `api/__tests__/ordersDashboard.test.ts`, `toHaveLength(7)` |
| `MARKETPLACE_MOCK_DATA_GAPS` | `api/marketplaceScreen.ts` | 12 | yes | `screens/__tests__/MarketplaceManagerScreen.test.tsx`, `toBe(12)` |
| `ADS_MOCK_DATA_GAPS` | `api/adsDashboard.ts` | 9 | yes | `api/__tests__/adsDashboard.test.ts`, `toBe(9)` |
| `STORE_MOCK_DATA_GAPS` | `api/storeDashboard.ts` | 8 | yes, plus field names | `api/__tests__/storeDashboard.test.ts` |
| `INSIGHTS_MOCK_DATA_GAPS` | `api/insightsDashboard.ts` | 5 | yes, plus keys | `api/__tests__/insightsDashboard.test.ts` |
| `PAYMENTS_MOCK_DATA_GAPS` | `api/paymentsHub.ts` | 9 | yes, plus field names | `api/__tests__/paymentsHub.test.ts`, `toHaveLength(9)` against the literal `PAYMENTS_MOCK_DATA_GAP_COUNT = 9` |
| `EVENTS_MOCK_DATA_GAPS` | `api/eventsManager.ts` | 6 | yes | `api/__tests__/eventsManager.test.ts`, `toHaveLength(6)` against the literal `EVENTS_MOCK_DATA_GAP_COUNT = 6` |

**Seventy-one rows. All nine tables are now length-locked against a literal.**

### The lock that was not a lock — fixed

The first version of this document recorded a defect in the last three rows of
that table, and it is worth keeping the description because it explains what the
lock is *for*.

`ACTIVITY_MOCK_DATA_GAPS` and `EVENTS_MOCK_DATA_GAPS` each shipped with a
companion constant and a test that compared the two:

```
export const ACTIVITY_MOCK_DATA_GAP_COUNT = ACTIVITY_MOCK_DATA_GAPS.length;   // was
expect(ACTIVITY_MOCK_DATA_GAPS.length).toBe(ACTIVITY_MOCK_DATA_GAP_COUNT);    // was
```

The constant was derived from the array, so the assertion compared the array's
length to itself. It passed for every possible value and could not fail. The
same pattern appeared in `eventsManager.ts`. `PAYMENTS_MOCK_DATA_GAPS` had no
test at all — which, given that its rows are the money ones and that one of them
is a declared security gap rather than a data gap, was the most consequential of
the three.

`INBOX_MOCK_DATA_GAP_COUNT` showed what the pattern was meant to be:
`commerceInbox.ts` reads `= 11`, a literal, so the identical-looking assertion in
`commerceInbox.test.ts` is a real lock. Tier 0.4 raised that table from eight
rows to eleven and had to edit the literal to do it, which is exactly the
deliberate, visible change the convention asks for.

All three are now fixed. `ACTIVITY_MOCK_DATA_GAP_COUNT = 4`,
`EVENTS_MOCK_DATA_GAP_COUNT = 6` and a new `PAYMENTS_MOCK_DATA_GAP_COUNT = 9`
are literals, and each table is asserted against its literal in a test.
`api/__tests__/paymentsHub.test.ts` is new: besides the length it pins the exact
ordered list of field names, requires every row to state both a reason and the
client behaviour chosen in response, and pins the step-up-authentication row by
itself because it is the precondition on all six Payments flags.

`ORDERS_MOCK_DATA_GAP_COUNT` in `ordersDashboard.ts` is still derived from the
array, but Orders was never at risk: a second assertion in its test file uses the
literal `7` directly against the array.

## Messages — `INBOX_MOCK_DATA_GAPS` (11 rows)

`api/commerceInbox.ts:95`. Raised from eight rows to eleven by Tier 0.4
(`c19520c1`); the last three entries are that tier's.

| Mocked field or surface | Waiting on |
| --- | --- |
| conversation → offer/order/listing association (the context chip) | A server-side join from a conversation to its commerce object — `offer_id` / `order_id` / `listing_id` on the conversation, or a resolver endpoint. A real association always renders; only fabricated ones are behind `EXPO_PUBLIC_MESSAGES_MOCK_CHIPS`. |
| avg reply time | Per-seller median first-response latency on the inbox payload. Shown only when a real stat is present. |
| fast-responder badge / ranking rule | A defined badge threshold. No badge or ranking system exists anywhere in the app, so the incentive framing stays off behind `EXPO_PUBLIC_MESSAGES_REPLY_BADGE`. |
| away mode / auto-reply state and text | A persisted away flag and auto-reply template, applied server-side to incoming threads. Optimistic-local only until then. |
| saved-reply templates count | Saved replies stored per seller, with a count returned. Count is hidden when unknown. |
| spam / blocked filtered counts | Spam-classified and blocked thread counts exposed on the payload. |
| starred / archived conversation state | Starred and archived persisted per conversation and returned on the list. Best-effort from existing fields today; absent means the filter shows empty honestly. |
| offer expiry TTL (72h) | Confirmation of the real TTL once an offers backend exists. `OFFER_TTL_HOURS` in `api/marketplaceOffers.ts` is a proposal, not a fact. |
| `conversation_domain` (SOCIAL / MARKETPLACE / STORE_SUPPORT / DISPUTE / EVENT) | Every conversation stamped with its domain at creation and the value returned on the list, so the split stops being a client-side guess. Derived at the read boundary today: explicit field → `conversation_type` → commerce association → SOCIAL fallback. |
| returns / return requests | A returns object. There is none — no model, no route, no state machine. See the closing section. |
| store-support vs. dispute distinction on a thread | A way to tell a support question from a contested order. Only an explicit `conversation_type` can do it today, so unlabelled commerce threads all land in Marketplace. |

## Activity — `ACTIVITY_MOCK_DATA_GAPS` (4 rows)

`api/activityFeed.ts`. Length-locked against the literal `4`.

| Mocked field or surface | Waiting on |
| --- | --- |
| unified notification feed (types, read state, aggregation) | A server notification service returning one aggregated typed feed. The client synthesizes it today and the collapsing is a documented client rule. The row is tagged TOP PRIORITY in the code. |
| cursor pagination | A cursor-paginated notifications endpoint, so "See earlier" loads more. `listNotifications` takes a limit and no cursor. |
| offer amount on offer notifications | A live offers backend. Notifications carry the offer id; offer state is read from `marketplaceOffers`, which is flag-gated off. |
| aggregation window length | A server-defined collapse window. The client uses a 6h rolling window as its documented rule. |

## Orders — `ORDERS_MOCK_DATA_GAPS` (7 rows)

`api/ordersDashboard.ts:458`. Each row carries a `perspective` naming who sees
the missing field.

| Mocked field or surface | Perspective | Waiting on |
| --- | --- | --- |
| Ship-by deadline (countdown / overdue) | seller | A per-order fulfillment SLA on the live seller-orders payload. |
| Packed sub-phase | both | A packed transition. The live status collapses processing and packed into "paid". |
| Pickup scheduled + handed-off sub-phases | both | Pickup lifecycle states. The live surface has only paid vs complete. |
| Escrow hold / release (the safety panel) | both | Escrow state on the live payload. The canonical hold exists but `/api/business-os` is dark in production. |
| Payout amount per order | seller | Net-payable per order. Only payout-connect onboarding is exposed today. |
| Return-window close date | buyer | A return-eligibility deadline on the live order payload. |
| Buy-again availability | buyer | A still-purchasable / relist signal per past order. |

The first row is load-bearing beyond Orders: `api/businessHub.ts:48` and
`screens/OrdersManagerScreen.tsx:277` both cite it as the reason a due-today
count and an overdue count cannot be shown on the hub.

## Marketplace — `MARKETPLACE_MOCK_DATA_GAPS` (12 rows)

`api/marketplaceScreen.ts:62`. Three rows name a gating constant; all three of
those constants are hard-coded `false` in `api/marketplaceOffers.ts` rather than
being environment flags, so turning them on is a commit.

| Mocked field or surface | Waiting on | Gate |
| --- | --- | --- |
| Offers (make, accept, counter, decline, expire) | A `marketplace_offers` table, CRUD endpoints, and a push notification on accept. The state machine in `api/marketplaceOffers.ts` is real and tested; it has no server to talk to. | `MARKETPLACE_OFFERS_ENABLED` |
| Cart and cart badge count | Cart endpoints (add, list, remove) over the existing payments surface. Single-item checkout is real. | `MARKETPLACE_CART_ENABLED` |
| Boost purchase and price | A boost SKU and a charge that sets `listings.featured`. The column is real and already orders search results; nothing prices or sells a boost. | `MARKETPLACE_BOOST_ENABLED` |
| Per-listing views, saves and offer counts | A listing impressions endpoint and a saves aggregate per listing. Saves are stored per viewer and never aggregated. | — |
| Saved searches and new-match counts | A `saved_searches` table and a matcher recording `last_seen_listing_id`. Search is stateless. | — |
| Location strip, radius, and per-item distance | Coarse listing lat/lon plus an account radius preference — exposing distance only, never coordinates. This is the safety-sensitive one. | — |
| Seller rating and review count | A per-seller review aggregate (mean rating + count). Same missing aggregate the Store dashboard hit. | — |
| Buyer rating flow | A review write endpoint and a rating screen. No review write path exists in either direction, and no route exists to link to. | — |
| Saved meetup spots | A `meetup_spots` table and a settings screen. The code says plainly this one is a safety feature and should not ship faked. | — |
| Sold history revenue this month | An order row written when an offer is accepted. A Marketplace sale closed by accepting an offer has no order row, so revenue cannot be totalled. | — |
| Original price (strikethrough on drops) | Listing price history, or a `previous_price_label` column. `updated_at` says a listing changed but not what changed or to what. | — |
| Notification bell and buyer-message unread counts | An unread-counts endpoint scoped to marketplace conversations. | — |

## Advertising — `ADS_MOCK_DATA_GAPS` (9 rows)

`api/adsDashboard.ts:94`. Each row carries the ads `mode` it belongs to.

| Mocked field or surface | Mode | Waiting on |
| --- | --- | --- |
| Spend — last 7 days (per day) | marketplace | An analytics endpoint returning daily spend buckets. `/api/pulse/ads/analytics` returns totals and per-campaign rows only. The real total spend *is* shown. |
| Campaign learning / limited delivery phase | marketplace | A delivery-phase field on the campaign. The backend status set has no way to distinguish a delivering campaign still in its learning window; both collapse into "active". |
| Post / Reel / Live promotions | post | A post-promotion service — create, list, review status, delivered metrics. The entire Post-ads product is a flag-gated preview. |
| Outperforming-post suggestion | post | A per-post organic reach/engagement feed plus a promote-worthiness ranking. Nothing produces either. |
| Promote-a-post picker (your recent posts) | post | An authored-posts endpoint with per-post reach for the picker rail. |
| Spend / clicks windowed to the last 7 days | marketplace | An analytics endpoint accepting a date range. `getAdAnalytics` takes only an account id, so its totals are lifetime. The KPI tiles say "to date" rather than "· 7d" — the label was changed rather than the number. |
| KPI period-over-period trend (▲/▼ vs previous period) | marketplace | Windowed analytics, so the same metric can be read for the prior period. No baseline exists, so no tile shows an arrow. |
| Post-ads KPIs (reach, new followers, engagements) | post | Post-promotion delivered metrics plus follower attribution saying which follows came from a promotion. |
| Advertising notification bell + unread badge | both | Notifications filtered to an advertising category, with an unread count. The app's feed is global; putting a DM count on an ads bell would misreport it, so the bell is omitted. |

## Store — `STORE_MOCK_DATA_GAPS` (8 rows)

`api/storeDashboard.ts:49`. The most tightly locked of the nine — its test pins
the length *and* the exact ordered list of field names, so a reordering is a
failure too. The eighth row was added by Tier 0.5 with the readiness ladder.

| Mocked field or surface | Waiting on |
| --- | --- |
| Views · 7 days | A seller impressions endpoint (views per storefront per day). |
| Seller rating | A per-seller review aggregate (mean rating + count). |
| On-time dispatch % | `order.ship_by` and `order.dispatched_at`. Orders carry neither. |
| Open orders — N ship today | `order.ship_by`. Without a ship-by date there is no way to know which open orders are due. |
| Listing rating and review count | A per-listing review aggregate. Same missing data as the seller rating, keyed by listing. |
| Store open / paused | A seller-level storefront status flag. Listings pause individually; there is no store-level switch. |
| Stock tracked / not tracked | Quantity preserved as `null` through listing normalization, or an explicit `tracks_stock` flag. `normalizeMarketplaceListing` applies `Number(item.quantity \|\| 0)` before this module sees a listing, collapsing "no stock concept" and "zero in stock" into one value. |
| Store restricted / suspended | A seller-level enforcement state with its reason and appeal route. `storeReadiness` has five rungs where the review's ladder has seven; guessing these two from listing statuses would tell a seller they were suspended whenever a moderator rejected their whole catalogue. |

## Insights — `INSIGHTS_MOCK_DATA_GAPS` (5 rows)

`api/insightsDashboard.ts:183`. Locked by length and by the ordered list of
`key` values. This table is different in kind from the others: it is kept in
sync with `UNAVAILABLE_METRICS` in `seller_analytics.py`, and once a real
summary arrives from the server, `isGap` treats the *server's* list as
authoritative and this constant becomes the pre-response fallback only.

| Key | Metric | Waiting on |
| --- | --- | --- |
| `store_views` | Store and listing views | A view-tracking table for storefronts and listings. The post and video counters cover feed content only; listings carry no view counter and nothing increments one. |
| `ads_attribution` | Revenue attributed to ads | A business-scoped, period-scoped attribution read. The attribution engine is real — four models, a lookback window — but its campaign and channel reports accept neither a business id nor a date range. |
| `on_time_dispatch` | On-time dispatch rate | A promised ship-by date and a recorded dispatch time on the order. Neither exists. |
| `reply_rate` | Replies under the response threshold | A messaging metric recording first-response latency per conversation. |
| `offers_answered` | Offers answered | A live offers table. The buyer-interest table has the right shape and is never written to. |

## Payments — `PAYMENTS_MOCK_DATA_GAPS` (9 rows)

`api/paymentsHub.ts`. Length-locked against the literal `9`, plus the ordered
field names. Each row states not only the gap but the client behaviour chosen in
response, which is why this table reads longer than the others — and the test
now requires both, so a row cannot be added with a shrug in either column.

| Mocked field or surface | Waiting on | What the client does instead |
| --- | --- | --- |
| next payout date | A stored or computed payout schedule. Stripe Connect owns it and the platform never reads it back. | The hero states the destination and omits a date. The "scheduled" dot renders only for a real `payout_in_flight` row. |
| available balance becoming non-zero | Any code path that writes a credit or release ledger entry. There is none, so `available = max(0, credits − debits)` is structurally zero. Reported as `release_path: none_in_product`. | The hero renders the real zero and explains where the money actually is, rather than an unexplained $0.00 beside a non-zero Processing. |
| masked bank destination | An account number. `seller_payout_accounts` stores a Stripe connected-account id. | The payout card names the Stripe connection with a masked reference and labels it as such. No bank shape is implied. |
| instant payout fee and net | A quote endpoint. Computing the fee client-side is forbidden. | The instant-payout affordance is absent. |
| held in escrow | Per-order escrow on a reachable surface. `mkt_order_escrow:<id>` is Business OS only and that vertical is dark. | The escrow card is absent. Held money appears as Processing, which is what it actually is. |
| statements and tax documents | Anything that generates a statement or issues a tax form. | Both sections are absent — including any "no form this year" message, which would itself assert a threshold determination nothing performs. |
| ad wallet top-up and auto top-up | The Pulse Ads funding path, which sits behind three gates that all deny: `PULSE_ADS_BILLING_ENABLED`, a hard-coded `live_charging=false`, and a native-iOS 403. | The balance renders; the top-up button and auto-top-up switch are absent. |
| refund response deadline | An auto-approval policy, a deadline field, or a timer. "Respond within N days or it is auto-approved" has no N. | The banner states the dispute and its real status and links to Orders. It asserts no deadline and no consequence. |
| step-up authentication | **A security gap, not a data gap.** This app has no re-authentication primitive at all, so nothing can be gated behind one. | Every action that would require step-up is already absent for other reasons, so nothing ships unprotected today. This must be built before any Payments flag is turned on. |

## Events — `EVENTS_MOCK_DATA_GAPS` (6 rows)

`api/eventsManager.ts`. Length-locked against the literal `6`.

| Mocked field or surface | Waiting on |
| --- | --- |
| hosted-event model (lifecycle, capacity, tickets, venue/stream) | A hosted-events service. `events.ts` today is only a scheduled-live projection. Real events render; deterministic samples are behind `EXPO_PUBLIC_EVENTS_MOCK`. |
| RSVP / attendee identities + check-in records | Persisted RSVPs and check-ins per event, returned with privacy visibility. Shown only when the event carries real attendees. |
| live orders-in-last-10-min stat | A live-commerce orders channel. The live service exposes `viewer_count` only. Behind `EXPO_PUBLIC_EVENTS_LIVE_STATS`. |
| promoted-event reach / follows | Post-ad KPIs (reach, new followers) — itself a documented gap in the Advertising mission. Read from the linked campaign row's impressions today, omitted when absent. |
| attributed sales on past-event results | An attribution model. None exists; the Insights and Payments rule applies. Withheld as an em dash behind `EXPO_PUBLIC_EVENTS_ATTRIBUTION`. |
| offer/ticket TTL + waitlist | Confirmed capacity and waitlist semantics once the events backend exists. A waitlist is shown only if the platform exposes one, and it does not. |

## Which gaps to close first

Seventy-one rows is not a backlog anyone works through in order, and most of
them are correctly parked — a saved-search matcher is not urgent. Four are
different, either because a control already ships that can never work, or
because the gap blocks other work that has already been scheduled.

**The Returns filter is the clearest case and the right example to lead with.**
Tier 0.4 replaced the Commerce Inbox rail with Marketplace / Store support /
Orders / Returns / Disputes, so `COMMERCE_SPLIT_FILTERS` at
`api/commerceInbox.ts:587` now ships a Returns chip. `rowMatchesFilter` at
`:637` answers it with a hard `false` and a comment saying so, because — in the
ledger's own words — there is no returns object anywhere in the app: no model,
no route, no state machine. The filter is present, tappable, and structurally
incapable of ever showing a row. That is not a mocked number; it is a dead
control of exactly the class Tier 0.5's "Set your location" removal was written
to eliminate, shipped in the tier immediately before it. Either the returns
object gets built or the chip comes out of the rail until it does. Leaving it as
is means a seller taps Returns, sees nothing, and learns that the inbox lies
about what it can show them.

**The conversation-to-commerce join** is the dependency with the longest tail.
The verdict record already adopted this row as the implementation spec for Tier
0.4, and Tier 2's Phase 1 is sequenced around it. Until a conversation carries
`offer_id` / `order_id` / `listing_id` — or a resolver endpoint exists — the
context chip, the Orders filter, the scoped commerce badge counts and the
Marketplace unread aggregate are all blocked on the same missing join. One
piece of backend work unblocks four rows across two tables.

**Order ship-by and dispatch timestamps** appear in three separate ledgers —
Orders ("Ship-by deadline"), Store ("On-time dispatch %" and "Open orders — N
ship today") and Insights (`on_time_dispatch`). Four of the seventy-one rows are
the same two missing columns on the order record. Nothing else in the set has
that ratio.

**Step-up authentication** is listed last here because it blocks nothing today
and everything later. It is the only row in any of the nine tables that is a
declared security gap rather than a data gap, and it is a hard precondition on
all six Payments flags. It used to sit in the one table no test asserted, which
meant the single most consequential line in the ledger was the least protected.
That is no longer true: `api/__tests__/paymentsHub.test.ts` now finds this row by
name and asserts that its reason still reads as a security gap, so deleting it
has to be somebody's deliberate act. The row counts in this document are now
something the suite can defend rather than something a reader has to trust.
