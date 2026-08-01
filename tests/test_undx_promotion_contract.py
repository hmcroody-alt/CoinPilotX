"""Release invariants for the UNDX production promotion path."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services import undx_agent_policy
from services.undx_brain.config import MINIMUM_PRODUCTION_CONTRACT


ROOT = Path(__file__).resolve().parents[1]


class ProductionVariableContract(unittest.TestCase):
    def test_minimum_contract_is_exact_and_non_secret(self):
        expected = {
            "UNDX_AGENT_ENABLED", "UNDX_AGENT_READS_ENABLED",
            "UNDX_AGENT_WRITES_ENABLED", "UNDX_AGENT_DISABLE_WRITES",
            "UNDX_AGENT_REQUIRE_VERIFICATION", "UNDX_AGENT_REQUIRE_AUDIT",
            "UNDX_AGENT_FAIL_CLOSED", "UNDX_MEMORY_FAIL_CLOSED",
            "UNDX_BRAIN_ENABLED", "UNDX_BRAIN_QA_ONLY",
            "UNDX_AGENT_QA_USER_IDS",
        }
        rows = {row["name"]: row for row in MINIMUM_PRODUCTION_CONTRACT}
        self.assertEqual(set(rows), expected)
        self.assertTrue(all(not row["secret"] for row in rows.values()))
        self.assertTrue(all(row["consumer"] and row["health_field"] for row in rows.values()))

    def test_write_disable_always_overrides_write_enable(self):
        cases = (
            ({}, False),
            ({"UNDX_AGENT_WRITES_ENABLED": "true"}, True),
            ({"UNDX_AGENT_WRITES_ENABLED": "false"}, False),
            ({"UNDX_AGENT_WRITES_ENABLED": "true", "UNDX_AGENT_DISABLE_WRITES": "true"}, False),
            ({"UNDX_AGENT_WRITES_ENABLED": "false", "UNDX_AGENT_DISABLE_WRITES": "false"}, False),
        )
        for values, expected in cases:
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                self.assertEqual(undx_agent_policy.writes_available(), expected)


class WorkerAndHealthContract(unittest.TestCase):
    def test_undx_worker_has_authoritative_marker_and_shutdown(self):
        source = (ROOT / "undx_worker.py").read_text(encoding="utf-8")
        self.assertIn("UNDX_WORKER_START", source)
        self.assertIn("SIGTERM", source)
        self.assertIn("STOP_EVENT.wait", source)

    def test_service_entrypoints_are_distinct(self):
        expected = {
            "undx_worker.py": "UNDX_WORKER_START",
            "pulse_worker.py": "PULSE_WORKER",
            "media_worker.py": "MEDIA",
            "alert_worker.py": "ALERT",
            "telegram_worker.py": "TELEGRAM",
        }
        for filename, marker in expected.items():
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8").upper()
                self.assertIn(marker, source)

    def test_health_endpoint_exposes_safe_release_state(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        route = source[source.index('@webhook_app.route("/health/undx"'):]
        route = route[:route.index('@webhook_app.route("/health/database"')]
        for field in (
            '"sha"', '"agent"', '"brain"', '"verification"', '"audit"',
            '"parity"', '"corpus"', '"worker"', '"coordination"', '"degraded"',
        ):
            self.assertIn(field, route)
        for forbidden in ("OPENAI_API_KEY", "DATABASE_URL", "private_message"):
            self.assertNotIn(forbidden, route)


if __name__ == "__main__":
    unittest.main()
