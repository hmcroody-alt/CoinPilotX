"""Addressable variants and supplier provenance for ``marketplace_listings``.

This is the write and read layer over the two tables owned by
``marketplace_supplier_schema``. It is deliberately not wired into any route or
buyer surface yet; see "Scope" below.

What a variant is here
----------------------
A *purchasable configuration* of a listing — the unit that can hold a SKU, a
supplier cost, a stock level and a provider's own identifier. The listing remains
the product. Nothing in this module can create a product, publish one, or change
what a buyer is charged.

Three decisions worth stating, because each one has a cheaper wrong version
that would pass every test written the obvious way.

**Unknown stock is not out of stock.** ``availability`` returns one of three
values and never collapses ``UNKNOWN`` into ``OUT_OF_STOCK``. The live column
cannot express this — ``marketplace_listing_lifecycle.inventory_available``
returns ``False`` for a NULL quantity — so a failed supplier sync currently
presents as a sell-out. Those are different facts: a sell-out is the merchant's
to fix and an unknown is the integration's, and a storefront that silently
converts the second into the first hides an outage behind a plausible-looking
product page. Callers are handed the distinction and must decide; this module
refuses to decide for them by collapsing it.

**Unknown cost is not zero cost.** ``margin_cents`` returns ``None`` when
``cost_cents`` is NULL rather than treating the cost as nothing, because
``cost or 0`` reports a full-price margin on a product whose economics are
unknown. A missing number and a zero are the same shape and opposite meanings,
and margin is exactly where that confusion becomes money.

**Refusals do not leak existence.** Writing to a listing owned by somebody else
and writing to a listing that does not exist produce the same rejection with the
same message. Distinguishing them would turn this into an oracle for "does
listing N exist", enumerable by a caller who owns nothing.

Scope
-----
No route calls this. No buyer surface reads it. ``price_cents`` is nullable and
NULL means "no variant price; the listing's ``price_label`` governs", which is
today's behaviour exactly. That ordering is deliberate: ``marketplace_listings``
stores retail money as prose and has live orders against it, so introducing a
second thing that claims to know the price is how the two drift. Making variants
a money authority is a migration, not a schema change, and it is not this change.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from services import marketplace_supplier_schema as _schema

VARIANT_TABLE = _schema.VARIANT_TABLE
SOURCE_TABLE = _schema.SOURCE_TABLE

STOCK_UNKNOWN = _schema.STOCK_UNKNOWN
STOCK_IN_STOCK = _schema.STOCK_IN_STOCK
STOCK_OUT_OF_STOCK = _schema.STOCK_OUT_OF_STOCK

PROVIDERS = _schema.PROVIDERS
FULFILLMENT_MODES = _schema.FULFILLMENT_MODES

#: Availability is the same three-valued vocabulary as stock state, restated as
#: the answer to a different question ("can this be bought") so a caller reading
#: an availability result is not tempted to write it back as a stock state.
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

#: A ceiling, not a guess at how many variants a real product has. CJ apparel
#: routinely exceeds the twelve that ``marketplace_listing_types`` allows in
#: JSON, so the old cap cannot simply be reused; a bound that is too low silently
#: truncates a catalogue. This one exists so a malformed or hostile import cannot
#: turn one listing into an unbounded write.
MAX_VARIANTS_PER_LISTING = 250

#: Option names/values are bounded for the same reason the listing's own text
#: columns are, and at the same order of magnitude.
MAX_OPTION_NAME = 40
MAX_OPTION_VALUE = 80
MAX_OPTIONS_PER_VARIANT = 8


class VariantRejected(ValueError):
    """A variant or source write was refused.

    A dedicated type so callers can tell "this input is wrong" from "this query
    broke". Import code needs that distinction: the first is a row to skip and
    report, the second is a run to abort.
    """


def _now() -> str:
    """UTC, second resolution, no offset suffix.

    Byte-identical to the ``datetime.utcnow().isoformat(timespec="seconds")``
    that every neighbouring marketplace table is written with (see the checkout
    path around ``bot.py:89327``) — deliberately, because these rows sit beside
    reservations and listings and a column that suddenly carried ``+00:00``
    would sort and compare differently from its siblings for no stated reason.
    The aware-then-stripped form is only here to avoid the ``utcnow``
    deprecation; the stored string is unchanged. Changing the format is a
    migration of every marketplace timestamp, not a tidy-up of this one.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Option normalisation and the variant key
