"""Mission 5 — financial fraud + transaction integrity defense.

Covers: financial source trust (client claims can NEVER become canonical
authority), the financial event contract (idempotent, payload-safe), the 15
FIN invariants, the multidimensional risk model (RISK != GUILT: decay,
expiry, contradicting evidence, external cap, high-risk evidence floor),
FAT ATO chains, refund/payout/coordination detections with the full
false-positive suite (family device, office network, CGNAT, high-volume
seller, flash sale, legit refunds, seasonal payout spike, traveler, new
phone after recovery, QA account), webhook replay/idempotency, read-only
reconciliation, class-separated exposure, the financial mutation hard lock
(NO bypass, env flag changes NOTHING), the UNDX advisory surface, the admin
read API, and self-health/owner-summary honesty.

Adversarial posture throughout: try to frame innocent users, poison
baselines, replay/duplicate/stale/out-of-order events, override client
authority — and prove every attempt fails.
"""

import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from services.sentinel import (
    ad_wallet_integrity, classification, events, financial_context,
    financial_detections, financial_entities, financial_events,
    financial_exposure, financial_invariants, financial_mutation_lock,
    financial_reconciliation, financial_risk, financial_sequences,
    financial_sources, financial_webhooks, incidents, killswitches,
    observability, undx_interface,
)

_TS = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now(timezone.utc)


def _ago(minutes=0, hours=0, days=0, now=None):
    return ((now or _now()) - timedelta(minutes=minutes, hours=hours,
                                        days=days)).strftime(_TS)


def _ingest_identity(conn, event_type, *, subject="77", occurred_at=None,
                     dedupe=None):
    events.ingest(events.Event(
        category="AUTH", event_type=event_type, severity="low",
        actor_id="sentinel.test", source="test", subject_type="user",
        subject_id=subject, occurred_at=occurred_at or _ago(0),
        dedupe_key=dedupe or f"{event_type}-{subject}-{occurred_at}"),
        conn=conn)


def _incident_count(conn, incident_type):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sentinel_incidents WHERE incident_type = ?",
                (incident_type,))
    return int(cur.fetchone()[0])


def _enable_detection(monkeypatch, *extra):
    monkeypatch.setenv("SENTINEL_FINANCIAL_DETECTION_ENABLED", "1")
    for name in extra:
        monkeypatch.setenv(name, "1")


# ---------------------------------------------------------------------------
# Stage 2/5 — financial sources: trust classes and authority
# ---------------------------------------------------------------------------

class TestFinancialSources:
    def test_forty_sources_registered(self):
        assert len(financial_sources.SOURCES) == 40

    def test_only_authoritative_sources_grant_canonical_authority(self):
        for sid in financial_sources.source_ids():
            src = financial_sources.get(sid)
            assert src.canonical_authority_allowed == (
                src.source_class == "AUTHORITATIVE")

    def test_client_reported_sources_are_named_and_never_canonical(self):
        client = financial_sources.client_reported_sources()
        assert client, "client-reported sources must be inventoried"
        for sid in client:
            assert not financial_sources.is_canonical_authority(sid)

    def test_payment_verifications_is_client_reported(self):
        src = financial_sources.get("payment_verifications")
        assert src.source_class == "CLIENT_REPORTED"

    def test_seller_transactions_is_authoritative(self):
        assert financial_sources.get(
            "seller_transactions").source_class == "AUTHORITATIVE"

    def test_confidence_ceilings_strictly_ordered(self):
        c = financial_sources.CLASS_CONFIDENCE_CEILING
        assert (c["AUTHORITATIVE"] > c["PROVIDER_REPORTED"] > c["DERIVED"]
                > c["PROJECTION"] > c["CACHE"] > c["LEGACY"]
                > c["CLIENT_REPORTED"] > c["UNKNOWN"])

    def test_client_reported_trust_grade_is_unknown(self):
        assert financial_sources.CLASS_TRUST_GRADE["CLIENT_REPORTED"] == "UNKNOWN"

    def test_unknown_source_id_returns_none(self):
        assert financial_sources.get("no_such_source") is None


# ---------------------------------------------------------------------------
# Stage 2 — entity refs + payload safety
# ---------------------------------------------------------------------------

class TestFinancialEntities:
    def test_ref_roundtrip_all_types(self):
        for etype in financial_entities.FINANCIAL_ENTITY_TYPES:
            ref = financial_entities.make_ref(etype, "x1")
            assert financial_entities.parse_ref(ref) == (etype, "x1")

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError):
            financial_entities.make_ref("CREDIT_CARD", "1")

    def test_colon_in_identifier_rejected(self):
        with pytest.raises(ValueError):
            financial_entities.make_ref("ORDER", "a:b")

    def test_forbidden_field_at_top_level(self):
        assert not financial_entities.payload_is_safe({"card_number": "x"})

    def test_forbidden_field_nested_in_list_of_dicts(self):
        payload = {"items": [{"ok": 1}, {"bank_account": "y"}]}
        with pytest.raises(financial_entities.UnsafeFinancialPayload):
            financial_entities.assert_payload_safe(payload)

    def test_forbidden_substring_matches(self):
        for bad in ("stripe_secret_key", "user_ssn", "iban_code",
                    "seed_phrase_words"):
            assert not financial_entities.payload_is_safe({bad: 1})

    def test_safe_payload_passes(self):
        financial_entities.assert_payload_safe(
            {"amount_cents": 100, "refs": [{"order_ref": "ORDER:1"}]})


# ---------------------------------------------------------------------------
# Stage 3/38 — financial event adapters: authority + idempotency
# ---------------------------------------------------------------------------

