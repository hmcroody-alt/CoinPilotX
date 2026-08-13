"""Mission 2, Stage 28 — adversarial suite. Every test attacks a boundary and
asserts the system fails CLOSED: rejection, containment, or honest downgrade —
never silent acceptance."""

import pytest

from services.sentinel import (events, health, invariants, providers,
                               undx_interface)
from services.sentinel.events import Event, EventRejected


def _event(**kw):
    base = dict(category="SECURITY", event_type="probe", severity="low",
                actor_id="sentinel.ingest", source="test")
    base.update(kw)
    return Event(**base)


class TestMalformedInput:
    def test_non_dict_payload_rejected(self):
        with pytest.raises(EventRejected):
            _event(payload="not a dict")

    def test_non_dict_policy_context_rejected(self):
        with pytest.raises(EventRejected):
            _event(policy_context=["a", "b"])

    def test_unknown_impact_level_rejected(self):
        with pytest.raises(EventRejected):
            _event(security_impact="apocalyptic")

    def test_malformed_typed_ref_rejected(self):
        with pytest.raises(EventRejected):
            _event(network_ref="not_a_known_type:x")


class TestSecretsInMetadata:
    def test_secret_named_field_redacted_before_persist(self, conn):
        events.ingest(_event(payload={"api_key": "sk_test_leak_me",
                                      "detail": "ok"},
                             dedupe_key="sec-1"), conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT payload_json FROM sentinel_events WHERE dedupe_key='sec-1'")
        stored = cur.fetchone()[0]
        assert "sk_test_" not in stored and "[REDACTED" in stored

    def test_value_smuggled_secret_caught_by_invariant(self, conn):
        # A secret hidden under an innocuous key survives key-based redaction —
        # the storage invariant is the second net.
        events.ingest(_event(payload={"note": "use sk_" + "live_abc123 for prod"},
                             dedupe_key="sec-2"), conn=conn)
        by_id = {r.invariant_id: r for r in invariants.run_all(conn=conn)}
        assert by_id["INV_NO_SECRETS_IN_SENTINEL"].status == invariants.STATUS_VIOLATED


class TestForgedIdentityAndConfidence:
    def test_forged_actor_type_rejected(self):
        with pytest.raises(EventRejected):
            _event(actor_type="ROOT")

    def test_confidence_above_trust_ceiling_rejected(self):
        with pytest.raises(EventRejected):
            _event(source_trust="CONFIGURED", confidence=0.9)

    def test_unknown_source_trust_rejected(self):
        with pytest.raises(EventRejected):
            _event(source_trust="ABSOLUTE")


class TestDuplicatesAndStaleness:
    def test_duplicate_event_ingested_once(self, conn):
        ev = _event(dedupe_key="dup-1")
        assert events.ingest(ev, conn=conn) is True
        assert events.ingest(ev, conn=conn) is False
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_events WHERE dedupe_key='dup-1'")
        assert cur.fetchone()[0] == 1

    def test_stale_health_observation_never_reads_healthy(self, conn):
        from datetime import datetime, timedelta, timezone
        health.record(health.HealthSnapshot(
            component="provider:stripe/webhook", status="HEALTHY",
            source_trust="MEASURED"), conn=conn)
        later = datetime.now(timezone.utc) + timedelta(days=1)
        assert health.current("provider:stripe/webhook", conn=conn,
                              now=later)["status"] == "STALE"


class TestContradictoryProviderStates:
    def test_latest_recorded_state_wins_and_is_single(self, conn):
        providers.record_status("stripe", "checkout", "up", conn=conn)
        providers.record_status("stripe", "checkout", "down", "outage", conn=conn)
        assert providers.capability_status("stripe", "checkout", conn=conn) == "down"
        assert len(providers.health_table(conn=conn)) == 1


class TestConfiguredIsNotHealthy:
    def test_configured_healthy_claim_downgraded_at_write(self, conn):
        snap = health.HealthSnapshot(
            component="provider:brevo/email_send", status="HEALTHY",
            source_trust="CONFIGURED", measurement="BREVO_API_KEY is set")
        assert snap.status != "HEALTHY"  # capped before it ever persists
        health.record(snap, conn=conn)
        assert health.current("provider:brevo/email_send",
                              conn=conn)["status"] != "HEALTHY"

    def test_configured_confidence_capped(self):
        with pytest.raises(health.HealthError):
            health.HealthSnapshot(
                component="provider:brevo/email_send", status="DEGRADED",
                source_trust="CONFIGURED", confidence=0.9)


class TestUndxCannotMutate:
    def test_unknown_surface_fails_closed(self, conn):
        out = undx_interface.read("delete_incidents", conn=conn)
        assert not out["ok"] and "SC15" in out["error"]

    def test_interface_exposes_no_mutation_functions(self):
        public = [n for n in dir(undx_interface) if not n.startswith("_")]
        for banned in ("transition", "resolve", "suppress", "delete",
                       "execute", "restart", "block"):
            assert not any(banned in n.lower() for n in public), banned

    def test_analysis_severity_is_forced_advisory(self, conn):
        undx_interface.submit_analysis("user", "7", "looks suspicious", 0.9,
                                       conn=conn)
        rows = events.recent(category="UNDX", conn=conn)
        assert rows[0]["severity"] == "info"


class TestApiRequiresAdmin:
    def test_summary_denied_without_admin_session(self):
        flask = pytest.importorskip("flask")
        from services.sentinel.api import sentinel_bp
        app = flask.Flask(__name__)
        app.secret_key = "test-only"
        app.register_blueprint(sentinel_bp)
        client = app.test_client()
        for path in ("/api/admin/sentinel/summary", "/api/admin/sentinel/events",
                     "/api/admin/sentinel/incidents", "/api/admin/sentinel/health"):
            assert client.get(path).status_code == 403