# ---------------------------------------------------------------------------

def normalize_options(options: Any) -> list[dict]:
    """Clean an option list into ``[{"name","value"}, ...]``.

    Rejects rather than silently dropping. A supplier import that sends a
    malformed option set should fail loudly on that product, not quietly produce
    a variant that is missing the dimension distinguishing it from its siblings —
    which would then collide on ``variant_key`` with a sibling and overwrite it.
    """
    if options is None:
        return []
    if not isinstance(options, (list, tuple)):
        raise VariantRejected("options must be a list")
    if len(options) > MAX_OPTIONS_PER_VARIANT:
        raise VariantRejected(
            f"a variant may have at most {MAX_OPTIONS_PER_VARIANT} options")
    cleaned = []
    seen = set()
    for entry in options:
        if not isinstance(entry, Mapping):
            raise VariantRejected("each option must be an object with name and value")
        name = str(entry.get("name") or "").strip()[:MAX_OPTION_NAME]
        value = str(entry.get("value") or "").strip()[:MAX_OPTION_VALUE]
        if not name or not value:
            raise VariantRejected("each option needs a non-empty name and value")
        folded = name.casefold()
        if folded in seen:
            # Two values for one dimension is not a variant, it is two variants.
            # Accepting it would make variant_key depend on which duplicate the
            # normaliser happened to keep.
            raise VariantRejected(f"duplicate option name: {name}")
        seen.add(folded)
        cleaned.append({"name": name, "value": value})
    return cleaned


def variant_key(options: Sequence[Mapping[str, Any]]) -> str:
    """A stable identifier for an option combination.

    Order-independent by construction: the pairs are sorted on the case-folded
    name before joining, so ``[Size=M, Color=Red]`` and ``[Color=Red, Size=M]``
    yield the same key. This is what makes re-import idempotent — providers do
    not promise option ordering, and a key that depended on it would create a
    duplicate variant on every sync rather than updating the existing one.

    Case-folded for grouping, but the original case is preserved in
    ``options_json`` so the merchant still sees what the supplier sent.

    A variant with no options — a product with exactly one configuration — keys
    to ``"-"`` rather than the empty string, so that "no options" is a real key
    the unique index can hold rather than a falsy value a caller might treat as
    absent.
    """
    pairs = sorted(
        (str(o.get("name") or "").strip().casefold(),
         str(o.get("value") or "").strip().casefold())
        for o in options or ()
    )
    if not pairs:
        return "-"
    return "|".join(f"{name}={value}" for name, value in pairs)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

def _assert_owned(cur, listing_id: int, seller_user_id: int) -> None:
    """Refuse unless this seller owns this listing.

    The two failure cases — not yours, and not there — raise the *same* message
    on purpose. Splitting them would answer "does listing N exist" for any caller
    willing to iterate, which is a question this module has no reason to answer.
    """
    cur.execute(
        f"SELECT seller_user_id FROM marketplace_listings WHERE id=? LIMIT 1",
        (int(listing_id),),
    )
    row = cur.fetchone()
    owner = None
    if row is not None:
        try:
            owner = row["seller_user_id"]
        except (KeyError, IndexError, TypeError):
            owner = row[0]
    if owner is None or int(owner) != int(seller_user_id):
        raise VariantRejected("listing not found")


# ---------------------------------------------------------------------------
# Three-valued reads
# ---------------------------------------------------------------------------

def availability(variant: Mapping[str, Any]) -> str:
    """``AVAILABLE`` / ``UNAVAILABLE`` / ``UNKNOWN`` for one variant.

    ``UNKNOWN`` is returned when the stock state says so, and also when the state
    is a value this code does not recognise. An unrecognised state is not a
    licence to guess: a future writer adding a fourth state must not have its
    rows silently read as purchasable by code that predates it.

    An archived variant is ``UNAVAILABLE`` regardless of stock — that is a
    merchant decision and it is known, not unknown.
    """
    if str(variant.get("status") or "active").strip().lower() != "active":
        return UNAVAILABLE
    state = str(variant.get("stock_state") or STOCK_UNKNOWN).strip().upper()
    if state == STOCK_OUT_OF_STOCK:
        return UNAVAILABLE
    if state == STOCK_IN_STOCK:
        quantity = variant.get("stock_quantity")
        if quantity is None:
            # Declared in stock with no count. Trust the declaration: providers
            # frequently report availability without a number, and demanding a
            # count would make every such variant permanently unbuyable.
            return AVAILABLE
        try:
            return AVAILABLE if int(quantity) > 0 else UNAVAILABLE
        except (TypeError, ValueError):
            return UNKNOWN
    return UNKNOWN


