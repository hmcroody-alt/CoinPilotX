"""Production-shaped proof that Brain decisions reach the canonical agent runtime."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


BRAIN = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_ATTENTION_ENABLED": "1",
    "UNDX_BRAIN_GOALS_ENABLED": "1",
    "UNDX_BRAIN_SELECTION_ENABLED": "1",
    "UNDX_BRAIN_PREDICTION_ENABLED": "1",
}


class BrainRuntimeActivation(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture(**BRAIN).start()
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")
        from services import undx_agent_runtime
        self.runtime = undx_agent_runtime

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, **kwargs):
        result = self.runtime.handle(
            self.fx.cur,
            user_id=OWNER_ID,
            text=text,
            correlation_id="brain-runtime-test",
            **kwargs,
        )
        self.fx.commit()
        return result

    def test_unsettled_repair_goal_clarifies_instead_of_guessing_a_write(self):
        response = self.say("Fix my alerts.")
        self.assertTrue(response.handled)
        self.assertEqual(response.card["status"], "clarification_required")
        self.assertFalse(response.card["may_claim_done"])
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")
        self.assertTrue(response.card["active_domains"])

    def test_selection_output_changes_the_live_capability(self):
        response = self.say("Should I pause my Bitcoin alert?")
        self.assertTrue(response.handled)
        self.assertFalse(response.receipt.risk_level != "read_only")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_prediction_is_checked_after_the_real_gateway_call(self):
        self.fx.set_flags(UNDX_BRAIN_EXECUTOR_ENABLED="1")
        response = self.say(
            "Pause my Bitcoin alert.",
            capability_id="crypto.alerts.pause",
        )
        self.assertEqual(response.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")
        summary = response.card["brain_prediction"]
        self.assertEqual(summary["verifier"], "canonical_read_back")
        self.assertIn(summary["fidelity"], {"confirmed", "nothing_predicted", "unobserved"})
        self.assertTrue(response.card["may_claim_done"])
        self.assertTrue(response.card["brain_execution"]["active"])
        self.assertEqual(
            response.card["brain_execution"]["completed_steps"],
            ["execute:crypto.alerts.pause"],
        )

    def test_flags_off_preserve_the_legacy_matcher(self):
        self.fx.set_flags(
            UNDX_BRAIN_ENABLED="0",
            UNDX_BRAIN_ATTENTION_ENABLED="0",
            UNDX_BRAIN_GOALS_ENABLED="0",
            UNDX_BRAIN_SELECTION_ENABLED="0",
            UNDX_BRAIN_PREDICTION_ENABLED="0",
        )
        response = self.say("show me my alerts")
        self.assertEqual(response.status, "verified_success")
        self.assertNotIn("brain_prediction", response.card)


if __name__ == "__main__":
    unittest.main()
