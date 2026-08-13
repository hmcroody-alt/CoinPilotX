"""Invalid traffic, attribution and the delivery funnel.

The properties defended here:

* **The invalid-traffic engine is a ratchet.** It can withdraw billability and
  never grant it, and it cannot reverse a charge — it can only produce a list
  for the financial system to act on.

* **Attribution is last-click only.** No view-through, no modelling, no
  cross-device. An unattributed conversion is reported as unattributed rather
  than quietly credited.

* **Advertiser-reported value stays labelled as theirs**, and is never divided
  by spend to manufacture a ROAS.

* **The funnel names the step**, because a percentage nobody can act on is not
  a diagnostic.

    python tests/business_os/test_ads_intelligence_attribution.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_attr_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-attr")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import inspect  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import (  # noqa: E402
    attribution, events, invalid_traffic, taxonomy)
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
        conn.execute("DELETE FROM ads_intel_events")
        conn.execute("DELETE FROM ads_intel_delivery_decisions")
        conn.commit()
    finally:
        conn.close()


def _event(conn, *, name, subject="subj-1", campaign="camp-1",
           creative="cr-1", decision=None, occurred=None, validity="valid",
           billable=None, value_cents=None, quality_notes=None):
    event_id = f"ev-{uuid.uuid4().hex[:12]}"
    occurred = occurred or _iso(datetime.now(timezone.utc))
    family = ("conversion" if name in taxonomy.CONVERSION_EVENTS
              else "opportunity" if name in taxonomy.OPPORTUNITY_EVENTS
              else "delivery" if name in taxonomy.DELIVERY_EVENTS
              else "engagement")
    if billable is None:
        billable = 1 if name in taxonomy.BILLABLE_CANDIDATE_EVENTS else 0
    conn.execute(
        "INSERT INTO ads_intel_events (event_id, dedup_key, event_name, "
        "event_family, occurred_at, received_at, subject_ref, decision_id, "
        "campaign_id, creative_id, value_cents, validity, billable, "
        "quality_status, quality_notes, schema_version, processing_version, "
        "ingest_source, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?, 1, 1, 'server', ?)",
        (event_id, event_id, name, family, occurred, occurred, subject,
         decision, campaign, creative, value_cents, validity, billable,
         quality_notes, occurred))
    return event_id


def _decision(conn, *, decision_id, campaign="camp-1", creative="cr-1",
              occurred=None, filled=1):
    occurred = occurred or _iso(datetime.now(timezone.utc))
    conn.execute(
        "INSERT INTO ads_intel_delivery_decisions (decision_id, "
        "opportunity_id, occurred_at, subject_ref, filled, campaign_id, "
        "creative_id, created_at) VALUES (?, ?, ?, 'subj-1', ?, ?, ?, ?)",
        (decision_id, f"opp-{decision_id}", occurred, filled, campaign,
         creative, occurred))
    return decision_id


# --------------------------------------------------------------------------- #
# The ratchet
# --------------------------------------------------------------------------- #

def test_the_engine_can_never_grant_billability():
    """The single most important property in this module.

    A detector that can set ``billable = 1`` is a detector that can be talked
    into charging for fraud. The literal is checked in the source rather than
    only in behaviour, because a behavioural test only covers the paths it
    happens to exercise.
    """
    source = inspect.getsource(invalid_traffic)
    body = source.split('"""', 2)[-1]
    _assert("billable = 0" in body, "the ratchet does not clear billable")
    for grant in ("billable = 1", "billable=1", "billable = ?"):
        _assert(grant not in body,
                f"the invalid-traffic engine can set billability via {grant!r}")


def test_the_engine_never_writes_to_the_canonical_billing_tables():
    body = inspect.getsource(invalid_traffic).split('"""', 2)[-1]
    for table in ("business_os_ad_impression_events",
                  "business_os_ad_click_events",
                  "business_os_ad_wallet", "business_os_ad_transactions"):
        for verb in ("INSERT INTO " + table, "UPDATE " + table,
                     "DELETE FROM " + table):
            _assert(verb not in body,
                    f"invalid traffic writes to a canonical table: {verb}")


