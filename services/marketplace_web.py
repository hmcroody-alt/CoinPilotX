"""Commerce derivation for the PulseSoc web Marketplace.

Everything a buyer-facing web page needs to say about a listing is derived here,
from the row and its variants, and nowhere else. The two Flask routes own SQL and
HTML; this module owns the facts.

The rule the whole module exists to enforce: **a commerce claim is rendered only
when the data proves it.** The catalogue is supplier-imported, so most of the
fields a storefront would normally lean on are absent — there is no review table,
no sales counter, no compare-at price, no shipping rate card, and
``estimated_delivery`` is empty on every row in production. Rather than leave that
judgement to each call site, every derivation below returns ``None`` or an empty
sequence when its evidence is missing, and the templates render nothing for a
missing value. A field that cannot be proven has no fallback copy, because
fallback copy on a commerce surface is indistinguishable from a claim.

What IS provable, measured against production on 2026-09-26:

* ``category`` is a supplier breadcrumb ("Women's Clothing > Tops & Sets >
  Blouses & Shirts"), so a real department/section/leaf taxonomy can be parsed
  out of it rather than hard-coded. Two delimiters are in live use, ``>`` and
  ``/``, plus one row with a fullwidth comma.
* ``marketplace_listing_variants`` holds 770 real rows over 41 listings with
  per-variant ``price_cents`` and ``stock_state``, so price ranges, colour/size
  controls and stock are real. ``price_label`` is set on only 21 of 47 rows and
  is a display string, so variants are the price authority and the label is the
  fallback.
* Descriptions are raw supplier text averaging 568 characters and carrying
  packed ``Key: Value`` attribute runs. Those are extracted, not invented: the
  spec table is a re-presentation of the seller's own words.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

PUBLIC_ORIGIN = "https://pulsesoc.com"

# A listing counts as new for this long after publication. Used for the only
# time-derived badge; everything else a storefront would put in that slot
# (trending, best seller, top rated, deal) has no data behind it and is absent
# from `BADGE_*` on purpose.
NEW_LISTING_WINDOW = timedelta(days=14)

BADGE_NEW = "new"
BADGE_FEATURED = "featured"
BADGE_DIGITAL = "digital"

# Supplier breadcrumbs use both of these, sometimes in the same catalogue.
_CATEGORY_SPLIT = re.compile(r"\s*(?:>|/|›|»)\s*")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
# Dropped rather than hyphenated, so "Men's Clothing" slugs to `mens-clothing`
# and not `men-s-clothing`. Without this the same department arrives twice.
_SLUG_DROP = re.compile(r"[’'`’]")
_WHITESPACE = re.compile(r"\s+")

# Capitalised words that may begin a key even though a capitalised word normally
# ends the backwards scan. Every one is observed leading a real key in the live
# catalogue ("Main Color", "Assembled Length (cm)", "Waist Type"). The scan stops
# at any other capitalised word, which is why the value "Four Seasons" does not
# donate "Seasons" to the key that follows it.
_KEY_MODIFIERS = frozenset({
    "Main", "Product", "Assembled", "Applicable", "Suitable", "Bed", "Waist",
    "Pants", "Clothing", "Lining", "Fabric", "Scope", "Sub-item", "Packing",
    "Net", "Gross", "Total", "Shipping", "Care", "Sleeve", "Collar", "Closure",
})
# Absorbed only *between* a modifier and the word that closed the key, so
# "Scope of Application" survives while a value ending in "of" does not donate it.
_KEY_CONNECTORS = frozenset({"of", "for", "and", "per", "in", "with", "to"})

# A value may trail off into the next section's heading, because the supplier run
# has no delimiter there either ("Bed Size: 135cm*190cm Product Dimensions",
# "Color: ... navy blue Size"). A trailing run of *capitalised* words from this
# set is that heading. The capitalisation requirement is what protects a real
# value like "free size" from being trimmed to "free", and the value is never
# emptied -- a one-word value is left alone whatever it says.
_TRAILING_SECTION_WORDS = frozenset({
    "product", "products", "dimensions", "dimension", "package", "packing",
    "size", "sizes", "weight", "information", "info", "specification",
    "specifications", "details", "parameters", "features", "image", "images",
    "list", "shipping", "note", "notes", "chart",
})
_NOTE_HEADING = re.compile(
    r"(?:^|\n|\s)((?:warm\s+)?(?:note|notes|notice|tips?|attention|reminder|warning|disclaimer)s?)\s*[:：]",
    re.IGNORECASE,
)

# Keys that are section headings rather than attributes -- they introduce the
# run that follows and have no value of their own.
_ATTRIBUTE_HEADINGS = frozenset({
    "product information", "product info", "specification", "specifications",
    "description", "details", "product details", "parameter", "parameters",
    "features", "feature", "item specifics",
})
_HEADING_WORDS = frozenset(w for heading in _ATTRIBUTE_HEADINGS for w in heading.split()) | {
    "specifications", "parameters", "spec", "specs", "size", "sizes",
}

_SIZE_TOKENS = frozenset({
    "xxxs", "xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "xxxxl",
    "2xl", "3xl", "4xl", "5xl", "6xl", "7xl", "8xl",
    "one size", "onesize", "free size", "os",
})
_SIZE_PATTERN = re.compile(r"^(?:\d{1,2}(?:\.\d)?|[2-8]xl|x{0,3}[sml]|eu\s?\d{2}|uk\s?\d{1,2}|us\s?\d{1,2})$")
_COLOR_WORDS = frozenset({
    "black", "white", "red", "blue", "green", "grey", "gray", "navy", "beige",
    "pink", "purple", "violet", "yellow", "orange", "brown", "khaki", "burgundy",
    "ivory", "gold", "silver", "olive", "teal", "cream", "apricot", "wine",
    "coffee", "camel", "rose", "mint", "lavender", "turquoise", "maroon",
    "champagne", "charcoal", "transparent", "multicolor", "colorful",
})
_QUANTITY_PATTERN = re.compile(r"^\d+\s*(?:pcs?|pairs?|sets?|packs?|boxes|bags?|rolls?)$", re.IGNORECASE)
_STYLE_PATTERN = re.compile(r"^(?:style|model|type|pattern)\s*\d*$", re.IGNORECASE)

_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "JPY": "¥"}

_PRICE_LABEL = re.compile(r"(\d{1,3}(?:,\d{3})*|\d+)(?:[.,](\d{1,2}))?")


def esc(value: Any) -> str:
    """HTML-escape for text and attribute positions alike.

    Every value on these pages is untrusted: titles, descriptions and category
    breadcrumbs are supplier-imported, and the seller controls the rest. Nothing
    in this module emits a caller's string without passing it through here, and
    nothing builds markup with ``innerHTML`` on the client either.
    """
    return html.escape(str(value if value is not None else ""), quote=True)


def _clean(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value if value is not None else "")).strip()


def slugify(value: Any) -> str:
    """The public, URL-facing form of a category label.

    Apostrophes are dropped instead of hyphenated so the two spellings a supplier
    feed uses for one department ("Mens Clothing", "Men's Clothing") produce one
    slug. This is what appears in ``?category=``, so it stays readable.
    """
    return _SLUG_STRIP.sub("-", _SLUG_DROP.sub("", _clean(value).lower())).strip("-")


def _fold_slug(slug: str) -> str:
    """The internal, never-displayed key two slugs are compared on.

    Naive singularisation, applied to both sides of every comparison, so
    "Bag & Shoes" and "Bags & Shoes" are one department. It is deliberately not
    used for the URL: folding the *slug* would ship ``jewelry-watche`` to users.
    A false merge needs two real categories differing only in a trailing "s",
    which the live catalogue does not contain.
    """
    parts = []
    for segment in str(slug or "").split("/"):
        words = []
        for word in segment.split("-"):
            if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
                word = word[:-1]
            words.append(word)
        parts.append("-".join(words))
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def parse_category_path(raw: Any) -> list[str]:
    """Split a supplier breadcrumb into its levels.

    ``"Women's Clothing > Tops & Sets > Blouses & Shirts"`` and
    ``"Jewelry & Watches / Fashion Jewelry / Rings"`` are the same shape from two
    suppliers; a flat ``"Education"`` is a one-level path. The fullwidth comma in
    ``"Toys， Kids & Baby"`` is folded so it does not read as a separate word.
    """
    text = _clean(str(raw or "").replace("，", ","))
    if not text:
        return []
    return [part for part in (_clean(p) for p in _CATEGORY_SPLIT.split(text)) if part]


@dataclass(frozen=True)
class CategoryNode:
    slug: str
    label: str
    count: int
    children: tuple["CategoryNode", ...] = ()


def build_taxonomy(category_values: Iterable[Any], max_depth: int = 2) -> list[CategoryNode]:
    """Aggregate the live catalogue's breadcrumbs into a navigable tree.

    A supplier feed disagrees with itself about spelling: this catalogue carries
    both "Mens Clothing" and "Men's Clothing", and both "Bag & Shoes" and
    "Bags & Shoes". Grouping happens on the folded key so each of those is one
    department; the label and the URL slug are the most common real spelling, so
    the merge is invisible and no ``jewelry-watche`` ever reaches a user.
    """
    levels: list[dict[str, dict[str, Any]]] = [{} for _ in range(max_depth)]
    for raw in category_values:
        path = parse_category_path(raw)[:max_depth]
        parent_key = ""
        for depth, label in enumerate(path):
            segment = slugify(label)
            if not segment:
                break
            folded = _fold_slug(segment)
            key = folded if depth == 0 else f"{parent_key}/{folded}"
            bucket = levels[depth].setdefault(
                key, {"labels": {}, "segments": {}, "count": 0, "parent": parent_key}
            )
            bucket["labels"][label] = bucket["labels"].get(label, 0) + 1
            bucket["segments"][segment] = bucket["segments"].get(segment, 0) + 1
            bucket["count"] += 1
            parent_key = key

    def _winner(bucket: Mapping[str, Any], field_name: str) -> str:
        return max(bucket[field_name].items(), key=lambda item: (item[1], item[0]))[0]

    nodes: list[CategoryNode] = []
    for bucket in sorted(levels[0].values(), key=lambda b: (-b["count"], _winner(b, "labels"))):
        parent_slug = _winner(bucket, "segments")
        parent_key = _fold_slug(parent_slug)
        children = tuple(
            CategoryNode(
                slug=f"{parent_slug}/{_winner(child, 'segments')}",
                label=_winner(child, "labels"),
                count=child["count"],
            )
            for child in sorted(
                (c for c in levels[1].values() if c["parent"] == parent_key),
                key=lambda b: (-b["count"], _winner(b, "labels")),
            )
        ) if max_depth > 1 else ()
        nodes.append(CategoryNode(
            slug=parent_slug, label=_winner(bucket, "labels"), count=bucket["count"], children=children,
        ))
    return nodes


def category_matches(raw: Any, slug: str) -> bool:
    """Does a listing's breadcrumb sit under ``slug``?

    ``slug`` is a prefix path ("mens-clothing" or "mens-clothing/bottoms"), so a
    department selects every section beneath it without the caller enumerating
    them. Both sides are folded, so the department link built from the majority
    spelling still selects the listings filed under the minority one -- and an
    old link that used the minority spelling keeps working.
    """
    wanted = _fold_slug(str(slug or "").strip("/").lower())
    if not wanted:
        return True
    candidate = ""
    for part in (_fold_slug(slugify(p)) for p in parse_category_path(raw)):
        candidate = part if not candidate else f"{candidate}/{part}"
        if candidate == wanted:
            return True
    return False


def category_crumbs(raw: Any) -> list[tuple[str, str]]:
    """``[(label, slug)]`` for a listing's own breadcrumb, deepest last."""
    crumbs: list[tuple[str, str]] = []
    slug = ""
    for label in parse_category_path(raw):
        part = slugify(label)
        if not part:
            continue
        slug = part if not slug else f"{slug}/{part}"
        crumbs.append((label, slug))
    return crumbs


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def format_money(cents: Optional[int], currency: str = "USD") -> str:
    if cents is None:
        return ""
    code = (str(currency or "USD").strip().upper() or "USD")
    symbol = _CURRENCY_SYMBOLS.get(code)
    amount = f"{int(cents) / 100:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{code} {amount}"


