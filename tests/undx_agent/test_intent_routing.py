"""What the deterministic matcher must refuse, and what it must not refuse.

``match_capability`` scores an intent phrase by the characters its tokens match, in
order, anywhere in the message. That is the right shape for the job — it is why "pause
alert" reaches "pause my Bitcoin alert" — and it has one blind spot that matters: the
tokens that earn the score are identical in "unfollow user 7" and "do not unfollow user
7". Scoring cannot see the difference, so something else has to.

The stake is not uniform across the registry. Eleven of the sixteen writes confirm
``always`` or ``contextual``, so a bad match there costs the person a card they did not
want. Five confirm ``never`` — ``saved.post.set``, ``social.follow``,
``social.unfollow``, ``feed.posts.like``, ``feed.posts.unlike`` — because each is cheap
to reverse, which is a defensible choice on its own. Combined with a matcher that
cannot read the word "not", it means "do not unfollow him" had nothing at all between
the sentence and the act. These tests are written against that combination rather than
against either half of it.

Two properties, and the second is why this file is longer than one assertion:

1. A message that *names* a write without *asking* for it must not reach that write,
   nor any other write.
2. A message that names a write and does ask for it must still reach it. A guard that
   bought property 1 by refusing anything containing "not" would pass a test suite made
   only of property-1 cases, and would have broken "delete alert 3 and do not ask
   again" — an ordinary instruction — in the process.

The negatives are here too, because "routes to nothing" is the same guarantee seen from
another side: a matcher that always finds something acts on greetings.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_agent_runtime import (  # noqa: E402
    asks_for_the_action, is_explicit, match_capability)
from services.undx_capability_registry import REGISTRY  # noqa: E402


def _matched(message: str) -> str:
    spec = match_capability(message)
    return spec.capability_id if spec else ""


class _RoutingAssertions(unittest.TestCase):

    def assertNoWrite(self, message: str) -> str:
        """No write, not merely not *that* write.

        Excluding only the obvious candidate would let the second-best write win, and
        "I was going to save post 9 but changed my mind" landing on a different write
        is not an improvement over landing on the right one.
        """
        landed = _matched(message)
        if landed:
            self.assertFalse(
                REGISTRY[landed].is_write,
                f"{message!r} reached the write {landed}")
        return landed

    def assertRoutes(self, message: str, capability_id: str) -> None:
        self.assertEqual(capability_id, _matched(message), repr(message))


class NegatedWriteTests(_RoutingAssertions):
    """"Do not X" must not do X — the case the scorer cannot see."""

    NEGATED = (
        "do not unfollow user 7",
        "do not delete alert 3 whatever you do",
        "do not like post 4 on my behalf",
        "do not turn off notifications for me",
        "i do not want to follow user 12",
        "don't unfollow user 7",
        "never delete alert 3",
    )

    def test_a_negated_write_reaches_no_write(self) -> None:
        for message in self.NEGATED:
            with self.subTest(message):
                self.assertNoWrite(message)

    def test_the_five_writes_with_no_confirmation_card_are_covered_here(self) -> None:
        """The list above is not arbitrary, and this test is what keeps it honest.

        A write that confirms ``never`` has no second line of defence, so every one of
        them needs a negated phrasing in this file. If someone adds a sixth such write
        the registry will change and this test will name it.
        """
        unguarded = {cid for cid, spec in REGISTRY.items()
                     if spec.is_write and spec.confirmation == "never"}
        # The verb each unguarded write answers to, as a person would type it.
        probes = {
            "saved.post.set": "do not save post 9",
            "social.follow": "do not follow user 12",
            "social.unfollow": "do not unfollow user 7",
            "feed.posts.like": "do not like post 4",
            "feed.posts.unlike": "do not unlike post 4",
        }
        self.assertEqual(unguarded, set(probes),
                         "a write confirming 'never' has no card behind it and needs a "
                         "negated phrasing here")
        for capability_id, message in probes.items():
            with self.subTest(capability_id):
                self.assertNoWrite(message)


class NegationScopeTests(_RoutingAssertions):
    """A negation binds to the verb after it, not to the whole sentence."""

    def test_a_trailing_negation_does_not_cancel_the_instruction(self) -> None:
        """These are instructions. The "not" governs something that is not a capability.

        This is the test that stops the guard from being implemented as ``"not" in
        message``, which would pass every test in the class above and quietly break
        ordinary use.
        """
        self.assertRoutes("delete alert 3, i do not need it", "crypto.alerts.delete")
        self.assertRoutes("delete alert 10 and do not ask again", "crypto.alerts.delete")
        self.assertRoutes("pause alert 2 and do not tell me again", "crypto.alerts.pause")
        self.assertRoutes("delete post 999, i do not need it", "feed.posts.delete")

    def test_a_leading_negation_does_cancel_it(self) -> None:
        self.assertNoWrite("do not delete alert 3")
        self.assertNoWrite("please do not ever delete alert 3")

    def test_the_reach_stops_before_the_next_clause(self) -> None:
        """Four tokens covers "do not want to follow" and stops short of a new clause.

        Stated as a test because the number is a judgement call, and a judgement call
        that nothing asserts is a number someone will change without knowing what it
        was for.
        """
        self.assertNoWrite("i do not want to follow user 12")
        self.assertRoutes("show me my alerts, i do not care about anything else, "
                          "then delete alert 3", "crypto.alerts.delete")


class DeliberationTests(_RoutingAssertions):
    """Weighing an action is not requesting it."""

    WEIGHING = (
        "should i unfollow user 7 or not",
        "is it a good idea to pause alert 2",
        "i am thinking about whether to delete alert 3",
        "i might pause alert 2 later, not now",
        "i was going to save post 9 but changed my mind",
        "would you like post 4 if you were me",
        "is it safe to follow user 12",
    )

    def test_deliberation_reaches_no_write(self) -> None:
        for message in self.WEIGHING:
            with self.subTest(message):
                self.assertNoWrite(message)


class ExplanationTests(_RoutingAssertions):
    """Asking what an action does is a question about the action, not the action."""

    ASKING = (
        "what does it mean to delete alert 3",
        "what happens when i save post 9",
        "what would happen if i unfollow user 7",
        "explain what turn off notifications actually does",
        "remind me how to pause alert 2 myself",
        "why would i follow user 12",
        "i already liked post 4, did i not",
    )

    def test_explanation_reaches_no_write(self) -> None:
        for message in self.ASKING:
            with self.subTest(message):
                self.assertNoWrite(message)

    def test_a_question_about_a_thing_may_still_reach_a_read(self) -> None:
        """Refusing the write is not the same as refusing to answer.

        Someone asking what deleting an alert would do is asking about the alert, and
        landing on the alert read is the useful outcome. This is asserted so that a
        future tightening of the guard cannot make the system merely silent and call it
        safety.
        """
        landed = _matched("what does it mean to delete alert 3")
        self.assertTrue(landed, "the question should still reach a read")
        self.assertFalse(REGISTRY[landed].is_write)


class PoliteRequestTests(_RoutingAssertions):
    """"Can you" is a request. The guard must not confuse manners with hesitation."""

    def test_polite_forms_still_reach_the_write(self) -> None:
        for prefix in ("", "hey ", "can you ", "could you ", "please ", "quick one - "):
            with self.subTest(prefix or "(bare)"):
                self.assertRoutes(f"{prefix}unfollow user 7", "social.unfollow")

    def test_the_two_predicates_disagree_about_politeness_on_purpose(self) -> None:
        """``is_explicit`` and ``asks_for_the_action`` have opposite defaults.

        ``is_explicit`` treats "can you" as a hedge, which is right for its job:
        deciding whether a CONTEXTUAL write may skip its confirmation card, where being
        cautious costs one extra tap. ``asks_for_the_action`` decides whether the write
        is reachable at all, where being cautious costs the answer. The divergence
        looks like an inconsistency, so it is pinned here rather than left to be
        tidied away by someone unifying the two lists.
        """
        message = "can you pause my bitcoin alert"
        self.assertFalse(is_explicit(message))
        self.assertTrue(asks_for_the_action(message))
        self.assertRoutes(message, "crypto.alerts.pause")


class NegativeTests(_RoutingAssertions):
    """Nothing is a valid answer, and the common one."""

    NOTHING = (
        "hey there", "good morning", "thanks, that helped", "how are you today",
        "what are you", "who built you", "can you write me a poem about the sea",
        "book me a flight to lisbon", "what is the weather tomorrow",
        "what is bitcoin trading at on coinbase right now",
    )

    def test_small_talk_and_the_unservable_route_to_nothing(self) -> None:
        for message in self.NOTHING:
            with self.subTest(message):
                self.assertEqual("", _matched(message))

    def test_an_empty_message_routes_to_nothing(self) -> None:
        for message in ("", "   ", "!!!", "?"):
            with self.subTest(repr(message)):
                self.assertEqual("", _matched(message))


class GuardShapeTests(unittest.TestCase):
    """Properties of the guard itself, independent of any one phrasing."""

    def test_the_guard_is_read_only_on_reads(self) -> None:
        """Hedging a read changes nothing. There is no harm to protect against.

        Worth asserting because the cheap implementation — refusing every hedged
        message — would look correct against a suite of write cases while quietly
        halving what the system answers.
        """
        for message in ("should i check my alerts", "what happens when i view my alerts",
                        "i wonder what alerts i have"):
            with self.subTest(message):
                landed = _matched(message)
                self.assertTrue(landed, f"{message!r} should still reach a read")
                self.assertFalse(REGISTRY[landed].is_write)

    def test_every_write_in_the_registry_is_refusable(self) -> None:
        """A negated form of each write's own first intent phrase must not reach it.

        Derived from the registry rather than listed by hand, so a write added
        tomorrow is covered today. This is the one place in this file where the test
        data comes from the thing under test, and it is safe here because the assertion
        is not "the phrase matches" — which would be circular — but "the phrase stops
        matching once negated", which the registry has no say in.
        """
        for capability_id, spec in REGISTRY.items():
            if not spec.is_write or not spec.intents:
                continue
            phrase = spec.intents[0]
            with self.subTest(capability_id):
                self.assertEqual(capability_id, _matched(phrase),
                                 "the intent phrase itself should route")
                self.assertNotEqual(capability_id, _matched(f"do not {phrase}"),
                                    "negating it should not")


if __name__ == "__main__":
    unittest.main(verbosity=2)
