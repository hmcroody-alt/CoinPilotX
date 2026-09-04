"""Market Intelligence — the reasoning layer under Market Pulse.

This module owns no networking, no cache, and no database access. It is given a
price series and a handful of already-fetched board numbers, and it returns a
structured opinion about them. That constraint is the whole design: it means the
same code can score fifty rows from one board response (the 7-day hourly series
CoinGecko already sends with `/coins/markets`) and, unchanged, score one open
asset from its deeper cached history. Nothing here can make a request, so
nothing here can turn a fifty-row list into fifty provider calls.

Three ideas run through every function below.

**Opportunity and Entry are different questions.** "Is this asset in a good
state?" and "is this a good moment to act?" have different answers far more
often than not — an asset in a clean uptrend that has just gone vertical is a
high-opportunity, low-entry situation, and collapsing the two into one "score"
is precisely how a screen ends up telling someone to buy the top. They are
computed from disjoint evidence and reported separately.

**Absence is not zero.** Every metric returns ``None`` when it cannot be
computed, and every consumer is expected to render that as unavailable. A
volatility of 0.0% and "we could not measure volatility" look identical once
they are both a number on a screen.

**Nothing is asserted that was not measured.** Every conclusion carries reasons,
and every reason is labelled KNOWN (measured from real data), INFERRED (derived
from a measurement by rule), or UNAVAILABLE (the input was missing). A verdict
whose reasons are mostly INFERRED is a weaker claim than one whose reasons are
KNOWN, and the labels are how a reader can tell without being told.

This is decision *support*. It never executes anything, and the vocabulary is
deliberately conditional — see ``BANNED_PHRASES``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

KNOWN = "KNOWN"
INFERRED = "INFERRED"
UNAVAILABLE = "UNAVAILABLE"

# The fifteen action states. A verdict is one of these or it is DATA_UNAVAILABLE
# — there is no sixteenth "probably fine".
STRONG_ACCUMULATION = "STRONG_ACCUMULATION"
ACCUMULATE = "ACCUMULATE"
HOLD = "HOLD"
WAIT = "WAIT"
WAIT_FOR_PULLBACK = "WAIT_FOR_PULLBACK"
WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
BREAKOUT_WATCH = "BREAKOUT_WATCH"
PULLBACK_WATCH = "PULLBACK_WATCH"
REVERSAL_WATCH = "REVERSAL_WATCH"
TAKE_PARTIAL_PROFIT = "TAKE_PARTIAL_PROFIT"
REDUCE = "REDUCE"
EXIT = "EXIT"
AVOID = "AVOID"
DO_NOT_CHASE = "DO_NOT_CHASE"
HIGH_RISK = "HIGH_RISK"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

ACTION_STATES = (
    STRONG_ACCUMULATION, ACCUMULATE, HOLD, WAIT, WAIT_FOR_PULLBACK,
    WAIT_FOR_CONFIRMATION, BREAKOUT_WATCH, PULLBACK_WATCH, REVERSAL_WATCH,
    TAKE_PARTIAL_PROFIT, REDUCE, EXIT, AVOID, DO_NOT_CHASE, HIGH_RISK,
)

ACTION_LABELS = {
    STRONG_ACCUMULATION: "Strong accumulation",
    ACCUMULATE: "Accumulate",
    HOLD: "Hold",
    WAIT: "Wait",
    WAIT_FOR_PULLBACK: "Wait for pullback",
    WAIT_FOR_CONFIRMATION: "Wait for confirmation",
    BREAKOUT_WATCH: "Breakout watch",
    PULLBACK_WATCH: "Pullback watch",
    REVERSAL_WATCH: "Reversal watch",
    TAKE_PARTIAL_PROFIT: "Take partial profit",
    REDUCE: "Reduce",
    EXIT: "Exit",
    AVOID: "Avoid",
    DO_NOT_CHASE: "Do not chase",
    HIGH_RISK: "High risk",
    DATA_UNAVAILABLE: "Data unavailable",
}

# Tone per state, so a client can colour a chip without re-deriving meaning.
ACTION_TONES = {
    STRONG_ACCUMULATION: "positive",
    ACCUMULATE: "positive",
    HOLD: "neutral",
    WAIT: "neutral",
    WAIT_FOR_PULLBACK: "caution",
    WAIT_FOR_CONFIRMATION: "neutral",
    BREAKOUT_WATCH: "watch",
    PULLBACK_WATCH: "watch",
    REVERSAL_WATCH: "watch",
    TAKE_PARTIAL_PROFIT: "caution",
    REDUCE: "caution",
    EXIT: "negative",
    AVOID: "negative",
    DO_NOT_CHASE: "caution",
    HIGH_RISK: "negative",
    DATA_UNAVAILABLE: "muted",
}

RISK_LOW = "LOW"
RISK_MODERATE = "MODERATE"
RISK_HIGH = "HIGH"
RISK_EXTREME = "EXTREME"
RISK_LEVELS = (RISK_LOW, RISK_MODERATE, RISK_HIGH, RISK_EXTREME)

# Phrases this product will not put on a screen, in any language of certainty.
# A market claim stated as a certainty is false regardless of how good the
# analysis behind it is, and one of these sentences is the difference between
# decision support and a promise. `assert_safe_language` is called on every
# assembled payload and the test suite asserts the same list.
BANNED_PHRASES = (
    "guaranteed buy",
    "guaranteed profit",
    "guaranteed return",
    "100% win",
    "100% profit",
    "certain sell",
    "certain buy",
    "risk free",
    "risk-free",
    "cannot lose",
    "sure thing",
    "will definitely",
)

# Timeframe rows. 5m and 15m are absent on purpose: the cheapest honest series
# available for the whole board is hourly, so a 5m row would either be invented
# or would cost a provider call per asset. An omitted row asks no questions; a
# greyed-out one invites "when will this work?".
#
# (key, label, bars of the hourly series each step spans, steps in the window)
TIMEFRAMES = (
    ("1h", "1H", 1, 6),
    ("4h", "4H", 4, 6),
    ("1d", "1D", 24, 3),
    # 168 hourly points is exactly what the 7-day sparkline carries, so the 1W
    # row is computable from a full board response and drops out cleanly when
    # the series is short rather than being computed from six days and called a
    # week.
    ("1w", "1W", 24, 7),
)

UP = "UP"
DOWN = "DOWN"
FLAT = "FLAT"

# Below this many hourly points there is not enough series to say anything about
# multi-day structure, and the honest answer is DATA_UNAVAILABLE.
MIN_POINTS_FOR_VERDICT = 24
# Below this the verdict stands but is marked limited, and the longer timeframes
# drop out rather than being computed from a handful of points.
MIN_POINTS_FOR_FULL_DEPTH = 120


# ---------------------------------------------------------------------------
# Small numeric helpers. All of them return None rather than a placeholder.
# ---------------------------------------------------------------------------

def _clean(series: Iterable[Any] | None) -> list[float]:
    out: list[float] = []
    for value in series or []:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            out.append(float(value))
    return out


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((v - average) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def _returns(series: Sequence[float]) -> list[float]:
    out = []
    for previous, current in zip(series, series[1:]):
        if previous:
            out.append((current - previous) / abs(previous))
    return out


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _reason(code: str, text: str, confidence: str) -> dict[str, Any]:
    return {"code": code, "text": text, "confidence": confidence}


# ---------------------------------------------------------------------------
# Structure: trend, alignment, extension, levels
# ---------------------------------------------------------------------------

def timeframe_trends(series: Sequence[float]) -> list[dict[str, Any]]:
    """Direction per timeframe, each measured over a stated window.

    Every row comes from the same real hourly closes — a 4H row is the last 24
    hours of that series read in four-hour steps, not a separate feed. The
    ``basis`` string travels with the row so the UI can say exactly what was
    measured instead of implying a provider timeframe that was never fetched.

    The up/down threshold is the series' own hourly volatility scaled to the
    window, so "up" means "moved more than this asset normally wanders in this
    much time" rather than a fixed percentage that calls every stablecoin flat
    and every microcap trending.
    """
    points = _clean(series)
    rows: list[dict[str, Any]] = []
    hourly_vol = _stdev(_returns(points)) if len(points) >= 8 else None
    for key, label, span, steps in TIMEFRAMES:
        # ``needed`` points span ``needed - 1`` hours from first to last, which
        # is why 1W asks for 168 rather than 169: the board's own series is 168
        # points and demanding one more would make the row permanently absent.
        needed = span * steps
        if len(points) < needed:
            rows.append({
                "key": key, "label": label, "direction": None, "changePct": None,
                "basis": f"needs {needed}h of history", "confidence": UNAVAILABLE,
            })
            continue
        window = points[-needed:]
        change = _pct(window[-1], window[0])
        hours = needed - 1
        if hourly_vol is not None and hourly_vol > 0:
            threshold = hourly_vol * math.sqrt(hours) * 100.0 * 0.5
        else:
            threshold = 0.5
        # A floor stops a dead-flat series from classifying rounding as a trend.
        threshold = max(threshold, 0.2)
        if change is None:
            direction = None
            confidence = UNAVAILABLE
        elif change > threshold:
            direction, confidence = UP, KNOWN
        elif change < -threshold:
            direction, confidence = DOWN, KNOWN
        else:
            direction, confidence = FLAT, KNOWN
        rows.append({
            "key": key,
            "label": label,
            "direction": direction,
            "changePct": _round(change),
            "thresholdPct": _round(threshold),
            "basis": f"last {hours}h of hourly closes",
            "confidence": confidence,
        })
    return rows


def trend_alignment(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How much the measurable timeframes agree, and on what.

    The percentage is over the timeframes that could actually be computed, and
    ``measured`` says how many that was. 100% agreement across two rows is a
    much weaker statement than 100% across four, and hiding the denominator
    would make them look the same.
    """
    measured = [r for r in rows if r.get("direction")]
    if not measured:
        return {"direction": None, "alignmentPct": None, "measured": 0,
                "total": len(TIMEFRAMES), "confidence": UNAVAILABLE}
    counts = {UP: 0, DOWN: 0, FLAT: 0}
    for row in measured:
        counts[row["direction"]] += 1
    direction = max(counts, key=lambda k: counts[k])
    if counts[direction] == 0:
        return {"direction": None, "alignmentPct": None, "measured": 0,
                "total": len(TIMEFRAMES), "confidence": UNAVAILABLE}
    return {
        "direction": direction,
        "alignmentPct": _round(counts[direction] / len(measured) * 100.0, 1),
        "measured": len(measured),
        "total": len(TIMEFRAMES),
        "agree": counts[direction],
        "confidence": KNOWN,
    }


