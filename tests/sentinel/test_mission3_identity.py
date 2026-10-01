"""Mission 3 — identity threat + behavioral detection foundation.

Covers: identity entities, the SessionTrust contract (UNKNOWN never TRUSTED,
fusion gate, confidence ceilings, mandatory decay), the deterministic
sequence engine and ATO chains, identity detections (including the
false-positive suite: normal behavior must NOT open incidents), explainable
baselines, exclusions, invariants, owner summary, and the UNDX identity
context. Detection ≠ guilt is asserted throughout: nothing here blocks,
bans, or invalidates anything.
"""

from datetime import datetime, timedelta, timezone

import json

import pytest

from services.sentinel import (
    entities, events, identity_detections, identity_trust, incidents,
    invariants, observability, sequences, undx_interface,
)

_TS = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now(timezone.utc)


def _ago(minutes=0, days=0, now=None):
    return ((now or _now()) - timedelta(minutes=minutes, days=days)).strftime(_TS)


def _ingest(conn, event_type, *, category="AUTH", subject="77", network=None,
            actor="sentinel.ingest", occurred_at=None, dedupe=None, severity="low"):
    events.ingest(events.Event(
        category=category, event_type=event_type, severity=severity,
        actor_id=actor, source="test", subject_type="user", subject_id=subject,
        network_ref=network, occurred_at=occurred_at or _ago(0),
        dedupe_key=dedupe or f"{event_type}-{subject}-{network}-{occurred_at}"),
        conn=conn)


def _incident_count(conn, incident_type, like=None):
    cur = conn.cursor()
    if like:
        cur.execute("SELECT COUNT(*) FROM sentinel_incidents WHERE incident_type = ? "
                    "AND title LIKE ?", (incident_type, f"%{like}%"))
    else:
        cur.execute("SELECT COUNT(*) FROM sentinel_incidents WHERE incident_type = ?",
                    (incident_type,))
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Stage 2 — identity entity types
# ---------------------------------------------------------------------------

class TestIdentityEntities:
    def test_new_identity_types_are_valid(self):
        for etype in ("ip", "asn", "auth_attempt", "recovery_attempt",
                      "session", "device", "network"):
            assert etype in entities.ENTITY_TYPES
            ref = entities.make_ref(etype, "abc123")
            assert entities.parse_ref(ref) == (etype, "abc123")

    def test_unknown_type_fails_closed(self):
        with pytest.raises(entities.EntityRefError):
            entities.make_ref("fingerprint", "x")   # we do NOT fingerprint
        assert not entities.is_valid_ref("fingerprint:x")


# ---------------------------------------------------------------------------
# Stage 3, 15–17 — the SessionTrust contract
# ---------------------------------------------------------------------------