def listing_availability(variants: Iterable[Mapping[str, Any]]) -> str:
    """Aggregate availability across a listing's variants.

    ``AVAILABLE`` if any variant is; otherwise ``UNKNOWN`` if any variant is
    unknown; otherwise ``UNAVAILABLE``.

    The middle clause is the whole point and it is the one a "simplification"
    removes. If every variant's state is unknown the honest answer is that we do
    not know, not that the product is sold out — reporting a sell-out would turn
    a supplier outage into a storefront that looks legitimately empty, which is
    the failure mode nobody investigates.

    A listing with no variants at all returns ``UNKNOWN``, not ``UNAVAILABLE``:
    every listing in production today has no variants, and this function must not
    imply anything about them. Their availability lives on the listing row and is
    none of this module's business.
    """
    seen_unknown = False
    seen_any = False
    for variant in variants or ():
        seen_any = True
        state = availability(variant)
        if state == AVAILABLE:
            return AVAILABLE
        if state == UNKNOWN:
            seen_unknown = True
    if not seen_any:
        return UNKNOWN
    return UNKNOWN if seen_unknown else UNAVAILABLE


def margin_cents(variant: Mapping[str, Any], retail_cents: int | None) -> int | None:
    """Retail minus supplier cost, or ``None`` when either side is unknown.

    Returns ``None`` — never a number — when ``cost_cents`` is NULL. A missing
    cost and a zero cost are the same shape in the database and opposite in
    meaning, and ``cost or 0`` quietly reports a 100% margin on a product whose
    economics nobody knows. A caller that cannot show a margin should show that
    it cannot.

    A negative result is returned as-is. Selling below cost is a real thing a
    merchant may do deliberately, and clamping it to zero would hide the one
    case worth alerting on.
    """
    cost = variant.get("cost_cents")
    if cost is None or retail_cents is None:
        return None
    try:
        return int(retail_cents) - int(cost)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Variant writes
# ---------------------------------------------------------------------------

def _coerce_minor(value: Any, field: str) -> int | None:
    """Integer minor units, or None. Refuses negatives and non-numbers.

    None passes through untouched because for both money fields it is a
    meaningful value — an unknown cost, or a variant that defers to the listing's
    price — and coercing it to 0 would destroy exactly the distinction this
    module exists to keep.
    """
    if value is None:
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        raise VariantRejected(f"{field} must be an integer number of minor units")
    if amount < 0:
        raise VariantRejected(f"{field} cannot be negative")
    return amount