def extension(series: Sequence[float], lookback: int = 72) -> dict[str, Any]:
    """How far price sits from its own recent mean, in that mean's own units.

    Percentage above an average is not comparable across assets — 8% above the
    3-day mean is ordinary for a microcap and remarkable for BTC. The z-score
    is, which is what makes "overextended" a claim worth putting next to a
    verdict rather than a number that means something different in every row.
    """
    points = _clean(series)
    if len(points) < 12:
        return {"zScore": None, "vsMeanPct": None, "state": None,
                "lookbackHours": None, "confidence": UNAVAILABLE}
    window = points[-min(lookback, len(points)):]
    average = _mean(window)
    deviation = _stdev(window)
    price = points[-1]
    vs_mean = _pct(price, average)
    if not deviation or average is None:
        return {"zScore": None, "vsMeanPct": _round(vs_mean), "state": None,
                "lookbackHours": len(window), "confidence": UNAVAILABLE}
    z = (price - average) / deviation
    if z >= 2.0:
        state = "STRETCHED_UP"
    elif z >= 1.2:
        state = "EXTENDED_UP"
    elif z <= -2.0:
        state = "STRETCHED_DOWN"
    elif z <= -1.2:
        state = "EXTENDED_DOWN"
    else:
        state = "IN_RANGE"
    return {"zScore": _round(z), "vsMeanPct": _round(vs_mean), "state": state,
            "lookbackHours": len(window), "confidence": KNOWN}


def _swings(points: Sequence[float], width: int = 3) -> tuple[list[float], list[float]]:
    highs, lows = [], []
    for index in range(width, len(points) - width):
        window = points[index - width:index + width + 1]
        value = points[index]
        if value == max(window):
            highs.append(value)
        if value == min(window):
            lows.append(value)
    return highs, lows


def levels(series: Sequence[float]) -> dict[str, Any]:
    """Nearest support below and resistance above, from swing pivots.

    These are pivots in a series of *closes*. The provider gives no OHLC, so
    there are no wicks here and no intraday extreme that never printed a close —
    which means these levels are real but conservative. The payload says so in
    ``basis`` rather than letting a client present them as exchange highs.
    """
    points = _clean(series)
    if len(points) < 24:
        return {"support": None, "resistance": None, "supportDistancePct": None,
                "resistanceDistancePct": None, "rangeHigh": None, "rangeLow": None,
                "basis": "closing prices only", "confidence": UNAVAILABLE}
    price = points[-1]
    highs, lows = _swings(points)
    below = [v for v in lows + [min(points)] if v < price]
    above = [v for v in highs + [max(points)] if v > price]
    support = max(below) if below else None
    resistance = min(above) if above else None
    return {
        "support": _round(support, 8),
        "resistance": _round(resistance, 8),
        "supportDistancePct": _round(_pct(price, support)) if support else None,
        "resistanceDistancePct": _round(_pct(resistance, price)) if resistance else None,
        "rangeHigh": _round(max(points), 8),
        "rangeLow": _round(min(points), 8),
        "positionInRangePct": _round(
            (price - min(points)) / (max(points) - min(points)) * 100.0, 1
        ) if max(points) > min(points) else None,
        # Named so nobody presents a close-derived pivot as a traded high.
        "basis": "swing pivots in 7d hourly closes (no intraday wicks)",
        "confidence": KNOWN,
    }


def volatility(series: Sequence[float]) -> dict[str, Any]:
    """Daily volatility from hourly returns, plus a band label."""
    points = _clean(series)
    returns = _returns(points)
    if len(returns) < 12:
        return {"dailyPct": None, "band": None, "confidence": UNAVAILABLE}
    hourly = _stdev(returns)
    if hourly is None:
        return {"dailyPct": None, "band": None, "confidence": UNAVAILABLE}
    daily = hourly * math.sqrt(24) * 100.0
    if daily < 2:
        band = RISK_LOW
    elif daily < 5:
        band = RISK_MODERATE
    elif daily < 10:
        band = RISK_HIGH
    else:
        band = RISK_EXTREME
    return {"dailyPct": _round(daily), "band": band, "confidence": KNOWN}