def test_a_credit_is_proposed_and_never_issued():
    """Reconciliation gets a list, not a refund."""
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", validity="invalid",
               occurred=_ago(minutes=5))
        conn.commit()
        candidates = invalid_traffic.credit_candidates(conn,
                                                       campaign_id="camp-1")
    finally:
        conn.close()
    _assert(len(candidates) == 1, candidates)
    _assert(candidates[0]["action"] == "review_for_credit", candidates[0])
    body = inspect.getsource(invalid_traffic.credit_candidates)
    for verb in ("INSERT", "UPDATE", "DELETE", "commit("):
        _assert(verb not in body,
                f"credit_candidates writes: it contains {verb!r}")


def test_severity_only_moves_one_way():
    _assert(invalid_traffic.is_more_severe("invalid", "valid"))
    _assert(invalid_traffic.is_more_severe("suspect", "valid"))
    _assert(invalid_traffic.is_more_severe("invalid", "suspect"))
    _assert(not invalid_traffic.is_more_severe("valid", "suspect"))
    _assert(not invalid_traffic.is_more_severe("valid", "invalid"))
    _assert(not invalid_traffic.is_more_severe("suspect", "invalid"))
    _assert(not invalid_traffic.is_more_severe("valid", "valid"))


def test_an_unclassified_rule_withholds_billing_rather_than_alleging_fraud():
    """A rule added without a state entry must fail toward the safe side."""
    _assert(invalid_traffic.state_for_reason("SOME_NEW_RULE") == "suspect")
    _assert(invalid_traffic.state_for_reason("IMPOSSIBLE_SEQUENCE") == "invalid")


def test_every_rule_state_is_a_real_validity_state():
    for reason, state in invalid_traffic.RULE_STATES.items():
        _assert(state in taxonomy.VALIDITY_STATES,
                f"{reason} produces {state!r}, which is not a validity state")
        _assert(reason in taxonomy.INVALID_REASONS,
                f"{reason} is not in the taxonomy's reason list")


