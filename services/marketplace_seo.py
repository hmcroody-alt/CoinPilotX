"""What a marketplace listing may claim to a search engine, and what it may not.

This module exists because structured data is not markup. It is a set of
machine-readable *assertions* made to Google about a product that a person can
buy with money, and every one of them is checkable against the row it came
from. A `<meta>` tag that overstates a page is an SEO mistake; a `price` that
disagrees with the checkout is a consumer-protection problem, and Merchant
Center suspends accounts for it under its misrepresentation policy rather than
mailing a warning.

So the rule this whole module is built around is: **derive or refuse.** Where
the row does not carry a fact, the fact is omitted. Nothing here defaults,
guesses, or fills a required field with a plausible string to make a validator
pass, because a validator passing is not the goal — the goal is that a claim we
publish is true.

WHY THIS IS NOT IN ``seo/schema.py``
------------------------------------
That module describes the *site*: one Organization, one WebSite, one iPhone
app, and a ``product_schema()`` for the PulseSoc Premium subscription. Those
are singletons written by hand and checked against sources outside the
codebase. This module describes rows in ``marketplace_listings`` — an
open-ended set, written by sellers, that changes without a deploy. Mixing the
two would put seller-controlled strings in the same file as the hand-verified
brand facts, and the review standard for those two things is not the same.

It does reuse ``organization_schema()`` and ``website_schema()`` rather than
re-declaring them, and that is deliberate for the reason ``seo/schema.py``
states about ``@id``: nodes sharing an ``@id`` are consolidated by Google into
one entity, so a second, differently-worded Organization node here would not
create a second organisation. It would create one organisation with
contradictory properties, resolved by whichever page was crawled last.

THE FIELDS WE CANNOT HONESTLY EMIT
----------------------------------
``marketplace_listings`` has 42 columns and none of them is a brand, a GTIN, an
MPN or a condition. That is a real gap for Merchant Center, and the answer is
to declare the absence, not to paper over it:

* **brand** is omitted. The tempting filler is ``"PulseSoc"``, and it is false:
  PulseSoc is the marketplace, not the manufacturer, and ``brand`` is a claim
  about who made the thing. Google treats a brand mismatch between the feed and
  the landing page as a data-quality failure, so the filler would be worse than
  the gap.
* **gtin / mpn** are omitted, and the feed declares ``identifier_exists: no``,
  which is the mechanism Google provides for exactly this case. Inventing a
  GTIN is not a formatting shortcut; a GTIN is somebody else's registered
  identifier.
* **condition** is omitted from the page. The feed sends ``new``, because every
  row that reaches it is a first-party dropship order from a supplier catalogue
  and there is no code path in this product that creates a used listing.

THE PRICE IS PARSED, AND MAY FAIL
---------------------------------
``price_label`` is a TEXT column. Measured against production on 2026-09-26 all
fifteen publishable rows hold ``$NN.NN``, but the column's *type* allows
"Request access" or "Free" and the create route does not stop them — the grid
and the detail page both already handle the unpriced case by drawing no price
pill at all. So the parse is strict and is allowed to return ``None``, and a
listing whose price does not parse gets **no Offer node and no feed row**. It
keeps its page; it simply stops making a price claim it cannot substantiate.
Guessing zero would publish "free" for a product that charges.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from seo import schema as seo_schema
from . import marketplace_listing_lifecycle, search_visibility

#: Where a listing lives on the web. One path, so the canonical, the sitemap,
#: the feed's ``link`` and the page a buyer shares are all the same URL. A
#: second product path would split the ranking signal for one product between
#: two URLs and make the canonical a guess.
PRODUCT_PATH = "/pulse/marketplace/{listing_id}"

#: The marketplace index — the crawl entry point that makes product pages
#: reachable without relying on the sitemap alone. Google treats a sitemap as a
#: hint; an internal link is a path.
INDEX_PATH = "/pulse/marketplace"

#: Minimum description length for a product page to ask to be ranked.
#:
#: Deliberately far below ``search_visibility.MIN_INDEXABLE_BODY_CHARS`` (180,
#: for user posts) because the two pages earn their place differently. A post
#: *is* its prose, so a short post is not a search destination. A product page
#: is carried by its title, image, price and availability, and the description
#: is supporting material. What this floor excludes is the genuinely empty case:
#: production currently holds two published listings whose descriptions are 2
#: and 0 characters long, and a page with a price and no sentence is the thin
#: result Google's own guidance is about.
MIN_DESCRIPTION_CHARS = 40

#: Currency symbols we can read, mapped to the ISO code they imply. Used only
#: to *cross-check* the ``currency`` column, never to override it.
_SYMBOL_CURRENCY = {
    "$": "USD",
    "US$": "USD",
    "£": "GBP",
    "€": "EUR",
}

#: ``$1,234.56`` / ``1234.56`` / ``£9`` and nothing more inventive. Anchored at
#: both ends: a label that merely *contains* a number ("from $20", "$20-$40")
#: is ambiguous about which number is the price, and an ambiguous price is
#: exactly the case that must refuse rather than pick.
_PRICE_RE = re.compile(
    r"""^\s*
    (?P<symbol>US\$|[$£€])?\s*
    (?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)
    \s*(?P<code>USD|GBP|EUR)?\s*$""",
    re.VERBOSE | re.IGNORECASE,
)

#: schema.org availability values. Only the two we can actually establish from a
#: row are used; ``PreOrder`` and ``BackOrder`` have no column behind them.
IN_STOCK = "https://schema.org/InStock"
OUT_OF_STOCK = "https://schema.org/OutOfStock"


@dataclass(frozen=True)
class Price:
    """A price we were able to read, with the currency it is denominated in."""

    amount: str
    currency: str


def parse_price(price_label, currency=None):
    """``Price`` for a label we can read, or ``None``.

    Returning ``None`` is a normal outcome and every caller handles it. The
    cases that deliberately fail: an empty label, prose ("Request access"), a
    range, a label with two numbers in it, and — importantly — a label whose
    symbol contradicts the ``currency`` column.

    That last one refuses rather than resolving. ``"$40"`` on a row marked
    ``EUR`` is not a formatting quirk to be normalised; it is two different
    claims about how much money the buyer owes, and there is no basis in the
    row for preferring either. Picking one would publish a price the checkout
    may not charge.
    """

    raw = str(price_label or "").strip()
    if not raw:
        return None
    match = _PRICE_RE.match(raw)
    if not match:
        return None

    try:
        amount = Decimal(match.group("amount").replace(",", ""))
    except InvalidOperation:
        return None
    # A zero or negative price is not "free" -- nothing in this product creates
    # a free listing, so a 0 here is a data fault, and publishing "0.00" to
    # Shopping would advertise a giveaway.
    if amount <= 0:
        return None

    column = str(currency or "").strip().upper() or None
    implied = _SYMBOL_CURRENCY.get((match.group("symbol") or "").upper() or None)
    if not implied:
        implied = _SYMBOL_CURRENCY.get(match.group("symbol") or "")
    stated = (match.group("code") or "").upper() or None

    candidates = {value for value in (column, implied, stated) if value}
    if len(candidates) > 1:
        # Two sources disagree. See the docstring: refuse.
        return None
    if not candidates:
        return None

    return Price(amount=f"{amount:.2f}", currency=candidates.pop())


def availability(listing):
    """schema.org availability, from the same rule the catalogue filters on.

    ``marketplace_listing_lifecycle.inventory_available`` is what
    ``public_sql`` uses to decide whether a row may appear at all, so asking it
    again here is what keeps the page from claiming ``InStock`` for something
    the catalogue has already withdrawn. Duplicating the quantity check instead
    would let the two drift, and the direction it would drift is a product page
    advertising stock that cannot be bought.

    ``OUT_OF_STOCK`` IS UNREACHABLE FROM THE PRODUCT PAGE TODAY, ON PURPOSE
    ----------------------------------------------------------------------
    Worth writing down, because it reads like dead code and is not. ``public_sql``
    ends with ``type IN (stockless…) OR COALESCE(quantity,0)>0``, and
    ``inventory_available`` returns ``True`` for exactly that same stockless set
    and otherwise requires ``quantity >= 1``. The two are equivalent, so every
    row that survives the query to reach this function is in stock, and a
    zero-quantity physical listing 404s rather than rendering as unavailable.

    This branch is kept rather than collapsed to a constant for two reasons.
    Showing withdrawn products as "currently unavailable" instead of hiding them
    is an ordinary catalogue decision — it is how a retailer keeps a ranked URL
    alive through a restock — and the day someone loosens that ``OR`` clause,
    delegation means this page starts telling the truth without anyone
    remembering it exists. And ``availability`` is called by the feed builder as
    well, where the rows are not filtered by the same query.
    """

    return IN_STOCK if marketplace_listing_lifecycle.inventory_available(listing) else OUT_OF_STOCK


def product_url(listing_id):
    return search_visibility.CANONICAL_ORIGIN + PRODUCT_PATH.format(listing_id=int(listing_id or 0))


def _first_image(listing):
    """The cover image URL, or ``""``.

    Reads the ``media`` list that ``pulse_marketplace_listing_payload`` built
    rather than the raw columns, so the page shows the same image the app and
    the API show. The payload has already resolved ``cover_image_url`` /
    ``media_url`` / ``gallery_json`` into one ordered list with the cover first,
    and re-deriving that here is how the web would end up showing a different
    picture for the same product.
    """

    for entry in listing.get("media") or []:
        if (entry.get("media_type") or "image") == "image" and entry.get("media_url"):
            return entry["media_url"]
    return ""


def _description(listing):
    return str(listing.get("description") or listing.get("short_description") or "").strip()


@dataclass(frozen=True)
class Eligibility:
    """Whether this listing's page may ask to be ranked, and why not."""

    indexable: bool
    feed_eligible: bool
    reason: str


