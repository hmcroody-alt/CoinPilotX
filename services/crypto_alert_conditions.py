"""Advanced crypto alert conditions: metrics, comparators, compound rules.

A pure library — no database, no network, no notification. ``alert_engine`` owns
the state machine (armed/latched, crossing claims, cooldowns, delivery) and calls
in here to answer one question: *given this asset snapshot and what we saw last
time, does the rule match?* Keeping that answer pure is what makes the advanced
conditions testable without a worker, a provider, or a device.

Time-window clauses
-------------------
A clause may carry ``window_minutes``, in which case the quantity compared is the
percent change in that metric over the window rather than its current level.
Purity is preserved the same way it is for crossings: the library does not read
the series. The caller measures each window against
``services.market_observations`` and hands the readings in, exactly as it hands
in ``previous``. A window the series cannot answer arrives as ``None`` and is
undecidable — never zero, and never "it did not move".

What is deliberately NOT here
-----------------------------
**Crossings of a windowed value.** A window's baseline advances with every
sample, so the 60-minute change moves even when the price does not. A crossing
comparator on top of that would fire on the baseline sliding forward and report
it as a market event. Windowed clauses are restricted to level comparators, and
the combination is rejected at creation.

**Arbitrary user expressions.** A clause is a fixed ``{metric, comparator,
value}`` triple validated against closed vocabularies. There is no expression
parser and nothing is ever ``eval``'d. An unrecognised metric or comparator is
rejected when the rule is created, never skipped at evaluation time — silently
dropping a clause would leave the member with an alert that watches less than
they asked for and no way to tell.

Metric availability is a first-class answer
-------------------------------------------
Providers differ: the Coinbase fallback carries a price but no 24h percentage and
no market cap, so those fields arrive as ``None``. A missing metric is reported
as *undecidable*, never coerced to ``False``. The difference matters for a
compound rule — treating "volume unknown" as "volume did not spike" would turn a
two-clause alert into a silent one-clause alert. The one case where an unknown
metric is still decidable is an ``or`` rule with another clause already matched:
nothing the missing value could have been would change the outcome.
"""

from __future__ import annotations

from typing import Any, Optional

#: Metric key -> the field on a normalized market item
#: (``services.market_data.normalize_market_item``) that carries it.
#:
#: Every one of these arrives on the single ``/coins/markets`` board request the
#: app already makes, so supporting all five costs no extra provider call.
#: ``market_cap_rank`` is deliberately absent: rank improves as the number falls,
#: so "rank above 10" reads both ways and would ship an ambiguity as a feature.
METRIC_FIELDS: dict[str, str] = {
    "price": "price",
    "change_24h": "change_24h",
    "price_change_24h": "price_change_24h",
    "volume_24h": "volume_24h",
    "market_cap": "market_cap",
}

#: Metric key -> field, for the derived readings produced by
#: ``services.market_intelligence``. These are computed, not published: the
#: engine only asks for them when a rule actually names one (see
#: :func:`spec_uses_intelligence`), so an ordinary price alert costs exactly
#: what it cost before.
#:
#: All of them are numeric on purpose. The comparator vocabulary, the edge
#: detection, the crossing-key idempotency and the cooldown all work on numbers,
#: and the point of this addition is to reuse that machinery rather than build a
#: second alerting path with its own idea of when something fired.
#:
#: The categorical concepts are given *ordered* numeric encodings, and only where
#: an order genuinely exists. Risk has one (low to extreme). Setup readiness has
#: one (not ready, ready). Action posture has one, from "get out" to "size in" —
#: and that ordering is about posture only. It is not a confidence ranking and
#: not a score, which is why it is documented here rather than left for a reader
#: to infer from the numbers.
INTELLIGENCE_METRIC_FIELDS: dict[str, str] = {
    "opportunity_quality": "intel_opportunity",
    "entry_quality": "intel_entry",
    "risk_level": "intel_risk_ordinal",
    "action_posture": "intel_action_posture",
    "setup_ready": "intel_setup_ready",
    "breakout_confirmed": "intel_breakout_confirmed",
    "at_support": "intel_at_support",
    "volume_ratio": "intel_volume_ratio",
}
METRIC_FIELDS.update(INTELLIGENCE_METRIC_FIELDS)

#: Risk, low to extreme. "Risk becomes High" is ``risk_level crosses_above 2.5``.
RISK_ORDINALS = {"LOW": 1, "MODERATE": 2, "HIGH": 3, "EXTREME": 4}

