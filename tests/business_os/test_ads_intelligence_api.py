"""Ads intelligence — HTTP controller: darkness, identity, and client forgery.

This surface is the only place an untrusted device talks to the measurement
system, so the tests here are mostly about what a *hostile* client cannot do.
Three of them correspond to concrete ways an ad system gets defrauded:

* **Identity forgery.** A client putting ``user_id`` or ``subject_ref`` in its
  own payload must not be able to attribute its behaviour to another account —
  or to invent a subject at all. The server derives the subject from the
  authenticated session, so the fields are simply dropped.

* **Minting revenue.** ``ad_purchase_completed`` is rejected from a client no
  matter how it is dressed up, because a device that can assert a purchase can
  assert a payout.

* **Setting derived columns.** ``billable``, ``validity`` and ``quality_status``
  are conclusions the server draws about an event. A client that could set them
  could mark its own fraudulent impressions billable and clean.

The field allowlist is what enforces all three, which is why it is tested
directly rather than only through its consequences.

    python tests/business_os/test_ads_intelligence_api.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_api_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-api")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import datetime, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import api, events, privacy  # noqa: E402
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402

_FLAG = "BUSINESS_OS_ADS_INTELLIGENCE_MEASUREMENT"
_VIEWER = 5551212


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def setup_module(module=None):
    ensure_schema()
    os.environ[_FLAG] = "on"


def teardown_module(module=None):
    os.environ.pop(_FLAG, None)


def _ev(**kw):
    base = {"event_name": "ad_viewable", "dedup_key": "api-default",
            "occurred_at": _now(), "decision_id": "dec-api",
            "campaign_id": "camp-api", "creative_id": "cre-api",
            "percent_visible": 80, "duration_ms": 1500}
    base.update(kw)
    return base


def _fetch(dedup_key, columns):
    conn = db.connect()
    try:
        return conn.execute(
            f"SELECT {columns} FROM ads_intel_events WHERE dedup_key = ?",
            (dedup_key,)).fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Darkness
# --------------------------------------------------------------------------- #

def test_every_handler_is_dark_when_disabled():
    os.environ.pop(_FLAG, None)
    os.environ.pop("BUSINESS_OS_ADS_INTELLIGENCE", None)
    try:
        calls = (
            lambda: api.ingest_events(viewer_user_id=_VIEWER, payload={"events": []}),
            lambda: api.delivery_health(),
            lambda: api.status(),
            lambda: api.campaign_delivery_diagnosis(owner_user_id=_VIEWER,
                                                    campaign_id="c1"),
        )
        for call in calls:
            status, body = call()
            _assert(status == 404, f"expected 404, got {status}")
            _assert(body.get("ok") is False, body)
    finally:
        os.environ[_FLAG] = "on"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

def test_client_cannot_attribute_events_to_another_account():
    payload = {"batch_key": "b-forge", "events": [
        dict(_ev(dedup_key="forge-1"), user_id=999999, subject_ref="forged-ref"),
    ]}
    status, body = api.ingest_events(viewer_user_id=_VIEWER, payload=payload)
    _assert(status == 200 and body["accepted"] == 1, (status, body))

    row = _fetch("forge-1", "subject_ref")
    _assert(row[0] == privacy.subject_ref(_VIEWER),
            "subject was not derived from the session")

    conn = db.connect()
    try:
        forged = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events WHERE subject_ref = ?",
            ("forged-ref",)).fetchone()[0]
        other = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events WHERE subject_ref = ?",
            (privacy.subject_ref(999999),)).fetchone()[0]
    finally:
        conn.close()
    _assert(forged == 0, "a client-supplied subject_ref was stored")
    _assert(other == 0, "events were attributed to another account")


def test_raw_viewer_id_never_reaches_the_event_log():
    api.ingest_events(viewer_user_id=_VIEWER,
                      payload={"batch_key": "b-raw",
                               "events": [_ev(dedup_key="raw-1")]})
    conn = db.connect()
    try:
        leaked = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events WHERE subject_ref = ?",
            (str(_VIEWER),)).fetchone()[0]
    finally:
        conn.close()
    _assert(leaked == 0, "a raw account id was stored as a subject")


# --------------------------------------------------------------------------- #
# Forgery
# --------------------------------------------------------------------------- #

def test_client_cannot_assert_a_purchase():
    status, body = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"events": [_ev(dedup_key="rev-1",
                                event_name="ad_purchase_completed")]})
    _assert(status == 200, (status, body))
    _assert(body["accepted"] == 0, body)
    _assert(body["reject_reasons"] == {"CLIENT_ASSERTED_CONVERSION": 1}, body)


def test_client_cannot_declare_itself_a_server():
    """The escalation: adding ``ingest_source`` to the payload.

    ``ingest_source`` is not in the client field allowlist, so it is dropped
    before it can reach validation — and validation takes its trust level from
    the caller regardless.
    """
    status, body = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"events": [dict(_ev(dedup_key="rev-2",
                                     event_name="ad_purchase_completed"),
                                 ingest_source="server")]})
    _assert(body["accepted"] == 0, body)
    _assert(body["reject_reasons"] == {"CLIENT_ASSERTED_CONVERSION": 1}, body)


def test_client_cannot_set_derived_columns():
    """billable / validity / quality_status are server conclusions.

    The event below fails the viewability contract (2% visible) while claiming
    to be clean and billable. The stored row must reflect the server's judgement.
    """
    status, body = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"events": [dict(_ev(dedup_key="derive-1", percent_visible=2),
                                 billable=1, validity="valid",
                                 quality_status="ok")]})
    _assert(body["accepted"] == 1, body)
    row = _fetch("derive-1", "billable, quality_status")
    _assert(row[0] == 0, "client marked its own event billable")
    _assert(row[1] == "suspect", f"quality was not assessed server-side: {row}")


def test_allowlist_excludes_every_derived_and_identity_field():
    for field in ("billable", "validity", "quality_status", "invalid_reason",
                  "ingest_source", "subject_ref", "user_id", "received_at",
                  "schema_version", "processing_version", "event_id"):
        _assert(field not in api.CLIENT_EVENT_FIELDS,
                f"{field} must not be client-settable")


def test_unknown_fields_do_not_fail_the_batch():
    # A newer client sending an extra field must not break ingest for the rest.
    status, body = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"events": [dict(_ev(dedup_key="extra-1"),
                                 some_future_field="whatever")]})
    _assert(body["accepted"] == 1, body)


# --------------------------------------------------------------------------- #
# Batch behaviour
# --------------------------------------------------------------------------- #

def test_partial_batch_is_reported_not_rejected():
    """A batch with one bad event keeps the good ones.

    Returning 400 here would discard real events because of one bad type, and
    the client would retry the identical batch forever.
    """
    status, body = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"batch_key": "b-partial", "events": [
            _ev(dedup_key="part-good"),
            _ev(dedup_key="part-bad", event_name="ad_not_a_real_event"),
        ]})
    _assert(status == 200, (status, body))
    _assert(body["accepted"] == 1 and body["rejected"] == 1, body)
    _assert(body["reject_reasons"] == {"SCHEMA_INVALID": 1}, body)


def test_batch_replay_is_idempotent():
    first = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"batch_key": "b-replay", "events": [_ev(dedup_key="rep-a")]})[1]
    second = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"batch_key": "b-replay", "events": [_ev(dedup_key="rep-a")]})[1]
    _assert(first["accepted"] == 1, first)
    _assert(second["replayed"] is True, second)


def test_oversized_batch_is_refused():
    status, body = api.ingest_events(
        viewer_user_id=_VIEWER,
        payload={"events": [_ev()] * (events.MAX_BATCH_EVENTS + 1)})
    _assert(status == 413, (status, body))


def test_malformed_payloads_are_refused():
    _assert(api.ingest_events(viewer_user_id=_VIEWER, payload=None)[0] == 400)
    _assert(api.ingest_events(viewer_user_id=_VIEWER,
                              payload={"events": "nope"})[0] == 400)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def test_delivery_health_returns_a_report():
    status, body = api.delivery_health()
    _assert(status == 200 and "report" in body, (status, body))
    _assert("fill_rate" in body["report"], body)


def test_status_distinguishes_measurement_from_full_enablement():
    status, body = api.status()
    _assert(status == 200, (status, body))
    _assert(body["measurement_enabled"] is True, body)
    _assert(body["fully_enabled"] is False,
            "measurement must not imply the master flag")


def test_campaign_diagnosis_hides_existence_from_non_owners():
    status, body = api.campaign_delivery_diagnosis(
        owner_user_id=_VIEWER, campaign_id="a-campaign-that-is-not-mine")
    _assert(status == 404, (status, body))


def test_campaign_diagnosis_rejects_an_empty_id():
    _assert(api.campaign_delivery_diagnosis(
        owner_user_id=_VIEWER, campaign_id="")[0] == 404)


# --------------------------------------------------------------------------- #
# The viewer-facing surfaces
# --------------------------------------------------------------------------- #

def _seed_decision(subject_ref, decision_id="dec-why-1"):
    conn = db.connect()
    try:
        conn.execute(
            "DELETE FROM ads_intel_delivery_decisions WHERE decision_id = ?",
            (decision_id,))
        conn.execute(
            "INSERT INTO ads_intel_delivery_decisions (decision_id, "
            "opportunity_id, occurred_at, subject_ref, placement_key, filled, "
            "campaign_id, creative_id, score, score_breakdown_json, "
            "ranking_mode, exploration, created_at) "
            "VALUES (?, ?, ?, ?, 'feed', 1, 'camp-api', 'cre-api', 0.5, ?, "
            "'ranked_v1', 0, ?)",
            (decision_id, f"opp-{decision_id}", _now(), subject_ref,
             '{"context": 0.8, "affinity": 0.2}', _now()))
        conn.commit()
    finally:
        conn.close()
    return decision_id


def test_a_viewer_can_read_why_they_saw_an_ad():
    """The whole point of the surface: a real answer, from the real decision."""
    subject = privacy.subject_ref(_VIEWER)
    decision_id = _seed_decision(subject)
    status, body = api.explain_ad(viewer_user_id=_VIEWER,
                                  decision_id=decision_id)
    _assert(status == 200, (status, body))
    _assert(body["explanation"]["reasons"], body)
    _assert(body["explanation"]["controls"], body)


def test_one_viewer_cannot_read_another_viewers_explanation():
    """The single most important property of this endpoint.

    A 404 here must be indistinguishable from the 404 for an id that was never
    issued, because a distinct "not yours" confirms the id is real and therefore
    that some specific person was shown that ad.
    """
    decision_id = _seed_decision(privacy.subject_ref(_VIEWER))
    somebody_else = 909090
    theirs = api.explain_ad(viewer_user_id=somebody_else,
                            decision_id=decision_id)
    never_issued = api.explain_ad(viewer_user_id=somebody_else,
                                  decision_id="dec-does-not-exist")
    _assert(theirs[0] == 404, theirs)
    _assert(theirs == never_issued,
            f"another viewer's decision is distinguishable from a missing "
            f"one: {theirs} vs {never_issued}")


def test_the_explanation_endpoint_takes_no_subject_from_the_caller():
    """There is no parameter that lets a caller name whose ads to explain."""
    import inspect
    params = set(inspect.signature(api.explain_ad).parameters)
    _assert(params == {"viewer_user_id", "decision_id"},
            f"explain_ad grew a parameter: {sorted(params)}")


def test_the_interest_disclosure_is_scoped_to_the_caller():
    import inspect
    params = set(inspect.signature(api.my_ad_interests).parameters)
    _assert(params == {"viewer_user_id"},
            f"my_ad_interests grew a parameter: {sorted(params)}")
    status, body = api.my_ad_interests(viewer_user_id=_VIEWER)
    _assert(status == 200, (status, body))
    _assert("categories" in body["disclosure"], body)


def test_the_caller_cannot_raise_the_autonomy_level_over_http():
    """Autonomy is a server-side decision, not a request parameter.

    If this endpoint took a level, the HTTP surface would become a way to widen
    what automation may do to a campaign.
    """
    import inspect
    params = set(inspect.signature(api.campaign_delivery_diagnosis).parameters)
    _assert("autonomy_level" not in params,
            "the diagnosis endpoint accepts an autonomy level from the caller")


def test_the_viewer_surfaces_are_dark_when_measurement_is_off():
    os.environ.pop(_FLAG, None)
    try:
        _assert(api.explain_ad(viewer_user_id=_VIEWER,
                               decision_id="dec-why-1")[0] == 404)
        _assert(api.my_ad_interests(viewer_user_id=_VIEWER)[0] == 404)
    finally:
        os.environ[_FLAG] = "on"


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
    teardown_module()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
