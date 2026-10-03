"""What PulseSoc actually knows about a product, and how it came to know it.

``marketplace_seo`` already answers "what may this row claim to a search
engine". It does that well, and it does it by reading a listing dict and
refusing to emit a field the row does not carry. This module is the layer
underneath: it answers *why* the row does not carry the field, which of several
sources a value came from, and whether two rows are the same product.

The distinction matters because "absent" has at least three different causes in
this catalogue and they need different responses:

* ``price_label`` is empty on 152 of 202 rows because nobody filled it in. The
  value exists elsewhere (``marketplace_listing_variants.price_cents``, set on
  all 3,797 rows) and the right response is to read the other authority.
* ``brand`` is absent because **the supplier never sends it**. Measured against
  production on 2026-10-03: a full recursive key census over all 20,247
  ``supplier_snapshots`` rows of ``kind='product'`` found 20 distinct keys and
  not one identifier-shaped key -- no ``brand``, ``brandName``, ``gtin``,
  ``upc``, ``mpn``, ``barcode`` or ``modelNumber``. So no amount of schema work
  recovers it. ``normalize._cj_product`` does read ``brandName``/``brand``
  (normalize.py:553), which means the extractor is correct and the field is
  simply never populated. The right response is ``UNKNOWN``, permanently.
* A variant's image is absent from ``marketplace_listing_variants`` because
  that table has no image column -- but the supplier *does* send one, for
  3,801 of 3,801 variants, and on 191 of 197 products the first option value
  maps to exactly one image. So the mapping is real, consistent, and currently
  discarded at import. The right response is to record it as recoverable rather
  than to pretend it is unknowable.

Collapsing those three into "field is empty" is what produces a feed that
invents a brand to fill a required column. Hence :class:`Fact`, which carries a
provenance alongside every value, and :data:`UNKNOWN`, which is a value in its
own right rather than ``None``.

WHY A SUPPLIER PRODUCT KEY IS NAMESPACED HERE
---------------------------------------------
``marketplace_product_sources.provider_product_id`` holds a CJ product id. In
production all 196 rows hold 196 distinct values, so the bare id *looks*
globally unique. It is not: the uniqueness that the schema actually enforces is
``(seller_user_id, provider, supplier_connection_id, provider_product_id)``
(``marketplace_supplier_schema``), which exists precisely so one seller can
import the same CJ product through two supplier connections. The observed
values are also not a single format -- 195 are long digit strings like
``2504170952451615600`` and one is a UUID, ``A7F7AAD1-54C3-4DEF-84B6-5E019E671DBE``
-- so nothing about their shape can be relied on either.

A downstream consumer that keys a Merchant ``item_group_id`` on the bare
``provider_product_id`` therefore works today and silently merges two distinct
seller offers the first time a second connection is added. :func:`supplier_product_key`
makes the namespace explicit so that bug cannot be written.

WHY OPTION AXES STAY UNNAMED
----------------------------
CJ sends a variant's options as a single hyphenated string -- ``"Blue-10.5g"``,
``"Apricot-XL"``. ``normalize`` splits on the hyphen and labels the parts
``option1``, ``option2`` (normalize.py:638). Measured in production: all 3,797
variant rows carry positional names, 0 carry a semantic one; 3,684 have two
axes, 101 have one, and 12 have three.

``option1`` is *usually* a colour and ``option2`` *usually* a size. Usually is
not a fact. Listing 209 is a three-axis product whose ``option1`` value is
``"Picture Color"`` -- supplier boilerplate meaning "as shown", not a colour --
and whose ``option3`` is a plug region (``US``/``EU``/``UK``/``AU``). Relabelling
``option1`` to ``color`` would make that row claim a colour named "Picture
Color" and would lose the plug region entirely, which for an electrical product
is the attribute that decides whether it works in the buyer's country.

So this module reports the axis position and refuses to name it. A consumer
that needs ``color`` for structured data gets ``UNKNOWN`` and omits the
property, which is what ``marketplace_seo`` already does with brand.

WHAT THIS MODULE MUST NEVER EXPOSE
----------------------------------
``marketplace_listing_variants.cost_cents`` is populated on all 3,797 rows and
``marketplace_product_sources.supplier_cost_cents`` on all 196. That is
supplier economics. It is not a public product fact, it is not a search field,
and :func:`public_projection` is written as an allowlist rather than a
denylist so that adding a column to the variants table cannot leak it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

__all__ = [
    "Provenance",
    "Confidence",
    "IdentifierClass",
    "UNKNOWN",
    "Fact",
    "OptionAxis",
    "VariantIdentity",
    "ProductIdentity",
    "SemanticState",
    "classify_identifier",
    "supplier_product_key",
    "option_axes",
    "variant_identity",
    "product_identity",
    "public_projection",
    "semantic_state",
    "PRIVATE_FIELDS",
]


class Provenance:
    """Where a value came from. Not a quality score -- a source attribution."""

    #: The supplier sent this field and we stored it unchanged.
    SUPPLIER_ASSERTED = "SUPPLIER_ASSERTED"
    #: A human seller typed this.
    SELLER_ASSERTED = "SELLER_ASSERTED"
    #: Derived from a supplier value by a deterministic, reversible rule.
    PULSESOC_NORMALIZED = "PULSESOC_NORMALIZED"
    #: Computed from other stored columns (an id, a key, a state).
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    #: The source does not carry this field and never has. Distinct from
    #: UNKNOWN: this one will not be fixed by a backfill.
    UNAVAILABLE_FROM_SOURCE = "UNAVAILABLE_FROM_SOURCE"
    #: Present in the raw supplier snapshot but dropped before it reached a
    #: queryable column. Recoverable without asking the supplier again.
    RECOVERABLE_FROM_SNAPSHOT = "RECOVERABLE_FROM_SNAPSHOT"
    #: We do not know, and we are not guessing.
    UNKNOWN = "UNKNOWN"


class Confidence:
    """How much weight a consumer may put on a value.

    Deliberately coarse. A float would invite a threshold, and a threshold
    invites "0.72 is close enough to publish a GTIN".
    """

    VERIFIED = "VERIFIED"
    ASSERTED = "ASSERTED"
    DERIVED = "DERIVED"
    NONE = "NONE"


class IdentifierClass:
    """What kind of identifier a string actually is."""

    #: Structurally a valid GTIN-8/12/13/14 including check digit.
    GTIN = "GTIN"
    #: A supplier's own catalogue code. Unique inside that supplier, meaningless
    #: outside it. Never a GTIN, never an MPN.
    SUPPLIER_INTERNAL = "SUPPLIER_INTERNAL"
    #: An id this platform minted.
    PULSESOC_INTERNAL = "PULSESOC_INTERNAL"
    #: GTIN-shaped -- 8, 12, 13 or 14 digits -- but the check digit does not
    #: validate. Not publishable as a GTIN, and not assertable as a supplier
    #: code either, because we cannot tell which it was meant to be.
    UNVERIFIED = "UNVERIFIED"
    #: Absent.
    MISSING = "MISSING"


#: Sentinel for "we do not know". A distinct object rather than ``None`` so a
#: consumer cannot confuse it with "nobody asked" and cannot ``or``-default it
#: into a plausible string.
class _Unknown:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNKNOWN"

    def __bool__(self) -> bool:
        return False


UNKNOWN = _Unknown()


#: Columns that exist in the catalogue and must never reach a public surface.
#: Enforced by :func:`public_projection` building an allowlist, and asserted by
#: ``tests/catalog/test_catalog_semantics.py``.
PRIVATE_FIELDS = frozenset(
    {
        "cost_cents",
        "supplier_cost_cents",
        "supplier_cost_currency",
        "credential_reference",
        "supplier_connection_id",
        "source_snapshot_id",
        "inventory_reference",
    }
)


@dataclass(frozen=True)
class Fact:
    """A value plus the reason we believe it.

    ``value`` is :data:`UNKNOWN` rather than ``None`` when we do not know, so
    that ``fact.value or "Unbranded"`` -- the exact line this class exists to
    prevent -- is visibly wrong at review rather than quietly plausible.
    """

    value: Any
    provenance: str
    confidence: str

    @property
    def known(self) -> bool:
        return self.value is not UNKNOWN

    @classmethod
    def unknown(cls, provenance: str = Provenance.UNKNOWN) -> "Fact":
        return cls(UNKNOWN, provenance, Confidence.NONE)


@dataclass(frozen=True)
class OptionAxis:
    """One axis of a variant's choice, at a known position with an unknown name.

    ``position`` is 1-based and matches the stored ``option1``/``option2``
    label. ``name`` is a :class:`Fact` whose value is :data:`UNKNOWN` whenever
    the stored label is positional, which in production is every row.
    """

    position: int
    name: Fact
    value: Fact


@dataclass(frozen=True)
class VariantIdentity:
    """Exactly which thing the buyer receives.

    ``variant_key`` is PulseSoc's *internal* identity: an order-independent
    normalisation of the option pairs, written by the supplier importer, unique
    per ``(listing_id, variant_key)``, and stable across a re-import in a way
    that ``position`` and a row ``id`` are not. It is not publishable, for two
    reasons measured on 2026-10-03. It is not unique on its own -- 3,797 rows
    hold only 2,571 distinct keys, because the key is scoped to its listing. And
    every one of the 3,797 is of the form ``option1=sapphire blue|option2=iphone
    11pro``: it carries ``|`` (3,696 rows), ``=`` (3,797), a space (1,783), and
    occasionally ``/``, a bracket or an apostrophe, none of which are RFC-3986
    unreserved -- and it embeds the positional label this module otherwise
    refuses to name. Publishing it would hand a consumer the string ``option1``
    and invite exactly the guess :func:`option_axes` exists to prevent.

    So :attr:`stable_id` is published and ``variant_key`` is not.
    """

    listing_id: int
    variant_key: str
    provider_variant_id: Fact
    sku: Fact
    sku_class: str
    gtin: Fact
    mpn: Fact
    axes: Sequence[OptionAxis]
    price_cents: Fact
    currency: Fact
    stock_state: Fact
    image: Fact

    @property
    def stable_id(self) -> str:
        """The identity a downstream feed or JSON-LD node should key on.

        Keyed on the supplier's own variant id rather than on ``variant_key``,
        because that id is present and distinct on all 3,797 production rows and
        reused across listings on none, while ``variant_key`` is derived from the
        option *values* -- so a supplier renaming "Sapphire Blue" to "Blue"
        changes the key, and a published id that moves is not an id.

        Digested rather than emitted raw. The digest is deterministic, so the id
        survives a re-import; it is URL-safe, which the raw key is not; and it
        does not publish CJ's catalogue numbering, which sits alongside
        ``supplier_connection_id`` and ``source_snapshot_id`` in
        :data:`PRIVATE_FIELDS`. It is **not** a secret: the input space is
        structured and a party who already knows a supplier id can confirm it by
        hashing. It is an opaque, stable, URL-safe handle, and claiming more than
        that would be the kind of overstatement this module exists to avoid.
        """
        basis = (
            str(self.provider_variant_id.value)
            if self.provider_variant_id.known
            else self.variant_key
        )
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        return f"pulsesoc-variant-{self.listing_id}-{digest}"


@dataclass(frozen=True)
class ProductIdentity:
    """One PulseSoc listing, and the supplier product it is bound to.

    A listing is *not* a product family. In this catalogue the mapping happens
    to be 1:1:1 -- 196 listings, 196 ``marketplace_product_sources`` rows, 196
    distinct ``provider_product_id`` values, with a UNIQUE index making one
    source per listing -- so a listing is today the natural ProductGroup and
    its variants are the group's members. That is an observation about current
    data, not a guarantee, which is why ``group_id`` is derived from the
    namespaced supplier key rather than from ``listing_id``.
    """

    listing_id: int
    seller_user_id: Optional[int]
    provider: Fact
    supplier_product_key: Fact
    group_id: str
    title: Fact
    description: Fact
    supplier_category_path: Fact
    pulsesoc_category: Fact
    brand: Fact
    variants: Sequence[VariantIdentity] = field(default_factory=tuple)


class SemanticState:
    """Actionable semantic states. Not a score out of 100.

    These describe what a human or a job would have to *do*, which is the only
    thing a state is useful for. They deliberately do not duplicate the
    lifecycle states in ``marketplace_listing_lifecycle`` -- that module owns
    whether a row is public, and this one owns whether its meaning is complete
    enough to describe.
    """

    READY = "READY"
    READY_WITH_UNKNOWNS = "READY_WITH_UNKNOWNS"
    NEEDS_CATEGORY = "NEEDS_CATEGORY"
    NEEDS_TITLE = "NEEDS_TITLE"
    NEEDS_VARIANT_DECISION = "NEEDS_VARIANT_DECISION"
    NEEDS_IMAGE = "NEEDS_IMAGE"
    INVALID_DATA = "INVALID_DATA"


_GTIN_SHAPE = re.compile(r"\A(?:\d{8}|\d{12}|\d{13}|\d{14})\Z")
_POSITIONAL_LABEL = re.compile(r"\A(?:option|opt|attr|attribute)[\s_-]*\d+\Z", re.IGNORECASE)


def _gtin_check_digit_ok(digits: str) -> bool:
    """GS1 mod-10: weight 3 and 1 alternating from the right, excluding the check digit."""
    body = [int(c) for c in digits[:-1]][::-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == int(digits[-1])


def classify_identifier(value: Any) -> str:
    """What kind of identifier is this string, structurally?

    The question this exists to answer correctly is "may we publish the SKU as a
    GTIN", and the answer in this catalogue is no. Of the 3,721 non-empty SKUs
    in ``marketplace_listing_variants``, every one is a CJ catalogue code --
    ``CJYD235792608HS``, ``CJTW244381907GT`` -- and **zero** are a structurally
    valid GTIN. A length-and-digits test alone would also be wrong: a 13-digit
    supplier code would pass it. So the check digit is verified, because a GTIN
    is a registered identifier belonging to somebody else and a number that
    merely looks like one is a false claim about a third party. A GTIN-shaped
    value that fails the check digit is reported as ``UNVERIFIED`` rather than
    as a supplier code, because which of the two it was meant to be is exactly
    what we do not know.
    """
    if value is None or value is UNKNOWN:
        return IdentifierClass.MISSING
    text = str(value).strip()
    if not text:
        return IdentifierClass.MISSING
    if _GTIN_SHAPE.match(text):
        return (
            IdentifierClass.GTIN
            if _gtin_check_digit_ok(text)
            else IdentifierClass.UNVERIFIED
        )
    if text.lower().startswith("pulsesoc-"):
        return IdentifierClass.PULSESOC_INTERNAL
    return IdentifierClass.SUPPLIER_INTERNAL


def supplier_product_key(
    provider: Any, supplier_connection_id: Any, provider_product_id: Any
) -> Fact:
    """A supplier product identity that stays distinct across connections.

    Returns :data:`UNKNOWN` when the provider product id is missing rather than
    a key with an empty segment, because a key like ``cj::`` would collide with
    every other id-less row and a collision in an identity function is the one
    failure that silently merges two products.
    """
    pid = "" if provider_product_id is None else str(provider_product_id).strip()
    if not pid:
        return Fact.unknown(Provenance.UNKNOWN)
    prov = ("" if provider is None else str(provider).strip().lower()) or "unknown"
    conn = "" if supplier_connection_id is None else str(supplier_connection_id).strip()
    return Fact(
        f"{prov}:{conn}:{pid}",
        Provenance.SYSTEM_DERIVED,
        Confidence.DERIVED,
    )


def _load_options(raw: Any) -> list:
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def option_axes(variant: Mapping[str, Any]) -> tuple[OptionAxis, ...]:
    """The variant's choice axes, with positions kept and names refused.

    A stored label of ``option1`` is reported as position 1 with an
    :data:`UNKNOWN` name. A genuinely semantic label -- which no production row
    currently has -- is reported as a ``SUPPLIER_ASSERTED`` name, so this
    function does not have to change if a future supplier sends real axis names.
    """
    axes: list[OptionAxis] = []
    for index, entry in enumerate(_load_options(variant.get("options_json")), start=1):
        if not isinstance(entry, Mapping):
            continue
        label = entry.get("name")
        text = "" if label is None else str(label).strip()
        if text and not _POSITIONAL_LABEL.match(text):
            name = Fact(text, Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED)
        else:
            name = Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE)
        raw_value = entry.get("value")
        value_text = "" if raw_value is None else str(raw_value).strip()
        value = (
            Fact(value_text, Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED)
            if value_text
            else Fact.unknown(Provenance.UNKNOWN)
        )
        axes.append(OptionAxis(position=index, name=name, value=value))
    return tuple(axes)


def _int_fact(value: Any, provenance: str, confidence: str) -> Fact:
    try:
        if value is None:
            raise TypeError
        return Fact(int(value), provenance, confidence)
    except (TypeError, ValueError):
        return Fact.unknown(Provenance.UNKNOWN)


def _text_fact(value: Any, provenance: str, confidence: str) -> Fact:
    text = "" if value is None else str(value).strip()
    if not text:
        return Fact.unknown(Provenance.UNKNOWN)
    return Fact(text, provenance, confidence)


def variant_identity(
    variant: Mapping[str, Any], *, listing_id: int, snapshot_image: Any = None
) -> VariantIdentity:
    """Build one variant's identity from its stored row.

    ``gtin`` and ``mpn`` are always :data:`UNKNOWN` with provenance
    ``UNAVAILABLE_FROM_SOURCE``. That is not a placeholder awaiting a backfill:
    the supplier payload has no such key in any of 20,247 snapshots, so there is
    nothing to backfill from. A SKU is reported as a SKU and classified, never
    promoted into either field.

    ``snapshot_image`` lets a caller that has read ``supplier_snapshots`` pass
    the per-variant image through; it is marked ``RECOVERABLE_FROM_SNAPSHOT``
    rather than ``SUPPLIER_ASSERTED`` because it is not in a queryable column
    and a consumer should know it came from the audit trail.
    """
    sku_raw = variant.get("sku")
    sku_class = classify_identifier(sku_raw)
    gtin = (
        Fact(str(sku_raw).strip(), Provenance.SUPPLIER_ASSERTED, Confidence.VERIFIED)
        if sku_class == IdentifierClass.GTIN
        else Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE)
    )
    return VariantIdentity(
        listing_id=listing_id,
        variant_key=str(variant.get("variant_key") or "").strip(),
        provider_variant_id=_text_fact(
            variant.get("provider_variant_id"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED
        ),
        sku=_text_fact(sku_raw, Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED),
        sku_class=sku_class,
        gtin=gtin,
        mpn=Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE),
        axes=option_axes(variant),
        price_cents=_int_fact(
            variant.get("price_cents"), Provenance.PULSESOC_NORMALIZED, Confidence.DERIVED
        ),
        currency=_text_fact(
            variant.get("currency"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED
        ),
        stock_state=_text_fact(
            variant.get("stock_state"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED
        ),
        image=(
            Fact(str(snapshot_image).strip(), Provenance.RECOVERABLE_FROM_SNAPSHOT, Confidence.ASSERTED)
            if snapshot_image
            else Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE)
        ),
    )


def product_identity(
    listing: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]] = (),
    source: Optional[Mapping[str, Any]] = None,
    *,
    variant_images: Optional[Mapping[str, Any]] = None,
) -> ProductIdentity:
    """Assemble one listing's semantic identity.

    ``brand`` is :data:`UNKNOWN` unconditionally, and the provenance says
    ``UNAVAILABLE_FROM_SOURCE`` rather than ``UNKNOWN`` so that a reader can
    tell the difference between "we have not looked" and "there is nothing to
    look at". ``"Unbranded"`` is not substituted: that string is itself a claim
    about the product, and for a CJ catalogue item we have no basis for it.

    ``pulsesoc_category`` is :data:`UNKNOWN` because there is no PulseSoc
    taxonomy. ``marketplace_listings.category`` holds the supplier's own
    breadcrumb verbatim -- 108 distinct strings across 202 rows, e.g.
    ``"Women's Clothing > Tops & Sets > Lady Dresses"`` -- and ``subcategory`` is
    empty on all 202. Reporting the supplier path as a PulseSoc category would
    make a supplier taxonomy change look like a PulseSoc decision.
    """
    src = source or {}
    listing_id = int(listing.get("id") or listing.get("listing_id") or 0)
    images = variant_images or {}
    built = tuple(
        variant_identity(
            v,
            listing_id=listing_id,
            snapshot_image=images.get(str(v.get("variant_key") or "")),
        )
        for v in (variants or ())
    )
    key = supplier_product_key(
        src.get("provider"),
        src.get("supplier_connection_id"),
        src.get("provider_product_id"),
    )
    return ProductIdentity(
        listing_id=listing_id,
        seller_user_id=listing.get("seller_user_id"),
        provider=_text_fact(src.get("provider"), Provenance.SYSTEM_DERIVED, Confidence.DERIVED),
        supplier_product_key=key,
        group_id=(key.value if key.known else f"pulsesoc-listing-{listing_id}"),
        title=_text_fact(listing.get("title"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED),
        description=_text_fact(
            listing.get("description"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED
        ),
        supplier_category_path=_text_fact(
            listing.get("category"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED
        ),
        pulsesoc_category=Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE),
        brand=Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE),
        variants=built,
    )


def public_projection(identity: ProductIdentity) -> dict:
    """The subset of a product's semantics that may cross a public boundary.

    Built as an allowlist. A denylist would mean that the next column added to
    ``marketplace_listing_variants`` is public by default, and the column most
    likely to be added next to a dropship variants table is a cost or a margin.

    Known-only: a :data:`UNKNOWN` fact is omitted from the output entirely
    rather than emitted as ``null``, because a consumer serialising this into
    JSON-LD should be unable to produce ``"brand": null`` -- which some
    validators accept and which reads to a crawler as an assertion about a
    field we have no information on.
    """

    def emit(fact: Fact) -> Any:
        return fact.value if fact.known else None

    def axis_row(axis: OptionAxis) -> dict:
        """Position always, name and value only when known.

        The name is omitted rather than nulled for the same reason brand is:
        every production axis is positional, so this is the field most likely to
        be filled in by a well-meaning consumer that sees ``"name": null`` and
        decides position 1 is obviously the colour.
        """
        row: dict = {"position": axis.position}
        for key, fact in (("name", axis.name), ("value", axis.value)):
            value = emit(fact)
            if value is not None:
                row[key] = value
        return row

    out: dict = {
        "listing_id": identity.listing_id,
        "group_id": identity.group_id,
        "semantic_state": semantic_state(identity),
    }
    for name, fact in (
        ("title", identity.title),
        ("description", identity.description),
        ("supplier_category_path", identity.supplier_category_path),
        ("provider", identity.provider),
    ):
        value = emit(fact)
        if value is not None:
            out[name] = value
            out[f"{name}_provenance"] = fact.provenance

    variants = []
    for v in identity.variants:
        # variant_key is deliberately absent: it is listing-scoped rather than
        # unique, it is not URL-safe, and it spells out the positional option
        # label. stable_id is the handle a consumer correlates on.
        row: dict = {
            "stable_id": v.stable_id,
            "sku_class": v.sku_class,
            "axes": [axis_row(a) for a in v.axes],
        }
        for name, fact in (
            ("sku", v.sku),
            ("price_cents", v.price_cents),
            ("currency", v.currency),
            ("stock_state", v.stock_state),
            ("image", v.image),
            ("gtin", v.gtin),
            ("mpn", v.mpn),
        ):
            value = emit(fact)
            if value is not None:
                row[name] = value
        variants.append(row)
    out["variants"] = variants
    return out


def semantic_state(identity: ProductIdentity) -> str:
    """What would have to happen for this product's meaning to be complete.

    Reports only on *semantics*. Whether the row may be published is
    ``marketplace_listing_lifecycle``'s question, and duplicating it here would
    create a second lifecycle that can disagree with the first.

    THERE IS NO TITLE-LENGTH THRESHOLD HERE, ON PURPOSE. Run over production,
    five listings have a title under 16 characters: ``Big T``, ``T2``, ``T3``,
    ``T4`` -- and ``Lip Medex``, which is a real product name. A character count
    cannot tell the fifth from the other four, so a threshold would park a
    legitimate short name in NEEDS_TITLE and call that quality control. The four
    that are genuinely placeholders are identifiable by a fact instead: they have
    no variants and no supplier binding, which the clause below reports.
    """
    if not identity.listing_id:
        return SemanticState.INVALID_DATA
    if not identity.title.known:
        return SemanticState.NEEDS_TITLE
    if not identity.supplier_category_path.known:
        return SemanticState.NEEDS_CATEGORY
    # No variants means there is nothing for a buyer to choose, so the product's
    # meaning is not complete however good its prose is. Six of 202 production
    # rows are in this state and all six are also unbound from any supplier.
    if not identity.variants:
        return SemanticState.NEEDS_VARIANT_DECISION
    if not all(v.variant_key for v in identity.variants):
        return SemanticState.NEEDS_VARIANT_DECISION
    unknown_axis = any(
        not axis.name.known for v in identity.variants for axis in v.axes
    )
    if unknown_axis or not identity.brand.known:
        return SemanticState.READY_WITH_UNKNOWNS
    return SemanticState.READY
