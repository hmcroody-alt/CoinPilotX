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

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from seo import schema as seo_schema
from . import marketplace_listing_lifecycle, marketplace_web, search_visibility

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


def public_price(listing):
    """What the logged-out page may claim this costs, or ``None``.

    ``parse_price`` with one more refusal in front of it: a label the row's own
    variants contradict is not a price this page may print. See
    ``price_label_contradicts_variants`` for the measurement — the member page
    and checkout read ``price_cents``, so a label that disagrees is a number
    nobody will ever be charged.

    Refusing rather than rendering the variant price, and the reason is the
    shape of ``Price``: it holds one amount, and two of the four production
    rows that disagree are *ranges* ($27.84-$37.72). Teaching this dataclass to
    hold a range would rebuild ``marketplace_web.PriceView``, which already has
    ``is_range``, ``display`` and ``as_schema_offer``, and a second price
    renderer is the thing that produced this defect. So the honest interim
    claim is no claim, which is the rule this module already follows for a
    label that does not parse: the page keeps its pill-less layout and its
    ``Product`` node simply carries no ``Offer``.

    Returns ``None`` only for the refusals; a row with no variants loaded is
    judged on its label exactly as before.
    """

    price = parse_price(listing.get("price_label"), listing.get("currency"))
    if price and price_label_contradicts_variants(listing):
        return None
    return price


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
    remembering it exists. And ``availability`` is called by
    ``merchant_center_feed`` as well, which maps the result onto Merchant
    Center's own ``in_stock`` / ``out_of_stock`` tokens -- so the branch has a
    second reader whose vocabulary depends on it staying a branch.

    The feed selects on the *same* query, deliberately, so the equivalence above
    makes ``OUT_OF_STOCK`` unreachable there too. That is the right trade for a
    feed even though Merchant Center would rather be told ``out_of_stock`` than
    have an item disappear: the item's landing page 404s once ``public_sql``
    drops it, and submitting a URL that 404s is a disapproval, not a nuance.
    """

    return IN_STOCK if marketplace_listing_lifecycle.inventory_available(listing) else OUT_OF_STOCK


def product_url(listing_id):
    return search_visibility.CANONICAL_ORIGIN + PRODUCT_PATH.format(listing_id=int(listing_id or 0))


def cover_image(listing):
    """The cover image URL, or ``""``.

    Public, along with ``listing_description`` below, because
    ``merchant_center_feed`` needs exactly these two fields and re-deriving them
    there is the thing that must not happen: Merchant Center compares the feed's
    ``image_link`` and ``description`` against the landing page, so a second
    reader of the same columns is a way to fail that comparison. (They were
    ``_first_image`` / ``_description`` until the feed existed. The rename is not
    cosmetic -- an underscore would have invited a copy.)

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


def listing_description(listing):
    return str(listing.get("description") or listing.get("short_description") or "").strip()


@dataclass(frozen=True)
class Eligibility:
    """Whether this listing's page may ask to be ranked, and why not."""

    indexable: bool
    feed_eligible: bool
    reason: str