class TestFinancialEvents:
    def test_authoritative_event_gets_full_confidence(self):
        evt = financial_events.build("ORDER_PAID", "ORDER:o1",
                                     "seller_transactions",
                                     source_event_id="txn_1")
        assert evt.confidence == 1.0
        assert evt.source_trust == "AUTHORITATIVE"

    def test_client_claim_confidence_is_floor(self):
        evt = financial_events.build("CLIENT_PAYMENT_CLAIM", "ORDER:o1",
                                     "payment_verifications",
                                     source_event_id="c1")
        # min(class ceiling 0.3, UNKNOWN trust ceiling 0.1) = 0.1
        assert evt.confidence == pytest.approx(0.1)

    def test_client_source_cannot_emit_order_paid(self):
        with pytest.raises(financial_events.FinancialEventRejected):
            financial_events.build("ORDER_PAID", "ORDER:o1",
                                   "payment_verifications")

    def test_client_claim_cannot_come_from_server_source(self):
        with pytest.raises(financial_events.FinancialEventRejected):
            financial_events.build("CLIENT_PAYMENT_CLAIM", "ORDER:o1",
                                   "seller_transactions")

    def test_unknown_event_type_rejected(self):
        with pytest.raises(financial_events.FinancialEventRejected):
            financial_events.build("MONEY_MOVED", "ORDER:o1",
                                   "seller_transactions")

    def test_malformed_subject_ref_rejected(self):
        with pytest.raises(ValueError):
            financial_events.build("ORDER_PAID", "not-a-ref",
                                   "seller_transactions")

    def test_unsafe_payload_rejected(self):
        with pytest.raises(financial_entities.UnsafeFinancialPayload):
            financial_events.build("ORDER_PAID", "ORDER:o1",
                                   "seller_transactions",
                                   {"card_number": "4111"})

    def test_unknown_source_gets_unknown_floor(self):
        evt = financial_events.build("ORDER_PAID", "ORDER:o1", "mystery_src")
        assert evt.confidence == pytest.approx(0.1)
        assert evt.source_trust == "UNKNOWN"

    def test_replay_is_idempotent(self, conn):
        kw = dict(source_event_id="txn_9", occurred_at=_ago(5))
        assert financial_events.observe("ORDER_PAID", "ORDER:o9",
                                        "seller_transactions", conn=conn, **kw)
        assert not financial_events.observe("ORDER_PAID", "ORDER:o9",
                                            "seller_transactions", conn=conn,
                                            **kw)
        rows = events.recent(category="PAYMENT", conn=conn)
        assert len([r for r in rows if r["event_type"] == "ORDER_PAID"]) == 1

    def test_emergency_kill_stops_ingest(self, conn, monkeypatch):
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert not financial_events.observe("ORDER_PAID", "ORDER:o2",
                                            "seller_transactions", conn=conn,
                                            source_event_id="t2")

    def test_correlation_keys_include_subject_ref(self):
        evt = financial_events.build("PAYOUT_REQUESTED", "PAYOUT:p1",
                                     "seller_payouts",
                                     correlation_keys=("SELLER:5",))
        assert "PAYOUT:p1" in evt.correlation_keys
        assert "SELLER:5" in evt.correlation_keys

    def test_all_17_event_types_have_valid_categories(self):
        for etype, (cat, sev) in financial_events.FINANCIAL_EVENT_TYPES.items():
            assert cat in ("PAYMENT", "LEDGER", "SETTLEMENT", "PAYOUT",
                           "ADVERTISING"), etype


# ---------------------------------------------------------------------------
# Stage 6 — invariants FIN-001…FIN-015
# ---------------------------------------------------------------------------

_VIOLATION_FACTS = {
    "FIN-001": {"captured_cents": 100, "refunded_total_cents": 150},
    "FIN-002": {"payout_state": "paid", "provider_payout_ref": ""},
    "FIN-003": {"provider_event_id": "e1", "economic_effect_count": 2},
    "FIN-004": {"refund_fee_bps": 500, "snapshot_fee_bps": 1000},
    "FIN-005": {"fee_rules": {"merchant": 500, "teacher": 1500}},
    "FIN-006": {"credit_source_class": "CLIENT_REPORTED"},
    "FIN-007": {"debit_has_billing_reference": False},
    "FIN-008": {"paid_state_source_class": "CLIENT_REPORTED"},
    "FIN-009": {"order_status": "paid", "payment_ref": "pi_1",
                "order_amount_cents": 100, "payment_amount_cents": 90},
    "FIN-010": {"refund_actor_type": "buyer"},
    "FIN-011": {"settlement_count_for_order": 2},
    "FIN-012": {"payout_from_state": "paid", "payout_to_state": "pending"},
    "FIN-013": {"balance_delta_cents": 100, "ledger_entries_sum_cents": 90},
    "FIN-014": {"duplicate_deliveries": 2, "processed_count": 2},
    "FIN-015": {"confirmed_cents": 100, "confirmed_basis_count": 0},
}

_HOLDS_FACTS = {
    "FIN-001": {"captured_cents": 100, "refunded_total_cents": 100},
    "FIN-002": {"payout_state": "paid", "provider_payout_ref": "po_x"},
    "FIN-003": {"provider_event_id": "e1", "economic_effect_count": 1},
    "FIN-004": {"refund_fee_bps": 1000, "snapshot_fee_bps": 1000},
    "FIN-005": {"fee_rules": {"merchant": 1000, "teacher": 1500},
                "proposed_standard_active": False},
    "FIN-006": {"credit_source_class": "AUTHORITATIVE"},
    "FIN-007": {"debit_has_billing_reference": True},
    "FIN-008": {"paid_state_source_class": "AUTHORITATIVE"},
    "FIN-009": {"order_status": "paid", "payment_ref": "pi_1",
                "order_amount_cents": 100, "payment_amount_cents": 100},
    "FIN-010": {"refund_actor_type": "admin"},
    "FIN-011": {"settlement_count_for_order": 1},
    "FIN-012": {"payout_from_state": "in_transit", "payout_to_state": "paid"},
    "FIN-013": {"balance_delta_cents": 100, "ledger_entries_sum_cents": 100},
    "FIN-014": {"duplicate_deliveries": 2, "processed_count": 1},
    "FIN-015": {"confirmed_cents": 100, "confirmed_basis_count": 1},
}


class TestFinancialInvariants:
    @pytest.mark.parametrize("iid", sorted(_VIOLATION_FACTS))
    def test_violation_detected(self, iid):
        assert financial_invariants.check(
            iid, _VIOLATION_FACTS[iid]).status == financial_invariants.VIOLATED

    @pytest.mark.parametrize("iid", sorted(_HOLDS_FACTS))
    def test_holds_on_good_facts(self, iid):
        assert financial_invariants.check(
            iid, _HOLDS_FACTS[iid]).status == financial_invariants.HOLDS

    @pytest.mark.parametrize("iid", sorted(_VIOLATION_FACTS))
    def test_missing_facts_are_not_evaluated_never_guessed(self, iid):
        assert financial_invariants.check(
            iid, {}).status == financial_invariants.NOT_EVALUATED

    def test_unknown_invariant_rejected(self):
        with pytest.raises(ValueError):
            financial_invariants.check("FIN-099", {})

    def test_escalate_opens_owner_action_incident(self, conn):
        res = financial_invariants.check("FIN-001", _VIOLATION_FACTS["FIN-001"])
        ref = financial_invariants.escalate(res, "ORDER:o1", conn=conn)
        assert ref is not None
        assert _incident_count(conn, "REFUND_ABUSE_SUSPECTED") == 1
        cur = conn.cursor()
        cur.execute("SELECT owner_action_required, detail_json FROM "
                    "sentinel_incidents WHERE incident_type = "
                    "'REFUND_ABUSE_SUSPECTED'")
        row = cur.fetchone()
        assert row[0]
        assert '"subject_ref": "ORDER:o1"' in row[1]

    def test_escalate_is_idempotent(self, conn):
        res = financial_invariants.check("FIN-013", _VIOLATION_FACTS["FIN-013"])
        financial_invariants.escalate(res, "FINANCIAL_LEDGER:main", conn=conn)
        financial_invariants.escalate(res, "FINANCIAL_LEDGER:main", conn=conn)
        assert _incident_count(conn, "FINANCIAL_LEDGER_MISMATCH") == 1

    def test_escalate_on_holds_is_noop(self, conn):
        res = financial_invariants.check("FIN-001", _HOLDS_FACTS["FIN-001"])
        assert financial_invariants.escalate(res, "ORDER:o1", conn=conn) is None

    def test_live_fee_policy_constants_unchanged(self):
        assert financial_invariants.LIVE_FEE_BPS == {"merchant": 1000,
                                                     "teacher": 1500}
        assert financial_invariants.PROPOSED_STANDARD_FEE_BPS == 500

    def test_fin005_flags_activated_proposed_standard(self):
        res = financial_invariants.check(
            "FIN-005", {"fee_rules": {"merchant": 1000, "teacher": 1500},
                        "proposed_standard_active": True})
        assert res.status == financial_invariants.VIOLATED