def test_a_ruling_cannot_be_walked_back_by_a_second_sweep():
    """An event already ruled invalid stays invalid when a softer rule matches."""
    _clear()
    conn = db.connect()
    try:
        event_id = _event(conn, name="ad_click", validity="invalid",
                          occurred=_ago(minutes=5))
        conn.commit()
        invalid_traffic._apply(conn, event_id, "suspect", "RAPID_REPEAT")
        conn.commit()
        row = conn.execute("SELECT validity FROM ads_intel_events "
                           "WHERE event_id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    _assert(row[0] == "invalid", f"a softer rule downgraded to {row[0]}")


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

def test_a_click_on_an_ad_that_was_never_displayed_is_invalid():
    _clear()
    conn = db.connect()
    try:
        _decision(conn, decision_id="d-ghost")
        event_id = _event(conn, name="ad_click", decision="d-ghost",
                          occurred=_ago(minutes=5))
        conn.commit()
        result = invalid_traffic.sweep(conn)
        row = conn.execute("SELECT validity, invalid_reason, billable "
                           "FROM ads_intel_events WHERE event_id = ?",
                           (event_id,)).fetchone()
    finally:
        conn.close()
    _assert(result["reclassified"] == 1, result)
    _assert(row[0] == "invalid", row)
    _assert(row[1] == "IMPOSSIBLE_SEQUENCE", row)
    _assert(int(row[2]) == 0, f"an impossible click is still billable: {row}")


def test_an_event_naming_a_decision_we_never_made_is_invalid():
    _clear()
    conn = db.connect()
    try:
        event_id = _event(conn, name="ad_viewable", decision="d-nonexistent",
                          occurred=_ago(minutes=5))
        conn.commit()
        invalid_traffic.sweep(conn)
        row = conn.execute("SELECT validity, invalid_reason FROM "
                           "ads_intel_events WHERE event_id = ?",
                           (event_id,)).fetchone()
    finally:
        conn.close()
    _assert(row[0] == "invalid", row)
    _assert(row[1] == "UNKNOWN_DECISION", row)


def test_an_event_that_disagrees_with_its_decision_is_invalid():
    _clear()
    conn = db.connect()
    try:
        _decision(conn, decision_id="d-1", campaign="camp-1")
        event_id = _event(conn, name="ad_viewable", decision="d-1",
                          campaign="camp-OTHER", occurred=_ago(minutes=5))
        conn.commit()
        invalid_traffic.sweep(conn)
        row = conn.execute("SELECT validity, invalid_reason FROM "
                           "ads_intel_events WHERE event_id = ?",
                           (event_id,)).fetchone()
    finally:
        conn.close()
    _assert(row[1] == "DECISION_MISMATCH", row)


def test_a_burst_of_activity_is_suspect_rather_than_invalid():
    """Velocity is a plausibility judgement, so it must not allege fraud."""
    _clear()
    conn = db.connect()
    try:
        _decision(conn, decision_id="d-v")
        _event(conn, name="ad_served", decision="d-v", occurred=_ago(minutes=5))
        for _ in range(invalid_traffic.VELOCITY_LIMIT + 5):
            _event(conn, name="ad_click", subject="subj-fast", decision="d-v",
                   occurred=_ago(minutes=5))
        conn.commit()
        invalid_traffic.sweep(conn)
        rows = conn.execute(
            "SELECT validity, COUNT(*) FROM ads_intel_events "
            "WHERE subject_ref = 'subj-fast' GROUP BY validity").fetchall()
    finally:
        conn.close()
    states = {r[0]: int(r[1]) for r in rows}
    _assert(states.get("suspect", 0) > invalid_traffic.VELOCITY_LIMIT, states)
    _assert("invalid" not in states,
            f"a fast viewer was labelled fraudulent, not suspect: {states}")


def test_a_normal_viewer_is_left_alone():
    _clear()
    conn = db.connect()
    try:
        _decision(conn, decision_id="d-ok")
        _event(conn, name="ad_served", decision="d-ok", occurred=_ago(minutes=9))
        _event(conn, name="ad_viewable", decision="d-ok",
               occurred=_ago(minutes=8))
        event_id = _event(conn, name="ad_click", decision="d-ok",
                          occurred=_ago(minutes=7))
        conn.commit()
        result = invalid_traffic.sweep(conn)
        row = conn.execute("SELECT validity, billable FROM ads_intel_events "
                           "WHERE event_id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    _assert(result["reclassified"] == 0,
            f"a clean session was reclassified: {result}")
    _assert(row[0] == "valid" and int(row[1]) == 1, row)


def test_a_second_sweep_finds_nothing_left_to_do():
    _clear()
    conn = db.connect()
    try:
        _decision(conn, decision_id="d-r")
        _event(conn, name="ad_click", decision="d-r", occurred=_ago(minutes=5))
        conn.commit()
        first = invalid_traffic.sweep(conn)
        second = invalid_traffic.sweep(conn)
    finally:
        conn.close()
    _assert(first["reclassified"] == 1, first)
    _assert(second["reclassified"] == 0,
            f"the sweep re-counted its own work: {second}")


def test_a_ruling_records_the_rule_version_that_made_it():
    _clear()
    conn = db.connect()
    try:
        _decision(conn, decision_id="d-ver")
        event_id = _event(conn, name="ad_click", decision="d-ver",
                          occurred=_ago(minutes=5),
                          quality_notes="an earlier ingest note")
        conn.commit()
        invalid_traffic.sweep(conn)
        row = conn.execute("SELECT quality_notes FROM ads_intel_events "
                           "WHERE event_id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    notes = row[0] or ""
    _assert(f"v{taxonomy.FRAUD_RULE_VERSION}" in notes, notes)
    _assert("an earlier ingest note" in notes,
            f"the ruling destroyed the ingest note: {notes!r}")


def test_a_screening_failure_admits_rather_than_rejects():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("down")

    verdict = invalid_traffic.screen(
        _Broken(), {"event_name": "ad_click", "subject_ref": "s",
                    "creative_id": "c"})
    _assert(verdict["validity"] == "valid", verdict)


def test_a_click_faster_than_a_person_is_rejected_at_ingest():
    verdict = invalid_traffic.screen(None, {
        "event_name": "ad_click", "since_impression_ms": 40})
    _assert(verdict["validity"] == "invalid", verdict)
    _assert(verdict["reason"] == "IMPOSSIBLE_SEQUENCE", verdict)


def test_screening_ignores_events_that_could_never_be_billed():
    verdict = invalid_traffic.screen(None, {
        "event_name": "ad_quick_skip", "since_impression_ms": 1})
    _assert(verdict["validity"] == "valid", verdict)


def test_screening_actually_runs_at_ingest_and_not_only_on_demand():
    """A rule nobody calls protects nothing.

    The sweep is a safety net; between an event being stored and the sweep
    running, a row marked valid and billable is visible to the billing path.
    That interval is exactly when a click farm is most productive, so the cheap
    rules have to run inline.
    """
    _clear()
    stored = events.record_event({
        "event_name": "ad_click",
        "dedup_key": f"ingest-screen-{uuid.uuid4().hex[:10]}",
        "occurred_at": _iso(datetime.now(timezone.utc)),
        "decision_id": "d-screen",
        "campaign_id": "camp-1",
        "creative_id": "cr-1",
        "subject_ref": "subj-screen",
        "since_impression_ms": 12,
    }, ingest_source="server")
    _assert(stored.get("accepted"), stored)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT validity, invalid_reason, billable FROM ads_intel_events "
            "WHERE event_id = ?", (stored["event_id"],)).fetchone()
    finally:
        conn.close()
    _assert(row[0] == "invalid", f"ingest stored an impossible click as {row}")
    _assert(row[1] == "IMPOSSIBLE_SEQUENCE", row)
    _assert(int(row[2]) == 0,
            f"an event ruled invalid at ingest is still billable: {row}")


def test_a_screened_event_keeps_both_its_quality_and_validity_notes():
    _clear()
    stored = events.record_event({
        "event_name": "ad_click",
        "dedup_key": f"ingest-notes-{uuid.uuid4().hex[:10]}",
        "occurred_at": _iso(datetime.now(timezone.utc)),
        "decision_id": "d-notes",
        "campaign_id": "camp-1",
        "creative_id": "cr-1",
        "subject_ref": "subj-notes",
        "since_impression_ms": 5,
        "duration_ms": -4,
    }, ingest_source="server")
    conn = db.connect()
    try:
        row = conn.execute("SELECT quality_notes FROM ads_intel_events "
                           "WHERE event_id = ?", (stored["event_id"],)).fetchone()
    finally:
        conn.close()
    notes = row[0] or ""
    _assert("duration_ms" in notes, notes)
    _assert("after the impression" in notes, notes)


def test_the_summary_reports_a_rate_not_only_a_count():
    _clear()
    conn = db.connect()
    try:
        for _ in range(9):
            _event(conn, name="ad_click", occurred=_ago(minutes=5))
        _event(conn, name="ad_click", validity="invalid",
               occurred=_ago(minutes=5))
        conn.commit()
        summary = invalid_traffic.summarise(conn, campaign_id="camp-1")
    finally:
        conn.close()
    _assert(summary["total"] == 10, summary)
    _assert(summary["excluded"] == 1, summary)
    _assert(abs(summary["invalid_rate"] - 0.1) < 1e-9, summary)


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

def test_view_through_attribution_does_not_exist():
    """Absent by design, and pinned so it cannot be added without a decision."""
    _assert(attribution.VIEW_THROUGH_SUPPORTED is False)
    body = inspect.getsource(attribution).split('"""', 2)[-1]
    for banned in ("view_through_window", "modelled_conversion",
                   "modeled_conversion", "estimated_conversions"):
        _assert(banned not in body, f"attribution grew {banned!r}")


def test_a_conversion_without_a_click_is_reported_unattributed():
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_viewable", occurred=_ago(hours=2))
        _event(conn, name="ad_add_to_cart", occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 0, result)
    _assert(result["unattributed_conversions"] == 1,
            f"a viewed-not-clicked conversion was credited: {result}")
    _assert(result["total_conversions_observed"] == 1, result)


def test_a_conversion_after_a_click_is_attributed():
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", occurred=_ago(hours=6))
        _event(conn, name="ad_add_to_cart", occurred=_ago(hours=1),
               value_cents=2500)
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 1, result)
    _assert(result["reported_value_cents"] == 2500, result)