def parse_price_label_cents(label: Any) -> Optional[int]:
    """Best-effort cents from a seller-typed price string.

    Only reached when a listing has no variants. Returns ``None`` rather than
    zero for an unparseable label, because zero is a price and "no price" is not.
    """
    text = str(label or "").strip()
    if not text:
        return None
    match = _PRICE_LABEL.search(text)
    if not match:
        return None
    whole = match.group(1).replace(",", "")
    frac = (match.group(2) or "0").ljust(2, "0")[:2]
    try:
        return int(whole) * 100 + int(frac)
    except ValueError:
        return None


@dataclass(frozen=True)
class PriceView:
    currency: str = "USD"
    min_cents: Optional[int] = None
    max_cents: Optional[int] = None
    source: str = "none"  # variants | label | none

    @property
    def known(self) -> bool:
        return self.min_cents is not None

    @property
    def is_range(self) -> bool:
        return self.known and self.max_cents is not None and self.max_cents > self.min_cents

    @property
    def display(self) -> str:
        if not self.known:
            return ""
        low = format_money(self.min_cents, self.currency)
        if not self.is_range:
            return low
        return f"{low} – {format_money(self.max_cents, self.currency)}"

    def as_schema_offer(self) -> Optional[dict[str, Any]]:
        """schema.org Offer/AggregateOffer, or ``None`` when there is no price.

        Structured data is the one place where a wrong number is invisible to
        the person who wrote it and visible to everyone else, so this mirrors the
        page exactly rather than reaching for a default.
        """
        if not self.known:
            return None
        if self.is_range:
            return {
                "@type": "AggregateOffer",
                "priceCurrency": self.currency,
                "lowPrice": f"{self.min_cents / 100:.2f}",
                "highPrice": f"{self.max_cents / 100:.2f}",
            }
        return {
            "@type": "Offer",
            "priceCurrency": self.currency,
            "price": f"{self.min_cents / 100:.2f}",
        }


