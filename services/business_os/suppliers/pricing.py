"""Retail price from supplier cost, and the margin the merchant actually has.

The one rule
------------
**Unknown cost produces unknown price and unknown margin.** Not zero, not a
default, not the cost. Every function here returns ``None`` rather than a number
when the input it needs is absent, and the caller is expected to render that as
"unavailable".

This sounds obvious and is the single most likely thing to be quietly removed.
``cost or 0`` and ``(price - cost) / price`` both look like tidying up, and both
turn "we could not read the supplier's price" into "this product has a 100%
margin" — a claim the merchant will act on and which is not true.

Margin states are named, not numeric, for the same reason: a caller that
receives ``0.59`` has to decide what counts as healthy, and a caller that
receives a state cannot accidentally decide that ``None`` is a low number.

Item cost is not landed cost
----------------------------
The supplier's per-item cost is not what the merchant pays the supplier. CJ
bills freight on every order, and a 45% target margin computed against the item
alone is not a 45% margin — on a cheap, heavy product it can be a negative one.
The merchant is nonetheless shown ``HEALTHY``, because the number is arithmetically
correct about the wrong quantity.

So shipping is a first-class, nullable input here, and every quote says which
cost its margin was measured against via ``margin_basis``. Two things that look
like simplifications must not happen:

* ``shipping_cents or 0`` — an unknown freight cost then reads as free shipping,
  which is the same fabrication as ``cost or 0`` and produces the same
  confidently wrong margin.
* dropping ``margin_basis`` because "the caller knows" — the caller is a JSON
  payload rendered by a screen written by someone else, and a 45% that silently
  means two different things depending on a store setting is worse than either.

There is deliberately **no platform default shipping number**. A default margin
is a policy choice the platform is entitled to make; a default freight cost is a
claim about what a supplier charges, and §1 forbids inventing one. When nobody
has declared an allowance, shipping is unknown, the basis is :data:`ITEM`, and
the margin is exactly the number this module produced before landed cost
existed — correct about the item, and now labelled as such.
"""

from __future__ import annotations

#: Merchant sets every price by hand; the engine proposes nothing.
MANUAL_PRICE = "MANUAL_PRICE"
#: retail = cost + fixed markup, in minor units.
COST_PLUS_FIXED = "COST_PLUS_FIXED"
#: retail = cost * (1 + percent/100).
COST_PLUS_PERCENT = "COST_PLUS_PERCENT"
#: retail = cost * multiplier.
MULTIPLIER = "MULTIPLIER"
#: retail = cost / (1 - margin/100) — the price at which margin is achieved.
TARGET_MARGIN = "TARGET_MARGIN"

RULES = (MANUAL_PRICE, COST_PLUS_FIXED, COST_PLUS_PERCENT, MULTIPLIER, TARGET_MARGIN)

HEALTHY = "HEALTHY"
LOW_MARGIN = "LOW_MARGIN"
CRITICAL_MARGIN = "CRITICAL_MARGIN"
NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
UNKNOWN = "UNKNOWN"

MARGIN_STATES = (HEALTHY, LOW_MARGIN, CRITICAL_MARGIN, NEGATIVE_MARGIN, UNKNOWN)

#: Percentage-point boundaries between margin states. Below 0 is negative,
#: [0,10) critical, [10,25) low, >=25 healthy.
CRITICAL_BELOW = 10.0
LOW_BELOW = 25.0

#: A price of a billion minor units is a fat-finger, not a product.
MAX_PRICE_CENTS = 1_000_000_000

#: Margin measured against the supplier's item cost alone. What every margin in
#: this system meant before landed cost existed, and still the honest answer when
#: no shipping figure has been declared.
ITEM = "ITEM"
#: Margin measured against item cost plus the merchant's declared per-unit
#: shipping allowance. Only reachable when that allowance exists.
LANDED = "LANDED"

MARGIN_BASES = (ITEM, LANDED)


class PricingRejected(ValueError):
    """A pricing rule that cannot be applied as specified."""


