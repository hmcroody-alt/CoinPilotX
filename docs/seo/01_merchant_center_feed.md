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

Measured against production on 2026-09-26, counted with the **same predicates
the feed selects on** — lifecycle, listing approval and seller approval:

| | rows |
|---|---|
| publishable (what the query returns) | 15 |
| non-empty, parseable `price_label` | 15 |
| cover image present | 15 |
| description ≥ 40 chars | 13 |
| **in the feed** | **13** |

**Do not quote the number 47.** That is every row in `marketplace_listings`,
drafts and unapproved rows included, and it is the count an earlier draft of this
document used. Measuring before the predicates does not just inflate the total,
it inverts the diagnosis: it suggests price is what holds products back, when
among rows that actually reach the feed nothing is missing a price and
**description is the only field excluding anything**. Two rows (ids 50 and 52)
have descriptions of 2 and 0 characters. That is the listing-composer gap, and it
is two rows, not twenty-six.

One more consequence, and it is the reason the tests matter more than they look
like they should. The asymmetry this whole design rests on — a listing that is a
good web page but not a Shopping offer — has **zero live instances**. The only
two excluded rows fail the description floor, which bars them from Search as
well, so no production row currently distinguishes `indexable` from
`feed_eligible`. Keep the distinction anyway: it costs nothing and the first
seller to leave a price blank on a described product creates the case. Because no
live data exercises it, the test suite is its only defence — which is why one of
the three tests is a source-level assertion that this module never reads
`.indexable`. A mutation probe showed the behavioural tests alone did not catch
the collapse.

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
boundary, with no ellipsis. This affects 1 of the 15 publishable listings (id 14,
160 characters). Truncating preserves the leading words, where a supplier title puts
the product name; it is a different act from rewriting, which would make the
feed disagree with the page.

## Turning it on

1. ~~Publish the four policy pages~~ — done; read them once (blocker 1).
2. Configure account-level shipping and tax (blocker 2).
3. In Merchant Center, add a **scheduled fetch** pointing at
   `https://pulsesoc.com/feeds/merchant-center.xml`. Daily is right — the
   `max-age=300` only affects edge caching, not fetch frequency.
4. Expect a diagnostics pass with warnings for missing GTIN/brand. Those are
   warnings, not errors, and are the intended state (see above).
5. Verify the fetched item count is 13, per the table above. A count of
   zero means the query broke, not that the catalogue emptied — the route logs
   `MARKETPLACE_PUBLIC_QUERY_FAILED` and returns a valid empty feed rather than
   a 500, so an empty feed is silent by design and has to be checked for.
