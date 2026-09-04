"""Portfolio side of Market Intelligence — what the user already owns.

``market_intelligence`` is deliberately networkless and stateless, so it cannot
know whether anyone holds anything. This module is the bridge: it reads the
existing portfolio through ``services.portfolio_service`` — the same numbers the
Portfolio screen shows — and shapes them into the small ``holding`` dict the
analysis engine accepts.

It deliberately does *not* build a second portfolio. There is one holdings
store, one cost basis, one valuation path, and this file reads it. A second
source of "what do I own" would eventually disagree with the first, and the
disagreement would surface as a verdict framed for a position the user closed
last week.

The distinction that matters throughout: **no holding record means unknown, not
zero.** A user whose portfolio failed to load is not a non-holder, and the
payloads here carry ``known`` so a caller can tell the two apart instead of
quietly rendering someone's position as absent.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Sequence

from services import market_data
from services import market_intelligence

LOGGER = logging.getLogger(__name__)

# Above this share of portfolio value, one asset is the portfolio.
CONCENTRATION_HIGH_PCT = 40.0
CONCENTRATION_MODERATE_PCT = 20.0
# Pearson r over hourly returns. Crypto is correlated by default, so the bar for
# "this adds nothing" is high — 0.85 is near-duplicate, not merely related.
CORRELATION_HIGH = 0.85
CORRELATION_MODERATE = 0.6


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _returns(series: Sequence[float]) -> list[float]:
    out = []
    for previous, current in zip(series, series[1:]):
        if previous:
            out.append((current - previous) / abs(previous))
    return out


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson r over the overlapping tail of two hourly return series.

    Real arithmetic on real series — both come from the board response that was
    already fetched, so this costs nothing. Returns None rather than 0.0 when
    there is not enough overlap: zero correlation is a strong claim and "we
    could not measure it" is the honest one.
    """
    a, b = _returns(list(left)), _returns(list(right))
    size = min(len(a), len(b))
    if size < 24:
        return None
    a, b = a[-size:], b[-size:]
    mean_a, mean_b = sum(a) / size, sum(b) / size
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / math.sqrt(var_a * var_b)


def portfolio_snapshot(user_id: Any) -> dict[str, Any]:
    """The user's holdings as this module needs them, or a stated failure.

    ``available`` false means we could not read the portfolio — which is
    different from an empty portfolio (``available`` true, no holdings), and the
    two must not be rendered the same way.
    """
    try:
        from services import portfolio_service

        data = portfolio_service.calculate_user_portfolio(int(user_id))
    except Exception as exc:  # noqa: BLE001 - a portfolio read failure is not a market failure
        LOGGER.info("Portfolio unavailable for intelligence user=%s error=%s", user_id, exc)
        return {"available": False, "holdings": [], "totalValue": None,
                "note": "Your portfolio could not be read, so verdicts are framed without it."}
    holdings = []
    for row in data.get("holdings") or []:
        symbol = str(row.get("symbol") or "").upper()
        quantity = _num(row.get("amount"))
        if not symbol or not quantity:
            continue
        holdings.append({
            "symbol": symbol,
            "quantity": quantity,
            "value": _num(row.get("value")),
            "unrealizedPnlPct": _num(row.get("pnl_percent")),
            # Legacy rows carry an amount and no basis; profit is genuinely
            # unknowable for them and must not read as break-even.
            "costKnown": row.get("pnl_percent") is not None,
        })
    return {
        "available": True,
        "holdings": holdings,
        "totalValue": _num(data.get("total_value")),
        "valuation": data.get("valuation"),
        "warning": data.get("warning") or None,
    }


def holding_for(symbol: str, snapshot: dict[str, Any] | None,
                risk_budget_pct: float = 1.0) -> dict[str, Any] | None:
    """The ``holding`` argument for ``market_intelligence.assess``, or None.

    None means "not held, as far as a portfolio we successfully read is
    concerned". When the portfolio could not be read at all this also returns
    None, and the caller is expected to surface ``snapshot['available']`` so the
    screen says the verdict is unpersonalised rather than implying the user owns
    nothing.
    """
    if not snapshot or not snapshot.get("available"):
        return None
    symbol = str(symbol or "").upper()
    row = next((h for h in snapshot.get("holdings") or [] if h["symbol"] == symbol), None)
    if not row:
        return None
    total = _num(snapshot.get("totalValue"))
    value = _num(row.get("value"))
    weight = (value / total * 100.0) if (total and value is not None and total > 0) else None
    return {
        "quantity": row.get("quantity"),
        "unrealizedPnlPct": row.get("unrealizedPnlPct"),
        "portfolioWeightPct": weight,
        "portfolioValue": total,
        "riskBudgetPct": risk_budget_pct,
        "correlation": _correlation_factor(symbol, snapshot),
    }


