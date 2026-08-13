"""Transparency, campaign diagnosis and recommendations.

The properties defended here:

* **"Why am I seeing this ad" is read from the recorded decision**, not
  reconstructed afterwards. A plausible reason that is not the real reason is
  the specific failure this module exists to prevent.

* **One person cannot read another's explanation**, and a decision belonging to
  somebody else is indistinguishable from one that does not exist.

* **Findings are ordered by what blocks the most**, and thin data produces "not
  enough to say" rather than an empty list that reads as "nothing is wrong".

* **No recommendation can move money.** Not at any autonomy level, not with any
  argument, and the module has no apply path at all.

    python tests/business_os/test_ads_intelligence_diagnostics.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_diag_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-diag")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import inspect  # noqa: E402
import json  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import (  # noqa: E402
    diagnostics, recommendations, taxonomy, transparency)
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def setup_module(module=None):
    ensure_schema()


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ago(**kwargs):
    return _iso(datetime.now(timezone.utc) - timedelta(**kwargs))


def _clear():
    conn = db.connect()
    try:
        for table in ("ads_intel_events", "ads_intel_delivery_decisions",
                      "ads_intel_interest_affinity"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _decision(conn, *, decision_id=None, subject="subj-1", campaign="camp-1",
              creative="cr-1", filled=1, mode="intelligence_v1",
              breakdown=None, exploration=0, no_fill=None, occurred=None):
    decision_id = decision_id or f"d-{uuid.uuid4().hex[:10]}"
    occurred = occurred or _ago(hours=1)
    conn.execute(
        "INSERT INTO ads_intel_delivery_decisions (decision_id, "
        "opportunity_id, occurred_at, subject_ref, placement_key, filled, "
        "no_fill_reason, campaign_id, creative_id, score, "
        "score_breakdown_json, ranking_mode, exploration, created_at) "
        "VALUES (?, ?, ?, ?, 'feed', ?, ?, ?, ?, 0.5, ?, ?, ?, ?)",
        (decision_id, f"opp-{decision_id}", occurred, subject, filled, no_fill,
         campaign if filled else None, creative if filled else None,
         json.dumps(breakdown) if breakdown else None, mode, exploration,
         occurred))
    return decision_id


def _affinity(conn, subject, category, score=0.8):
    conn.execute(
        "INSERT INTO ads_intel_interest_affinity (affinity_id, subject_ref, "
        "category, window_days, score, signal_count, last_signal_at, "
        "computed_at) VALUES (?, ?, ?, 30, ?, 5, ?, ?)",
        (f"af-{uuid.uuid4().hex[:10]}", subject, category, score,
         _ago(hours=2), _ago(hours=2)))


# --------------------------------------------------------------------------- #
# Transparency
# --------------------------------------------------------------------------- #

def test_the_explanation_comes_from_the_recorded_decision():
    """The score that was actually used, not a story assembled afterwards."""
    _clear()
    conn = db.connect()
    try:
        decision = _decision(conn, breakdown={"context": 0.02,
                                              "affinity": 0.28,
                                              "quality": 0.02,
                                              "exploration": 0.0})
        conn.commit()
        result = transparency.explain_delivery(conn, decision,
                                               subject_ref="subj-1")
    finally:
        conn.close()
    _assert(result["found"] is True, result)
    _assert(len(result["reasons"]) == 1,
            f"reasons that did not cause the placement were listed: {result}")
    _assert("engaged with similar" in result["reasons"][0], result)


def test_the_biggest_component_is_named_first():
    _clear()
    conn = db.connect()
    try:
        decision = _decision(conn, breakdown={"context": 0.30,
                                              "affinity": 0.10,
                                              "quality": 0.05})
        conn.commit()
        result = transparency.explain_delivery(conn, decision,
                                               subject_ref="subj-1")
    finally:
        conn.close()
    _assert("content you were looking at" in result["reasons"][0], result)


def test_a_legacy_decision_admits_it_does_not_know():
    """An honest blank beats a plausible invention."""
    _clear()
    conn = db.connect()
    try:
        decision = _decision(conn, mode="legacy", breakdown=None)
        conn.commit()
        result = transparency.explain_delivery(conn, decision,
                                               subject_ref="subj-1")
    finally:
        conn.close()
    _assert(result["found"] is True, result)
    _assert("do not have a detailed reason" in result["reasons"][0], result)


def test_another_persons_explanation_is_not_readable():
    _clear()
    conn = db.connect()
    try:
        decision = _decision(conn, subject="subj-A")
        conn.commit()
        theirs = transparency.explain_delivery(conn, decision,
                                               subject_ref="subj-B")
        missing = transparency.explain_delivery(conn, "d-does-not-exist",
                                                subject_ref="subj-B")
    finally:
        conn.close()
    _assert(theirs["found"] is False, theirs)
    _assert(theirs == missing,
            "a decision belonging to somebody else answers differently from "
            "one that does not exist, which confirms it is real")


def test_an_explanation_never_names_another_person():
    _clear()
    conn = db.connect()
    try:
        decision = _decision(conn, breakdown={"affinity": 0.3})
        conn.commit()
        result = transparency.explain_delivery(conn, decision,
                                               subject_ref="subj-1")
    finally:
        conn.close()
    text = json.dumps(result).lower()
    for leak in ("friend", "people like you", "others who", "your contacts",
                 "someone you follow"):
        _assert(leak not in text, f"the explanation contains {leak!r}: {text}")


def test_an_explanation_always_offers_a_control_that_works():
    _clear()
    conn = db.connect()
    try:
        result = transparency.explain_delivery(conn, "missing",
                                               subject_ref="subj-1")
    finally:
        conn.close()
    _assert(result["controls"], "even a not-found answer must offer controls")
    for control in transparency.CONTROLS:
        _assert(control["event"] in taxonomy.EXPLICIT_NEGATIVE_EVENTS,
                f"{control['action']} logs {control['event']}, which the "
                f"interest graph is not required to honour")


def test_a_category_outside_the_taxonomy_is_never_echoed_back():
    _clear()
    conn = db.connect()
    try:
        _affinity(conn, "subj-x", "fitness")
        _affinity(conn, "subj-x", "recently_bereaved")
        conn.commit()
        disclosure = transparency.interest_disclosure(conn, "subj-x")
    finally:
        conn.close()
    _assert("fitness" in disclosure["categories"], disclosure)
    _assert("recently_bereaved" not in disclosure["categories"],
            f"an off-taxonomy category was disclosed: {disclosure}")


def test_the_disclosure_shows_categories_and_not_scores():
    _clear()
    conn = db.connect()
    try:
        _affinity(conn, "subj-y", "travel", score=0.6231)
        conn.commit()
        disclosure = transparency.interest_disclosure(conn, "subj-y")
    finally:
        conn.close()
    _assert(disclosure["categories"] == ["travel"], disclosure)
    _assert("0.62" not in json.dumps(disclosure),
            f"a raw score leaked into the disclosure: {disclosure}")


def test_a_broken_transparency_read_does_not_leak_an_answer():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("down")

    result = transparency.explain_delivery(_Broken(), "d-1",
                                           subject_ref="subj-1")
    _assert(result["found"] is False, result)
    _assert(result["degraded"] is True, result)


def test_the_rendered_text_states_what_was_not_done():
    _clear()
    conn = db.connect()
    try:
        decision = _decision(conn, breakdown={"context": 0.3})
        conn.commit()
        text = transparency.render(
            transparency.explain_delivery(conn, decision,
                                          subject_ref="subj-1"))
    finally:
        conn.close()
    _assert("sensitive categories" in text, text)
    _assert("identity" in text, text)


# --------------------------------------------------------------------------- #
# Diagnosis
# --------------------------------------------------------------------------- #

def test_a_campaign_that_never_served_is_told_the_blocking_reason():
    _clear()
    conn = db.connect()
    try:
        for _ in range(20):
            _decision(conn, filled=0, no_fill="ACCOUNT_UNVERIFIED")
        conn.commit()
        result = diagnostics.diagnose(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["delivering"] is False, result)
    primary = result["primary"]
    _assert(primary["severity"] == "blocking", primary)
    _assert("verif" in primary["headline"].lower(), primary)
    _assert(primary["fixable_by"] == diagnostics.ACTOR_ADVERTISER, primary)


def test_a_platform_caused_block_is_not_blamed_on_the_advertiser():
    _clear()
    conn = db.connect()
    try:
        for _ in range(20):
            _decision(conn, filled=0, no_fill="CAMPAIGN_IN_REVIEW")
        conn.commit()
        result = diagnostics.diagnose(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["primary"]["fixable_by"] == diagnostics.ACTOR_PLATFORM,
            result["primary"])


def test_a_blocking_finding_outranks_an_efficiency_one():
    """Fixing an audience while the campaign cannot serve teaches an advertiser
    that our advice does not work."""
    findings = [
        diagnostics.finding("A", severity="efficiency", headline="h",
                            detail="d", evidence={},
                            actor=diagnostics.ACTOR_ADVERTISER),
        diagnostics.finding("B", severity="blocking", headline="h",
                            detail="d", evidence={},
                            actor=diagnostics.ACTOR_ADVERTISER),
        diagnostics.finding("C", severity="limiting", headline="h",
                            detail="d", evidence={},
                            actor=diagnostics.ACTOR_ADVERTISER),
    ]
    ranks = sorted(findings, key=lambda f: -f["severity_rank"])
    _assert([f["code"] for f in ranks] == ["B", "C", "A"], ranks)


def test_thin_delivery_says_so_rather_than_returning_nothing():
    """An empty list reads as 'nothing is wrong', which is a claim."""
    _clear()
    conn = db.connect()
    try:
        for _ in range(5):
            _decision(conn, filled=1)
        conn.commit()
        result = diagnostics.diagnose(conn, "camp-1")
    finally:
        conn.close()
    codes = [f["code"] for f in result["findings"]]
    _assert("NOT_ENOUGH_DELIVERY" in codes, result)
    _assert(result["primary"] is not None, result)


def test_a_guess_is_labelled_as_a_guess():
    _clear()
    conn = db.connect()
    try:
        for _ in range(20):
            _decision(conn, filled=0, no_fill="AUDIENCE_MISMATCH")
        conn.commit()
        result = diagnostics.diagnose(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["primary"]["confidence"] == "inferred",
            f"a platform-wide inference was presented as measured: "
            f"{result['primary']}")


def test_every_finding_carries_evidence_and_an_actor():
    _clear()
    conn = db.connect()
    try:
        for _ in range(20):
            _decision(conn, filled=0, no_fill="WALLET_EMPTY")
        conn.commit()
        result = diagnostics.diagnose(conn, "camp-1")
    finally:
        conn.close()
    for item in result["findings"]:
        for key in ("code", "severity", "headline", "detail", "evidence",
                    "fixable_by", "confidence"):
            _assert(key in item, f"{item.get('code')} is missing {key}")
        _assert(item["fixable_by"] in (diagnostics.ACTOR_ADVERTISER,
                                       diagnostics.ACTOR_PLATFORM,
                                       diagnostics.ACTOR_NOBODY), item)
        _assert(item["confidence"] in ("measured", "inferred"), item)


def test_one_failing_source_does_not_silence_the_whole_diagnosis():
    """A campaign whose fatigue read fails should still hear it is unverified."""
    _clear()
    conn = db.connect()
    try:
        for _ in range(20):
            _decision(conn, filled=0, no_fill="ACCOUNT_UNVERIFIED")
        conn.commit()
        result = diagnostics.diagnose(conn, "camp-1", creative_id="cr-broken")
    finally:
        conn.close()
    _assert(result["findings"], "the diagnosis went silent")
    _assert(result["primary"]["severity"] == "blocking", result["primary"])


def test_the_diagnosis_never_invents_a_win_rate():
    """The decision log records the winner only, so the denominator is absent."""
    body = inspect.getsource(diagnostics).split('"""', 2)[-1]
    for invented in ("win_rate", "win_share", "auction_participation"):
        _assert(invented not in body,
                f"diagnostics computes {invented!r} from a denominator it does "
                f"not have")


