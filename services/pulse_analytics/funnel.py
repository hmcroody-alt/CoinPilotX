"""The exposure-to-outcome funnel, as arithmetic over rows.

Pure. No database, no clock. Every input is passed in so that every branch can
be tested against rows chosen to break it.

What this will not tell you
---------------------------

It will not tell you that showing a product caused someone to buy it.

The evidence available is a sequence: an impression exists, and later an order
exists, and both name the same listing. That is a *path*, and a path is
consistent with the exposure having caused the sale, with the buyer having
already decided and the impression being incidental, and with the buyer having
arrived from a search engine and never seeing the placement at all. Nothing in
``commerce_discovery_impression_events`` distinguishes these, because the
impression row is written when the product is rendered, not when it is read.

So the terminal figures are reported as ``orders_with_prior_exposure`` and
``orders_without_exposure``, and the ratio is named ``exposed_order_share``
rather than "conversion rate". ``attribution_basis`` is the literal string
``"path_only"`` on every report, so a caller that ships this to a seller as a
causal claim has to type over the disclaimer to do it.

The ordering of the funnel steps is imported from ``commerce_discovery.events``
and not restated. That module is explicit that it will not infer a missing step:
a purchase with no recorded click is a real thing — the user saved the product
and came back days later — and backfilling the click would make the funnel a
fiction. Steps are therefore counted independently. **The counts are not
required to descend.**
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from services.commerce_discovery.events import ENGAGEMENT_ACTIONS

#: The step a seller's outcome number is allowed to come from. ``purchase`` is
#: absent on purpose — see the module docstring and :func:`summarize`.
CLIENT_REPORTED_STEPS = tuple(a for a in ENGAGEMENT_ACTIONS if a != "purchase")

ATTRIBUTION_BASIS = "path_only"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A share, or ``None`` when there is nothing to divide by.

    ``None`` rather than ``0.0``: a surface with no impressions has not got a
    0% click rate, it has not been measured. Rendering "0%" for the unmeasured
    case is how a dashboard tells a seller their listing is failing when in
    fact it has never been shown.
    """
    if denominator <= 0:
        return None
    return numerator / float(denominator)


def summarize(
    impressions: Iterable[Mapping[str, Any]],
    engagements: Iterable[Mapping[str, Any]],
    *,
    seller_metrics: Optional[Mapping[str, Any]] = None,
    orders: Iterable[Mapping[str, Any]] = (),
) -> dict:
    """One seller's funnel over one window.

    ``impressions`` and ``engagements`` are ``commerce_discovery_*`` rows.
    ``seller_metrics`` is the output of
    :func:`services.business_os.marketplace.seller_metrics.compute` — the
    authority on what a confirmed order is. ``orders`` are the same order rows
    that were fed to it, needed here only for the listing-level exposure join,
    which ``compute`` has no reason to expose.

    With ``seller_metrics`` absent the outcome block reports ``None`` rather
    than zero, for the same reason ``deriveKpis`` on the phone renders an em
    dash: a number this module invented for itself is precisely what
    ``seller_metrics`` was written to remove.
    """
    impression_rows = [r for r in impressions if not _int(r.get("self_view"))]
    engagement_rows = list(engagements)
    order_rows = list(orders)

    exposed_listings: set[int] = set()
    by_surface: dict[str, int] = {}
    for row in impression_rows:
        listing_id = _int(row.get("listing_id"))
        if listing_id:
            exposed_listings.add(listing_id)
        surface = _text(row.get("surface"))
        if surface:
            by_surface[surface] = by_surface.get(surface, 0) + 1

    steps: dict[str, int] = {action: 0 for action in ENGAGEMENT_ACTIONS}
    for row in engagement_rows:
        action = _text(row.get("action"))
        if action in steps:
            steps[action] += 1

    impression_count = len(impression_rows)
    report: dict[str, Any] = {
        "impressions": impression_count,
        "unique_listings_exposed": len(exposed_listings),
        "impressions_by_surface": by_surface,
        "steps": {action: steps[action] for action in CLIENT_REPORTED_STEPS},
        "client_reported_purchases": steps["purchase"],
        "click_through_rate": _rate(steps["click"], impression_count),
        "attribution_basis": ATTRIBUTION_BASIS,
    }

    if seller_metrics is None:
        report["outcome"] = {
            "confirmed_orders": None,
            "net_sales_minor": None,
            "currency": None,
            "orders_with_prior_exposure": None,
            "orders_without_exposure": None,
            "exposed_order_share": None,
            "client_server_purchase_gap": None,
            "source": "unavailable",
        }
        return report

    confirmed = _int(seller_metrics.get("confirmed_orders"))
    with_exposure = 0
    without_exposure = 0
    for row in order_rows:
        if not _is_confirmed(row):
            continue
        if _int(row.get("item_id")) in exposed_listings:
            with_exposure += 1
        else:
            without_exposure += 1

    report["outcome"] = {
        "confirmed_orders": confirmed,
        "net_sales_minor": _int(seller_metrics.get("net_sales_minor")),
        "currency": seller_metrics.get("currency") or "USD",
        "orders_with_prior_exposure": with_exposure,
        "orders_without_exposure": without_exposure,
        "exposed_order_share": _rate(with_exposure, with_exposure + without_exposure),
        # Positive means the client claimed more sales than the ledger confirms.
        # Reported, never reconciled away: which of the two is wrong is not
        # knowable from here, and averaging them would produce a third number
        # that is wrong in a new way.
        "client_server_purchase_gap": steps["purchase"] - confirmed,
        "source": "seller_metrics.compute",
    }
    return report


def _is_confirmed(row: Mapping[str, Any]) -> bool:
    """Delegates to the one definition of a confirmed order.

    Imported inside the function so this module stays importable when the
    business_os package is unavailable — the caller then supplies
    ``seller_metrics=None`` and the outcome block reports unavailable, which is
    the honest answer rather than a zero.
    """
    from services.business_os.marketplace import seller_metrics as _sm

    return _sm.is_confirmed_order(row)
