"""Stages 2 + 17: event envelope contract, idempotent ingest, evidence chain."""

import json

import pytest

from services.sentinel import evidence, events
from services.sentinel.events import Event, EventRejected


def _event(**overrides):
    base = dict(category="AUTH", event_type="login_failed", severity="low",
                actor_id="sentinel.ingest", source="test",
                subject_type="user", subject_id="42")
    base.update(overrides)
    return Event(**base)


class TestEventEnvelope:
    def test_unknown_category_rejected(self):
        with pytest.raises(EventRejected):
            _event(category="NOT_A_CATEGORY")

    def test_unknown_severity_rejected(self):
        with pytest.raises(EventRejected):
            _event(severity="apocalyptic")

    def test_actor_required(self):
        with pytest.raises(EventRejected):
            _event(actor_id="")

    def test_ingest_is_idempotent_by_dedupe_key(self, conn):
        ev = _event(dedupe_key="fixed-key")
        assert events.ingest(ev, conn=conn) is True
        assert events.ingest(_event(dedupe_key="fixed-key"), conn=conn) is False
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_events")
        assert cur.fetchone()[0] == 1

    def test_secrets_redacted_before_persistence(self, conn):
        ev = _event(payload={"password": "hunter2", "note": "fine"})
        events.ingest(ev, conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT payload_json FROM sentinel_events")
        stored = json.loads(cur.fetchone()[0])
        assert "hunter2" not in json.dumps(stored)
        assert stored["note"] == "fine"

    def test_ingest_respects_emergency_kill(self, conn, monkeypatch):
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert events.ingest(_event(), conn=conn) is False


class TestEvidenceChain:
    def test_chain_appends_and_verifies(self, conn):
        for i in range(5):
            evidence.append("test_record", "sentinel.ingest", {"i": i}, conn=conn)
        result = evidence.verify_chain(conn=conn)
        assert result == {"ok": True, "records": 5, "broken_at": None}

    def test_tampering_breaks_the_chain(self, conn):
        for i in range(3):
            evidence.append("test_record", "sentinel.ingest", {"i": i}, conn=conn)
        conn.execute("UPDATE sentinel_evidence SET body_json = '{\"i\":999}' WHERE seq = 2")
        result = evidence.verify_chain(conn=conn)
        assert result["ok"] is False
        assert result["broken_at"] == 2

    def test_deletion_breaks_the_chain(self, conn):
        for i in range(3):
            evidence.append("test_record", "sentinel.ingest", {"i": i}, conn=conn)
        conn.execute("DELETE FROM sentinel_evidence WHERE seq = 2")
        assert evidence.verify_chain(conn=conn)["ok"] is False

    def test_evidence_body_is_redacted(self, conn):
        evidence.append("test_record", "sentinel.ingest",
                        {"api_key": "sk-live-123", "kind": "x"}, conn=conn)
        cur = conn.cursor()
        cur.execute("SELECT body_json FROM sentinel_evidence")
        assert "sk-live-123" not in cur.fetchone()[0]

    def test_no_update_or_delete_functions_exist(self):
        assert not hasattr(evidence, "update")
        assert not hasattr(evidence, "delete")
