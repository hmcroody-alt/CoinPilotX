"""What a sentence has to yield before a capability can run, and what it must not.

Routing is settled: the benchmark proves 4,000 paraphrases reach the right capability.
Reaching it is not the same as running it. Forty-seven of the eighty capabilities
declare a required field with no default, and until that field is filled the turn ends
at schema validation — the person's message was understood, matched, authorised, and
then refused for a reason expressed in the vocabulary of the schema.

Measured across the 800-body corpus, 147 messages routed correctly and arrived with a
required field empty. That is the gap these tests are written against. It divides in
two, and the division is the design:

  * Sentences that *do* contain the value and were not read. "Show post performance
    for 9" names post 9; the extractor required the number to be adjacent to the noun,
    so the more precisely someone named the capability the further they pushed the id
    out of reach. These are defects, and the tests for them assert a filled field.

  * Sentences that genuinely do not contain it. "Update alert 1 with a new threshold"
    names no threshold, and no amount of parsing will find one. These are not defects
    — but the *reply* was, because a schema error naming ``threshold`` is unanswerable.
    The tests for them assert a question the person could actually answer.

The second half is the one that needs guarding. It would be easy to make the first
number pass by widening extraction until something is always found, and a suite made
only of positive cases would go green while the system started guessing which alert to
retarget. So the refusals are asserted as firmly as the extractions.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_agent_runtime import (  # noqa: E402
    _CHOICE_LABELS, Reference, missing_required, resolve_arguments,
    resolve_query_argument, resolve_threshold, resource_reference)
from services.undx_capability_registry import REGISTRY  # noqa: E402


class _ResolutionCase(unittest.TestCase):
    """Resolution without a database.

    Only the alert-reference branch reads the account, and what it reads is not what
    these tests are about. Replacing it with a fixed answer is what lets every other
    branch be asserted on the sentence alone — which is the property that was missing
    while this code lived inside ``handle``.
    """

    ALERT_ID = 5

    def setUp(self) -> None:
        from services import undx_agent_runtime

        self.runtime = undx_agent_runtime
        self._original = undx_agent_runtime.resolve_alert_reference
        undx_agent_runtime.resolve_alert_reference = (
            lambda user_id, text, explicit_id=None: Reference(1, int(explicit_id or self.ALERT_ID)))

    def tearDown(self) -> None:
        self.runtime.resolve_alert_reference = self._original

    def resolve(self, capability_id: str, text: str, **arguments):
        return resolve_arguments(7, REGISTRY[capability_id], text, dict(arguments))

    def assertResolves(self, capability_id: str, text: str, **expected) -> None:
        resolution = self.resolve(capability_id, text)
        self.assertEqual((), resolution.missing, f"{text!r} left a field empty")
        for name, value in expected.items():
            self.assertEqual(value, resolution.arguments.get(name), f"{text!r}: {name}")

    def assertAsks(self, capability_id: str, text: str, *fields: str) -> str:
        """Missing, and missing in a way the person can do something about."""
        resolution = self.resolve(capability_id, text)
        self.assertEqual(set(fields), set(resolution.missing), repr(text))
        self.assertIsNotNone(resolution.unresolved, f"{text!r} produced no question")
        detail = resolution.unresolved.detail
        self.assertTrue(detail.strip(), f"{text!r} produced an empty question")
        # A question that names the schema field is the failure this replaced.
        self.assertNotIn("required", detail.lower())
        return detail


class ReferenceFormTests(_ResolutionCase):
    """The three ways a person points at a numbered thing."""

    def test_the_number_next_to_the_noun(self) -> None:
        for text in ("show post 9", "show post #9", "show post id 9", "post number 9"):
            with self.subTest(text):
                self.assertEqual(9, resource_reference(text, ("post",)))

    def test_the_number_separated_by_the_capabilitys_own_words(self) -> None:
        """This is the family the old extractor missed, and it missed it backwards.

        "Show post performance for 9" is *more* precise than "show post 9" — it names
        the capability as well as the resource — and precision was what pushed the
        number out of the adjacency window.
        """
        self.assertEqual(9, resource_reference("show post performance for 9", ("post",)))
        self.assertEqual(4, resource_reference("summarize reel comments on 4", ("reel",)))
        self.assertEqual(9, resource_reference("status viewer summary for 9", ("status",)))
        self.assertEqual(9, resource_reference("marketplace order status for 9", ("order",)))

    def test_the_only_number_in_a_sentence_that_names_the_thing(self) -> None:
        self.assertEqual(9, resource_reference("explain live session 9",
                                               ("live", "live session")))
        self.assertEqual(5, resource_reference("summarize chat 5, it is long",
                                               ("conversation", "chat")))

    def test_a_second_number_turns_the_permissive_rule_off(self) -> None:
        """Where the reading is genuinely uncertain, two numbers appear. Two is the off switch.

        Asserted because the last rule is the one that would quietly start guessing if
        the guard were removed, and its failure mode — retargeting an alert to its own
        id — is silent and destructive.
        """
        self.assertEqual(3, resource_reference("change alert 3 to trigger at 95000",
                                               ("alert",)))
        self.assertEqual(0, resource_reference("i have 2 and 9 to look at", ("order",)))

    def test_a_number_with_no_noun_is_not_a_reference(self) -> None:
        self.assertEqual(0, resource_reference("show me 9", ("post",)))
        self.assertEqual(0, resource_reference("nothing numeric here", ("post",)))

    def test_synonyms_count(self) -> None:
        """"Chat 5" and "conversation 5" are the same sentence to the person saying it."""
        for text in ("summarize chat 5", "summarize thread 5", "summarize conversation 5"):
            with self.subTest(text):
                self.assertEqual(5, resource_reference(
                    text, ("conversation", "chat", "thread", "dm")))


class ThresholdTests(_ResolutionCase):
    """Prices, as people write them."""

    def test_suffixed_magnitudes_expand(self) -> None:
        self.assertEqual(100_000.0, resolve_threshold("goes over 100k"))
        self.assertEqual(1_500_000.0, resolve_threshold("above 1.5m"))
        self.assertEqual(2000.0, resolve_threshold("drops under 2000"))

    def test_the_last_number_wins(self) -> None:
        """Identifier first, value second — the order every corpus phrasing uses."""
        self.assertEqual(95000.0, resolve_threshold("change alert 3 to trigger at 95000"))

    def test_a_sentence_with_no_number_yields_nothing(self) -> None:
        self.assertIsNone(resolve_threshold("make it stricter"))
        self.assertIsNone(resolve_threshold(""))


class AlertWriteTests(_ResolutionCase):
    """Creating and retargeting alerts — three fields that nothing used to fill."""

    def test_a_complete_sentence_fills_all_three(self) -> None:
        self.assertResolves("crypto.alerts.create", "alert me when bitcoin goes over 100k",
                            symbol="BTC", condition="above", threshold=100_000.0)
        self.assertResolves("crypto.alerts.create", "notify me when ether drops under 2000",
                            symbol="ETH", condition="below", threshold=2000.0)

    def test_the_identifier_is_not_the_threshold(self) -> None:
        """"Change alert 3 to 95000" must not set the threshold to 3.

        The bug this prevents is not hypothetical arithmetic: strip the alert id and one
        number is left, so the rule is simple; leave it in and the first number in the
        sentence is an alert id being read as a price.
        """
        resolution = self.resolve("crypto.alerts.update", "change alert 3 to trigger at 95000")
        self.assertEqual(95000.0, resolution.arguments["threshold"])

    def test_an_unstated_threshold_is_asked_for_not_invented(self) -> None:
        """The whole point of the fix, stated as its own test.

        "Update alert 1 with a new threshold" is a perfectly ordinary thing to say and
        contains no threshold. Filling one would be inventing an instruction; the alert
        would then fire at a price nobody chose.
        """
        detail = self.assertAsks("crypto.alerts.update",
                                 "update alert 1 with a new threshold", "threshold")
        self.assertIn("price", detail.lower())

    def test_an_unstated_direction_is_asked_for(self) -> None:
        """"At 300" does not say above or below, and the two are opposite alerts."""
        self.assertAsks("crypto.alerts.create", "create alert for solana at 300", "condition")

    def test_two_coins_in_one_sentence_resolve_to_neither(self) -> None:
        """Ambiguity is not resolved by picking the first one mentioned."""
        resolution = self.resolve("crypto.alerts.create",
                                  "alert me when bitcoin or ethereum goes over 100k")
        self.assertIn("symbol", resolution.missing)


class QueryTests(_ResolutionCase):
    """What someone is searching for, separated from the fact that they are searching."""

    def test_a_preposition_still_wins_when_there_is_one(self) -> None:
        self.assertEqual("the market crash",
                         resolve_query_argument("find content about the market crash"))

    def test_a_search_with_no_preposition_still_has_a_query(self) -> None:
        """The rule this replaces required "about", "for" or "named" and filled nothing
        otherwise. "Find people who work in crypto" is not ambiguous; it just has no
        preposition."""
        self.assertEqual("who work in crypto",
                         resolve_query_argument("find people who work in crypto"))
        self.assertEqual("that is upbeat",
                         resolve_query_argument("search music that is upbeat"))

    def test_a_search_naming_only_the_thing_searched_yields_nothing(self) -> None:
        """"Search groups please" says where to look and not what for.

        Returning "" here rather than "groups" is what stops the system answering with
        results that are an artefact of the parse.
        """
        self.assertEqual("", resolve_query_argument("search groups please"))
        self.assertEqual("", resolve_query_argument("search live sessions please"))

    def test_an_optional_query_field_is_left_empty_rather_than_invented(self) -> None:
        """"Find my saved posts" is a complete instruction, not an empty search.

        ``saved.items.list`` defaults ``query`` to "", meaning the whole library. The
        aggressive rule turned this sentence into a search for "saved posts", which
        filtered the library to nothing and reported an empty Saved folder to someone
        who has one. Found by breaking three existing tests, kept as a test of its own.
        """
        self.assertResolves("saved.items.list", "Find my saved posts.", query=None)
        self.assertResolves("saved.items.list", "Show my saved items.", query=None)

    def test_a_stated_term_is_read(self) -> None:
        self.assertResolves("search.global", "global search please, term is ethereum",
                            query="ethereum")


class DeicticReferenceTests(_ResolutionCase):
    """"This post" names something the runtime cannot see, and must say so.

    These are the cases the trusted native context envelope is meant to close: the app
    knows which post is open, the runtime does not. Until it does, the honest answer is
    a question. Asserted now so that the day the envelope lands, the change is visible
    as these tests being rewritten rather than as behaviour drifting.
    """

    def test_a_write_on_an_unnamed_post_asks_rather_than_guesses(self) -> None:
        for capability_id, text in (
            ("feed.posts.like", "like this post for me"),
            ("feed.posts.unlike", "unlike this post of mine"),
            ("saved.post.set", "save this post so i can find it"),
        ):
            with self.subTest(capability_id):
                detail = self.assertAsks(capability_id, text, "post_id")
                self.assertIn("which post", detail.lower())

    def test_a_read_on_an_unnamed_thing_asks_too(self) -> None:
        self.assertAsks("messages.suggest", "suggest a response to that last message",
                        "conversation_id")
        self.assertAsks("notifications.explain",
                        "why did i get this notification about a follow", "notification_id")


class UnsupportedValueTests(_ResolutionCase):
    """A value the product does not have is a different answer from a value not given."""

    def test_an_unsupported_enum_names_what_is_supported(self) -> None:
        detail = self.assertAsks("profile.preferences.update",
                                 "set my preferred language to german please",
                                 "preferred_language")
        self.assertIn("English", detail)
        self.assertIn("Spanish", detail)
        self.assertIn("French", detail)

    def test_the_supported_list_comes_from_the_registry(self) -> None:
        """So the sentence stays true when the list changes.

        A hand-written "English, Spanish and French" would survive the addition of a
        fourth language and quietly start lying to the person who asked for it.
        """
        detail = self.assertAsks("profile.preferences.update",
                                 "set my preferred language to japanese",
                                 "preferred_language")
        field = next(item for item in REGISTRY["profile.preferences.update"].fields
                     if item.name == "preferred_language")
        # Every choice the registry declares must appear, under whichever name the
        # sentence uses for it. Derived from the registry, and not circular: the
        # registry has no say in whether the sentence mentions them.
        for choice in field.choices:
            with self.subTest(choice):
                self.assertTrue(
                    choice in detail or _CHOICE_LABELS.get(choice, choice) in detail,
                    f"{choice!r} is offered by the registry and missing from {detail!r}")
        self.assertNotIn("japanese", detail.lower())


class ResolutionShapeTests(_ResolutionCase):
    """Properties of the resolver itself."""

    def test_a_supplied_argument_is_never_overwritten(self) -> None:
        """The planner is more specific than a regex, and the gateway validates either way."""
        resolution = self.resolve("feed.posts.get", "show post 9", post_id=41)
        self.assertEqual(41, resolution.arguments["post_id"])

    def test_missing_required_ignores_fields_that_have_defaults(self) -> None:
        """A message that says nothing about ``limit`` is not an incomplete message."""
        spec = REGISTRY["search.global"]
        self.assertEqual(("query",), missing_required(spec, {}))
        self.assertEqual((), missing_required(spec, {"query": "ethereum"}))

    def test_every_capability_resolves_without_raising(self) -> None:
        """Including on sentences that have nothing to do with them.

        Resolution runs after routing, so it only ever sees plausible messages — but it
        runs before validation, so anything it raises becomes a 500 rather than a
        refusal. Cheap to assert, and the assertion is the thing that stays true when
        someone adds a regex with an unescaped noun in it.
        """
        for capability_id in REGISTRY:
            for text in ("", "   ", "!!!", "do the thing", "post reel status 1 2 3 conversation",
                         "a" * 400):
                with self.subTest(capability_id=capability_id, text=text[:20]):
                    resolution = self.resolve(capability_id, text)
                    self.assertIsInstance(resolution.arguments, dict)

    def test_an_unfillable_field_always_carries_a_question(self) -> None:
        """The invariant the benchmark check rests on.

        Every capability, fed a sentence naming it and nothing else. Whatever cannot be
        filled must arrive as something a person can answer — never as an empty string,
        which would reach the app as a blank prompt.
        """
        for capability_id, spec in REGISTRY.items():
            with self.subTest(capability_id):
                resolution = self.resolve(capability_id, spec.intents[0] if spec.intents else "go")
                if resolution.missing:
                    self.assertIsNotNone(resolution.unresolved)
                    self.assertTrue(resolution.unresolved.detail.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