def _correlation_factor(symbol: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """One risk-factor row describing how much this asset duplicates the rest.

    Measured against the *largest other holding* rather than an average: the
    concentration risk in a portfolio comes from its biggest pair, and averaging
    across a long tail of small positions would dilute exactly the number that
    matters.
    """
    series = market_data.hourly_series(symbol)
    if len(series) < 48:
        return None
    others = [h for h in snapshot.get("holdings") or [] if h["symbol"] != symbol]
    others.sort(key=lambda h: _num(h.get("value")) or 0, reverse=True)
    for other in others[:5]:
        peer = market_data.hourly_series(other["symbol"])
        r = _correlation(series, peer)
        if r is None:
            continue
        if r >= CORRELATION_HIGH:
            level = market_intelligence.RISK_HIGH
        elif r >= CORRELATION_MODERATE:
            level = market_intelligence.RISK_MODERATE
        else:
            level = market_intelligence.RISK_LOW
        return {
            "level": level,
            "confidence": market_intelligence.KNOWN,
            "peer": other["symbol"],
            "r": round(r, 2),
            "detail": (f"Hourly returns move with {other['symbol']} at r={r:.2f} over the last week — "
                       f"{'these two are close to one position' if r >= CORRELATION_HIGH else 'meaningfully related' if r >= CORRELATION_MODERATE else 'largely independent'}."),
        }
    return None


def portfolio_risk(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Concentration, correlation clusters and overlap across the whole book.

    All three are properties of the portfolio rather than of any one asset,
    which is why they live here and not in the per-asset payload. Every figure
    is computed from holdings the user entered and price series already in
    memory — this function makes no request either.
    """
    if not snapshot or not snapshot.get("available"):
        return {"available": False, "concentration": None, "clusters": [],
                "note": "Portfolio risk needs a portfolio this session could read."}
    holdings = [h for h in snapshot.get("holdings") or [] if _num(h.get("value")) is not None]
    total = _num(snapshot.get("totalValue"))
    if not holdings or not total or total <= 0:
        return {"available": True, "concentration": None, "clusters": [], "positions": len(holdings),
                "note": "No valued holdings, so concentration and overlap cannot be measured."}

    weighted = sorted(
        ({"symbol": h["symbol"], "weightPct": round(_num(h["value"]) / total * 100.0, 2)} for h in holdings),
        key=lambda h: h["weightPct"], reverse=True,
    )
    top = weighted[0]
    if top["weightPct"] >= CONCENTRATION_HIGH_PCT:
        level = market_intelligence.RISK_HIGH
    elif top["weightPct"] >= CONCENTRATION_MODERATE_PCT:
        level = market_intelligence.RISK_MODERATE
    else:
        level = market_intelligence.RISK_LOW
    concentration = {
        "level": level,
        "topSymbol": top["symbol"],
        "topWeightPct": top["weightPct"],
        "positions": len(weighted),
        "weights": weighted[:10],
        "detail": f"{top['symbol']} is {top['weightPct']:.1f}% of portfolio value across {len(weighted)} positions.",
        "confidence": market_intelligence.KNOWN,
    }

    clusters = []
    measured_pairs = 0
    for index, left in enumerate(weighted[:8]):
        for right in weighted[index + 1:8]:
            r = _correlation(market_data.hourly_series(left["symbol"]),
                             market_data.hourly_series(right["symbol"]))
            if r is None:
                continue
            measured_pairs += 1
            if r >= CORRELATION_MODERATE:
                clusters.append({
                    "pair": [left["symbol"], right["symbol"]],
                    "r": round(r, 2),
                    "combinedWeightPct": round(left["weightPct"] + right["weightPct"], 2),
                    "level": market_intelligence.RISK_HIGH if r >= CORRELATION_HIGH else market_intelligence.RISK_MODERATE,
                })
    clusters.sort(key=lambda c: (c["r"], c["combinedWeightPct"]), reverse=True)

    exposure = None
    if clusters:
        worst = clusters[0]
        exposure = {
            "detail": (f"{worst['pair'][0]} and {worst['pair'][1]} are {worst['combinedWeightPct']:.1f}% of the "
                       f"portfolio and move together at r={worst['r']:.2f} — closer to one position than two."),
            "level": worst["level"],
        }
    return {
        "available": True,
        "concentration": concentration,
        "clusters": clusters[:5],
        "overlap": exposure,
        "positions": len(weighted),
        "measuredPairs": measured_pairs,
        "basis": "hourly returns from the loaded market board over the last 7 days",
        "note": None if measured_pairs else "Not enough overlapping price history to measure how these holdings move together.",
        "confidence": market_intelligence.KNOWN if measured_pairs else market_intelligence.UNAVAILABLE,
    }
