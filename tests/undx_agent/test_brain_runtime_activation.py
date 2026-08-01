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

    def test_a_goal_reaches_the_response_layer_and_changes_the_mode(self):
        """The whole thread, end to end, through the real gateway.

        The unit tests in ``test_response_intelligence`` prove the response layer reacts
        to a shape it is handed. This proves the runtime actually hands it one — two
        different claims, and the gap between them is where the original defect lived
        for as long as it did.
        """
        # A second alert, so the retrieval genuinely returns a set. With one record a
        # ``show`` is documented to render as ``resource`` rather than ``list`` — a
        # list of one is a thing, not a list — and asserting ``list`` against a
        # single-record fixture would have been testing the fixture.
        self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=4000.0)

        listed = self.say("show me my alerts")
        plan = listed.receipt.evidence["response_plan"]
        self.assertEqual("show", plan["goal_shape"])
        self.assertEqual("list", plan["response_mode"])

        explained = self.say("explain my alerts")
        plan = explained.receipt.evidence["response_plan"]
        self.assertEqual("explain", plan["goal_shape"])
        self.assertEqual("explanation", plan["response_mode"])
        self.assertIn("perform_a_write", plan["must_not_do"])
        self.assertNotEqual(listed.reply, explained.reply)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_the_shape_is_absent_when_the_goal_layer_is_off(self):
        """The flag gates the Brain's contribution — and only the Brain's.

        With the goal layer off the shape is empty, which is the whole of what the flag
        controls. The mode is still ``explanation``, and that is not a leak: it comes
        from :func:`~services.undx_response_intelligence.select_response_type`, which
        read the word "explain" out of the question and predates the goal layer
        entirely. ``select_response_mode`` falls back to that classification precisely
        so a turn arriving without a shape is answered as well as it was before the
        Brain existed, rather than being demoted.

        The consequence is worth stating plainly because it is a real behavioural
        change: an explanation-phrased question now receives the account clauses even
        with ``UNDX_BRAIN_GOALS_ENABLED=0``. What the Brain adds is the ability to read
        *intent* from sentences whose wording does not announce it — which is the point
        — not the account itself.
        """
        self.fx.set_flags(UNDX_BRAIN_GOALS_ENABLED="0")
        response = self.say("explain my alerts")
        plan = response.receipt.evidence["response_plan"]
        self.assertEqual("", plan["goal_shape"])
        self.assertEqual("explanation", plan["response_mode"])

        # And a question carrying no such wording falls all the way back to the
        # evidence-shaped default, proving the fallback is reading the question rather
        # than defaulting every turn to an explanation.
        plain = self.say("my alerts")
        plain_plan = plain.receipt.evidence["response_plan"]
        self.assertEqual("", plain_plan["goal_shape"])
        self.assertIn(plain_plan["response_mode"], {"list", "resource"})

    def test_an_unsettled_goal_that_reaches_execution_still_carries_its_shape(self):
        """The reachability fact an earlier docstring got wrong.

        :func:`~services.undx_agent_runtime.goal_shape_for` used to refuse unsettled
        goals, justified by the claim that they "never reach here". :func:`handle`
        diverts an unsettled goal only when it *also* has somewhere to look, so one with
        nothing readable falls straight through to execution. Since REPAIR and MANAGE are
        never settled by construction, that made ``diagnosis`` unreachable from the
        runtime — a whole response mode that existed only in unit tests.

        Asserted against the translator directly rather than through a sentence, because
        which sentence produces an unsettled goal with no reads depends on which areas
        attention happens to activate, and a test resting on that would be testing
        attention rather than this.
        """
        from services.undx_brain.goals import Goal, Shape

        unsettled = Goal(shape=Shape.REPAIR, settled=False, single_step=True,
                         capability_id="", inspect_with=(), ok=True)
        self.assertEqual("repair",
                         self.runtime.goal_shape_for(unsettled, narrowed=False))

        scope = Goal(shape=Shape.MANAGE, settled=False, single_step=False,
                     capability_id="", inspect_with=(), ok=True)
        self.assertEqual("manage", self.runtime.goal_shape_for(scope, narrowed=False))

    def test_a_retrieval_splits_on_whether_the_request_narrowed(self):
        """``show`` and ``find`` are one Brain shape and two answers.

        The split is taken from argument resolution rather than from a second list of
        phrasings, so this is the test that the derivation — not a re-reading of the
        sentence — is what separates them.
        """
        from services.undx_brain.goals import Goal, Shape

        goal = Goal(shape=Shape.RETRIEVE, settled=True, single_step=True,
                    capability_id="crypto.alerts.list", ok=True)
        self.assertEqual("show", self.runtime.goal_shape_for(goal, narrowed=False))
        self.assertEqual("find", self.runtime.goal_shape_for(goal, narrowed=True))

    def test_a_goal_that_was_never_read_translates_to_nothing(self):
        from services.undx_brain.goals import Goal, Shape

        self.assertEqual("", self.runtime.goal_shape_for(None, narrowed=False))
        self.assertEqual("", self.runtime.goal_shape_for(
            Goal(shape=Shape.UNKNOWN, ok=True), narrowed=False))
        self.assertEqual("", self.runtime.goal_shape_for(
            Goal(shape=Shape.RETRIEVE, settled=True, ok=False), narrowed=False))

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
