"""Ads intelligence — event fabric: privacy, envelope, dedupe, data quality.

Everything downstream in this subsystem (metrics, pacing, the interest graph,
the ranker, billing vetoes) is a projection of what the ingest front door
accepts, so these are the tests that decide whether any of it can be trusted.

Four properties are asserted here, each of which has a specific way of going
wrong silently in production:

* **Pseudonymity.** A raw account id must never reach ``ads_intel_events``.
  The failure mode is not dramatic — everything works — you have simply built a
  second, unguarded copy of the user table inside the analytics store.

* **Idempotency.** A phone retries. If ``dedup_key`` is not genuinely enforced
  at the database, an impression count inflates and nothing anywhere errors.
  Batch replay is tested separately from event dedupe because they fail
  differently: a whole-batch retry must not re-parse, while a partially
  overlapping batch must still arbitrate event by event.

* **Trust boundary.** A client may not assert revenue. The subtle case, and the
  one that motivated a fix, is a client putting ``"ingest_source": "server"`` in
  its own JSON: trust must come from the calling route, never from the payload.

* **Quality without destruction.** A viewable claiming 4% visibility is stored
  and flagged, not discarded — throwing it away would destroy the evidence that
  a client release is broken — but it must not be billable.

    python tests/business_os/test_ads_intelligence_events.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ads_intel_ev_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)
os.environ.setdefault("ADS_INTEL_SUBJECT_SALT", "test-salt-deterministic")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.business_os.ads_intelligence import events, privacy, taxonomy  # noqa: E402
from services.business_os.ads_intelligence.schema import ensure_schema  # noqa: E402

_NOW = datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _assert(cond, detail=""):
    if not cond:
        raise AssertionError(detail)


def setup_module(module=None):
    ensure_schema()


def _ev(**kw):
    base = dict(event_name="ad_viewable", dedup_key="d-default",
                occurred_at=_iso(_NOW), decision_id="dec-1",
                campaign_id="camp-1", creative_id="cre-1",
                percent_visible=80, duration_ms=1500, user_id=4242)
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #

def test_subject_ref_is_deterministic_and_pseudonymous():
    first = privacy.subject_ref(4242)
    _assert(first == privacy.subject_ref(4242), "must be stable across calls")
    _assert(len(first) == 32, f"unexpected digest length: {first}")
    _assert("4242" not in first, "raw id leaked into the digest")
    _assert(privacy.subject_ref(4243) != first, "distinct users must not collide")


def test_subject_and_session_namespaces_do_not_collide():
    # Same numeric id in two roles must not merge into one subject.
    _assert(privacy.subject_ref(7) != privacy.session_ref(7),
            "namespacing is not applied")


def test_absent_identity_stays_absent():
    for value in (None, "", "0", "none", "null"):
        _assert(privacy.subject_ref(value) is None,
                f"{value!r} should not produce a subject")


def test_privacy_classes_fail_closed():
    _assert(privacy.allows("product_signal", "targeting") is True)
    _assert(privacy.allows("measurement_only", "targeting") is False,
            "measurement-only signals must never shape targeting")
    _assert(privacy.allows("security_only", "analytics") is False)
    _assert(privacy.allows("bogus_class", "targeting") is False,
            "an unknown class must deny, not default open")
    _assert(privacy.allows("product_signal", "bogus_purpose") is False,
            "an unknown purpose must deny")


def test_negative_signals_are_measurement_only():
    # A complaint must inform reporting without becoming a profile attribute.
    _assert(privacy.classify_event("ad_hide") == "measurement_only")
    _assert(privacy.classify_event("ad_report") == "measurement_only")
    _assert(privacy.classify_event("ad_click") == "product_signal")


def test_forbidden_sources_are_recognised():
    _assert(privacy.is_forbidden_source("call_audio") is True)
    _assert(privacy.is_forbidden_source("private_message") is True)
    _assert(privacy.is_forbidden_source("feed") is False)


# --------------------------------------------------------------------------- #
# Envelope validation
# --------------------------------------------------------------------------- #

def test_valid_event_passes():
    _assert(events.validate_envelope(_ev())["ok"], "a well-formed event must pass")


def test_unknown_event_name_is_rejected():
    result = events.validate_envelope(_ev(event_name="ad_teleported"))
    _assert(result["reason"] == "SCHEMA_INVALID", result)


def test_dedup_key_is_required():
    _assert(events.validate_envelope(_ev(dedup_key=""))["reason"] == "SCHEMA_INVALID")


def test_non_opportunity_events_require_a_decision():
    # Without a decision the event cannot be joined to a delivery: it would
    # inflate a total while explaining nothing.
    _assert(events.validate_envelope(_ev(decision_id=""))["reason"]
            == "UNKNOWN_DECISION")


def test_opportunity_events_need_no_decision():
    payload = dict(event_name="ad_opportunity_created", dedup_key="opp-1",
                   occurred_at=_iso(_NOW))
    _assert(events.validate_envelope(payload)["ok"],
            "an opportunity is what creates the decision")


def test_future_timestamps_are_rejected():
    result = events.validate_envelope(_ev(occurred_at=_iso(_NOW + timedelta(minutes=30))))
    _assert(result["reason"] == "IMPLAUSIBLE_TIMESTAMP", result)


def test_small_clock_skew_is_tolerated():
    result = events.validate_envelope(_ev(occurred_at=_iso(_NOW + timedelta(seconds=30))))
    _assert(result["ok"], "ordinary clock skew must not drop real events")


def test_offline_queue_is_tolerated_but_ancient_events_are_not():
    recent = events.validate_envelope(_ev(occurred_at=_iso(_NOW - timedelta(hours=12))))
    _assert(recent["ok"], "offline queues legitimately deliver hours late")
    old = events.validate_envelope(_ev(occurred_at=_iso(_NOW - timedelta(hours=72))))
    _assert(old["reason"] == "IMPLAUSIBLE_TIMESTAMP", old)


def test_client_may_not_assert_a_purchase():
    result = events.validate_envelope(_ev(event_name="ad_purchase_completed"))
    _assert(result["reason"] == "CLIENT_ASSERTED_CONVERSION", result)


def test_server_may_assert_a_purchase():
    result = events.validate_envelope(_ev(event_name="ad_purchase_completed"),
                                      ingest_source="server")
    _assert(result["ok"], "server-derived conversions are the only valid path")


def test_payload_cannot_claim_server_trust():
    """The escalation this guards against: a modified client declaring itself.

    Trust is the calling route's to assert. If this regresses, a phone can mint
    revenue events by adding one key to its own JSON.
    """
    result = events.validate_envelope(
        _ev(event_name="ad_purchase_completed", ingest_source="server"))
    _assert(result["reason"] == "CLIENT_ASSERTED_CONVERSION", result)


def test_forbidden_signal_source_is_rejected():
    result = events.validate_envelope(_ev(signal_source="call_audio"))
    _assert(result["reason"] == "FORBIDDEN_SOURCE", result)


# --------------------------------------------------------------------------- #
# Data quality
# --------------------------------------------------------------------------- #

def test_clean_viewable_is_billable():
    quality = events.assess_quality(_ev(), event_name="ad_viewable")
    _assert(quality["quality_status"] == "ok", quality)
    _assert(quality["billable"] is True, quality)


def test_viewable_below_contract_is_flagged_and_not_billable():
    quality = events.assess_quality(_ev(percent_visible=4), event_name="ad_viewable")
    _assert(quality["quality_status"] == "suspect", quality)
    _assert(quality["billable"] is False, "a broken viewable must never bill")
    _assert(quality["quality_notes"], "a flag without a reason is unactionable")


def test_backgrounded_exposure_is_not_billable():
    quality = events.assess_quality(_ev(foreground=False), event_name="ad_viewable")
    _assert(quality["billable"] is False,
            "a suspended app must not accumulate viewable time")


def test_implausible_dwell_is_flagged():
    quality = events.assess_quality(_ev(duration_ms=999_999), event_name="ad_viewable")
    _assert(quality["billable"] is False, quality)


def test_out_of_range_and_negative_values_are_flagged():
    _assert(events.assess_quality(_ev(percent_visible=180),
                                  event_name="ad_viewable")["quality_status"] == "suspect")
    _assert(events.assess_quality(_ev(duration_ms=-5),
                                  event_name="ad_viewable")["quality_status"] == "suspect")
    _assert(events.assess_quality(_ev(value_cents=-100),
                                  event_name="ad_click")["quality_status"] == "suspect")


def test_only_taxonomy_billable_events_can_be_billable():
    for name in ("ad_rendered", "ad_served", "ad_hide", "ad_video_50"):
        quality = events.assess_quality(_ev(event_name=name), event_name=name)
        _assert(quality["billable"] is False, f"{name} must not be billable")
    for name in taxonomy.BILLABLE_CANDIDATE_EVENTS:
        quality = events.assess_quality(_ev(event_name=name), event_name=name)
        _assert(quality["billable"] is True, f"{name} should be a candidate")


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #

def test_batch_accepts_valid_events():
    result = events.ingest_batch({"batch_key": "batch-accept", "events": [
        _ev(dedup_key="acc-1"),
        _ev(dedup_key="acc-2", event_name="ad_click"),
    ]})
    _assert(result["accepted"] == 2, result)
    _assert(result["duplicate"] == 0 and result["rejected"] == 0, result)


def test_duplicate_event_is_absorbed_not_double_counted():
    events.ingest_batch({"batch_key": "batch-dup-a", "events": [_ev(dedup_key="dup-1")]})
    again = events.ingest_batch({"batch_key": "batch-dup-b",
                                 "events": [_ev(dedup_key="dup-1")]})
    _assert(again["accepted"] == 0 and again["duplicate"] == 1, again)

    conn = db.connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events WHERE dedup_key = ?",
            ("dup-1",)).fetchone()[0]
    finally:
        conn.close()
    _assert(count == 1, f"dedup_key stored {count} times")


def test_batch_replay_is_answered_from_the_batch_record():
    first = events.ingest_batch({"batch_key": "batch-replay",
                                 "events": [_ev(dedup_key="rep-1")]})
    _assert(first.get("replayed") is False, first)
    # Same batch key, different contents: a replay must not ingest anything new.
    second = events.ingest_batch({"batch_key": "batch-replay",
                                  "events": [_ev(dedup_key="rep-NEVER-STORED")]})
    _assert(second.get("replayed") is True, second)
    _assert(second["accepted"] == first["accepted"], second)

    conn = db.connect()
    try:
        found = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events WHERE dedup_key = ?",
            ("rep-NEVER-STORED",)).fetchone()[0]
    finally:
        conn.close()
    _assert(found == 0, "a replayed batch must not ingest new events")


def test_one_bad_event_does_not_discard_the_batch():
    result = events.ingest_batch({"batch_key": "batch-mixed", "events": [
        _ev(dedup_key="mix-bad", event_name="ad_nonsense"),
        _ev(dedup_key="mix-good"),
    ]})
    _assert(result["accepted"] == 1, result)
    _assert(result["rejected"] == 1, result)
    _assert(result["reject_reasons"] == {"SCHEMA_INVALID": 1}, result)


def test_rejections_are_counted_by_reason():
    result = events.ingest_batch({"batch_key": "batch-reasons", "events": [
        _ev(dedup_key="rr-1", event_name="ad_purchase_completed"),
        _ev(dedup_key="rr-2", decision_id=""),
        _ev(dedup_key="rr-3"),
    ]})
    _assert(result["reject_reasons"].get("CLIENT_ASSERTED_CONVERSION") == 1, result)
    _assert(result["reject_reasons"].get("UNKNOWN_DECISION") == 1, result)
    _assert(result["accepted"] == 1, result)


def test_oversized_batch_is_refused():
    result = events.ingest_batch({"events": [_ev()] * (events.MAX_BATCH_EVENTS + 1)})
    _assert(result["ok"] is False, "an unbounded batch is a denial-of-service surface")


def test_malformed_batch_is_refused():
    _assert(events.ingest_batch({"events": "not-a-list"})["ok"] is False)


def test_record_event_round_trip():
    result = events.record_event(
        _ev(dedup_key="single-1", event_name="ad_purchase_completed",
            value_cents=500), ingest_source="server")
    _assert(result["accepted"] is True, result)
    _assert(result["event_id"], result)
    repeat = events.record_event(
        _ev(dedup_key="single-1", event_name="ad_purchase_completed",
            value_cents=500), ingest_source="server")
    _assert(repeat["duplicate"] is True, repeat)


def test_stored_rows_carry_family_version_and_no_raw_user_id():
    events.ingest_batch({"batch_key": "batch-shape",
                         "events": [_ev(dedup_key="shape-1")]})
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT event_family, schema_version, processing_version, "
            "subject_ref, ingest_source FROM ads_intel_events WHERE dedup_key = ?",
            ("shape-1",)).fetchone()
        leaked = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_events WHERE subject_ref = ?",
            ("4242",)).fetchone()[0]
    finally:
        conn.close()
    _assert(row is not None, "event was not stored")
    _assert(row[0] == "delivery", row)
    _assert(row[1] == taxonomy.EVENT_SCHEMA_VERSION, row)
    _assert(row[3] == privacy.subject_ref(4242), "subject_ref is not the digest")
    _assert(row[4] == "client", row)
    _assert(leaked == 0, "a raw user id reached the event log")


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
