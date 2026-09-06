"""The Capital Command Center — one honest summary over two governed reads.

Why this is not in ``capital_graph.py``
---------------------------------------
``capital_graph`` is pinned: it holds no SQL, imports no ``graph``, and
:data:`capital_graph.NO_AGGREGATE_VALUE` records a deliberate refusal to
produce a total from a *bounded traversal of mixed-provenance nodes*. That
refusal is correct and this module does not weaken it: nothing here sums
graph nodes.

What this module sums is narrower and defensible — the two projections that
already carry their own completeness flags:

* :func:`portfolio_projection.portfolio_view` — the member's holdings,
  valued at read time in USD, with ``None`` (never ``0``) for an unknown
  quantity, price or basis, and ``totals.complete`` stating whether every
  row was priced.
* :func:`obligation_projection.liabilities_view` — the member's projected
  obligations, with ``amount`` ``None`` for an obligation the record store
  has no figure for, and amounts grouped by their own currency.

Both are owner-only, both are bounded and both report truncation. This
module composes them and does arithmetic **only over the subset each one
marks as known**, then states in the payload exactly what was left out. It
performs no SQL and touches no writer.

The one number this file will not produce
-----------------------------------------
A net worth presented as fact. :func:`overview` returns
``net_position.estimated`` together with ``net_position.complete`` and an
``excluded`` block, and ``complete`` is False whenever anything at all was
left out — including the case that costs members the most: **no liabilities
recorded**. A store with $1M of priced assets and no debt records must not
render "net worth $1M", because the absence of a mortgage record is not
evidence of no mortgage. That case sets ``complete`` False with reason
``no_liabilities_recorded``, and a client that renders ``estimated`` without
reading ``complete`` is the bug the mutation tests exist to catch.

No FX either. A liability in EUR is not converted into the USD asset side; it
is excluded and counted in ``excluded.foreign_currency_liabilities``. An
approved rate source carrying a timestamp would be a separate decision with
its own provenance, not a constant in this file.

Coverage is a formula, not a mood
---------------------------------
:data:`COVERAGE_FORMULA` and :data:`COVERAGE_DIMENSIONS` publish it. Each
dimension is a plain ``known / countable`` ratio; the score is the unweighted
mean of the dimensions that had anything to count, and is ``None`` when
nothing was countable at all. There is no hand-tuned weighting and no
decorative floor — a member with an empty store gets ``None``, not 100%.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from services.private_office import capital_graph as _capital
from services.private_office import obligation_projection as _obligations
from services.private_office import portfolio_projection as _portfolio

LOGGER = logging.getLogger("private_office.capital_overview")

#: The matrix row this surface belongs to — the same one the graph reads use,
#: named from there rather than repeated, so a rename cannot leave the overview
#: gated on a feature id nothing else knows about.
FEATURE_ID = _capital.FEATURE_ID

#: Market prices come from ``market_data.live_market_board``, which quotes in
#: USD. The asset side is therefore USD, and only the USD share of the
#: liability side can be set against it without inventing a rate.
BASE_CURRENCY = "USD"

#: How the net position is derived, in one sentence, carried in the payload so
#: a client renders the caveat rather than reconstructing it.
NET_POSITION_BASIS = (
    "Priced asset value minus liabilities recorded in the same currency. "
    "Unpriced assets, unquantified liabilities and liabilities in other "
    "currencies are excluded and counted, never treated as zero."
)

#: Published so the number is auditable (mission §41). A coverage score that
#: cannot be recomputed by hand from the payload is decoration.
COVERAGE_FORMULA = (
    "score = mean(known / countable) over the dimensions that had anything to "
    "count. Dimensions: pricing (assets with a market value / assets), "
    "cost_basis (assets with a known basis / assets), liability_amounts "
    "(obligations with a stated amount / obligations), evidence (records "
    "carrying at least one supporting fact / records). A dimension with a zero "
    "denominator is omitted, not scored as 1. An empty store scores None."
)

COVERAGE_DIMENSIONS: tuple[str, ...] = (
    "pricing", "cost_basis", "liability_amounts", "evidence",
)

#: Concentration rows returned by the exposure surface. Bounded because a
#: member with hundreds of holdings does not need every one of them ranked to
#: learn where their money is; the tail is reported as a count.
MAX_CONCENTRATIONS = 12

#: Review items are a prompt to go and fix something, not a second inbox.
MAX_REVIEW_ITEMS = 40

DENIED_NOT_OWNER = "actor_is_not_owner"


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------
def _denied(reason: str) -> dict:
    """The refusal shape. Identical for every reason, and never a thin payload.

    A denied overview is not an overview of nothing: a client that renders an
    empty command center over a refusal tells the member their affairs are
    empty, which is the one lie this whole surface exists to avoid.
    """
    return {
        "ok": False,
        "denied": {"reason": reason},
        "assets": {},
        "liabilities": {},
        "net_position": {},
        "coverage": {},
        "concentrations": {},
        "needs_review": [],
        "prices": {},
        "sync": {},
    }


def _ratio(known: int, countable: int) -> dict:
    """One coverage dimension, carrying its own arithmetic for the client."""
    return {
        "known": int(known),
        "countable": int(countable),
        "ratio": (float(known) / float(countable)) if countable > 0 else None,
    }


def _review(kind: str, subject: str, detail: str, source: str) -> dict:
    """A review item that explains itself (mission §35).

    ``what`` / ``why`` / ``source`` are all required because an unexplained
    badge is worse than no badge: the member cannot act on it and cannot
    judge whether it matters.
    """
    return {"kind": kind, "subject": subject, "detail": detail, "source": source}


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------
def overview(cur, *, owner_user_id: int, actor_user_id: int) -> dict:
    """The command center payload for one owner.

    Owner-only. Composes the portfolio and liability projections, both of
    which enforce owner scoping themselves — this function re-checks first
    anyway, so a future refactor that loosens one of them still cannot open
    the summary.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id or 0)
    if owner <= 0 or actor != owner:
        return _denied(DENIED_NOT_OWNER)

    portfolio = _portfolio.portfolio_view(
        cur, owner_user_id=owner, actor_user_id=actor)
    if not portfolio.get("ok"):
        return _denied(str((portfolio.get("denied") or {}).get("reason")
                           or DENIED_NOT_OWNER))

    liabilities = _obligations.liabilities_view(
        cur, owner_user_id=owner, actor_user_id=actor)
    if not liabilities.get("ok"):
        return _denied(str((liabilities.get("denied") or {}).get("reason")
                           or DENIED_NOT_OWNER))

    asset_rows = list(portfolio.get("assets") or ())
    asset_totals = dict(portfolio.get("totals") or {})
    liability_rows = list(liabilities.get("liabilities") or ())
    liability_totals = dict(liabilities.get("totals") or {})

    # --- asset side --------------------------------------------------------
    # Summed from the rows rather than read from ``totals.value``, which is
    # deliberately None whenever anything is unpriced. What is wanted here is
    # the *priced* subtotal, which is a different and weaker claim, and it is
    # labelled as such everywhere it appears.
    priced_rows = [row for row in asset_rows if row.get("value") is not None]
    priced_value = sum(float(row["value"]) for row in priced_rows)
    unpriced_rows = [row for row in asset_rows if row.get("value") is None]
    basis_known = int(asset_totals.get("basis_known") or 0)

    assets_block = {
        "priced_value": priced_value if priced_rows else 0.0,
        "currency": BASE_CURRENCY,
        "count": len(asset_rows),
        "priced": len(priced_rows),
        "unpriced": len(unpriced_rows),
        "unpriced_symbols": list(asset_totals.get("unpriced_symbols") or ()),
        "basis_known": basis_known,
        "known_cost": asset_totals.get("cost"),
        # From the projection, not recomputed: it also accounts for missing
        # quantity facts, which a value-only scan here would not see.
        "complete": bool(asset_totals.get("complete")),
    }

    # --- liability side ----------------------------------------------------
    by_currency = dict(liability_totals.get("by_currency") or {})
    base_bucket = by_currency.get(BASE_CURRENCY) or {"amount": 0.0, "count": 0}
    known_liabilities = float(base_bucket.get("amount") or 0.0)
    liability_count = int(liability_totals.get("count") or 0)
    unquantified = int(liability_totals.get("unquantified") or 0)
    unspecified_currency = int(liability_totals.get("unspecified_currency") or 0)
    foreign = sum(
        int(bucket.get("count") or 0)
        for name, bucket in by_currency.items()
        if name not in (BASE_CURRENCY, _obligations.CURRENCY_UNSPECIFIED)
    )

    liabilities_block = {
        "known_amount": known_liabilities,
        "currency": BASE_CURRENCY,
        "count": liability_count,
        "quantified": int(liability_totals.get("quantified") or 0),
        "unquantified": unquantified,
        "foreign_currency": foreign,
        "unspecified_currency": unspecified_currency,
        "by_currency": by_currency,
        "complete": bool(liability_totals.get("complete")),
        "truncated": bool(liability_totals.get("truncated")),
    }

    # --- net position ------------------------------------------------------
    excluded = {
        "unpriced_assets": len(unpriced_rows),
        "unquantified_liabilities": unquantified,
        "foreign_currency_liabilities": foreign,
        "unspecified_currency_liabilities": unspecified_currency,
    }
    reasons: list[str] = []
    if unpriced_rows:
        reasons.append("unpriced_assets")
    if not asset_totals.get("complete"):
        reasons.append("incomplete_portfolio")
    if unquantified:
        reasons.append("unquantified_liabilities")
    if foreign or unspecified_currency:
        reasons.append("uncomparable_currency")
    if liability_totals.get("truncated"):
        reasons.append("liabilities_truncated")
    # The expensive omission: no debt on file is not the same as no debt.
    if liability_count == 0:
        reasons.append("no_liabilities_recorded")

    net_block = {
        "estimated": priced_value - known_liabilities,
        "currency": BASE_CURRENCY,
        "known_assets": priced_value,
        "known_liabilities": known_liabilities,
        "complete": not reasons,
        "incomplete_reasons": reasons,
        "excluded": excluded,
        "basis": NET_POSITION_BASIS,
        # Repeated from the graph module so a client reading only this payload
        # still finds the reason there is no authoritative net worth here.
        "disclaimer": _capital.NO_AGGREGATE_VALUE,
    }

    # --- coverage ----------------------------------------------------------
    records_total = len(asset_rows) + len(liability_rows)
    evidenced = sum(
        1 for row in asset_rows + liability_rows
        if ((row.get("evidence") or {}).get("fact_ids") or [])
    )
    dimensions = {
        "pricing": _ratio(len(priced_rows), len(asset_rows)),
        "cost_basis": _ratio(basis_known, len(asset_rows)),
        "liability_amounts": _ratio(liability_count - unquantified,
                                    liability_count),
        "evidence": _ratio(evidenced, records_total),
    }
    scored = [dim["ratio"] for dim in dimensions.values()
              if dim["ratio"] is not None]
    coverage_block = {
        "dimensions": dimensions,
        "score": (sum(scored) / len(scored)) if scored else None,
        "scored_dimensions": [name for name in COVERAGE_DIMENSIONS
                              if dimensions[name]["ratio"] is not None],
        "formula": COVERAGE_FORMULA,
    }

    # --- concentration -----------------------------------------------------
    concentrations = _concentrations(priced_rows, priced_value,
                                     liability_rows, known_liabilities)

    # --- needs review ------------------------------------------------------
    review = _needs_review(portfolio, asset_rows, liability_rows,
                           liability_totals)

    return {
        "ok": True,
        "denied": {},
        "assets": assets_block,
        "liabilities": liabilities_block,
        "net_position": net_block,
        "coverage": coverage_block,
        "concentrations": concentrations,
        "needs_review": review[:MAX_REVIEW_ITEMS],
        "needs_review_total": len(review),
        "prices": dict(portfolio.get("prices") or {}),
        "sync": {
            "portfolio": dict(portfolio.get("sync") or {}),
            "liabilities": dict(liabilities.get("sync") or {}),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _concentrations(priced_rows: list[dict], priced_value: float,
                    liability_rows: list[dict],
                    known_liabilities: float) -> dict:
    """Where the money is, over the subset whose value is actually known.

    Shares are percentages **of the priced total**, never of a guess at the
    real total, and the count of what was excluded travels with them so a
    client can say "of your priced assets" rather than "of your assets".

    Counterparty, custodian and geographic exposure are absent on purpose:
    nothing in the current projections records a custodian or a counterparty,
    and a concentration chart assembled from fields that do not exist would
    be the most convincing kind of wrong.
    """
    assets: list[dict] = []
    if priced_value > 0:
        ranked = sorted(priced_rows, key=lambda row: float(row["value"]),
                        reverse=True)
        for row in ranked[:MAX_CONCENTRATIONS]:
            value = float(row["value"])
            assets.append({
                "key": str(row.get("symbol") or ""),
                "label": str(row.get("name") or row.get("symbol") or ""),
                "value": value,
                "share": value / priced_value,
            })

    kinds: dict[str, float] = {}
    for row in liability_rows:
        if row.get("amount") is None:
            continue
        kind = str(row.get("kind") or "").strip() or "unspecified"
        kinds[kind] = kinds.get(kind, 0.0) + float(row["amount"])
    liabilities = [
        {
            "key": kind,
            "label": kind,
            "value": amount,
            "share": (amount / known_liabilities) if known_liabilities > 0 else None,
        }
        for kind, amount in sorted(kinds.items(), key=lambda item: -item[1])
    ][:MAX_CONCENTRATIONS]

    return {
        "assets": assets,
        "asset_basis": "priced_asset_value",
        "asset_total": priced_value,
        "assets_ranked": len(assets),
        "assets_unranked_tail": max(0, len(priced_rows) - len(assets)),
        "liabilities": liabilities,
        "liability_basis": "quantified_liability_amount",
        "liability_total": known_liabilities,
        "currency": BASE_CURRENCY,
    }


def _needs_review(portfolio: dict, asset_rows: list[dict],
                  liability_rows: list[dict],
                  liability_totals: dict) -> list[dict]:
    """The specific, explained gaps in the picture (mission §117).

    Every item names the source system that owns the fix. Capital Graph
    cannot resolve any of these itself — an unpriced asset is fixed in the
    market data source or the Portfolio, an unquantified obligation in the
    record store — and an item that implied otherwise would send the member
    to a screen with no button on it.
    """
    items: list[dict] = []

    prices = dict(portfolio.get("prices") or {})
    if str(prices.get("source") or "") == "unavailable":
        items.append(_review(
            "price_source_unavailable", "market_data",
            "Live prices could not be read, so asset values are unavailable "
            "rather than current.",
            "market_data",
        ))

    for row in asset_rows:
        symbol = str(row.get("symbol") or "")
        if row.get("quantity") is None:
            items.append(_review(
                "unknown_quantity", symbol,
                "No quantity is recorded for this holding, so it has no value "
                "and is excluded from every total.",
                "portfolio",
            ))
        elif row.get("value") is None:
            items.append(_review(
                "unpriced_asset", symbol,
                "No market price is available, so this holding is excluded "
                "from the priced total.",
                "market_data",
            ))
        if row.get("cost_basis") is None:
            items.append(_review(
                "unknown_cost_basis", symbol,
                "Cost basis is unknown, so gain or loss cannot be computed "
                "for this holding.",
                "portfolio",
            ))

    for row in liability_rows:
        label = str(row.get("title") or "") or f"obligation:{row.get('root_id')}"
        if row.get("amount") is None:
            items.append(_review(
                "unquantified_liability", label,
                "This obligation has no recorded amount. It is counted but not "
                "subtracted; unknown is not zero.",
                "operations",
            ))
        elif not row.get("currency"):
            items.append(_review(
                "unspecified_currency", label,
                "An amount is recorded without a currency, so it cannot be "
                "compared with the rest of the picture.",
                "operations",
            ))

    if liability_totals.get("truncated"):
        items.append(_review(
            "liabilities_truncated", "obligations",
            "More obligations exist than this read returns, so liability "
            "figures cover only part of the set.",
            "operations",
        ))

    return items


__all__ = [
    "FEATURE_ID", "BASE_CURRENCY", "NET_POSITION_BASIS",
    "COVERAGE_FORMULA", "COVERAGE_DIMENSIONS",
    "MAX_CONCENTRATIONS", "MAX_REVIEW_ITEMS",
    "DENIED_NOT_OWNER", "overview",
]
