"""The agent as seen from ``/api/pulse-ai/message``.

Everything below this file has already been proven against the real service layer.
What is unproven until here is the claim made in the comment beside the wiring: that
when the agent is switched off, the V4/V5 conversational path is untouched. That
claim protects every existing deployment, so it is asserted rather than trusted.

``send_message`` calls a model provider on the conversational path. These tests never
assert on what that provider says — only on which component answered, which is the
property under test and the one that does not depend on a network.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID, OUTSIDER_ID  # noqa: E402


class TransportWiring(unittest.TestCase):
    """The seam between the messenger endpoint and the agent runtime."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import pulse_ai_service

        self.svc = pulse_ai_service
        self.svc.ensure_schema(self.fx.cur, self.fx.conn)
        self.fx.commit()
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")

    def tearDown(self) -> None:
        self.fx.stop()

    # -- the off switch ---------------------------------------------------

    def test_disabled_agent_never_answers(self):
        """With the master flag cleared, an actionable sentence is still conversation.

        This is the regression guard for every deployment that has not opted in. The
        text used is the one the matcher is *most* confident about, so a pass here
        means the gate is in front of the matcher rather than beside it.
        """
        self.fx.set_flags(UNDX_AGENT_ENABLED="")
        result = self.svc._agent_turn(
            self.fx.cur, OWNER_ID, "pause my bitcoin alert", {},
            conversation_id=1, correlation_id="c1",
        )
        self.assertIsNone(result)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_user_outside_cohort_never_answers(self):
        """Enabled globally is not enabled for everyone."""
        result = self.svc._agent_turn(
            self.fx.cur, OUTSIDER_ID, "pause my bitcoin alert", {},
            conversation_id=1, correlation_id="c1",
        )
        self.assertIsNone(result)

    # -- fall-through -----------------------------------------------------

    def test_conversation_falls_through(self):
        """An unhandled turn must be ``None``, not a falsy-looking object.

        ``AgentResponse`` is a plain object; before it defined ``__bool__`` an
        unhandled turn was truthy, and the endpoint would have short-circuited the
        provider to reply with an empty string. The failure was silent and total, so
        it gets an explicit test.
        """
        for text in ("what is bitcoin?", "how are you today", "explain staking to me"):
            with self.subTest(text=text):
                result = self.svc._agent_turn(
                    self.fx.cur, OWNER_ID, text, {}, conversation_id=1, correlation_id="c1",
                )
                self.assertIsNone(result)

    def test_unhandled_response_is_falsy(self):
        from services.undx_agent_runtime import AgentResponse

        self.assertFalse(AgentResponse(handled=False))
        self.assertTrue(AgentResponse(handled=True, reply="done"))

    def test_unimplemented_operational_writes_fail_closed_before_chat(self):
        for text, reason in (
            ("Send a message saying hello.", "Message sending"),
            ("Buy 100 dollars of Bitcoin.", "Financial transactions"),
            ("Delete my last Reel.", "Destructive content actions"),
        ):
            with self.subTest(text=text):
                result = self.svc._agent_turn(
                    self.fx.cur, OWNER_ID, text, {}, conversation_id=1,
                    correlation_id="blocked-op",
                )
                self.assertIsNotNone(result)
                self.assertIn(reason, result.reply)
                self.assertIn("did not make any change", result.reply)

    def test_post_deletion_routes_to_governed_capability_and_requests_a_target(self):
        result = self.svc._agent_turn(
            self.fx.cur, OWNER_ID, "Delete my last post.", {}, conversation_id=1,
            correlation_id="post-delete",
        )
        self.assertIsNotNone(result)
        self.assertIn("Which post", result.reply)
        self.assertNotIn("not enabled", result.reply)

    def test_draft_without_send_and_explanatory_write_language_still_fall_through(self):
        for text in ("Draft a reply, but do not send it.", "Why would I pause an alert?"):
            with self.subTest(text=text):
                result = self.svc._agent_turn(
                    self.fx.cur, OWNER_ID, text, {}, conversation_id=1,
                    correlation_id="non-action",
                )
                self.assertIsNone(result)

    def test_ordinary_questions_still_reach_the_model_provider(self):
        """The regression that matters most, because its failure mode is silent.

        These are the sentences UNDX is for. If the agent claims any of them the user
        gets an empty reply instead of an answer, and nothing errors — chat simply
        stops working for the cohort the agent is enabled for. Both halves are asserted:
        the runtime declines the turn, and the transport converts that into the ``None``
        that lets ``send_message`` fall through to the provider.
        """
        from services import undx_agent_runtime

        for text in ("What is artificial intelligence?",
                     "Help me write a birthday message.",
                     "What can UNDX do?",
                     "explain how staking rewards work",
                     "why is my portfolio down this week"):
            with self.subTest(text=text):
                direct = undx_agent_runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text)
                self.assertFalse(direct.handled, "the agent claimed a conversational turn")
                self.assertFalse(bool(direct), "an unhandled turn must be falsy")
                self.assertEqual(direct.reply, "")
                self.assertIsNone(self.svc._agent_turn(
                    self.fx.cur, OWNER_ID, text, {}, conversation_id=1, correlation_id="c1"))

    def test_a_question_about_an_action_never_performs_it(self):
        """A hedged sentence may be answered, but it may not be acted on.

        These all name a real capability, so the matcher reaches one — that is correct
        and useful; "what does pausing do" is fairly answered by showing the alerts. What
        must not happen is a write. So the assertion is not "the agent stayed quiet" but
        the narrower and truer one: nothing it did was a completed change.
        """
        from services import undx_capability_registry

        writes = set(undx_capability_registry.write_capability_ids())
        for text in ("what does pausing an alert do",
                     "can you turn off notifications for me?",
                     "should i delete my bitcoin alert"):
            with self.subTest(text=text):
                result = self.svc._agent_turn(
                    self.fx.cur, OWNER_ID, text, {}, conversation_id=1, correlation_id="c1")
                if result is not None and result.capability_id in writes:
                    self.assertNotEqual(
                        result.status, "verified_success",
                        "a hedged sentence must not complete a write")
                    self.assertFalse(result.receipt.may_claim_completed)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    # -- the handled path -------------------------------------------------

    def test_handled_turn_returns_a_receipt(self):
        result = self.svc._agent_turn(
            self.fx.cur, OWNER_ID, "pause my bitcoin alert", {},
            conversation_id=1, correlation_id="c1",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.handled)
        self.assertEqual(result.capability_id, "crypto.alerts.pause")
        self.assertEqual(result.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")
        self.assertTrue(result.reply)
        self.assertTrue(result.card)

    def test_receipt_survives_serialisation(self):
        """The endpoint puts ``to_dict()`` on the wire; it must not raise or lose the
        outcome, because the client renders its card from exactly this payload."""
        result = self.svc._agent_turn(
            self.fx.cur, OWNER_ID, "pause my bitcoin alert", {},
            conversation_id=1, correlation_id="c1",
        )
        import json

        blob = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(blob["status"], "verified_success")
        self.assertEqual(blob["receipt"]["verification_state"], "verified")
        self.assertTrue(blob["card"])

    # -- coexistence with the legacy notification action -------------------

    def test_the_agent_wins_the_notification_sentence_for_its_cohort(self):
        """Two systems can answer "turn off my notifications". Only one may.

        ``send_message`` calls the agent before it reaches the V4/V5 notification
        branch, so for a cohort user the sentence becomes an agent confirmation and
        the legacy pending-action never forms. Precedence is what makes the migration
        a migration rather than a race, and it is not visible from either module alone.
        """
        result = self.svc._agent_turn(
            self.fx.cur, OWNER_ID, "turn off my notifications", {},
            conversation_id=1, correlation_id="c1",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.capability_id, "notifications.preference.update")
        self.assertEqual(result.status, "confirmation_required")
        self.assertTrue(result.card.get("confirmation_token"))

    def test_outside_the_cohort_the_legacy_action_still_owns_that_sentence(self):
        """The same words, a different account, and the agent declines entirely.

        The assertion is deliberately about the agent returning ``None`` rather than
        about what the legacy branch then does: that branch is covered by the existing
        V4/V5 suites, and the property this migration must not break is simply that
        control still reaches it.
        """
        result = self.svc._agent_turn(
            self.fx.cur, OUTSIDER_ID, "turn off my notifications", {},
            conversation_id=1, correlation_id="c1",
        )
        self.assertIsNone(result)

        from services import undx_architecture

        self.assertEqual(
            (undx_architecture.notification_action_from_text("turn off my notifications") or {})
            .get("action_id"),
            "notifications.preference.update",
            "the legacy parser must still recognise what the agent just declined",
        )

    # -- failure containment ----------------------------------------------

    def test_runtime_exception_degrades_to_conversation(self):
        """A broken agent must cost the user an action, never their message.

        The runtime is replaced with one that raises on every call — the bluntest
        possible simulation of a bad deploy — and the transport is required to answer
        ``None`` so the conversational path still runs.
        """
        import services.undx_agent_runtime as runtime

        original = runtime.handle
        runtime.handle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            result = self.svc._agent_turn(
                self.fx.cur, OWNER_ID, "pause my bitcoin alert", {},
                conversation_id=1, correlation_id="c1",
            )
        finally:
            runtime.handle = original
        self.assertIsNone(result)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_unauthenticated_caller_does_not_leak_an_exception(self):
        result = self.svc._agent_turn(
            self.fx.cur, 0, "pause my bitcoin alert", {},
            conversation_id=1, correlation_id="c1",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
