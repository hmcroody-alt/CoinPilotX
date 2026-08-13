"""Stages 7 + 8: incident lifecycle guards and deterministic correlation."""

import pytest

from services.sentinel import correlation, events, incidents
from services.sentinel.correlation import CorrelationRule
from services.sentinel.incidents import TransitionError


def _open(conn, key="inc-1"):
    return incidents.open_incident(key, "SECURITY_INTRUSION", "high",
                                   "test incident", "sentinel.correlator", conn=conn)


class TestIncidentLifecycle:
    def test_open_is_idempotent(self, conn):
        first = _open(conn)
        second = _open(conn)
        assert first.created and not second.created

    def test_unknown_type_rejected(self, conn):
        with pytest.raises(ValueError):
            incidents.open_incident("k", "ALIEN_INVASION", "high", "t", "a", conn=conn)

    def test_illegal_transition_rejected(self, conn):
        _open(conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "RESOLVED", "op", note="skip states", conn=conn)

    def test_resolution_requires_note(self, conn):
        _open(conn)
        for state in ("TRIAGED", "CONFIRMED", "RECOVERY_PROPOSED"):
            incidents.transition("inc-1", state, "op",
                                 verified_by=None, conn=conn)
        incidents.transition("inc-1", "RECOVERY_VERIFIED", "op",
                             verified_by="sentinel.verifier", conn=conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "RESOLVED", "op", note="", conn=conn)
        incidents.transition("inc-1", "RESOLVED", "op", note="root cause fixed", conn=conn)

    def test_recovery_verification_must_be_independent(self, conn):
        _open(conn)
        incidents.transition("inc-1", "TRIAGED", "op", conn=conn)
        incidents.transition("inc-1", "CONFIRMED", "op", conn=conn)
        incidents.transition("inc-1", "RECOVERY_PROPOSED", "op", conn=conn)
        with pytest.raises(TransitionError):
            incidents.transition("inc-1", "RECOVERY_VERIFIED", "op",
                                 verified_by="op", conn=conn)  # self-verification (SC4)

    def test_transitions_are_recorded_append_only(self, conn):
        _open(conn)
        incidents.transition("inc-1", "TRIAGED", "op", conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_incident_transitions")
        assert cur.fetchone()[0] == 1


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