def test_diagnostics_has_no_write_path():
    body = inspect.getsource(diagnostics).split('"""', 2)[-1]
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "conn.commit"):
        _assert(verb not in body, f"diagnostics writes: it contains {verb!r}")


# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #

def test_no_autonomy_level_can_spend_money():
    """The property that matters most, checked at every level including
    hypothetical ones above the defined maximum."""
    for action in recommendations.MONEY_ACTIONS:
        for level in list(recommendations.AUTONOMY_LEVELS) + [4, 99, 10**6]:
            _assert(not recommendations.may_apply_automatically(
                action, autonomy_level=level),
                f"{action} became automatic at autonomy level {level}")


def test_pausing_is_treated_as_a_money_action():
    """It saves money, which is not the same as being safe to do unasked."""
    _assert("pause_campaign" in recommendations.MONEY_ACTIONS)
    _assert(recommendations.max_autonomy_for("pause_campaign")
            == recommendations.LEVEL_RECOMMEND)


def test_an_unknown_action_defaults_to_needing_a_person():
    _assert(recommendations.max_autonomy_for("do_something_novel")
            == recommendations.LEVEL_RECOMMEND)
    _assert(not recommendations.may_apply_automatically(
        "do_something_novel", autonomy_level=recommendations.LEVEL_AUTO))