# ---------------------------------------------------------------------------
# Stages 7–9, 21–24 — risk model: RISK != GUILT
# ---------------------------------------------------------------------------

class TestFinancialRisk:
    def test_unknown_role_rejected(self):
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.assess("USER:1", "VICTIM", {}, [])

    def test_unknown_dimension_rejected(self):
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.assess("USER:1", "USER", {"vibes": 1.0}, ["r"])

    def test_weights_sum_to_one_per_role(self):
        for role, dims in financial_risk.DIMENSIONS.items():
            assert sum(dims.values()) == pytest.approx(1.0), role

    def test_high_risk_requires_reasons(self):
        a = financial_risk.assess(
            "USER:1", "USER",
            {d: 1.0 for d in financial_risk.DIMENSIONS["USER"]}, [])
        assert a.risk_score < financial_risk.HIGH_RISK_THRESHOLD
        assert "high_risk_evidence_floor" in a.capped_by

    def test_high_risk_with_evidence(self):
        a = financial_risk.assess(
            "USER:1", "USER",
            {d: 1.0 for d in financial_risk.DIMENSIONS["USER"]},
            ["burst of paid orders", "linked to elevated identity risk"])
        assert a.trust_state == "HIGH_RISK"

    def test_contradicting_evidence_reduces_score(self):
        dims = {"payment_velocity": 1.0, "identity_risk_link": 1.0,
                "refund_pattern": 1.0}
        base = financial_risk.assess("USER:1", "USER", dims, ["r1"])
        reduced = financial_risk.assess(
            "USER:1", "USER", dims, ["r1"],
            contradicting_evidence=["long-standing account",
                                    "verified travel"])
        assert reduced.risk_score < base.risk_score
        assert any("contradicting" in c for c in reduced.capped_by)

    def test_shared_infrastructure_damping(self):
        dims = {d: 1.0 for d in financial_risk.DIMENSIONS["USER"]}
        a = financial_risk.assess("USER:1", "USER", dims, ["r"],
                                  shared_infrastructure_factor=0.3)
        assert a.risk_score == pytest.approx(0.3)
        assert a.trust_state == "WATCH"

    def test_external_only_capped_at_0_6(self):
        dims = {d: 1.0 for d in financial_risk.DIMENSIONS["USER"]}
        a = financial_risk.assess("USER:1", "USER", dims, ["vendor verdict"],
                                  external_only=True)
        assert a.risk_score <= 0.6
        assert any("external_only_cap" in c for c in a.capped_by)

    def test_record_latest_roundtrip_with_decay(self, conn):
        a = financial_risk.assess(
            "SELLER:7", "SELLER",
            {d: 1.0 for d in financial_risk.DIMENSIONS["SELLER"]},
            ["payout anomaly", "settlement anomaly"])
        financial_risk.record(a, conn=conn)
        fresh = financial_risk.latest("SELLER:7", conn=conn)
        assert fresh["trust_state"] == "HIGH_RISK"
        late = financial_risk.latest(
            "SELLER:7", conn=conn, now=_now() + timedelta(hours=71))
        assert late["risk_score"] < 0.1  # linear decay nearly done

    def test_expired_risk_reads_as_none_no_permanent_label(self, conn):
        a = financial_risk.assess("SELLER:8", "SELLER",
                                  {"payout_pattern": 1.0}, ["r"])
        financial_risk.record(a, conn=conn)
        assert financial_risk.latest(
            "SELLER:8", conn=conn, now=_now() + timedelta(hours=73)) is None

    def test_every_risk_row_has_mandatory_expiry(self, conn):
        a = financial_risk.assess("BUYER:1", "BUYER",
                                  {"refund_pattern": 0.4}, ["r"])
        financial_risk.record(a, conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_financial_risk "
                    "WHERE expires_at IS NULL OR expires_at = ''")
        assert cur.fetchone()[0] == 0

    def test_ttl_must_be_bounded(self):
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.assess("USER:1", "USER", {}, [], ttl_hours=0)
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.assess("USER:1", "USER", {}, [], ttl_hours=1000)

    def test_velocity_window_bounded(self, conn):
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.payment_velocity("USER:1", conn=conn,
                                            window_minutes=0)
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.payment_velocity(
                "USER:1", conn=conn,
                window_minutes=financial_risk.MAX_VELOCITY_WINDOW_MINUTES + 1)

    def test_velocity_is_signal_not_verdict(self, conn):
        for i in range(25):
            financial_events.observe(
                "ORDER_PAID", "USER:velo", "seller_transactions", conn=conn,
                source_event_id=f"v{i}", occurred_at=_ago(minutes=30))
        out = financial_risk.payment_velocity("USER:velo", conn=conn,
                                              window_minutes=60)
        assert out["velocity_signal"] == 1.0
        assert "HIGH VOLUME != FRAUD" in out["note"]

    def test_fusion_external_cannot_exceed_cap_without_internal(self):
        out = financial_risk.fuse_with_external(0.5, 1.0,
                                                internal_evidence_count=0)
        assert out["fused_score"] == 0.6
        assert out["external_only_capped"]

    def test_fusion_with_internal_evidence_not_capped(self):
        out = financial_risk.fuse_with_external(0.5, 1.0,
                                                internal_evidence_count=3)
        assert out["fused_score"] == pytest.approx(0.8)
        assert not out["external_only_capped"]


# ---------------------------------------------------------------------------
# Stage 10/27 — FAT ATO chains
# ---------------------------------------------------------------------------

