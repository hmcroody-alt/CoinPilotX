"""Choosing which shipping route a quote refers to.

Why this is not in ``estimate``
------------------------------
Selecting a route and estimating an arrival date are different decisions with
different inputs, and fusing them produces a specific bug: an estimator that
picks its own route will happily pick the *fastest* one to quote from, while
fulfillment later books the *cheapest* one. Nothing is inconsistent inside either
module, the buyer is simply told a date for a service nobody bought.

So selection happens once, here, and its answer travels with the estimate. The
option identifiers in the result are what fulfillment must book. A date computed
from one ``option_id`` and an order placed against another is the failure this
separation exists to make visible.

Determinism
-----------
The same candidate set must always yield the same route. Not "usually" — a PDP
and a checkout page that call this seconds apart must agree, and provider
responses do not arrive in a stable order. Every comparison therefore ends in a
total ordering with ``option_id`` as the final tie-break, so the result never
depends on the order CJ happened to list its channels in.

What is deliberately not done
-----------------------------
**An unknown freight cost is not a cheap one.** ``provider_total`` of ``None``
means the quote did not price that route, and sorting ``None`` as zero would make
the least-understood option always win. Such options are excluded from a
cost-ranked policy and the exclusion is reported, not silently applied.

**An option with no readable transit range cannot be selected for a delivery
promise.** It may be perfectly shippable; it simply cannot answer the question
this domain exists to answer, and substituting a duration for it is the
fabrication the provider boundary already refused to perform.

**There is no fallback pick.** When nothing is eligible the result says so. A
selector that returns its least-bad rejected candidate rather than nothing is how
an unshippable route reaches a checkout button.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: Ranked cheapest-first among routes that meet a declared speed ceiling. This is
#: the ordinary policy: the buyer is not charged freight, so PulseSoc pays it, and
#: the platform's interest is the cheapest route that still arrives acceptably
#: soon. It is not "cheapest" — a ceiling the caller declares is what stops a
#: $0.40 saving buying a six-week delivery.
POLICY_CHEAPEST_ACCEPTABLE = "CHEAPEST_ACCEPTABLE"

#: Ranked soonest-first. For a route chosen on speed rather than cost.
POLICY_FASTEST = "FASTEST"

POLICIES = (POLICY_CHEAPEST_ACCEPTABLE, POLICY_FASTEST)

#: Why a candidate was not eligible. Reported per option, because "no delivery
#: estimate available" with no further detail is unactionable for whoever has to
#: work out whether the corridor, the catalogue or the credentials are at fault.
EXCLUDED_UNAVAILABLE = "provider_marked_unavailable"
EXCLUDED_NO_TRANSIT = "no_readable_transit_range"
EXCLUDED_NO_COST = "freight_cost_unknown"
EXCLUDED_TOO_SLOW = "exceeds_declared_transit_ceiling"
EXCLUDED_MALFORMED = "malformed_option"

#: No route selected, and the reason is structural rather than per-option.
NO_CANDIDATES = "no_candidates_offered"
NO_ELIGIBLE = "no_eligible_route"


class RoutingRejected(ValueError):
    """The caller asked for a policy that does not exist, or a malformed ceiling."""


def select_route(options, *, policy: str = POLICY_CHEAPEST_ACCEPTABLE,
                 ceiling_days: int | None = None) -> dict:
    """Pick one route from a provider's quote options, deterministically.

    ``options`` are the normalized quotes from the supplier boundary. Each needs
    ``option_id``; a route that cannot be identified cannot be booked later, so an
    option without one is malformed rather than merely unranked.

    ``ceiling_days`` is the declared upper bound on acceptable transit for
    :data:`POLICY_CHEAPEST_ACCEPTABLE`. Left ``None``, no ceiling is applied and
    the policy really is "cheapest" — which is a legitimate choice, but it has to
    be an explicit one, so the result records ``ceiling_days`` either way.

    Returns ``{"selected", "reason", "eligible", "excluded", "policy",
    "ceiling_days"}``. ``selected`` is the chosen option dict or ``None``.
    """
    if policy not in POLICIES:
        raise RoutingRejected(f"unknown routing policy {policy!r}")
    if ceiling_days is not None and (
            isinstance(ceiling_days, bool) or not isinstance(ceiling_days, int) or ceiling_days < 1):
        raise RoutingRejected("ceiling_days must be a positive whole number of days or None")

    rows = list(options or [])
    excluded: list[dict] = []
    eligible: list[dict] = []

    for option in rows:
        if not isinstance(option, dict):
            excluded.append({"option_id": None, "reason": EXCLUDED_MALFORMED})
            continue
        option_id = option.get("option_id")
        if not isinstance(option_id, str) or not option_id.strip():
            excluded.append({"option_id": None, "reason": EXCLUDED_MALFORMED})
            continue
        if not option.get("available"):
            excluded.append({"option_id": option_id, "reason": EXCLUDED_UNAVAILABLE})
            continue
        transit = option.get("transit")
        if not isinstance(transit, dict) or not isinstance(transit.get("max_days"), int):
            excluded.append({"option_id": option_id, "reason": EXCLUDED_NO_TRANSIT})
            continue
        if ceiling_days is not None and transit["max_days"] > ceiling_days:
            excluded.append({"option_id": option_id, "reason": EXCLUDED_TOO_SLOW})
            continue
        cost = _freight(option.get("provider_total"))
        if policy == POLICY_CHEAPEST_ACCEPTABLE and cost is None:
            excluded.append({"option_id": option_id, "reason": EXCLUDED_NO_COST})
            continue
        eligible.append({"option": option, "cost": cost, "transit": transit, "option_id": option_id})

    if not rows:
        return _result(None, NO_CANDIDATES, eligible, excluded, policy, ceiling_days)
    if not eligible:
        return _result(None, NO_ELIGIBLE, eligible, excluded, policy, ceiling_days)

    eligible.sort(key=lambda row: _sort_key(row, policy))
    return _result(eligible[0]["option"], None, eligible, excluded, policy, ceiling_days)


def _sort_key(row: dict, policy: str) -> tuple:
    """A total ordering. The trailing ``option_id`` is what makes it total.

    Without it two channels at the same price and speed would rank by whatever
    order the provider listed them in, and the selection would flip between two
    calls that saw the same facts.
    """
    transit = row["transit"]
    speed = (transit["max_days"], transit.get("min_days", transit["max_days"]))
    # Decimal, not float: freight differences are routinely in the cents, and a
    # float comparison that ranks 8.10 above 8.10 is a non-deterministic selector.
    cost = row["cost"] if row["cost"] is not None else Decimal("Infinity")
    if policy == POLICY_FASTEST:
        return (speed[0], speed[1], cost, row["option_id"])
    return (cost, speed[0], speed[1], row["option_id"])


def _freight(value) -> Decimal | None:
    """Freight as an exact decimal, or None when the quote did not price it.

    None is never coerced to zero. ``estimate_shipping`` already has a test named
    for this — a missing total is not free shipping — and the same claim has to
    hold when the number is used to rank rather than to display.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _result(selected, reason, eligible, excluded, policy, ceiling_days) -> dict:
    return {
        "selected": selected, "reason": reason, "policy": policy,
        "ceiling_days": ceiling_days,
        "eligible": [row["option_id"] for row in eligible],
        "excluded": excluded,
    }
