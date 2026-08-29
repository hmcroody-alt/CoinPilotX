"""The status payload has to name the gate that is actually closed.

Four separate misconfigurations — the master flag off, the caller left out of the
cohort, the cohort populated under a retired variable name, the emergency switch
thrown — all produce one identical symptom: ``undx_agent_runtime.available`` returns
``False``, the turn falls through to ordinary conversation, and the person sees an
assistant that talks but never acts. ``scripts/undx_production_gate_probe.py``
demonstrates that collapse directly.

That is why this exists. ``/health/undx`` already publishes the deployment-wide flag
surface and is unauthenticated on purpose, so it can be read when sign-in is the
broken thing — but for the same reason it cannot answer *is the agent on for this
person*, and reports only ``qa_cohort_configured``, which is true of any non-empty
list regardless of who is in it. A deployment that omits exactly one tester reads as
perfectly healthy on every surface the server publishes.

These tests hold the code constant and vary only the environment, because that is the
one axis that differs between a green local run and a live container. They assert two
things and no more: that the answer is right, and that the *reason* is the gate that
really closed rather than merely one that happens to be shut.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID, OUTSIDER_ID  # noqa: E402


class AgentAvailabilityDiagnosis(unittest.TestCase):
    """``pulse_ai_service.status`` as an operator would read it."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        self.addCleanup(self.fx.stop)
        from services import pulse_ai_service

        self.service = pulse_ai_service

    def _agent(self, user_id: int | None = OWNER_ID) -> dict:
        return self.service.status(user_id)["agent"]

    # -- the healthy case -------------------------------------------------

    def test_a_caller_inside_the_cohort_is_told_the_agent_is_available(self):
        agent = self._agent()
        self.assertTrue(agent["available"])
        self.assertTrue(agent["writes_available"])
        # No reason, because there is nothing to explain. An availability probe that
        # always returns prose invites somebody to read the prose instead of the flag.
        self.assertEqual(agent["reason"], "")

    def test_the_unauthenticated_shape_still_answers(self):
        """Callers that pass no user id get a well-formed refusal, not an exception.

        The admin surface calls ``status()`` with no argument and must keep working;
        an availability block is not worth breaking a dashboard over.
        """
        agent = self._agent(None)
        self.assertFalse(agent["available"])
        self.assertIn("reason", agent)

    # -- the four indistinguishable failures ------------------------------

    def test_a_caller_outside_the_cohort_is_told_that_and_not_something_else(self):
        agent = self._agent(OUTSIDER_ID)
        self.assertFalse(agent["available"])
        self.assertEqual(agent["reason"], "this account is not in the agent cohort")

    def test_the_master_flag_being_off_outranks_cohort_membership(self):
        """Order is the whole point.

        With the master flag off, *every* account is outside the cohort as far as
        ``user_enabled`` is concerned, so a naive check would report "not in the
        cohort" and send an operator to edit a list that is already correct. The
        deployment-wide cause has to win over the per-account one.
        """
        self.fx.set_flags(UNDX_AGENT_ENABLED="")
        agent = self._agent()
        self.assertFalse(agent["available"])
        self.assertEqual(agent["reason"],
                         "the agent is switched off for this deployment")

    def test_the_emergency_switch_outranks_everything(self):
        self.fx.set_flags(UNDX_EMERGENCY_KILL_SWITCH="1")
        agent = self._agent()
        self.assertFalse(agent["available"])
        self.assertEqual(agent["reason"], "the emergency kill switch is set")

    def test_a_cohort_set_under_the_retired_name_reads_as_an_omission(self):
        """``UNDX_V5_QA_USER_IDS`` is consumed by nothing in the runtime.

        It survives only in ``scripts/undx_railway_variable_audit.py`` as a known
        equivalent of the live name. Setting it and nothing else is therefore exactly
        as effective as setting no cohort at all, and this asserts that the payload
        says so plainly rather than reporting a configured cohort that no code reads.
        """
        self.fx.set_flags(UNDX_AGENT_QA_USER_IDS="", UNDX_V5_QA_USER_IDS="7,8")
        self.addCleanup(self.fx.set_flags, UNDX_V5_QA_USER_IDS="")
        agent = self._agent()
        self.assertFalse(agent["available"])
        self.assertEqual(agent["reason"], "this account is not in the agent cohort")

    # -- available, but read-only -----------------------------------------

    def test_writes_being_off_does_not_read_as_the_agent_being_off(self):
        """A read-only rollout is a state somebody chose, not a defect.

        Collapsing it into ``available: false`` would send an operator hunting for a
        broken cohort when the actual answer is that writes are deliberately dark.
        """
        self.fx.set_flags(UNDX_AGENT_WRITES_ENABLED="")
        agent = self._agent()
        self.assertTrue(agent["available"])
        self.assertFalse(agent["writes_available"])
        self.assertEqual(agent["reason"], "")

    def test_the_payload_never_echoes_the_cohort(self):
        """A list of privileged account ids is not something an availability probe hands out."""
        self.fx.set_flags(UNDX_AGENT_QA_USER_IDS="7,8,4242")
        rendered = repr(self.service.status(OWNER_ID))
        self.assertNotIn("4242", rendered)


if __name__ == "__main__":
    unittest.main()