class TestFATSequences:
    def test_kill_switch_off_means_no_evaluation(self, conn):
        _ingest_identity(conn, "password_reset_requested", subject="900")
        assert financial_sequences.evaluate_all(conn=conn) == []

    def test_fat1_reset_login_payout_fires(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        _ingest_identity(conn, "password_reset_requested", subject="901",
                         occurred_at=_ago(minutes=40))
        _ingest_identity(conn, "login_succeeded", subject="901",
                         occurred_at=_ago(minutes=30))
        financial_events.observe(
            "PAYOUT_REQUESTED", "SELLER:901", "seller_payouts", conn=conn,
            source_event_id="po1", occurred_at=_ago(minutes=10))
        firings = financial_sequences.evaluate_all(conn=conn)
        fat1 = [f for f in firings
                if f["sequence_id"] == "FAT1_RESET_LOGIN_PAYOUT"
                and f["subject_ref"] == "user:901"]
        assert fat1
        refs = financial_sequences.open_incidents_for(fat1, conn=conn)
        assert refs
        assert _incident_count(
            conn, "FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED") == 1
        # idempotent: re-opening collapses into the same incident
        financial_sequences.open_incidents_for(fat1, conn=conn)
        assert _incident_count(
            conn, "FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED") == 1

    def test_fat2_partial_without_payout_is_honest(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        _ingest_identity(conn, "unusual_device", subject="902",
                         occurred_at=_ago(minutes=50))
        financial_events.observe(
            "PAYOUT_DESTINATION_CHANGED", "SELLER:902", "seller_payouts",
            conn=conn, source_event_id="d1", occurred_at=_ago(minutes=20))
        firings = [f for f in financial_sequences.evaluate_all(conn=conn)
                   if f["sequence_id"] == "FAT2_NEWDEVICE_DEST_PAYOUT"]
        assert firings and firings[0]["completeness"] == "PARTIAL"
        financial_sequences.open_incidents_for(firings, conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT title FROM sentinel_incidents WHERE "
                    "incident_type = 'FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED'")
        assert "PARTIAL" in cur.fetchone()[0]

    def test_all_fat_chains_open_suspicion_never_verdict(self):
        for seq in financial_sequences.FAT_SEQUENCES:
            assert seq.incident_type == "FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED"
            assert "SUSPECTED" in seq.incident_type


# ---------------------------------------------------------------------------
# Stages 11–15, 37 — detections + the false-positive suite
# ---------------------------------------------------------------------------

class TestRefundPatternFalsePositives:
    def test_fp_cs_approved_refunds_never_count(self):
        refunds = [{"reason": "cs_approved", "amount_cents": 100,
                    "order_amount_cents": 100}] * 8
        out = financial_detections.refund_pattern(refunds)
        assert out["signal"] == 0.0
        assert out["exempt_legitimate"] == 8

    def test_fp_partial_refunds_are_legitimate(self):
        refunds = [{"reason": "partial_refund", "amount_cents": 50,
                    "order_amount_cents": 100}] * 6
        assert financial_detections.refund_pattern(refunds)["signal"] == 0.0

    def test_fp_item_not_received_and_platform_error_exempt(self):
        refunds = [{"reason": "item_not_received"},
                   {"reason": "platform_error"},
                   {"reason": "seller_fault"}, {"reason": "goodwill"}]
        out = financial_detections.refund_pattern(refunds)
        assert out["counted"] == 0

    def test_abusive_full_refund_cycling_scores(self):
        refunds = [{"reason": "", "amount_cents": 100,
                    "order_amount_cents": 100, "same_seller": True}] * 6
        out = financial_detections.refund_pattern(refunds)
        assert out["signal"] > 0.5
        assert out["reasons"]

    def test_refund_window_bounded(self):
        with pytest.raises(ValueError):
            financial_detections.refund_pattern([], window_days=0)
        with pytest.raises(ValueError):
            financial_detections.refund_pattern([], window_days=365)


class TestPayoutThreatFalsePositives:
    def test_single_signal_never_flags(self):
        out = financial_detections.payout_threat({"velocity_spike": True})
        assert not out["flagged"]
        assert out["signal"] <= 0.4

    def test_fp_seasonal_payout_spike_damped(self):
        out = financial_detections.payout_threat(
            {"velocity_spike": True, "amount_outlier": True},
            seasonal_context=True)
        assert not out["flagged"]
        assert out["seasonal_damping_applied"]

    def test_two_concurrent_signals_flag(self):
        out = financial_detections.payout_threat(
            {"destination_changed_recently": True,
             "identity_risk_active": True})
        assert out["flagged"]
        assert "cannot execute" in out["note"]

    def test_unknown_signal_rejected(self):
        with pytest.raises(ValueError):
            financial_detections.payout_threat({"looks_shady": True})


class TestSharedInfrastructureFalsePositives:
    @pytest.mark.parametrize("ctx,factor", [
        ("family_household", 0.3), ("office_network", 0.4),
        ("cgnat_carrier", 0.5), ("qa_test_account", 0.2),
        ("shared_public_device", 0.4), ("none_known", 1.0)])
    def test_context_factors(self, ctx, factor):
        assert financial_detections.shared_infrastructure_factor(
            [ctx]) == factor

    def test_most_forgiving_context_wins(self):
        assert financial_detections.shared_infrastructure_factor(
            ["none_known", "qa_test_account"]) == 0.2

    def test_fp_family_device_is_not_a_fraud_ring(self):
        out = financial_detections.coordination_candidate(
            ["u1", "u2", "u3"],
            {"shared_device": ["u1", "u2", "u3"],
             "shared_network": ["u1", "u2", "u3"]},
            "refund cycling", shared_contexts=["family_household"])
        assert out["verdict"] == "NOT_ESTABLISHED"

    def test_fp_office_network_is_not_collusion(self):
        out = financial_detections.coordination_candidate(
            ["a", "b", "c", "d"],
            {"shared_network": ["a", "b", "c", "d"],
             "temporal_lockstep": ["a", "b"]},
            "synchronized orders", shared_contexts=["office_network"])
        assert out["verdict"] == "NOT_ESTABLISHED"

    def test_fp_cgnat_carrier_ip_sharing(self):
        out = financial_detections.coordination_candidate(
            ["a", "b", "c"],
            {"shared_network": ["a", "b", "c"],
             "shared_device": ["a", "b"]},
            "spend anomaly", shared_contexts=["cgnat_carrier"])
        assert out["verdict"] == "NOT_ESTABLISHED"

    def test_fp_qa_test_accounts(self):
        out = financial_detections.coordination_candidate(
            ["qa1", "qa2", "qa3"],
            {"shared_device": ["qa1", "qa2", "qa3"],
             "temporal_lockstep": ["qa1", "qa2", "qa3"]},
            "test purchases", shared_contexts=["qa_test_account"])
        assert out["verdict"] == "NOT_ESTABLISHED"


class TestCoordinationRequirements:
    def test_two_accounts_never_a_ring(self):
        out = financial_detections.coordination_candidate(
            ["u1", "u2"], {"shared_device": ["u1", "u2"],
                           "shared_payment_method_ref": ["u1", "u2"],
                           "shared_payout_destination_ref": ["u1", "u2"]},
            "anomaly")
        assert out["verdict"] == "NOT_ESTABLISHED"

    def test_sharing_alone_without_economic_anomaly_fails(self):
        out = financial_detections.coordination_candidate(
            ["u1", "u2", "u3"],
            {"shared_device": ["u1", "u2", "u3"],
             "shared_network": ["u1", "u2", "u3"]}, "")
        assert out["verdict"] == "NOT_ESTABLISHED"
        assert any("sharing alone is not fraud" in f
                   for f in out["failed_requirements"])

    def test_one_linking_dimension_is_relationship_not_collusion(self):
        out = financial_detections.coordination_candidate(
            ["u1", "u2", "u3"], {"shared_network": ["u1", "u2", "u3"]},
            "refund cycling")
        assert out["verdict"] == "NOT_ESTABLISHED"
        assert any("RELATIONSHIP != COLLUSION" in f
                   for f in out["failed_requirements"])

    def test_established_candidate_is_possible_never_confirmed(self):
        out = financial_detections.coordination_candidate(
            ["u1", "u2", "u3"],
            {"shared_payment_method_ref": ["u1", "u2", "u3"],
             "shared_payout_destination_ref": ["u1", "u2"],
             "temporal_lockstep": ["u1", "u2", "u3"]},
            "circular refund flow totaling 40000c")
        assert out["verdict"] == "POSSIBLE_COORDINATED_FINANCIAL_ABUSE"
        assert "CONFIRMED" not in out["verdict"]

    def test_incident_gated_by_marketplace_kill_switch(self, conn, monkeypatch):
        cand = financial_detections.coordination_candidate(
            ["u1", "u2", "u3"],
            {"shared_payment_method_ref": ["u1", "u2", "u3"],
             "shared_payout_destination_ref": ["u1", "u2"]},
            "circular refunds")
        assert financial_detections.open_coordination_incident(
            cand, conn=conn) is None  # switches OFF by default
        _enable_detection(monkeypatch, "SENTINEL_MARKETPLACE_RISK_ENABLED")
        assert financial_detections.open_coordination_incident(
            cand, conn=conn) is not None
        assert _incident_count(conn, "COORDINATED_FINANCIAL_ABUSE") == 1

    def test_unknown_linking_dimension_rejected(self):
        with pytest.raises(ValueError):
            financial_detections.coordination_candidate(
                ["a", "b", "c"], {"same_astrology_sign": ["a", "b"]}, "x")


class TestMoreFalsePositives:
    def test_fp_flash_sale_high_volume_alone_is_not_high_risk(self):
        a = financial_risk.assess("USER:shop", "USER",
                                  {"payment_velocity": 1.0},
                                  ["flash sale burst"])
        assert a.trust_state in ("NORMAL", "WATCH")
        assert a.risk_score <= 0.25

    def test_fp_high_volume_seller_single_dimension(self):
        a = financial_risk.assess("SELLER:big", "SELLER",
                                  {"payout_pattern": 1.0}, ["many payouts"])
        assert a.trust_state != "HIGH_RISK"

    def test_fp_traveler_new_device_alone_no_fat_firing(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        _ingest_identity(conn, "unusual_device", subject="tr1",
                         occurred_at=_ago(minutes=30))
        firings = financial_sequences.evaluate_all(conn=conn)
        assert not [f for f in firings if f.get("subject_ref") == "user:tr1"]

    def test_fp_new_phone_after_recovery_without_payout(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        _ingest_identity(conn, "password_reset_requested", subject="np1",
                         occurred_at=_ago(minutes=40))
        _ingest_identity(conn, "login_succeeded", subject="np1",
                         occurred_at=_ago(minutes=35))
        firings = financial_sequences.evaluate_all(conn=conn)
        assert not [f for f in firings if f.get("subject_ref") == "user:np1"]
        assert _incident_count(
            conn, "FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED") == 0


# ---------------------------------------------------------------------------
# Stage 18/38 — webhook security: replay, signature, ordering, idempotency
# ---------------------------------------------------------------------------

class TestFinancialWebhooks:
    def test_first_delivery_stores_without_detections(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        out = financial_webhooks.observe("stripe", "evt_1",
                                         event_kind="payment_intent.succeeded",
                                         signature_valid=True, conn=conn)
        assert out["stored"] and out["delivery_count"] == 1
        assert out["detections"] == []

    def test_redelivery_detected_as_replay(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        financial_webhooks.observe("stripe", "evt_r", conn=conn)
        out = financial_webhooks.observe("stripe", "evt_r", conn=conn)
        assert "REPLAY" in out["detections"]
        assert "DUPLICATE_ECONOMIC_EFFECT_RISK_CANDIDATE" in out["detections"]
        assert _incident_count(conn, "FINANCIAL_WEBHOOK_REPLAY") == 1
        # third delivery: still one incident (idempotent key)
        financial_webhooks.observe("stripe", "evt_r", conn=conn)
        assert _incident_count(conn, "FINANCIAL_WEBHOOK_REPLAY") == 1

    def test_signature_failure_opens_provider_inconsistency(self, conn,
                                                            monkeypatch):
        _enable_detection(monkeypatch)
        out = financial_webhooks.observe("stripe", "evt_sig",
                                         signature_valid=False, conn=conn)
        assert "SIGNATURE_FAILURE" in out["detections"]
        assert _incident_count(conn, "FINANCIAL_PROVIDER_INCONSISTENCY") == 1

    def test_out_of_order_is_context_not_incident(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        financial_webhooks.observe("stripe", "evt_new",
                                   provider_created_at="2026-08-13 10:00:00",
                                   conn=conn)
        out = financial_webhooks.observe("stripe", "evt_old",
                                         provider_created_at="2026-08-13 09:00:00",
                                         conn=conn)
        assert "OUT_OF_ORDER" in out["detections"]
        assert _incident_count(conn, "FINANCIAL_WEBHOOK_REPLAY") == 0
        assert _incident_count(conn, "FINANCIAL_PROVIDER_INCONSISTENCY") == 0

    def test_detections_skipped_when_switch_off(self, conn):
        financial_webhooks.observe("stripe", "evt_off", conn=conn)
        out = financial_webhooks.observe("stripe", "evt_off",
                                         signature_valid=False, conn=conn)
        assert out["detections"] == []
        assert "skipped" in out["note"]
        assert _incident_count(conn, "FINANCIAL_WEBHOOK_REPLAY") == 0

    def test_missing_identifiers_rejected(self, conn):
        with pytest.raises(ValueError):
            financial_webhooks.observe("", "evt", conn=conn)
        with pytest.raises(ValueError):
            financial_webhooks.observe("stripe", "", conn=conn)


# ---------------------------------------------------------------------------
# Stage 19 — reconciliation: read-only, honest statuses
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_match(self):
        r = financial_reconciliation.reconcile("order_payment", "ORDER:1",
                                               100, 100)
        assert r["status"] == "MATCH"

    def test_mismatch_carries_delta(self):
        r = financial_reconciliation.reconcile("order_payment", "ORDER:1",
                                               100, 90)
        assert r["status"] == "MISMATCH" and "-10c" in r["detail"]

    def test_unknown_side_is_never_assumed_equal(self):
        assert financial_reconciliation.reconcile(
            "s", "ORDER:1", None, 100)["status"] == "UNKNOWN"

    def test_stale_inputs_never_treated_as_current(self):
        assert financial_reconciliation.reconcile(
            "s", "ORDER:1", 100, 100, stale=True)["status"] == "STALE"

    def test_partial_coverage_reported(self):
        assert financial_reconciliation.reconcile(
            "s", "ORDER:1", 100, 100, partial=True)["status"] == "PARTIAL"

    def test_mismatch_escalates_and_preserves_both_sides(self, conn):
        r = financial_reconciliation.reconcile("escrow", "SELLER:5", 500, 400)
        financial_reconciliation.record(r, conn=conn)
        assert _incident_count(conn, "FINANCIAL_LEDGER_MISMATCH") == 1
        rows = financial_reconciliation.recent(scope="escrow", conn=conn)
        assert rows[0]["expected_cents"] == 500
        assert rows[0]["observed_cents"] == 400  # recorded, never repaired

    def test_unknown_status_cannot_be_recorded(self, conn):
        with pytest.raises(ValueError):
            financial_reconciliation.record(
                {"status": "FIXED", "scope": "s", "subject_ref": "ORDER:1"},
                conn=conn)

    def test_status_counts(self, conn):
        financial_reconciliation.record(
            financial_reconciliation.reconcile("a", "ORDER:1", 1, 1), conn=conn)
        counts = financial_reconciliation.status_counts(conn=conn)
        assert counts["MATCH"] == 1


# ---------------------------------------------------------------------------
# Stage 20 — exposure: classes structurally apart
# ---------------------------------------------------------------------------

class TestFinancialExposure:
    def test_confirmed_requires_concrete_basis_ref(self):
        with pytest.raises(financial_exposure.ExposureError):
            financial_exposure.estimate(
                [{"exposure_class": "CONFIRMED", "amount_cents": 100,
                  "ref": ""}])

    def test_classes_never_summed_together(self):
        est = financial_exposure.estimate([
            {"exposure_class": "CONFIRMED", "amount_cents": 100,
             "ref": "REFUND:r1"},
            {"exposure_class": "POTENTIAL", "amount_cents": 900, "ref": ""},
            {"exposure_class": "DISPUTED", "amount_cents": 50, "ref": ""},
        ])
        assert est["confirmed_cents"] == 100
        assert est["potential_cents"] == 900
        assert est["disputed_cents"] == 50

    def test_unknown_is_counted_not_priced(self):
        est = financial_exposure.estimate(
            [{"exposure_class": "UNKNOWN", "ref": "", "note": "no data"}])
        assert est["unknown_items"] == 1
        assert est["confirmed_cents"] == est["potential_cents"] == 0

    def test_unknown_class_rejected(self):
        with pytest.raises(financial_exposure.ExposureError):
            financial_exposure.estimate([{"exposure_class": "GUESSED",
                                          "amount_cents": 1}])

    def test_negative_amount_rejected(self):
        with pytest.raises(financial_exposure.ExposureError):
            financial_exposure.estimate([{"exposure_class": "POTENTIAL",
                                          "amount_cents": -5}])

    def test_totals_use_latest_estimate_per_incident(self, conn):
        e1 = financial_exposure.estimate(
            [{"exposure_class": "POTENTIAL", "amount_cents": 1000, "ref": ""}])
        e2 = financial_exposure.estimate(
            [{"exposure_class": "POTENTIAL", "amount_cents": 200, "ref": ""}])
        financial_exposure.record("inc_a", e1, conn=conn)
        financial_exposure.record("inc_a", e2, conn=conn)  # supersedes
        totals = financial_exposure.totals(conn=conn)
        assert totals["potential_cents"] == 200  # never 1200
        assert totals["incidents_with_estimates"] == 1


# ---------------------------------------------------------------------------
# Stages 16–17 — ad wallet integrity
# ---------------------------------------------------------------------------

class TestAdWalletIntegrity:
    def _figs(self, b, a, e):
        return {"billing_events_sum_cents": b, "accumulator_cents": a,
                "escrow_delta_cents": e}

    def test_three_source_agreement(self, conn):
        out = ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c1", self._figs(1000, 1000, 1000), conn=conn)
        assert out["status"] == "MATCH"

    def test_sub_cent_accrual_tolerance(self, conn):
        out = ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c1", self._figs(1000, 999, 1000), conn=conn)
        assert out["status"] == "MATCH"

    def test_disagreement_is_mismatch_and_never_repaired(self, conn,
                                                         monkeypatch):
        _enable_detection(monkeypatch, "SENTINEL_AD_WALLET_RISK_ENABLED")
        out = ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c2", self._figs(1000, 900, 1000), conn=conn)
        assert out["status"] == "MISMATCH"
        assert "no balance is ever mutated" in out["note"]
        assert _incident_count(conn, "AD_WALLET_INTEGRITY_ANOMALY") == 1

    def test_mismatch_without_switch_records_but_no_incident(self, conn):
        ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c3", self._figs(1000, 800, 1000), conn=conn)
        assert _incident_count(conn, "AD_WALLET_INTEGRITY_ANOMALY") == 0

    def test_missing_source_degrades_to_partial(self, conn):
        out = ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c1", {"billing_events_sum_cents": 1000,
                            "accumulator_cents": None,
                            "escrow_delta_cents": 1000}, conn=conn)
        assert out["status"] == "PARTIAL"

    def test_no_sources_is_unknown_never_pass(self, conn):
        out = ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c1", {}, conn=conn)
        assert out["status"] == "UNKNOWN"

    def test_stale_figures_reported_stale(self, conn):
        out = ad_wallet_integrity.spend_agreement(
            "CAMPAIGN:c1", self._figs(1, 1, 1), stale=True, conn=conn)
        assert out["status"] == "STALE"

    def test_unknown_spend_source_rejected(self, conn):
        with pytest.raises(ValueError):
            ad_wallet_integrity.spend_agreement(
                "CAMPAIGN:c1", {"vibes_cents": 1}, conn=conn)

    def test_advertiser_assessment_is_advisory(self):
        out = ad_wallet_integrity.assess_advertiser(
            "ADVERTISER:a1", {"funding_ops_7d": 10, "funding_failures_7d": 8,
                              "spend_vs_budget_ratio": 1.5,
                              "campaign_creates_7d": 6,
                              "campaign_cancels_7d": 5})
        assert out["dimensions"]["wallet_funding_pattern"] > 0.5
        assert "advisory only" in out["note"]


# ---------------------------------------------------------------------------
# Stage 48 — the financial mutation hard lock
# ---------------------------------------------------------------------------

class TestFinancialMutationLock:
    @pytest.mark.parametrize(
        "capability", financial_mutation_lock.FORBIDDEN_CAPABILITIES)
    def test_every_forbidden_capability_refuses(self, conn, capability):
        with pytest.raises(
                financial_mutation_lock.FinancialMutationForbidden):
            financial_mutation_lock.attempt(capability, "SELLER:1",
                                            "test", conn=conn)

    def test_refusal_is_recorded_as_evidence(self, conn):
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_evidence")
        before = int(cur.fetchone()[0])
        with pytest.raises(
                financial_mutation_lock.FinancialMutationForbidden):
            financial_mutation_lock.attempt("issue_refund", "ORDER:1",
                                            "attacker", conn=conn)
        cur.execute("SELECT COUNT(*) FROM sentinel_evidence")
        assert int(cur.fetchone()[0]) > before

    def test_attempt_has_no_bypass_parameter(self):
        params = set(inspect.signature(
            financial_mutation_lock.attempt).parameters)
        assert params == {"capability", "subject_ref", "requested_by", "conn"}
        for banned in ("force", "override", "admin", "bypass", "confirm"):
            assert banned not in params

    def test_module_surface_scan_is_clean(self):
        out = financial_mutation_lock.verify_module_surface()
        assert out["clean"], out["violations"]
        assert out["scanned_modules"] > 30

    def test_surface_scan_catches_a_planted_capability(self, monkeypatch):
        monkeypatch.setattr(financial_context, "issue_refund",
                            lambda *a, **k: None, raising=False)
        out = financial_mutation_lock.verify_module_surface()
        assert "services.sentinel.financial_context.issue_refund" in \
            out["violations"]

    def test_twenty_capabilities_are_named(self):
        assert len(financial_mutation_lock.FORBIDDEN_CAPABILITIES) == 20
        for cap in ("move_funds", "freeze_wallet", "issue_refund",
                    "issue_payout", "suspend_seller", "ban_buyer",
                    "modify_balance", "change_fee", "alter_payment_routing",
                    "charge_payment_method"):
            assert cap in financial_mutation_lock.FORBIDDEN_CAPABILITIES


# ---------------------------------------------------------------------------
# Stage 49 — kill switches
# ---------------------------------------------------------------------------

class TestFinancialKillSwitches:
    def test_detection_default_off(self):
        assert not killswitches.financial_detection_enabled()

    def test_subdomains_require_detection_chain(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MARKETPLACE_RISK_ENABLED", "1")
        assert not killswitches.marketplace_risk_enabled()  # parent OFF
        monkeypatch.setenv("SENTINEL_FINANCIAL_DETECTION_ENABLED", "1")
        assert killswitches.marketplace_risk_enabled()

    def test_emergency_kills_financial_detection(self, monkeypatch):
        _enable_detection(monkeypatch)
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert not killswitches.financial_detection_enabled()

    def test_adversarial_automation_env_flag_changes_nothing(self, monkeypatch):
        assert killswitches.financial_automation_enabled() is False
        monkeypatch.setenv("SENTINEL_FINANCIAL_AUTOMATION_ENABLED", "1")
        assert killswitches.financial_automation_enabled() is False
        monkeypatch.setenv("SENTINEL_AUTOMATION_ENABLED", "1")
        monkeypatch.setenv("SENTINEL_FINANCIAL_DETECTION_ENABLED", "1")
        assert killswitches.financial_automation_enabled() is False

    def test_switch_state_reports_hard_false(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_FINANCIAL_AUTOMATION_ENABLED", "1")
        state = killswitches.switch_state()
        assert state["financial_automation_enabled"] is False
        assert "no money-movement capability" in \
            state["financial_automation_note"]


# ---------------------------------------------------------------------------
# Stage 32 — UNDX financial context: advisory, redacted, zero authority
# ---------------------------------------------------------------------------

class TestUndxFinancialContext:
    def test_surface_registered_and_dispatchable(self, conn):
        assert "financial_threat_context" in undx_interface.READ_SURFACES
        out = undx_interface.read("financial_threat_context",
                                  subject="SELLER:42", conn=conn)
        assert out["ok"]

    def test_invalid_subject_fails_closed(self, conn):
        out = undx_interface.financial_threat_context("droptable", conn=conn)
        assert not out["ok"]

    def test_zero_money_authority_is_explicit(self, conn):
        out = undx_interface.financial_threat_context("ORDER:o1", conn=conn)
        row = out["rows"][0]
        for op in ("move_funds", "issue_refunds", "freeze_wallets",
                   "confirm_fraud", "assign_guilt"):
            assert op in row["may_not"]
        assert "ZERO money authority" in out["authority_note"]

    def test_signal_quality_note_survives_redaction(self, conn):
        out = undx_interface.financial_threat_context("BUYER:9", conn=conn)
        note = out["rows"][0]["signal_quality_note"]
        assert "ANOMALY != FRAUD" in note and "RISK != GUILT" in note

    def test_context_carries_contradicting_evidence(self, conn):
        a = financial_risk.assess(
            "SELLER:ctx", "SELLER", {"payout_pattern": 0.6, "refund_inflow": 0.6},
            ["payout anomaly"], contradicting_evidence=["8-year account"])
        financial_risk.record(a, conn=conn)
        out = undx_interface.financial_threat_context("SELLER:ctx", conn=conn)
        assert out["rows"][0]["contradicting_evidence"] == ["8-year account"]

    def test_no_money_moving_functions_on_interface(self):
        public = [n for n in dir(undx_interface) if not n.startswith("_")]
        for banned in ("refund", "payout", "freeze", "transfer", "suspend",
                       "ban_"):
            assert not any(banned in n.lower() for n in public), banned


# ---------------------------------------------------------------------------
# Stage 42 — admin read API
# ---------------------------------------------------------------------------

class TestFinancialAdminApi:
    def _client(self):
        flask = pytest.importorskip("flask")
        from services.sentinel.api import sentinel_bp
        app = flask.Flask(__name__)
        app.secret_key = "test-only"
        app.register_blueprint(sentinel_bp)
        return app, app.test_client()

    def test_all_financial_routes_require_admin(self):
        _, client = self._client()
        for path in ("financial/summary", "financial/incidents",
                     "financial/transactions/ORDER:1",
                     "financial/reconciliation", "financial/risk/SELLER:1"):
            assert client.get(
                f"/api/admin/sentinel/{path}").status_code == 403

    def test_financial_routes_are_get_only(self):
        app, _ = self._client()
        fin_rules = [r for r in app.url_map.iter_rules()
                     if "/financial/" in str(r)]
        assert len(fin_rules) == 5
        for rule in fin_rules:
            assert rule.methods - {"HEAD", "OPTIONS"} == {"GET"}

    def test_invalid_entity_ref_is_400(self):
        _, client = self._client()
        with client.session_transaction() as s:
            s["is_admin"] = True
        assert client.get(
            "/api/admin/sentinel/financial/risk/nonsense").status_code == 400
        assert client.get(
            "/api/admin/sentinel/financial/transactions/nonsense"
        ).status_code == 400

    def test_unknown_incident_type_filter_is_400(self):
        _, client = self._client()
        with client.session_transaction() as s:
            s["is_admin"] = True
        resp = client.get(
            "/api/admin/sentinel/financial/incidents?type=WITCHCRAFT")
        assert resp.status_code == 400
        assert "known_types" in resp.get_json()

    def test_api_source_has_no_mutation_verbs(self):
        import pathlib
        source = (pathlib.Path(financial_context.__file__).parent
                  / "api.py").read_text()
        for verb in (".post(", ".put(", ".delete(", ".patch("):
            assert verb not in source


# ---------------------------------------------------------------------------
# Stages 41/43 — owner summary + self-health honesty
# ---------------------------------------------------------------------------

class TestFinancialObservability:
    def test_self_health_reports_disabled_switch_honestly(self, conn):
        fd = observability.self_health(conn=conn)["financial_defense"]
        assert fd["financial_detection_status"] == "disabled_by_kill_switch"
        assert fd["mutation_lock_clean"] is True
        assert "structurally absent" in fd["money_movement_capability"]

    def test_self_health_enabled_no_signal(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        fd = observability.self_health(conn=conn)["financial_defense"]
        assert fd["financial_detection_status"] == "enabled_no_signal"

    def test_owner_summary_honest_zero_defaults(self, conn):
        s = observability.owner_summary(conn=conn)
        assert s["financial_risk_status"] == "quiet"
        assert s["financial_incidents_open"] == 0
        assert s["suspected_financial_ato"] == 0
        assert s["owner_financial_review_required"] == 0
        exp = s["estimated_potential_exposure"]
        assert exp["potential_cents"] == 0
        assert "never" in exp["note"]

    def test_owner_summary_counts_are_real(self, conn):
        res = financial_invariants.check("FIN-001", _VIOLATION_FACTS["FIN-001"])
        financial_invariants.escalate(res, "ORDER:oc1", conn=conn)
        financial_exposure.record("inc_x", financial_exposure.estimate(
            [{"exposure_class": "POTENTIAL", "amount_cents": 750,
              "ref": ""}]), conn=conn)
        s = observability.owner_summary(conn=conn)
        assert s["refund_abuse_suspected"] == 1
        assert s["financial_incidents_open"] == 1
        assert s["owner_financial_review_required"] == 1
        assert s["financial_risk_status"] == "watch"
        assert s["estimated_potential_exposure"]["potential_cents"] == 750
        assert s["estimated_potential_exposure"]["confirmed_cents"] == 0

    def test_ato_or_ledger_findings_escalate_status(self, conn):
        r = financial_reconciliation.reconcile("escrow", "SELLER:9", 100, 50)
        financial_reconciliation.record(r, conn=conn)
        s = observability.owner_summary(conn=conn)
        assert s["ledger_mismatch_findings"] == 1
        assert s["financial_risk_status"] == "attention"


# ---------------------------------------------------------------------------
# Stage 36/50 — named adversarial suite
# ---------------------------------------------------------------------------

class TestAdversarial:
    def test_adversarial_frame_innocent_user_single_signal(self):
        """One planted anomaly must never condemn anyone."""
        for role in financial_risk.ENTITY_ROLES:
            dim = next(iter(financial_risk.DIMENSIONS[role]))
            a = financial_risk.assess(f"{role}:victim", role, {dim: 1.0},
                                      ["planted"])
            assert a.trust_state != "HIGH_RISK"

    def test_adversarial_frame_without_reasons_capped(self):
        a = financial_risk.assess(
            "USER:v2", "USER",
            {d: 1.0 for d in financial_risk.DIMENSIONS["USER"]}, [])
        assert a.risk_score < 0.70

    def test_adversarial_baseline_poisoning_window_bounded(self, conn):
        """Attacker cannot widen the velocity window into forever-profiling."""
        with pytest.raises(financial_risk.FinancialRiskError):
            financial_risk.payment_velocity("USER:1", conn=conn,
                                            window_minutes=60 * 24 * 365)

    def test_adversarial_client_authority_override_rejected(self):
        """A client claiming 'payment succeeded' can never become ORDER_PAID."""
        with pytest.raises(financial_events.FinancialEventRejected):
            financial_events.build("ORDER_PAID", "ORDER:o1",
                                   "client_payment_claim")
        res = financial_invariants.check(
            "FIN-008", {"paid_state_source_class": "CLIENT_REPORTED"})
        assert res.status == financial_invariants.VIOLATED

    def test_adversarial_client_claim_stays_low_confidence(self, conn):
        financial_events.observe("CLIENT_PAYMENT_CLAIM", "ORDER:cc1",
                                 "payment_verifications", conn=conn,
                                 source_event_id="cc1")
        rows = [r for r in events.recent(category="PAYMENT", conn=conn)
                if r["event_type"] == "CLIENT_PAYMENT_CLAIM"]
        assert rows[0]["confidence"] <= 0.1
        assert rows[0]["source_trust"] == "UNKNOWN"

    def test_adversarial_stale_events_never_match(self):
        assert financial_reconciliation.reconcile(
            "x", "ORDER:1", 100, 100, stale=True)["status"] == "STALE"

    def test_adversarial_duplicate_events_are_idempotent(self, conn):
        for _ in range(5):
            financial_events.observe("ORDER_PAID", "ORDER:dup",
                                     "seller_transactions", conn=conn,
                                     source_event_id="same-id",
                                     occurred_at=_ago(minutes=1))
        rows = [r for r in events.recent(category="PAYMENT", conn=conn)
                if r["event_type"] == "ORDER_PAID"]
        assert len(rows) == 1

    def test_adversarial_out_of_order_does_not_corrupt(self, conn, monkeypatch):
        _enable_detection(monkeypatch)
        financial_webhooks.observe("stripe", "b",
                                   provider_created_at="2026-08-13 10:00:00",
                                   conn=conn)
        out = financial_webhooks.observe(
            "stripe", "a", provider_created_at="2026-08-13 08:00:00",
            conn=conn)
        assert "OUT_OF_ORDER" in out["detections"]
        assert _incident_count(conn, "FINANCIAL_PROVIDER_INCONSISTENCY") == 0

    def test_adversarial_provider_verdict_cannot_hold_funds(self, conn):
        """External MALICIOUS verdict: capped at 0.6, and there is no hold
        capability anywhere to trigger."""
        out = financial_risk.fuse_with_external(0.0, 1.0,
                                                internal_evidence_count=0)
        assert out["fused_score"] <= 0.6
        with pytest.raises(
                financial_mutation_lock.FinancialMutationForbidden):
            financial_mutation_lock.attempt("hold_funds", "SELLER:1",
                                            "external_provider", conn=conn)

    def test_adversarial_confirmed_loss_without_evidence_forbidden(self):
        with pytest.raises(financial_exposure.ExposureError):
            financial_exposure.estimate(
                [{"exposure_class": "CONFIRMED", "amount_cents": 99999,
                  "ref": " "}])
        assert financial_invariants.check(
            "FIN-015", {"confirmed_cents": 99999,
                        "confirmed_basis_count": 0}
        ).status == financial_invariants.VIOLATED

    def test_adversarial_coordination_from_sharing_alone_fails(self):
        out = financial_detections.coordination_candidate(
            ["a", "b", "c", "d", "e"],
            {"shared_device": ["a", "b", "c", "d", "e"],
             "shared_network": ["a", "b", "c", "d", "e"]},
            "", shared_contexts=["family_household"])
        assert out["verdict"] == "NOT_ESTABLISHED"

    def test_adversarial_no_permanent_fraud_label_exists(self, conn):
        a = financial_risk.assess("BUYER:perm", "BUYER",
                                  {"refund_pattern": 0.9,
                                   "dispute_history": 0.9},
                                  ["pattern", "disputes"])
        financial_risk.record(a, conn=conn)
        assert financial_risk.latest(
            "BUYER:perm", conn=conn,
            now=_now() + timedelta(days=31)) is None

    def test_adversarial_forbidden_payload_cannot_enter_any_path(self, conn):
        bad = {"routing_number": "12345"}
        with pytest.raises(financial_entities.UnsafeFinancialPayload):
            financial_events.observe("ORDER_PAID", "ORDER:x",
                                     "seller_transactions", bad, conn=conn)

    def test_adversarial_payment_instrument_fields_redacted(self):
        red = classification.redact({"card_number": "4111", "cvv": "000",
                                     "amount_cents": 100},
                                    classification.Level.INTERNAL)
        assert red["card_number"] == classification.REDACTED
        assert red["cvv"] == classification.REDACTED
        assert red["amount_cents"] == 100

    def test_adversarial_incident_types_are_suspicions_not_verdicts(self):
        for t in ("FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED",
                  "PAYMENT_ABUSE_SUSPECTED", "REFUND_ABUSE_SUSPECTED",
                  "PAYOUT_ABUSE_SUSPECTED", "MARKETPLACE_ABUSE_SUSPECTED"):
            assert t in incidents.INCIDENT_TYPES
            assert "SUSPECTED" in t
        for absent in ("FRAUD_CONFIRMED", "GUILTY", "FRAUDSTER"):
            assert not any(absent in t for t in incidents.INCIDENT_TYPES)


# ---------------------------------------------------------------------------
# Transaction context (Stage 4)
# ---------------------------------------------------------------------------

class TestFinancialContext:
    def test_context_is_references_only(self, conn):
        financial_events.observe("ORDER_PAID", "ORDER:ctx1",
                                 "seller_transactions", conn=conn,
                                 source_event_id="e1")
        ctx = financial_context.build_context("ORDER:ctx1", conn=conn)
        assert ctx["entity_type"] == "ORDER"
        assert len(ctx["events"]) == 1
        assert "references only" in ctx["note"]

    def test_expired_risk_reads_unknown_in_context(self, conn):
        ctx = financial_context.build_context("SELLER:none", conn=conn)
        assert ctx["risk"] is None or ctx["risk"].get("trust_state") in (
            "UNKNOWN", None)

    def test_invalid_ref_rejected(self, conn):
        with pytest.raises(ValueError):
            financial_context.build_context("bogus", conn=conn)
