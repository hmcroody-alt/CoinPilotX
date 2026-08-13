"""Ads intelligence — delivery decision and no-fill recording.

The gap this closes: ``request_placement`` used to return "no eligible
candidate" and leave no trace, so a campaign that never delivered looked exactly
like a campaign nobody requested. These tests pin the behaviour that makes the
difference answerable — and, just as importantly, pin the guarantees that let
measurement be switched on in production without risking delivery.

The safety properties are the ones worth being strict about:

* **Default inert.** Both flags unset means nothing is recorded at all.
* **Observer only.** ``record_safely`` swallows every failure, because a
  measurement bug must cost a log line, not an ad request.
* **Closed vocabulary.** An unmapped eligibility reason collapses to
  ``NO_ELIGIBLE_CAMPAIGN`` instead of quietly inventing a new no-fill reason
  that admin screens and UNDX explanations would then have to understand.

    python tests/business_os/test_ads_intelligence_decisions.py
"""

import json
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_dec_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os import ads_intelligence as ai  # noqa: E402
from services.business_os.ads_intelligence import decisions as dec  # noqa: E402
from services.business_os.ads_intelligence import taxonomy  # noqa: E402
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402

_FLAGS = ("BUSINESS_OS_ADS_INTELLIGENCE", "BUSINESS_OS_ADS_INTELLIGENCE_MEASUREMENT")


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def setup_module(module=None):
    ensure_schema()


def _clear_flags():
    for key in _FLAGS:
        os.environ.pop(key, None)


# --------------------------------------------------------------------------- #
# Flag gating
# --------------------------------------------------------------------------- #

def test_default_posture_is_inert():
    _clear_flags()
    try:
        _assert(ai.is_enabled() is False, "master flag must default off")
        _assert(ai.measurement_enabled() is False,
                "measurement must default off")
    finally:
        _clear_flags()


def test_measurement_flag_is_independent_of_the_master_flag():
    # The staged rollout turns measurement on long before anything is allowed
    # to influence delivery, so these must be separable.
    _clear_flags()
    os.environ["BUSINESS_OS_ADS_INTELLIGENCE_MEASUREMENT"] = "on"
    try:
        _assert(ai.measurement_enabled() is True)
        _assert(ai.is_enabled() is False,
                "measurement must not imply full enablement")
    finally:
        _clear_flags()


def test_master_flag_implies_measurement():
    _clear_flags()
    os.environ["BUSINESS_OS_ADS_INTELLIGENCE"] = "on"
    try:
        _assert(ai.measurement_enabled() is True)
    finally:
        _clear_flags()


def test_unrecognised_flag_values_are_off():
    _clear_flags()
    for value in ("", "0", "off", "false", "maybe", "disabled"):
        os.environ["BUSINESS_OS_ADS_INTELLIGENCE"] = value
        _assert(ai.is_enabled() is False, f"{value!r} must not enable")
    _clear_flags()


# --------------------------------------------------------------------------- #
# Reason mapping
# --------------------------------------------------------------------------- #

def test_eligibility_reasons_map_to_the_closed_taxonomy():
    cases = {
        "advertiser_ineligible:unverified": "ACCOUNT_UNVERIFIED",
        "advertiser_ineligible:suspended": "POLICY_BLOCKED",
        "hierarchy:campaign_pending": "CAMPAIGN_IN_REVIEW",
        "placement_incompatible": "PLACEMENT_UNSUPPORTED",
        "placement_not_selected": "PLACEMENT_UNSUPPORTED",
        "audience_mismatch": "AUDIENCE_MISMATCH",
        "campaign_schedule_inactive": "SCHEDULE_INACTIVE",
        "ad_set_schedule_inactive": "SCHEDULE_INACTIVE",
        "frequency_cap_reached": "FREQUENCY_CAPPED",
        "media_unavailable": "CREATIVE_UNAVAILABLE",
        "destination_invalid": "CREATIVE_UNAVAILABLE",
    }
    for raw, expected in cases.items():
        actual = dec.map_exclusion_reason(raw)
        _assert(actual == expected, f"{raw} -> {actual}, expected {expected}")


def test_longest_prefix_wins():
    # `advertiser_ineligible:suspended` must not be captured by the shorter
    # `advertiser_ineligible:` fallback.
    _assert(dec.map_exclusion_reason("advertiser_ineligible:suspended")
            == "POLICY_BLOCKED")


def test_unknown_reasons_do_not_grow_the_vocabulary():
    mapped = dec.map_exclusion_reason("some_new_gate_nobody_told_us_about")
    _assert(mapped == "NO_ELIGIBLE_CAMPAIGN", mapped)
    _assert(mapped in taxonomy.NO_FILL_REASON_SET)


def test_every_mapped_reason_is_in_the_taxonomy():
    for _prefix, mapped in dec._REASON_MAP:
        _assert(mapped in taxonomy.NO_FILL_REASON_SET,
                f"{mapped} is not a declared no-fill reason")


# --------------------------------------------------------------------------- #
# No-fill inference
# --------------------------------------------------------------------------- #

def test_modal_reason_wins_not_the_first_one():
    """With many candidates the common cause is the actionable one.

    Taking the first would just report whichever campaign happened to be
    enumerated first, which is an artefact of query order.
    """
    decisions = [
        {"eligible": False, "reasons": ["audience_mismatch"]},
        {"eligible": False, "reasons": ["frequency_cap_reached"]},
        {"eligible": False, "reasons": ["frequency_cap_reached"]},
    ]
    _assert(dec.infer_no_fill_reason(decisions, candidate_count=3)
            == "FREQUENCY_CAPPED")


