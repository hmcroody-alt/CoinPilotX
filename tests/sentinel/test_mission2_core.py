"""Mission 2 core: source trust, entity refs, health freshness, deterministic
detections, extended invariants, owner summary, UNDX hypothesis contract."""

from datetime import datetime, timedelta, timezone

import pytest

from services.sentinel import (detections, entities, events, health,
                               invariants, observability, providers,
                               source_trust, undx_interface)


def _now():
    return datetime.now(timezone.utc)


class TestSourceTrust:
    def test_unknown_grade_rejected(self):
        with pytest.raises(source_trust.SourceTrustError):
            source_trust.validate("TRUST_ME_BRO")

    def test_configured_is_never_healthy(self):
        assert source_trust.effective_health("HEALTHY", "CONFIGURED") != "HEALTHY"
        assert source_trust.effective_health("HEALTHY", "SIMULATED") != "HEALTHY"
        assert source_trust.effective_health("HEALTHY", "UNKNOWN") != "HEALTHY"

    def test_stale_trust_makes_healthy_stale(self):
        assert source_trust.effective_health("HEALTHY", "STALE") == "STALE"

    def test_weak_trust_never_softens_bad_news(self):
        # A FAILED observation stays FAILED regardless of provenance grade.
        assert source_trust.effective_health("FAILED", "CONFIGURED") == "FAILED"
        assert source_trust.effective_health("DEGRADED", "UNKNOWN") == "DEGRADED"

    def test_confidence_ceilings_ordered(self):
        c = source_trust.confidence_ceiling
        assert c("AUTHORITATIVE") >= c("DERIVED") > c("CONFIGURED") > c("UNKNOWN")


class TestEntities:
    def test_ref_roundtrip(self):
        ref = entities.make_ref("user", "42")
        assert ref == "user:42"
        assert entities.parse_ref(ref) == ("user", "42")

    def test_unknown_actor_type_rejected(self):
        with pytest.raises(entities.EntityRefError):
            entities.validate_actor_type("SUPERADMIN")

    def test_all_mission_actor_types_present(self):
        for t in ("USER", "ADMIN", "SELLER", "ADVERTISER", "SERVICE", "WORKER",
                  "PROVIDER", "WEBHOOK", "DEVICE", "SESSION", "UNDX_AGENT",
                  "RUNBOOK", "DEPLOYMENT", "SYSTEM"):
            entities.validate_actor_type(t)


class TestHealthFreshness:
    def test_no_snapshot_is_unknown_not_healthy(self, conn):
        assert health.current("worker:never_seen", conn=conn)["status"] == "UNKNOWN"

    def test_expired_healthy_decays_to_stale(self, conn):
        health.record(health.HealthSnapshot(
            component="worker:w1", status="HEALTHY", source_trust="MEASURED"),
            conn=conn)
        fresh = health.current("worker:w1", conn=conn)
        assert fresh["status"] == "HEALTHY" and fresh["fresh"]
        later = _now() + timedelta(hours=2)
        decayed = health.current("worker:w1", conn=conn, now=later)
        assert decayed["status"] == "STALE" and not decayed["fresh"]

    def test_expiry_must_follow_observation(self):
        with pytest.raises(health.HealthError):
            health.HealthSnapshot(
                component="x", status="HEALTHY", source_trust="MEASURED",
                observed_at="2026-08-13 10:00:00", expires_at="2026-08-13 09:00:00")

    def test_stale_count_counts_unfresh_components(self, conn):
        health.record(health.HealthSnapshot(
            component="worker:w1", status="HEALTHY", source_trust="MEASURED"),
            conn=conn)
        assert health.stale_count(conn=conn) == 0
        assert health.stale_count(conn=conn, now=_now() + timedelta(hours=2)) == 1


