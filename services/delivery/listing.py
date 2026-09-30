"""What a listing *declares* about who ships it, and whether a buyer may ask.

Why the declaration needs a module
----------------------------------
``quote.quote_delivery`` refuses anything whose ``fulfillment`` is not
``SUPPLIER``, and §47-48 says that value is a declared fact and never inferred.
Nothing in this package resolves it. The layer above would have to, and the
obvious guess there is wrong in a way that shows up as a wrong promise rather
than as an error.

The obvious guess is "this listing has a row in ``marketplace_product_sources``
with ``provider='cj'``, so CJ ships it". That is not what the column means.
``marketplace_supplier_schema`` separates provider identity from fulfillment mode
on purpose, and says why in its own comment: a product can be *imported from CJ
and stocked in the seller's own garage*. Such a listing has a CJ source row, CJ
product and variant ids, a CJ snapshot with a real weight — everything a freight
quote needs — and CJ will happily price a shipment it is never going to make.
The buyer would be shown a warehouse-to-door window for a parcel the seller is
about to post themselves.

So the declaration is ``provider`` **and** ``fulfillment_mode`` together, which is
exactly the pair the two live fulfillment readers already use:
``marketplace_supplier_checkout`` refuses a non-``DROPSHIP`` source with
``merchant_stocked``, and ``suppliers/fulfillment`` filters its obligation list on
``s.fulfillment_mode = 'DROPSHIP'``. Reading one of the two columns here would
mean the delivery estimate and the order that follows it disagree about who is
shipping — the estimate quoting CJ, the dispatcher declining to send CJ an order.

Nothing ships beats anyone ships
--------------------------------
A listing whose effective type is not ``physical`` gets no delivery estimate no
matter what its source row says. Deciding it the other way round would quote
freight for a download. The tie-break is not arbitrary: the supplier dispatcher
*already* refuses a non-physical order, so an estimate built on the opposite
precedence would be promising a delivery the rest of the platform is built to
decline.

The type itself comes from ``marketplace_listing_types.effective_listing_type``
rather than from either column directly, because ``listing_type`` was added after
``product_type`` and a listing that predates it only has the latter. That
function is the platform's own rule, and it is the same one the supplier
dispatcher uses, so a legacy row classifies identically in all three places.

Why visibility is answered here too
-----------------------------------
Nothing else in this package reads ``marketplace_listings`` at all — ``origin``
and ``variant_facts`` go straight to the supplier tables. So as things stand the
engine would quote a draft, a rejected listing, or one belonging to a suspended
seller, and the estimate would be correct and the listing unbuyable. Worse, it
would cost a supplier call per unpublished product, which is how a seller's
private drafts end up warming CJ's cache.

The predicate is ``marketplace_listing_lifecycle.public_sql`` verbatim, with the
same two joins its other callers use. It is not restated: it covers listing
status, moderation status, the seller's own status, the store-name invariant and
stock, and a second spelling of it here would drift the day one of those five
rules changed. This module *asks* the canonical question; it does not own it.

Why no estimate copy, and why ``estimated_delivery`` is not read
----------------------------------------------------------------
``marketplace_listings.estimated_delivery`` is a 200-character free-text field a
seller types, and ``marketplace_storefront.fulfilment_html`` prints it verbatim
under the label "Estimated delivery". It is empty on every production row today,
which is the only reason there is no conflict yet. It is deliberately not read
here: it is a hard-coded duration in a database column, which is the thing §5
forbids, and rendering it beside a computed window would put two delivery
promises with different provenance on one screen. Retiring the field is a
product decision and a migration, and it is named here so that decision is made
on purpose rather than discovered.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from services import db, marketplace_listing_lifecycle as lifecycle
from services import marketplace_listing_types as listing_types
from services import marketplace_supplier_schema as supplier_schema

from . import quote, variant_facts

#: The one supplier this deployment can quote. Borrowed from ``variant_facts``
#: rather than restated: the two must agree about which ``provider`` value means
#: CJ, and a second spelling would let a listing be declared supplier-fulfilled
#: by this module and then found unparseable by that one.
SUPPLIER_NAME = variant_facts.SUPPLIER_NAME

#: The only mode under which the supplier actually ships. See the module
#: docstring; the same constant gates the live dispatch path.
DROPSHIP = supplier_schema.MODE_DROPSHIP

#: The effective listing type that has anything to deliver. The other four
#: (``digital``, ``service``, ``event``, ``booking``) are all "nothing is posted",
#: which is what ``quote.FULFILLMENT_DIGITAL`` means in this domain — the constant
#: is named after the commonest case, not the whole set.
PHYSICAL = "physical"

_LISTINGS = "marketplace_listings"
_SELLERS = "marketplace_sellers"
_SOURCES = supplier_schema.SOURCE_TABLE

#: The connection coordinates a supplier adapter is built from. All three or none:
#: ``connections.worker_adapter`` takes them as a triple and a partial triple
#: selects a different merchant's connection or none at all.
_CONNECTION_FIELDS = ("supplier_connection_id", "business_id", "store_id")


def declaration(variant_ref: Any, *,
                connect: Callable[[], Any] = db.connect) -> Dict[str, Any]:
    """Whether a buyer may ask about this listing, and who would ship it.

    Returns ``{"visible", "fulfillment", "supplier"}``.

    ``visible`` is false for a listing that does not exist and for one the buyer
    could not reach anyway; the two are deliberately the same answer, because
    telling an unauthenticated caller which listing ids exist as drafts is a
    disclosure the estimate has no reason to make.

    ``fulfillment`` is one of ``quote.FULFILLMENT_TYPES``, or ``None`` when the
    listing is not visible. It is never guessed: see the module docstring.

    ``supplier`` carries ``{"connection_id", "business_id", "store_id"}`` when a
    supplier adapter could be built, and ``None`` otherwise — *including* for a
    listing that is declared supplier-fulfilled but whose connection mapping is
    incomplete. That combination is intentional and is explained at
    :func:`_supplier`.

    Raises :class:`variant_facts.VariantRefInvalid` for a reference that names no
    listing, by parsing through ``variant_facts.parse_ref`` rather than with its
    own parser. One grammar for a variant reference across the domain means a
    caller cannot be accepted here and rejected one layer down.
    """
    parsed = variant_facts.parse_ref(variant_ref)
    listing_id = parsed["listing_id"]

    conn = connect()
    try:
        row = _row(
            conn,
            # Only the two type columns are read from the result. The seller table
            # is joined for `public_sql`'s sake and nothing is selected from it:
            # the predicate is evaluated by the database, so projecting the
            # columns it tests would only create a second, divergent copy of the
            # visibility decision in Python.
            f"SELECT l.listing_type, l.product_type FROM {_LISTINGS} l "
            f"LEFT JOIN {_SELLERS} ms ON ms.user_id = l.seller_user_id "
            f"WHERE l.id = ? AND {lifecycle.public_sql('l', 'ms')} LIMIT 1",
            (listing_id,),
        )
        if row is None:
            return _absent()
        source = _row(conn, f"SELECT * FROM {_SOURCES} WHERE listing_id = ? LIMIT 1",
                      (listing_id,))
    finally:
        conn.close()

    if _effective_type(row) != PHYSICAL:
        # Nothing is posted, so nothing has a transit time. Decided before the
        # source row is consulted at all -- see "Nothing ships beats anyone
        # ships".
        return {"visible": True, "fulfillment": quote.FULFILLMENT_DIGITAL,
                "supplier": None}

    if not _supplier_fulfilled(source):
        # Physical, and nobody has declared a supplier for it -- or has declared
        # one and also declared that the merchant holds the stock. Either way the
        # seller posts it, and this domain has nothing to say about how long that
        # takes: PulseSoc holds no carrier relationship for a seller's own parcel.
        return {"visible": True, "fulfillment": quote.FULFILLMENT_SELLER,
                "supplier": None}

    return {"visible": True, "fulfillment": quote.FULFILLMENT_SUPPLIER,
            "supplier": _supplier(source)}


def _absent() -> Dict[str, Any]:
    """The answer for a listing a buyer cannot see, whatever the reason.

    ``fulfillment`` is ``None`` rather than any declared type, so a caller that
    ignores ``visible`` and passes it straight to ``quote_delivery`` gets
    ``fulfillment_undeclared`` — no estimate — instead of an estimate for a
    listing nobody may buy.
    """
    return {"visible": False, "fulfillment": None, "supplier": None}


def _effective_type(row: Any) -> str:
    """The listing's type by the platform's own rule, not by one column."""
    return listing_types.effective_listing_type(
        _maybe(row, "listing_type"), _maybe(row, "product_type"))


def _supplier_fulfilled(source: Any) -> bool:
    """Whether the declared source says *the supplier ships this*.

    Both halves are required, and the comparison is on normalized text because
    these columns are written by several callers and one of them stores the mode
    lowercase. An unrecognised provider is not supplier-fulfilled *here* even if
    it is a supplier somewhere: ``PROVIDERS`` lists ``printful`` and ``printify``,
    neither of which appears in a single line of code in this repository, and
    this package binds exactly one adapter. Answering ``SUPPLIER`` for one of
    them would hand a Printful product to the CJ provider, which would quote a
    parcel CJ has never seen.
    """
    if source is None:
        return False
    provider = _text(_maybe(source, "provider"))
    mode = _text(_maybe(source, "fulfillment_mode"))
    if provider is None or mode is None:
        return False
    return provider.lower() == SUPPLIER_NAME and mode.upper() == DROPSHIP


def _supplier(source: Any) -> Optional[Dict[str, Any]]:
    """The connection coordinates, or ``None`` when they are not all present.

    Returning ``None`` beside ``fulfillment=SUPPLIER`` looks contradictory and is
    the honest answer. The listing genuinely is supplier-fulfilled — the
    merchant declared it and the dispatcher will act on it — and we genuinely
    cannot reach the supplier, because the row that maps this listing to a
    credential is incomplete.

    The two alternatives are both worse. Reclassifying it as
    ``FULFILLMENT_SELLER`` would tell the buyer the seller arranges delivery,
    which is false and which the checkout would then contradict. Inventing a
    partial triple would authorize against whatever connection those columns
    happen to match. Left as it is, the caller's adapter source finds nothing to
    build, raises, and the buyer is told the estimate is unavailable — which is
    exactly what is true, and which ``entry.REASON_CONNECTION_UNAVAILABLE`` sends
    to the credential vault rather than to the catalogue.
    """
    found = {}
    for column in _CONNECTION_FIELDS:
        value = _text(_maybe(source, column))
        if value is None:
            return None
        found[column] = value
    return {"connection_id": found["supplier_connection_id"],
            "business_id": found["business_id"],
            "store_id": found["store_id"]}


def _row(conn: Any, sql: str, params: tuple) -> Optional[Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def _maybe(row: Any, column: str) -> Any:
    """One column, or ``None`` if this row has no such column.

    Both row types in this codebase raise rather than return ``None`` for an
    absent key -- ``sqlite3.Row`` raises ``IndexError``, ``db.CompatRow`` a
    ``KeyError`` -- and the columns read here arrive through
    ``add_columns_if_missing``, so a database that has not been through the
    current ``init_db`` is genuinely missing some of them. An absent column is
    the same as an unset one for every decision this module makes.
    """
    try:
        return row[column]
    except (KeyError, IndexError, TypeError):
        return None


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.strip() or None
