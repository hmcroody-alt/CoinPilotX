# The Google Merchant Center product feed

`GET /feeds/merchant-center.xml` — built by `services/merchant_center_feed.py`,
served from `bot.merchant_center_feed_xml`, tested by
`tests/test_merchant_center_feed.py`.

This document is the operator's half. The code's own reasoning lives in the
module docstring and is not repeated here; what follows is what a person has to
do, and what the feed will and will not claim on their behalf.

## What the feed is

The same claims the public product page already makes, serialised a second way.
Google reads a product through two doors — the JSON-LD on
`/pulse/marketplace/<id>` for Search, this feed for Shopping — and Merchant
Center enforces its misrepresentation policy by **comparing the two**. That is
why every field is derived from `services/marketplace_seo.py` rather than
re-derived here: a second implementation of "what is the price" does not crash,
it produces a feed that validates, uploads, and quietly disagrees with the page
it points at.

Transport, all asserted in the test suite:

| | |
|---|---|
| Path | `/feeds/merchant-center.xml` (a long-lived contract — changing it reports as *stale data*, not as a 404) |
| Auth | none; `@public_route`, declared in the route-auth baseline |
| Content type | `application/xml` |
| Cache | `public, max-age=300` |
| Robots | `X-Robots-Tag: noindex,follow`, read from `search_visibility.robots_meta` so the header cannot drift from the policy table |
| robots.txt | **not** disallowed, deliberately — this is a URL we positively want fetched |

The `noindex` is a header and not a `<meta>` tag because an XML file has no
`<head>` to put one in. This was a real bug in an earlier draft of the route.

## How many products it carries, and why some are held back

Measured against production on 2026-10-03, counted with the **same predicates
the feed selects on** — lifecycle, listing approval, seller approval and
discovery visibility:

| | rows |
|---|---|
| publishable (what the query returns) | 44 |
| non-empty, parseable `price_label` | 44 |
| − description < 40 chars | −3 (ids 50, 52, 110) |
| − `price_label` disagrees with variant prices | −5 (ids 15, 35, 36, 89, 112) |
| **in the feed** | **36** |

**Do not pin a number here and verify against it.** The count moves with the
catalogue — this table said 13 on 2026-09-26 and the honest figure a week later
was 36 — so a step that reads "confirm the count is N" turns ordinary growth into
a false alarm. Count it, then reconcile the gap against the two
exclusion reasons above. What should stay stable is the *shape*: every
publishable row carries a price, and everything held back is held back for one of
those two reasons.

**Do not quote the raw `marketplace_listings` total either** (47 on 2026-09-26).
That counts drafts and unapproved rows, and measuring before the predicates
inverts the diagnosis: it suggests price is what holds products back, when among
rows that actually reach the feed *nothing* is missing a price.

### The asymmetry is live — five rows, as of 2026-10-03

Earlier drafts of this document and of the module docstring said the asymmetry
this design rests on — a listing that is a good web page but not a Shopping offer
— had **zero live instances**, and concluded that the test suite was its only
defence. Both were stale on the day they were written:
`tests/test_merchant_center_feed.py::FeedPriceMatchesCheckoutTestCase` already
records four such rows in the production feed on 2026-10-01, which is why the
guard was built. The prose was the last place still claiming zero. Re-measured
2026-10-03 the figure is five.

Ids 15, 35, 36, 89 and 112 are `indexable` and **not** `feed_eligible`: real
pages, correctly in the sitemap, correctly withheld from Shopping. They are
withheld because `price_label` — the string a human typed at publish time, and
the only thing this feed publishes as `g:price` — disagrees with
`marketplace_listing_variants.price_cents`, which is what the product page
renders and what checkout actually charges. Two of them disagree badly:

All five PDPs were fetched on 2026-10-03; the JSON-LD column is what the live page
actually publishes, not an inference.