def eligibility(listing):
    """Content-level indexability for one listing.

    Separate from ``search_visibility.classify``, and for the reason that
    module states about itself: the path is public, but a public path can still
    hold a record that is thin, imageless or unpriced. Path-level and
    record-level eligibility are different questions and answering them in one
    place is how a rule for the section ends up deciding a fact about a row.

    ``feed_eligible`` is strictly narrower than ``indexable``. Merchant Center
    requires a description and an image outright, and it requires a price that
    matches the landing page — so a row that is fine as a web page can still be
    wrong to submit as a Shopping offer. Two verdicts rather than one, because
    collapsing them would either keep sellable products out of the index or put
    unfeedable ones into the feed.
    """

    if not str(listing.get("title") or "").strip():
        return Eligibility(False, False, "no title")

    price = parse_price(listing.get("price_label"), listing.get("currency"))
    image = _first_image(listing)
    description = _description(listing)

    if len(description) < MIN_DESCRIPTION_CHARS:
        # Not indexable: a priced page with no sentence is the thin-content case.
        return Eligibility(False, False, f"description under {MIN_DESCRIPTION_CHARS} chars")
    if not image:
        return Eligibility(False, False, "no image")
    if not price:
        # The page is a real page and stays indexable -- it simply makes no
        # price claim. The feed cannot accept it, because `price` is required.
        return Eligibility(True, False, "price_label does not parse")

    return Eligibility(True, True, "complete")