def normalize_rule(rule) -> dict:
    """Validate a merchant pricing rule into a canonical dict.

    ``None`` means manual pricing, which is the correct default: a merchant who
    has expressed no rule has not consented to the system choosing their prices.
    """
    if rule is None:
        return {"type": MANUAL_PRICE}
    if not isinstance(rule, dict):
        raise PricingRejected("pricing rule must be an object")
    kind = str(rule.get("type") or MANUAL_PRICE).strip().upper()
    if kind not in RULES:
        raise PricingRejected("unknown pricing rule")
    if kind == MANUAL_PRICE:
        return {"type": MANUAL_PRICE}
    value = rule.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PricingRejected("pricing rule needs a numeric value")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise PricingRejected("pricing rule value must be finite")
    if kind == COST_PLUS_FIXED:
        if not 0 <= value <= MAX_PRICE_CENTS:
            raise PricingRejected("markup out of range")
        return {"type": kind, "value": int(value)}
    if kind == COST_PLUS_PERCENT:
        if not 0 <= value <= 100_000:
            raise PricingRejected("percent out of range")
        return {"type": kind, "value": value}
    if kind == MULTIPLIER:
        if not 0 < value <= 1000:
            raise PricingRejected("multiplier out of range")
        return {"type": kind, "value": value}
    # TARGET_MARGIN. 100% is excluded because cost/(1-1) is a division by zero:
    # there is no finite price at which margin is exactly 100% of a nonzero cost.
    if not 0 <= value < 100:
        raise PricingRejected("target margin must be between 0 and 100")
    return {"type": kind, "value": value}


def apply_rule(rule, cost_cents) -> int | None:
    """Proposed retail in minor units, or ``None`` when it cannot be computed.

    Returns ``None`` for manual pricing and for unknown cost. Note that these
    two produce the same output for opposite reasons — no rule, versus a rule
    with nothing to apply it to — and the caller is right not to distinguish
    them: in both cases the merchant has to type a price.
    """
    rule = rule if isinstance(rule, dict) else normalize_rule(rule)
    kind = rule.get("type", MANUAL_PRICE)
    if kind == MANUAL_PRICE or cost_cents is None:
        return None
    try:
        cost = int(cost_cents)
    except (TypeError, ValueError):
        return None
    if cost < 0:
        return None
    value = rule.get("value")
    if kind == COST_PLUS_FIXED:
        price = cost + int(value)
    elif kind == COST_PLUS_PERCENT:
        price = cost * (1.0 + float(value) / 100.0)
    elif kind == MULTIPLIER:
        price = cost * float(value)
    elif kind == TARGET_MARGIN:
        price = cost / (1.0 - float(value) / 100.0)
    else:
        return None
    price = int(price + 0.5)
    if price < 0 or price > MAX_PRICE_CENTS:
        return None
    return price


def normalize_shipping(shipping_cents) -> int | None:
    """A per-unit shipping allowance as a usable integer, or ``None``.

    ``None`` for anything that is not a plain non-negative integer within
    :data:`MAX_PRICE_CENTS` — including ``True``, which ``isinstance(x, int)``
    accepts and which would otherwise become a one-cent freight charge.

    Zero is a real answer and must survive: suppliers do ship free, and a
    merchant who declares "freight is included in the item cost" has said
    something specific. Collapsing ``0`` into ``None`` here would turn that
    declaration into "unknown" and downgrade their quotes to :data:`ITEM` basis
    for stating the very fact that makes the two bases identical.
    """
    if shipping_cents is None or isinstance(shipping_cents, bool):
        return None
    if not isinstance(shipping_cents, (int, float)):
        return None
    value = float(shipping_cents)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if value < 0 or value > MAX_PRICE_CENTS:
        return None
    return int(value)


def landed_cost_cents(cost_cents, shipping_cents) -> int | None:
    """Item cost plus shipping, or ``None`` when either half is unknown.

    The one rule, applied to a second input. An unknown freight cost does not
    make landed cost equal to item cost — that is the ``shipping_cents or 0``
    fabrication with an extra step, and it produces a landed margin identical to
    the item margin while claiming to have accounted for shipping.

    A known cost with unknown shipping therefore returns ``None``, and the caller
    is expected to fall back to the item basis *explicitly* rather than receive a
    number that quietly already has.
    """
    shipping = normalize_shipping(shipping_cents)
    if shipping is None or cost_cents is None:
        return None
    try:
        cost = int(cost_cents)
    except (TypeError, ValueError):
        return None
    if cost < 0:
        return None
    total = cost + shipping
    if total > MAX_PRICE_CENTS:
        return None
    return total