| id | `price_label` | variant price(s) | live PDP JSON-LD |
|---|---|---|---|
| 36 | $38.00 | $2.29 (all 95 variants) | `Offer` price `2.29` |
| 35 | $35.00 | $14.33 (all 42 variants) | `Offer` price `14.33` |
| 15 | $51.74 | $15.92 – $51.74 (42 variants) | `AggregateOffer` 15.92 – 51.74 |
| 89 | $51.63 | $51.63, $56.13 (30 variants) | `AggregateOffer` 51.63 – 56.13 |
| 112 | $29.31 | $27.84 – $37.72 (4 variants) | `AggregateOffer` 27.84 – 37.72 |

Read that table the right way round. The *page* is truthful — verified against the
live site on 2026-10-03, its JSON-LD publishes the variant-derived price, so page,
structured data and checkout all agree. It is `price_label` that is stale, and
`g:price` is the one field that reads it. Had `price_label_contradicts_variants`
not held these back, the feed would have advertised $38.00 for an item that
charges $2.29 — a misrepresentation finding, not a rounding difference. The guard
is doing exactly the job it was written for, and it is doing it in production
today.

These are **two different defects** wearing one verdict, and they want different
fixes. Do not write a single ticket for all five:

- **Ids 35 and 36 are a stale label.** Every variant is one price and the label
  names a different one. Nothing about the product is ambiguous; `price_label`
  is simply wrong, and correcting it would put both products straight into the
  feed. This is the listing-composer bug — `price_label` is allowed to drift from
  the variant prices after publish.
- **Ids 15, 89 and 112 are genuinely ranged**, and the label is defensible: for 15
  it is the top of the range, for 89 the bottom. No single `g:price` can be
  truthful about a product sold at four different prices, so no edit to
  `price_label` fixes these. The structurally correct answer is the one Google
  provides for exactly this: submit each variant as its own item sharing an
  `item_group_id`, priced from `price_cents`. That is a real feature — it changes
  `g:id` from listing-scoped to variant-scoped — and it is the single largest
  distribution gain available here, since ranged products are the normal shape of
  this catalogue.

Either way:

- **The fix is upstream or structural, never in the feed's price field.** Until
  one of them lands, these five products are invisible to Shopping — the correct
  failure, but still five products of lost distribution.
- **Keep the source-level test.** One of the tests asserts this module never
  reads `.indexable`; a mutation probe showed the behavioural tests alone did not
  catch that collapse. Live data now exercises the distinction, but it exercises
  it only for as long as these five rows stay broken.

## Blockers the code cannot clear

The feed being correct is necessary and not sufficient. Every item below is
owned by a person, not a commit, and the first three will cause disapproval or
suspension regardless of feed quality.

1. ~~**Four required pages are 404 today.**~~ **Cleared in code.** `/returns`,
   `/refund-policy`, `/shipping` and `/contact` now answer 200 to an anonymous
   visitor, are linked from the shared footer of every public page, and are in
   `/sitemap-pages.xml`. Content and the reasoning behind each claim live in
   `seo/commerce_policies.py`; `tests/test_commerce_policy_pages.py` asserts both
   that they resolve and that they do not promise a returns flow this codebase
   does not have. The one thing left for a person: read them once and confirm
   they describe the business you intend to run.

2. **Shipping is not configured.** `g:shipping` is omitted from every item on
   purpose: shipping is an account-level setting in Merchant Center, this
   codebase has no per-item shipping column, and an item-level value would
   override the account setting with a number invented here. Sending a made-up
   shipping price is precisely the consumer-protection failure this feed is
   otherwise built to avoid. **Set it in the Merchant Center account.**

3. **Checkout opens, but the order tail has never run.** An earlier draft said
   "Stripe is in test mode and production has never processed a real payment".
   That was wrong as of 2026-10-03: production runs a live secret key, has taken
   29 succeeded charges totalling $420.22 including one real marketplace charge
   (listing 13, $0.50, 2026-08-23), and `GET /api/pulse/marketplace/cart/
   checkout-options` answers `{"card_payments_available": true,
   "shipping_countries": ["US"]}`.

   The true residual is narrower and still a blocker: `seller_transactions`
   has **zero** rows at `status='paid'` all-time and `marketplace_orders` is
   **empty**, so the webhook → paid → order → fulfilment tail has never executed
   end to end. A buyer arriving from Shopping can reach a card form; nobody has
   demonstrated that paying produces an order. Prove that path with a real
   low-value purchase before sending Google traffic at it.