def test_a_click_outside_the_window_does_not_claim_the_conversion():
    _clear()
    window = taxonomy.CLICK_ATTRIBUTION_WINDOW_HOURS
    conn = db.connect()
    try:
        _event(conn, name="ad_click", occurred=_ago(hours=window + 24))
        _event(conn, name="ad_add_to_cart", occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 0,
            f"a click {window + 24}h old claimed a conversion: {result}")


def test_a_click_after_the_conversion_cannot_claim_it():
    """Causation runs forwards. A later click is not why an earlier order was
    placed, and crediting it is how a campaign appears to cause its own past."""
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_add_to_cart", occurred=_ago(hours=3))
        _event(conn, name="ad_click", occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 0, result)


def test_the_last_click_wins_not_the_first():
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", occurred=_ago(hours=48))
        recent = _event(conn, name="ad_click", occurred=_ago(hours=2))
        conn.commit()
        verdict = attribution.attribute(conn, subject_ref="subj-1",
                                        campaign_id="camp-1",
                                        occurred_at=_ago(hours=1))
    finally:
        conn.close()
    _assert(verdict["attributed"] is True, verdict)
    _assert(verdict["click_event_id"] == recent,
            f"the first click won instead of the last: {verdict}")


def test_four_clicks_and_one_order_is_one_conversion():
    _clear()
    conn = db.connect()
    try:
        for hours in (5, 4, 3, 2):
            _event(conn, name="ad_click", occurred=_ago(hours=hours))
        _event(conn, name="ad_purchase_completed", occurred=_ago(hours=1),
               value_cents=9900)
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 1,
            f"one order was counted {result['attributed_conversions']} times")
    _assert(result["reported_value_cents"] == 9900, result)


