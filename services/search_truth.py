"""Cross-surface truth comparison for PulseSoc's search-facing pages.

WHAT THIS IS FOR
----------------
One product page states its price in seven places. Measured on the live
``/pulse/marketplace/163`` on 2026-10-03: ``<meta name="description">``,
``og:description``, ``twitter:description``, the JSON-LD ``WebPage.description``
prose, the JSON-LD ``Offer.price``, the ``data-mkt-variants`` bootstrap
attribute, and the visible ``data-mkt-price`` pill. Behind the page there are
three more -- ``marketplace_listings.price_label``,
``marketplace_listing_variants.price_cents``, and the Merchant Center
``g:price``. Ten claims about how much money the buyer owes, and until this
module nothing compared any of them to any other.

Each of those spellings is produced by a different code path, so they can drift
independently, and the drift is invisible: the page renders, the feed validates,
the tests pass, and the only reader who notices is a buyer at checkout or a
Merchant Center reviewer filing a misrepresentation finding.

WHAT IT DELIBERATELY IS NOT
---------------------------
It does not decide what a price, canonical URL, robots directive or eligibility
verdict *should* be. Those belong to ``marketplace_seo`` (record-level price,
availability, eligibility), ``search_visibility`` (path-level indexability and
canonical policy), ``marketplace_storefront`` (the canonical of a *paginated*
marketplace URL) and ``merchant_center_feed`` (feed projection). All four are
imported and asked. Re-deriving any of them here would mean this module can
agree with itself while disagreeing with production, which is the exact failure
mode it exists to catch.

It also performs no I/O. ``compare_listing`` takes HTML it is handed, so the
comparison is unit-testable against a fixture and the network lives in
``scripts/search_os/search_truth_sentinel.py``. A sentinel that can only be
exercised against production is a sentinel nobody runs.

UNKNOWN IS NOT A FAULT
----------------------
An empty ``faults`` tuple does not mean "verified". It means "no contradiction
was found among the surfaces that could be read". ``Verdict.skipped`` names the
comparisons that could not be made -- a page that failed to fetch, a listing
loaded without its variants, a sitemap we were not given -- and a caller that
reports a clean verdict without reporting ``skipped`` alongside it is
manufacturing certainty. The two are one result and must travel together.

For the same reason absence and contradiction are different fault codes.
``PRICE_ABSENT_ON_WIRE`` means the page made no price claim; ``PRICE_WIRE_VS_DB``
means it made a wrong one. Collapsing them would turn "we did not look in the
right place" into "production is broken", and during this module's own
development a shell-quoting mistake did exactly that -- a bounded ``grep``
reported no price on a page that states it seven times.
"""

from __future__ import annotations

import html as html_entities
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import marketplace_seo, marketplace_web, merchant_center_feed, search_visibility

#: Where a claim came from. Carried on every ``Observation`` so a verdict can be
#: read back as evidence rather than as an assertion -- "the wire says X, the
#: database says Y" is actionable; "price mismatch" is not.
SOURCE_POLICY = "POLICY"
SOURCE_DATABASE = "DATABASE"
SOURCE_WIRE = "WIRE"
SOURCE_FEED = "FEED"
#: Reserved and currently unused: no inbound provider evidence exists for this
#: property (no Search Console client, no Merchant diagnostics ingest, no Bing
#: API, no IndexNow submission code). Declared so that provider-observed state,
#: when it arrives, is labelled as a third-party claim rather than silently
#: joining our own measurements.
SOURCE_PROVIDER = "PROVIDER"

P0 = "P0"
P1 = "P1"
P2 = "P2"

_SEVERITY_ORDER = {P0: 0, P1: 1, P2: 2}

#: ``sku`` / ``g:id`` identity for a listing. One spelling, asserted rather than
#: inferred, because a changed SKU silently splits a product's history in
#: Merchant Center instead of erroring.
SKU_TEMPLATE = "pulsesoc-listing-{listing_id}"


@dataclass(frozen=True)
class Observation:
    """One claim, and where it was read.

    ``value is None`` means UNKNOWN -- not "zero", not "absent and therefore
    wrong". Every comparison in this module skips an unknown rather than
    counting it as a disagreement.
    """

    value: object
    source: str
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class Fault:
    code: str
    severity: str
    detail: str