def derive_price(listing: Mapping[str, Any], variants: Sequence[Mapping[str, Any]] = ()) -> PriceView:
    """Price authority: active variants first, then the seller's own label.

    ``price_label`` is set on fewer than half the live rows and is a display
    string a human typed; ``marketplace_listing_variants.price_cents`` is what
    the supplier sync writes and what checkout would charge. Preferring the label
    would have priced a 42-variant listing from a field its seller never filled.
    """
    currency = (str(listing.get("currency") or "USD").strip().upper() or "USD")
    prices = [
        int(v["price_cents"]) for v in variants
        if v.get("price_cents") is not None and str(v.get("status") or "active").lower() == "active"
    ]
    if prices:
        variant_currency = next(
            (str(v.get("currency")).strip().upper() for v in variants if v.get("currency")), currency)
        return PriceView(currency=variant_currency, min_cents=min(prices), max_cents=max(prices), source="variants")
    label_cents = parse_price_label_cents(listing.get("price_label"))
    if label_cents is not None:
        return PriceView(currency=currency, min_cents=label_cents, max_cents=label_cents, source="label")
    return PriceView(currency=currency, source="none")


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VariantOption:
    value: str
    label: str
    swatch: str = ""


@dataclass(frozen=True)
class OptionGroup:
    key: str
    label: str
    kind: str  # color | size | style | quantity | option
    options: tuple[VariantOption, ...]


def _looks_like_size(values: Sequence[str]) -> bool:
    hits = sum(1 for v in values if v.strip().lower() in _SIZE_TOKENS or _SIZE_PATTERN.match(v.strip().lower()))
    return bool(values) and hits / len(values) >= 0.6


def _looks_like_color(values: Sequence[str]) -> bool:
    hits = sum(1 for v in values if any(word in v.lower().split() for word in _COLOR_WORDS))
    return bool(values) and hits / len(values) >= 0.6


def _looks_like_quantity(values: Sequence[str]) -> bool:
    hits = sum(1 for v in values if _QUANTITY_PATTERN.match(v.strip()))
    return bool(values) and hits / len(values) >= 0.6


def _looks_like_style(values: Sequence[str]) -> bool:
    hits = sum(1 for v in values if _STYLE_PATTERN.match(v.strip()))
    return bool(values) and hits / len(values) >= 0.6


def _option_kind(values: Sequence[str]) -> str:
    """Name an option group from the values it actually contains.

    The supplier feed names every group ``option1``/``option2``, which is true
    but useless -- a shopper cannot pick a size from a control called "Option 2".
    Classifying the *values* against fixed vocabularies keeps the label
    answerable from the data: a group is "Size" because its members are sizes.
    When the values do not clearly belong to any vocabulary the group keeps its
    positional name rather than being guessed into one.
    """
    if _looks_like_size(values):
        return "size"
    if _looks_like_color(values):
        return "color"
    if _looks_like_quantity(values):
        return "quantity"
    if _looks_like_style(values):
        return "style"
    return "option"


_KIND_LABELS = {"color": "Color", "size": "Size", "quantity": "Quantity", "style": "Style"}


def parse_variant_options(options_json: Any) -> list[tuple[str, str]]:
    try:
        parsed = json.loads(options_json) if isinstance(options_json, (str, bytes)) else options_json
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    pairs: list[tuple[str, str]] = []
    for entry in parsed:
        if isinstance(entry, Mapping):
            name, value = _clean(entry.get("name")), _clean(entry.get("value"))
            if name and value:
                pairs.append((name, value))
    return pairs


def build_option_groups(variants: Sequence[Mapping[str, Any]]) -> list[OptionGroup]:
    ordered: list[str] = []
    values_by_key: dict[str, list[str]] = {}
    for variant in variants:
        if str(variant.get("status") or "active").lower() != "active":
            continue
        for name, value in parse_variant_options(variant.get("options_json")):
            if name not in values_by_key:
                values_by_key[name] = []
                ordered.append(name)
            if value not in values_by_key[name]:
                values_by_key[name].append(value)

    kinds = {key: _option_kind(values_by_key[key]) for key in ordered}
    # Number the unnamed groups among themselves. Numbering them by their overall
    # position produced a lone control called "Option 2" sitting under "Color",
    # with no "Option 1" anywhere on the page.
    unnamed = [key for key in ordered if kinds[key] not in _KIND_LABELS]
    positions = {key: i for i, key in enumerate(unnamed, start=1)}

    groups: list[OptionGroup] = []
    for key in ordered:
        values = values_by_key[key]
        if len(values) < 1:
            continue
        kind = kinds[key]
        label = _KIND_LABELS.get(kind)
        if not label:
            label = "Option" if len(unnamed) == 1 else f"Option {positions[key]}"
        groups.append(OptionGroup(
            key=key,
            label=label,
            kind=kind,
            options=tuple(VariantOption(value=v, label=v, swatch=_swatch_for(v) if kind == "color" else "") for v in values),
        ))
    return groups


_SWATCH_HEXES = {
    "black": "#111417", "white": "#f4f5f7", "red": "#d43b3b", "blue": "#3b6fd4",
    "navy": "#1f2a54", "green": "#3fa06a", "grey": "#8b9199", "gray": "#8b9199",
    "beige": "#d9c9ae", "pink": "#e58cb0", "purple": "#8b5fd0", "violet": "#8b5fd0",
    "yellow": "#e8c84a", "orange": "#e2853c", "brown": "#6f4b32", "khaki": "#b6a67c",
    "burgundy": "#6d2338", "ivory": "#efe8d8", "gold": "#c8a24a", "silver": "#c3c8cf",
    "olive": "#6d7340", "teal": "#2f8f8a", "cream": "#f0e6d2", "wine": "#6d2338",
    "coffee": "#5b4034", "camel": "#c19a6b", "rose": "#d98a95", "mint": "#9fd8bf",
    "lavender": "#b9a7e0", "turquoise": "#40c0c0", "maroon": "#5c2230",
    "champagne": "#e4d3b4", "charcoal": "#36393d", "apricot": "#e8b183",
}


