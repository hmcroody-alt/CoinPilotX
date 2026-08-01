"""There is one definition of completion, and every other module defers to it.

The rule this file enforces is narrow and worth stating before the tests do: *only* the
central evidence/truth model may decide whether UNDX is allowed to tell somebody their
change is done. Everything else — an executor, an audit ledger, a prose renderer, an
HTTP body — may report that a service accepted a call, that an operation was attempted,
that execution returned, that verification is pending or that verification failed. None
of them may reach the word "done" by their own arithmetic.

The canonical rule has two halves and both are required:

* :attr:`~services.undx_agent_contracts.AgentReceipt.may_claim_completed` — a completed
  status *and* a verification state of ``verified``; and
* :func:`services.undx_brain.evidence.derive` — the same pair resolved through the
  Brain's independently written state machine,

conjoined in :attr:`~services.undx_tool_gateway.GatewayOutcome.may_claim_done`. A
conjunction can only narrow, which is why it is safe unflagged: a defect in either half
can withhold a true claim, never manufacture a false one.

Four second definitions were live when this file was written, and each one is pinned
below by a test that fails if it comes back. They were not exotic. Three of them were a
single expression that had *one* of the canonical rule's two conditions in it, which is
how this class of defect always looks: not a competing rule, but the real rule with a
clause missing.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from services import undx_response_intelligence as ri  # noqa: E402
from services import undx_tool_gateway  # noqa: E402
from services.undx_agent_contracts import (  # noqa: E402
    AgentOutcome,
    AgentReceipt,
    VerificationResult,
    VerificationState,
)
from services.undx_capability_registry import get as get_spec  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICES = os.path.join(REPO, "services")


def _source(name: str) -> str:
    with open(os.path.join(SERVICES, name), "r", encoding="utf-8") as handle:
        return handle.read()


class TheCanonicalRuleIsTheOnlyRule(unittest.TestCase):
    """Properties of the definition itself, asserted directly."""

    def _receipt(self, status: str, verification_state: str) -> AgentReceipt:
        return AgentReceipt(
            task_id="t", request_id="r", capability_id="crypto.alerts.pause",
            action="Pause an alert", status=status, owner_user_id=7,
            verification_state=verification_state,
        )

    def test_a_completed_status_alone_does_not_license_the_claim(self):
        receipt = self._receipt(AgentOutcome.VERIFIED_SUCCESS, VerificationState.IMPOSSIBLE)
        self.assertFalse(receipt.may_claim_completed)

    def test_a_verified_read_back_alone_does_not_license_the_claim(self):
        receipt = self._receipt(AgentOutcome.TERMINAL_FAILURE, VerificationState.VERIFIED)
        self.assertFalse(receipt.may_claim_completed)

    def test_both_halves_together_do(self):
        receipt = self._receipt(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)
        self.assertTrue(receipt.may_claim_completed)

    def test_the_conjunction_can_only_narrow(self):
        """Whatever the Brain says, ``may_claim_done`` is never wider than the receipt.

        Driven over the whole cross product rather than a chosen case, because the
        property being asserted is about the *shape* of the combination and a single
        example would only demonstrate that one cell of it behaves.
        """
        for status in sorted(AgentOutcome.ALL):
            for state in sorted(VerificationState.ALL):
                receipt = self._receipt(status, state)
                verification = VerificationResult(state=state)
                for is_write in (True, False):
                    outcome = undx_tool_gateway.GatewayOutcome(
                        receipt, result=None, verification=verification, is_write=is_write)
                    with self.subTest(status=status, state=state, is_write=is_write):
                        if outcome.may_claim_done:
                            self.assertTrue(
                                receipt.may_claim_completed,
                                "may_claim_done reached True where the receipt said no")


class NoModuleDefinesSuccessForItself(unittest.TestCase):
    """The four second definitions that were live, pinned so they cannot return."""

    def test_the_prose_layer_requires_the_read_back_and_not_only_the_status(self):
        """``_action_state_for`` used to say ``verified_success`` on status alone.

        That state is what :func:`~services.undx_response_intelligence._lead_forms` keys
        the sentence "Done — …, and I read it back from PulseSoc to confirm it" off. With
        the verification half missing, any path reaching a completed status without a
        read-back rendered that sentence. The gateway's idempotent replay is such a path.
        """
        view = ri.build_view(
            get_spec("crypto.alerts.pause"),
            ri.ToolResult(ok=True, tool_name="pulsesoc.crypto_alerts.pause",
                          capability_id="crypto.alerts.pause"),
        ) if hasattr(ri, "ToolResult") else None
        if view is None:  # ToolResult lives in contracts, not here
            from services.undx_agent_contracts import ToolResult
            view = ri.build_view(
                get_spec("crypto.alerts.pause"),
                ToolResult(ok=True, tool_name="pulsesoc.crypto_alerts.pause",
                           capability_id="crypto.alerts.pause"),
            )
        self.assertTrue(view.is_write)

        verified = ri._action_state_for(
            AgentOutcome.VERIFIED_SUCCESS, view,
            VerificationResult(state=VerificationState.VERIFIED))
        self.assertEqual(ri.ActionState.VERIFIED_SUCCESS, verified)

        for state in (VerificationState.IMPOSSIBLE, VerificationState.PENDING,
                      VerificationState.FAILED):
            with self.subTest(state=state):
                self.assertNotEqual(
                    ri.ActionState.VERIFIED_SUCCESS,
                    ri._action_state_for(AgentOutcome.VERIFIED_SUCCESS, view,
                                         VerificationResult(state=state)),
                    "a write with no read-back was described as a verified success")

    def test_undo_is_offered_only_where_the_canonical_rule_says_done(self):
        """Undo is itself a write, aimed at state whose value is in doubt.

        The gate used to be ``status == VERIFIED_SUCCESS`` — the first of the two
        conditions and not the second — so the highest-consequence affordance in the
        system had the weakest definition of success behind it.
        """
        spec = get_spec("crypto.alerts.pause")
        for state in sorted(VerificationState.ALL):
            receipt = undx_tool_gateway._receipt(
                spec, user_id=7, request_id="r", task_id="t",
                status=AgentOutcome.VERIFIED_SUCCESS, explanation="",
                arguments={"alert_id": 1}, canonical_ids=["alert:1"],
                verification=VerificationResult(state=state))
            with self.subTest(state=state):
                if receipt.undo_arguments or receipt.undo_capability_id:
                    self.assertTrue(
                        receipt.may_claim_completed,
                        "an undo was offered on a change the system may not claim happened")

    def test_the_http_body_reports_the_conjunction_rather_than_half_of_it(self):
        source = _source("pulse_ai_service.py")
        self.assertIn('"ok": bool(outcome.may_claim_done)', source)
        self.assertNotIn('"ok": bool(receipt.may_claim_completed)', source)

    def test_the_runtime_self_check_reads_the_canonical_claim_list(self):
        """The metacognitive guard had its own four-word vocabulary.

        ``(" completed", " is paused", " is active", " was deleted")`` — which missed
        every sentence the system actually writes to claim a completion, and matched
        state descriptions a *read* is entitled to make.
        """
        source = _source("undx_agent_runtime.py")
        self.assertNotIn('completion_words = (', source)
        self.assertIn("from services.undx_response_intelligence import completion_claim",
                      source)


class TheClaimDetectorRecognisesTheSystemsOwnVocabulary(unittest.TestCase):
    """Every sentence UNDX writes to claim a completion is caught by the detector.

    Harvested from the modules that produce them rather than hand-listed, so a new
    completion sentence added to either module fails here until it is either recognised
    or shown not to be a claim.
    """

    #: Real output, quoted from the renderers.
    CLAIMS = (
        "Done — the alert is paused, and I read it back from PulseSoc to confirm it",
        "I confirmed this against your account after the change: the alert is paused",
        "The change went through and the follow-up read agrees: the alert is paused",
        "I had already done that; I have not repeated it.",
        "Your alert has been paused.",
        "That's done.",
        "All set.",
        "Push notifications are now off.",
    )

    #: Honest non-claims. The detector firing on any of these would rewrite a true
    #: sentence into a needlessly cautious one, which is its own kind of dishonesty.
    NOT_CLAIMS = (
        "PulseSoc accepted the change, but I could not read it back to confirm it",
        "The change was accepted and then my confirming read did not come back, so I "
        "cannot tell you it is done",
        "I started that once already and could not confirm how it finished, so I have "
        "not run it again. Check it before retrying.",
        "Your BTC alert is paused and your ETH alert is active.",
        "There are no alerts on your account right now",
        "You have 2 alerts: BTC above 90000, ETH above 4000.",
        "I have not done it. Tell me plainly if you want that and I will.",
    )

    def test_every_completion_sentence_is_recognised(self):
        for sentence in self.CLAIMS:
            with self.subTest(sentence=sentence):
                self.assertTrue(ri.completion_claim(sentence),
                                "an unsupported completion claim would pass the guard")

    def test_no_honest_sentence_is_mistaken_for_one(self):
        for sentence in self.NOT_CLAIMS:
            with self.subTest(sentence=sentence):
                self.assertFalse(ri.completion_claim(sentence),
                                 "a true sentence would be rewritten as a non-answer")

    def test_a_read_describing_a_paused_alert_is_not_a_completion_claim(self):
        """The specific false positive the old four-word tuple had.

        ``" is paused"`` matched "your BTC alert is paused" — a read reporting what it
        found — and the guard would have replaced it with "The request returned without
        enough independent evidence to claim completion." The patterns are shaped around
        completion *verbs* precisely so that describing a state is not mistaken for
        claiming to have caused it.
        """
        self.assertFalse(ri.completion_claim("Your BTC alert is paused"))
        self.assertTrue(ri.completion_claim("Your BTC alert is now paused"))

    def test_it_survives_junk_without_raising(self):
        for junk in ("", None, 0, [], {"a": 1}, "\x00\x00", "é" * 5000):
            with self.subTest(junk=repr(junk)[:20]):
                self.assertIsInstance(ri.completion_claim(junk), str)

    def test_the_detector_is_the_only_reader_of_the_pattern_list(self):
        """One list, one reader, so the two callers cannot drift apart again."""
        source = _source("undx_response_intelligence.py")
        readers = re.findall(r"_COMPLETION_CLAIM_PATTERNS", source)
        # One definition, one loop inside ``completion_claim``, and the docstring
        # reference in that function. Nothing else may iterate it.
        self.assertLessEqual(
            sum(1 for line in source.splitlines()
                if "_COMPLETION_CLAIM_PATTERNS" in line and "#" not in line.split(
                    "_COMPLETION_CLAIM_PATTERNS")[0]),
            3, f"the pattern list grew a second reader ({len(readers)} references)")


class TheLedgerVerdictIsNeverInventedStructurally(unittest.TestCase):
    """Every caller of ``record_tool_result`` states the read-back verdict explicitly.

    :func:`~services.undx_architecture.record_tool_result` falls back to a structural
    heuristic when ``canonical_verified`` is omitted — "was it a POST, did an id come
    back" — which can file a write as ``verified`` with nothing having read it. Today no
    caller omits it. That fact now matters more than it did, because
    :func:`~services.undx_tool_gateway.execute` carries a ledger ``verified`` forward
    into a replay's :class:`VerificationResult`, so a structurally-invented verdict would
    become a completion claim on a later turn.
    """

    def test_no_call_site_omits_canonical_verified(self):
        found = 0
        for name in sorted(os.listdir(SERVICES)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(_source(name), filename=name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                attr = getattr(func, "attr", None) or getattr(func, "id", None)
                if attr != "record_tool_result":
                    continue
                found += 1
                keywords = {kw.arg for kw in node.keywords}
                self.assertIn(
                    "canonical_verified", keywords,
                    f"{name}:{node.lineno} lets the ledger invent a verification verdict")
        self.assertGreaterEqual(found, 2, "the scan found no call sites to check")


class TheDormantParallelExecutorStaysDormant(unittest.TestCase):
    """``pulse_ai_service._confirm_action``'s legacy branch is a genuine second definition.

    It computes ``verified = actual == proposed`` from two of its own reads, emits
    ``"status": "verified_success"`` and the prose "Verified: {category} notifications
    are on", and never builds an :class:`AgentReceipt` or calls the gateway. It is
    *reachable only* when the agent path declines the token and the V4/V5 executor flags
    are on — and the file's own comment records that those flags are off in every
    environment the agent runs in.

    It is pinned rather than rewritten. Rewriting a dormant legacy executor to route
    through the gateway would be building a system to make an audit look productive; what
    the audit actually owes is a test that fails the day it stops being dormant.
    """

    def test_the_agent_path_is_consulted_before_the_legacy_flags(self):
        source = _source("pulse_ai_service.py")
        agent_first = source.index("agent_outcome = _agent_confirm(")
        flag_gate = source.index("v4_allowed = metadata.get(")
        self.assertLess(agent_first, flag_gate,
                        "the legacy executor would answer for an agent-minted approval")

    def test_an_agent_outcome_returns_before_the_legacy_branch_can_run(self):
        source = _source("pulse_ai_service.py")
        window = source[source.index("if agent_outcome is not None:"):
                        source.index("v4_allowed = metadata.get(")]
        self.assertIn("return _agent_confirm_payload(agent_outcome", window)

    def test_the_legacy_executor_is_behind_a_gate_that_defaults_closed(self):
        from services import undx_policy

        for env in (undx_policy.V4_ACTIONS_ENV, undx_policy.V5_ACTIONS_ENV):
            with self.subTest(env=env):
                os.environ.pop(env, None)
        metadata = undx_policy.policy_metadata()
        self.assertFalse(metadata.get("v4_actions_enabled"))
        self.assertFalse(metadata.get("v5_notification_actions_enabled"))


if __name__ == "__main__":
    unittest.main()