@dataclass(frozen=True)
class Verdict:
    """The result of comparing one listing across every readable surface."""

    listing_id: int
    faults: tuple = ()
    evidence: dict = field(default_factory=dict)
    #: Comparisons that could not be attempted, as ``code -> why``. Must be
    #: reported wherever ``faults`` is reported; see the module docstring.
    skipped: dict = field(default_factory=dict)

    @property
    def worst(self):
        if not self.faults:
            return None
        return min((f.severity for f in self.faults), key=lambda s: _SEVERITY_ORDER[s])

    @property
    def codes(self) -> tuple:
        return tuple(f.code for f in self.faults)


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MoneyClaim:
    """What a surface says this costs: one amount, or a span of amounts.

    A span is not a vaguer version of a point -- it is what a multi-option
    product honestly costs, and it is the case that matters most here. Three of
    the 41 product URLs in production's sitemap on 2026-10-03 are ranged, and
    ranged rows are exactly where ``marketplace_seo`` documents real drift
    (listing 112 advertising $29.31 against a $27.84-$37.72 variant span). An
    earlier draft of this module modelled only points, read ``AggregateOffer``
    as "no price", and so reported UNKNOWN on precisely the 7% of the catalogue
    with a known defect. Hence ``low``/``high`` rather than one amount.
    """

    low: str
    high: str
    currency: str

    @property
    def is_range(self) -> bool:
        return self.low != self.high

    def __str__(self) -> str:
        if self.is_range:
            return f"{self.low}-{self.high} {self.currency}"
        return f"{self.low} {self.currency}"


#: A money *mention* inside prose. Requires a symbol or an ISO code, unlike
#: ``marketplace_seo._PRICE_RE`` which anchors a whole label. A bare number in a
#: meta description ("2 Pack", "12V") is not a price claim and must not be read
#: as one.
_MONEY_MENTION = (
    r"(?:(?:US\$|[$£€])\s*\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"|\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\s*(?:USD|GBP|EUR))"
)
_PROSE_MONEY_RE = re.compile(_MONEY_MENTION, re.IGNORECASE)
#: ``$15.92 – $51.74`` as the storefront prints it. The separator is an en dash
#: surrounded by spaces (``marketplace_web.PriceView.display``); the hyphen and
#: em dash are accepted too rather than relying on one renderer's choice of
#: character staying put.
_PROSE_RANGE_RE = re.compile(
    rf"({_MONEY_MENTION})\s*[–—-]\s*({_MONEY_MENTION})", re.IGNORECASE
)


def normalize_money(text, currency=None):
    """``MoneyClaim`` for a money string -- point or range -- or ``None``.

    Each endpoint is parsed by ``marketplace_seo.parse_price``, so there is
    exactly one money grammar in the codebase and this module inherits its
    refusals: a zero, two numbers in one endpoint, or a symbol that contradicts
    ``currency`` all return ``None``. A sentinel with its own looser parser
    would resolve ambiguity the renderer refused to resolve, and then report the
    renderer as wrong.
    """

    raw = str(text or "").strip()
    if not raw:
        return None

    match = _PROSE_RANGE_RE.fullmatch(raw)
    if match:
        low = marketplace_seo.parse_price(match.group(1), currency)
        high = marketplace_seo.parse_price(match.group(2), currency)
        if not low or not high or low.currency != high.currency:
            return None
        if Decimal(low.amount) > Decimal(high.amount):
            # A span printed backwards is not a span to be silently sorted; it
            # is a renderer fault, and guessing the author's intent here would
            # hide it.
            return None
        return MoneyClaim(low.amount, high.amount, low.currency)

    point = marketplace_seo.parse_price(raw, currency)
    if not point:
        return None
    return MoneyClaim(point.amount, point.amount, point.currency)


def money_span(low_text, high_text, currency=None):
    """``MoneyClaim`` from two endpoints, as ``AggregateOffer`` states them."""

    low = marketplace_seo.parse_price(low_text, currency)
    high = marketplace_seo.parse_price(high_text, currency)
    if not low or not high or low.currency != high.currency:
        return None
    if Decimal(low.amount) > Decimal(high.amount):
        return None
    return MoneyClaim(low.amount, high.amount, low.currency)


def prose_mentions(text):
    """Every distinct money claim a sentence states, as a set.

    A range counts once. More than one entry means the sentence contradicts
    itself, and the caller must report that rather than treat it as an absence
    -- a buyer and a Merchant Center reviewer read the sentence, not whichever
    tag we would have preferred them to read. Returning the set instead of a
    single claim is what keeps the case reportable: an earlier version answered
    ``None`` for "two amounts", which made the worst prose defect available look
    exactly like a page stating no prose price at all.

    A span that parses backwards is deliberately not stripped before the
    point-mention scan, so it surfaces as the two contradictory endpoints it
    reads as instead of vanishing.
    """

    body = str(text or "")
    claims = set()
    rest = body
    for raw in sorted({m.group(0) for m in _PROSE_RANGE_RE.finditer(body)}):
        claim = normalize_money(" ".join(raw.split()))
        if claim is None:
            continue
        claims.add(claim)
        rest = rest.replace(raw, " ")
    for raw in {m.group(0).strip() for m in _PROSE_MONEY_RE.finditer(rest)}:
        claim = normalize_money(raw)
        if claim is not None:
            claims.add(claim)
    return claims