4. **Every published listing belongs to one seller** ("M&W Store", `user_id=1`),
   with supplier-scraped titles and quantities in the thousands. That is a
   genuine misrepresentation-policy exposure independent of anything in the
   feed, and it is a business decision about what the catalogue is.

## Claims the feed makes that someone should be willing to defend

Two fields are constants rather than data, so they are assertions about the
product rather than facts read from a row:

- **`g:condition: new`** — there is no code path that creates a used listing and
  every feed row is a first-party dropship item. If a used-goods path is added,
  this constant becomes a lie; grep `CONDITION` in the module.
- **`g:identifier_exists: no`** — the mechanism Google provides for a product
  that genuinely has no manufacturer identifier. Verified 2026-10-03 that there
  is nothing to send and nowhere to get it:

  - No column named `brand`, `gtin`, `upc`, `mpn`, `ean` or `barcode` exists in
    **any** production table (`information_schema.columns`; the single ILIKE
    `%ean%` hit was `business_os_perf_summaries.mean_value`).
  - The upstream CJ Dropshipping payload has no such key either. Across 38
    distinct JSON keys sampled from `supplier_read_cache`, the only identifier is
    `sku` — CJ's own internal SKU, not a manufacturer part number. There is no
    supplier field to start importing.

  So this is not a shortcut taken instead of doing the work; the work has no
  input. Do not "fix" it by filling `g:brand` with `"PulseSoc"`: PulseSoc is the
  marketplace, not the manufacturer, and a brand that disagrees with the landing
  page is itself a data-quality failure.

  **One nuance worth knowing**, because Google's wording invites a wrong fix.
  `support.google.com/merchants/answer/6324351` (accessed 2026-10-03) says to use
  the store name as the brand and mint your own MPN *instead of* sending
  `identifier_exists: false` — but it conditions that on the merchant also being
  **the manufacturer and only seller** of an unbranded product, "for example,
  custom or homemade goods". That is not this catalogue: these are resold
  supplier items. The carve-out does not apply, so `identifier_exists: no`
  remains the correct answer rather than the discouraged one. Re-check this if
  PulseSoc ever sells something it makes.

One field is transformed: the title is truncated to 150 characters on a word
boundary, with no ellipsis. Measured on the live feed 2026-10-03, exactly one of
the 36 items sits at the cap (the longest emitted title is 146 characters), so
the path is real and rare. Truncating preserves the leading words, where a
supplier title puts the product name; it is a different act from rewriting, which
would make the feed disagree with the page.

## Certified against production, 2026-10-03

All read-only `GET`s against the live site. Recorded so the next person can tell a
regression from a thing that was never true.

| check | result |
|---|---|
| feed responds | HTTP 200, `application/xml`, `max-age=300`, `X-Robots-Tag: noindex,follow` |
| items | 36, all `g:id` unique |
| every item has `g:price` and `g:image_link` | yes, 36/36 |
| `g:price` agrees with the landing page's JSON-LD | **36/36** (single `Offer` exact match, or inside an `AggregateOffer` range) |
| `g:availability` agrees with JSON-LD `availability` | 36/36 |
| `link` matches the page's `rel=canonical` | 36/36 |
| `g:image_link` appears on its own landing page | 36/36 |
| landing pages reachable | 36/36 HTTP 200 |
| images reachable under a Googlebot UA | 36/36 HTTP 200 |
| images ≥ 500×500 (the 2027-01-31 floor) | 36/36 (smallest 500×500, id 105) |

Two gotchas that cost time and will cost it again:

- **The PDP's JSON-LD nests `Product` inside `@graph`.** A parser that scans only
  top-level `@type` finds nothing and then reports zero mismatches — a clean bill
  of health from a check that never ran. Walk `@graph`.
- **`grep -c '<item>'` under-reports** (31 against a true 36), because it counts
  lines and seller descriptions carry raw newlines. See step 5 below.

## Current Google policy, and what it means for this feed