def product_page_meta(listing):
    """The ``page`` dict ``_public_shell.html`` renders from.

    The title is the seller's product title, trimmed, with the brand appended.
    Not rewritten: a product's title is the string buyers search for, and
    "improving" it here would make the page disagree with the feed, which
    Merchant Center checks.
    """

    listing_id = int(listing.get("id") or 0)
    title = str(listing.get("title") or "").strip()
    store = str(listing.get("seller_store_name") or "").strip()
    description = _description(listing)
    # Google truncates around 155-160; cutting on a word boundary rather than
    # mid-word because a snippet ending in half a word reads as broken.
    if len(description) > 155:
        description = description[:155].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    if not description:
        description = f"{title} — available on the PulseSoc Marketplace."

    canonical = product_url(listing_id)
    return {
        "canonical": canonical,
        "title": f"{title} | PulseSoc Marketplace" if title else "PulseSoc Marketplace",
        "h1": title or "PulseSoc Marketplace listing",
        "breadcrumb": title or "Listing",
        "description": description,
        "image": _first_image(listing) or seo_schema.SHARE_IMAGE_URL,
        "store": store,
    }


def product_schema_graph(listing):
    """The JSON-LD graph for one product page.

    Not ``seo_schema.schema_graph``. That builder appends ``MobileApplication``
    and ``Service`` nodes to every page it renders, which are true statements
    about the site but are not what this page is about — a Product page whose
    graph also advertises an iPhone app and a consulting service is describing
    three entities and asking Google to decide which one the page is for. The
    two shared singletons are included by reference to the same ``@id`` they
    always have.
    """

    meta = product_page_meta(listing)
    canonical = meta["canonical"]
    price = parse_price(listing.get("price_label"), listing.get("currency"))
    image = _first_image(listing)

    product = {
        "@type": "Product",
        "@id": canonical + "#product",
        "name": meta["h1"],
        "description": _description(listing) or meta["description"],
        "url": canonical,
        "mainEntityOfPage": {"@id": canonical + "#webpage"},
    }
    if image:
        product["image"] = image
    category = str(listing.get("category") or "").strip()
    if category:
        product["category"] = category

    # No `brand`, no `gtin`, no `mpn`, no `sku`. See the module docstring: the
    # columns do not exist and the fillers would be false claims.

    if price:
        offer = {
            "@type": "Offer",
            "@id": canonical + "#offer",
            "price": price.amount,
            "priceCurrency": price.currency,
            "availability": availability(listing),
            "url": canonical,
            # `itemCondition` is asserted because every row reaching this page
            # is a first-party dropship order from a supplier catalogue and this
            # product has no code path that creates a used listing.
            "itemCondition": "https://schema.org/NewCondition",
        }
        store = meta["store"]
        if store:
            # The store is the seller of record, and it is a different entity
            # from the Organization that runs the marketplace. Saying PulseSoc
            # sells it would misstate who the buyer is contracting with.
            offer["seller"] = {"@type": "Organization", "name": store}
        product["offers"] = offer

    # Deliberately no `aggregateRating` and no `review`. `marketplace_listings`
    # has no rating column and there is no review table behind these rows, so
    # any star rating here would be fabricated -- which is the single most
    # heavily penalised structured-data abuse Google names.

    webpage = {
        "@type": "WebPage",
        "@id": canonical + "#webpage",
        "url": canonical,
        "name": meta["title"],
        "description": meta["description"],
        "isPartOf": {"@id": f"{seo_schema.SITE_URL}/#website"},
        "publisher": {"@id": f"{seo_schema.SITE_URL}/#organization"},
        "primaryImageOfPage": image or seo_schema.SHARE_IMAGE_URL,
        "inLanguage": "en",
    }

    breadcrumb = seo_schema.breadcrumb_schema([
        ("Home", seo_schema.SITE_URL + "/"),
        ("Marketplace", seo_schema.SITE_URL + INDEX_PATH),
        (meta["h1"], canonical),
    ])

    return [
        seo_schema.organization_schema(),
        seo_schema.website_schema(),
        webpage,
        product,
        breadcrumb,
    ]


def product_page_graph(listing):
    """``product_schema_graph`` serialised the way the templates consume it.

    Named and shaped after ``seo_schema.app_page_graph`` so the two public
    shells are fed by the same contract: the route passes a string, the template
    writes it into the ``ld+json`` block, and no template ever serialises JSON
    itself.

    ``ensure_ascii=False`` matches the existing graphs. Seller titles in
    production contain non-ASCII punctuation, and escaping it would leave the
    structured title subtly different from the visible one.
    """

    return json.dumps(
        {"@context": "https://schema.org", "@graph": product_schema_graph(listing)},
        ensure_ascii=False,
    )