def test_an_invalid_click_cannot_earn_a_conversion():
    """Otherwise fraudulent clicks manufacture the outcome being optimised for."""
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", occurred=_ago(hours=2),
               validity="invalid")
        _event(conn, name="ad_add_to_cart", occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 0, result)
    _assert(result["unattributed_conversions"] == 1, result)


def test_a_click_on_a_different_campaign_does_not_claim_the_conversion():
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", campaign="camp-OTHER",
               occurred=_ago(hours=2))
        _event(conn, name="ad_add_to_cart", campaign="camp-1",
               occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 0, result)


def test_a_different_person_does_not_claim_the_conversion():
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", subject="subj-A", occurred=_ago(hours=2))
        _event(conn, name="ad_add_to_cart", subject="subj-B",
               occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["attributed_conversions"] == 0, result)


def test_conversion_value_is_never_divided_by_spend():
    """No ROAS. The mission is explicit and the module must have no such key."""
    body = inspect.getsource(attribution).split('"""', 2)[-1]
    for banned in ("roas", "return_on_ad_spend", "spent_cents", "cost_cents",
                   "revenue_per"):
        _assert(banned.lower() not in body.lower(),
                f"attribution computes a return on spend via {banned!r}")


def test_reported_value_is_labelled_as_the_advertisers_own():
    _clear()
    conn = db.connect()
    try:
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["value_is_advertiser_reported"] is True, result)
    _assert("reported_value_cents" in result,
            "the value key does not say who reported it")
    _assert("value_cents" not in result,
            "an unlabelled value_cents key would read as verified")


def test_the_attribution_model_travels_with_the_numbers():
    _clear()
    conn = db.connect()
    try:
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    _assert(result["model"] == "last_click", result)
    _assert(result["window_hours"] == taxonomy.CLICK_ATTRIBUTION_WINDOW_HOURS,
            result)
    _assert(result["attribution_version"] == taxonomy.ATTRIBUTION_VERSION,
            result)