def test_no_candidates_at_all_is_distinct_from_all_excluded():
    _assert(dec.infer_no_fill_reason([], candidate_count=0)
            == "NO_ELIGIBLE_CAMPAIGN")


def test_eligible_but_unfilled_is_inventory_not_targeting():
    # Candidates existed and passed every gate, yet nothing was served: that is
    # selection or ad load declining, not a targeting problem.
    _assert(dec.infer_no_fill_reason([{"eligible": True}], candidate_count=1)
            == "INVENTORY_UNAVAILABLE")


def test_exclusion_counts_preserve_the_raw_reasons():
    decisions = [
        {"eligible": False, "reasons": ["audience_mismatch", "media_unavailable"]},
        {"eligible": False, "reasons": ["audience_mismatch"]},
        {"eligible": True, "reasons": []},
    ]
    counts = dec.summarise_exclusions(decisions)
    _assert(counts == {"audience_mismatch": 2, "media_unavailable": 1}, counts)


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #

def test_no_fill_opportunity_is_recorded_with_a_cause():
    decisions = [{"eligible": False, "reasons": ["frequency_cap_reached"]}] * 2
    decision_id = dec.record_decision(
        placement_key="feed", surface="feed", subject_ref="subj-1",
        selection={"candidate_count": 2, "eligible_count": 0,
                   "decisions": decisions})
    conn = db.connect()
    try:
        row = dec.load_decision(conn, decision_id)
    finally:
        conn.close()
    _assert(row is not None, "no row written")
    _assert(row["filled"] == 0, row)
    _assert(row["no_fill_reason"] == "FREQUENCY_CAPPED", row)
    _assert(row["candidate_count"] == 2 and row["eligible_count"] == 0, row)
    _assert(json.loads(row["exclusion_counts_json"])["frequency_cap_reached"] == 2,
            row)


def test_filled_opportunity_records_the_winner():
    decision_id = dec.record_decision(
        placement_key="feed", subject_ref="subj-2",
        selection={"candidate_count": 3, "eligible_count": 2,
                   "decisions": [{"eligible": True}, {"eligible": True}]},
        winner={"campaign_id": "camp-9", "creative_id": "cre-9"},
        latency_ms=17)
    conn = db.connect()
    try:
        row = dec.load_decision(conn, decision_id)
    finally:
        conn.close()
    _assert(row["filled"] == 1, row)
    _assert(row["no_fill_reason"] is None, "a filled opportunity has no cause")
    _assert(row["campaign_id"] == "camp-9" and row["creative_id"] == "cre-9", row)
    _assert(row["latency_ms"] == 17, row)


def test_forced_reason_overrides_inference():
    # A winner whose creative row cannot be loaded is a data-integrity fault,
    # and must not be filed as a generic targeting miss.
    decision_id = dec.record_decision(
        placement_key="feed", subject_ref="subj-3",
        selection={"candidate_count": 1, "eligible_count": 1,
                   "decisions": [{"eligible": True}]},
        forced_no_fill_reason="CREATIVE_UNAVAILABLE")
    conn = db.connect()
    try:
        row = dec.load_decision(conn, decision_id)
    finally:
        conn.close()
    _assert(row["no_fill_reason"] == "CREATIVE_UNAVAILABLE", row)


def test_forced_reason_outside_the_taxonomy_is_refused():
    decision_id = dec.record_decision(
        placement_key="feed", selection={"candidate_count": 0},
        forced_no_fill_reason="SOMETHING_INVENTED")
    conn = db.connect()
    try:
        row = dec.load_decision(conn, decision_id)
    finally:
        conn.close()
    _assert(row["no_fill_reason"] == "NO_ELIGIBLE_CAMPAIGN", row)


def test_opportunity_id_defaults_to_the_decision_id():
    decision_id = dec.record_decision(placement_key="feed",
                                      selection={"candidate_count": 0})
    conn = db.connect()
    try:
        row = dec.load_decision(conn, decision_id)
    finally:
        conn.close()
    _assert(row["opportunity_id"] == decision_id, row)


def test_record_safely_never_raises():
    """The property that makes Stage 1 safe to switch on.

    A measurement failure must cost a log line, never an ad request.
    """
    _assert(dec.record_safely(selection="not-a-dict") is None)
    _assert(dec.record_safely(selection={"decisions": "nonsense"}) is None)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def test_no_fill_breakdown_reports_fill_rate_and_causes():
    placement = "breakdown-probe"
    dec.record_decision(placement_key=placement,
                        selection={"candidate_count": 1, "eligible_count": 1,
                                   "decisions": [{"eligible": True}]},
                        winner={"campaign_id": "c1", "creative_id": "r1"})
    for _ in range(3):
        dec.record_decision(
            placement_key=placement,
            selection={"candidate_count": 1, "eligible_count": 0,
                       "decisions": [{"eligible": False,
                                      "reasons": ["audience_mismatch"]}]})
    conn = db.connect()
    try:
        report = dec.no_fill_breakdown(conn, placement_key=placement)
    finally:
        conn.close()
    _assert(report["opportunities"] == 4, report)
    _assert(report["filled"] == 1 and report["unfilled"] == 3, report)
    _assert(abs(report["fill_rate"] - 0.25) < 1e-9, report)
    _assert(report["reasons"] == {"AUDIENCE_MISMATCH": 3}, report)


def test_breakdown_of_an_unused_placement_is_empty_not_broken():
    conn = db.connect()
    try:
        report = dec.no_fill_breakdown(conn, placement_key="never-requested")
    finally:
        conn.close()
    _assert(report["opportunities"] == 0, report)
    _assert(report["fill_rate"] is None,
            "a fill rate over zero opportunities is not 0%, it is unknown")


def _main():
    setup_module()
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001 — standalone runner
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