def price_label_contradicts_variants(listing):
    """Whether this row's two price authorities name different numbers.

    There are two, and that is the fact this function exists to contain.
    ``parse_price`` above reads ``price_label`` -- a display string a human
    typed at publish time -- and it is what this module and the Merchant Center
    feed publish. ``marketplace_web.derive_price`` prefers
    ``marketplace_listing_variants.price_cents``, which is what the supplier
    sync writes, what the product page renders, and -- via
    ``marketplace_cart_routes._line_price_minor`` -- **what checkout actually
    charges**.

    When those disagree, the feed advertises a number the buyer will not be
    asked to pay. Measured against production on 2026-10-01 across the 35 items
    the live feed was serving: four rows disagreed. Listing 36 advertised $38.00
    against a $2.29 variant; listing 112 advertised $29.31 against a
    $27.84-$37.72 range, so a buyer choosing the top option paid $8.41 over the
    advertised price. That direction -- page dearer than feed -- is a Merchant
    Center misrepresentation finding, not a rounding complaint.

    Asks ``derive_price`` rather than re-filtering the variant rows here. The
    question is literally "will the page show the number the feed published",
    and the only way to answer it without inviting drift is to call the function
    that decides what the page shows. A reimplementation of its ``status`` and
    ``price_cents`` handling would agree today and be the next instance of this
    same bug.

    Needs ``listing["variants"]``. Callers that do not load variants get
    ``False`` and the pre-existing behaviour, which is a deliberate fail-open:
    this is a narrowing check layered onto a feed that already shipped, and a
    listing page must not stop rendering because a caller skipped a join.
    ``bot.marketplace_public_listings`` -- the single loader behind both the
    sitemap and the feed -- is the caller that populates it.
    """

    variants = listing.get("variants") or ()
    if not variants:
        return False
    label = parse_price(listing.get("price_label"), listing.get("currency"))
    if not label:
        # No label claim to contradict. `eligibility` has already refused this
        # row for the feed on its own terms.
        return False
    derived = marketplace_web.derive_price(listing, variants)
    if derived.source != "variants":
        # `derive_price` fell back to the same label we just read, so there is
        # only one authority in play and nothing can disagree.
        return False

    label_cents = int((Decimal(label.amount) * 100).to_integral_value())
    if derived.min_cents != label_cents or derived.max_cents != label_cents:
        return True
    # A matching number in the wrong denomination is still two different claims
    # about how much money the buyer owes -- the same refusal `parse_price`
    # makes when a symbol contradicts the currency column.
    return (derived.currency or "").upper() != (label.currency or "").upper()


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
    image = cover_image(listing)
    description = listing_description(listing)

    if len(description) < MIN_DESCRIPTION_CHARS:
        # Not indexable: a priced page with no sentence is the thin-content case.
        return Eligibility(False, False, f"description under {MIN_DESCRIPTION_CHARS} chars")
    if not image:
        return Eligibility(False, False, "no image")
    if not price:
        # The page is a real page and stays indexable -- it simply makes no
        # price claim. The feed cannot accept it, because `price` is required.
        return Eligibility(True, False, "price_label does not parse")
    if price_label_contradicts_variants(listing):
        # Indexable for the same reason as the branch above: the page is real
        # and, pricing from `derive_price`, it is correct. It is the *feed's*
        # number that cannot be substantiated, so the row leaves the feed and
        # keeps its ranking rather than both surfaces going quiet over one
        # stale label.
        return Eligibility(True, False, "price_label disagrees with variant prices")

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
    description = listing_description(listing)
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
        "image": cover_image(listing) or seo_schema.SHARE_IMAGE_URL,
        "store": store,
    }


def site_nodes(canonical, title, description, image):
    """The three nodes that say which site a marketplace page belongs to.

    Organization and WebSite are the shared singletons, included by ``@id`` so
    Google consolidates them rather than minting rivals (see the module
    docstring). WebPage is the document itself, linked to both.

    Split out of ``product_schema_graph`` so a page that builds its own
    ``Product`` node can still publish the site identity. A bare ``Product``
    with nothing around it describes a thing that belongs to no site and is
    published by nobody, which is what the storefront renderer emits on its
    own -- it is a page renderer and does not know about the site graph.
    """

    return [
        seo_schema.organization_schema(),
        seo_schema.website_schema(),
        {
            "@type": "WebPage",
            "@id": canonical + "#webpage",
            "url": canonical,
            "name": title,
            "description": description,
            "isPartOf": {"@id": f"{seo_schema.SITE_URL}/#website"},
            "publisher": {"@id": f"{seo_schema.SITE_URL}/#organization"},
            "primaryImageOfPage": image or seo_schema.SHARE_IMAGE_URL,
            "inLanguage": "en",
        },
    ]


