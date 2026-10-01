"""Falsification suite for counting deferred capabilities as inside the focus.

The runtime change under review is small and sensitive: both attention vetoes in
:mod:`services.undx_agent_runtime` — the selection gate and the planner gate — now
treat ``Focus.deferred`` as inside the focus, where before only ``capability_ids``
counted. The argument for it is that deferral is the capability *budget* cutting,
not a relevance judgment, and the veto is about relevance; ``goals._reads_in`` and
``Area.reachable`` already read deferred the same way.

The argument against it is the one this file tries to prove: that widening the veto
makes UNDX more eager to perform actions nobody asked for. Each test is a targeted
attempt at that failure, not a demonstration of the feature. Where a test forces the
selection layer's output with a stub, that is deliberate — the question is not "does
selection choose badly" (it has its own suite) but "if it ever did, what stands
behind the veto". A veto that is only safe while the layer in front of it is perfect
is not a veto.

The six checks, and what each would falsify:

1. QUESTION — "Should I pause my Bitcoin alert?" reads context; the pause write does
   not run. Falsified if the deferred read's dispatch comes with a write.
2. DISTRACTORS — the same question padded with unrelated wants; a write outside the
   focus, forced into the selection, must still be vetoed. Falsified if distractor
   vocabulary pulls an unrelated deferred write into executability.
3. AMBIGUOUS WRITE — a contested selection still asks which, before any focus
   arithmetic. Falsified if the widened focus resolves ambiguity instead of the user.
4. READ-ONLY QUESTION — a plain retrieval stays a retrieval. Falsified if any write
   becomes newly reachable on a turn that asked for a list.
5. CROSS-DOMAIN — a capability from the wrong domain, forced into the selection,
   stays outside the focus and is rejected. Falsified if deferral in *some* focus
   made the capability dispatchable in *this* one.
6. BUDGET OVERFLOW — a question whose relevant set exceeds ``MAX_CAPABILITIES``:
   the focus stays bounded, and a deferred *write* that passes the widened veto is
   still stopped by the layers the veto was never a substitute for (hedged text is
   not explicit, so a contextual write gets a card, not an execution). Falsified if
   overflow plus the widened veto adds up to an unconfirmed write.

Every focus-shape claim a test relies on is asserted as a precondition against the
real attention layer, so a vocabulary or budget change that invalidates the scenario
fails loudly here instead of quietly testing nothing.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

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

#: The overflow question. Eleven capabilities are relevant to it — six make the
#: focus, five are deferred — which makes it both check 1 and the substrate for
#: check 6.
PAUSE_QUESTION = "Should I pause my Bitcoin alert?"


def _selection(**fields):
    from services.undx_brain.selection import Selection

    return Selection(**fields)


def _forced(selection):
    """Patch the selection layer to return ``selection`` regardless of the text."""
    return mock.patch(
        "services.undx_brain.selection.select",
        lambda text, **kwargs: selection,
    )


class DeferredFocusVetoFalsification(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture(**BRAIN).start()
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")
        from services import undx_agent_runtime
        from services.undx_brain import attention

        self.runtime = undx_agent_runtime
        self.attention = attention

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str):
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text=text,
            correlation_id="deferred-focus-falsification",
        )
        self.fx.commit()
        return response

    def focus(self, text: str):
        focus = self.attention.attend(text)
        self.assertTrue(focus.ok, "attention must be live for these scenarios to mean anything")
        return focus

    # -- 1. QUESTION ------------------------------------------------------

    def test_question_reads_context_and_performs_no_write(self):
        """The deferred read is dispatched; the in-focus pause write is not.

        The preconditions are the point: the read that answers the question sits in
        ``deferred``, not ``capability_ids``, so before the change this turn was a
        silent conversational fallthrough. The change makes it a read — and must
        make it nothing more.
        """
        focus = self.focus(PAUSE_QUESTION)
        self.assertIn("crypto.alerts.pause", focus.capability_ids)
        self.assertIn("crypto.alerts.list", focus.deferred)
        self.assertNotIn("crypto.alerts.list", focus.capability_ids)

        response = self.say(PAUSE_QUESTION)
        self.assertTrue(response.handled)
        self.assertIsNotNone(response.receipt)
        self.assertEqual(response.receipt.risk_level, "read_only")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- 2. DISTRACTORS ---------------------------------------------------

    def test_unrelated_deferred_write_does_not_become_executable(self):
        """Distractor vocabulary widens the focus; it must not widen it to *that*.

        The distractors are real — "order" and "party" pull marketplace capabilities
        into the focus — and the forced selection is a write that no amount of that
        drift admits. The veto must drop it, and the turn must end as the
        conversation it always was, not as a message send.
        """
        text = (PAUSE_QUESTION
                + " Also what pizza should I order for the party?")
        focus = self.focus(text)
        everywhere = focus.capability_ids + focus.deferred
        self.assertNotIn("messages.send", everywhere)
        self.assertTrue(focus.deferred, "the scenario needs a non-empty deferred set")

        forced = _selection(ok=True, decided=True, capability_id="messages.send")
        with _forced(forced):
            response = self.say(text)
        self.assertFalse(response.handled)
        self.assertIsNone(response.receipt)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- 3. AMBIGUOUS WRITE -----------------------------------------------

    def test_ambiguous_write_still_asks_which_one(self):
        """A contested selection clarifies before any focus membership is consulted.

        Both contested ids are inside the focus for this text, so if the widened
        veto were consulted first it would wave either through. The contested branch
        must win, and no write may run.
        """
        focus = self.focus(PAUSE_QUESTION)
        self.assertIn("crypto.alerts.pause", focus.capability_ids)
        self.assertIn("crypto.alerts.delete", focus.capability_ids)

        forced = _selection(
            ok=True, decided=False,
            contested=("crypto.alerts.pause", "crypto.alerts.delete"),
        )
        with _forced(forced):
            response = self.say(PAUSE_QUESTION)
        self.assertTrue(response.handled)
        self.assertEqual(response.card["status"], "clarification_required")
        self.assertEqual(
            response.card["candidate_capability_ids"],
            ["crypto.alerts.pause", "crypto.alerts.delete"],
        )
        self.assertFalse(response.card["may_claim_done"])
        self.assertIsNone(response.receipt)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- 4. READ-ONLY QUESTION --------------------------------------------

    def test_read_only_question_reaches_no_write(self):
        response = self.say("show me my alerts")
        self.assertTrue(response.handled)
        self.assertIsNotNone(response.receipt)
        self.assertEqual(response.receipt.risk_level, "read_only")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- 5. CROSS-DOMAIN --------------------------------------------------

    def test_cross_domain_capability_stays_rejected(self):
        """Deferred-in-some-focus is not deferred-in-this-one.

        ``crypto.alerts.pause`` is squarely inside the focus of the pause question;
        for a messages question it is in neither set, and the widened veto must
        reject it exactly as the narrow one did.
        """
        text = "what is in my message inbox?"
        focus = self.focus(text)
        self.assertNotIn("crypto.alerts.pause", focus.capability_ids + focus.deferred)

        forced = _selection(ok=True, decided=True, capability_id="crypto.alerts.pause")
        with _forced(forced):
            response = self.say(text)
        self.assertFalse(response.handled)
        self.assertIsNone(response.receipt)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- 6. BUDGET OVERFLOW -----------------------------------------------

    def test_overflow_stays_bounded_and_a_deferred_write_is_not_escalated(self):
        """The worst case the change makes possible, driven to its conclusion.

        The pause question's relevant set exceeds the capability budget, so the
        focus is full and the overflow is real. A *write* from the deferred set,
        forced into the selection, now passes the widened veto — that is the change
        working as designed. What must then happen is what this test exists to pin:
        the hedged sentence is not an explicit instruction, so the contextual write
        gets a confirmation card and nothing mutates. If this assertion ever fails
        toward execution, the veto widening has become an escalation and must be
        reverted.
        """
        focus = self.focus(PAUSE_QUESTION)
        self.assertEqual(len(focus.capability_ids), self.attention.MAX_CAPABILITIES)
        self.assertIn("crypto.alerts.resume", focus.deferred)
        self.assertFalse(self.runtime.is_explicit(PAUSE_QUESTION))

        forced = _selection(ok=True, decided=True, capability_id="crypto.alerts.resume")
        with _forced(forced):
            response = self.say(PAUSE_QUESTION)
        self.assertTrue(response.handled)
        self.assertNotEqual(response.status, "verified_success")
        self.assertEqual(response.card["status"], "confirmation_required")
        self.assertFalse(response.card["may_claim_done"])
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")


if __name__ == "__main__":
    unittest.main()