def _swatch_for(value: str) -> str:
    """A colour chip is only drawn when the value names a colour we can map.

    An unmappable value ("Navy Blue Sparkling Style") still gets a text control;
    it just does not get a square of the wrong colour next to it.
    """
    words = value.lower().split()
    for word in words:
        hexcode = _SWATCH_HEXES.get(word)
        if hexcode and hexcode.startswith("#") and len(hexcode) == 7:
            return hexcode
    return ""


@dataclass(frozen=True)
class VariantView:
    variant_id: int
    key: str
    options: tuple[tuple[str, str], ...]
    price_cents: Optional[int]
    currency: str
    available: bool
    #: The supplier's own three-valued answer, kept rather than collapsed into
    #: ``available`` so the stock line can distinguish "sold out" from
    #: "the supplier has not told us yet".
    stock_state: str = "UNKNOWN"
    stock_quantity: Optional[int] = None

    @property
    def stock_label(self) -> str:
        """What this exact variant's availability may be *said* to be.

        Returns an empty string for UNKNOWN. That is the whole point of the
        three-valued state: a variant nobody has confirmed gets no availability
        sentence at all, rather than an optimistic one.
        """
        state = str(self.stock_state or "UNKNOWN").upper()
        if state == "OUT_OF_STOCK" or not self.available:
            return "Out of stock"
        if state != "IN_STOCK":
            return ""
        # A count is only shown when it is low enough to be decision-relevant
        # *and* real. "1,284 left" is noise; "2 left" changes behaviour.
        if self.stock_quantity is not None and 0 < int(self.stock_quantity) <= 5:
            left = int(self.stock_quantity)
            return f"Only {left} left" if left > 1 else "Last one"
        return "In stock"

    def as_client_dict(self) -> dict[str, Any]:
        """The shape `static/js/pulse_marketplace.js` reads.

        ``price`` is the *formatted string this server produced*, never a raw
        number, so the browser cannot round, convert or re-format a price. The
        client picks between strings the server already wrote; it never computes
        one. ``key`` is the opaque ``variant_key`` the checkout route resolves,
        so the form carries an identifier rather than a price.
        """
        return {
            "id": self.variant_id,
            "key": self.key,
            "options": {name: value for name, value in self.options},
            "price": format_money(self.price_cents, self.currency),
            "available": self.available,
            "stock_label": self.stock_label,
        }


def build_variant_views(variants: Sequence[Mapping[str, Any]], currency: str = "USD") -> list[VariantView]:
    views: list[VariantView] = []
    for variant in variants:
        if str(variant.get("status") or "active").lower() != "active":
            continue
        options = tuple(parse_variant_options(variant.get("options_json")))
        if not options:
            continue
        state = str(variant.get("stock_state") or "UNKNOWN").upper()
        quantity = variant.get("stock_quantity")
        # Three-valued on purpose: UNKNOWN is not OUT_OF_STOCK. A supplier that
        # has not answered yet must not be rendered as sold out, and must not be
        # rendered as buyable either -- it stays selectable and the stock line
        # simply says nothing.
        available = state != "OUT_OF_STOCK" and (quantity is None or int(quantity) > 0)
        views.append(VariantView(
            variant_id=int(variant.get("id") or 0),
            key=str(variant.get("variant_key") or ""),
            options=options,
            price_cents=int(variant["price_cents"]) if variant.get("price_cents") is not None else None,
            currency=str(variant.get("currency") or currency).upper(),
            available=available,
            stock_state=state,
            stock_quantity=int(quantity) if quantity is not None else None,
        ))
    return views


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DescriptionView:
    summary: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    body: str = ""


def _split_key(prefix: str) -> tuple[str, str]:
    """Split the text before a colon into ``(tail of the previous value, key)``.

    This is the whole difficulty of the supplier format. A run reads

        Pattern: Jacquard Color: black, red, light gray Size: M,L,XL

    with no delimiter between one value and the next key, so "where does the key
    begin" has to be decided from the words themselves. Scanning *backwards* from
    the colon answers it: lowercase words are collected (they can only be the
    tail of a multi-word key like "Fabric name"), the first capitalised word
    closes the key, and only a known modifier ("Main", "Assembled", "Waist") is
    absorbed beyond it. That is what keeps "Color" out of "navy blue Color" while
    still recovering "Main fabric composition" whole.

    A comma ends the search: a comma-separated list belongs to the value. So does
    anything before a newline -- a key never spans one -- which is what lets
    "Packing list:\\nJacket suit*1set" keep its value.
    """
    prefix = prefix.rsplit("\n", 1)[-1] if "\n" in prefix else prefix
    words = prefix.split()
    key_words: list[str] = []
    index = len(words)
    while index > 0 and len(key_words) < 4:
        word = words[index - 1]
        if word.endswith((",", ";", "，", "、", ":", "·")):
            break
        bare = word.strip("()[]【】").strip(".")
        if not bare:
            break
        if bare[0].isupper():
            key_words.insert(0, word)
            index -= 1
            while index > 0 and len(key_words) < 4:
                previous = words[index - 1]
                if previous.endswith((",", ";", "，")):
                    break
                stripped = previous.strip("()[]")
                if stripped not in _KEY_MODIFIERS and stripped.lower() not in _KEY_CONNECTORS:
                    break
                key_words.insert(0, previous)
                index -= 1
            # A connector cannot be the start of a key ("of Application"); if the
            # absorb loop stopped on one, give it back to the value.
            while key_words and key_words[0].lower() in _KEY_CONNECTORS:
                key_words.pop(0)
                index += 1
            break
        key_words.insert(0, word)
        index -= 1
    return " ".join(words[:index]), " ".join(key_words)


def _parse_attribute_run(text: str) -> tuple[str, list[tuple[str, str]]]:
    """``(text before the first key, [(key, value)])`` for one supplier run."""
    lead = ""
    attributes: list[tuple[str, str]] = []
    pending_key = ""
    cursor = 0
    for match in re.finditer(r"[:：]", text):
        value_tail, key = _split_key(text[cursor:match.start()])
        if not key:
            # A colon with no key in front of it is punctuation inside a value
            # ("10:30", "Ratio 1:1"). Leaving the cursor alone keeps it there.
            continue
        if pending_key:
            attributes.append((pending_key, value_tail))
        else:
            lead = value_tail
        pending_key = key
        cursor = match.end()
    if pending_key:
        attributes.append((pending_key, text[cursor:]))
    return lead, attributes