#: Action posture, most defensive to most constructive. Deliberately coarse:
#: several states share a rung because they call for the same posture, and
#: pretending WAIT and WAIT_FOR_CONFIRMATION differ by a step would invent a
#: precision the analysis does not have.
ACTION_POSTURE = {
    "EXIT": 1, "AVOID": 1, "HIGH_RISK": 1,
    "REDUCE": 2,
    "TAKE_PARTIAL_PROFIT": 3, "DO_NOT_CHASE": 3,
    "WAIT": 4, "WAIT_FOR_CONFIRMATION": 4, "WAIT_FOR_PULLBACK": 4,
    "REVERSAL_WATCH": 5, "BREAKOUT_WATCH": 5, "PULLBACK_WATCH": 5,
    "HOLD": 6,
    "ACCUMULATE": 7,
    "STRONG_ACCUMULATION": 8,
    # DATA_UNAVAILABLE has no posture. It is absent here on purpose so it
    # resolves to None and the rule reports undecidable rather than firing an
    # "action turned defensive" alert that was really an outage.
}

#: Human labels for notification copy and validation errors. Not user-facing
#: translations — the native client renders its own i18n from the metric key.
METRIC_LABELS: dict[str, str] = {
    "price": "price",
    "change_24h": "24h change",
    "price_change_24h": "24h price move",
    "volume_24h": "24h volume",
    "market_cap": "market cap",
    "opportunity_quality": "opportunity quality",
    "entry_quality": "entry quality",
    "risk_level": "risk level",
    "action_posture": "action posture",
    "setup_ready": "setup readiness",
    "breakout_confirmed": "breakout confirmation",
    "at_support": "pullback to support",
    "volume_ratio": "volume vs recent median",
}

#: Metrics quoted as a percentage rather than a currency amount.
PERCENT_METRICS = frozenset({"change_24h"})

DEFAULT_METRIC = "price"

#: Comparators. ``above``/``below`` are level tests: true whenever the value sits
#: on that side of the threshold. ``crosses_above``/``crosses_below`` are edge
#: tests: true only on the observation that moved from one side to the other.
#:
#: The engine's armed/latched machine already prevents a level rule from
#: notifying repeatedly while the value stays put, so these are not redundant.
#: They answer a different question: a level rule that arms while the market is
#: already past the threshold waits for the value to come back and cross again,
#: whereas a crossing rule states that requirement in the rule itself, and is
#: honest about not being answerable until a prior observation exists.
COMPARATORS = ("above", "below", "crosses_above", "crosses_below")
LEVEL_COMPARATORS = frozenset({"above", "below"})
CROSSING_COMPARATORS = frozenset({"crosses_above", "crosses_below"})

#: Metrics a window may be measured over. ``change_24h`` and ``price_change_24h``
#: are excluded because they are already deltas, and the percent change of a
#: percent change is not a quantity anybody means to ask about.
WINDOWABLE_METRICS = frozenset({"price", "volume_24h", "market_cap"})

#: Windows the sampled series can actually answer, in minutes.
#: ``services.market_observations`` samples on the alert worker's ~45s cycle, so
#: the shortest offered window still spans many distinct readings; anything
#: shorter would risk comparing a reading with itself. The longest is bounded by
#: that module's retention. It reads these values from here rather than
#: declaring its own, so the rule vocabulary and the series capability cannot
#: drift apart.
WINDOW_CHOICES = (15, 30, 60, 120, 240, 360, 720, 1440)

LOGIC_AND = "and"
LOGIC_OR = "or"
LOGIC_MODES = (LOGIC_AND, LOGIC_OR)

#: A compound rule is capped so one rule cannot fan out into an unbounded
#: evaluation. Four clauses covers every combination the UI offers.
MAX_CLAUSES = 4


class ConditionError(ValueError):
    """A rule the member asked for that cannot be honoured as written."""


def spec_uses_intelligence(spec: Any) -> bool:
    """Does this rule name any derived-intelligence metric?

    The engine calls this before doing the analysis work, so a rule that only
    reads published board fields never triggers a single line of it. Without
    this check, adding these metrics would have quietly made every alert in the
    system more expensive to evaluate.
    """
    if not isinstance(spec, dict):
        return False
    for clause in spec.get("clauses") or []:
        if isinstance(clause, dict) and str(clause.get("metric") or "").strip().lower() in INTELLIGENCE_METRIC_FIELDS:
            return True
    return False


def normalize_metric(metric: Any) -> str:
    value = str(metric or DEFAULT_METRIC).strip().lower()
    return value if value in METRIC_FIELDS else DEFAULT_METRIC