def _optional_text(value: Any, limit: int) -> str | None:
    """A trimmed, length-capped string, or None for absent.

    Empty string collapses to None so that a caller passing ``""`` cannot write a
    row that reads as "bound to a connection whose id is the empty string". An
    absent supplier reference and a blank one are the same fact and must have the
    same storage, or every downstream ``IS NOT NULL`` test acquires a second case
    it will not have been written to handle.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _coerce_stock(state: Any, quantity: Any) -> tuple[str, int | None]:
    """Normalise a (state, quantity) pair, defaulting to UNKNOWN.

    An unrecognised state becomes ``UNKNOWN`` rather than raising: a sync that
    receives a state this code has not seen should degrade to "we do not know",
    which is true, rather than abort an import over a vocabulary mismatch.
    """
    resolved = str(state or STOCK_UNKNOWN).strip().upper()
    if resolved not in _schema.STOCK_STATES:
        resolved = STOCK_UNKNOWN
    if quantity is None:
        return resolved, None
    try:
        count = int(quantity)
    except (TypeError, ValueError):
        raise VariantRejected("stock_quantity must be an integer or null")
    if count < 0:
        raise VariantRejected("stock_quantity cannot be negative")
    return resolved, count


def upsert_variant(cur, *, listing_id: int, seller_user_id: int,
                   options: Any = None, sku: str | None = None,
                   provider_variant_id: str | None = None,
                   price_cents: Any = None, cost_cents: Any = None,
                   currency: str | None = None,
                   stock_state: Any = None, stock_quantity: Any = None,
                   position: int = 0, status: str = "active") -> int:
    """Create or update one variant, keyed on its option combination.

    Idempotent by ``variant_key``: importing the same supplier variant twice
    updates the existing row rather than adding a second one. This is why the key
    is order-independent — see :func:`variant_key`.

    Returns the variant id. Reads it back with a SELECT rather than trusting
    ``cur.lastrowid``: ``lastrowid`` is a SQLite concept and on PostgreSQL it
    only holds a value because ``services.db.CompatCursor`` appends ``RETURNING``
    for tables listed in ``AUTO_PK_TABLES``. A table missing from that list ends
    on ``int(None)`` in production and nowhere else — a failure this codebase has
    already shipped once, in ``ensure_pulse_saved_collection``. A re-SELECT is
    correct on every dialect and cannot regress if the list drifts.
    """
    _assert_owned(cur, listing_id, seller_user_id)

    cleaned = normalize_options(options)
    key = variant_key(cleaned)
    price = _coerce_minor(price_cents, "price_cents")
    cost = _coerce_minor(cost_cents, "cost_cents")
    state, quantity = _coerce_stock(stock_state, stock_quantity)
    now = _now()

    cur.execute(
        f"SELECT id FROM {VARIANT_TABLE} WHERE listing_id=? AND variant_key=? LIMIT 1",
        (int(listing_id), key),
    )
    row = cur.fetchone()
    if row is not None:
        try:
            existing_id = int(row["id"])
        except (KeyError, IndexError, TypeError):
            existing_id = int(row[0])
        cur.execute(
            f"UPDATE {VARIANT_TABLE} SET options_json=?, sku=?, provider_variant_id=?, "
            f"price_cents=?, cost_cents=?, currency=?, stock_quantity=?, stock_state=?, "
            f"stock_synced_at=?, position=?, status=?, updated_at=? "
            f"WHERE id=? AND seller_user_id=?",
            (json.dumps(cleaned), sku, provider_variant_id, price, cost, currency,
             quantity, state, now, int(position or 0), str(status or "active"),
             now, existing_id, int(seller_user_id)),
        )
        return existing_id

    # The cap is checked only on insert. An update cannot grow the set, and
    # checking it on every write would make a listing that is already at the
    # ceiling — legitimately, from an earlier larger import — impossible to
    # correct or restock.
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {VARIANT_TABLE} WHERE listing_id=?",
        (int(listing_id),),
    )
    count_row = cur.fetchone()
    try:
        current = int(count_row["n"])
    except (KeyError, IndexError, TypeError):
        current = int(count_row[0])
    if current >= MAX_VARIANTS_PER_LISTING:
        raise VariantRejected(
            f"a listing may have at most {MAX_VARIANTS_PER_LISTING} variants")

    cur.execute(
        f"INSERT INTO {VARIANT_TABLE} "
        f"(listing_id, seller_user_id, variant_key, options_json, sku, provider_variant_id, "
        f"price_cents, cost_cents, currency, stock_quantity, stock_state, stock_synced_at, "
        f"position, status, created_at, updated_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(listing_id), int(seller_user_id), key, json.dumps(cleaned), sku,
         provider_variant_id, price, cost, currency, quantity, state, now,
         int(position or 0), str(status or "active"), now, now),
    )
    cur.execute(
        f"SELECT id FROM {VARIANT_TABLE} WHERE listing_id=? AND variant_key=? LIMIT 1",
        (int(listing_id), key),
    )
    created = cur.fetchone()
    if created is None:
        raise VariantRejected("variant could not be created")
    try:
        return int(created["id"])
    except (KeyError, IndexError, TypeError):
        return int(created[0])


def variants_for(cur, listing_id: int) -> list[dict]:
    """Every variant on a listing, ordered by position then id.

    Not ownership-scoped: reading a listing's variants is a read of the listing,
    and the listing's own visibility rules (``marketplace_listing_lifecycle``)
    govern whether the caller should be looking at it at all. Adding a second,
    different ownership answer here is how two readers of the same question drift.
    """
    cur.execute(
        f"SELECT * FROM {VARIANT_TABLE} WHERE listing_id=? ORDER BY position ASC, id ASC",
        (int(listing_id),),
    )
    out = []
    for row in cur.fetchall():
        item = dict(row)
        try:
            item["options"] = json.loads(item.get("options_json") or "[]")
        except (TypeError, ValueError):
            item["options"] = []
        out.append(item)
    return out


def archive_variant(cur, *, variant_id: int, seller_user_id: int) -> bool:
    """Mark a variant inactive. Returns whether a row changed.

    Archive rather than delete. A variant may be referenced by an order that has
    already happened, and a purchase history that cannot name what was bought is
    worse than a row nobody sells any more.
    """
    cur.execute(
        f"UPDATE {VARIANT_TABLE} SET status='archived', updated_at=? "
        f"WHERE id=? AND seller_user_id=?",
        (_now(), int(variant_id), int(seller_user_id)),
    )
    return bool(getattr(cur, "rowcount", 0))


# ---------------------------------------------------------------------------
# Supplier source
# ---------------------------------------------------------------------------

def coerce_listing_id(value: Any) -> int:
    """Parse an external product reference into a listing id, or refuse.

    The supplier gateway carries its product reference as a string, because it
    used to name a ``business_os_mkt_products.product_id`` (``"mktp_..."``).
    Pointed at the canonical ledger it now has to name a
    ``marketplace_listings.id``, which is an integer.

    Anything unparseable raises the *same* ``VariantRejected("listing not
    found")`` that ``_assert_owned`` raises for a listing owned by someone else.
    That identical wording is load-bearing, not lazy: if a malformed reference
    produced a distinguishable error, a caller could tell "this is not a listing
    id at all" apart from "this is a listing id that is not yours", and the
    second answer is the existence oracle the whole ownership check exists to
    deny.
    """
    try:
        listing_id = int(str(value).strip())
    except (TypeError, ValueError):
        raise VariantRejected("listing not found") from None
    if listing_id <= 0:
        raise VariantRejected("listing not found")
    return listing_id


def link_source(cur, *, listing_id: int, seller_user_id: int, provider: str,
                provider_product_id: str,
                fulfillment_mode: str = _schema.MODE_DROPSHIP,
                supplier_connection_id: Any = None,
                business_id: Any = None, store_id: Any = None,
                provider_variant_id: Any = None, external_sku: Any = None,
                source_snapshot_id: Any = None,
                supplier_cost_cents: Any = None,
                supplier_cost_currency: Any = None,
                inventory_source: Any = None, inventory_reference: Any = None,
                sync_state: Any = None) -> int:
    """Record where a listing came from. Idempotent per listing.

    This is the *only* writer of supplier provenance. The CJ gateway used to
    keep its own ``supplier_product_links`` table keyed on a
    ``business_os_mkt_products`` id; that table was a second mapping authority
    for the same fact, and two authorities for one fact means the question "where
    does this listing come from" has two answers that can disagree. The gateway
    now calls this function, so there is one answer.

    ``provider`` and ``fulfillment_mode`` are separate on purpose. A product can
    be imported from CJ and stocked in the seller's own garage, or authored by
    hand and drop-shipped; one column for both makes "who do I call to fulfil
    this order" unanswerable.

    Re-linking the same listing to the same provider product *and variant*
    updates the row. Re-linking it to a different one is refused: a listing whose
    supplier silently changed underneath it would keep selling a page describing
    the old product.

    Every optional argument left as ``None`` leaves the stored value alone rather
    than nulling it. That matters most for ``supplier_cost_cents``, where None
    means "this caller is not telling us the cost" and overwriting a known cost
    with it would manufacture the unknown-cost state the caller never claimed.
    """
    _assert_owned(cur, listing_id, seller_user_id)
    resolved = str(provider or "").strip().lower()
    if resolved not in PROVIDERS:
        raise VariantRejected(f"unknown provider: {provider}")
    reference = str(provider_product_id or "").strip()[:190]
    if not reference:
        raise VariantRejected("provider_product_id is required")
    mode = str(fulfillment_mode or "").strip().upper()
    if mode not in FULFILLMENT_MODES:
        raise VariantRejected(f"unknown fulfillment_mode: {fulfillment_mode}")
    variant_ref = _optional_text(provider_variant_id, 190)
    state = str(sync_state or "").strip().upper() or None
    if state is not None and state not in _schema.SYNC_STATES:
        raise VariantRejected(f"unknown sync_state: {sync_state}")
    cost = _coerce_minor(supplier_cost_cents, "supplier_cost_cents")
    now = _now()

    optional = {
        "supplier_connection_id": _optional_text(supplier_connection_id, 190),
        "business_id": _optional_text(business_id, 190),
        "store_id": _optional_text(store_id, 190),
        "provider_variant_id": variant_ref,
        "external_sku": _optional_text(external_sku, 190),
        "source_snapshot_id": _optional_text(source_snapshot_id, 190),
        "supplier_cost_cents": cost,
        "supplier_cost_currency": _optional_text(supplier_cost_currency, 8),
        "inventory_source": _optional_text(inventory_source, 190),
        "inventory_reference": _optional_text(inventory_reference, 190),
        "sync_state": state,
    }

    cur.execute(
        f"SELECT id, provider, provider_product_id, provider_variant_id "
        f"FROM {SOURCE_TABLE} WHERE listing_id=? LIMIT 1",
        (int(listing_id),),
    )
    row = cur.fetchone()
    if row is not None:
        existing = dict(row)
        bound_variant = existing.get("provider_variant_id")
        if (str(existing.get("provider")) != resolved
                or str(existing.get("provider_product_id")) != reference
                or (variant_ref is not None and bound_variant is not None
                    and str(bound_variant) != variant_ref)):
            raise VariantRejected(
                "this listing is already linked to a different supplier product")
        sets = ["fulfillment_mode=?", "updated_at=?"]
        values: list[Any] = [mode, now]
        for column, value in optional.items():
            if value is None:
                continue
            sets.append(f"{column}=?")
            values.append(value)
        values.extend([int(existing["id"]), int(seller_user_id)])
        cur.execute(
            f"UPDATE {SOURCE_TABLE} SET {', '.join(sets)} "
            f"WHERE id=? AND seller_user_id=?",
            tuple(values),
        )
        return int(existing["id"])

    columns = ["listing_id", "seller_user_id", "provider", "provider_product_id",
               "fulfillment_mode", "overridden_fields_json", "created_at", "updated_at"]
    values = [int(listing_id), int(seller_user_id), resolved, reference, mode,
              "[]", now, now]
    for column, value in optional.items():
        if value is None:
            continue
        columns.append(column)
        values.append(value)
    cur.execute(
        f"INSERT INTO {SOURCE_TABLE} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values),
    )
    cur.execute(
        f"SELECT id FROM {SOURCE_TABLE} WHERE listing_id=? LIMIT 1",
        (int(listing_id),),
    )
    created = cur.fetchone()
    if created is None:
        raise VariantRejected("source could not be created")
    try:
        return int(created["id"])
    except (KeyError, IndexError, TypeError):
        return int(created[0])


def source_for(cur, listing_id: int) -> dict | None:
    """This listing's supplier row, or None if it was authored in PulseSoc."""
    cur.execute(
        f"SELECT * FROM {SOURCE_TABLE} WHERE listing_id=? LIMIT 1",
        (int(listing_id),),
    )
    row = cur.fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["overridden_fields"] = json.loads(item.get("overridden_fields_json") or "[]")
    except (TypeError, ValueError):
        item["overridden_fields"] = []
    return item


def supplier_binding(cur, *, listing_id: Any, supplier_connection_id: Any,
                     business_id: Any, store_id: Any) -> dict | None:
    """The supplier mapping for one listing *within one connection scope*.

    This replaces ``gateway.get_product_binding``, which read the retired
    ``supplier_product_links`` table. The scope tuple is matched, not trusted:
    asking for a listing through a connection it was not bound through returns
    None, so a worker holding a stale tuple cannot reach another tenant's row.

    It takes no ``seller_user_id`` because its callers are worker and webhook
    paths that have no actor. That is safe only because the connection scope is
    itself already owner-scoped — ``business_os_supplier_connections`` is unique
    on (business_id, store_id, provider) and the row was written by
    ``link_source``, which did check ownership. The check happens once, at bind
    time, and this read inherits it; it does not skip it.
    """
    try:
        resolved = coerce_listing_id(listing_id)
    except VariantRejected:
        return None
    cur.execute(
        f"SELECT * FROM {SOURCE_TABLE} WHERE listing_id=? AND supplier_connection_id=? "
        f"AND business_id=? AND store_id=? LIMIT 1",
        (resolved, str(supplier_connection_id), str(business_id), str(store_id)),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else None


def supplier_bindings_for_connection(cur, *, supplier_connection_id: Any,
                                     business_id: Any, store_id: Any,
                                     provider_product_id: Any = None,
                                     provider_variant_id: Any = None,
                                     variant_ids: Sequence[Any] | None = None,
                                     limit: int = 101) -> list[dict]:
    """Every supplier mapping reachable through one connection scope.

    The webhook path needs this to answer "which of my listings does this
    provider event touch". It is scoped to the connection tuple for the same
    reason the single-row read is: a provider can name any product id it likes in
    a webhook body, and the answer must be bounded by what this connection
    actually bound rather than by what the event claims.
    """
    where = ["supplier_connection_id=?", "business_id=?", "store_id=?"]
    args: list[Any] = [str(supplier_connection_id), str(business_id), str(store_id)]
    if provider_product_id is not None:
        where.append("provider_product_id=?")
        args.append(str(provider_product_id))
    if provider_variant_id is not None:
        where.append("provider_variant_id=?")
        args.append(str(provider_variant_id))
    if variant_ids is not None:
        wanted = [str(v) for v in variant_ids]
        if not wanted:
            return []
        where.append("provider_variant_id IN (" + ",".join("?" for _ in wanted) + ")")
        args.extend(wanted)
    cur.execute(
        f"SELECT * FROM {SOURCE_TABLE} WHERE {' AND '.join(where)} "
        f"ORDER BY id LIMIT {int(limit)}",
        tuple(args),
    )
    return [dict(row) for row in cur.fetchall()]


def mark_overridden(cur, *, listing_id: int, seller_user_id: int,
                    fields: Iterable[str]) -> list[str]:
    """Record that the merchant has taken ownership of these fields.

    Field-level ownership is what makes a re-sync safe. Without it the choice is
    between never refreshing (so supplier price and stock go stale) and always
    overwriting (so the merchant's edited title and margin are destroyed on the
    next sync). Neither is acceptable, so the row records which side owns what.

    Additive and idempotent: marking a field twice is a no-op, and marking a new
    one does not release the others. Releasing is a separate, deliberate act —
    it hands a field back to the supplier and the merchant should have to say so.
    """
    _assert_owned(cur, listing_id, seller_user_id)
    source = source_for(cur, listing_id)
    if source is None:
        raise VariantRejected("listing has no supplier source")
    merged = list(source.get("overridden_fields") or [])
    for field in fields or ():
        name = str(field or "").strip()
        if name and name not in merged:
            merged.append(name)
    cur.execute(
        f"UPDATE {SOURCE_TABLE} SET overridden_fields_json=?, updated_at=? "
        f"WHERE listing_id=? AND seller_user_id=?",
        (json.dumps(merged), _now(), int(listing_id), int(seller_user_id)),
    )
    return merged


def sync_updates_allowed(source: Mapping[str, Any] | None,
                         incoming: Mapping[str, Any]) -> dict:
    """Filter a supplier payload down to the fields the supplier still owns.

    Pure — it touches no database — so that the ownership rule can be tested
    exhaustively without a fixture, and so a caller cannot accidentally apply a
    half-filtered payload by calling the write before the check.

    A listing with no source row is merchant-authored, and a merchant-authored
    product has no supplier to accept updates from: everything is filtered out.
    That is deliberately not the same as "no source means no restrictions", which
    is the reading that would let a stray sync overwrite a hand-made product.
    """
    if source is None:
        return {}
    owned_by_merchant = {str(f) for f in (source.get("overridden_fields") or ())}
    return {k: v for k, v in (incoming or {}).items() if k not in owned_by_merchant}
