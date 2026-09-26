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

## How many products it carries, and why that number is small

Measured against production on 2026-09-26 — 47 published listings:

| | rows |
|---|---|
| published and approved | 47 |
| description ≥ 40 chars | 39 |
| non-empty `price_label` | 21 |
| **both — i.e. in the feed** | **14** |

A feed builder that returns 14 of 47 looks broken and is not. `price_label` is a
free-text column sellers leave blank, and a listing with no price cannot be a
Shopping offer. **The remaining 26 rows are a listing-composer gap, not a feed
bug** — the fix is upstream, in whatever lets a product be published without a
price.

Note the asymmetry this creates and keep it: an unpriced listing is a perfectly
good web page and stays in `/sitemap-products.xml`; it is only barred from
Shopping. `marketplace_seo.eligibility` returns two verdicts for exactly this
reason and the feed consumes only the narrower one. Collapsing them looks like a
cleanup and would either withhold pages from Search or submit unpriced items to
Shopping. Three tests pin it; one of them is a source-level assertion that this
module never reads `.indexable`, because a mutation probe showed the behavioural
tests alone did not catch the collapse.

## Blockers the code cannot clear

The feed being correct is necessary and not sufficient. Every item below is
owned by a person, not a commit, and the first three will cause disapproval or
suspension regardless of feed quality.

1. **Four required pages are 404 today.** Merchant Center requires reachable
   Return policy, Refund policy, Shipping and Contact pages. `/returns`,
   `/refund-policy`, `/shipping` and `/contact` do not exist. Policy *text*
   exists in `docs/marketplace_returns_refunds.md` and
   `docs/marketplace_compliance.md`, so this is publishable work rather than a
   drafting problem — but until those URLs resolve the account cannot pass
   review.

2. **Shipping is not configured.** `g:shipping` is omitted from every item on
   purpose: shipping is an account-level setting in Merchant Center, this
   codebase has no per-item shipping column, and an item-level value would
   override the account setting with a number invented here. Sending a made-up
   shipping price is precisely the consumer-protection failure this feed is
   otherwise built to avoid. **Set it in the Merchant Center account.**

3. **Checkout cannot take real money.** Stripe is in test mode and production
   has never processed a real payment. A Shopping listing whose landing page
   cannot complete a purchase is a policy violation.

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
- **`g:identifier_exists: no`** — there is no brand, GTIN or MPN column, and
  this is the mechanism Google provides for a product that genuinely has no
  manufacturer identifier. Do not "fix" it by filling `g:brand` with
  `"PulseSoc"`: PulseSoc is the marketplace, not the manufacturer, and a brand
  that disagrees with the landing page is itself a data-quality failure. The
  filler is worse than the gap.

One field is transformed: the title is truncated to 150 characters on a word
boundary, with no ellipsis. This affects 1 of 47 listings (id 14, 160
characters). Truncating preserves the leading words, where a supplier title puts
the product name; it is a different act from rewriting, which would make the
feed disagree with the page.

## Turning it on

1. Publish the four policy pages (blocker 1).
2. Configure account-level shipping and tax (blocker 2).
3. In Merchant Center, add a **scheduled fetch** pointing at
   `https://pulsesoc.com/feeds/merchant-center.xml`. Daily is right — the
   `max-age=300` only affects edge caching, not fetch frequency.
4. Expect a diagnostics pass with warnings for missing GTIN/brand. Those are
   warnings, not errors, and are the intended state (see above).
5. Verify the fetched item count matches the 14-of-47 arithmetic. A count of
   zero means the query broke, not that the catalogue emptied — the route logs
   `MARKETPLACE_PUBLIC_QUERY_FAILED` and returns a valid empty feed rather than
   a 500, so an empty feed is silent by design and has to be checked for.
