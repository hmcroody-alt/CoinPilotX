"""Stages 9-13 + 26-28: graph edges, journeys, invariants (read-only),
provider health, breaker contract, adapters, metrics, self-health, bridge."""

import pytest

from services.sentinel import (adapters, graph, invariants, journeys,
                               observability, providers, security_center_bridge)
from services.sentinel.providers import CircuitBreaker


class TestGraph:
    def test_upsert_accumulates_weight(self, conn):
        graph.upsert_edge("user", "1", "used_device", "device", "d1", conn=conn)
        graph.upsert_edge("user", "1", "used_device", "device", "d1", conn=conn)
        out = graph.neighbors("user", "1", conn=conn)
        assert len(out) == 1 and out[0]["weight"] == 2.0

    def test_unknown_edge_type_rejected(self, conn):
        with pytest.raises(ValueError):
            graph.upsert_edge("user", "1", "teleported_to", "place", "x", conn=conn)

    def test_shared_destination_reverse_lookup(self, conn):
        graph.upsert_edge("user", "1", "used_device", "device", "d1", conn=conn)
        graph.upsert_edge("user", "2", "used_device", "device", "d1", conn=conn)
        cluster = graph.shared_destination("device", "d1", "used_device", conn=conn)
        assert {c["src_id"] for c in cluster} == {"1", "2"}


class TestJourneys:
    def test_six_canonical_journeys_declared(self):
        assert set(journeys.JOURNEYS) == {"AUTH", "CHECKOUT", "SETTLEMENT",
                                          "AD_DELIVERY", "DEPLOYMENT", "NATIVE_API"}

    def test_complete_journey_evaluates_complete(self):
        observed = [
            {"category": "AUTH", "event_type": "login_attempt"},
            {"category": "AUTH", "event_type": "login_succeeded"},
            {"category": "SESSION", "event_type": "session_used"},
        ]
        result = journeys.evaluate("AUTH", observed)
        assert result["complete"] and result["broken_step"] is None

    def test_broken_step_identified(self):
        observed = [{"category": "AUTH", "event_type": "login_attempt"}]
        result = journeys.evaluate("AUTH", observed)
        assert not result["complete"] and result["broken_step"] == "login_result"

    def test_unknown_journey_fails_closed(self):
        with pytest.raises(ValueError):
            journeys.evaluate("TIME_TRAVEL", [])


class TestInvariants:
    def test_missing_tables_skip_not_violate(self, conn):
        results = invariants.run_all(conn=conn)
        by_id = {r.invariant_id: r for r in results}
        assert by_id["INV_LEDGER_BALANCED"].status == invariants.STATUS_SKIPPED
        assert by_id["INV_EVIDENCE_CHAIN"].status == invariants.STATUS_OK

    def test_violation_opens_incident_without_mutating_state(self, conn):
        conn.execute("CREATE TABLE pulse_ad_wallets (id INTEGER PRIMARY KEY, balance_cents INTEGER)")
        conn.execute("INSERT INTO pulse_ad_wallets (balance_cents) VALUES (-500)")
        results = invariants.run_all(conn=conn)
        by_id = {r.invariant_id: r for r in results}
        assert by_id["INV_AD_WALLET_NON_NEGATIVE"].status == invariants.STATUS_VIOLATED
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_incidents WHERE incident_type='INVARIANT_VIOLATION'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT balance_cents FROM pulse_ad_wallets")
        assert cur.fetchone()[0] == -500  # observed, never corrected


class TestProvidersAndBreaker:
    def test_unrecorded_capability_is_unknown_not_up(self, conn):
        assert providers.capability_status("stripe", "payouts", conn=conn) == "unknown"

    def test_status_upsert(self, conn):
        providers.record_status("stripe", "checkout", "up", conn=conn)
        providers.record_status("stripe", "checkout", "degraded", "5xx spike", conn=conn)
        assert providers.capability_status("stripe", "checkout", conn=conn) == "degraded"
        assert len(providers.health_table(conn=conn)) == 1

    def test_invalid_status_rejected(self, conn):
        with pytest.raises(ValueError):
            providers.record_status("stripe", "checkout", "fabulous", conn=conn)

    def test_breaker_contract_state_machine(self):
        b = CircuitBreaker("test", failure_threshold=2, recovery_timeout_seconds=10)
        assert b.allow_request(now=0)
        b.record_failure(now=0)
        b.record_failure(now=1)
        assert b.state == "open" and not b.allow_request(now=5)
        assert b.allow_request(now=12) and b.state == "half_open"
        b.record_success()
        assert b.state == "closed"

    def test_breaker_rejects_unbounded_thresholds(self):
        with pytest.raises(ValueError):
            CircuitBreaker("bad", failure_threshold=0)