def test_assisting_is_not_acting_alone():
    _assert(not recommendations.may_apply_automatically(
        "rotate_creative", autonomy_level=recommendations.LEVEL_ASSIST),
        "LEVEL_ASSIST acted without approval")
    _assert(recommendations.may_apply_automatically(
        "rotate_creative", autonomy_level=recommendations.LEVEL_AUTO))


def test_the_module_has_no_apply_path():
    body = inspect.getsource(recommendations).split('"""', 2)[-1]
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "def apply",
                 "conn.commit", "charge(", "debit(", "credit("):
        _assert(verb not in body,
                f"recommendations can act on its own advice: it contains "
                f"{verb!r}")


def test_advice_does_not_change_with_what_the_system_may_do():
    """A system that recommends differently when allowed to act is widening
    its own remit."""
    _clear()
    conn = db.connect()
    try:
        for _ in range(20):
            _decision(conn, filled=0, no_fill="AUDIENCE_MISMATCH")
        conn.commit()
        low = recommendations.for_campaign(
            conn, "camp-1", autonomy_level=recommendations.LEVEL_OBSERVE)
        high = recommendations.for_campaign(
            conn, "camp-1", autonomy_level=recommendations.LEVEL_AUTO)
    finally:
        conn.close()
    _assert([r["code"] for r in low["recommendations"]]
            == [r["code"] for r in high["recommendations"]],
            f"advice changed with autonomy: {low} vs {high}")