class TestSessionTrustContract:
    def test_unknown_provenance_can_never_be_trusted(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="TRUSTED",
                risk_score=0.0, source_trust="UNKNOWN")

    def test_trusted_incompatible_with_material_risk(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="TRUSTED",
                risk_score=0.5, reasons=("some risk",),
                source_trust="AUTHORITATIVE")

    def test_high_risk_requires_two_independent_signals(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="HIGH_RISK", risk_score=0.9,
                dimensions={"session_risk": 0.9}, reasons=("one loud signal",),
                evidence_refs=("evt-1",))

    def test_high_risk_requires_evidence(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="HIGH_RISK", risk_score=0.9,
                dimensions={"session_risk": 0.9, "credential_risk": 0.8},
                reasons=("two signals",))

    def test_high_risk_valid_with_corroboration_and_evidence(self):
        t = identity_trust.SessionTrust(
            subject_ref="session:1", trust_state="HIGH_RISK", risk_score=0.9,
            dimensions={"session_risk": 0.9, "credential_risk": 0.8},
            reasons=("reuse", "burst"), evidence_refs=("evt-1", "evt-2"))
        assert t.expires_at  # decay is mandatory
        assert t.confidence <= 0.8  # DERIVED ceiling

    def test_risk_without_reasons_is_invalid(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="ELEVATED", risk_score=0.5,
                dimensions={"credential_risk": 0.5})

    def test_confidence_never_exceeds_provenance_ceiling(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="NORMAL", risk_score=0.0,
                source_trust="DERIVED", confidence=0.95)

    def test_unknown_dimension_fails_closed(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.SessionTrust(
                subject_ref="session:1", trust_state="NORMAL", risk_score=0.0,
                dimensions={"vibes_risk": 0.4}, reasons=("?",))


class TestEvaluateSession:
    def test_platform_compromise_revoke_is_loud_but_single_signal_caps_elevated(self):
        t = identity_trust.evaluate_session({
            "session_ref": "session:9", "status": "revoked",
            "revoked_reason": "refresh_token_reuse",
            "source_trust": "AUTHORITATIVE"})
        assert t.dimensions["session_risk"] == 0.9
        assert t.trust_state == "ELEVATED"       # honesty: no corroboration yet
        assert any("capped at" in r.lower() or "capped" in r.lower()
                   for r in t.reasons)

    def test_two_signals_plus_evidence_reach_high_risk(self):
        t = identity_trust.evaluate_session({
            "session_ref": "session:9", "status": "revoked",
            "revoked_reason": "device_mismatch", "recent_failed_logins": 9,
            "source_trust": "AUTHORITATIVE", "evidence_refs": ("evt-1",)})
        assert t.trust_state == "HIGH_RISK"
        assert t.source_trust == "DERIVED"       # the judgment is computed

    def test_known_device_active_authoritative_is_trusted(self):
        t = identity_trust.evaluate_session({
            "session_ref": "session:9", "status": "active",
            "device_known": True, "source_trust": "AUTHORITATIVE",
            "last_seen_at": _ago(5)})
        assert t.trust_state == "TRUSTED"
        assert "device previously seen" in " ".join(t.contradicting)

    def test_normal_session_stays_normal_no_false_positive(self):
        t = identity_trust.evaluate_session({
            "session_ref": "session:9", "status": "active",
            "recent_failed_logins": 0, "source_trust": "DERIVED",
            "last_seen_at": _ago(5)})
        assert t.trust_state == "NORMAL"
        assert t.risk_score == 0.0
        assert t.contradicting  # innocence is recorded, not just absence

    def test_unknown_provenance_fails_closed_to_unknown(self):
        t = identity_trust.evaluate_session({
            "session_ref": "session:9", "status": "active",
            "device_known": True, "source_trust": "UNKNOWN"})
        assert t.trust_state == "UNKNOWN"

    def test_new_device_risk_is_capped_and_labeled_client_reported(self):
        t = identity_trust.evaluate_session({
            "session_ref": "session:9", "status": "active",
            "device_known": False, "source_trust": "AUTHORITATIVE"})
        assert t.dimensions["device_risk"] <= identity_trust.DEVICE_RISK_CAP
        assert any("CLIENT_REPORTED" in r for r in t.reasons)

    def test_invalid_session_ref_rejected(self):
        with pytest.raises(identity_trust.IdentityTrustError):
            identity_trust.evaluate_session({"session_ref": "not-a-ref"})


class TestRiskDecay:
    def test_latest_live_observation(self, conn):
        t = identity_trust.evaluate_session({
            "session_ref": "session:42", "status": "revoked",
            "revoked_reason": "refresh_token_reuse", "source_trust": "AUTHORITATIVE"})
        identity_trust.record(t, conn=conn)
        out = identity_trust.latest("session:42", conn=conn)
        assert out["expired"] is False
        assert out["trust_state"] == "ELEVATED"

    def test_expired_high_risk_degrades_to_stale_and_inactive(self, conn):
        t = identity_trust.SessionTrust(
            subject_ref="session:43", trust_state="HIGH_RISK", risk_score=0.9,
            dimensions={"session_risk": 0.9, "credential_risk": 0.8},
            reasons=("r1", "r2"), evidence_refs=("evt-1",),
            observed_at=_ago(minutes=600))   # ttl 240m → expired 6h ago
        identity_trust.record(t, conn=conn)
        out = identity_trust.latest("session:43", conn=conn)
        assert out["expired"] is True
        assert out["trust_state"] == "STALE"
        assert out["risk_score"] == 0.0
        assert identity_trust.active_high_risk(conn=conn) == []
        # And the invariant agrees: no expired risk is active.
        by_id = {r.invariant_id: r for r in invariants.run_all(conn=conn)}
        assert by_id["INV_EXPIRED_RISK_INACTIVE"].status != invariants.STATUS_VIOLATED

    def test_active_high_risk_lists_fresh_observations(self, conn):
        t = identity_trust.SessionTrust(
            subject_ref="session:44", trust_state="HIGH_RISK", risk_score=0.9,
            dimensions={"session_risk": 0.9, "credential_risk": 0.8},
            reasons=("r1", "r2"), evidence_refs=("evt-1",))
        identity_trust.record(t, conn=conn)
        active = identity_trust.active_high_risk(conn=conn)
        assert [a["subject_ref"] for a in active] == ["session:44"]


# ---------------------------------------------------------------------------
# Stages 6–7 — deterministic sequence engine + ATO chains
# ---------------------------------------------------------------------------

def _ato1_events(conn, subject="77", *, with_device=True, now=None):
    now = now or _now()
    _ingest(conn, "password_reset_requested", subject=subject,
            occurred_at=_ago(50, now=now), dedupe=f"a1r-{subject}")
    _ingest(conn, "login_succeeded", subject=subject,
            occurred_at=_ago(40, now=now), dedupe=f"a1l-{subject}")
    if with_device:
        _ingest(conn, "unusual_device", category="SECURITY", subject=subject,
                severity="medium", occurred_at=_ago(30, now=now),
                dedupe=f"a1d-{subject}")


class TestSequenceEngine:
    def test_ato1_full_chain_fires_with_evidence(self, conn):
        now = _now()
        _ato1_events(conn, now=now)
        firings = sequences.evaluate(sequences.ATO_SEQUENCES[0], conn=conn, now=now)
        assert len(firings) == 1
        f = firings[0]
        assert f["completeness"] == "FULL"
        assert f["subject_ref"] == "user:77"
        assert len(f["matched_event_ids"]) == 3   # evidence linkage

    def test_missing_optional_step_is_partial_never_faked(self, conn):
        now = _now()
        _ato1_events(conn, with_device=False, now=now)
        firings = sequences.evaluate(sequences.ATO_SEQUENCES[0], conn=conn, now=now)
        assert len(firings) == 1
        assert firings[0]["completeness"] == "PARTIAL"
        assert firings[0]["missing_optional_steps"] == ["new_device"]

    def test_missing_required_step_does_not_fire(self, conn):
        now = _now()
        _ingest(conn, "password_reset_requested", occurred_at=_ago(50, now=now))
        firings = sequences.evaluate(sequences.ATO_SEQUENCES[0], conn=conn, now=now)
        assert firings == []

    def test_out_of_order_does_not_fire(self, conn):
        now = _now()
        _ingest(conn, "login_succeeded", occurred_at=_ago(50, now=now))
        _ingest(conn, "password_reset_requested", occurred_at=_ago(40, now=now))
        firings = sequences.evaluate(sequences.ATO_SEQUENCES[0], conn=conn, now=now)
        assert firings == []

    def test_failed_burst_min_count_enforced(self, conn):
        now = _now()
        for i in range(4):   # below min_count=5
            _ingest(conn, "login_failed", occurred_at=_ago(60 - i, now=now),
                    dedupe=f"fb-{i}")
        _ingest(conn, "login_succeeded", occurred_at=_ago(10, now=now))
        assert sequences.evaluate(sequences.ATO_SEQUENCES[1], conn=conn, now=now) == []
        _ingest(conn, "login_failed", occurred_at=_ago(55, now=now), dedupe="fb-5")
        firings = sequences.evaluate(sequences.ATO_SEQUENCES[1], conn=conn, now=now)
        assert len(firings) == 1

    def test_cooldown_prevents_alert_storm(self, conn):
        now = _now()
        _ato1_events(conn, now=now)
        assert len(sequences.evaluate(sequences.ATO_SEQUENCES[0], conn=conn, now=now)) == 1
        assert sequences.evaluate(sequences.ATO_SEQUENCES[0], conn=conn, now=now) == []

    def test_definition_validation_fails_closed(self):
        with pytest.raises(ValueError):
            sequences.SequenceDefinition(
                sequence_id="X", title="all optional",
                steps=(sequences.SequenceStep("s", ("e",), optional=True),),
                window_minutes=10, incident_type="T", severity="low")
        with pytest.raises(ValueError):
            sequences.SequenceDefinition(
                sequence_id="X", title="unbounded",
                steps=(sequences.SequenceStep("s", ("e",)),),
                window_minutes=0, incident_type="T", severity="low")


class TestAtoCorrelation:
    def test_multiple_chains_collapse_into_one_incident(self, conn):
        now = _now()
        _ato1_events(conn, now=now)                      # ATO1 (+ATO4 prefix)
        _ingest(conn, "email_changed", category="SECURITY", subject="77",
                severity="medium", occurred_at=_ago(20, now=now))  # ATO3 + ATO4
        result = identity_detections.detect_ato_chains(conn=conn, now=now)
        assert len(result["findings"]) == 1              # ONE incident (Stage 20)
        assert _incident_count(conn, "ACCOUNT_TAKEOVER_SUSPECTED") == 1
        # Re-run: cooldown holds, still exactly one incident, no duplicates.
        identity_detections.detect_ato_chains(conn=conn, now=now)
        assert _incident_count(conn, "ACCOUNT_TAKEOVER_SUSPECTED") == 1

    def test_incident_carries_chains_and_recommendations(self, conn):
        now = _now()
        _ato1_events(conn, now=now)
        result = identity_detections.detect_ato_chains(conn=conn, now=now)
        key = result["findings"][0]["incident_key"]
        inc = incidents.get(key, conn=conn)
        detail = inc.get("detail") or {}
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert detail["chains"]
        recs = " ".join(detail["recommended_actions"]).lower()
        assert "human" in recs or "review" in recs
        for forbidden in ("ban", "lockout", "seize"):
            assert forbidden not in recs


# ---------------------------------------------------------------------------
# Stages 8–12 — identity detections + false-positive suite
# ---------------------------------------------------------------------------

class TestCredentialStuffing:
    def test_fanout_fires_at_threshold(self, conn):
        now = _now()
        for acct in range(identity_detections.STUFFING_FANOUT_MIN_ACCOUNTS):
            for j in range(2):   # 6 accounts x 2 = 12 failures
                _ingest(conn, "login_failed", subject=f"u{acct}",
                        network="network:evil01", occurred_at=_ago(10, now=now),
                        dedupe=f"fo-{acct}-{j}")
        result = identity_detections.detect_credential_stuffing_fanout(conn=conn, now=now)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["subject"] == "network:evil01"

    def test_fanout_below_threshold_is_silent(self, conn):
        now = _now()
        for acct in range(identity_detections.STUFFING_FANOUT_MIN_ACCOUNTS - 1):
            for j in range(3):
                _ingest(conn, "login_failed", subject=f"u{acct}",
                        network="network:office1", occurred_at=_ago(10, now=now),
                        dedupe=f"fp-{acct}-{j}")
        result = identity_detections.detect_credential_stuffing_fanout(conn=conn, now=now)
        assert result["findings"] == []   # an office full of typos is not an attack

    def test_fanin_distributed_attack_on_one_account(self, conn):
        now = _now()
        for n in range(identity_detections.STUFFING_FANIN_MIN_NETWORKS):
            _ingest(conn, "login_failed", subject="99", network=f"network:bot{n}",
                    occurred_at=_ago(10, now=now), dedupe=f"fi-{n}")
        result = identity_detections.detect_credential_stuffing_fanin(conn=conn, now=now)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["subject"] == "user:99"


class TestRecoveryAbuseV2:
    def test_wide_recovery_probing_fires_enumeration_safe(self, conn):
        now = _now()
        for t in range(identity_detections.RECOVERY_V2_MIN_TARGETS):
            _ingest(conn, "password_reset_no_match", subject=f"victim{t}@mail.com",
                    network="network:probe1", occurred_at=_ago(10, now=now),
                    dedupe=f"rv-{t}")
        result = identity_detections.detect_recovery_abuse_v2(conn=conn, now=now)
        assert len(result["findings"]) == 1
        key = result["findings"][0]["incident_key"]
        inc = incidents.get(key, conn=conn)
        blob = json.dumps(inc)
        assert "victim0@mail.com" not in blob    # enumeration resistance
        assert "withheld" in blob

    def test_single_user_forgetting_password_is_not_abuse(self, conn):
        now = _now()
        for i in range(4):
            _ingest(conn, "password_reset_requested", subject="42",
                    network="network:home1", occurred_at=_ago(10, now=now),
                    dedupe=f"forgot-{i}")
        result = identity_detections.detect_recovery_abuse_v2(conn=conn, now=now)
        assert result["findings"] == []


class TestSessionAnomalies:
    def test_platform_compromise_indicator_opens_incident(self, conn):
        now = _now()
        _ingest(conn, "refresh_token_reuse", category="SESSION", subject="7",
                severity="high", occurred_at=_ago(30, now=now))
        result = identity_detections.detect_session_compromise_indicators(conn=conn, now=now)
        assert len(result["findings"]) == 1
        assert _incident_count(conn, "SESSION_ANOMALY") == 1

    def test_session_burst_threshold(self, conn):
        now = _now()
        for i in range(identity_detections.SESSION_BURST_THRESHOLD - 1):
            _ingest(conn, "login_succeeded", subject="8",
                    occurred_at=_ago(10, now=now), dedupe=f"sb-{i}")
        assert identity_detections.detect_session_burst(conn=conn, now=now)["findings"] == []
        _ingest(conn, "login_succeeded", subject="8",
                occurred_at=_ago(9, now=now), dedupe="sb-last")
        result = identity_detections.detect_session_burst(conn=conn, now=now)
        assert len(result["findings"]) == 1


class TestDeviceAndNetwork:
    def _sessions_table(self, conn):
        conn.execute(
            "CREATE TABLE mobile_security_sessions (id INTEGER PRIMARY KEY, "
            "user_id INTEGER, device_hash TEXT, ip_hash TEXT, status TEXT, "
            "revoked_reason TEXT, last_seen_at TEXT)")

    def test_family_sharing_a_device_is_not_an_incident(self, conn):
        self._sessions_table(conn)
        for uid in range(3):    # a family of three
            conn.execute("INSERT INTO mobile_security_sessions "
                         "(user_id, device_hash, status, last_seen_at) "
                         "VALUES (?, 'famdev01', 'active', ?)", (uid, _ago(60)))
        result = identity_detections.detect_shared_device_cluster(conn=conn)
        assert result["findings"] == []   # Stage 11: shared ≠ malicious

    def test_device_farm_fires_with_legitimacy_note(self, conn):
        self._sessions_table(conn)
        for uid in range(identity_detections.SHARED_DEVICE_MIN_USERS):
            conn.execute("INSERT INTO mobile_security_sessions "
                         "(user_id, device_hash, status, last_seen_at) "
                         "VALUES (?, 'farmdev1', 'active', ?)", (uid, _ago(60)))
        result = identity_detections.detect_shared_device_cluster(conn=conn)
        assert len(result["findings"]) == 1
        key = result["findings"][0]["incident_key"]
        blob = json.dumps(incidents.get(key, conn=conn))
        assert "legitimate" in blob         # honesty in the incident itself
        assert "CLIENT_REPORTED" in blob    # signal quality disclosed

    def test_absent_platform_table_skips_never_crashes(self, conn):
        result = identity_detections.detect_shared_device_cluster(conn=conn)
        assert result["skipped"] is True

    def test_network_many_accounts_notes_shared_networks(self, conn):
        now = _now()
        for u in range(identity_detections.NETWORK_MANY_ACCOUNTS_THRESHOLD):
            _ingest(conn, "login_succeeded", subject=f"n{u}",
                    network="network:campus1", occurred_at=_ago(10, now=now),
                    dedupe=f"nm-{u}")
        result = identity_detections.detect_network_many_accounts(conn=conn, now=now)
        assert len(result["findings"]) == 1
        blob = json.dumps(incidents.get(result["findings"][0]["incident_key"], conn=conn))
        assert "no external reputation" in blob   # Stage 5/34


class TestAdminIdentity:
    def _admin_logs(self, conn):
        conn.execute(
            "CREATE TABLE admin_session_logs (id INTEGER PRIMARY KEY, "
            "admin_id INTEGER, action TEXT, ip_hash TEXT, created_at TEXT)")

    def test_admin_from_unseen_network_opens_review_incident(self, conn):
        self._admin_logs(conn)
        for d in range(5, 15):
            conn.execute("INSERT INTO admin_session_logs "
                         "(admin_id, action, ip_hash, created_at) "
                         "VALUES (1, 'login', 'oldnet', ?)", (_ago(days=d),))
        conn.execute("INSERT INTO admin_session_logs "
                     "(admin_id, action, ip_hash, created_at) "
                     "VALUES (1, 'login', 'newnet', ?)", (_ago(minutes=30),))
        result = identity_detections.detect_admin_unseen_network(conn=conn)
        assert len(result["findings"]) == 1
        blob = json.dumps(incidents.get(result["findings"][0]["incident_key"], conn=conn))
        assert "never auto-lockout" in blob       # Stage 12

    def test_brand_new_admin_gets_no_accusation(self, conn):
        self._admin_logs(conn)
        conn.execute("INSERT INTO admin_session_logs "
                     "(admin_id, action, ip_hash, created_at) "
                     "VALUES (2, 'login', 'firstnet', ?)", (_ago(minutes=30),))
        result = identity_detections.detect_admin_unseen_network(conn=conn)
        assert result["findings"] == []           # no baseline, no accusation


class TestBaselines:
    def test_insufficient_history_yields_no_baseline(self, conn):
        now = _now()
        for d in (1, 2):
            _ingest(conn, "login_succeeded", subject="55",
                    occurred_at=_ago(days=d, now=now), dedupe=f"ub-{d}")
        out = identity_detections.user_login_baseline("55", conn=conn, now=now)
        assert out["baseline_available"] is False
        assert out["scope"] == "security-relevant signals only"   # Stage 14

    def test_normal_cadence_does_not_deviate(self, conn):
        now = _now()
        for d in range(1, 8):
            _ingest(conn, "login_succeeded", subject="56",
                    occurred_at=_ago(days=d, now=now), dedupe=f"un-{d}")
        _ingest(conn, "login_succeeded", subject="56",
                occurred_at=_ago(60, now=now), dedupe="un-today")
        out = identity_detections.user_login_baseline("56", conn=conn, now=now)
        assert out["baseline_available"] is True
        assert out["deviates"] is False
        assert "median" in out["method"]          # explainable, no ML

    def test_admin_spike_deviates_and_opens_incident(self, conn):
        now = _now()
        for d in range(1, 7):
            for j in range(2):
                _ingest(conn, "admin_action", category="ADMIN", subject="ops",
                        actor="admin:7", occurred_at=_ago(days=d, now=now),
                        dedupe=f"ab-{d}-{j}")
        for j in range(30):
            _ingest(conn, "admin_action", category="ADMIN", subject="ops",
                    actor="admin:7", occurred_at=_ago(minutes=30 + j, now=now),
                    dedupe=f"ab-today-{j}")
        base = identity_detections.admin_baseline("7", conn=conn, now=now)
        assert base["baseline_available"] and base["deviates"]
        result = identity_detections.detect_admin_baseline_deviation(conn=conn, now=now)
        assert len(result["findings"]) == 1
        assert _incident_count(conn, "ADMIN_IDENTITY_ANOMALY") == 1


# ---------------------------------------------------------------------------
# Stage 27 — exclusions: explicit, versioned, audited, time-bounded
# ---------------------------------------------------------------------------

class TestExclusions:
    def test_exclusion_requires_reason_author_and_bounded_ttl(self, conn):
        with pytest.raises(ValueError):
            identity_detections.add_exclusion("ID2_STUFFING_FANIN", "user:99",
                                              "", "owner", 60, conn=conn)
        with pytest.raises(ValueError):
            identity_detections.add_exclusion("ID2_STUFFING_FANIN", "user:99",
                                              "pen test", "", 60, conn=conn)
        with pytest.raises(ValueError):
            identity_detections.add_exclusion("ID2_STUFFING_FANIN", "user:99",
                                              "pen test", "owner",
                                              60 * 24 * 365, conn=conn)

    def test_exclusion_suppresses_rule_and_leaves_evidence(self, conn):
        now = _now()
        identity_detections.add_exclusion(
            "ID2_STUFFING_FANIN", "user:99",
            "authorized penetration test #442", "owner:roody", 120, conn=conn)
        for n in range(identity_detections.STUFFING_FANIN_MIN_NETWORKS):
            _ingest(conn, "login_failed", subject="99", network=f"network:pt{n}",
                    occurred_at=_ago(10, now=now), dedupe=f"pt-{n}")
        result = identity_detections.detect_credential_stuffing_fanin(conn=conn, now=now)
        assert result["findings"] == []
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_evidence "
                    "WHERE kind = 'detection_exclusion'")
        assert int(cur.fetchone()[0]) == 1        # audited, never silent


