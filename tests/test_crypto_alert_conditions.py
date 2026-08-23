"""Unit tests for the advanced crypto alert condition library.

``services.crypto_alert_conditions`` is pure — no database, no provider, no
notification — so these tests need no schema and no fixtures. That is the point
of splitting it out: the interesting part of an advanced alert is the decision,
and the decision can be proven exhaustively without a worker cycle.

The invariants worth stating explicitly, because each one is a defect the
obvious implementation would have shipped:

  * a metric the provider does not publish reads as ``None``, never ``0`` — the
    Coinbase fallback carries no 24h change and no market cap, and a rule that
    treated "unknown" as "zero" would fire "market cap below 1,000,000" on every
    fallback quote;
  * an undecidable clause makes the *rule* undecidable only when the outcome
    still depends on it, so a partially-known compound rule is still answered
    when the answer is already forced;
  * a crossing is unanswerable on a first observation and says so, rather than
    reporting "did not cross";
  * unusable rules are rejected when they are written, not skipped when they are
    evaluated — an alert that silently watches less than the member asked for is
    worse than one that refuses to be created.

Run directly (no pytest required):

    python tests/test_crypto_alert_conditions.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import crypto_alert_conditions as cond  # noqa: E402


FAILURES: list[str] = []


def check(label: str, actual, expected):
    if actual == expected:
        print(f"  PASS  {label}")
        return
    FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _raises(fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except cond.ConditionError:
        return True
    return False


# A full CoinGecko board row, as ``market_data.normalize_market_item`` emits it.
FULL_ASSET = {
    "symbol": "BTC",
    "price": 61500.0,
    "change_24h": 4.25,
    "price_change_24h": 2500.0,
    "volume_24h": 31_000_000_000.0,
    "market_cap": 1_210_000_000_000.0,
}

# The Coinbase ticker fallback: a real price, and deliberate ``None`` for the
# fields Coinbase does not publish.
FALLBACK_ASSET = {
    "symbol": "BTC",
    "price": 61500.0,
    "change_24h": None,
    "price_change_24h": None,
    "volume_24h": None,
    "market_cap": None,
}


def clause(metric="price", comparator="above", value=0.0) -> dict:
    return {"metric": metric, "comparator": comparator, "value": value}


def spec(*clauses, logic="and") -> dict:
    return {"logic": logic, "clauses": list(clauses)}


# --- metric resolution ------------------------------------------------------
def test_every_supported_metric_resolves_from_a_board_row():
    """All five metrics come off the one ``/coins/markets`` call already made."""
    check("price", cond.metric_value(FULL_ASSET, "price"), 61500.0)
    check("change_24h", cond.metric_value(FULL_ASSET, "change_24h"), 4.25)
    check("price_change_24h", cond.metric_value(FULL_ASSET, "price_change_24h"), 2500.0)
    check("volume_24h", cond.metric_value(FULL_ASSET, "volume_24h"), 31_000_000_000.0)
    check("market_cap", cond.metric_value(FULL_ASSET, "market_cap"), 1_210_000_000_000.0)


def test_missing_metric_is_none_and_never_zero():
    """The fallback provider's omissions must not read as an observation of 0.

    This is the defect that would fire "market cap below 1,000,000" on every
    fallback quote for the largest asset on the board.
    """
    for metric in ("change_24h", "price_change_24h", "volume_24h", "market_cap"):
        value = cond.metric_value(FALLBACK_ASSET, metric)
        check(f"{metric} is None on fallback", value, None)
        check(f"{metric} is not 0 on fallback", value == 0, False)
    check("price still resolves on fallback",
          cond.metric_value(FALLBACK_ASSET, "price"), 61500.0)


def test_unusable_raw_values_are_not_observations():
    check("absent asset", cond.metric_value(None, "price"), None)
    check("absent key", cond.metric_value({}, "price"), None)
    check("non-numeric string", cond.metric_value({"price": "n/a"}, "price"), None)
    check("nan", cond.metric_value({"price": float("nan")}, "price"), None)
    # ``True`` is an int in Python; a boolean is a flag, not a price.
    check("boolean", cond.metric_value({"price": True}, "price"), None)
    check("numeric string still counts", cond.metric_value({"price": "12.5"}, "price"), 12.5)


def test_unknown_metric_falls_back_to_price_for_reads():
    check("normalize unknown", cond.normalize_metric("moon_factor"), "price")
    check("normalize empty", cond.normalize_metric(""), "price")
    check("normalize case", cond.normalize_metric("Market_Cap"), "market_cap")
    check("percent metric", cond.is_percent_metric("change_24h"), True)
    check("non-percent metric", cond.is_percent_metric("price"), False)


# --- comparators ------------------------------------------------------------
def test_level_comparators_are_inclusive_of_the_threshold():
    check("above, over", cond.compare("above", 10.0, 5.0), True)
    check("above, exactly at", cond.compare("above", 5.0, 5.0), True)
    check("above, under", cond.compare("above", 4.0, 5.0), False)
    check("below, under", cond.compare("below", 4.0, 5.0), True)
    check("below, exactly at", cond.compare("below", 5.0, 5.0), True)
    check("below, over", cond.compare("below", 6.0, 5.0), False)


def test_crossing_needs_a_prior_observation():
    """A first observation cannot show a crossing, and must not claim otherwise.

    ``None`` here is what stops the engine reporting "we looked and it did not
    cross" for a reading there was nothing to compare against.
    """
    check("crosses_above, no prior", cond.compare("crosses_above", 10.0, 5.0, None), None)
    check("crosses_below, no prior", cond.compare("crosses_below", 1.0, 5.0, None), None)


def test_crossing_is_an_edge_not_a_level():
    check("crossed up", cond.compare("crosses_above", 10.0, 5.0, 4.0), True)
    check("already above, stayed above", cond.compare("crosses_above", 10.0, 5.0, 6.0), False)
    check("landed exactly on the threshold", cond.compare("crosses_above", 5.0, 5.0, 4.0), True)
    check("left the threshold upward", cond.compare("crosses_above", 6.0, 5.0, 5.0), False)
    check("crossed down", cond.compare("crosses_below", 1.0, 5.0, 6.0), True)
    check("already below, stayed below", cond.compare("crosses_below", 1.0, 5.0, 2.0), False)
    check("fell to exactly the threshold", cond.compare("crosses_below", 5.0, 5.0, 6.0), True)


def test_unknown_comparator_is_an_error_not_a_default():
    check("unknown comparator raises", _raises(cond.compare, "vibes", 1.0, 2.0), True)


# --- validation -------------------------------------------------------------
def test_valid_clause_normalizes():
    got = cond.validate_clause({"metric": " Price ", "comparator": "ABOVE", "value": "61500"})
    check("clause normalized", got,
          {"metric": "price", "comparator": "above", "value": 61500.0})


def test_clause_accepts_the_legacy_field_names():
    """The existing engine calls these ``condition`` and ``threshold``."""
    got = cond.validate_clause({"metric": "price", "condition": "below", "threshold": 100})
    check("legacy names", got,
          {"metric": "price", "comparator": "below", "value": 100.0})


def test_unusable_clauses_are_rejected_at_creation():
    check("not an object", _raises(cond.validate_clause, ["price", "above", 1]), True)
    check("unknown metric", _raises(cond.validate_clause, clause(metric="moon_factor")), True)
    check("unknown comparator", _raises(cond.validate_clause, clause(comparator="vibes")), True)
    check("missing value", _raises(cond.validate_clause, {"metric": "price", "comparator": "above"}), True)
    check("non-numeric value", _raises(cond.validate_clause, clause(value="soon")), True)
    check("infinite value", _raises(cond.validate_clause, clause(value=float("inf"))), True)
    check("nan value", _raises(cond.validate_clause, clause(value=float("nan"))), True)


def test_spec_validation_bounds_the_rule():
    check("not an object", _raises(cond.validate_spec, "price above 5"), True)
    check("unknown logic", _raises(cond.validate_spec, {"logic": "xor", "clauses": [clause()]}), True)
    check("no clauses", _raises(cond.validate_spec, {"logic": "and", "clauses": []}), True)
    check("clauses not a list", _raises(cond.validate_spec, {"logic": "and", "clauses": {}}), True)
    too_many = [clause(metric=m) for m in
                ("price", "change_24h", "volume_24h", "market_cap", "price_change_24h")]
    check("over the clause cap",
          _raises(cond.validate_spec, {"logic": "and", "clauses": too_many}), True)


def test_spec_rejects_a_repeated_condition():
    """Two identical clauses are a UI mistake, and silently deduping them would
    change the rule the member believes they wrote."""
    duplicate = spec(clause("price", "above", 100), clause("price", "above", 200))
    check("duplicate metric+comparator", _raises(cond.validate_spec, duplicate), True)
    # The same metric with a different comparator is a legitimate range rule.
    ranged = cond.validate_spec(spec(clause("price", "above", 100), clause("price", "below", 200)))
    check("range rule allowed", len(ranged["clauses"]), 2)


def test_spec_defaults_to_and():
    check("default logic", cond.validate_spec({"clauses": [clause()]})["logic"], "and")


# --- compound evaluation ----------------------------------------------------
def test_and_requires_every_clause():
    both = spec(clause("price", "above", 61000), clause("change_24h", "above", 4))
    result = cond.evaluate_spec(FULL_ASSET, both)
    check("and, both true: ok", result["ok"], True)
    check("and, both true: matched", result["matched"], True)

    one_short = spec(clause("price", "above", 61000), clause("change_24h", "above", 90))
    result = cond.evaluate_spec(FULL_ASSET, one_short)
    check("and, one false: ok", result["ok"], True)
    check("and, one false: matched", result["matched"], False)


def test_or_needs_only_one_clause():
    either = spec(clause("price", "above", 999999), clause("change_24h", "above", 4),
                  logic="or")
    result = cond.evaluate_spec(FULL_ASSET, either)
    check("or, one true: matched", result["matched"], True)

    neither = spec(clause("price", "above", 999999), clause("change_24h", "above", 90),
                   logic="or")
    result = cond.evaluate_spec(FULL_ASSET, neither)
    check("or, none true: ok", result["ok"], True)
    check("or, none true: matched", result["matched"], False)


def test_an_unknown_metric_makes_the_rule_undecidable():
    """``ok=False`` is the signal the engine treats like a failed quote."""
    rule = spec(clause("price", "above", 61000), clause("market_cap", "above", 1))
    result = cond.evaluate_spec(FALLBACK_ASSET, rule)
    check("undecidable: ok", result["ok"], False)
    check("undecidable: matched", result["matched"], None)
    check("undecidable: names the metric", result["undecidable"], ["market_cap"])
    check("undecidable: explains itself", bool(result["message"]), True)


def test_an_or_already_matched_is_decided_despite_an_unknown():
    """Nothing the missing value could have been changes an OR that already hit."""
    rule = spec(clause("price", "above", 61000), clause("market_cap", "above", 1),
                logic="or")
    result = cond.evaluate_spec(FALLBACK_ASSET, rule)
    check("or short-circuit: ok", result["ok"], True)
    check("or short-circuit: matched", result["matched"], True)
    check("or short-circuit: still reports the gap", result["undecidable"], ["market_cap"])


def test_an_and_already_failed_is_decided_despite_an_unknown():
    rule = spec(clause("price", "above", 999999), clause("market_cap", "above", 1))
    result = cond.evaluate_spec(FALLBACK_ASSET, rule)
    check("and short-circuit: ok", result["ok"], True)
    check("and short-circuit: matched", result["matched"], False)


def test_an_and_still_pending_an_unknown_is_undecidable():
    rule = spec(clause("price", "above", 61000), clause("market_cap", "above", 1))
    result = cond.evaluate_spec(FALLBACK_ASSET, rule)
    check("and pending: ok", result["ok"], False)
    check("and pending: matched", result["matched"], None)


def test_an_or_pending_an_unknown_is_undecidable():
    rule = spec(clause("price", "above", 999999), clause("market_cap", "above", 1),
                logic="or")
    result = cond.evaluate_spec(FALLBACK_ASSET, rule)
    check("or pending: ok", result["ok"], False)
    check("or pending: matched", result["matched"], None)


def test_every_metric_unknown_is_undecidable_not_false():
    rule = spec(clause("market_cap", "above", 1), clause("volume_24h", "above", 1))
    result = cond.evaluate_spec(FALLBACK_ASSET, rule)
    check("all unknown: ok", result["ok"], False)
    check("all unknown: matched", result["matched"], None)


def test_a_failed_quote_is_undecidable_not_a_miss():
    rule = spec(clause("price", "above", 61000))
    result = cond.evaluate_spec(None, rule)
    check("no asset: ok", result["ok"], False)
    check("no asset: matched", result["matched"], None)


# --- crossings inside a compound rule ---------------------------------------
def test_compound_crossing_uses_the_per_metric_previous_value():
    rule = spec(clause("price", "crosses_above", 61000),
                clause("volume_24h", "above", 30_000_000_000))
    previous = {"price": 60_000.0, "volume_24h": 29_000_000_000.0}
    result = cond.evaluate_spec(FULL_ASSET, rule, previous)
    check("crossing in compound: matched", result["matched"], True)

    # Same snapshot, but we were already above: the edge did not happen.
    result = cond.evaluate_spec(FULL_ASSET, rule, {"price": 61_400.0})
    check("no edge: matched", result["matched"], False)


def test_first_observation_of_a_crossing_rule_is_undecidable():
    rule = spec(clause("price", "crosses_above", 61000))
    result = cond.evaluate_spec(FULL_ASSET, rule, None)
    check("first crossing observation: ok", result["ok"], False)
    check("first crossing observation: matched", result["matched"], None)
    check("first crossing observation: reason",
          result["clauses"][0]["reason"], "no_prior_observation")


def test_an_unusable_stored_previous_is_treated_as_absent():
    """A corrupt persisted snapshot must degrade to "no prior", not crash."""
    rule = spec(clause("price", "crosses_above", 61000))
    result = cond.evaluate_spec(FULL_ASSET, rule, {"price": "unknown"})
    check("corrupt previous: ok", result["ok"], False)
    check("corrupt previous: matched", result["matched"], None)


# --- observation snapshot ---------------------------------------------------
def test_observation_map_records_only_what_the_rule_reads():
    rule = spec(clause("price", "above", 1), clause("volume_24h", "above", 1))
    check("observation keys", sorted(cond.observation_map(FULL_ASSET, rule)),
          ["price", "volume_24h"])
    check("observation values",
          cond.observation_map(FULL_ASSET, rule)["price"], 61500.0)


def test_observation_map_records_gaps_as_none():
    """Persisting ``None`` is what makes the next cycle's crossing honest: it
    remembers that we had no reading, rather than remembering a stale one."""
    rule = spec(clause("market_cap", "above", 1))
    check("gap recorded", cond.observation_map(FALLBACK_ASSET, rule), {"market_cap": None})


# --- descriptions -----------------------------------------------------------
def test_descriptions_read_as_the_member_wrote_them():
    check("level clause",
          cond.describe_clause(clause("price", "above", 61000), "BTC"),
          "BTC price is above 61,000")
    check("percent clause",
          cond.describe_clause(clause("change_24h", "below", -5), "ETH"),
          "ETH 24h change is below -5%")
    check("crossing clause",
          cond.describe_clause(clause("price", "crosses_above", 61000), "BTC"),
          "BTC price crosses above 61,000")
    check("compound and",
          cond.describe_spec(spec(clause("price", "above", 61000),
                                  clause("volume_24h", "above", 30_000_000_000)), "BTC"),
          "BTC price is above 61,000 and BTC 24h volume is above 30,000,000,000")
    check("compound or",
          cond.describe_spec(spec(clause("price", "above", 1),
                                  clause("price", "below", 2), logic="or"), "BTC"),
          "BTC price is above 1 and BTC price is below 2".replace(" and ", " or "))


def test_description_works_without_a_symbol():
    check("no symbol", cond.describe_clause(clause("price", "above", 100)),
          "price is above 100")


# --- time windows -----------------------------------------------------------
def wclause(metric="price", comparator="below", value=-5.0, minutes=60) -> dict:
    return {"metric": metric, "comparator": comparator, "value": value,
            "window_minutes": minutes}


def windows(**readings) -> dict:
    """A measured-window map as ``alert_engine`` builds it.

    Values are the readings ``market_observations.window_reading`` returns; a
    window it could not answer is passed through here as ``ok`` False, exactly
    as the engine files it.
    """
    return {key: ({"ok": True, "change_percent": value}
                  if value is not None else {"ok": False, "change_percent": None})
            for key, value in readings.items()}


def test_a_window_is_accepted_only_on_a_metric_that_has_one():
    """``change_24h`` is already a delta; the percent change of a percent change
    is not a quantity anybody means to ask about."""
    check("price is windowable", "window_minutes" in cond.validate_clause(wclause("price")), True)
    check("volume is windowable", "window_minutes" in cond.validate_clause(wclause("volume_24h")), True)
    check("24h change is not", _raises(cond.validate_clause, wclause("change_24h")), True)


def test_a_windowed_crossing_is_rejected_at_creation():
    """The baseline advances with every sample, so a crossing here would fire on
    the window sliding forward and report it to the member as a market event."""
    check("crosses_above", _raises(cond.validate_clause, wclause(comparator="crosses_above")), True)
    check("crosses_below", _raises(cond.validate_clause, wclause(comparator="crosses_below")), True)


def test_an_unoffered_window_is_rejected_rather_than_snapped():
    """A rule stored as 60 minutes when the member asked for 45 is a rule they
    did not write, and nothing on screen would show the substitution."""
    check("45m", _raises(cond.validate_clause, wclause(minutes=45)), True)
    check("nonsense", _raises(cond.validate_clause, wclause(minutes="soon")), True)
    check("60m survives", cond.validate_clause(wclause(minutes=60))["window_minutes"], 60)


def test_an_absent_window_leaves_a_plain_level_clause():
    for empty in (None, "", 0):
        result = cond.validate_clause({"metric": "price", "comparator": "above",
                                       "value": 1.0, "window_minutes": empty})
        check(f"{empty!r} is not a window", "window_minutes" in result, False)


def test_a_window_is_part_of_a_clause_identity():
    """"price above 61,000" and "price over 1h above 5%" are different
    conditions on the same metric and both belong in one rule."""
    result = cond.validate_spec(spec(clause("price", "above", 61000.0),
                                     wclause("price", "above", 5.0, 60)))
    check("both kept", len(result["clauses"]), 2)
    check("same window is still a duplicate",
          _raises(cond.validate_spec, spec(wclause("price", "above", 5.0, 60),
                                          wclause("price", "above", 9.0, 60))), True)
    check("different windows are not duplicates",
          len(cond.validate_spec(spec(wclause("price", "above", 5.0, 60),
                                      wclause("price", "above", 9.0, 240)))["clauses"]), 2)


def test_required_windows_names_only_what_the_rule_needs():
    """The engine measures these and nothing else, rather than every window of
    every metric on every cycle."""
    rule = spec(clause("price", "above", 1.0), wclause("price", "below", -5.0, 60),
                wclause("volume_24h", "above", 50.0, 240))
    check("pairs", cond.required_windows(rule),
          [("price", 60), ("volume_24h", 240)])
    check("a level-only rule needs none", cond.required_windows(spec(clause())), [])


def test_a_windowed_clause_reads_the_measured_change_not_the_level():
    rule = spec(wclause("price", "below", -5.0, 60))
    result = cond.evaluate_spec(FULL_ASSET, rule,
                                windows=windows(**{"price@60m": -8.0}))
    check("decided", result["ok"], True)
    check("matched", result["matched"], True)
    # The price itself is 61,500 — far above -5. Reading the level here would
    # have answered a completely different question.
    check("observed is the window change", result["clauses"][0]["observed"], -8.0)


def test_a_window_the_series_cannot_answer_is_undecidable_not_flat():
    """The single most important case: "not enough history" must never resolve
    to "it did not move", or a fall alert stays silent through the fall."""
    rule = spec(wclause("price", "below", -5.0, 60))
    result = cond.evaluate_spec(FULL_ASSET, rule,
                                windows=windows(**{"price@60m": None}))
    check("undecidable", result["ok"], False)
    check("no verdict", result["matched"], None)
    check("reason", result["clauses"][0]["reason"], "window_unavailable")
    check("named to the member",
          "not enough recorded readings" in result["message"], True)


def test_a_missing_window_map_is_undecidable_not_an_error():
    rule = spec(wclause("price", "below", -5.0, 60))
    check("undecidable", cond.evaluate_spec(FULL_ASSET, rule)["ok"], False)


def test_an_and_with_a_matched_level_still_waits_on_its_window():
    """Half a compound rule is not the rule. Answering on the level alone would
    fire an alert the member did not write."""
    rule = spec(clause("price", "above", 1000.0), wclause("price", "below", -5.0, 60))
    result = cond.evaluate_spec(FULL_ASSET, rule, windows=windows(**{"price@60m": None}))
    check("undecidable", result["ok"], False)
    result = cond.evaluate_spec(FULL_ASSET, rule, windows=windows(**{"price@60m": -9.0}))
    check("decided once measured", result["matched"], True)


def test_an_or_already_matched_does_not_wait_on_its_window():
    """Nothing the unmeasured window could have said would change the outcome."""
    rule = spec(clause("price", "above", 1000.0), wclause("price", "below", -5.0, 60),
                logic="or")
    result = cond.evaluate_spec(FULL_ASSET, rule, windows=windows(**{"price@60m": None}))
    check("decided", result["ok"], True)
    check("matched", result["matched"], True)


def test_observations_keep_the_window_and_the_level_apart():
    """Keyed by metric alone, the windowed clause would overwrite the level one
    and the next cycle's crossing would compare against the wrong quantity."""
    rule = spec(clause("price", "above", 1.0), wclause("price", "below", -5.0, 60))
    snapshot = cond.observation_map(FULL_ASSET, rule, windows(**{"price@60m": -8.0}))
    check("level reading", snapshot["price"], 61500.0)
    check("window reading", snapshot["price@60m"], -8.0)