def liquidity(volume_24h: Any, market_cap: Any) -> dict[str, Any]:
    """Turnover as a stand-in for how easily a position can be left.

    Volume against market cap, not volume alone: $40m a day is deep for a $200m
    asset and thin for a $40bn one, and the absolute figure on its own would
    rank every large cap as liquid and every small one as not.
    """
    volume = _num(volume_24h)
    cap = _num(market_cap)
    if volume is None or cap is None or cap <= 0:
        return {"turnoverPct": None, "band": None, "volume24h": volume,
                "marketCap": cap, "confidence": UNAVAILABLE}
    turnover = volume / cap * 100.0
    if turnover >= 15:
        band = "HIGH"
    elif turnover >= 4:
        band = "MODERATE"
    else:
        band = "LOW"
    return {"turnoverPct": _round(turnover), "band": band, "volume24h": volume,
            "marketCap": cap, "confidence": KNOWN}


def relative_strength(change_24h: Any, board_median_change: Any,
                      benchmark_change: Any = None) -> dict[str, Any]:
    """This asset's 24h move against the board, and against BTC when known."""
    change = _num(change_24h)
    median = _num(board_median_change)
    benchmark = _num(benchmark_change)
    if change is None or median is None:
        return {"vsBoardPct": None, "vsBenchmarkPct": None, "state": None,
                "confidence": UNAVAILABLE}
    vs_board = change - median
    state = "OUTPERFORMING" if vs_board > 1 else "UNDERPERFORMING" if vs_board < -1 else "IN_LINE"
    return {
        "vsBoardPct": _round(vs_board),
        "vsBenchmarkPct": _round(change - benchmark) if benchmark is not None else None,
        "state": state,
        "confidence": KNOWN,
    }


def volume_anomaly(volume_series: Sequence[Any] | None, volume_24h: Any = None) -> dict[str, Any]:
    """Is current turnover unusual against this asset's own recent readings?

    Requires a persisted volume history (``market_observations``); the board
    carries one volume figure and one figure cannot be unusual. When there is no
    history the answer is UNAVAILABLE — not "normal", which would be a claim.

    An anomaly is a question, not a verdict: unusual volume precedes moves in
    both directions, so this feeds attention, never an action state on its own.
    """
    history = _clean(volume_series)
    current = _num(volume_24h)
    if current is None and history:
        current = history[-1]
    baseline = history[:-1] if len(history) > 1 else []
    if current is None or len(baseline) < 4:
        return {"ratio": None, "state": None, "samples": len(baseline),
                "confidence": UNAVAILABLE,
                "note": "Needs several recorded volume readings before unusual can mean anything."}
    ordered = sorted(baseline)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    if not median:
        return {"ratio": None, "state": None, "samples": len(baseline), "confidence": UNAVAILABLE}
    ratio = current / median
    if ratio >= 2.0:
        state = "SURGING"
    elif ratio >= 1.4:
        state = "EXPANDING"
    elif ratio <= 0.6:
        state = "CONTRACTING"
    else:
        state = "NORMAL"
    return {"ratio": _round(ratio), "state": state, "samples": len(baseline),
            "median": _round(median, 2), "confidence": KNOWN}


# ---------------------------------------------------------------------------
# Scores — deliberately two of them
# ---------------------------------------------------------------------------

def opportunity_quality(alignment: dict, strength: dict, liquidity_info: dict,
                        volumes: dict, regime: dict | None) -> dict[str, Any]:
    """Is this asset in a favourable *state*? Says nothing about timing.

    Structure, relative strength, liquidity, participation and market regime.
    Extension is pointedly excluded — an asset that has run hard is in a *better*
    state, not a worse one, and it is the entry score's job to notice that this
    makes it a worse moment.
    """
    score = 50.0
    reasons: list[dict[str, Any]] = []
    components: list[str] = []

    direction = alignment.get("direction")
    pct = _num(alignment.get("alignmentPct"))
    if direction and pct is not None:
        components.append("alignment")
        if direction == UP:
            score += (pct - 50) * 0.5
            reasons.append(_reason(
                "alignment_up",
                f"{alignment.get('agree')} of {alignment.get('measured')} measurable timeframes are trending up ({pct:.0f}% aligned).",
                KNOWN))
        elif direction == DOWN:
            score -= (pct - 50) * 0.5
            reasons.append(_reason(
                "alignment_down",
                f"{alignment.get('agree')} of {alignment.get('measured')} measurable timeframes are trending down ({pct:.0f}% aligned).",
                KNOWN))
        else:
            reasons.append(_reason("alignment_flat", "Timeframes are mostly flat — no directional edge either way.", KNOWN))
    else:
        reasons.append(_reason("alignment_missing", "Trend alignment could not be measured from the available history.", UNAVAILABLE))

    state = strength.get("state")
    if state:
        components.append("relative_strength")
        vs_board = _num(strength.get("vsBoardPct")) or 0.0
        score += max(-12.0, min(12.0, vs_board * 1.5))
        reasons.append(_reason(
            "relative_strength",
            f"24h move is {abs(vs_board):.1f} points {'ahead of' if vs_board >= 0 else 'behind'} the board median.",
            KNOWN))
    else:
        reasons.append(_reason("relative_strength_missing", "Relative strength needs a 24h change for this asset and the board.", UNAVAILABLE))

    band = liquidity_info.get("band")
    if band:
        components.append("liquidity")
        score += {"HIGH": 6.0, "MODERATE": 0.0, "LOW": -10.0}.get(band, 0.0)
        reasons.append(_reason(
            "liquidity",
            f"Turnover is {liquidity_info.get('turnoverPct')}% of market cap in 24h ({band.lower()}).",
            KNOWN))
    else:
        reasons.append(_reason("liquidity_missing", "Turnover needs both 24h volume and market cap.", UNAVAILABLE))

    vol_state = volumes.get("state")
    if vol_state in {"SURGING", "EXPANDING"}:
        components.append("participation")
        score += 6.0 if vol_state == "SURGING" else 3.0
        reasons.append(_reason("volume_expanding", f"Recorded volume is {volumes.get('ratio')}x its recent median — participation is rising.", KNOWN))
    elif vol_state == "CONTRACTING":
        components.append("participation")
        score -= 4.0
        reasons.append(_reason("volume_contracting", f"Recorded volume is {volumes.get('ratio')}x its recent median — participation is thinning.", KNOWN))
    elif vol_state is None:
        reasons.append(_reason("volume_missing", "No stored volume history yet, so participation is not measurable for this asset.", UNAVAILABLE))

    if regime and regime.get("state"):
        components.append("regime")
        score += {"RISK_ON": 5.0, "NEUTRAL": 0.0, "RISK_OFF": -7.0}.get(regime["state"], 0.0)
        reasons.append(_reason("regime", f"Market regime reads {regime.get('label') or regime['state']}.", INFERRED))

    return {
        "score": int(round(_clamp(score))),
        "band": _band(score),
        "reasons": reasons,
        "components": components,
        "confidence": KNOWN if len(components) >= 3 else INFERRED if components else UNAVAILABLE,
    }