class TestDetections:
    def _heartbeat(self, conn, name, last_success):
        conn.execute("CREATE TABLE IF NOT EXISTS alert_worker_heartbeat ("
                     "worker_name TEXT, last_run_at TEXT, last_success_at TEXT, "
                     "error_count INTEGER, last_error TEXT)")
        conn.execute("INSERT INTO alert_worker_heartbeat VALUES (?, ?, ?, 0, '')",
                     (name, last_success, last_success))

    def test_stale_worker_opens_incident_and_health_snapshot(self, conn):
        old = (_now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        self._heartbeat(conn, "email_worker", old)
        result = detections.detect_stale_workers(conn=conn)
        assert len(result["findings"]) == 1 and result["findings"][0]["created"]
        assert health.current("worker:email_worker", conn=conn)["status"] == "FAILED"

    def test_fresh_worker_opens_nothing(self, conn):
        self._heartbeat(conn, "email_worker",
                        _now().strftime("%Y-%m-%d %H:%M:%S"))
        result = detections.detect_stale_workers(conn=conn)
        assert result["findings"] == []
        assert health.current("worker:email_worker", conn=conn)["status"] == "HEALTHY"

    def test_dead_letter_spike_threshold(self, conn):
        conn.execute("CREATE TABLE failed_email_queue (id INTEGER PRIMARY KEY, "
                     "status TEXT)")
        for _ in range(detections.DEAD_LETTER_SPIKE_THRESHOLD):
            conn.execute("INSERT INTO failed_email_queue (status) VALUES ('dead_letter')")
        result = detections.detect_dead_letter_spike(conn=conn)
        assert len(result["findings"]) == 1

    def test_failed_login_spike_from_bridged_stream(self, conn):
        for i in range(detections.FAILED_LOGIN_SPIKE_THRESHOLD):
            events.ingest(events.Event(
                category="AUTH", event_type="login_failed", severity="low",
                actor_id="sentinel.ingest", source="test",
                subject_type="user", subject_id="13",
                dedupe_key=f"spike-{i}"), conn=conn)
        result = detections.detect_failed_login_spike(conn=conn)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["subject"] == "user:13"
        # Deterministic + idempotent: re-run reuses the same incident.
        again = detections.detect_failed_login_spike(conn=conn)
        assert not again["findings"][0]["created"]

    def test_recovery_abuse_grouped_by_network_ref(self, conn):
        for i in range(detections.RECOVERY_ABUSE_THRESHOLD):
            events.ingest(events.Event(
                category="AUTH", event_type="password_reset_invalid_email",
                severity="low", actor_id="sentinel.ingest", source="test",
                network_ref="network:abc123", dedupe_key=f"ra-{i}"), conn=conn)
        result = detections.detect_recovery_abuse(conn=conn)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["subject"] == "network:abc123"

    def test_absent_source_tables_skip_never_crash(self, conn):
        results = detections.run_all(conn=conn)
        assert {r["rule"] for r in results} >= {"stale_worker", "dead_letter_spike"}
        assert all(r["findings"] == [] for r in results)


class TestExtendedInvariants:
    def _by_id(self, conn):
        return {r.invariant_id: r for r in invariants.run_all(conn=conn)}

    def test_refund_cap_violation(self, conn):
        conn.execute("CREATE TABLE pulse_ad_wallet_funding_sessions ("
                     "id INTEGER PRIMARY KEY, amount_cents INTEGER)")
        conn.execute("CREATE TABLE pulse_ad_refunds (id INTEGER PRIMARY KEY, "
                     "funding_session_id INTEGER, amount_cents INTEGER, status TEXT)")
        conn.execute("INSERT INTO pulse_ad_wallet_funding_sessions VALUES (1, 1000)")
        conn.execute("INSERT INTO pulse_ad_refunds VALUES (1, 1, 800, 'completed')")
        conn.execute("INSERT INTO pulse_ad_refunds VALUES (2, 1, 800, 'completed')")
        assert self._by_id(conn)["INV_REFUND_CAP"].status == invariants.STATUS_VIOLATED

    def test_paid_payout_requires_provider_ref(self, conn):
        conn.execute("CREATE TABLE seller_payouts (id INTEGER PRIMARY KEY, "
                     "amount_cents INTEGER, status TEXT, provider_payout_id TEXT)")
        conn.execute("INSERT INTO seller_payouts VALUES (1, 500, 'paid', '')")
        assert self._by_id(conn)["INV_PAYOUT_PROVIDER_REF"].status == invariants.STATUS_VIOLATED

    def test_closed_settlement_requires_snapshot(self, conn):
        conn.execute("CREATE TABLE settlement_batches (id INTEGER PRIMARY KEY, "
                     "status TEXT, closed_at TEXT)")
        conn.execute("INSERT INTO settlement_batches VALUES (1, 'closed', NULL)")
        assert self._by_id(conn)["INV_SETTLEMENT_SNAPSHOT"].status == invariants.STATUS_VIOLATED

    def test_ad_wallet_entry_requires_authority_ref(self, conn):
        conn.execute("CREATE TABLE pulse_ad_wallet_transactions ("
                     "id INTEGER PRIMARY KEY, amount_cents INTEGER, "
                     "idempotency_key TEXT)")
        conn.execute("INSERT INTO pulse_ad_wallet_transactions VALUES (1, 100, NULL)")
        assert self._by_id(conn)["INV_AD_WALLET_AUTHORITY"].status == invariants.STATUS_VIOLATED

    def test_privacy_violation_opens_data_exposure_incident(self, conn):
        # Redaction masks the VALUE but the key still landed in storage —
        # the invariant flags the association itself.
        events.ingest(events.Event(
            category="PRIVACY", event_type="leak_probe", severity="low",
            actor_id="sentinel.ingest", source="test",
            payload={"pulse_id": "P123"}, dedupe_key="leak-1"), conn=conn)
        by_id = self._by_id(conn)
        assert by_id["INV_NO_PULSE_ID_IN_SENTINEL"].status == invariants.STATUS_VIOLATED
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_incidents "
                    "WHERE incident_type = 'DATA_EXPOSURE'")
        assert cur.fetchone()[0] >= 1

    def test_clean_sentinel_store_passes_privacy_invariants(self, conn):
        by_id = self._by_id(conn)
        assert by_id["INV_NO_PULSE_ID_IN_SENTINEL"].status == invariants.STATUS_OK
        assert by_id["INV_NO_SECRETS_IN_SENTINEL"].status == invariants.STATUS_OK


