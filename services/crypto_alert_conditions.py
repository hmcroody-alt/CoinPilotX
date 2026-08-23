"""Advanced crypto alert conditions: metrics, comparators, compound rules.

A pure library — no database, no network, no notification. ``alert_engine`` owns
the state machine (armed/latched, crossing claims, cooldowns, delivery) and calls
in here to answer one question: *given this asset snapshot and what we saw last
time, does the rule match?* Keeping that answer pure is what makes the advanced
conditions testable without a worker, a provider, or a device.

What is deliberately NOT here
-----------------------------
**Time-window conditions.** "Moved 5% within the last hour" cannot be answered
from a single snapshot, and the existing ``price_history`` table is written by
legacy Telegram paths at no fixed cadence and read as "the last N rows" — so a
window built on it would be a guess wearing a timestamp. Those conditions belong
on top of a real sampled observation series and are not offered until one exists.

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

#: Human labels for notification copy and validation errors. Not user-facing
#: translations — the native client renders its own i18n from the metric key.
METRIC_LABELS: dict[str, str] = {
    "price": "price",
    "change_24h": "24h change",
    "price_change_24h": "24h price move",
    "volume_24h": "24h volume",
    "market_cap": "market cap",
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

LOGIC_AND = "and"
LOGIC_OR = "or"
LOGIC_MODES = (LOGIC_AND, LOGIC_OR)

#: A compound rule is capped so one rule cannot fan out into an unbounded
#: evaluation. Four clauses covers every combination the UI offers.
MAX_CLAUSES = 4


class ConditionError(ValueError):
    """A rule the member asked for that cannot be honoured as written."""


def normalize_metric(metric: Any) -> str:
    value = str(metric or DEFAULT_METRIC).strip().lower()
    return value if value in METRIC_FIELDS else DEFAULT_METRIC


def metric_label(metric: Any) -> str:
    return METRIC_LABELS.get(normalize_metric(metric), "value")


def is_percent_metric(metric: Any) -> bool:
    return normalize_metric(metric) in PERCENT_METRICS


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
    return {"metric": metric, "comparator": comparator, "value": value}


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
        key = (clause["metric"], clause["comparator"])
        if key in seen:
            raise ConditionError(
                f"This alert repeats the same {metric_label(clause['metric'])} condition twice.")
        seen.add(key)
    return {"logic": logic, "clauses": clauses}


def observation_map(asset: Optional[dict], spec: dict) -> dict:
    """Every metric this rule reads, snapshotted. Persisted as the next ``previous``.

    Only the metrics the rule actually uses are recorded, so the stored snapshot
    stays a description of the rule rather than of the provider payload.
    """
    return {
        clause["metric"]: metric_value(asset, clause["metric"])
        for clause in spec.get("clauses") or ()
    }


def evaluate_clause(asset: Optional[dict], clause: dict,
                    previous: Optional[dict] = None) -> dict:
    """One clause against one snapshot.

    ``matched`` is ``None`` when undecidable: the provider omitted the metric, or
    a crossing was asked for and there is no prior observation.
    """
    metric = clause["metric"]
    observed = metric_value(asset, metric)
    if observed is None:
        return {"metric": metric, "comparator": clause["comparator"],
                "value": clause["value"], "observed": None, "matched": None,
                "reason": "metric_unavailable"}
    prior = (previous or {}).get(metric)
    if prior is not None:
        try:
            prior = float(prior)
        except (TypeError, ValueError):
            prior = None
    matched = compare(clause["comparator"], observed, clause["value"], prior)
    reason = ""
    if matched is None:
        reason = "no_prior_observation"
    return {"metric": metric, "comparator": clause["comparator"],
            "value": clause["value"], "observed": observed, "previous": prior,
            "matched": matched, "reason": reason}


def evaluate_spec(asset: Optional[dict], spec: dict,
                  previous: Optional[dict] = None) -> dict:
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
    results = [evaluate_clause(asset, clause, previous) for clause in clauses]
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
        "observations": observation_map(asset, spec),
        "undecidable": [r["metric"] for r in undecidable],
        "message": "" if resolved else _undecidable_message(undecidable),
    }


def _undecidable_message(undecidable: list) -> str:
    if not undecidable:
        return ""
    reasons = {r["reason"] for r in undecidable}
    names = ", ".join(sorted({metric_label(r["metric"]) for r in undecidable}))
    if reasons == {"no_prior_observation"}:
        return f"Watching {names}; a crossing needs one earlier reading to compare against."
    return f"{names.capitalize()} is not available from the market source right now."


def describe_clause(clause: dict, symbol: str = "") -> str:
    """Plain description of one clause, for notification bodies and audit rows."""
    metric = normalize_metric(clause.get("metric"))
    comparator = str(clause.get("comparator") or "above")
    value = clause.get("value")
    verbs = {"above": "is above", "below": "is below",
             "crosses_above": "crosses above", "crosses_below": "crosses below"}
    amount = f"{value}%" if is_percent_metric(metric) else f"{value:,}"
    subject = f"{symbol} {metric_label(metric)}".strip()
    return f"{subject} {verbs.get(comparator, comparator)} {amount}"


def describe_spec(spec: dict, symbol: str = "") -> str:
    """Plain description of a compound rule, e.g. ``BTC price is above 61,000 and
    BTC 24h volume is above 30,000,000,000``."""
    clauses = spec.get("clauses") or ()
    joiner = " or " if str(spec.get("logic") or LOGIC_AND).lower() == LOGIC_OR else " and "
    return joiner.join(describe_clause(clause, symbol) for clause in clauses)