def metric_label(metric: Any) -> str:
    return METRIC_LABELS.get(normalize_metric(metric), "value")


def is_percent_metric(metric: Any) -> bool:
    return normalize_metric(metric) in PERCENT_METRICS


def normalize_window(minutes: Any) -> int:
    """One of :data:`WINDOW_CHOICES`, or :class:`ConditionError`.

    Rejects rather than snapping to the nearest offered window: a rule stored as
    "60 minutes" when the member asked for 45 is a rule they did not write.
    """
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        raise ConditionError("An alert window must be a whole number of minutes.")
    if value not in WINDOW_CHOICES:
        offered = ", ".join(str(choice) for choice in WINDOW_CHOICES)
        raise ConditionError(
            f"Unsupported alert window: {value} minutes. Choose one of {offered}.")
    return value


def clause_window(clause: Any) -> int:
    """The clause's window in minutes, or ``0`` for a level clause."""
    if not isinstance(clause, dict):
        return 0
    raw = clause.get("window_minutes")
    if raw in (None, "", 0):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def window_label(minutes: Any) -> str:
    value = int(minutes or 0)
    if value and value % 60 == 0:
        hours = value // 60
        return f"{hours}h"
    return f"{value}m"


def clause_key(clause: Any) -> str:
    """Identity of a clause within one rule's observation map.

    A rule may legitimately hold both "price is above 61,000" and "price over 1h
    is below -5%". Keying the stored observations by metric alone would let the
    second overwrite the first, so the previous reading a clause is compared
    against would belong to the other clause.
    """
    metric = normalize_metric((clause or {}).get("metric"))
    minutes = clause_window(clause)
    return f"{metric}@{minutes}m" if minutes else metric


def window_key(metric: Any, minutes: Any) -> str:
    """The key the caller must file a measured window reading under."""
    return f"{normalize_metric(metric)}@{int(minutes)}m"


def required_windows(spec: Any) -> list:
    """``(metric, minutes)`` for every window this rule needs measured.

    The engine uses this to fetch exactly the readings the rule depends on,
    rather than measuring every window of every metric on every cycle.
    """
    seen: list = []
    for clause in (spec or {}).get("clauses") or ():
        minutes = clause_window(clause)
        if not minutes:
            continue
        pair = (normalize_metric(clause.get("metric")), minutes)
        if pair not in seen:
            seen.append(pair)
    return seen


def clause_is_percent(clause: Any) -> bool:
    """Windowed clauses always compare a percent change, whatever the metric."""
    return bool(clause_window(clause)) or is_percent_metric((clause or {}).get("metric"))


def metric_value(asset: Optional[dict], metric: Any) -> Optional[float]:
    """The asset's value for ``metric``, or ``None`` when the provider omitted it.

    ``None`` is a real answer that propagates all the way out as "undecidable".
    It is never turned into a zero: a market cap of ``None`` means the fallback
    provider does not publish one, while a market cap of ``0`` would be a claim
    about the asset.
    """
    if not asset:
        return None
    raw = asset.get(METRIC_FIELDS.get(normalize_metric(metric), DEFAULT_METRIC))
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # NaN is not an observation


def compare(comparator: Any, observed: float, threshold: float,
            previous: Optional[float] = None) -> Optional[bool]:
    """Does ``observed`` satisfy ``comparator`` against ``threshold``?

    Returns ``None`` — undecidable — when a crossing is asked for and there is no
    prior observation to cross from. A first observation genuinely cannot show a
    crossing, and answering ``False`` would be indistinguishable from "we looked
    and it did not cross".
    """
    name = str(comparator or "above").strip().lower()
    if name == "above":
        return observed >= threshold
    if name == "below":
        return observed <= threshold
    if name in CROSSING_COMPARATORS:
        if previous is None:
            return None
        if name == "crosses_above":
            return previous < threshold <= observed
        return previous > threshold >= observed
    raise ConditionError(f"Unsupported comparator: {comparator!r}")