def entry_quality(extension_info: dict, level_info: dict, volatility_info: dict,
                  series: Sequence[float]) -> dict[str, Any]:
    """Is *now* a reasonable moment, given where price sits in its own range?

    Low here does not mean bearish. It means the asset has already moved, or is
    pressed against resistance, or is moving so fast that any level chosen now
    will be wrong in an hour. This is the score that separates "good asset" from
    "good moment", and it is the reason a strong uptrend can legitimately
    produce WAIT.
    """
    score = 55.0
    reasons: list[dict[str, Any]] = []
    components: list[str] = []

    ext_state = extension_info.get("state")
    z = _num(extension_info.get("zScore"))
    if ext_state and z is not None:
        components.append("extension")
        if ext_state == "STRETCHED_UP":
            score -= 30
            reasons.append(_reason("stretched_up", f"Price is {z:.1f} standard deviations above its 3-day mean — a poor place to start a position.", KNOWN))
        elif ext_state == "EXTENDED_UP":
            score -= 16
            reasons.append(_reason("extended_up", f"Price is {z:.1f} standard deviations above its 3-day mean.", KNOWN))
        elif ext_state == "EXTENDED_DOWN":
            score += 10
            reasons.append(_reason("extended_down", f"Price is {abs(z):.1f} standard deviations below its 3-day mean.", KNOWN))
        elif ext_state == "STRETCHED_DOWN":
            score += 6
            reasons.append(_reason("stretched_down", f"Price is {abs(z):.1f} standard deviations below its 3-day mean — cheap relative to itself, but falling.", KNOWN))
        else:
            score += 5
            reasons.append(_reason("in_range", "Price is inside its normal 3-day range.", KNOWN))
    else:
        reasons.append(_reason("extension_missing", "Extension needs at least 12 hours of history.", UNAVAILABLE))

    support_distance = _num(level_info.get("supportDistancePct"))
    resistance_distance = _num(level_info.get("resistanceDistancePct"))
    if support_distance is not None:
        components.append("support")
        if support_distance <= 2:
            score += 14
            reasons.append(_reason("near_support", f"Price is {support_distance:.1f}% above the nearest support pivot — invalidation is close, so risk per unit is small.", KNOWN))
        elif support_distance <= 6:
            score += 6
            reasons.append(_reason("above_support", f"Price is {support_distance:.1f}% above the nearest support pivot.", KNOWN))
        else:
            score -= 8
            reasons.append(_reason("far_from_support", f"Price is {support_distance:.1f}% above the nearest support pivot — any stop is a long way down.", KNOWN))
    if resistance_distance is not None:
        components.append("resistance")
        if resistance_distance <= 1.5:
            score -= 12
            reasons.append(_reason("under_resistance", f"Resistance sits {resistance_distance:.1f}% overhead — little room before the first obstacle.", KNOWN))
        elif resistance_distance >= 8:
            score += 6
            reasons.append(_reason("room_above", f"Nearest resistance is {resistance_distance:.1f}% above.", KNOWN))

    band = volatility_info.get("band")
    if band:
        components.append("volatility")
        score += {RISK_LOW: 6.0, RISK_MODERATE: 2.0, RISK_HIGH: -6.0, RISK_EXTREME: -14.0}.get(band, 0.0)
        reasons.append(_reason("volatility", f"Daily volatility is about {volatility_info.get('dailyPct')}% ({band.lower()}).", KNOWN))

    points = _clean(series)
    if len(points) >= 7:
        components.append("recent_move")
        recent = _pct(points[-1], points[-7])
        if recent is not None and recent >= 8:
            score -= 12
            reasons.append(_reason("vertical_move", f"Up {recent:.1f}% in the last six hours — chasing this is paying for someone else's entry.", KNOWN))
        elif recent is not None and recent <= -8:
            score -= 6
            reasons.append(_reason("falling_fast", f"Down {abs(recent):.1f}% in the last six hours — a falling market has no reliable level yet.", KNOWN))

    return {
        "score": int(round(_clamp(score))),
        "band": _band(score),
        "reasons": reasons,
        "components": components,
        "confidence": KNOWN if len(components) >= 3 else INFERRED if components else UNAVAILABLE,
    }