def test_the_advertiser_sentence_says_what_is_not_counted():
    _clear()
    conn = db.connect()
    try:
        _event(conn, name="ad_click", occurred=_ago(hours=4))
        _event(conn, name="ad_add_to_cart", occurred=_ago(hours=1))
        _event(conn, name="ad_favorite", subject="subj-Z",
               occurred=_ago(hours=1))
        conn.commit()
        result = attribution.campaign_conversions(conn, "camp-1")
    finally:
        conn.close()
    sentence = attribution.explain_attribution(result)
    _assert("not credited" in sentence, sentence)
    _assert("reported by your own systems" in sentence, sentence)


# --------------------------------------------------------------------------- #
# Funnel
# --------------------------------------------------------------------------- #

def test_the_funnel_names_the_step_that_loses_the_most_people():
    _clear()
    conn = db.connect()
    try:
        for i in range(100):
            _decision(conn, decision_id=f"d-f{i}", occurred=_ago(hours=2))
        for i in range(100):
            _event(conn, name="ad_served", decision=f"d-f{i}",
                   occurred=_ago(hours=2))
        for i in range(95):
            _event(conn, name="ad_rendered", decision=f"d-f{i}",
                   occurred=_ago(hours=2))
        for i in range(20):
            _event(conn, name="ad_viewable", decision=f"d-f{i}",
                   occurred=_ago(hours=2))
        for i in range(18):
            _event(conn, name="ad_click", decision=f"d-f{i}",
                   occurred=_ago(hours=2))
        conn.commit()
        result = attribution.funnel(conn, "camp-1")
    finally:
        conn.close()
    drop = result["biggest_drop"]
    _assert(drop is not None, result)
    _assert(drop["step"] == "viewable",
            f"the funnel blamed {drop['step']} rather than viewability")
    _assert(drop["lost"] == 75, drop)
    _assert("placement" in drop["likely_cause"], drop)


def test_the_funnel_counts_opportunities_this_campaign_lost():
    """Events alone cannot show them: a lost opportunity produces no event."""
    _clear()
    conn = db.connect()
    try:
        for i in range(50):
            _decision(conn, decision_id=f"d-l{i}", occurred=_ago(hours=2))
        for i in range(5):
            _event(conn, name="ad_served", decision=f"d-l{i}",
                   occurred=_ago(hours=2))
        conn.commit()
        result = attribution.funnel(conn, "camp-1")
    finally:
        conn.close()
    steps = {s["step"]: s for s in result["steps"]}
    _assert(steps["opportunity"]["count"] == 50, steps["opportunity"])
    _assert(result["biggest_drop"]["step"] == "served", result["biggest_drop"])
    _assert("targeting" in result["biggest_drop"]["likely_cause"],
            result["biggest_drop"])


def test_every_funnel_step_that_can_drop_has_a_diagnosis():
    droppable = [label for label, _ in attribution.FUNNEL_STEPS][1:]
    for label in droppable:
        _assert(label in attribution.STEP_DIAGNOSIS,
                f"the funnel can blame {label} without explaining it")


def test_an_empty_funnel_says_so_rather_than_inventing_a_cause():
    _clear()
    conn = db.connect()
    try:
        result = attribution.funnel(conn, "camp-empty")
    finally:
        conn.close()
    _assert(result["biggest_drop"] is None, result)
    _assert("not enough delivery" in attribution.explain_funnel(result))


def test_invalid_events_do_not_appear_in_the_funnel():
    _clear()
    conn = db.connect()
    try:
        for i in range(10):
            _decision(conn, decision_id=f"d-i{i}", occurred=_ago(hours=2))
            _event(conn, name="ad_served", decision=f"d-i{i}",
                   occurred=_ago(hours=2))
        for i in range(6):
            _event(conn, name="ad_click", decision=f"d-i{i}",
                   occurred=_ago(hours=2), validity="invalid")
        conn.commit()
        result = attribution.funnel(conn, "camp-1")
    finally:
        conn.close()
    steps = {s["step"]: s for s in result["steps"]}
    _assert(steps["click"]["count"] == 0,
            f"invalid clicks inflated the funnel: {steps['click']}")


def test_a_broken_funnel_read_degrades_rather_than_raises():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("down")

    result = attribution.funnel(_Broken(), "camp-1")
    _assert(result["degraded"] is True, result)
    _assert(result["biggest_drop"] is None, result)


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