def test_a_spend_changing_proposal_says_so_out_loud():
    proposal = recommendations.recommendation(
        "X", action="increase_daily_budget", headline="h", rationale="r",
        finding_code="F", evidence={})
    _assert(proposal["affects_spend"] is True, proposal)
    _assert(proposal["requires_human"] is True, proposal)
    _assert("yours to decide" in recommendations.explain(proposal))


def test_a_finding_with_no_rule_produces_no_generic_advice():
    _assert(recommendations.for_finding({"code": "SOMETHING_NEW"}) is None)
    _assert(recommendations.for_finding({}) is None)


def test_every_rule_maps_to_an_action_the_module_knows():
    known = (recommendations.MONEY_ACTIONS | recommendations.REVERSIBLE_ACTIONS
             | {"none", "wait", "verify_account"})
    for code, rule in recommendations._RULES.items():
        _assert(rule[1] in known,
                f"{code} proposes the unknown action {rule[1]!r}")


def test_every_diagnostic_finding_code_has_a_rule_or_is_deliberate():
    """Findings without advice are allowed, but must be a decision rather than
    an oversight — so the ones we know about are listed here explicitly."""
    deliberate = {"NOT_DELIVERING_UNKNOWN", "NOT_DELIVERING_POLICY_BLOCKED",
                  "NOT_DELIVERING_SCHEDULE_INACTIVE"}
    codes = {f"NOT_DELIVERING_{code}"
             for code in diagnostics.NOT_DELIVERING_CAUSES}
    codes.add("NOT_DELIVERING_UNKNOWN")
    for state in ("REJECTED", "FATIGUED", "WEARING"):
        codes.add(f"CREATIVE_{state}")
    for step in ("SERVED", "RENDERED", "VIEWABLE", "CLICK", "CONVERSION"):
        codes.add(f"FUNNEL_DROP_{step}")
    codes |= {"BUDGET_EXHAUSTED", "PACING_THROTTLED", "UNDERSPENDING",
              "NOT_ENOUGH_DELIVERY", "INVALID_TRAFFIC_EXCLUDED"}
    missing = sorted(c for c in codes
                     if c not in recommendations._RULES and c not in deliberate)
    _assert(not missing, f"findings with no advice and no decision: {missing}")


def test_a_paced_campaign_is_told_not_to_raise_its_budget():
    """The intuitive fix is the wrong one here, so the rule must say so."""
    proposal = recommendations.for_finding(
        {"code": "PACING_THROTTLED", "evidence": {}})
    _assert(proposal["action"] == "none", proposal)
    _assert("not a fault" in proposal["rationale"], proposal)


def test_an_underspending_campaign_is_not_told_to_add_budget():
    proposal = recommendations.for_finding(
        {"code": "UNDERSPENDING", "evidence": {}})
    _assert(proposal["affects_spend"] is False, proposal)
    _assert("would not be spent" in proposal["rationale"], proposal)


def test_a_recommendation_carries_the_evidence_it_was_built_from():
    proposal = recommendations.for_finding(
        {"code": "CREATIVE_FATIGUED", "evidence": {"drop": 0.55},
         "confidence": "measured"})
    _assert(proposal["evidence"] == {"drop": 0.55}, proposal)
    _assert(proposal["from_finding"] == "CREATIVE_FATIGUED", proposal)
    _assert(proposal["version"] == taxonomy.RECOMMENDATION_VERSION, proposal)


def _main():
    setup_module()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
