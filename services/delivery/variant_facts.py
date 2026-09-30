"""What a listing's chosen variant weighs, and which supplier variant it is.

Why this exists
---------------
``providers/cj_logistics.py`` refuses to guess: it needs a ``vid``, a ``sku``, an
origin country, a weight in grams and CJ's logistics properties, and it raises
rather than default any of them. This module is what answers that question for a
listing, and it is deliberately the *only* place in the delivery domain that
knows about ``marketplace_product_sources`` or ``supplier_snapshots``.

Four of the five facts are already on this machine
--------------------------------------------------
The order path (``fulfillment.quote_for_order``) reads them through
``gateway.get_snapshot``, which is a plain read of a ``supplier_snapshots`` row
rather than a call to CJ. So a product page needs no supplier traffic at all to
learn what a parcel weighs — the import already wrote it down:

* ``pid`` and the snapshot id come from ``marketplace_product_sources``, keyed by
  listing. One row per listing, enforced by ``idx_mkt_source_listing``, so the
  read is single-valued without a tenant tuple to match.
* ``vid`` and ``sku`` come from the ``marketplace_variants`` row for the variant
  the buyer actually chose. Using the listing-level ``provider_variant_id``
  instead would quote whichever variant the importer happened to bind, and a
  200-gram shirt and a 2-kilo pair of boots on one listing do not share a parcel.
  That column is only populated when the listing has a single orderable variant
  anyway (``importer._sole_orderable``), so it is the fallback and not the source.
* ``weight_grams`` and the logistics properties come from the snapshot payload,
  matched on ``(pid, vid)`` — never on position, because a supplier reordering
  its variant list would silently reassign every weight.

The fifth fact is not, and is not invented
------------------------------------------
Nothing local records a warehouse country. ``marketplace_product_sources`` has
``inventory_source`` and ``inventory_reference``, but those hold the string
``"cj"`` and the pid. The real answer needs CJ's inventory, which is why
``fulfillment._stocked_origin`` pays a call for it and says in its own first line
that it is "Not a hardcoded 'CN'".

So ``origin_lookup`` is injected. Origin is a function of ``(pid, vid)`` and of
stock — not of the buyer, the destination or the quantity — so it belongs to a
cache tier of its own, shared by every buyer of that variant, rather than to a
per-quote path. Building that tier here would bury a supplier call inside what is
otherwise a local read, and hard-coding ``"CN"`` would put a fabricated origin
underneath every estimate the domain produces. A lookup that cannot answer yields
no facts, the provider refuses by name, and the page says it cannot say.

The snapshot is a merchant document and this read has no actor
-------------------------------------------------------------
``gateway.get_snapshot`` demands an authenticated actor in a merchant scope. A
visitor on a product page is not one, for the same reason set out in
``providers/cj_logistics.py``: they have no merchant identity to authorize with.
This module therefore reads the row directly — and that makes the projection
load-bearing rather than tidy. The payload holds CJ's *wholesale* prices beside
the weights (``fulfillment`` reads ``variant["price"]`` out of this same body),
so returning it, or any part of it not named below, would put supplier cost
economics one careless caller away from an unauthenticated page. §33–35 forbid
that outright. Exactly five keys leave this module.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, Optional

from services import db, marketplace_supplier_schema as supplier_schema

#: The provider this module can describe variants for. Every fact below is read
#: out of a CJ snapshot shape, so answering for another supplier would mean
#: reading its payload with CJ's field names.
SUPPLIER_NAME = "cj"

#: Separates the listing from the variant inside a ``variant_ref``. A ref with no
#: separator names the listing's sole orderable variant.
REF_SEPARATOR = ":"

_SOURCES = supplier_schema.SOURCE_TABLE
_VARIANTS = supplier_schema.VARIANT_TABLE

FACT_KEYS = frozenset({"vid", "sku", "origin", "weight_grams", "properties"})


class VariantRefInvalid(ValueError):
    """The reference does not name a listing, so there is nothing to look up."""


def parse_ref(variant_ref: Any) -> Dict[str, Optional[str]]:
    """Split ``"<listing_id>"`` or ``"<listing_id>:<variant_key>"``.

    Raising on a malformed ref rather than returning no facts keeps a coding
    mistake distinguishable from a listing that genuinely has no weight on file:
    the first is a bug, the second is a product page that honestly cannot say.
    """
    if not isinstance(variant_ref, str) or not variant_ref.strip():
        raise VariantRefInvalid("a variant_ref must be a non-empty string")
    listing, _, variant_key = variant_ref.strip().partition(REF_SEPARATOR)
    try:
        listing_id = int(listing)
    except (TypeError, ValueError):
        raise VariantRefInvalid(
            f"{variant_ref!r} does not begin with a listing id") from None
    if listing_id < 1:
        raise VariantRefInvalid("a listing id is a positive integer")
    return {"listing_id": listing_id, "variant_key": variant_key.strip() or None}


def resolver(*, origin_lookup: Callable[[str, str], Optional[str]],
             connect: Callable[[], Any] = db.connect) -> Callable[[str], Optional[dict]]:
    """A ``facts(variant_ref)`` callable of the shape the CJ provider expects.

    Bound rather than called directly so the provider keeps taking a plain
    one-argument callable and stays unaware of both the database and the origin
    tier.
    """
    def facts(variant_ref: str) -> Optional[dict]:
        return describe(variant_ref, origin_lookup=origin_lookup, connect=connect)
    return facts


def describe(variant_ref: str, *,
             origin_lookup: Callable[[str, str], Optional[str]],
             connect: Callable[[], Any] = db.connect) -> Optional[dict]:
    """The five logistics facts for one variant, or ``None`` if any is missing.

    All-or-nothing on purpose. A partial dict would reach the provider, which
    would refuse it on the first absent key anyway — but by then the refusal
    names whichever fact happened to be checked first rather than the one that is
    actually absent, and the repair goes to the wrong column.
    """
    parsed = parse_ref(variant_ref)
    conn = connect()
    try:
        source = _row(conn, f"SELECT * FROM {_SOURCES} WHERE listing_id=? LIMIT 1",
                      (parsed["listing_id"],))
        if source is None or str(source["provider"] or "").lower() != SUPPLIER_NAME:
            return None
        pid = _text(source["provider_product_id"])
        vid = _text(source["provider_variant_id"])
        sku = _text(source["external_sku"])
        if parsed["variant_key"] is not None:
            variant = _row(
                conn,
                f"SELECT * FROM {_VARIANTS} WHERE listing_id=? AND variant_key=? LIMIT 1",
                (parsed["listing_id"], parsed["variant_key"]))
            if variant is None:
                # The ref names a variant this listing does not have. Not the
                # listing's default: quoting the wrong variant's parcel is the
                # failure this whole path exists to avoid.
                return None
            vid = _text(variant["provider_variant_id"]) or vid
            sku = _text(variant["sku"]) or sku
        payload = _snapshot(conn, _text(source["source_snapshot_id"]))
    finally:
        conn.close()

    if not pid or not vid or payload is None:
        return None

    described = _from_payload(payload, pid=pid, vid=vid)
    if described is None:
        return None
    sku = sku or described["sku"]
    origin = origin_lookup(pid, vid)
    if not (sku and origin and isinstance(origin, str) and len(origin.strip()) == 2):
        return None

    return {
        "vid": vid,
        "sku": sku,
        "origin": origin.strip().upper(),
        "weight_grams": described["weight_grams"],
        "properties": described["properties"],
    }


def _from_payload(payload: dict, *, pid: str, vid: str) -> Optional[dict]:
    """The weight, sku and logistics properties this snapshot states.

    The variant is found by ``(pid, vid)`` rather than by index, and a weight
    must be a positive real number — ``normalize._cj_variant`` leaves
    ``weight_grams`` null for a variant CJ did not weigh, and ``0`` is not a
    parcel.
    """
    declared = payload.get("logistics_properties")
    if not isinstance(declared, (list, tuple)):
        # A bare string would iterate into one property per character, and
        # "ORDINARY" would reach CJ as eight single-letter productProps.
        declared = ()
    properties = [p for p in declared if isinstance(p, str) and p]
    if not properties:
        # CJ routes batteries, liquids and magnets down different channels and
        # its freight endpoint rejects a request with no productProp at all.
        # Substituting "ORDINARY" would quote the wrong service for precisely
        # the goods where the difference matters.
        return None
    for row in payload.get("variants") or ():
        if not isinstance(row, dict) or row.get("pid") != pid or row.get("vid") != vid:
            continue
        grams = row.get("weight_grams")
        if isinstance(grams, bool) or not isinstance(grams, (int, float)):
            return None
        if not math.isfinite(grams) or grams <= 0:
            return None
        return {"weight_grams": grams, "sku": _text(row.get("sku")),
                "properties": properties}
    return None


def _snapshot(conn: Any, snapshot_id: Optional[str]) -> Optional[dict]:
    """The product snapshot body, or ``None``.

    ``kind`` is checked because an inventory or shipping snapshot has neither a
    variant list nor logistics properties, and reading one would produce "this
    variant has no weight" for a listing whose weight is recorded perfectly well
    one row over.
    """
    if not snapshot_id:
        return None
    row = _row(conn, "SELECT kind, payload_json FROM supplier_snapshots "
                     "WHERE snapshot_id=? LIMIT 1", (snapshot_id,))
    if row is None or row["kind"] != "product":
        return None
    try:
        body = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _row(conn: Any, sql: str, params: tuple) -> Optional[Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.strip() or None