def prose_money(text):
    """The single money claim in a sentence, or ``None``.

    ``None`` for no mention and for two unrelated mentions: a description
    quoting two amounts is ambiguous about which is the offer, and this module
    does not pick. ``prose_mentions`` tells those two cases apart, and callers
    that must not silently drop the second one use it instead.
    """

    claims = prose_mentions(text)
    return claims.pop() if len(claims) == 1 else None


# ---------------------------------------------------------------------------
# HTML reading
# ---------------------------------------------------------------------------

#: Attribute values here are double-quoted, single-quoted *and* unquoted in the
#: same document: the live PDP carries
#: ``data-mkt-variants='[{&quot;price&quot;: ...}]'`` next to
#: ``content="..."``. A double-quote-only matcher reads the single-quoted one as
#: absent, which is a false negative on the richest price surface on the page.
_ATTR_RE = re.compile(
    r"""([a-zA-Z0-9:_.-]+)       # name
        (?:\s*=\s*
          (?:"([^"]*)"|'([^']*)'|([^\s"'>]+))
        )?""",
    re.VERBOSE,
)
_TAG_RE = re.compile(r"<(meta|link)\b([^>]*?)/?>", re.IGNORECASE)
_LDJSON_RE = re.compile(
    r"<script[^>]+type\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_VARIANTS_RE = re.compile(
    r"data-mkt-variants\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE
)
_TAGS_INSIDE_RE = re.compile(r"<[^>]*>")


def _attrs(blob):
    out = {}
    for match in _ATTR_RE.finditer(blob):
        name = match.group(1).lower()
        value = match.group(2)
        if value is None:
            value = match.group(3)
        if value is None:
            value = match.group(4)
        out[name] = html_entities.unescape(value) if value is not None else ""
    return out


def _tags(page_html, kind):
    for match in _TAG_RE.finditer(page_html or ""):
        if match.group(1).lower() == kind:
            yield _attrs(match.group(2))


def meta_content(page_html, name):
    """``content`` of the first ``<meta>`` keyed on ``name`` or ``property``."""

    wanted = name.lower()
    for attrs in _tags(page_html, "meta"):
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key == wanted:
            return attrs.get("content") or None
    return None


def canonical_from_html(page_html):
    for attrs in _tags(page_html, "link"):
        rels = (attrs.get("rel") or "").lower().split()
        if "canonical" in rels:
            return (attrs.get("href") or "").strip() or None
    return None


def robots_from_html(page_html):
    value = meta_content(page_html, "robots")
    return value.strip() if value else None


def jsonld_nodes(page_html):
    """Every JSON-LD node on the page, ``@graph`` flattened.

    Malformed blocks are skipped rather than raised on, and the skip is visible
    to the caller as the node simply not being there -- which surfaces as
    ``*_ABSENT``, never as a mismatch.
    """

    nodes = []
    for match in _LDJSON_RE.finditer(page_html or ""):
        try:
            payload = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        queue = payload if isinstance(payload, list) else [payload]
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph)
            nodes.append(item)
    return nodes


def _node_of_type(nodes, wanted):
    for node in nodes:
        declared = node.get("@type")
        types = declared if isinstance(declared, list) else [declared]
        if wanted in [t for t in types if isinstance(t, str)]:
            return node
    return None


def _data_attr_text(page_html, attr):
    pattern = re.compile(
        r"<(\w+)\b[^>]*\b" + re.escape(attr) + r"\b[^>]*>(.*?)</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(page_html or "")
    if not match:
        return None
    text = html_entities.unescape(_TAGS_INSIDE_RE.sub(" ", match.group(2)))
    return " ".join(text.split()) or None


def variant_bootstrap(page_html):
    """The parsed ``data-mkt-variants`` payload, or ``None``.

    This is the attribute whose single quotes and ``&quot;`` bodies defeat a
    naive reader. It is also the only wire surface that carries *per-option*
    prices, so it is the one that can prove the page and the feed disagree about
    which of several numbers the buyer pays.
    """

    match = _VARIANTS_RE.search(page_html or "")
    if not match:
        return None
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    try:
        payload = json.loads(html_entities.unescape(raw))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, list) else None


# ---------------------------------------------------------------------------
# surface extraction
# ---------------------------------------------------------------------------