def storefront_product_graph(nodes, *, canonical, title, description, image):
    """Wrap a rendered product page's own nodes in the site graph.

    ``nodes`` are what ``marketplace_storefront.render_product`` produced --
    a Product and a BreadcrumbList, each carrying its own ``@context`` because
    the renderer expects them to be emitted as separate ``<script>`` blocks.
    Here they become members of one graph, so the per-node ``@context`` is
    dropped in favour of the document's: repeating it inside ``@graph`` is
    legal but says a scoped context applies where none does.

    The Product keeps the price ``render_product`` gave it. That is the whole
    reason this takes rendered nodes instead of re-deriving them from the row:
    the visible price pill and this ``Offer`` are one claim in two formats, and
    building the second from a different source is the defect that let the page
    advertise an amount checkout would not charge.
    """

    product_id = canonical + "#product"
    graph = site_nodes(canonical, title, description, image)
    for node in nodes:
        node = {key: value for key, value in node.items() if key != "@context"}
        if node.get("@type") == "Product":
            # Cross-linked both ways, which is the only thing a shared graph
            # buys over separate blocks.
            node["@id"] = product_id
            node["mainEntityOfPage"] = {"@id": canonical + "#webpage"}
        graph.append(node)
    return {"@context": "https://schema.org", "@graph": graph}


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
    # `public_price`, not `parse_price`: the `Offer` below is the page's
    # machine-readable price claim and Merchant Center compares it against the
    # feed, so it must make exactly the claim the visible pill makes -- and
    # neither may state a number the row's own variants contradict.
    price = public_price(listing)
    image = cover_image(listing)

    product = {
        "@type": "Product",
        "@id": canonical + "#product",
        "name": meta["h1"],
        "description": listing_description(listing) or meta["description"],
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

    breadcrumb = seo_schema.breadcrumb_schema([
        ("Home", seo_schema.SITE_URL + "/"),
        ("Marketplace", seo_schema.SITE_URL + INDEX_PATH),
        (meta["h1"], canonical),
    ])

    return site_nodes(
        canonical, meta["title"], meta["description"], image,
    ) + [product, breadcrumb]


def product_page_graph(listing):
    """``product_schema_graph`` serialised the way the templates consume it.

    Named and shaped after ``seo_schema.app_page_graph`` so the two public
    shells are fed by the same contract: the route passes a string, the template
    writes it into the ``ld+json`` block, and no template ever serialises JSON
    itself.

    Through ``seo_schema.serialise_graph`` rather than ``json.dumps`` for the
    last part of that contract: these are the only graphs on the domain carrying
    text a seller wrote, and that serialiser is where ``<`` is escaped so nothing
    in a title can close the ``script`` element it is written into. The write
    path's ``clean_html`` strips matched ``<...>`` pairs only, so a title holding
    no ``>`` arrives here intact.

    This function has no caller in the app today -- its one caller,
    ``bot._marketplace_public_product_response``, is the retained rollback for
    the public-marketplace unification. The escaping is here anyway, because a
    rollback is exactly when nobody re-reads the renderer being restored.
    """

    return seo_schema.serialise_graph(
        {"@context": "https://schema.org", "@graph": product_schema_graph(listing)},
    )


# --- The index page ----------------------------------------------------------
#
# The grid matters to search for a reason that has nothing to do with ranking the
# grid itself: it is the only internal path to the product pages. A sitemap is a
# hint Google may ignore and re-check on its own schedule; a linked page in the
# site's own navigation is how a crawler finds a URL and how it decides how often
# to come back. Product pages reachable only from a sitemap get crawled late and
# shallowly, and products nobody links to look like products nobody sells.


def index_page_meta(listings):
    """The ``page`` dict for the public marketplace grid.

    No product count in the description, deliberately. It is the obvious thing to
    write -- "Browse 14 products" -- and it is a claim that is wrong between the
    crawl and the click, on a page whose whole job is to be fetched repeatedly. A
    description that never needs to change is also a description Google will not
    re-snippet.
    """

    canonical = seo_schema.SITE_URL + INDEX_PATH
    return {
        "canonical": canonical,
        "title": "Marketplace | PulseSoc",
        "h1": "PulseSoc Marketplace",
        "breadcrumb": "Marketplace",
        "description": (
            "Products and services from approved PulseSoc sellers. Every listing is "
            "reviewed before it is published, and orders, delivery updates and seller "
            "messages happen inside the PulseSoc app."
        ),
        "image": seo_schema.SHARE_IMAGE_URL,
        "store": "",
    }


def index_schema_graph(listings):
    """``[organization, website, CollectionPage, ItemList, breadcrumb]``.

    The ``ItemList`` carries **positions and URLs only** -- no names, no prices, no
    images. That is Google's documented summary-page shape, and the reason to
    follow it here is specific rather than deferential: this page holds up to
    forty products, each of which already has a ``Product`` node at its own
    canonical URL. Restating name and price here would publish a second
    description of every one of those products at a different URL, and the two
    would disagree the moment a seller edits a price -- the list page is cached
    for five minutes and rebuilt from a forty-row query, the product page is not.
    One authority per product; the list says where they are.

    Only rows whose product page is *indexable* are listed, while the HTML below
    links to every card. That split is not an oversight either. The links are the
    crawl paths and must stay complete, or a thin listing becomes unreachable and
    can never recover when its seller writes a description. The ``ItemList`` is a
    claim about what we are asking to rank, and a list that names pages carrying
    ``noindex`` contradicts itself.
    """

    canonical = seo_schema.SITE_URL + INDEX_PATH
    meta = index_page_meta(listings)

    elements = []
    for listing in listings or ():
        if not eligibility(listing).indexable:
            continue
        listing_id = int(listing.get("id") or 0)
        if not listing_id:
            continue
        elements.append({
            "@type": "ListItem",
            "position": len(elements) + 1,
            "url": product_url(listing_id),
        })

    item_list = {
        "@type": "ItemList",
        "@id": canonical + "#itemlist",
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "numberOfItems": len(elements),
        "itemListElement": elements,
    }

    collection = {
        "@type": "CollectionPage",
        "@id": canonical + "#webpage",
        "url": canonical,
        "name": meta["title"],
        "description": meta["description"],
        "isPartOf": {"@id": f"{seo_schema.SITE_URL}/#website"},
        "publisher": {"@id": f"{seo_schema.SITE_URL}/#organization"},
        "primaryImageOfPage": meta["image"],
        "mainEntity": {"@id": item_list["@id"]},
        "inLanguage": "en",
    }

    breadcrumb = seo_schema.breadcrumb_schema([
        ("Home", seo_schema.SITE_URL + "/"),
        ("Marketplace", canonical),
    ])

    return [
        seo_schema.organization_schema(),
        seo_schema.website_schema(),
        collection,
        item_list,
        breadcrumb,
    ]


def index_page_graph(listings):
    """``index_schema_graph`` serialised for the template, like its product twin."""

    return seo_schema.serialise_graph(
        {"@context": "https://schema.org", "@graph": index_schema_graph(listings)},
    )


def index_card(listing):
    """One card's view data, derived from the same payload the product page uses.

    Here rather than in the route because every value on a card is a value the
    product page also shows, and the two must agree: a grid that formats its own
    price from the row while the product page parses it through ``parse_price``
    is how a listing ends up priced on one page and unpriced on the next, for the
    same product. This returns ``None`` for the price in exactly the cases the
    product page prints no price pill.
    """

    listing_id = int(listing.get("id") or 0)
    return {
        "id": listing_id,
        "title": str(listing.get("title") or "").strip() or "PulseSoc Marketplace listing",
        "path": PRODUCT_PATH.format(listing_id=listing_id),
        "price": parse_price(listing.get("price_label"), listing.get("currency")),
        "store": str(listing.get("seller_store_name") or "").strip(),
        "category": str(listing.get("category") or "").strip(),
        "image": cover_image(listing),
        "in_stock": availability(listing) == IN_STOCK,
        # The one-line summary, cut the way the meta description is cut. Not the
        # full description: forty of those is a page nobody reads and a crawl
        # budget spent on text that is already on forty other URLs.
        "summary": _index_summary(listing),
    }


def _index_summary(listing, limit=120):
    description = listing_description(listing)
    if len(description) <= limit:
        return description
    return description[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


# --- The department pages ----------------------------------------------------
#
# `/pulse/marketplace?category=<slug>` is already a first-class indexable page:
# `marketplace_storefront.render_discovery` gives it its own `<h1>`, its own
# `<title>`, its own meta description and a self-referencing canonical, and it
# serves `index,follow` whenever the slug names a real department. Twelve of
# them existed in production on 2026-10-02 and not one appeared in any sitemap,
# so their only route to discovery was the hub's own category nav.
#
# This section decides which of them we *submit*. Submission is a stronger
# claim than linking: a sitemap entry says "this is a page we want ranked", and
# a department holding one product is a page that competes with that product's
# own page while saying less about it.


#: How many indexable products a department must hold before we submit it.
#:
#: Named and explainable rather than tuned, because this is the number that
#: answers "why is this URL in your sitemap" at a review. Three is the point at
#: which a department page stops being a restatement of a single product: at one
#: or two listings the department's title, image and text are substantially the
#: product's own, and the product page is the better result for the same query.
#:
#: Measured against production on 2026-10-02, over the 41 products the live
#: sitemap submits: the catalogue has twelve departments and the distribution is
#: 14 / 9 / 3 / 3 / 2 / 2 / 2 / 2 / 1 / 1 / 1 / 1. So this threshold submits four
#: and withholds eight, half of which are singletons. All twelve already serve
#: ``index,follow`` with a self-canonical, so the eight are a submission
#: judgement and not a technical limit -- they stay linked from the hub's
#: category nav, and Google stays free to index them on its own.
#:
#: Counted over *indexable* listings, not public ones. A department of five
#: products where four are thin renders four `noindex` links, and submitting it
#: would ask Google to rank a collection of pages we have asked it to ignore.
CATEGORY_MIN_INDEXABLE_LISTINGS = 3


def category_path(slug):
    """The department URL, built the way the page builds its own canonical.

    Goes through ``marketplace_web.build_query_string`` rather than formatting a
    string here, for the same reason the product path is a single constant: the
    department page emits its own ``rel=canonical`` from that function, and a
    sitemap that spells the URL differently -- ``/`` where the page writes
    ``%2F``, or parameters in another order -- submits a URL that points at a
    canonical it does not match. Google follows the canonical and the submitted
    URL is wasted.
    """

    return INDEX_PATH + marketplace_web.build_query_string({"category": slug})


def category_entries(pairs):
    """Submittable department URLs as ``(path, lastmod)``.

    ``pairs`` is ``bot.marketplace_public_listings()`` output -- ``(row,
    listing)`` -- because this needs both halves: the payload to ask
    :func:`eligibility`, and the raw row for ``updated_at``, which the payload
    does not preserve.

    **The taxonomy is built from the whole public catalogue, and the threshold
    is then measured on the indexable subset.** Those are deliberately two
    different populations and swapping them is the bug this function is shaped
    to avoid. ``build_taxonomy`` picks each department's slug by majority
    spelling -- the catalogue carries both "Mens Clothing" and "Men's
    Clothing" -- and ``render_discovery`` tests the requested slug against the
    taxonomy *it* builds, from every public row, with an exact ``==``. Build
    from a narrower set here and the majority spelling can flip, at which point
    the slug we submit is one the live page calls unknown: it would answer
    ``noindex,follow`` and canonicalise to the bare hub. So the slug comes from
    the same input the page uses, and only the *count* looks at eligibility.

    Top-level departments only. Depth-2 sections are excluded by the same
    judgement the threshold encodes rather than by a second rule -- at this
    catalogue size a section is a near-duplicate of its department
    ("phones-accessories" holds two listings and its only child holds one) --
    and they stay crawlable through the department page's own sub-nav, so
    nothing becomes undiscoverable. Fewer, stronger URLs; not more of them.

    ``lastmod`` is the newest ``updated_at`` among the department's indexable
    listings, which is the honest answer: what changes about a department page
    is the products on it. A department nobody has touched in four months keeps
    a four-month-old date, because the alternative -- stamping today on every
    crawl -- is the behaviour that teaches a crawler to stop reading the field.
    """

    rows = list(pairs or ())
    taxonomy = marketplace_web.build_taxonomy([
        listing.get("category") for _row, listing in rows
    ])

    indexable = [
        (row, listing) for row, listing in rows if eligibility(listing).indexable
    ]

    entries = []
    for node in taxonomy:
        members = [
            row for row, listing in indexable
            if marketplace_web.category_matches(listing.get("category"), node.slug)
        ]
        if len(members) < CATEGORY_MIN_INDEXABLE_LISTINGS:
            continue
        path = category_path(node.slug)
        if not search_visibility.sitemap_eligible(path):
            continue
        stamps = [
            str(row.get("updated_at") or row.get("created_at") or "") for row in members
        ]
        entries.append((path, max([s for s in stamps if s], default="")))
    return entries