class TestAdaptersAndSharing:
    SPEC = adapters.AdapterSpec("iprep", "ExampleVendor", "IP reputation", ("ip_risk",))

    def test_external_severity_is_capped(self):
        ev = adapters.normalize_signal(self.SPEC, "ip_risk", "ip", "1.2.3.4",
                                       "critical", {"score": 99})
        assert ev.severity == "medium"
        assert ev.payload["verified"] is False

    def test_undeclared_signal_type_rejected(self):
        with pytest.raises(ValueError):
            adapters.normalize_signal(self.SPEC, "dna_match", "ip", "1.2.3.4", "low", {})

    def test_outbound_minimize_strips_sensitive_fields(self):
        out = adapters.outbound_filter({
            "public_profile_name": "x", "email": "a@b.c", "password": "p",
            "pulse_id": "P1", "mystery": "m"})
        assert "email" not in out and "password" not in out
        assert "pulse_id" not in out and "mystery" not in out


class TestObservabilityAndBridge:
    def test_unknown_metric_rejected_without_raising(self, conn):
        assert observability.record("made_up_metric", conn=conn) is False

    def test_metrics_summarize(self, conn):
        observability.record("events_ingested", 1, conn=conn)
        observability.record("events_ingested", 1, conn=conn)
        summary = observability.summary(conn=conn)
        assert summary["events_ingested"]["count"] == 2

    def test_self_health_reports_chain_and_store(self, conn):
        health = observability.self_health(conn=conn)
        assert health["store_ok"] and health["evidence_chain_ok"] and health["ok"]

    def test_bridge_tolerates_missing_source_tables(self, conn):
        result = security_center_bridge.sync_security_events(conn=conn)
        assert result == {"ingested": 0, "deduped": 0, "skipped": 0}

    def test_bridge_ingests_and_dedupes(self, conn):
        # Real platform schema: the payload column is details_json (the
        # Mission 1 bridge asked for `details` and silently lost this table).
        conn.execute("CREATE TABLE security_events (id INTEGER PRIMARY KEY, "
                     "event_type TEXT, user_id INTEGER, ip_address TEXT, path TEXT, "
                     "status TEXT, details_json TEXT, created_at TEXT)")
        conn.execute("INSERT INTO security_events (event_type, user_id, ip_address, "
                     "path, status, details_json, created_at) VALUES "
                     "('unusual_device', 42, '10.0.0.1', '/login', 'observed', '{}', "
                     "'2026-08-13T10:00:00')")
        first = security_center_bridge.sync_security_events(conn=conn)
        assert first["ingested"] == 1
        second = security_center_bridge.sync_security_events(conn=conn)
        assert second["ingested"] == 0 and second["deduped"] == 1
        # Raw IP never persists; 'T' timestamp is normalised; trust recorded.
        cur = conn.cursor()
        cur.execute("SELECT network_ref, occurred_at, source_trust, actor_type, "
                    "payload_json FROM sentinel_events")
        row = cur.fetchone()
        assert row[0].startswith("network:") and "10.0.0.1" not in str(row[4])
        assert row[1] == "2026-08-13 10:00:00"
        assert row[2] == "AUTHORITATIVE" and row[3] == "USER"

    def test_bridge_skips_unmapped_types_not_guesses(self, conn):
        conn.execute("CREATE TABLE security_events (id INTEGER PRIMARY KEY, "
                     "event_type TEXT, user_id INTEGER, ip_address TEXT, path TEXT, "
                     "status TEXT, details_json TEXT, created_at TEXT)")
        conn.execute("INSERT INTO security_events (event_type, user_id, ip_address, "
                     "path, status, details_json, created_at) VALUES "
                     "('some_novel_type', 1, '', '', '', '{}', '2026-08-13T10:00:00')")
        result = security_center_bridge.sync_security_events(conn=conn)
        assert result["ingested"] == 0 and result["skipped"] == 1

    def test_bridge_maps_real_auth_event_types(self, conn):
        conn.execute("CREATE TABLE auth_events (id INTEGER PRIMARY KEY, event_type TEXT, "
                     "user_id INTEGER, email_hash TEXT, status TEXT, severity TEXT, "
                     "ip_address TEXT, country TEXT, device TEXT, route TEXT, created_at TEXT)")
        conn.execute("INSERT INTO auth_events (event_type, user_id, email_hash, status, "
                     "severity, ip_address, country, device, route, created_at) VALUES "
                     "('forgot_password_invalid_email', 0, 'abc123', 'invalid', 'low', "
                     "'1.2.3.4', 'US', 'iPhone', '/forgot', '2026-08-13T09:00:00')")
        result = security_center_bridge.sync_security_events(conn=conn)
        assert result["ingested"] == 1
        cur = conn.cursor()
        cur.execute("SELECT event_type, subject_type, subject_id FROM sentinel_events")
        row = cur.fetchone()
        assert row[0] == "password_reset_invalid_email"
        assert row[1] == "email_hash" and row[2] == "abc123"