def _band(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 55:
        return "ELEVATED"
    if score >= 40:
        return "MODERATE"
    return "LOW"


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

def risk_profile(volatility_info: dict, liquidity_info: dict, extension_info: dict,
                 level_info: dict, correlation: dict | None = None,
                 event_risk: dict | None = None) -> dict[str, Any]:
    """One risk surface plus the factors that produced it.

    The surface is the worst honest reading among the factors rather than their
    average: a highly liquid asset in a calm market that is 3 standard
    deviations extended is not "moderate risk overall", and averaging is how
    that gets said.
    """
    factors: list[dict[str, Any]] = []

    band = volatility_info.get("band")
    factors.append({
        "key": "volatility", "label": "Volatility",
        "level": band, "confidence": volatility_info.get("confidence"),
        "detail": f"{volatility_info.get('dailyPct')}% typical daily move" if band else "Not measurable from the available history.",
    })

    liq_band = liquidity_info.get("band")
    liq_level = {"HIGH": RISK_LOW, "MODERATE": RISK_MODERATE, "LOW": RISK_HIGH}.get(liq_band)
    factors.append({
        "key": "liquidity", "label": "Liquidity",
        "level": liq_level, "confidence": liquidity_info.get("confidence"),
        "detail": f"{liquidity_info.get('turnoverPct')}% of market cap traded in 24h" if liq_band else "Needs 24h volume and market cap.",
    })

    ext_state = extension_info.get("state")
    ext_level = {
        "STRETCHED_UP": RISK_HIGH, "EXTENDED_UP": RISK_MODERATE, "IN_RANGE": RISK_LOW,
        "EXTENDED_DOWN": RISK_MODERATE, "STRETCHED_DOWN": RISK_HIGH,
    }.get(ext_state)
    factors.append({
        "key": "extension", "label": "Extension",
        "level": ext_level, "confidence": extension_info.get("confidence"),
        "detail": f"{extension_info.get('zScore')} sd from the 3-day mean" if ext_state else "Needs at least 12 hours of history.",
    })

    support_distance = _num(level_info.get("supportDistancePct"))
    if support_distance is None:
        support_level, support_detail = None, "No support pivot resolved from closing prices."
    elif support_distance <= 3:
        support_level, support_detail = RISK_LOW, f"Support {support_distance:.1f}% below — a tight invalidation."
    elif support_distance <= 10:
        support_level, support_detail = RISK_MODERATE, f"Support {support_distance:.1f}% below."
    else:
        support_level, support_detail = RISK_HIGH, f"Nearest support is {support_distance:.1f}% below — a wide invalidation."
    factors.append({"key": "support_distance", "label": "Distance to support",
                    "level": support_level, "confidence": level_info.get("confidence"),
                    "detail": support_detail})

    if correlation and correlation.get("level"):
        factors.append({"key": "correlation", "label": "Correlation",
                        "level": correlation.get("level"), "confidence": correlation.get("confidence", INFERRED),
                        "detail": correlation.get("detail") or ""})
    else:
        factors.append({"key": "correlation", "label": "Correlation",
                        "level": None, "confidence": UNAVAILABLE,
                        "detail": "Correlation needs portfolio holdings to compare against."})

    # Event risk — scheduled unlocks, listings, hard forks — is not in any feed
    # this product subscribes to. Saying "no events" would be a claim we cannot
    # support, so the row exists and reports that it is unmeasured.
    factors.append({
        "key": "event_risk", "label": "Event risk",
        "level": (event_risk or {}).get("level"),
        "confidence": (event_risk or {}).get("confidence", UNAVAILABLE),
        "detail": (event_risk or {}).get("detail") or "No scheduled-event feed is connected, so this is unmeasured rather than clear.",
    })

    measured = [f["level"] for f in factors if f.get("level") in RISK_LEVELS]

    # A surface needs at least one reading taken from how price has actually
    # behaved. Liquidity, correlation and event risk are all computable without a
    # single historical price, so on an asset with no history the worst-of rule
    # would reduce to "it trades in size, therefore LOW" — publishing a
    # reassuring risk level for an asset the engine has just declared it cannot
    # analyse. Deep liquidity is not an argument that something is safe; it is an
    # argument that you can get out, which is a different claim.
    PRICE_DERIVED = {"volatility", "extension", "support_distance"}
    grounded = any(
        f.get("key") in PRICE_DERIVED and f.get("level") in RISK_LEVELS for f in factors
    )

    if not measured or not grounded:
        surface = None
    else:
        surface = max(measured, key=lambda level: RISK_LEVELS.index(level))
        # Two independently high factors is a different situation from one.
        if measured.count(RISK_HIGH) >= 2 and surface == RISK_HIGH:
            surface = RISK_EXTREME
    return {
        "level": surface,
        "factors": factors,
        "measuredFactors": len(measured),
        "confidence": (
            KNOWN if surface and len(measured) >= 3
            else INFERRED if surface
            else UNAVAILABLE
        ),
    }


# ---------------------------------------------------------------------------
# Setups
# ---------------------------------------------------------------------------

def detect_setup(series: Sequence[float], level_info: dict, alignment: dict,
                 extension_info: dict, volatility_info: dict,
                 volumes: dict | None = None) -> dict[str, Any]:
    """The structure price is currently in, with its own trigger and invalidation.

    Every field is conditional and level-based: a trigger that has not happened,
    a zone to act in *if* it does, a price that would prove the idea wrong, and
    two targets. Nothing here predicts. A setup with no trigger is not a setup,
    so an unclear structure returns type ``None`` rather than the nearest match.
    """
    points = _clean(series)
    price = points[-1] if points else None
    support = _num(level_info.get("support"))
    resistance = _num(level_info.get("resistance"))
    support_distance = _num(level_info.get("supportDistancePct"))
    resistance_distance = _num(level_info.get("resistanceDistancePct"))
    direction = alignment.get("direction")
    ext_state = extension_info.get("state")
    daily_vol = _num(volatility_info.get("dailyPct"))

    empty = {
        "type": None, "label": None, "status": None, "trigger": None,
        "entryZone": None, "invalidation": None, "target1": None, "target2": None,
        "riskReward": None, "confidence": UNAVAILABLE,
        "note": "No clear structure in the available history — no trigger, so no setup.",
    }
    if price is None or len(points) < MIN_POINTS_FOR_VERDICT:
        return empty

    def build(kind, label, status, trigger, zone, invalidation, target1, target2, note, confidence=KNOWN):
        rr = None
        if zone and invalidation and target1:
            entry = sum(zone) / 2.0
            risk = abs(entry - invalidation)
            reward = abs(target1 - entry)
            if risk > 0:
                rr = _round(reward / risk, 2)
        return {
            "type": kind, "label": label, "status": status, "trigger": trigger,
            "entryZone": [_round(zone[0], 8), _round(zone[1], 8)] if zone else None,
            "invalidation": _round(invalidation, 8) if invalidation else None,
            "target1": _round(target1, 8) if target1 else None,
            "target2": _round(target2, 8) if target2 else None,
            "riskReward": rr, "note": note, "confidence": confidence,
        }

    # A volatility-scaled buffer, so a "close above" is a real break rather than
    # a tick through the level.
    buffer = (daily_vol or 3.0) / 100.0 * 0.25
    # Targets are measured in this asset's own daily range, not in fixed
    # percentages. A 3% target is a day's work for one asset and a month's for
    # another, and a fixed number would make every reward:risk figure on the
    # board incomparable with every other.
    step = max((daily_vol or 3.0) / 100.0, 0.015)

    if resistance and resistance_distance is not None and resistance_distance <= 3 and direction in {UP, FLAT}:
        return build(
            "breakout", "Breakout", "PENDING",
            f"An hourly close above {_round(resistance, 8)}",
            (resistance, resistance * (1 + buffer)),
            support if support and _pct(resistance, support) and _pct(resistance, support) < 12 else resistance * (1 - 2 * step),
            resistance * (1 + 2 * step),
            resistance * (1 + 4 * step),
            "Price is pressed against resistance. Nothing has broken yet — this becomes actionable only if it closes above the level.",
        )

    if support and support_distance is not None and support_distance <= 2.5 and direction == UP:
        return build(
            "pullback", "Pullback to support", "READY",
            f"Price holding above {_round(support, 8)} on the next hourly close",
            (support, support * (1 + max(buffer, 0.005))),
            support * (1 - 1.5 * step),
            resistance if resistance else price * (1 + 2 * step),
            resistance * (1 + 2 * step) if resistance else price * (1 + 4 * step),
            "Higher timeframes point up and price has come back to a level it previously held. The level holding is the condition; losing it is the answer.",
        )

    if resistance and support and direction == UP and ext_state in {"IN_RANGE", "EXTENDED_DOWN"}:
        return build(
            "trend_continuation", "Trend continuation", "WATCHING",
            f"Continuation while price stays above {_round(support, 8)}",
            (price * (1 - buffer), price * (1 + buffer)),
            support,
            resistance,
            resistance * 1.06,
            "Trend is intact and price is not stretched. This is a continuation idea, not a fresh signal — it fails the moment support goes.",
        )

    if direction == DOWN and ext_state == "STRETCHED_DOWN" and support:
        return build(
            "reversal", "Reversal watch", "UNCONFIRMED",
            f"Two consecutive hourly closes back above {_round(support * (1 + buffer), 8)}",
            (support, support * (1 + 2 * buffer)),
            support * (1 - 2 * step),
            price * (1 + 2 * step),
            price * (1 + 4 * step),
            "Price is stretched below its mean in a downtrend. Nothing has turned — a reversal is a possibility being watched, not a state that has occurred.",
            INFERRED,
        )

    if support and resistance and direction == FLAT:
        position = _num(level_info.get("positionInRangePct"))
        if position is not None and position <= 30:
            return build(
                "range_support", "Range — at support", "READY",
                f"Price holding the {_round(support, 8)} range floor",
                (support, support * (1 + 2 * buffer)),
                support * (1 - 1.5 * step), resistance, resistance,
                "Price is oscillating in a range and sits near the floor. Range ideas fail when the range does.",
            )
        if position is not None and position >= 70:
            return build(
                "range_resistance", "Range — at resistance", "CAUTION",
                f"Rejection from {_round(resistance, 8)}, or a close above it",
                None, resistance * (1 + 2 * buffer), support, support,
                "Price sits near the range ceiling. This is the unfavourable end of a range for a new long, whichever way it resolves.",
            )

    return empty


# ---------------------------------------------------------------------------
# Anomaly / early detection
# ---------------------------------------------------------------------------

def anomaly_scan(volumes: dict, extension_info: dict, strength: dict,
                 alignment: dict) -> dict[str, Any]:
    """Things worth *looking* at early, stated as questions rather than signals.

    ANOMALY DOES NOT EQUAL BUY, and the wording here carries that: every entry
    describes what was observed, never what to do about it. Unusual volume with
    no price confirmation is one of the most common ways to end up early and
    wrong, so nothing in this function can move an action state.
    """
    findings: list[dict[str, Any]] = []
    vol_state = volumes.get("state")
    if vol_state == "SURGING":
        findings.append({
            "key": "volume_surge", "label": "Volume surge",
            "detail": f"Turnover is {volumes.get('ratio')}x its recent median. Direction is not implied — surges precede moves both ways.",
            "confidence": KNOWN,
        })
    elif vol_state == "EXPANDING":
        findings.append({
            "key": "volume_expansion", "label": "Volume expanding",
            "detail": f"Turnover is {volumes.get('ratio')}x its recent median.",
            "confidence": KNOWN,
        })
    if strength.get("state") == "OUTPERFORMING" and alignment.get("direction") != UP:
        findings.append({
            "key": "quiet_strength", "label": "Outperforming without trend",
            "detail": "This asset is beating the board over 24h while its own timeframes have not aligned. Early, or noise — the timeframes have not confirmed either way.",
            "confidence": INFERRED,
        })
    if extension_info.get("state") == "STRETCHED_DOWN" and strength.get("state") == "OUTPERFORMING":
        findings.append({
            "key": "washout", "label": "Stretched down but outperforming",
            "detail": "Price is well below its own mean while holding up against the board. Worth watching for a base; not evidence one has formed.",
            "confidence": INFERRED,
        })
    return {
        "findings": findings,
        # Repeated in the payload so a client cannot render this section as
        # opportunities without also carrying the caveat.
        "caveat": "An anomaly is a reason to look, not a reason to act.",
        "confidence": KNOWN if findings else volumes.get("confidence", UNAVAILABLE),
    }


# ---------------------------------------------------------------------------
# Market regime and breadth — free from the board that was already fetched
# ---------------------------------------------------------------------------

def market_regime(changes: Sequence[Any], global_metrics: dict | None = None) -> dict[str, Any]:
    """One compact read on conditions, from breadth the board already contains.

    Advancers against decliners across the fifty rows the product already has.
    No new provider call, no paid breadth feed — the data was in the response
    that drew the list.
    """
    values = [c for c in (_num(c) for c in changes or []) if c is not None]
    if len(values) < 5:
        return {"state": None, "label": None, "advancers": None, "decliners": None,
                "breadthPct": None, "confidence": UNAVAILABLE,
                "detail": "Breadth needs a loaded market board."}
    advancers = len([v for v in values if v > 0])
    decliners = len([v for v in values if v < 0])
    breadth = advancers / len(values) * 100.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2

    if breadth >= 65 and median > 0:
        state, label = "RISK_ON", "Risk-on"
    elif breadth <= 35 and median < 0:
        state, label = "RISK_OFF", "Risk-off"
    else:
        state, label = "NEUTRAL", "Mixed"

    detail = (f"{advancers} of {len(values)} assets on the board are up over 24h "
              f"(median {median:+.1f}%).")
    dominance = _num((global_metrics or {}).get("btcDominance"))
    if dominance is not None:
        detail += f" BTC dominance {dominance:.1f}%."
    return {
        "state": state, "label": label, "advancers": advancers, "decliners": decliners,
        "breadthPct": _round(breadth, 1), "medianChangePct": _round(median),
        "btcDominance": _round(dominance, 2) if dominance is not None else None,
        "detail": detail,
        "basis": "24h change across the loaded market board",
        "confidence": KNOWN,
    }


def rotation(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Where strength sits by size band — large, mid, small.

    Rank buckets, because that is the only grouping the board can support
    honestly. There is no sector taxonomy in this data, and inventing one would
    put a "DeFi is leading" claim on screen that no field in the response backs.
    """
    buckets = {"large": [], "mid": [], "small": []}
    for row in rows or []:
        change = _num(row.get("change24h") if "change24h" in row else row.get("change_24h"))
        rank = row.get("rank") if "rank" in row else row.get("market_cap_rank")
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            continue
        if change is None:
            continue
        key = "large" if rank <= 10 else "mid" if rank <= 50 else "small"
        buckets[key].append(change)
    groups = []
    for key, values in buckets.items():
        if len(values) < 3:
            groups.append({"key": key, "avgChangePct": None, "count": len(values), "confidence": UNAVAILABLE})
            continue
        groups.append({"key": key, "avgChangePct": _round(_mean(values)), "count": len(values), "confidence": KNOWN})
    measured = [g for g in groups if g.get("avgChangePct") is not None]
    leader = max(measured, key=lambda g: g["avgChangePct"])["key"] if measured else None
    return {
        "groups": groups, "leader": leader,
        "basis": "market-cap rank bands from the loaded board (no sector data available)",
        "confidence": KNOWN if len(measured) >= 2 else UNAVAILABLE,
    }


# ---------------------------------------------------------------------------
# Action state
# ---------------------------------------------------------------------------

def decide_action(opportunity: dict, entry: dict, risk: dict, setup: dict,
                  alignment: dict, extension_info: dict,
                  holding: dict | None = None) -> dict[str, Any]:
    """The one word on the card, and why it is that word.

    Holder and non-holder get different verdicts from identical market data,
    because they are answering different questions: a holder asks what to do
    with an existing position, a non-holder asks whether to start one. The same
    extended uptrend is HOLD or TAKE PARTIAL PROFIT for one and DO NOT CHASE for
    the other, and both are right.

    ``holding`` must come from real portfolio data. Absent it, this returns the
    non-holder branch and says so — it never guesses whether someone owns
    something.
    """
    reasons: list[dict[str, Any]] = []
    opp = _num(opportunity.get("score"))
    ent = _num(entry.get("score"))
    risk_level = risk.get("level")
    direction = alignment.get("direction")
    alignment_pct = _num(alignment.get("alignmentPct"))
    ext_state = extension_info.get("state")
    setup_type = setup.get("type")
    setup_status = setup.get("status")

    if opp is None or ent is None or opportunity.get("confidence") == UNAVAILABLE:
        return {
            "state": DATA_UNAVAILABLE, "label": ACTION_LABELS[DATA_UNAVAILABLE],
            "tone": ACTION_TONES[DATA_UNAVAILABLE], "perspective": "unknown",
            "reasons": [_reason("no_data", "There is not enough market history for this asset to support any verdict.", UNAVAILABLE)],
            "confidence": UNAVAILABLE,
        }

    holds = bool(holding and _num(holding.get("quantity")))
    perspective = "holder" if holds else "non_holder"

    if risk_level == RISK_EXTREME:
        state = HIGH_RISK
        reasons.append(_reason("extreme_risk", "Risk reads extreme on more than one independent factor, which outranks anything the trend is doing.", KNOWN))
        return _action(state, perspective, reasons, KNOWN)

    if holds:
        unrealized = _num(holding.get("unrealizedPnlPct"))
        if opp < 30:
            state = EXIT
            reasons.append(_reason("state_broken", f"Opportunity quality has fallen to {opp:.0f} — the conditions that supported holding are no longer present.", KNOWN))
        elif opp < 45:
            state = REDUCE
            reasons.append(_reason("state_weakening", f"Opportunity quality is {opp:.0f} and weakening.", KNOWN))
        elif unrealized is not None and unrealized >= 40 and ext_state in {"EXTENDED_UP", "STRETCHED_UP"}:
            state = TAKE_PARTIAL_PROFIT
            reasons.append(_reason("gains_and_extended", f"Position is up {unrealized:.0f}% and price is extended above its 3-day mean — taking part of that off changes the question from timing to sizing.", KNOWN))
        elif opp >= 60 and ent >= 65:
            state = ACCUMULATE
            reasons.append(_reason("adding_conditions", f"State is strong ({opp:.0f}) and the current level is reasonable ({ent:.0f}) — the same conditions that would justify a new position.", KNOWN))
        else:
            state = HOLD
            reasons.append(_reason("thesis_intact", f"Opportunity quality is {opp:.0f} with nothing in the risk factors demanding action.", KNOWN))
        if unrealized is None and holding:
            reasons.append(_reason("no_cost_basis", "No cost basis recorded for this holding, so profit-taking logic could not be applied.", UNAVAILABLE))
        return _action(state, perspective, reasons, KNOWN)

    # Non-holder branch.
    if opp >= 60 and ext_state in {"STRETCHED_UP"}:
        state = DO_NOT_CHASE
        reasons.append(_reason("strong_but_extended", f"The asset's state is strong ({opp:.0f}) but price is stretched well above its own mean — a good asset at a poor price.", KNOWN))
    elif opp >= 60 and ent < 40:
        state = WAIT_FOR_PULLBACK
        reasons.append(_reason("strong_poor_entry", f"Opportunity is {opp:.0f} but entry quality is only {ent:.0f}. The asset is not the problem; the level is.", KNOWN))
    elif opp >= 70 and ent >= 65 and risk_level in {RISK_LOW, RISK_MODERATE, None}:
        state = STRONG_ACCUMULATION
        reasons.append(_reason("aligned_and_priced", f"Opportunity {opp:.0f} and entry {ent:.0f} agree, with risk reading {str(risk_level).lower() if risk_level else 'unmeasured'}.", KNOWN))
    elif opp >= 58 and ent >= 55:
        state = ACCUMULATE
        reasons.append(_reason("favourable", f"Opportunity {opp:.0f} and entry {ent:.0f} are both favourable.", KNOWN))
    elif setup_type == "breakout" and setup_status == "PENDING":
        state = BREAKOUT_WATCH
        reasons.append(_reason("breakout_pending", "Price is at resistance with no break yet — the trigger, not the position, is what to watch.", KNOWN))
    elif setup_type in {"pullback", "range_support"}:
        state = PULLBACK_WATCH
        reasons.append(_reason("at_support", "Price has come back to a level it previously held; the level holding is the condition.", KNOWN))
    elif setup_type == "reversal":
        state = REVERSAL_WATCH
        reasons.append(_reason("possible_turn", "Price is stretched below its mean in a downtrend. Nothing has turned yet.", INFERRED))
    elif opp < 35 and direction == DOWN:
        state = AVOID
        reasons.append(_reason("weak_and_falling", f"Opportunity quality is {opp:.0f} with timeframes aligned down.", KNOWN))
    elif alignment_pct is not None and alignment_pct < 60:
        state = WAIT_FOR_CONFIRMATION
        reasons.append(_reason("timeframes_disagree", f"Only {alignment_pct:.0f}% of measurable timeframes agree — the timeframes are arguing with each other.", KNOWN))
    else:
        state = WAIT
        reasons.append(_reason("nothing_compelling", f"Nothing in the current readings (opportunity {opp:.0f}, entry {ent:.0f}) argues for acting now.", KNOWN))

    if risk_level == RISK_HIGH and state in {STRONG_ACCUMULATION, ACCUMULATE}:
        # Risk does not veto a good setup, but it does downgrade the language.
        state = ACCUMULATE if state == STRONG_ACCUMULATION else state
        reasons.append(_reason("risk_high", "Risk reads high, which caps how strongly this can be stated.", KNOWN))
    return _action(state, perspective, reasons, KNOWN)


def _action(state: str, perspective: str, reasons: list, confidence: str) -> dict[str, Any]:
    return {
        "state": state,
        "label": ACTION_LABELS.get(state, state),
        "tone": ACTION_TONES.get(state, "neutral"),
        "perspective": perspective,
        "reasons": reasons,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Position sizing — decision support only
# ---------------------------------------------------------------------------

def position_sizing(price: Any, setup: dict, risk: dict,
                    portfolio_value: Any = None,
                    risk_budget_pct: float = 1.0) -> dict[str, Any]:
    """What a stated risk budget implies about size, given the invalidation.

    Arithmetic, not advice: if the invalidation is 6% away and someone is
    willing to lose 1% of a portfolio on being wrong, the position is 1/6 of
    that portfolio. It cannot place an order and does not know the user's
    circumstances, which is why the caveat travels inside the payload.
    """
    entry = _num(price)
    invalidation = _num(setup.get("invalidation"))
    caveat = ("Arithmetic from a risk budget you set, not a recommendation. "
              "This never places an order.")
    if entry is None or invalidation is None or entry <= 0 or invalidation >= entry:
        return {"available": False, "riskPerUnitPct": None, "suggestedAllocationPct": None,
                "portfolioValue": _num(portfolio_value), "riskBudgetPct": risk_budget_pct,
                "caveat": caveat, "confidence": UNAVAILABLE,
                "note": "Sizing needs an entry above a defined invalidation level."}
    risk_per_unit = (entry - invalidation) / entry * 100.0
    if risk_per_unit <= 0:
        return {"available": False, "riskPerUnitPct": None, "suggestedAllocationPct": None,
                "portfolioValue": _num(portfolio_value), "riskBudgetPct": risk_budget_pct,
                "caveat": caveat, "confidence": UNAVAILABLE}
    allocation = min(100.0, risk_budget_pct / risk_per_unit * 100.0)
    if risk.get("level") == RISK_EXTREME:
        allocation *= 0.5
    elif risk.get("level") == RISK_HIGH:
        allocation *= 0.75
    value = _num(portfolio_value)
    return {
        "available": True,
        "riskPerUnitPct": _round(risk_per_unit),
        "suggestedAllocationPct": _round(allocation),
        "suggestedAmount": _round(value * allocation / 100.0, 2) if value else None,
        "portfolioValue": value,
        "riskBudgetPct": risk_budget_pct,
        "invalidation": _round(invalidation, 8),
        "riskAdjusted": risk.get("level") in {RISK_HIGH, RISK_EXTREME},
        "caveat": caveat,
        "confidence": KNOWN,
    }


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def assert_safe_language(payload: Any) -> None:
    """Raise if any string anywhere in a payload promises a certainty.

    Called on every assembled result rather than trusted to review, because the
    failure mode is a single template string added months from now, in a file
    nobody re-reads, that turns decision support into a guarantee.
    """
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            lowered = node.lower()
            for phrase in BANNED_PHRASES:
                if phrase in lowered:
                    raise ValueError(f"Prohibited certainty language in market intelligence: {phrase!r}")
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def data_quality(series: Sequence[float], volumes: dict) -> dict[str, Any]:
    points = _clean(series)
    if len(points) < MIN_POINTS_FOR_VERDICT:
        level = "INSUFFICIENT"
    elif len(points) < MIN_POINTS_FOR_FULL_DEPTH:
        level = "LIMITED"
    else:
        level = "FULL"
    return {
        "level": level,
        "pricePoints": len(points),
        "priceBasis": "hourly closing prices (no OHLC available from the provider)",
        "volumeHistory": volumes.get("confidence") == KNOWN,
        "note": {
            "INSUFFICIENT": "Not enough price history to form a view.",
            "LIMITED": "Short history — longer timeframes are omitted rather than estimated.",
            "FULL": "Full 7-day hourly history available.",
        }[level],
    }


def assess(asset: dict[str, Any], series: Sequence[float] | None = None,
           context: dict[str, Any] | None = None,
           volume_series: Sequence[Any] | None = None,
           holding: dict[str, Any] | None = None,
           depth: str = "list") -> dict[str, Any]:
    """The whole opinion for one asset, at the requested depth.

    ``depth="list"`` returns what a row needs — action, the two scores, the risk
    surface, and a short reason list — and is cheap enough to run for every row
    of a fifty-asset board from data already in hand. ``depth="full"`` adds
    timeframes, levels, setup, the full risk breakdown, anomalies and evidence
    for the single asset a user has opened.

    No branch of this function performs I/O, which is the property that makes
    Stage 17 hold: the list depth cannot become fifty requests because it cannot
    make one.
    """
    context = context or {}
    prices = _clean(series if series is not None else asset.get("sparkline"))
    price = _num(asset.get("price"))
    if price is not None and prices and prices[-1] != price:
        # The row's price is fresher than the series' last point. Appending it
        # keeps "distance to support" honest against the number on screen.
        prices = prices + [price]

    volumes = volume_anomaly(volume_series, asset.get("volume24h") or asset.get("volume_24h"))
    quality = data_quality(prices, volumes)

    timeframes = timeframe_trends(prices)
    alignment = trend_alignment(timeframes)
    ext = extension(prices)
    level_info = levels(prices)
    vol = volatility(prices)
    liq = liquidity(asset.get("volume24h") if "volume24h" in asset else asset.get("volume_24h"),
                    asset.get("marketCap") if "marketCap" in asset else asset.get("market_cap"))
    strength = relative_strength(
        asset.get("change24h") if "change24h" in asset else asset.get("change_24h"),
        context.get("boardMedianChange"),
        context.get("benchmarkChange"),
    )
    regime = context.get("regime")

    opportunity = opportunity_quality(alignment, strength, liq, volumes, regime)
    entry = entry_quality(ext, level_info, vol, prices)
    correlation = (holding or {}).get("correlation") if holding else None
    risk = risk_profile(vol, liq, ext, level_info, correlation)
    setup = detect_setup(prices, level_info, alignment, ext, vol, volumes)
    action = decide_action(opportunity, entry, risk, setup, alignment, ext, holding)

    if quality["level"] == "INSUFFICIENT":
        action = {
            "state": DATA_UNAVAILABLE, "label": ACTION_LABELS[DATA_UNAVAILABLE],
            "tone": ACTION_TONES[DATA_UNAVAILABLE], "perspective": "unknown",
            "reasons": [_reason("insufficient_history", quality["note"], UNAVAILABLE)],
            "confidence": UNAVAILABLE,
        }
        opportunity = dict(opportunity, score=None, band=None, confidence=UNAVAILABLE)
        entry = dict(entry, score=None, band=None, confidence=UNAVAILABLE)

    payload: dict[str, Any] = {
        "symbol": asset.get("symbol"),
        "action": action,
        "opportunity": {"score": opportunity.get("score"), "band": opportunity.get("band"),
                        "confidence": opportunity.get("confidence")},
        "entry": {"score": entry.get("score"), "band": entry.get("band"),
                  "confidence": entry.get("confidence")},
        "risk": {"level": risk.get("level"), "confidence": risk.get("confidence")},
        "dataQuality": quality,
        # Repeated at every depth. A card that shows a verdict without this is
        # the thing this module exists to avoid.
        "disclaimer": "Decision support, not advice. Conditional readings from market data, which can be wrong.",
    }

    if depth == "list":
        payload["why"] = (action.get("reasons") or [])[:2]
        assert_safe_language(payload)
        return payload

    payload["why"] = {
        "action": action.get("reasons") or [],
        "opportunity": opportunity.get("reasons") or [],
        "entry": entry.get("reasons") or [],
    }
    payload["timeframes"] = {
        "rows": timeframes,
        "alignment": alignment,
        # Said out loud so an absent 5m row is understood as a choice.
        "note": "Timeframes are derived from real hourly closes. Intraday rows below 1H are not offered because the provider data behind this board is hourly.",
    }
    payload["structure"] = {"extension": ext, "levels": level_info, "volatility": vol,
                            "liquidity": liq, "relativeStrength": strength}
    payload["setup"] = setup
    payload["riskDetail"] = risk
    payload["anomalies"] = anomaly_scan(volumes, ext, strength, alignment)
    payload["volume"] = volumes
    payload["evidence"] = {
        "priceSeries": {"points": len(prices), "granularity": "1h",
                        "source": context.get("priceSource") or "coingecko board sparkline (7d hourly)"},
        "volumeSeries": {"samples": volumes.get("samples"), "source": "market_observations" if volumes.get("confidence") == KNOWN else None},
        "boardContext": {"medianChangePct": _round(_num(context.get("boardMedianChange"))),
                         "benchmarkChangePct": _round(_num(context.get("benchmarkChange"))),
                         "assets": context.get("boardSize")},
        "regime": regime,
        "observedAt": context.get("observedAt"),
        "limits": [
            "Price history is closing prices only — the provider exposes no OHLC, so levels carry no intraday wicks.",
            "Timeframes are computed from an hourly series, not fetched per interval.",
            "No scheduled-event, on-chain or order-book data is connected.",
        ],
    }
    if holding:
        payload["holding"] = {
            "known": True,
            "quantity": _num(holding.get("quantity")),
            "unrealizedPnlPct": _round(_num(holding.get("unrealizedPnlPct"))),
            "portfolioWeightPct": _round(_num(holding.get("portfolioWeightPct"))),
            "note": "Verdict is framed for an existing position.",
        }
        payload["sizing"] = position_sizing(price, setup, risk,
                                            holding.get("portfolioValue"),
                                            _num(holding.get("riskBudgetPct")) or 1.0)
    else:
        payload["holding"] = {
            "known": False,
            "note": "No holding recorded for this asset, so the verdict is framed for someone not currently in the position.",
        }
        payload["sizing"] = position_sizing(price, setup, risk)

    assert_safe_language(payload)
    return payload


def board_context(rows: Sequence[dict[str, Any]], global_metrics: dict | None = None) -> dict[str, Any]:
    """Shared context computed once per board, not once per row."""
    changes = [r.get("change24h") if "change24h" in r else r.get("change_24h") for r in rows or []]
    values = [c for c in (_num(c) for c in changes) if c is not None]
    median = None
    if values:
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    benchmark = next((_num(r.get("change24h") if "change24h" in r else r.get("change_24h"))
                      for r in rows or [] if str(r.get("symbol") or "").upper() == "BTC"), None)
    regime = market_regime(changes, global_metrics)
    return {
        "boardMedianChange": median,
        "benchmarkChange": benchmark,
        "boardSize": len(values),
        "regime": regime,
        "rotation": rotation(rows),
    }