# ---------------------------------------------------------------------------
# Adversarial — forged metadata, garbage input, containment
# ---------------------------------------------------------------------------

class TestAdversarial:
    def test_forged_metadata_never_crashes_the_runner(self, conn):
        now = _now()
        weird = ("0 OR 1=1", "…" * 50, "user:user:user", "\x00null")
        for i, subj in enumerate(weird):
            _ingest(conn, "login_failed", subject=subj,
                    network="network:forged1", occurred_at=_ago(5, now=now),
                    dedupe=f"forge-{i}")
        results = identity_detections.run_identity_detections(conn=conn, now=now)
        assert len(results) == len(identity_detections.ALL_IDENTITY_RULES)
        for r in results:
            assert "findings" in r                # contained, structured

    def test_no_enforcement_verbs_in_identity_namespaces(self):
        for module in (identity_detections, identity_trust, sequences):
            names = " ".join(n.lower() for n in dir(module) if not n.startswith("_"))
            for verb in ("ban_", "block_", "lockout", "suspend", "seize",
                         "invalidate_session", "revoke_session"):
                assert verb not in names, f"{module.__name__} exposes {verb}"

    def test_recommendations_are_documentation_not_execution(self):
        for itype, recs in identity_detections.SAFE_RECOMMENDATIONS.items():
            joined = " ".join(recs).lower()
            assert "review" in joined or "human" in joined, itype


