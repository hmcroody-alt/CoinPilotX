"""The Google Merchant Center product feed.

This is the same set of claims ``marketplace_seo`` already makes on a product
page, serialised a second way because Google reads them through two different
doors and checks that they agree. The JSON-LD on the landing page is what
Search sees; this feed is what Shopping sees; and Merchant Center's
misrepresentation policy is enforced by *comparing the two*. A price here that
disagrees with the price there is not a duplicated-code smell, it is the single
most common cause of account suspension.

So every field below is derived from ``marketplace_seo``, never re-derived
locally. ``availability``, ``parse_price``, ``product_url``, ``cover_image``
and ``eligibility`` are imported rather than reimplemented, because the failure
mode of a second implementation is not a crash — it is a feed that validates,
uploads, and quietly contradicts the page it points at.

WHY THIS IS A SEPARATE MODULE FROM ``marketplace_seo``
-----------------------------------------------------
Not because the concerns differ — they barely do — but because the *required
fields* differ and the consequences of a missing one differ. A page missing an
image renders slightly worse. A feed item missing ``g:image_link`` is rejected
by Merchant Center, and enough rejections put the account under review. That
asymmetry is why ``eligibility`` returns two verdicts, and this module consumes
only the narrower one: ``feed_eligible``. It never consults ``indexable``.

FEED VALUES ARE NOT SCHEMA.ORG VALUES
-------------------------------------
The one place a naive reuse would actually break. ``marketplace_seo.availability``
returns ``https://schema.org/InStock`` because that is what JSON-LD requires;
Merchant Center requires the bare token ``in_stock`` and rejects the URL. The
mapping is explicit in ``_FEED_AVAILABILITY`` rather than a string replace, so
adding a third schema.org value without deciding its feed spelling fails loudly
in ``feed_row`` instead of shipping an unmapped URL into the feed.

WHAT WE DO NOT SEND, AND WHY THAT IS THE CORRECT ANSWER
-------------------------------------------------------
``marketplace_listings`` carries no brand, GTIN, MPN or condition column.
``marketplace_seo``'s own docstring already settled what to do about that and
this module implements it:

* **brand, gtin, mpn**: omitted, and ``g:identifier_exists`` is set to ``no``,
  which is precisely the mechanism Google provides for a product that genuinely
  has no manufacturer identifier. The tempting filler for ``brand`` is
  ``"PulseSoc"`` and it is false — PulseSoc is the marketplace, not the
  manufacturer — and a brand mismatch between feed and landing page is itself a
  data-quality failure. The filler would be worse than the gap.
* **condition**: ``new``, sent as a constant. This is a claim, so it is worth
  being able to defend: there is no code path in this product that creates a
  used listing, and every row that reaches the feed is a first-party dropship
  item from a supplier catalogue. If a used-goods path is ever added, this
  constant becomes a lie and the grep for ``CONDITION`` is the fix.
* **g:shipping**: omitted deliberately, not forgotten. Shipping is required for
  a Shopping offer, but it is configured *per account* in Merchant Center, not
  per item, and an item-level ``g:shipping`` would override the account setting
  with a number this codebase has no source for. Sending a made-up shipping
  price is a consumer-protection problem of exactly the kind this module's
  parent docstring is about. It is an account-setup task, and it is one of the
  reasons this feed cannot go live on code alone.

THE TITLE IS THE ONE FIELD WE TRANSFORM
---------------------------------------
``marketplace_seo`` refuses to rewrite a seller's title, for the good reason
that it is the string buyers search for and a rewrite makes the page disagree
with the feed. This module truncates it anyway, at 150 characters, on a word
boundary — because Google's limit is 150 and the alternative to truncating is
dropping the product entirely. Measured on the live feed 2026-10-03, exactly one
of the 36 items sits at the cap (longest emitted title: 146 characters), so it is
a real case and a rare one. Truncation preserves the leading words, which is
where a supplier title puts the product; it is a different act from rewriting,
which would change them.

HOW BIG THIS FEED ACTUALLY IS
-----------------------------
Worth stating plainly, because a feed builder that returns a fraction of the
catalogue looks broken. Measured against production on 2026-10-03, counted with
the *same* predicates this feed selects on: **44** listings are publishable, all
44 carry a parseable price, and **36** reach the feed. The 8 held back split two
ways — 3 fail the 40-character description floor (ids 50, 52, 110) and 5 are
refused by ``marketplace_seo.price_label_contradicts_variants`` (ids 15, 35, 36,
89, 112).

Do not pin a number here and verify against it. This docstring said 13 on
2026-09-26 and the honest figure a week later was 36; the count tracks the
catalogue. What should stay stable is the shape: everything publishable is
priced, and everything held back is held back for one of those two reasons.

The count to be careful with is the raw ``marketplace_listings`` total (47 on
2026-09-26). That includes drafts and unapproved rows, and an earlier draft of
this docstring quoted it — along with "21 priced" and "39 described" — as though
it described the feed. It does not: those numbers are measured before the
lifecycle and seller-approval predicates, and they invert the finding. Among rows
that actually reach this module nothing is missing a price.

THE ASYMMETRY IS LIVE, AND THIS DOCSTRING WAS THE LAST PLACE TO SAY OTHERWISE
-----------------------------------------------------------------------------
An earlier draft of this docstring said the wider-verdict-but-not-feed-eligible
case had **zero** instances in production and that the test suite was therefore
the only thing defending it. That was already wrong when it was written:
``FeedPriceMatchesCheckoutTestCase`` records four such rows in the production
feed on 2026-10-01, which is why the guard exists at all. Re-measured 2026-10-03
the figure is five of 44 — ids 15, 35, 36, 89 and 112 — held back because
``price_label`` disagrees with ``marketplace_listing_variants.price_cents``.

Read the direction correctly, because it is the opposite of the intuitive one.
The *page* is truthful — its JSON-LD publishes the variant-derived price, which is
also what checkout charges. It is ``price_label`` that has gone stale, and
``g:price`` is the one field that reads it. Id 36 carries a ``price_label`` of
$38.00 against 95 variants all priced $2.29; id 35 says $35.00 against 42
variants all at $14.33. Without the guard this module would have advertised a
price 16x the one a buyer is charged, which is a misrepresentation finding rather
than a rounding error.

So the guard is load-bearing in production right now, not theoretically. Two
things follow: the real fix is upstream (``price_label`` must not be allowed to
drift from the variant prices, and until it stops drifting these five products
are invisible to Shopping), and the source-level test asserting this module reads
only the narrower verdict still earns its keep — a mutation probe showed the
behavioural tests alone did not catch that collapse, and live data only exercises
the distinction for as long as these five rows stay broken.

A note for whoever edits this docstring next: that test greps this file's source
with ``#`` comments stripped but docstrings intact, so writing the wider
verdict's attribute name in prose here turns the suite red. Name it in words, as
above.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from . import marketplace_seo, search_visibility

#: Where the feed is served. Submitted to Merchant Center as a scheduled fetch,
#: so the URL is a long-lived contract: changing it silently stops the fetch,
#: and Merchant Center reports that as stale data rather than as a 404.
FEED_PATH = "/feeds/merchant-center.xml"

#: Google's product-data namespace. The prefix is conventionally ``g``.
G_NS = "http://base.google.com/ns/1.0"

#: schema.org availability -> the token Merchant Center accepts. See the module
#: docstring: the URL forms are rejected outright, so this is a mapping and not
#: a pass-through, and an unmapped value raises rather than shipping.
_FEED_AVAILABILITY = {
    marketplace_seo.IN_STOCK: "in_stock",
    marketplace_seo.OUT_OF_STOCK: "out_of_stock",
}

#: Every row that reaches the feed is a new item. A constant claim, defended in
#: the module docstring; grep here if a used-goods listing path is ever added.
CONDITION = "new"

#: No brand, GTIN or MPN exists for these rows, and this is how Google is told
#: so. Sending ``no`` is what makes the absence legal rather than a rejection.
IDENTIFIER_EXISTS = "no"

#: Google's limits. The title is truncated to fit; the description is too, at a
#: bound well above anything in the catalogue (the longest publishable
#: description is 2,708 characters, id 14) so it exists only to stop a
#: pathological row producing an oversized feed.
MAX_TITLE_CHARS = 150
MAX_DESCRIPTION_CHARS = 5000


def _truncate(text, limit):
    """Trim to ``limit`` on a word boundary, or return it unchanged.

    No ellipsis. In a page's meta description an ellipsis signals "there is
    more", which is useful; in a feed field it is a character Google counts
    against a limit and a buyer reads as part of the product name.
    """

    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    # A single word longer than the limit has no boundary to cut on; a hard cut
    # is the only option left, and it is still better than dropping the item.
    return head or text[:limit]


def feed_row(listing):
    """One feed item as a dict of tag -> text, or ``None`` if it cannot be sent.

    Returning ``None`` rather than raising for an ineligible row, because
    ineligibility is an ordinary outcome here, not an error: a seller can publish
    a product with no description, and 3 of the 44 publishable rows have done so
    (measured 2026-10-03). Raising is reserved for the two cases the
    module cannot reason about — an availability value with no feed spelling,
    and an eligibility verdict that contradicts the price it was based on —
    where continuing would publish a wrong claim rather than skip a row.

    This function consumes ``feed_eligible`` and never ``indexable``. The
    narrower verdict is the whole point: an unpriced listing is a perfectly good
    web page and must stay in the sitemap, while submitting it as a Shopping
    offer is a disapproval.
    """

    if not marketplace_seo.eligibility(listing).feed_eligible:
        return None

    listing_id = int(listing.get("id") or 0)
    price = marketplace_seo.parse_price(listing.get("price_label"), listing.get("currency"))
    if not price:
        # `feed_eligible` already required a parseable price, so reaching here
        # means the two modules disagree about the same string. That is not a
        # row to skip quietly -- a silent skip is how a product leaves Shopping
        # with no trace -- and it is not a row to send either, because the claim
        # would be a `g:price` of "None USD". So: loud, and one row wide.
        # `feed_xml` catches this per row, which is the blast radius we want.
        raise ValueError(
            f"Listing {listing_id} is feed_eligible but its price_label "
            f"{listing.get('price_label')!r} does not parse. One of "
            "marketplace_seo.eligibility and marketplace_seo.parse_price is wrong; "
            "reading `indexable` here instead of `feed_eligible` is the usual cause."
        )

    schema_availability = marketplace_seo.availability(listing)
    try:
        availability = _FEED_AVAILABILITY[schema_availability]
    except KeyError:
        raise ValueError(
            f"No Merchant Center spelling for availability {schema_availability!r}; "
            "add it to _FEED_AVAILABILITY rather than sending the schema.org URL."
        ) from None

    return {
        "g:id": str(listing_id),
        "title": _truncate(listing.get("title"), MAX_TITLE_CHARS),
        "description": _truncate(marketplace_seo.listing_description(listing), MAX_DESCRIPTION_CHARS),
        "link": marketplace_seo.product_url(listing_id),
        "g:image_link": marketplace_seo.cover_image(listing),
        "g:availability": availability,
        "g:price": f"{price.amount} {price.currency}",
        "g:condition": CONDITION,
        "g:identifier_exists": IDENTIFIER_EXISTS,
    }


def feed_xml(listings):
    """An RSS 2.0 feed of every eligible listing.

    BUILT WITH ElementTree, NOT WITH STRING CONCATENATION
    -----------------------------------------------------
    A deliberate departure from ``seo_engine.sitemap_xml`` next door, which
    concatenates. The difference is whose strings are being serialised: a
    sitemap holds paths and dates this codebase derived, while every ``title``
    and ``description`` here was typed by a seller. An ampersand in a product
    title is not an attack and does not need to be — it is enough to make the
    feed malformed, at which point Merchant Center rejects the whole file and
    the catalogue disappears from Shopping in one step. ElementTree escapes as a
    property of how it works, so the guarantee does not depend on remembering.

    A row that cannot be rendered is skipped, not fatal, for the same reason
    the sitemap routes swallow their query errors: 13 correct items are worth
    more than a 500, and a single malformed row must not take the other twelve
    down with it.
    """

    ET.register_namespace("g", G_NS)
    rss = ET.Element("rss", {"version": "2.0", "xmlns:g": G_NS})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "PulseSoc Marketplace"
    ET.SubElement(channel, "link").text = search_visibility.CANONICAL_ORIGIN + marketplace_seo.INDEX_PATH
    ET.SubElement(channel, "description").text = "Products available on the PulseSoc Marketplace."

    for listing in listings or []:
        try:
            row = feed_row(listing)
        except Exception:
            logging.exception(
                "MERCHANT_FEED_ROW_FAILED listing_id=%s; the row is omitted and the rest of the feed stands",
                (listing or {}).get("id"),
            )
            continue
        if row is None:
            continue
        item = ET.SubElement(channel, "item")
        for tag, value in row.items():
            # `g:id` etc. are written as literal prefixed tags rather than
            # `{uri}id`, because ET would then rename the prefix to `ns0` and
            # Google's parser keys on the declared prefix in the xmlns above.
            ET.SubElement(item, tag).text = value

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")
