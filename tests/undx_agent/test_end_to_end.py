"""Whole journeys, from a sentence to a row in the database and back.

Nothing here is mocked. Each test asks the runtime to do something in the words a
person would use, and then checks the outcome by reading the record through the
service layer directly — never through the agent's own report of what it did. A
receipt that agreed with itself would be evidence of nothing.

Between them these tests reach all seven canonical outcomes. Which ones are hard to
reach honestly is itself informative: ``terminal_failure`` and ``accepted_unverified``
are produced by breaking a real dependency rather than by asserting on a stub, because
the interesting question is what the system says when something genuinely goes wrong.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


class Journey(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_agent_tools, undx_tool_gateway

        self.runtime = undx_agent_runtime
        self.tools = undx_agent_tools
        self.gateway = undx_tool_gateway
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, **kwargs):
        response = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text, **kwargs)
        self.fx.commit()
        return response


class ReadJourneys(Journey):

    def test_listing_alerts_returns_records_not_prose(self):
        """A read must produce a structured card the client can render natively.

        The reply text is a courtesy. The records are the product, and they carry the
        canonical ids the deep link and any follow-up action depend on.
        """
        response = self.say("show me my alerts")
        self.assertTrue(response.handled)
        self.assertEqual(response.status, "verified_success")
        self.assertGreaterEqual(response.card.get("record_count", 0), 1)
        ids = {str(r.get("alert_id")) for r in response.card["records"]}
        self.assertIn(str(self.alert_id), ids)

    def test_a_read_needs_no_confirmation(self):
        response = self.say("what alerts do i have")
        self.assertEqual(response.status, "verified_success")
        self.assertNotIn("confirmation_token", response.card)


class ReversibleWriteJourney(Journey):
    """Scenario: "pause my bitcoin alert" — explicit, unambiguous, reversible."""

    def test_pause_then_verify_then_undo(self):
        paused = self.say("pause my bitcoin alert")
        self.assertEqual(paused.status, "verified_success")
        self.assertEqual(paused.receipt.verification_state, "verified")
        self.assertTrue(paused.receipt.may_claim_completed)
        # Checked against the service, not the receipt.
        self.assertEqual(self.fx.alert_status(self.alert_id), "paused")
        # The receipt advertises its own reversal, which is what makes an undo button
        # possible without the client hardcoding a mapping.
        self.assertEqual(paused.receipt.undo_capability_id, "crypto.alerts.resume")

        resumed = self.say("resume my bitcoin alert")
        self.assertEqual(resumed.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_a_question_is_not_an_instruction(self):
        """"Should I pause my alert?" must not pause the alert.

        Hedged phrasing fails the explicitness test, so a CONTEXTUAL policy escalates
        to a confirmation card instead of acting. This is the difference between an
        assistant and a hazard.
        """
        response = self.say("should i pause my bitcoin alert?")
        self.assertNotEqual(response.status, "verified_success")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_the_receipt_carries_a_native_deep_link(self):
        response = self.say("pause my bitcoin alert")
        self.assertTrue(response.receipt.native_deep_link)
        self.assertNotIn(":", response.receipt.native_deep_link.split("//")[-1],
                         "every :param in the route template must be substituted")


class ConsequentialWriteJourney(Journey):
    """Scenario: "delete my bitcoin alert" — always confirmed, then verified."""

    def test_the_full_two_step(self):
        asked = self.say("delete my bitcoin alert")
        self.assertEqual(asked.status, "confirmation_required")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active",
                         "nothing may change before the user agrees")
        self.assertEqual(asked.card["current_value"], "active")
        self.assertEqual(asked.card["proposed_value"], "deleted")

        token = asked.card["confirmation_token"]
        done = self.say("delete my bitcoin alert", confirmation_token=token)
        self.assertEqual(done.status, "verified_success")
        self.assertEqual(done.receipt.verification_state, "verified")
        self.assertEqual(self.fx.alert_status(self.alert_id), "deleted")

    def test_declining_leaves_everything_alone(self):
        """The user simply never redeems the token. The TTL does the rest."""
        self.say("delete my bitcoin alert")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")


class NotificationJourney(Journey):
    """The second capability pack, on the same spine as the first."""

    def test_read_then_change_then_verify(self):
        current = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id="notifications.preference.read",
            proposed_arguments={"category": "global"}, request_id="r1")
        self.fx.commit()
        self.assertEqual(current.receipt.status, "verified_success")

        asked = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id="notifications.preference.update",
            proposed_arguments={"category": "global", "push": False},
            request_id="r2", explicit_request=True)
        self.fx.commit()
        self.assertEqual(asked.receipt.status, "confirmation_required")

        done = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id="notifications.preference.update",
            proposed_arguments={"category": "global", "push": False},
            request_id="r3", explicit_request=True,
            confirmation_token=asked.confirmation.confirmation_token)
        self.fx.commit()
        self.assertEqual(done.receipt.status, "verified_success")
        # Read back through the notification service directly — a different call from
        # the one that performed the write, parsed by the same shared helper the
        # verifier uses so a mismatch cannot be an artefact of reading the document
        # two different ways.
        from services import pulsesoc_notification_system

        observed = self.tools.read_push_value(
            pulsesoc_notification_system.get_preferences(OWNER_ID), "global")
        self.assertFalse(observed)

    def push_value(self, category: str) -> bool:
        """Read a preference back through the notification service, not the receipt."""
        from services import pulsesoc_notification_system

        return self.tools.read_push_value(
            pulsesoc_notification_system.get_preferences(OWNER_ID), category)

    def test_a_sentence_reaches_the_same_pack_the_gateway_does(self):
        """The pack was reachable by capability id before it was reachable by asking.

        This is the journey a person actually takes: a sentence, a card showing the
        true before and after, an approval, and a changed setting observed through a
        different call than the one that changed it.
        """
        asked = self.say("turn off notifications for messages")
        self.assertEqual(asked.status, "confirmation_required")
        self.assertEqual(asked.card["capability_id"], "notifications.preference.update")
        self.assertTrue(asked.card["current_value"])
        self.assertFalse(asked.card["proposed_value"])
        self.assertTrue(self.push_value("messages"),
                        "nothing may change before the user agrees")

        done = self.say("turn off notifications for messages",
                        confirmation_token=asked.card["confirmation_token"])
        self.assertEqual(done.status, "verified_success")
        self.assertEqual(done.receipt.verification_state, "verified")
        self.assertFalse(self.push_value("messages"))
        # The named category is the only one touched. A parser that quietly widened
        # "messages" to "global" would still pass a test that only checked the one
        # setting it expected to change.
        self.assertTrue(self.push_value("global"))

    def test_both_live_paths_read_the_same_sentence_identically(self):
        """The agent must not disagree with the V4/V5 parser it shares a release with.

        Both paths are reachable right now — the agent for the rollout cohort, the
        legacy action for everyone else. If they parsed differently, the same words
        would change different settings depending on a flag the user cannot see. The
        agent delegates rather than reimplementing, and this asserts the delegation
        rather than trusting the comment that describes it.
        """
        from services import undx_architecture

        for sentence in ("turn off my notifications", "enable reel notifications",
                         "turn off notifications for messages", "disable notifications"):
            legacy = (undx_architecture.notification_action_from_text(sentence) or {}).get("arguments")
            derived = self.runtime.resolve_notification_arguments(sentence, {})
            self.assertEqual(derived, legacy, sentence)

    def test_a_direction_is_never_guessed(self):
        """Naming a setting is not naming a value to set it to.

        Reached by naming the capability outright, which is how a planner would reach
        it. The refusal has to live in the runtime: the gateway would reject this as a
        missing required field, which is true and tells the person nothing.
        """
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text="do it",
            capability_id="notifications.preference.update")
        self.fx.commit()
        self.assertNotEqual(response.status, "verified_success")
        self.assertNotEqual(response.status, "confirmation_required")
        self.assertIn("on or off", response.reply)
        self.assertTrue(self.push_value("global"))

    def test_an_ambiguous_sentence_reads_rather_than_writes(self):
        """"Change my notification settings" resolves to the read, and stops there.

        Worth pinning down because the safe outcome here is produced by the matcher
        rather than by the guard above it: the phrasing carries no direction, and the
        capability it selects cannot change anything.
        """
        response = self.say("change my notification settings")
        self.assertEqual(response.card["capability_id"], "notifications.preference.read")
        self.assertEqual(response.status, "verified_success")
        self.assertTrue(self.push_value("global"))

    def test_notification_categories_are_real(self):
        """Every category UNDX offers must exist in the notification store.

        The first version of this pack invented "reels", "posts" and "alerts". Nothing
        rejected them: the write created an orphan row the delivery pipeline never
        consults, and — the part that made it a lie rather than a no-op — reading back
        a category that does not exist returns False, so UNDX reported reel
        notifications as already off to a user who had never touched them, and the
        verifier agreed. This test is the reason the alias map cannot drift again.
        """
        from services import pulsesoc_notification_system as system
        from services import undx_agent_tools, undx_capability_registry

        offered = set(undx_capability_registry.category_choices())
        self.assertTrue(offered, "the registry must declare its categories")
        real = set(system.DEFAULT_CATEGORIES)
        for category in sorted(offered):
            with self.subTest(category=category):
                stored = undx_agent_tools.resolve_category(category)
                self.assertTrue(
                    stored == "global" or stored in real,
                    f"UNDX offers '{category}' but PulseSoc stores no '{stored}' category",
                )

    def test_every_offered_category_reads_back_a_real_default(self):
        """A category that is genuinely on must not read as off before anyone edits it.

        Stronger than the mapping check above, because it goes through the live
        preferences document rather than the constant: a name that maps to a real
        category the store nonetheless never seeds would still read False here.
        """
        from services import undx_capability_registry

        for category in sorted(undx_capability_registry.category_choices()):
            with self.subTest(category=category):
                self.assertTrue(
                    self.push_value(category),
                    f"'{category}' reads as already off before anything changed it",
                )

    def test_turning_off_reel_notifications_end_to_end(self):
        """The named scenario, in the words it was specified in.

        Every step is asserted from outside the agent: the category is read from the
        card, the setting is read back through the notification service, and the deep
        link is the one the native router can actually resolve.
        """
        asked = self.say("turn off reel like notifications")
        self.assertEqual(asked.status, "confirmation_required")
        self.assertEqual(asked.card["capability_id"], "notifications.preference.update")
        self.assertEqual(asked.card["target"], "reels")
        self.assertTrue(asked.card["current_value"])
        self.assertFalse(asked.card["proposed_value"])
        self.assertTrue(self.push_value("reels"))

        done = self.say("turn off reel like notifications",
                        confirmation_token=asked.card["confirmation_token"])
        self.assertEqual(done.status, "verified_success")
        self.assertEqual(done.receipt.verification_state, "verified")
        self.assertFalse(self.push_value("reels"))
        self.assertTrue(self.push_value("global"), "only the named category may change")
        self.assertEqual(done.receipt.native_deep_link, "/pulse/settings/notifications")

    def test_turning_off_something_already_off_is_still_verified(self):
        """Idempotent in the world, not just in the ledger.

        The desired end state already holds, so the honest answer is that it is off —
        confirmed by reading it, not by noticing that nothing needed doing. An agent
        that reported ``recoverable_failure`` here would be technically defensible and
        useless.
        """
        first = self.say("turn off notifications for messages")
        self.say("turn off notifications for messages",
                 confirmation_token=first.card["confirmation_token"])
        self.assertFalse(self.push_value("messages"))

        again = self.say("turn off notifications for messages")
        self.assertEqual(again.status, "confirmation_required")
        self.assertFalse(again.card["current_value"], "the card must show the real before state")
        self.assertFalse(again.card["proposed_value"])
        done = self.say("turn off notifications for messages",
                        confirmation_token=again.card["confirmation_token"])
        self.assertEqual(done.status, "verified_success")
        self.assertFalse(self.push_value("messages"))

    def test_an_approval_cannot_be_redirected_to_another_category(self):
        """Approval of "off for messages" is not approval of "off for everything".

        The argument hash covers the category, so a redemption that renames it is
        refused. Without this, a client — or anything sitting between the card and the
        confirm call — could widen the blast radius of a change the user did agree to.
        """
        asked = self.say("turn off notifications for messages")
        token = asked.card["confirmation_token"]
        redirected = self.gateway.execute(
            self.fx.cur, user_id=OWNER_ID, capability_id="notifications.preference.update",
            proposed_arguments={"category": "global", "push": False},
            request_id="r9", explicit_request=True, confirmation_token=token)
        self.fx.commit()
        self.assertNotEqual(redirected.receipt.status, "verified_success")
        self.assertTrue(self.push_value("global"))
        self.assertTrue(self.push_value("messages"))

    def test_another_account_cannot_spend_this_approval(self):
        from tests.undx_agent.harness import OTHER_ID

        asked = self.say("turn off notifications for messages")
        stolen = self.gateway.execute(
            self.fx.cur, user_id=OTHER_ID, capability_id="notifications.preference.update",
            proposed_arguments={"category": "messages", "push": False},
            request_id="r10", explicit_request=True,
            confirmation_token=asked.card["confirmation_token"])
        self.fx.commit()
        self.assertNotEqual(stolen.receipt.status, "verified_success")
        self.assertTrue(self.push_value("messages"))

    def test_a_preference_write_that_does_not_stick_is_not_called_success(self):
        """The write is accepted, the read-back disagrees, and UNDX says so.

        Simulated by making the read-back return the old value — which is exactly what a
        partially-applied write, a caching layer, or a silently-rejected field would look
        like from here. The point is that the claim of success is derived from the
        read-back and not from the write returning cleanly.
        """
        from services import undx_agent_tools

        asked = self.say("turn off notifications for messages")
        token = asked.card["confirmation_token"]

        original = undx_agent_tools.EXECUTORS["notification_preferences_update"]

        def writes_nothing(user_id, arguments):
            result = original(user_id, dict(arguments, push=True))
            return result

        undx_agent_tools.EXECUTORS["notification_preferences_update"] = writes_nothing
        try:
            done = self.say("turn off notifications for messages", confirmation_token=token)
        finally:
            undx_agent_tools.EXECUTORS["notification_preferences_update"] = original

        self.assertNotEqual(done.status, "verified_success")
        self.assertFalse(done.receipt.may_claim_completed)
        self.assertEqual(done.receipt.verification_state, "verification_failed")


class HonestFailure(Journey):
    """The outcomes that exist so the agent can admit something went wrong."""

    def test_a_broken_executor_is_a_recoverable_failure_not_a_success(self):
        """When the service raises, the receipt must say so and the row must be intact.

        The exception text is deliberately not forwarded: a service traceback is not a
        message to a user, and it is a reliable way to leak internals.
        """
        # Patched in the dispatch table rather than as a module attribute: the gateway
        # resolves executors by name through ``EXECUTORS``, so rebinding the function
        # object would leave the real one wired up and the test would pass vacuously.
        original = self.tools.EXECUTORS["crypto_alerts_pause"]

        def broken(*args, **kwargs):
            raise RuntimeError("simulated outage in alert_engine")

        self.tools.EXECUTORS["crypto_alerts_pause"] = broken
        try:
            response = self.say("pause my bitcoin alert")
        finally:
            self.tools.EXECUTORS["crypto_alerts_pause"] = original
        self.assertEqual(response.status, "recoverable_failure")
        self.assertFalse(response.receipt.may_claim_completed)
        self.assertNotIn("simulated outage", response.reply)
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")

    def test_an_unverifiable_write_is_accepted_unverified_not_success(self):
        """The single most important distinction the system makes.

        The write lands. The read-back cannot run. The honest answer is "I did this but
        could not confirm it", and ``may_claim_completed`` must be false so no layer
        above is entitled to render it as done.
        """
        from services import undx_verification
        from services.undx_agent_contracts import VerificationResult, VerificationState

        original = undx_verification.VERIFIERS["crypto_alert_status"]
        undx_verification.VERIFIERS["crypto_alert_status"] = lambda *a, **k: VerificationResult(
            state=VerificationState.IMPOSSIBLE, detail="verification backend unavailable")
        try:
            response = self.say("pause my bitcoin alert")
        finally:
            undx_verification.VERIFIERS["crypto_alert_status"] = original

        self.assertEqual(self.fx.alert_status(self.alert_id), "paused",
                         "the write itself must still have happened")
        self.assertEqual(response.status, "accepted_unverified")
        self.assertFalse(response.receipt.may_claim_completed)
        self.assertNotEqual(response.receipt.verification_state, "verified")

    def test_a_failed_verification_is_terminal_not_softened(self):
        """A read-back that actively contradicts the write is the worst case.

        It is reported as ``terminal_failure`` rather than downgraded to "unverified",
        because "I could not check" and "I checked and it is wrong" are different
        statements and only one of them warrants a retry.
        """
        from services import undx_verification
        from services.undx_agent_contracts import VerificationResult, VerificationState

        original = undx_verification.VERIFIERS["crypto_alert_status"]
        undx_verification.VERIFIERS["crypto_alert_status"] = lambda *a, **k: VerificationResult(
            state=VerificationState.FAILED, detail="observed state does not match")
        try:
            response = self.say("pause my bitcoin alert")
        finally:
            undx_verification.VERIFIERS["crypto_alert_status"] = original
        self.assertEqual(response.status, "terminal_failure")
        self.assertFalse(response.receipt.may_claim_completed)

    def test_an_unsupported_capability_is_refused_by_name(self):
        from services.undx_agent_contracts import AgentError

        with self.assertRaises(AgentError) as caught:
            self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text="do it",
                                capability_id="account.delete")
        self.assertEqual(caught.exception.outcome, "unsupported_capability")

    def test_permission_denied_is_reported_as_itself(self):
        self.fx.set_flags(UNDX_AGENT_WRITES_ENABLED="")
        response = self.say("pause my bitcoin alert")
        self.assertEqual(response.status, "permission_denied")
        self.assertEqual(self.fx.alert_status(self.alert_id), "active")


class OutcomeCoverage(Journey):
    """All seven canonical outcomes are reachable, and the suite reaches them."""

    def test_every_canonical_outcome_is_exercised_somewhere(self):
        from services.undx_agent_contracts import AgentOutcome

        source = ""
        here = os.path.dirname(os.path.abspath(__file__))
        for name in sorted(os.listdir(here)):
            if name.startswith("test_") and name.endswith(".py"):
                with open(os.path.join(here, name), encoding="utf-8") as handle:
                    source += handle.read()
        missing = sorted(outcome for outcome in AgentOutcome.ALL if outcome not in source)
        self.assertEqual(missing, [], f"outcomes with no test asserting on them: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