# ---------------------------------------------------------------------------
# Stages 23, 32–33 — invariants, owner summary, self-health
# ---------------------------------------------------------------------------

class TestIdentityInvariants:
    NEW = ("INV_INVALIDATED_SESSION_NOT_TRUSTED", "INV_HIGH_RISK_HAS_EVIDENCE",
           "INV_RISK_CONFIDENCE_CEILING", "INV_EXPIRED_RISK_INACTIVE",
           "INV_IDENTITY_EVIDENCE_PRESERVED")

    def test_registered_and_clean_on_empty_db(self, conn):
        by_id = {r.invariant_id: r for r in invariants.run_all(conn=conn)}
        for inv in self.NEW:
            assert inv in by_id
            assert by_id[inv].status in (invariants.STATUS_OK,
                                         invariants.STATUS_SKIPPED)

    def test_high_risk_without_evidence_is_caught_in_storage(self, conn):
        # The dataclass forbids this; simulate a corrupted/legacy row directly.
        conn.execute(
            "INSERT INTO sentinel_identity_risk (subject_ref, trust_state, "
            "risk_score, evidence_refs_json, source_trust, confidence, "
            "observed_at, expires_at, deployment_sha, policy_version) "
            "VALUES ('session:x', 'HIGH_RISK', 0.9, "
            "'[]', 'DERIVED', 0.8, ?, ?, 'test', 'v1')", (_ago(0), _ago(-240)))
        by_id = {r.invariant_id: r for r in invariants.run_all(conn=conn)}
        assert by_id["INV_HIGH_RISK_HAS_EVIDENCE"].status == invariants.STATUS_VIOLATED

    def test_confidence_ceiling_violation_is_caught(self, conn):
        conn.execute(
            "INSERT INTO sentinel_identity_risk (subject_ref, trust_state, "
            "risk_score, evidence_refs_json, source_trust, confidence, "
            "observed_at, expires_at, deployment_sha, policy_version) "
            "VALUES ('session:y', 'NORMAL', 0.0, "
            "'[]', 'DERIVED', 0.95, ?, ?, 'test', 'v1')", (_ago(0), _ago(-240)))
        by_id = {r.invariant_id: r for r in invariants.run_all(conn=conn)}
        assert by_id["INV_RISK_CONFIDENCE_CEILING"].status == invariants.STATUS_VIOLATED