#: Prose surfaces that restate the price. They share one generator today, so
#: they should agree trivially -- which is why a disagreement between them is
#: worth a fault code of its own rather than being folded into the page-wide
#: comparison.
_PROSE_SURFACES = ("description", "og:description", "twitter:description")


def _prose_texts(page_html):
    """``surface -> prose``, for every surface that restates the price in words.

    Shared by ``price_surfaces`` and ``prose_contradictions`` so the set of
    sentences being read is defined once. A surface added to one and forgotten
    in the other would be a surface we report as agreeing without having read.
    """

    texts = {f"prose:{name}": meta_content(page_html, name) for name in _PROSE_SURFACES}
    webpage = _node_of_type(jsonld_nodes(page_html), "WebPage")
    texts["prose:jsonld_webpage"] = (
        webpage.get("description") if isinstance(webpage, dict) else None
    )
    return texts


def prose_contradictions(page_html):
    """``surface -> sorted claims`` for prose that states more than one amount.

    Empty for a healthy page. This is reported separately from the page-wide
    price comparison because the comparison can only see one claim per surface:
    a sentence holding both $30.50 and $99.99 contributes no comparable value at
    all, so without this the page would be reported as having every surface in
    agreement.
    """

    out = {}
    for surface, text in _prose_texts(page_html).items():
        if not text:
            continue
        claims = prose_mentions(text)
        if len(claims) > 1:
            out[surface] = sorted(_show(_money_key(c)) for c in claims)
    return out


def price_surfaces(page_html):
    """Every price the page states, as ``surface -> Observation``.

    Always returns all keys. An unreadable surface is an ``Observation`` with
    ``value=None``, never a missing key, so a caller cannot accidentally read
    "absent" as "agrees".
    """

    nodes = jsonld_nodes(page_html)
    out = {}

    offer = None
    product = _node_of_type(nodes, "Product")
    if isinstance(product, dict):
        candidate = product.get("offers")
        if isinstance(candidate, list):
            candidate = candidate[0] if candidate else None
        offer = candidate if isinstance(candidate, dict) else None
    if offer is None:
        out["jsonld_offer"] = Observation(None, SOURCE_WIRE, "no Offer node")
    elif "lowPrice" in offer or "highPrice" in offer:
        # `AggregateOffer`, which `marketplace_web.PriceView.as_schema_offer`
        # emits for a multi-option product. Reading only `price` here is how
        # ranged listings -- the subset with documented drift -- went unchecked.
        out["jsonld_offer"] = Observation(
            money_span(offer.get("lowPrice"), offer.get("highPrice"), offer.get("priceCurrency")),
            SOURCE_WIRE,
            f"{offer.get('@type')}.lowPrice/highPrice",
        )
    else:
        out["jsonld_offer"] = Observation(
            normalize_money(offer.get("price"), offer.get("priceCurrency")),
            SOURCE_WIRE,
            "Product.offers.price",
        )

    pill = _data_attr_text(page_html, "data-mkt-price")
    out["visible_pill"] = Observation(
        normalize_money(pill) if pill else None,
        SOURCE_WIRE,
        f"data-mkt-price={pill!r}" if pill else "no data-mkt-price element",
    )

    variants = variant_bootstrap(page_html)
    if variants is None:
        out["variant_bootstrap"] = Observation(None, SOURCE_WIRE, "no data-mkt-variants")
    else:
        prices = {normalize_money(v.get("price")) for v in variants if isinstance(v, dict)}
        prices.discard(None)
        # Several option prices are normal and collapse to the span the page
        # prints, so they are one comparable claim rather than an unknown. Mixed
        # currencies across options are not a span this module will invent.
        currencies = {p.currency for p in prices}
        if len(prices) == 1:
            out["variant_bootstrap"] = Observation(
                prices.pop(), SOURCE_WIRE, "single option price"
            )
        elif prices and len(currencies) == 1:
            bounds = sorted(prices, key=lambda p: Decimal(p.low))
            out["variant_bootstrap"] = Observation(
                MoneyClaim(bounds[0].low, bounds[-1].high, currencies.pop()),
                SOURCE_WIRE,
                f"{len(prices)} distinct option prices",
            )
        else:
            out["variant_bootstrap"] = Observation(
                None,
                SOURCE_WIRE,
                f"{len(prices)} option prices in {len(currencies)} currencies"
                if prices
                else "no readable option price",
            )

    for surface, text in _prose_texts(page_html).items():
        label = surface.split(":", 1)[1]
        if not text:
            out[surface] = Observation(None, SOURCE_WIRE, f"no {label}")
            continue
        claims = prose_mentions(text)
        if len(claims) == 1:
            out[surface] = Observation(claims.pop(), SOURCE_WIRE, label)
        elif claims:
            # Not comparable, and not clean either. `prose_contradictions`
            # reports it as a fault; this observation only has to avoid
            # presenting it as a silent absence.
            shown = ", ".join(sorted(_show(_money_key(c)) for c in claims))
            out[surface] = Observation(None, SOURCE_WIRE, f"{label} states {shown}")
        else:
            out[surface] = Observation(None, SOURCE_WIRE, f"{label} states no price")
    return out