Researched against Google's own documentation on **2026-10-03**. Recorded with
dates because three of these have deadlines, and because implementing a Merchant
integration from memory is how you build on a dead API.

### The Content API sunset does not touch this feed

`developers.google.com/shopping-content/guides/deprecation-and-sunset` — Content
API for Shopping sunset **2026-08-18**, progressive HTTP 410 from 2026-09-01,
full decommission early 2027. Merchant API **v1** is the GA successor (v1beta
discontinued 2026-02-28).

**No migration is required here, and this is the single most useful fact in this
document.** The sunset applies to *programmatic* submission. Scheduled fetch,
manual file upload and Google Sheets remain supported and unaffected — this feed
is a scheduled fetch of a static XML URL, so it never touched the Content API and
has nothing to migrate. Anyone who arrives believing PulseSoc has an urgent
Merchant API migration is reading the general advisory, not this integration.

Two caveats so the good news is not over-read:

- The feed *rules* apply to all feeds regardless of how the data arrives. Being on
  a scheduled fetch exempts you from the migration, not from the product data
  spec.
- If PulseSoc ever needs to *read* Merchant Center state back (diagnostics,
  disapproval reasons, per-item status), that requires the Merchant API, an
  `API`-type data source, and a service account. None of that exists today, and
  it is a separate build — not a migration of this one.

### Image minimum rises to 500×500 on 2027-01-31

Announced in the 2026-04-14 product data spec update; warnings since July 2026;
**enforced 2027-01-31**, across all categories and marketing methods including
free listings. Today's floors (250×250 apparel, 100×100 otherwise) are what the
catalogue is currently measured against.

Status: **currently compliant, structurally unguarded.** All 36 live items were
fetched 2026-10-03 and every one is ≥500×500 (smallest 500×500, id 105). But
nothing in this codebase enforces that floor — there is no dimension check in
`merchant_center_feed`, no check at upload, and the images are not ours: all 36
are hosted on `cf.cjdropshipping.com`. A supplier that re-encodes its thumbnails
smaller puts the catalogue under disapproval on 2027-01-31 with no local signal.
That is the one dated risk worth a calendar entry.

Related and minor: 22 of the 36 images are served with the nonstandard MIME type
`image/jpg` (rather than `image/jpeg`). Google fetches them fine today; it is
noted because it is the kind of thing a stricter fetcher would reject.

### Shipping is required, and it is an account-level task

Shipping is mandatory for free listings in the US. This is blocker 2 above and it
cannot be cleared in code — see the reasoning there for why an item-level
`g:shipping` would be worse than its absence.

### Return policy is recommended, not required

So the policy pages (blocker 1) matter for the *landing page* requirements and for
buyer trust, not as a feed-level gate.

## Turning it on

1. ~~Publish the four policy pages~~ — done; read them once (blocker 1).
2. Configure account-level shipping and tax (blocker 2).
3. In Merchant Center, add a **scheduled fetch** pointing at
   `https://pulsesoc.com/feeds/merchant-center.xml`. Daily is right — the
   `max-age=300` only affects edge caching, not fetch frequency.
4. Expect a diagnostics pass with warnings for missing GTIN/brand. Those are
   warnings, not errors, and are the intended state (see above).
5. Reconcile the fetched item count against the funnel above — do not compare it
   to a number written down here. A count of **zero** is the one unambiguous
   failure: it means the query broke, not that the catalogue emptied, because the
   route logs `MARKETPLACE_PUBLIC_QUERY_FAILED` and returns a valid *empty* feed
   rather than a 500. An empty feed is silent by design and has to be checked for.

   To count locally, count **occurrences**, not lines:

   ```sh
   curl -s https://pulsesoc.com/feeds/merchant-center.xml | grep -o '<item>' | wc -l
   ```

   `grep -c '<item>'` is wrong and will under-report — it counts matching *lines*,
   and because seller-typed descriptions carry raw newlines several `<item>` tags
   share a line. On 2026-10-03 `grep -c` said 31 where the true count was 36.
   Anything that parses the XML (or counts `<g:id>` occurrences) agrees on 36.
