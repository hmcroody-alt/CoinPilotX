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


def quote(rule, cost_cents, retail_cents=None) -> dict:
    """The full economics of one variant, for display.

    ``retail_cents`` overrides the rule when the merchant has already set a
    price — merchant-entered prices always win over a computed proposal, which
    is the storefront half of the merchant/supplier field-ownership split.
    """
    proposed = apply_rule(rule, cost_cents)
    retail = retail_cents if retail_cents is not None else proposed
    return {
        "cost_cents": cost_cents,
        "retail_cents": retail,
        "proposed_retail_cents": proposed,
        "margin_cents": margin_cents(retail, cost_cents),
        "margin_percent": margin_percent(retail, cost_cents),
        "margin_state": margin_state(retail, cost_cents),
    }