class TestOwnerVisibility:
    def test_owner_summary_reports_honest_zeroes(self, conn):
        out = observability.owner_summary(conn=conn)
        for f in ("suspected_account_takeovers", "credential_stuffing_incidents",
                  "recovery_abuse_incidents", "high_risk_sessions",
                  "admin_identity_incidents"):
            assert out[f] == 0
        assert out["identity_risk_status"] == "quiet"

    def test_owner_summary_counts_real_incidents_only(self, conn):
        now = _now()
        _ato1_events(conn, now=now)
        identity_detections.detect_ato_chains(conn=conn, now=now)
        out = observability.owner_summary(conn=conn)
        assert out["suspected_account_takeovers"] == 1
        assert out["identity_risk_status"] == "attention"

    def test_self_health_exposes_identity_detection(self, conn):
        health = observability.self_health(conn=conn)
        ident = health["identity_detection"]
        assert "identity_detection_status" in ident
        assert "identity_incidents_open" in ident


# ---------------------------------------------------------------------------
# Stage 24 — UNDX identity analyst context (read-only, contradiction-aware)
# ---------------------------------------------------------------------------

class TestUndxIdentityContext:
    def test_requires_subject_fails_closed(self, conn):
        out = undx_interface.read("identity_context", conn=conn)
        assert out["ok"] is False

    def test_context_includes_contradicting_evidence_and_signal_quality(self, conn):
        now = _now()
        _ato1_events(conn, subject="77", now=now)
        t = identity_trust.evaluate_session({
            "session_ref": "user:77", "status": "active", "device_known": True,
            "recent_failed_logins": 0, "source_trust": "AUTHORITATIVE",
            "last_seen_at": _ago(5, now=now)})
        identity_trust.record(t, conn=conn)
        out = undx_interface.read("identity_context", subject="user:77", conn=conn)
        assert out["ok"] is True
        row = out["rows"][0]
        assert "contradicting_evidence" in row
        assert row["contradicting_evidence"]      # innocence is visible to UNDX
        assert "CLIENT_REPORTED" in row["signal_quality_note"]
        assert len(row["timeline"]) >= 3
        assert "ADVISORY" in out["authority_note"]

    def test_identity_context_is_a_known_surface(self):
        assert "identity_context" in undx_interface.READ_SURFACES
