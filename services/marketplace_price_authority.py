"""One answer to "what will this listing cost at checkout".

Every buyer-facing surface prices a listing through
:func:`services.marketplace_web.derive_price`, which prefers
``marketplace_listing_variants.price_cents`` and falls back to the seller's
``price_label``. Every checkout lane priced it by parsing ``price_label`` alone.
Measured against production on 2026-09-29 over 123 listings, 117 of which carry
live variants: 82 rows have an empty ``price_label`` while displaying a real
variant price, and 3 more parse a label that differs from the displayed amount by
up to $35.71. So the two answers disagreed on 85 of 123 listings, and the shape
of the disagreement was not "off by a rounding step".

This module is the authority checkout asks. It resolves to a single unit price or
it refuses; it never invents one. Two refusals exist because they are different
facts with different fixes:

``NO_AUTHORITATIVE_PRICE``
    Neither the variants nor the label price this listing. The seller has not
    priced it. Nothing downstream can guess.

``VARIANT_SELECTION_REQUIRED``
    Variants price it, but at more than one price — 17 production listings span a
    range, the widest $6.25..$49.40 — and the caller named no variant. Charging
    the low end would undercharge the seller and charging the high end would
    overcharge the buyer, and the buyer's screen showed a range, so there is no
    amount here that both parties have agreed to.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from services import marketplace_web

#: Mirrors ``bot.MAX_PRICE_LABEL_CENTS``. Duplicated rather than imported because
#: ``bot`` imports this package; the label parser clamps and so must this one, or
#: routing checkout through the variant authority would quietly remove a ceiling.
MAX_UNIT_PRICE_MINOR = 99_999_999

NO_PRICE = "NO_AUTHORITATIVE_PRICE"
VARIANT_REQUIRED = "VARIANT_SELECTION_REQUIRED"

NO_PRICE_MESSAGE = "This item is not priced for checkout yet."
VARIANT_REQUIRED_MESSAGE = "Choose an option before you check out."

#: Public so a surface reading a whole page's variants in one statement spells
#: the same columns as :func:`fetch_variants`. A reader that selects fewer of
#: them prices a listing differently from checkout, which is the defect this
#: module exists to prevent.
VARIANT_COLUMNS = "id, listing_id, price_cents, currency, status, stock_state"


class PriceDecision:
    """The resolved unit price, or the reason there isn't one."""

    __slots__ = ("unit_price_minor", "currency", "source", "variant_id",
                 "error_code", "message", "min_cents", "max_cents")

    def __init__(self, unit_price_minor: Optional[int], currency: str, source: str,
                 variant_id: Optional[int] = None, error_code: str = "",
                 message: str = "", min_cents: Optional[int] = None,
                 max_cents: Optional[int] = None) -> None:
        self.unit_price_minor = unit_price_minor
        self.currency = currency
        self.source = source
        self.variant_id = variant_id
        self.error_code = error_code
        self.message = message
        self.min_cents = min_cents
        self.max_cents = max_cents

    @property
    def ok(self) -> bool:
        return self.error_code == "" and bool(self.unit_price_minor)


def _active_priced(variants: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        v for v in variants
        if v.get("price_cents") is not None
        and int(v.get("price_cents") or 0) > 0
        and str(v.get("status") or "active").strip().lower() == "active"
    ]


def fetch_variants(cur, listing_id: int) -> list[dict]:
    """Read a listing's variants, or return ``[]`` if the table is unreachable.

    Degrading to the label is the same answer the product page gives when this
    read fails, so buyer and checkout still agree. The caller then refuses on its
    own if the label prices nothing.
    """
    try:
        cur.execute(
            f"SELECT {VARIANT_COLUMNS} FROM marketplace_listing_variants WHERE listing_id=?",
            (int(listing_id),),
        )
        return [dict(row) for row in cur.fetchall()]
    except Exception:
        return []


def resolve_unit_price(listing: Mapping[str, Any],
                       variants: Sequence[Mapping[str, Any]] = (),
                       variant_id: Optional[int] = None) -> PriceDecision:
    """The amount checkout may charge for one unit, or a refusal.

    A named variant wins outright: picking Large has to move the amount that is
    charged, not just the label under the picture.
    """
    view = marketplace_web.derive_price(listing, variants)
    currency = view.currency or "USD"
    priced = _active_priced(variants)

    if variant_id:
        chosen = next((v for v in priced if int(v.get("id") or 0) == int(variant_id)), None)
        if chosen is not None:
            cents = min(int(chosen["price_cents"]), MAX_UNIT_PRICE_MINOR)
            chosen_currency = (str(chosen.get("currency") or currency).strip().upper() or "USD")
            return PriceDecision(cents, chosen_currency, "variant",
                                 variant_id=int(chosen["id"]),
                                 min_cents=cents, max_cents=cents)
        # A variant id that is gone, archived or unpriced must not silently fall
        # through to the listing's cheapest option: the buyer chose something, and
        # the thing they chose is no longer purchasable.
        return PriceDecision(None, currency, "none", error_code=NO_PRICE,
                             message=NO_PRICE_MESSAGE)

    if not view.known:
        return PriceDecision(None, currency, "none", error_code=NO_PRICE,
                             message=NO_PRICE_MESSAGE)
    if view.source == "variants" and view.is_range:
        return PriceDecision(None, currency, "variants", error_code=VARIANT_REQUIRED,
                             message=VARIANT_REQUIRED_MESSAGE,
                             min_cents=view.min_cents, max_cents=view.max_cents)

    cents = min(int(view.min_cents or 0), MAX_UNIT_PRICE_MINOR)
    if cents <= 0:
        return PriceDecision(None, currency, view.source, error_code=NO_PRICE,
                             message=NO_PRICE_MESSAGE)
    single_variant = priced[0] if view.source == "variants" and len(priced) == 1 else None
    return PriceDecision(cents, currency, view.source,
                         variant_id=int(single_variant["id"]) if single_variant else None,
                         min_cents=cents, max_cents=cents)


def resolve_for_listing(cur, listing: Mapping[str, Any],
                        variant_id: Optional[int] = None) -> PriceDecision:
    """:func:`resolve_unit_price` with the variant read done for the caller."""
    listing_id = int(listing.get("id") or 0)
    variants = fetch_variants(cur, listing_id) if listing_id else []
    return resolve_unit_price(listing, variants, variant_id)