def availability_surfaces(page_html):
    """Every stock claim the page states, normalised to schema.org URLs."""

    nodes = jsonld_nodes(page_html)
    out = {}

    offer = None
    product = _node_of_type(nodes, "Product")
    if isinstance(product, dict):
        candidate = product.get("offers")
        if isinstance(candidate, list):
            candidate = candidate[0] if candidate else None
        offer = candidate if isinstance(candidate, dict) else None
    declared = (offer or {}).get("availability")
    out["jsonld_offer"] = Observation(
        declared if declared in (marketplace_seo.IN_STOCK, marketplace_seo.OUT_OF_STOCK) else None,
        SOURCE_WIRE,
        f"Offer.availability={declared!r}" if offer else "no Offer node",
    )

    label = _data_attr_text(page_html, "data-mkt-stock")
    out["visible_label"] = Observation(
        _stock_label_to_schema(label),
        SOURCE_WIRE,
        f"data-mkt-stock={label!r}" if label else "no data-mkt-stock element",
    )

    variants = variant_bootstrap(page_html)
    if variants is None:
        out["variant_bootstrap"] = Observation(None, SOURCE_WIRE, "no data-mkt-variants")
    else:
        flags = {bool(v.get("available")) for v in variants if isinstance(v, dict)}
        # Any buyable option makes the product in stock; that is the same rule
        # `marketplace_listing_lifecycle.inventory_available` applies to the row.
        out["variant_bootstrap"] = Observation(
            (marketplace_seo.IN_STOCK if True in flags else marketplace_seo.OUT_OF_STOCK)
            if flags
            else None,
            SOURCE_WIRE,
            f"{len(variants)} options",
        )
    return out


#: The human strings the storefront prints for stock. Mapped rather than
#: substring-matched: "In stock" is a substring of nothing useful, but
#: "unavailable" contains "available", and a substring test on that pair reads
#: an out-of-stock page as in stock.
_STOCK_LABELS = {
    "in stock": marketplace_seo.IN_STOCK,
    "out of stock": marketplace_seo.OUT_OF_STOCK,
    "sold out": marketplace_seo.OUT_OF_STOCK,
    "currently unavailable": marketplace_seo.OUT_OF_STOCK,
    "unavailable": marketplace_seo.OUT_OF_STOCK,
}


def _stock_label_to_schema(label):
    if not label:
        return None
    return _STOCK_LABELS.get(" ".join(str(label).split()).strip().lower())


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------


def _money_key(claim):
    if claim is None:
        return None
    try:
        return (Decimal(claim.low), Decimal(claim.high), (claim.currency or "").upper())
    except (InvalidOperation, TypeError):
        return None


def _show(key):
    low, high, currency = key
    return f"{low}-{high} {currency}" if low != high else f"{low} {currency}"


def database_price(listing):
    """Both of the row's price authorities, as ``Observation`` pairs.

    There are two and they are not interchangeable. ``price_label`` is what the
    Merchant feed and the structured data publish; the variants' ``price_cents``
    is what the product page renders and what checkout charges. Reporting one
    of them as "the database price" is how this drift stayed invisible, so both
    are returned and the disagreement between them is its own fault.
    """

    label = marketplace_seo.parse_price(listing.get("price_label"), listing.get("currency"))
    label_obs = Observation(
        MoneyClaim(label.amount, label.amount, label.currency) if label else None,
        SOURCE_DATABASE,
        "price_label",
    )

    variants = listing.get("variants") or ()
    derived = marketplace_web.derive_price(listing, variants) if variants else None
    if derived is None or not derived.known:
        return label_obs, Observation(
            None,
            SOURCE_DATABASE,
            "variants not loaded" if not variants else "derive_price has no number",
        )
    return label_obs, Observation(
        MoneyClaim(
            f"{derived.min_cents / 100:.2f}",
            f"{derived.max_cents / 100:.2f}",
            (derived.currency or "").upper(),
        ),
        SOURCE_DATABASE,
        f"derive_price source={derived.source}",
    )