class TestOwnerSummary:
    def test_empty_platform_reports_unknown_not_healthy(self, conn):
        s = observability.owner_summary(conn=conn)
        assert s["provider_status"] == "unknown"
        assert s["worker_status"] == "unknown"
        assert s["deployment_status"] == "unknown"
        assert s["overall_status"] == "ok"  # sentinel itself is fine, nothing open

    def test_critical_incident_drives_overall_status(self, conn):
        from services.sentinel import incidents
        incidents.open_incident("crit-1", "FINANCIAL_DISCREPANCY", "critical",
                                "t", "sentinel.invariants", conn=conn)
        s = observability.owner_summary(conn=conn)
        assert s["overall_status"] == "critical"
        assert s["critical_incidents"] == 1 and s["open_incidents"] == 1

    def test_degraded_provider_reflected(self, conn):
        providers.record_status("stripe", "webhook", "degraded", "5xx", conn=conn)
        assert observability.owner_summary(conn=conn)["provider_status"] == "degraded"

    def test_maturity_ladder(self, conn):
        assert observability.self_health(conn=conn)["maturity"] == "FUNCTIONAL"
        events.ingest(events.Event(
            category="SENTINEL_SELF", event_type="heartbeat", severity="info",
            actor_id="sentinel.ingest", source="test", dedupe_key="hb-1"), conn=conn)
        assert observability.self_health(conn=conn)["maturity"] == "RECENTLY_PROVEN"


class TestHypothesisContract:
    def _incident(self, conn):
        from services.sentinel import incidents
        incidents.open_incident("hyp-1", "ACCOUNT_TAKEOVER", "high", "t",
                                "sentinel.correlator", conn=conn)

    def _valid(self):
        return {
            "hypothesis": "credential stuffing from a single network",
            "confidence": 0.6,
            "supporting_evidence_ids": ["ev1", "ev2"],
            "contradicting_evidence_ids": [],
            "affected_domains": ["auth"],
            "estimated_impact": "medium",
            "recommended_next_step": "review sessions for subject",
            "required_authority": "OWNER_REVIEW",
            "missing_evidence": ["device history"],
        }

    def test_valid_hypothesis_stored_as_advisory(self, conn):
        self._incident(conn)
        out = undx_interface.submit_hypothesis("hyp-1", self._valid(), conn=conn)
        assert out["ok"] and out["authority"] == "ADVISORY"
        rows = events.recent(category="UNDX", conn=conn)
        assert rows and rows[0]["event_type"] == "model_hypothesis"
        assert rows[0]["severity"] == "info"  # a model never self-assigns severity

    def test_overconfident_hypothesis_rejected(self, conn):
        self._incident(conn)
        data = self._valid()
        data["confidence"] = 0.95  # above the DERIVED ceiling
        assert not undx_interface.submit_hypothesis("hyp-1", data, conn=conn)["ok"]

    def test_missing_and_unknown_fields_rejected(self, conn):
        self._incident(conn)
        short = self._valid()
        short.pop("missing_evidence")
        assert not undx_interface.submit_hypothesis("hyp-1", short, conn=conn)["ok"]
        extra = self._valid()
        extra["execute_now"] = True
        assert not undx_interface.submit_hypothesis("hyp-1", extra, conn=conn)["ok"]

    def test_self_granted_authority_rejected(self, conn):
        self._incident(conn)
        data = self._valid()
        data["required_authority"] = "SELF_GRANTED"
        assert not undx_interface.submit_hypothesis("hyp-1", data, conn=conn)["ok"]

    def test_unknown_incident_fails_closed(self, conn):
        assert not undx_interface.submit_hypothesis("nope", self._valid(), conn=conn)["ok"]