def validate_clause(clause: Any) -> dict:
    """Normalize one clause, raising :class:`ConditionError` on anything unusable.

    Rejecting at creation is the point: a clause that cannot be evaluated must
    never reach the worker, because the worker's only options there are to skip
    it (an alert quieter than the member asked for) or to guess.
    """
    if not isinstance(clause, dict):
        raise ConditionError("Each alert condition must be an object.")
    metric = str(clause.get("metric") or DEFAULT_METRIC).strip().lower()
    if metric not in METRIC_FIELDS:
        raise ConditionError(f"Unsupported alert metric: {metric!r}")
    comparator = str(clause.get("comparator") or clause.get("condition") or "above").strip().lower()
    if comparator not in COMPARATORS:
        raise ConditionError(f"Unsupported alert comparator: {comparator!r}")
    raw_value = clause.get("value", clause.get("threshold"))
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise ConditionError("Each alert condition needs a numeric value.")
    if value != value or value in (float("inf"), float("-inf")):
        raise ConditionError("Each alert condition needs a finite value.")
    normalized = {"metric": metric, "comparator": comparator, "value": value}

    minutes = clause.get("window_minutes")
    if minutes in (None, "", 0):
        return normalized
    minutes = normalize_window(minutes)
    if metric not in WINDOWABLE_METRICS:
        raise ConditionError(
            f"{metric_label(metric)} cannot be measured over a window.")
    if comparator not in LEVEL_COMPARATORS:
        # See the module docstring: a window's baseline advances every sample, so
        # a crossing here would fire on the baseline moving rather than on the
        # market moving, and report that to the member as a market event.
        raise ConditionError(
            "A time-window condition compares a change, so it supports 'above' "
            "and 'below' only.")
    normalized["window_minutes"] = minutes
    return normalized


def validate_spec(spec: Any) -> dict:
    """Normalize a whole compound rule. Raises :class:`ConditionError`."""
    if not isinstance(spec, dict):
        raise ConditionError("An advanced alert needs a condition object.")
    logic = str(spec.get("logic") or LOGIC_AND).strip().lower()
    if logic not in LOGIC_MODES:
        raise ConditionError(f"Unsupported alert logic: {logic!r}")
    raw_clauses = spec.get("clauses")
    if not isinstance(raw_clauses, list) or not raw_clauses:
        raise ConditionError("An advanced alert needs at least one condition.")
    if len(raw_clauses) > MAX_CLAUSES:
        raise ConditionError(f"An advanced alert can combine at most {MAX_CLAUSES} conditions.")
    clauses = [validate_clause(clause) for clause in raw_clauses]
    seen = set()
    for clause in clauses:
        # The window is part of the identity: "price above 61,000" and "price
        # over 1h above 5%" are different conditions on the same metric and both
        # belong in one rule.
        key = (clause["metric"], clause["comparator"], clause_window(clause))
        if key in seen:
            raise ConditionError(
                f"This alert repeats the same {metric_label(clause['metric'])} condition twice.")
        seen.add(key)
    return {"logic": logic, "clauses": clauses}


def window_change(windows: Optional[dict], metric: Any, minutes: Any) -> Optional[float]:
    """The measured percent change for one window, or ``None`` if undecidable.

    ``windows`` maps :func:`window_key` to a reading from
    ``services.market_observations.window_reading``. A reading that came back
    ``ok`` False is a window the series could not answer, and it is returned here
    as ``None`` so it flows through the same undecidable path as a metric the
    provider omitted.
    """
    reading = (windows or {}).get(window_key(metric, minutes))
    if not isinstance(reading, dict) or not reading.get("ok"):
        return None
    value = reading.get("change_percent")
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def observation_map(asset: Optional[dict], spec: dict,
                    windows: Optional[dict] = None) -> dict:
    """Every quantity this rule reads, snapshotted. Persisted as the next ``previous``.

    Only the quantities the rule actually uses are recorded, so the stored
    snapshot stays a description of the rule rather than of the provider payload.
    Keyed by :func:`clause_key` so a windowed clause and a level clause on the
    same metric do not overwrite each other.
    """
    snapshot = {}
    for clause in spec.get("clauses") or ():
        minutes = clause_window(clause)
        if minutes:
            snapshot[clause_key(clause)] = window_change(windows, clause["metric"], minutes)
        else:
            snapshot[clause_key(clause)] = metric_value(asset, clause["metric"])
    return snapshot