def margin_cents(retail_cents, cost_cents) -> int | None:
    """Gross profit in minor units, or None when either side is unknown.

    A negative result is returned as-is. Selling below cost is a real decision
    and clamping it to zero would hide the only case worth an alert.
    """
    if retail_cents is None or cost_cents is None:
        return None
    try:
        return int(retail_cents) - int(cost_cents)
    except (TypeError, ValueError):
        return None


def margin_percent(retail_cents, cost_cents) -> float | None:
    """Margin as a percentage of *retail*, or None.

    Percent of retail, not of cost — that is what "59% margin" means in
    commerce, and computing it against cost would overstate every number the
    merchant sees. A zero retail price yields ``None`` rather than a division by
    zero or a fabricated 0%.
    """
    profit = margin_cents(retail_cents, cost_cents)
    if profit is None:
        return None
    retail = int(retail_cents)
    if retail <= 0:
        return None
    return round(profit * 100.0 / retail, 2)


def margin_state(retail_cents, cost_cents) -> str:
    """Named margin health. ``UNKNOWN`` whenever either side is unknown.

    The first branch is the one that matters: an unknown cost yields UNKNOWN,
    never HEALTHY. A merchant scanning a list for problems reads absence of a
    warning as "fine", so a product with unreadable economics must carry its own
    badge rather than blend into the healthy rows.
    """
    percent = margin_percent(retail_cents, cost_cents)
    if percent is None:
        return UNKNOWN
    if percent < 0:
        return NEGATIVE_MARGIN
    if percent < CRITICAL_BELOW:
        return CRITICAL_MARGIN
    if percent < LOW_BELOW:
        return LOW_MARGIN
    return HEALTHY


def basis(cost_cents, shipping_cents) -> tuple[str, int | None]:
    """Which cost this quote is measured against, and what it is.

    :data:`LANDED` exactly when a landed cost could be computed, :data:`ITEM`
    otherwise. Returned as a pair so that the name and the number cannot drift
    apart: every caller that reports one reports the other from the same call,
    rather than recomputing the condition and getting it subtly different.
    """
    landed = landed_cost_cents(cost_cents, shipping_cents)
    if landed is None:
        return ITEM, cost_cents
    return LANDED, landed


def quote(rule, cost_cents, retail_cents=None, shipping_cents=None) -> dict:
    """The full economics of one variant, for display.

    ``retail_cents`` overrides the rule when the merchant has already set a
    price — merchant-entered prices always win over a computed proposal, which
    is the storefront half of the merchant/supplier field-ownership split.

    ``shipping_cents`` is the merchant's declared per-unit shipping allowance, and
    when it is present *everything* here moves onto the landed basis: the proposed
    price, the margin, and the state. A store that declares its freight and then
    receives a price computed as though freight were free would have declared it
    for nothing.

    Omitting it reproduces this function's output before landed cost existed,
    plus the four new keys — ``shipping_cents`` and ``landed_cost_cents`` as
    ``None``, ``basis_cost_cents`` equal to ``cost_cents``, and ``margin_basis``
    as :data:`ITEM`. Existing callers therefore see no number change, which is
    the point: the new basis arrives only where a merchant asked for it.

    ``cost_cents`` stays the *item* cost in the payload. It is the number the
    supplier stated and the one the variant row stores, and overwriting it with
    the landed figure would make the quote disagree with the database about what
    the supplier charges for the product.
    """
    shipping = normalize_shipping(shipping_cents)
    margin_basis, basis_cents = basis(cost_cents, shipping)
    proposed = apply_rule(rule, basis_cents)
    retail = retail_cents if retail_cents is not None else proposed
    return {
        "cost_cents": cost_cents,
        "shipping_cents": shipping,
        "landed_cost_cents": landed_cost_cents(cost_cents, shipping),
        "basis_cost_cents": basis_cents,
        "margin_basis": margin_basis,
        "retail_cents": retail,
        "proposed_retail_cents": proposed,
        "margin_cents": margin_cents(retail, basis_cents),
        "margin_percent": margin_percent(retail, basis_cents),
        "margin_state": margin_state(retail, basis_cents),
    }
