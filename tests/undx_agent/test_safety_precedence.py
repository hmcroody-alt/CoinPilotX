from __future__ import annotations

import itertools
import os
import sqlite3
import unittest
from unittest.mock import patch

from services import undx_agent_policy
from services import undx_architecture
from services import undx_mission_runtime


BASE_RUNTIME_ENV = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_PLANNER_ENABLED": "1",
    "UNDX_TASK_GRAPH_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_WORKER_FAIL_CLOSED": "1",
    "UNDX_RECONCILIATION_ENABLED": "1",
    "UNDX_WORKER_RECONCILIATION_ENABLED": "1",
    "UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED": "0",
    "UNDX_AGENT_WRITES_ENABLED": "1",
}


class SafetyPrecedenceTruthTable(unittest.TestCase):
    def test_every_higher_level_stop_overrides_write_enable(self):
        stops = (
            "UNDX_EMERGENCY_KILL_SWITCH",
            "UNDX_WRITE_KILL_SWITCH",
            "UNDX_AGENT_DISABLE_WRITES",
            "UNDX_V4_DISABLE_WRITES",
        )
        for values in itertools.product(("0", "1"), repeat=len(stops)):
            env = {"UNDX_AGENT_WRITES_ENABLED": "1", **dict(zip(stops, values))}
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertEqual(undx_agent_policy.writes_available(), "1" not in values)

    def test_each_required_guard_fails_closed_when_explicitly_disabled(self):
        for guard in undx_agent_policy.REQUIRED_WRITE_GUARDS:
            env = {"UNDX_AGENT_WRITES_ENABLED": "1", guard: "0"}
            with self.subTest(guard=guard), patch.dict(os.environ, env, clear=True):
                self.assertFalse(undx_agent_policy.writes_available())

    def test_executor_only_success_is_never_a_write_enable(self):
        with patch.dict(os.environ, {
            "UNDX_AGENT_WRITES_ENABLED": "1",
            "UNDX_COMPLETION_ALLOW_EXECUTOR_ONLY_SUCCESS": "1",
        }, clear=True):
            self.assertFalse(undx_agent_policy.writes_available())

    def test_read_stop_does_not_depend_on_write_state(self):
        for emergency, read_stop, expected in (
            ("0", "0", True), ("0", "1", False), ("1", "0", False), ("1", "1", False),
        ):
            with self.subTest(emergency=emergency, read_stop=read_stop), patch.dict(os.environ, {
                "UNDX_AGENT_READS_ENABLED": "1",
                "UNDX_AGENT_WRITES_ENABLED": "0",
                "UNDX_EMERGENCY_KILL_SWITCH": emergency,
                "UNDX_READ_KILL_SWITCH": read_stop,
            }, clear=True):
                self.assertEqual(undx_agent_policy.reads_available(), expected)


class DurableMissionLeaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        undx_architecture.ensure_schema(self.cur)

    def tearDown(self):
        self.conn.close()

    def _persist(self, mission_id: str = "mission-1") -> None:
        plan = {
            "mission_id": mission_id,
            "objective": "Prepare a bounded read-only report.",
            "risk_level": "low",
            "status": "ready",
            "skills": ["cognitive.summarize"],
            "authorized_tools": [],
            "nodes": [
                {"level": "mission", "node_type": "understand", "objective": "Understand",
                 "status": "ready", "success_condition": "bounded"},
                {"level": "strategy", "node_type": "retrieve", "objective": "Retrieve",
                 "status": "pending", "success_condition": "evidence"},
                {"level": "verification", "node_type": "verify", "objective": "Verify",
                 "status": "pending", "success_condition": "verified"},
            ],
            "retrieval_proof": True,
            "verification_ready": True,
            "client_request_id": "client-1",
        }
        undx_architecture.persist_plan(self.cur, 7, 11, plan, "test")
        self.conn.commit()

    def test_worker_claims_leases_and_advances_to_verified_completion(self):
        with patch.dict(os.environ, BASE_RUNTIME_ENV, clear=True):
            self._persist()
            undx_mission_runtime.ensure_schema(self.cur)
            events = []
            for _ in range(3):
                claim = undx_mission_runtime.claim_next(self.cur, "worker:test")
                self.assertIsNotNone(claim)
                self.assertEqual(claim["lease_owner"], "worker:test")
                events.append(undx_mission_runtime.advance_claimed(self.cur, claim, "worker:test"))
                self.conn.commit()
            self.assertTrue(all(event["advanced"] for event in events))
            self.cur.execute("SELECT status FROM pulse_ai_missions WHERE mission_id='mission-1'")
            self.assertEqual(self.cur.fetchone()[0], "succeeded")

    def test_pause_resume_and_cancel_release_the_lease(self):
        with patch.dict(os.environ, BASE_RUNTIME_ENV, clear=True):
            self._persist("mission-controls")
            undx_mission_runtime.ensure_schema(self.cur)
            self.assertTrue(undx_mission_runtime.request_pause(self.cur, 7, "mission-controls"))
            self.assertTrue(undx_mission_runtime.resume(self.cur, 7, "mission-controls"))
            self.assertTrue(undx_mission_runtime.cancel(self.cur, 7, "mission-controls"))
            self.cur.execute("SELECT status, lease_owner FROM pulse_ai_missions WHERE mission_id='mission-controls'")
            row = self.cur.fetchone()
            self.assertEqual(row[0], "cancelled")
            self.assertFalse(row[1])

    def test_dynamic_limit_escalation_disables_claiming(self):
        env = {**BASE_RUNTIME_ENV, "UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED": "1"}
        with patch.dict(os.environ, env, clear=True):
            self._persist("mission-unsafe")
            self.assertFalse(undx_mission_runtime.surface().enabled)
            self.assertIsNone(undx_mission_runtime.claim_next(self.cur, "worker:test"))


if __name__ == "__main__":
    unittest.main()