def evaluate_clause(asset: Optional[dict], clause: dict,
                    previous: Optional[dict] = None,
                    windows: Optional[dict] = None) -> dict:
    """One clause against one snapshot.

    ``matched`` is ``None`` when undecidable: the provider omitted the metric, a
    crossing was asked for and there is no prior observation, or the clause is
    windowed and the series could not answer that window.
    """
    metric = clause["metric"]
    minutes = clause_window(clause)
    key = clause_key(clause)
    base = {"metric": metric, "key": key, "window_minutes": minutes,
            "comparator": clause["comparator"], "value": clause["value"]}

    if minutes:
        observed = window_change(windows, metric, minutes)
        if observed is None:
            return {**base, "observed": None, "matched": None,
                    "reason": "window_unavailable"}
    else:
        observed = metric_value(asset, metric)
        if observed is None:
            return {**base, "observed": None, "matched": None,
                    "reason": "metric_unavailable"}

    prior = (previous or {}).get(key)
    if prior is not None:
        try:
            prior = float(prior)
        except (TypeError, ValueError):
            prior = None
    matched = compare(clause["comparator"], observed, clause["value"], prior)
    reason = "" if matched is not None else "no_prior_observation"
    return {**base, "observed": observed, "previous": prior,
            "matched": matched, "reason": reason}


def evaluate_spec(asset: Optional[dict], spec: dict,
                  previous: Optional[dict] = None,
                  windows: Optional[dict] = None) -> dict:
    """A whole compound rule against one snapshot.

    Returns ``{ok, matched, logic, clauses, observations, undecidable}``.

    ``ok`` is False when the rule could not be decided, and the engine treats
    that exactly like a failed quote: mark the rule checked, leave the latch
    alone, do not notify. A provider blip must not re-arm a latched rule.

    Short-circuit honesty: an ``or`` whose first clause already matched is
    decided no matter what the unknown clauses would have said, and an ``and``
    with a clause that definitively did not match is likewise decided. Only a
    rule whose *outcome* still depends on an unknown value is undecidable.
    """
    clauses = spec.get("clauses") or ()
    logic = str(spec.get("logic") or LOGIC_AND).strip().lower()
    results = [evaluate_clause(asset, clause, previous, windows) for clause in clauses]
    decided = [r["matched"] for r in results if r["matched"] is not None]
    undecidable = [r for r in results if r["matched"] is None]

    if logic == LOGIC_OR:
        matched = any(decided)
        # Any True settles an OR; otherwise an unknown could still have been True.
        resolved = matched or not undecidable
    else:
        matched = bool(decided) and all(decided)
        # Any False settles an AND; otherwise an unknown could still have been False.
        resolved = (False in decided) or not undecidable
        if not decided and not undecidable:
            resolved, matched = False, False

    return {
        "ok": bool(resolved),
        "matched": bool(matched) if resolved else None,
        "logic": logic,
        "clauses": results,
        "observations": observation_map(asset, spec, windows),
        "undecidable": [r["key"] for r in undecidable],
        "message": "" if resolved else _undecidable_message(undecidable),
    }


def _clause_subject(clause: dict, symbol: str = "") -> str:
    """``BTC price`` or ``BTC price over 1h`` — the thing the clause is about."""
    metric = normalize_metric(clause.get("metric"))
    minutes = clause_window(clause)
    subject = f"{symbol} {metric_label(metric)}".strip()
    return f"{subject} over {window_label(minutes)}" if minutes else subject


def _undecidable_message(undecidable: list) -> str:
    if not undecidable:
        return ""
    reasons = {r["reason"] for r in undecidable}
    names = ", ".join(sorted({_clause_subject(r) for r in undecidable}))
    if reasons == {"no_prior_observation"}:
        return f"Watching {names}; a crossing needs one earlier reading to compare against."
    if reasons == {"window_unavailable"}:
        # Deliberately not "it has not moved": the series has not been running
        # long enough, or has a hole in it, and saying otherwise would report an
        # absence of data as an observation about the market.
        return (f"Watching {names}; there are not enough recorded readings to "
                "measure that window yet.")
    return f"{names.capitalize()} is not available from the market source right now."


def describe_clause(clause: dict, symbol: str = "") -> str:
    """Plain description of one clause, for notification bodies and audit rows."""
    comparator = str(clause.get("comparator") or "above")
    value = clause.get("value")
    verbs = {"above": "is above", "below": "is below",
             "crosses_above": "crosses above", "crosses_below": "crosses below"}
    amount = f"{value}%" if clause_is_percent(clause) else f"{value:,}"
    return f"{_clause_subject(clause, symbol)} {verbs.get(comparator, comparator)} {amount}"


def describe_spec(spec: dict, symbol: str = "") -> str:
    """Plain description of a compound rule, e.g. ``BTC price is above 61,000 and
    BTC 24h volume is above 30,000,000,000``."""
    clauses = spec.get("clauses") or ()
    joiner = " or " if str(spec.get("logic") or LOGIC_AND).lower() == LOGIC_OR else " and "
    return joiner.join(describe_clause(clause, symbol) for clause in clauses)