def _split_notes(text: str) -> tuple[str, list[str]]:
    match = _NOTE_HEADING.search(text)
    if not match:
        return text, []
    head, tail = text[: match.start()], text[match.end():]
    notes = [_clean(part) for part in re.split(r"(?:\n|(?<=[.!?])\s+(?=\d+[\.)]\s))", tail)]
    return head, [n for n in notes if len(n) > 3]


def parse_description(raw: Any) -> DescriptionView:
    """Turn supplier prose into a spec table plus a short human summary.

    Imported descriptions pack their real content into unpunctuated ``Key: Value``
    runs -- "Pattern: Jacquard Color: black, red Size: M,L,XL Version: Loose" --
    followed by boilerplate about measurement tolerance and monitor calibration.
    Rendered verbatim, as this page did, that paragraph is the loudest element
    between the price and the buy button.

    So the run is split back into the attributes it already was, and the
    boilerplate is kept but demoted to a disclosure. Nothing is summarised,
    rewritten or inferred: every key and every value below is a substring of what
    the seller supplied. When fewer than two attributes can be recovered the text
    is left alone as prose, because a one-row table is a worse rendering of a
    sentence than the sentence.
    """
    text = _clean(str(raw or "").replace("\r", "\n"))
    if not text:
        return DescriptionView()

    # Newlines survive into the scanner on purpose: they are the only reliable
    # delimiter this format has, and _split_key uses them to bound a key.
    body, notes = _split_notes(str(raw or "").replace("\r", "\n"))

    lead, raw_attributes = _parse_attribute_run(body)
    attributes: list[tuple[str, str]] = []
    for key, value in raw_attributes:
        key, value = _clean(key), _strip_trailing_heading(_clean(value))
        if key.lower() in _ATTRIBUTE_HEADINGS or not value:
            continue
        if len(key) < 2 or len(value) > 180:
            continue
        attributes.append((key, value))
    lead = "" if _is_heading_only(lead) else _clean(lead)

    if len(attributes) < 2:
        return DescriptionView(summary=_first_sentences(text), notes=tuple(_clean(n) for n in notes), body=text)

    # Deduplicate on key, keeping the first value a supplier gave.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for key, value in attributes:
        folded = key.lower()
        if folded in seen:
            continue
        seen.add(folded)
        unique.append((key, value))

    return DescriptionView(
        summary=_first_sentences(lead) if lead else "",
        attributes=tuple(unique),
        notes=tuple(_clean(n) for n in notes),
        body=text,
    )


def _strip_trailing_heading(value: str) -> str:
    """Drop a section heading that the supplier ran into the end of a value."""
    words = value.split()
    cut = len(words)
    while cut > 1:
        word = words[cut - 1]
        if word[:1].isupper() and word.strip("()[]:，,.").lower() in _TRAILING_SECTION_WORDS:
            cut -= 1
            continue
        break
    if cut == len(words):
        return value
    return " ".join(words[:cut])


def _is_heading_only(text: str) -> bool:
    """Is this fragment nothing but section headings ("Specification Product Information")?

    Such a fragment is the residue of a heading that had no value of its own. It
    is not a summary of the product, so it is dropped rather than shown as one.
    """
    words = [w.strip("()[]:：,.").lower() for w in _clean(text).split()]
    words = [w for w in words if w]
    return bool(words) and all(w in _HEADING_WORDS for w in words)