def compare_listing(listing, page_html, *, sitemap_ids=None):
    """Compare one listing's database row against the page production served.

    ``listing`` is a row as ``bot.marketplace_public_listings`` loads it --
    including ``variants``, without which the two-authority price check is
    skipped rather than guessed at. ``page_html`` is the response body, or
    ``None`` if it could not be fetched; a missing page produces no faults and
    one entry in ``skipped``, because an unreachable page is a statement about
    the fetch and not about the page.

    ``sitemap_ids`` is the set of listing ids currently in the sitemap, if we
    have it. Without it the exposure checks are skipped, not assumed clean.
    """

    listing_id = int(listing.get("id") or 0)
    faults = []
    skipped = {}
    evidence = {}

    path = marketplace_seo.PRODUCT_PATH.format(listing_id=listing_id)
    eligibility = marketplace_seo.eligibility(listing)
    decision = search_visibility.classify(path)
    evidence["policy:directive"] = Observation(decision.directive, SOURCE_POLICY, path)
    evidence["policy:path_indexable"] = Observation(decision.indexable, SOURCE_POLICY, decision.reason)
    evidence["db:record_indexable"] = Observation(eligibility.indexable, SOURCE_DATABASE, eligibility.reason)
    evidence["db:feed_eligible"] = Observation(eligibility.feed_eligible, SOURCE_DATABASE, eligibility.reason)

    label_obs, variant_obs = database_price(listing)
    evidence["db:price_label"] = label_obs
    evidence["db:price_variants"] = variant_obs

    if not (listing.get("variants") or ()):
        skipped["DB_PRICE_AUTHORITIES_DISAGREE"] = "listing loaded without variants"
    elif marketplace_seo.price_label_contradicts_variants(listing):
        faults.append(
            Fault(
                "DB_PRICE_AUTHORITIES_DISAGREE",
                P0,
                f"price_label {listing.get('price_label')!r} is not what "
                f"{variant_obs.detail} charges ({variant_obs.value})",
            )
        )

    feed = None
    try:
        feed = merchant_center_feed.feed_row(listing)
    except ValueError as exc:
        # `feed_row` raises only where it cannot reason -- an unparseable price
        # on a row it was told is feed-eligible, or an availability with no
        # Merchant spelling. Both are the module reporting an internal
        # contradiction, which is evidence, so it is recorded rather than
        # propagated.
        faults.append(Fault("FEED_ROW_REFUSED", P1, str(exc)))
    # Evidence, deliberately not a check. `feed_row` and the page's structured
    # data both read `price_label` through the same `parse_price`, so comparing
    # them here would compare a number to itself and pass whatever production is
    # doing. The feed claim worth verifying is the one Merchant Center actually
    # ingested, and no inbound Merchant evidence source exists for this property
    # -- see SOURCE_PROVIDER. So the number is recorded, and the gap is declared
    # rather than papered over with a green check.
    evidence["feed:price"] = Observation(
        _feed_price(feed),
        SOURCE_FEED,
        "g:price (not independently verifiable in-process)" if feed else "row not feed-eligible",
    )

    if page_html is None:
        skipped.update(
            {
                code: "page not fetched"
                for code in (
                    "PRICE_SURFACES_DISAGREE",
                    "PRICE_WIRE_VS_DB",
                    "AVAILABILITY_SURFACES_DISAGREE",
                    "AVAILABILITY_WIRE_VS_DB",
                    "CANONICAL_MISMATCH",
                    "ROBOTS_CONTRADICTS_POLICY",
                    "IDENTITY_MISMATCH",
                )
            }
        )
    else:
        faults.extend(
            _compare_wire(
                listing,
                page_html,
                evidence,
                skipped,
                label_obs,
                variant_obs,
                decision,
                eligibility,
            )
        )

    faults.extend(_compare_exposure(listing_id, sitemap_ids, evidence, skipped, decision, eligibility))
    return Verdict(listing_id=listing_id, faults=tuple(faults), evidence=evidence, skipped=skipped)


def _feed_price(feed):
    if not feed:
        return None
    raw = str(feed.get("g:price") or "")
    amount, _, currency = raw.rpartition(" ")
    return normalize_money(amount or raw, currency or None)


