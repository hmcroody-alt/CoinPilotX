"""Stages 7 + 8: incident lifecycle guards and deterministic correlation."""

import pytest

from services.sentinel import correlation, events, incidents
from services.sentinel.correlation import CorrelationRule
from services.sentinel.incidents import TransitionError


def _open(conn, key="inc-1"):
    return incidents.open_incident(key, "SECURITY_INTRUSION", "high",
                                   "test incident", "sentinel.correlator", conn=conn)


class TestIncidentLifecycle:
    def test_open_is_idempotent_and_counts_observations(self, conn):
        first = _open(conn)
        second = _open(conn)
        assert first.created and not second.created
        found = incidents.get("inc-1", conn=conn)
        assert found["state"] == "DETECTED"
        assert found["observation_count"] == 2  # recurrence counted, not duplicated

    def test_unknown_type_rejected(self, conn):
        with pytest.raises(ValueError):
            incidents.open_incident("k", "ALIEN_INVASION", "high", "t", "a", conn=conn)

    def test_illegal_transition_rejected(self, conn):
        _open(conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "RESOLVED", "op", note="skip states", conn=conn)

    def test_resolution_requires_note(self, conn):
        _open(conn)
        for state in ("INVESTIGATING", "CONFIRMED", "RECOVERING", "VERIFYING"):
            incidents.transition("inc-1", state, "op", conn=conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "RESOLVED", "op", note="",
                                 verified_by="sentinel.verifier", conn=conn)
        incidents.transition("inc-1", "RESOLVED", "op", note="root cause fixed",
                             verified_by="sentinel.verifier",
                             resolution_code="fixed_upstream", conn=conn)
        assert incidents.get("inc-1", conn=conn)["resolution_code"] == "fixed_upstream"

    def test_verification_exit_must_be_independent(self, conn):
        _open(conn)
        for state in ("INVESTIGATING", "CONFIRMED", "RECOVERING", "VERIFYING"):
            incidents.transition("inc-1", state, "op", conn=conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "RESOLVED", "op", note="done",
                                 verified_by="op", conn=conn)  # self-verification (SC4)

    def test_transitions_are_recorded_append_only(self, conn):
        _open(conn)
        incidents.transition("inc-1", "INVESTIGATING", "op", conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_incident_transitions")
        assert cur.fetchone()[0] == 1

    def test_resolved_recurrence_reopens_outside_cooldown(self, conn):
        from datetime import datetime, timedelta, timezone
        _open(conn)
        for state in ("INVESTIGATING", "CONFIRMED", "RECOVERING", "VERIFYING"):
            incidents.transition("inc-1", state, "op", conn=conn)
        incidents.transition("inc-1", "RESOLVED", "op", note="fixed",
                             verified_by="sentinel.verifier", conn=conn)
        later = datetime.now(timezone.utc) + timedelta(minutes=30)
        result = incidents.record_observation("inc-1", "sentinel.correlator",
                                              conn=conn, now=later)
        assert result["reopened"] and result["state"] == "INVESTIGATING"

    def test_resolved_recurrence_within_cooldown_only_counts(self, conn):
        _open(conn)
        for state in ("INVESTIGATING", "CONFIRMED", "RECOVERING", "VERIFYING"):
            incidents.transition("inc-1", state, "op", conn=conn)
        incidents.transition("inc-1", "RESOLVED", "op", note="fixed",
                             verified_by="sentinel.verifier", conn=conn)
        result = incidents.record_observation("inc-1", "sentinel.correlator", conn=conn)
        assert not result["reopened"] and result["state"] == "RESOLVED"
        assert incidents.get("inc-1", conn=conn)["observation_count"] == 2

    def test_suppression_requires_reason_and_expiry(self, conn):
        _open(conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "SUPPRESSED", "op", conn=conn)
        with pytest.raises(TransitionError):
            incidents.suppress("inc-1", "op", "", 60, conn=conn)
        with pytest.raises(TransitionError):
            incidents.suppress("inc-1", "op", "noisy rule", 0, conn=conn)
        ref = incidents.suppress("inc-1", "op", "noisy rule", 60, conn=conn)
        assert ref.state == "SUPPRESSED"
        # Suppressed incidents still exist and stay queryable.
        found = incidents.get("inc-1", conn=conn)
        assert found["suppressed_reason"] == "noisy rule"
        assert found["incident_key"] not in [i["incident_key"]
                                             for i in incidents.list_open(conn=conn)]
        assert found["incident_key"] in [i["incident_key"] for i in
                                         incidents.list_open(conn=conn, include_suppressed=True)]

    def test_expired_suppression_reopens_on_recurrence(self, conn):
        from datetime import datetime, timedelta, timezone
        _open(conn)
        incidents.suppress("inc-1", "op", "noisy rule", 60, conn=conn)
        later = datetime.now(timezone.utc) + timedelta(minutes=120)
        result = incidents.record_observation("inc-1", "sentinel.correlator",
                                              conn=conn, now=later)
        assert result["reopened"] and result["state"] == "INVESTIGATING"

    def test_dedupe_key_is_deterministic_and_scalar_only(self):
        a = incidents.dedupe_key("RULE1", "user:7", "2026-08-12")
        b = incidents.dedupe_key("RULE1", "user:7", "2026-08-12")
        assert a == b and a.startswith("inc_")
        with pytest.raises(ValueError):
            incidents.dedupe_key("RULE1", {"model": "output"})  # never structured blobs
        with pytest.raises(ValueError):
            incidents.dedupe_key()


class TestCorrelation:
    def test_single_signal_rules_are_unconstructible(self):
        with pytest.raises(ValueError):
            CorrelationRule("BAD", "one event fires", "AUTH", ("login_failed",),
                            window_minutes=30, min_events=1, min_distinct_types=1,
                            incident_type="ACCOUNT_TAKEOVER", severity="high")

    def test_unbounded_window_rejected(self):
        with pytest.raises(ValueError):
            CorrelationRule("BAD2", "forever", "AUTH", ("login_failed",),
                            window_minutes=0, min_events=5, min_distinct_types=1,
                            incident_type="ACCOUNT_TAKEOVER", severity="high")

    def test_threshold_opens_incident_deterministically(self, conn):
        for i in range(5):
            events.ingest(events.Event(
                category="AUTH", event_type="login_failed", severity="low",
                actor_id="sentinel.ingest", source="test",
                subject_type="user", subject_id="99",
                dedupe_key=f"lf-{i}"), conn=conn)
        rule = correlation.RULES[0]  # CR1: 5 failures in 30m
        findings = correlation.evaluate_rule(rule, conn=conn)
        assert len(findings) == 1 and findings[0]["created"]
        # Re-running is idempotent: same incident, not a second one.
        findings2 = correlation.evaluate_rule(rule, conn=conn)
        assert len(findings2) == 1 and not findings2[0]["created"]

    def test_below_threshold_opens_nothing(self, conn):
        for i in range(3):
            events.ingest(events.Event(
                category="AUTH", event_type="login_failed", severity="low",
                actor_id="sentinel.ingest", source="test",
                subject_type="user", subject_id="7",
                dedupe_key=f"few-{i}"), conn=conn)
        assert correlation.evaluate_rule(correlation.RULES[0], conn=conn) == []

    def test_distinct_type_rule_requires_both_signals(self, conn):
        # CR2 needs unusual_device AND unusual_country.
        events.ingest(events.Event(
            category="SECURITY", event_type="unusual_device", severity="medium",
            actor_id="sentinel.ingest", source="test",
            subject_type="user", subject_id="5", dedupe_key="ud-1"), conn=conn)
        assert correlation.evaluate_rule(correlation.RULES[1], conn=conn) == []
        events.ingest(events.Event(
            category="SECURITY", event_type="unusual_country", severity="medium",
            actor_id="sentinel.ingest", source="test",
            subject_type="user", subject_id="5", dedupe_key="uc-1"), conn=conn)
        findings = correlation.evaluate_rule(correlation.RULES[1], conn=conn)
        assert len(findings) == 1