def test_a_windowed_description_names_the_window_and_reads_as_a_percent():
    check("hours", cond.describe_clause(wclause("price", "below", -5.0, 60), "BTC"),
          "BTC price over 1h is below -5.0%")
    check("minutes", cond.describe_clause(wclause("price", "above", 3.0, 15), "BTC"),
          "BTC price over 15m is above 3.0%")
    # A currency metric measured over a window is still a percent change, so it
    # must not be rendered as an amount of money.
    check("volume is a percent too",
          cond.describe_clause(wclause("volume_24h", "above", 40.0, 240), "BTC"),
          "BTC 24h volume over 4h is above 40.0%")


TESTS = [
    test_every_supported_metric_resolves_from_a_board_row,
    test_missing_metric_is_none_and_never_zero,
    test_unusable_raw_values_are_not_observations,
    test_unknown_metric_falls_back_to_price_for_reads,
    test_level_comparators_are_inclusive_of_the_threshold,
    test_crossing_needs_a_prior_observation,
    test_crossing_is_an_edge_not_a_level,
    test_unknown_comparator_is_an_error_not_a_default,
    test_valid_clause_normalizes,
    test_clause_accepts_the_legacy_field_names,
    test_unusable_clauses_are_rejected_at_creation,
    test_spec_validation_bounds_the_rule,
    test_spec_rejects_a_repeated_condition,
    test_spec_defaults_to_and,
    test_and_requires_every_clause,
    test_or_needs_only_one_clause,
    test_an_unknown_metric_makes_the_rule_undecidable,
    test_an_or_already_matched_is_decided_despite_an_unknown,
    test_an_and_already_failed_is_decided_despite_an_unknown,
    test_an_and_still_pending_an_unknown_is_undecidable,
    test_an_or_pending_an_unknown_is_undecidable,
    test_every_metric_unknown_is_undecidable_not_false,
    test_a_failed_quote_is_undecidable_not_a_miss,
    test_compound_crossing_uses_the_per_metric_previous_value,
    test_first_observation_of_a_crossing_rule_is_undecidable,
    test_an_unusable_stored_previous_is_treated_as_absent,
    test_observation_map_records_only_what_the_rule_reads,
    test_observation_map_records_gaps_as_none,
    test_descriptions_read_as_the_member_wrote_them,
    test_description_works_without_a_symbol,
    test_a_window_is_accepted_only_on_a_metric_that_has_one,
    test_a_windowed_crossing_is_rejected_at_creation,
    test_an_unoffered_window_is_rejected_rather_than_snapped,
    test_an_absent_window_leaves_a_plain_level_clause,
    test_a_window_is_part_of_a_clause_identity,
    test_required_windows_names_only_what_the_rule_needs,
    test_a_windowed_clause_reads_the_measured_change_not_the_level,
    test_a_window_the_series_cannot_answer_is_undecidable_not_flat,
    test_a_missing_window_map_is_undecidable_not_an_error,
    test_an_and_with_a_matched_level_still_waits_on_its_window,
    test_an_or_already_matched_does_not_wait_on_its_window,
    test_observations_keep_the_window_and_the_level_apart,
    test_a_windowed_description_names_the_window_and_reads_as_a_percent,
]


def main():
    for test in TESTS:
        print(f"\n{test.__name__}")
        try:
            test()
        except AssertionError:
            # Already recorded by `check`; keep going so one run reports every
            # broken invariant rather than only the first.
            pass
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"PASSED — {len(TESTS)} tests, all assertions green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