def compare_page(listing_id, page_html):
    """Compare one served page against itself and against path-level policy.

    No database, and therefore no ``import bot`` -- which matters, because
    importing ``bot`` opens a connection and runs ``init_db()`` at module
    scope. That makes this the entry point a sentinel can safely point at
    production from anywhere, and it is not a weakened version of
    ``compare_listing``: the strongest finding available here, a page that
    states two different prices in two of its own tags, needs no row to detect.

    What it cannot do is judge *record-level* indexability, because thinness,
    imagelessness and an unparseable price are properties of the row. So a page
    sending ``noindex`` under an indexable path is recorded as UNKNOWN rather
    than as a fault: that is exactly what a legitimately thin listing looks
    like. The reverse -- a page sending ``index`` under a path policy says is
    not indexable -- needs no row to be wrong, and is a fault.
    """

    faults = []
    skipped = {}
    evidence = {}
    path = marketplace_seo.PRODUCT_PATH.format(listing_id=int(listing_id or 0))
    decision = search_visibility.classify(path)
    evidence["policy:directive"] = Observation(decision.directive, SOURCE_POLICY, path)
    evidence["policy:path_indexable"] = Observation(decision.indexable, SOURCE_POLICY, decision.reason)

    if page_html is None:
        skipped.update(
            {
                "PRICE_SURFACES_DISAGREE": "page not fetched",
                "AVAILABILITY_SURFACES_DISAGREE": "page not fetched",
                "CANONICAL_MISMATCH": "page not fetched",
                "ROBOTS_CONTRADICTS_POLICY": "page not fetched",
                "IDENTITY_MISMATCH": "page not fetched",
            }
        )
        return Verdict(int(listing_id or 0), (), evidence, skipped)

    faults.extend(_wire_internal(int(listing_id or 0), page_html, evidence, skipped))

    robots = robots_from_html(page_html)
    if robots is None:
        pass  # already reported by _wire_internal
    elif "noindex" in robots.lower():
        if decision.indexable:
            skipped["ROBOTS_CONTRADICTS_POLICY"] = (
                "page sends noindex under an indexable path; record-level "
                "eligibility is unknown without the row, and a thin listing is "
                "supposed to look like this"
            )
    elif not decision.indexable:
        faults.append(
            Fault(
                "ROBOTS_CONTRADICTS_POLICY",
                P0,
                f"page sends {robots!r} on a path policy excludes ({decision.reason})",
            )
        )
    return Verdict(int(listing_id or 0), tuple(faults), evidence, skipped)


def _wire_internal(listing_id, page_html, evidence, skipped):
    """Checks that need only the page: surface agreement, canonical, identity."""

    faults = []

    prices = price_surfaces(page_html)
    for name, obs in prices.items():
        evidence[f"wire:price:{name}"] = obs
    stated = {name: _money_key(obs.value) for name, obs in prices.items() if obs.known}
    if len(set(stated.values())) > 1:
        faults.append(
            Fault(
                "PRICE_SURFACES_DISAGREE",
                P0,
                "one page states "
                + "; ".join(f"{n}={_show(v)}" for n, v in sorted(stated.items())),
            )
        )
    elif not stated:
        skipped["PRICE_SURFACES_DISAGREE"] = "page states no readable price"

    # Reported on its own rather than through `stated` above, because a sentence
    # holding two amounts contributes no comparable value to that dict: the
    # page-wide comparison cannot see this defect by construction.
    for surface, claims in sorted(prose_contradictions(page_html).items()):
        faults.append(
            Fault(
                "PRICE_PROSE_STATES_TWO_AMOUNTS",
                P0,
                f"{surface} states " + " and ".join(claims),
            )
        )

    stock = availability_surfaces(page_html)
    for name, obs in stock.items():
        evidence[f"wire:availability:{name}"] = obs
    claimed = {obs.value for obs in stock.values() if obs.known}
    if len(claimed) > 1:
        faults.append(Fault("AVAILABILITY_SURFACES_DISAGREE", P1, f"page states {sorted(claimed)}"))
    elif not claimed:
        skipped["AVAILABILITY_SURFACES_DISAGREE"] = "page states no stock status"

    canonical = canonical_from_html(page_html)
    expected_canonical = marketplace_seo.product_url(listing_id)
    evidence["wire:canonical"] = Observation(canonical, SOURCE_WIRE, "link rel=canonical")
    evidence["policy:canonical"] = Observation(
        expected_canonical, SOURCE_POLICY, "marketplace_seo.product_url"
    )
    if canonical is None:
        faults.append(Fault("CANONICAL_ABSENT", P1, "no link rel=canonical"))
    elif canonical != expected_canonical:
        faults.append(
            Fault("CANONICAL_MISMATCH", P0, f"page declares {canonical}, policy says {expected_canonical}")
        )

    robots = robots_from_html(page_html)
    evidence["wire:robots"] = Observation(robots, SOURCE_WIRE, "meta robots")
    if robots is None:
        faults.append(Fault("ROBOTS_ABSENT", P2, "no meta robots; the page inherits an unstated default"))

    product = _node_of_type(jsonld_nodes(page_html), "Product")
    sku = (product or {}).get("sku")
    evidence["wire:sku"] = Observation(sku, SOURCE_WIRE, "Product.sku")
    expected_sku = SKU_TEMPLATE.format(listing_id=listing_id)
    if sku is None:
        skipped["IDENTITY_MISMATCH"] = "no Product.sku on the page"
    elif sku != expected_sku:
        faults.append(Fault("IDENTITY_MISMATCH", P1, f"Product.sku is {sku!r}, expected {expected_sku!r}"))
    return faults