def _first_sentences(text: str, limit: int = 220) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if boundary > 80:
        return cut[: boundary + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def meta_description(listing: Mapping[str, Any], price: PriceView, description: DescriptionView) -> str:
    """One sentence for search results and link previews, built from real fields."""
    parts: list[str] = []
    short = _clean(listing.get("short_description"))
    if short:
        parts.append(short)
    elif description.summary:
        parts.append(description.summary)
    elif description.attributes:
        parts.append(", ".join(f"{k}: {v}" for k, v in description.attributes[:3]))
    if price.known:
        parts.append(f"{price.display} on PulseSoc Marketplace.")
    else:
        parts.append("On PulseSoc Marketplace.")
    return _first_sentences(" ".join(parts), 300)


# ---------------------------------------------------------------------------
# Badges and stock
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Badge:
    key: str
    label: str


def _parse_timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def classify_badges(listing: Mapping[str, Any], now: Optional[datetime] = None) -> list[Badge]:
    """The only badges PulseSoc can prove.

    A storefront badge rail normally carries BEST SELLER / TRENDING / TOP RATED /
    20% OFF. None of those has a source here -- there is no order table feeding
    this catalogue, no review table at all, and no compare-at price column, so
    every one of them would be decoration wearing the costume of a fact. The
    three below are each a single column read.

    "New" reads ``published_at`` and deliberately does **not** fall back to
    ``created_at``. 29 of the 47 live rows have a null ``published_at`` and a
    ``created_at`` from the bulk supplier import, so the fallback dated a
    product by when PulseSoc ingested it -- it put "New" on 33 of 47 rows at
    once, which is a fact about the importer and not about the product.
    """
    badges: list[Badge] = []
    if str(listing.get("featured") or "0") not in ("0", "", "None", "False", "false"):
        badges.append(Badge(BADGE_FEATURED, "Featured"))
    published = _parse_timestamp(listing.get("published_at"))
    reference = now or datetime.now(timezone.utc)
    if published and reference - published <= NEW_LISTING_WINDOW:
        badges.append(Badge(BADGE_NEW, "New"))
    if str(listing.get("product_type") or listing.get("listing_type") or "").lower() == "digital":
        badges.append(Badge(BADGE_DIGITAL, "Digital"))
    return badges


def suppress_uninformative_badges(
    badges_by_id: Mapping[Any, Sequence[Badge]], share: float = 0.5
) -> dict[Any, list[Badge]]:
    """Drop a recency badge that most of the page is wearing.

    "New" on 13 of 15 products is true and useless: a badge everything carries
    distinguishes nothing, and a rail of them is exactly the decorative noise the
    real data is supposed to replace. Featured and Digital are never suppressed --
    Featured is a merchandising decision someone made per row, and Digital changes
    what the buyer receives.
    """
    total = len(badges_by_id)
    if not total:
        return {}
    wearing = sum(1 for badges in badges_by_id.values() if any(b.key == BADGE_NEW for b in badges))
    if wearing <= total * share:
        return {key: list(badges) for key, badges in badges_by_id.items()}
    return {key: [b for b in badges if b.key != BADGE_NEW] for key, badges in badges_by_id.items()}


def stock_line(listing: Mapping[str, Any], variants: Sequence[Mapping[str, Any]] = ()) -> str:
    """Say "In stock" only when something actually says so.

    ``stock_state`` is three-valued and the third value is UNKNOWN, which is the
    common case for a supplier row that has never synced. An unknown stock level
    renders as no line at all rather than as either answer.
    """
    product_type = str(listing.get("product_type") or listing.get("listing_type") or "").lower()
    if product_type in {"digital", "course", "service", "event", "booking"}:
        return ""
    states = {str(v.get("stock_state") or "UNKNOWN").upper() for v in variants
              if str(v.get("status") or "active").lower() == "active"}
    if states:
        if "IN_STOCK" in states:
            return "In stock"
        if states == {"OUT_OF_STOCK"}:
            return "Out of stock"
        return ""
    quantity = listing.get("quantity")
    if quantity is None or str(quantity).strip() == "":
        return ""
    try:
        return "In stock" if int(quantity) > 0 else "Out of stock"
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Adding to the cart
# ---------------------------------------------------------------------------


#: Why a grid card carries no add-to-cart control. Each value names the refusal
#: ``POST /api/pulse/marketplace/cart`` would have answered with, so the reason a
#: button is missing can be read against the lane that would have refused it
#: rather than against this module's own vocabulary.
CART_HIDDEN_OWN_LISTING = "own_listing"  # OWN_LISTING
CART_HIDDEN_UNAVAILABLE = "unavailable"  # SELLER_UNAVAILABLE / OUT_OF_STOCK, 409
CART_HIDDEN_NO_PRICE = "no_price"        # ITEM_UNAVAILABLE, 400, price_minor <= 0

#: The listing asks a question the caller has not answered. This one *does* mirror
#: a server refusal now — ``POST /api/pulse/marketplace/cart`` answers 400
#: ``VARIANT_REQUIRED`` for an add that names no variant on a listing that
#: requires one, from its own ``_listing_needs_variant``, which calls
#: ``requires_variant_choice`` below rather than restating it.
#:
#: It used to mirror nothing, because there was nothing to mirror: the route took
#: a ``listing_id`` and a ``qty`` and had no concept of a variant, so a one-tap add
#: on a listing selling four sizes did not fail — it succeeded, and booked a line
#: naming no size, priced from the listing's ``price_label``. Withholding the
#: control was the only place in the lane where that guess could be declined.
#:
#: Now it is declined at the till, which is what lets a *selected* variant through:
#: the question is no longer "does this listing have options" but "has this caller
#: answered them". A grid card can never answer, so it still gets this reason and
#: still links to the page; the product page can, and does.
CART_HIDDEN_NEEDS_CHOICE = "needs_choice"


@dataclass(frozen=True)
class CartAffordance:
    """The add-to-cart control a card may show, as facts rather than markup.

    ``listing_id`` is what the control posts. ``variant_id`` is posted with it —
    ``0`` when the listing has nothing to choose, matching
    ``marketplace_cart_schema.NO_VARIANT``, so the control carries one shape and
    the caller never has to decide whether to include the field. ``label`` is what
    it reads.

    Nothing here is a permission: ``POST /api/pulse/marketplace/cart`` re-derives
    every one of the refusals below on the way through, so this only decides
    whether a buyer is offered a button they can expect to work.
    """

    listing_id: int
    label: str = "Add to cart"
    variant_id: int = 0


def requires_variant_choice(
    variants: Sequence[Mapping[str, Any]], *, price: PriceView
) -> bool:
    """Would adding this listing in one tap commit the buyer to a guess?

    Three ways it would, and each is sufficient on its own:

    * An option group offers more than one value. This is the plain case: a
      jacket in S/M/L/XL. The groups come from ``build_option_groups``, the same
      function the product page's variant picker is built from, so the card and
      the picker cannot disagree about whether a choice exists. A group with a
      *single* value is not a choice — a one-colour product is not asking
      anything — and does not count.
    * More than one active variant, whatever their options parse to. A variant
      whose ``options_json`` is empty or malformed yields no groups, so the check
      above would pass it; but the line still has to name one of several rows and
      nothing here knows which.
    * The displayed price is a range. If the card prints "$20 – $40" then a cart
      line holding one number is holding a number the buyer did not agree to.
      Normally implied by the checks above, kept because it is the consequence
      that actually reaches the buyer's card statement.

    A listing with no variants at all — one price, one thing — requires no
    choice, which is the majority of this catalogue and the case the quick-add
    exists for.
    """
    active = [
        v for v in variants
        if str((v or {}).get("status") or "active").lower() == "active"
    ]
    if any(len(group.options) > 1 for group in build_option_groups(active)):
        return True
    if len(active) > 1:
        return True
    return bool(price.is_range)


def cart_affordance(
    payload: Mapping[str, Any],
    *,
    price: PriceView,
    viewer_user_id: Any = 0,
    variants: Sequence[Mapping[str, Any]] = (),
    chosen_variant: Optional["VariantView"] = None,
) -> tuple[Optional[CartAffordance], str]:
    """``(affordance, hidden_reason)`` — one of the two is always empty.

    Each test below is a *mirror of a specific server refusal*, and deliberately
    not a judgement of its own:

    * The viewer is the seller — ``OWN_LISTING``. Nobody buys their own listing,
      and the refusal arrives as an error toast rather than as a cart line.
    * The listing is not buyer-reachable, or is out of stock — 409
      ``SELLER_UNAVAILABLE`` / ``OUT_OF_STOCK``. Read from ``buyer_visible`` and
      ``inventory_state``, which ``pulse_marketplace_listing_payload`` has
      already derived through ``marketplace_listing_lifecycle``. Re-deriving them
      here from the raw columns would be a second copy of the publication rules,
      free to disagree with the serializer about the same row — and the rules are
      three-valued, so the copy that was handed a row missing ``seller_status``
      would answer differently from the copy that was not.
    * There is no price — the route refuses ``price_minor <= 0`` with 400
      ``ITEM_UNAVAILABLE``. This is the *displayed* price, derived from the same
      variants and label the card prints, so the button is offered only where the
      page is already willing to name a number.

    Then one check about the caller rather than the listing: a listing with
    options to pick is refused the quick-add *unless the caller has picked*.
    ``chosen_variant`` is how a caller answers — a fully resolved
    ``VariantView``, which only the product page can produce, because only the
    product page has a picker. A grid card passes nothing and is refused, which is
    right: it has no selection state and its job is to link to the page.

    Passing a variant is not a way around the check. It must be *available* and it
    must be one of the rows in ``variants``, so a caller cannot manufacture a
    selection, and the route re-validates the id against the listing before
    pricing anything. See ``CART_HIDDEN_NEEDS_CHOICE``. This is the only reason in
    the list that the caller is expected to *render* rather than simply obey, so it
    is returned last and kept distinct from ``CART_HIDDEN_UNAVAILABLE`` instead of
    being folded in.

    Where this module and the route could disagree, the button is withheld:
    a missing button costs a buyer one tap through the app, and an offered button
    that 409s costs them their trust in the page. Withholding is also why every
    check reads a fail-closed source — ``buyer_visible`` is ``False`` for a row
    whose seller status was never projected, and that is the answer this wants.

    There is no longer a refusal for *not being signed in*. There used to be,
    and it was the correct mirror of the route at the time: ``POST
    /api/pulse/marketplace/cart`` began with ``_require_user()`` and answered
    401, so the button could only ever have failed. The route now allocates a
    guest cart owner instead (``services/marketplace_guest_customer``), so the
    add succeeds, and withholding the control would be this module refusing
    something the server is willing to do. Every refusal that remains is a
    property of the listing or of the choice, not of who is asking — which is
    the point: authentication decides whose cart it is, not whether there is
    one.
    """
    try:
        listing_id = int(payload.get("listing_id") or payload.get("id") or 0)
    except (TypeError, ValueError):
        listing_id = 0
    if listing_id <= 0:
        # Not a reason a buyer needs told; a card with no id cannot post anything.
        return None, CART_HIDDEN_UNAVAILABLE
    try:
        seller_user_id = int(payload.get("seller_user_id") or 0)
    except (TypeError, ValueError):
        seller_user_id = 0
    try:
        viewer = int(viewer_user_id or 0)
    except (TypeError, ValueError):
        viewer = 0
    if viewer and seller_user_id and viewer == seller_user_id:
        return None, CART_HIDDEN_OWN_LISTING
    # Absent is treated exactly like False, and the two are one branch on
    # purpose. `buyer_visible` is emitted by every payload that went through the
    # serializer, so an absent key means this card was built from a raw row --
    # which is the case where nothing has consulted the publication rules at all.
    # Offering a button there would be guessing, so it is refused the same way a
    # suspended seller's listing is.
    if not payload.get("buyer_visible"):
        return None, CART_HIDDEN_UNAVAILABLE
    if str(payload.get("inventory_state") or "").lower() == "out_of_stock":
        return None, CART_HIDDEN_UNAVAILABLE
    if not price.known:
        return None, CART_HIDDEN_NO_PRICE
    # Last, because it is the only check that is not about whether this listing
    # can be bought at all. Everything above withholds the button from a listing
    # nobody can add; this withholds it from one that must be *configured* first,
    # and the caller distinguishes the two — the reasons above render nothing,
    # this one renders a way through to the picker.
    if requires_variant_choice(variants, price=price):
        chosen = _accepted_choice(chosen_variant, variants)
        if chosen is None:
            return None, CART_HIDDEN_NEEDS_CHOICE
        return CartAffordance(listing_id=listing_id, variant_id=chosen), ""
    return CartAffordance(listing_id=listing_id), ""


def _accepted_choice(
    chosen: Optional["VariantView"], variants: Sequence[Mapping[str, Any]]
) -> Optional[int]:
    """The variant id a selection may be added under, or ``None`` to keep refusing.

    Three ways a passed-in choice is not one, and each is deliberate:

    * No variant, or one with no id. An unresolved picker — the buyer has chosen
      Colour but not Size — is exactly the state the refusal exists for.
    * Not available. Offering "Add to cart" on the one combination that is sold
      out is worse than offering nothing, because it reads as though the page
      checked. The route answers 409 ``OUT_OF_STOCK`` for it too.
    * Not one of ``variants``. The caller supplies both arguments, so this is not
      a trust boundary — the route's own ``_load_variant`` is. It is a consistency
      check: a resolved variant that is not in the set this decision was made
      against means the two were derived from different reads, and the honest
      answer to a disagreement about what is for sale is to withhold the button.
    """
    if chosen is None:
        return None
    variant_id = int(getattr(chosen, "variant_id", 0) or 0)
    if variant_id <= 0 or not getattr(chosen, "available", False):
        return None
    known = {int((v or {}).get("id") or 0) for v in variants}
    return variant_id if variant_id in known else None


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaItem:
    url: str
    kind: str  # image | video
    poster: str = ""


def gallery_items(payload: Mapping[str, Any], limit: int = 8) -> list[MediaItem]:
    """Real media only.

    The reference design shows a seven-thumbnail rail; 41 of 47 live listings
    have exactly one image. Padding the rail would mean repeating that image or
    inventing frames, so the rail renders the media that exists and disappears
    below two items.

    Three sources, in descending order of trust, because the catalogue populates
    them unevenly: the moderated ``marketplace_product_media`` rows (only 8 exist,
    but they are cover-ordered and moderation-filtered), then ``gallery_json``
    (6 listings), then ``cover_image_url`` (all 47). Later sources only ever add
    URLs the earlier ones did not already supply, so a listing with real media
    rows is unaffected by a stale cover column.
    """
    items: list[MediaItem] = []
    seen: set[str] = set()

    def _add(url: Any, kind: str = "image", poster: Any = "") -> None:
        cleaned = _clean(url)
        if not cleaned or cleaned in seen or len(items) >= limit:
            return
        seen.add(cleaned)
        items.append(MediaItem(url=cleaned, kind=kind, poster=_clean(poster)))

    for entry in payload.get("media") or payload.get("media_assets") or []:
        if not isinstance(entry, Mapping):
            _add(entry)
            continue
        kind = "video" if str(entry.get("media_type") or "image").lower() == "video" else "image"
        _add(entry.get("media_url") or entry.get("url"), kind,
             entry.get("poster_url") or entry.get("thumbnail_url"))

    # The cover leads whatever follows it: when there are no media rows it is the
    # main image, and when there are, dedup makes this a no-op.
    _add(payload.get("cover_image_url") or payload.get("image_url") or payload.get("thumbnail_url"))

    gallery = payload.get("gallery_json")
    if isinstance(gallery, (str, bytes)):
        try:
            gallery = json.loads(gallery)
        except (ValueError, TypeError):
            gallery = None
    if isinstance(gallery, Mapping):
        gallery = gallery.get("images") or gallery.get("urls") or []
    if isinstance(gallery, list):
        for entry in gallery:
            if isinstance(entry, Mapping):
                _add(entry.get("url") or entry.get("media_url"), poster=entry.get("thumbnail_url"))
            else:
                _add(entry)

    return items


# ---------------------------------------------------------------------------
# Sorting, filtering, paging
# ---------------------------------------------------------------------------

SORT_OPTIONS = (
    ("featured", "Featured"),
    ("newest", "Newest"),
    ("price_asc", "Price: low to high"),
    ("price_desc", "Price: high to low"),
)
SORT_KEYS = frozenset(key for key, _ in SORT_OPTIONS)
DEFAULT_SORT = "featured"
PAGE_SIZE = 24


def normalize_sort(value: Any) -> str:
    key = str(value or "").strip().lower()
    return key if key in SORT_KEYS else DEFAULT_SORT


def sort_products(products: Sequence[Mapping[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Order a page of already-filtered products.

    Only the four modes above are offered, and each one reads a column that
    exists. "Most popular" and "Top rated" are absent from ``SORT_OPTIONS``
    because there is no view counter and no review table to rank by; offering
    them would mean picking an arbitrary order and calling it popularity.
    """
    key = normalize_sort(sort)
    rows = list(products)
    if key == "newest":
        return sorted(rows, key=lambda r: int(r.get("id") or 0), reverse=True)
    if key in ("price_asc", "price_desc"):
        priced = [r for r in rows if r.get("price") and r["price"].known]
        unpriced = [r for r in rows if not (r.get("price") and r["price"].known)]
        priced.sort(key=lambda r: r["price"].min_cents, reverse=(key == "price_desc"))
        return priced + unpriced
    return sorted(rows, key=lambda r: (0 if str(r.get("featured") or "0") in ("0", "", "None", "False", "false") else 1, int(r.get("id") or 0)), reverse=True)


def matches_query(product: Mapping[str, Any], query: str) -> bool:
    """Substring match over the fields a shopper would name.

    Deliberately excludes the full description: it is supplier boilerplate
    averaging 568 characters, so matching it turns "cotton" into a query that
    returns most of the catalogue on the strength of a care instruction.
    """
    needle = _clean(query).lower()
    if not needle:
        return True
    haystack = " ".join(str(product.get(field) or "") for field in
                        ("title", "short_description", "category", "seller_store_name", "tags_json")).lower()
    return all(token in haystack for token in needle.split())


def build_query_string(base: Mapping[str, Any], **overrides: Any) -> str:
    """URL-addressable filter state, with empty values dropped.

    Filters live in the URL so a filtered view can be shared, bookmarked,
    reloaded and crawled. ``page`` resets whenever any other facet changes,
    which is handled by callers passing ``page=None``.
    """
    from urllib.parse import urlencode

    merged: dict[str, Any] = {k: v for k, v in base.items()}
    merged.update(overrides)
    pairs = [(k, str(v)) for k, v in merged.items() if v not in (None, "", 0) or (k == "page" and v)]
    pairs = [(k, v) for k, v in pairs if not (k == "sort" and v == DEFAULT_SORT) and not (k == "page" and v == "1")]
    return ("?" + urlencode(sorted(pairs))) if pairs else ""


def url_quote(value: Any) -> str:
    """Percent-encode one query-string *value*.

    Separate from :func:`build_query_string` because a few links carry a single
    value into a URL this module does not otherwise build (the messenger's
    people search, for one). ``safe=""`` is deliberate: the value is
    seller-controlled, so a username containing ``/``, ``&`` or ``#`` must not
    be able to escape its own parameter.
    """
    from urllib.parse import quote

    return quote(str(value if value is not None else ""), safe="")


@dataclass
class Page:
    items: list[Any] = field(default_factory=list)
    page: int = 1
    total: int = 0
    size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.size - 1) // self.size)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def paginate(items: Sequence[Any], page: int, size: int = PAGE_SIZE) -> Page:
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    current = min(max(1, int(page or 1)), pages)
    start = (current - 1) * size
    return Page(items=list(items[start:start + size]), page=current, total=total, size=size)


# ---------------------------------------------------------------------------
# Structured data
# ---------------------------------------------------------------------------


def product_jsonld(
    payload: Mapping[str, Any],
    price: PriceView,
    description: DescriptionView,
    media: Sequence[MediaItem],
    canonical_url: str,
    in_stock: Optional[bool] = None,
) -> dict[str, Any]:
    """schema.org Product carrying only properties with a source.

    ``aggregateRating`` and ``review`` are structurally absent -- there is no
    review table -- and ``brand`` is absent because the column does not exist.
    Emitting either would be the specific kind of SEO lie that gets structured
    data ignored for a whole domain.
    """
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": _clean(payload.get("title")),
        "url": canonical_url,
        "sku": f"pulsesoc-listing-{int(payload.get('id') or 0)}",
    }
    # Prefer parsed content over the raw run: dumping "Product Type :Combo Item
    # Main Color:Beige" into a meta description is what the parser exists to stop.
    text = (
        description.summary
        or _clean(payload.get("short_description"))
        or (", ".join(f"{k}: {v}" for k, v in description.attributes[:6]) if description.attributes else "")
        or _first_sentences(description.body, 300)
    )
    if text:
        data["description"] = _first_sentences(text, 300)
    images = [item.url for item in media if item.kind == "image"]
    if images:
        data["image"] = images
    crumbs = category_crumbs(payload.get("category"))
    if crumbs:
        data["category"] = " > ".join(label for label, _ in crumbs)
    offer = price.as_schema_offer()
    if offer:
        offer["url"] = canonical_url
        if in_stock is not None:
            offer["availability"] = ("https://schema.org/InStock" if in_stock else "https://schema.org/OutOfStock")
        seller = _clean(payload.get("seller_store_name"))
        if seller:
            offer["seller"] = {"@type": "Organization", "name": seller}
        data["offers"] = offer
    if description.attributes:
        data["additionalProperty"] = [
            {"@type": "PropertyValue", "name": key, "value": value}
            for key, value in description.attributes[:12]
        ]
    return data


def breadcrumb_jsonld(crumbs: Sequence[tuple[str, str]], origin: str = PUBLIC_ORIGIN) -> Optional[dict[str, Any]]:
    if not crumbs:
        return None
    items = [{"@type": "ListItem", "position": 1, "name": "Marketplace", "item": f"{origin}/pulse/marketplace"}]
    for index, (label, slug) in enumerate(crumbs, start=2):
        items.append({
            "@type": "ListItem",
            "position": index,
            "name": label,
            "item": f"{origin}/pulse/marketplace?category={slug}",
        })
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