def _compare_wire(listing, page_html, evidence, skipped, label_obs, variant_obs, decision, eligibility):
    listing_id = int(listing.get("id") or 0)
    faults = _wire_internal(listing_id, page_html, evidence, skipped)

    stated = {
        name.split(":", 2)[2]: _money_key(obs.value)
        for name, obs in evidence.items()
        if name.startswith("wire:price:") and obs.known
    }
    distinct = set(stated.values())
    if len(distinct) == 1:
        wire = distinct.pop()
        db_keys = {
            name: _money_key(obs.value)
            for name, obs in (("price_label", label_obs), ("price_variants", variant_obs))
            if obs.known
        }
        if not db_keys:
            skipped["PRICE_WIRE_VS_DB"] = "no readable database price to compare"
        elif wire not in db_keys.values():
            # Covers a currency divergence as well as an amount one, because the
            # comparison key is (amount, currency). A separate CURRENCY_MISMATCH
            # code would be unreachable -- "$30.50 on a EUR row" never reaches a
            # matching amount to then disagree about denomination.
            faults.append(
                Fault(
                    "PRICE_WIRE_VS_DB",
                    P0,
                    f"page states {_show(wire)}; database states "
                    + ", ".join(f"{n}={_show(v)}" for n, v in sorted(db_keys.items())),
                )
            )
    elif not stated:
        if eligibility.indexable and (label_obs.known or variant_obs.known):
            faults.append(
                Fault("PRICE_ABSENT_ON_WIRE", P1, "no readable price on an indexable priced listing")
            )
        else:
            skipped["PRICE_WIRE_VS_DB"] = "page states no price"
    else:
        skipped["PRICE_WIRE_VS_DB"] = "page disagrees with itself; fix that before comparing to the row"

    claimed = {
        obs.value
        for name, obs in evidence.items()
        if name.startswith("wire:availability:") and obs.known
    }
    if len(claimed) == 1:
        expected = marketplace_seo.availability(listing)
        evidence["db:availability"] = Observation(
            expected, SOURCE_DATABASE, "marketplace_seo.availability"
        )
        stated_stock = claimed.pop()
        if stated_stock != expected:
            faults.append(
                Fault("AVAILABILITY_WIRE_VS_DB", P1, f"page states {stated_stock}; the row says {expected}")
            )
    else:
        skipped["AVAILABILITY_WIRE_VS_DB"] = "no single stock claim on the page"

    robots = robots_from_html(page_html)
    if robots is not None:
        wire_noindex = "noindex" in robots.lower()
        want_noindex = not (decision.indexable and eligibility.indexable)
        if wire_noindex != want_noindex:
            faults.append(
                Fault(
                    "ROBOTS_CONTRADICTS_POLICY",
                    P0,
                    f"page sends {robots!r}; policy+record want "
                    f"{'noindex' if want_noindex else 'index'} ({decision.reason}; {eligibility.reason})",
                )
            )
    return faults


def _compare_exposure(listing_id, sitemap_ids, evidence, skipped, decision, eligibility):
    faults = []
    indexable = bool(decision.indexable and eligibility.indexable)
    if sitemap_ids is None:
        skipped["PRIVATE_IN_SITEMAP"] = "sitemap not supplied"
        skipped["SITEMAP_MISSING_INDEXABLE"] = "sitemap not supplied"
        return faults

    present = listing_id in set(sitemap_ids)
    evidence["sitemap:present"] = Observation(present, SOURCE_WIRE, "sitemap listing ids")
    if present and not indexable:
        faults.append(
            Fault(
                "PRIVATE_IN_SITEMAP",
                P0,
                f"listing {listing_id} is submitted for indexing but is not indexable "
                f"({decision.reason}; {eligibility.reason})",
            )
        )
    elif indexable and not present:
        # P2 on purpose: sitemap generation is not this module's to own and a
        # freshly published listing is legitimately absent until the next
        # regeneration. It is reported so the lag is measurable, not so it can
        # be alarmed on.
        faults.append(Fault("SITEMAP_MISSING_INDEXABLE", P2, "indexable listing absent from the sitemap"))
    return faults
