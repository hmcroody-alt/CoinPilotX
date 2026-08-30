"""No model-generated execution claims on the conversational path.

The failure this file exists for is a screenshot: UNDX replied to "like my most recent
post" with a bracketed stage direction and a statement that the action was done, having
called nothing. Tracing it produced a surprise worth stating plainly, because it is what
determines where a guard belongs.

The agent path is not where it came from. When
:func:`services.undx_agent_runtime.handle` returns ``handled=True``, the provider is
short-circuited entirely and the person reads the receipt's own explanation, which the
runtime has already checked against verification state. No language model touches it.

The reply came from the *other* branch — ``handled=False``, the ordinary conversational
fallback, where a provider writes the answer. And on that branch no capability was
resolved, no confirmation was raised and no gateway was called, so **no receipt can
exist**. That is what makes the check here deterministic instead of a guess about
whether the model happened to be telling the truth: the absence of authority is
structural, so every execution claim in that text is false without needing to be
compared against anything.

Two things are therefore tested, in the two places they live:

* the detectors — :func:`~services.undx_response_intelligence.execution_narration` and
  :func:`~services.undx_response_intelligence.completion_claim` — against the exact
  phrases the mission forbids, and against honest prose that must survive them,
* the seam — :func:`services.pulse_ai_service._strip_unsupported_execution_claims` —
  which is the last thing to touch a reply before it is stored and returned.

The false-positive half is not padding. A guard that eats "you can like a post by
tapping the heart" has broken the help UNDX exists to give, and would be removed within
a week, which is a worse outcome than the bug.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.undx_agent import bootstrap as _bootstrap  # noqa: F401
from services import pulse_ai_service
from services import undx_capability_lifecycle as lifecycle
from services import undx_response_intelligence as ri


#: Every phrase the mission names verbatim, plus the variants a model actually produces
#: around them. A claim is refused if *either* detector sees it; which one fires is an
#: implementation detail (in-progress versus completed), and pinning that per phrase
#: would make the test brittle about a distinction the caller does not care about.
FORBIDDEN = (
    # The five named in the directive.
    "[Executing action...]",
    "I've completed that.",
    "I just changed it.",
    "Done.",
    "I successfully liked the post.",
    # The same claims in the shapes a provider reaches for unprompted.
    "Sure! [Executing action: like_post] The post has been liked.",
    "One moment while I like that for you.",
    "I am now executing the request.",
    "I'm deleting that reel for you.",
    "Let me go ahead and do that.",
    "I'll go ahead and do that right now.",
    "Working on that right now.",
    "Calling the gateway now.",
    "Action in progress.",
    "I have already unfollowed them.",
    "Push notifications are now off.",
    "Your post has been liked.",
    "I've done that for you.",
    "All set!",
    "I turned it off.",
)

#: Prose that must survive untouched. Four kinds, and each was a live risk rather than a
#: hypothetical: second-person help, honest negation, a read describing state it
#: observed, and the runtime's own degradation and roadmap sentences.
PERMITTED = (
    "You can like a post by tapping the heart underneath it.",
    "To delete a reel, open it and tap the menu in the corner.",
    "I could not read it back to confirm it.",
    "I have not sent anything yet.",
    "I never deleted anything on your account.",
    "I followed up on your earlier question about reels.",
    "Your BTC alert is paused.",
    "Push notifications are something you can change in Settings.",
    "Would you like me to like that post? I can prepare it for your confirmation.",
    "I can prepare that action but I need your confirmation before it is executed.",
    "That capability is part of the PulseSoc roadmap and is not currently available.",
    "I am not able to do that yet.",
    "Deleting a post is permanent, so it is worth being sure first.",
)


def _claims(text: str) -> str:
    """Both detectors at the limits the conversational seam uses."""
    return (ri.execution_narration(text)
            or ri.completion_claim(text, ri.MAX_SCANNED_REPLY_CHARS))


class DetectorTests(unittest.TestCase):
    def test_every_forbidden_phrase_is_recognised(self):
        for phrase in FORBIDDEN:
            with self.subTest(phrase=phrase):
                self.assertTrue(_claims(phrase), f"unsupported claim went unrecognised: {phrase!r}")

    def test_honest_prose_is_not_recognised_as_a_claim(self):
        for phrase in PERMITTED:
            with self.subTest(phrase=phrase):
                self.assertFalse(_claims(phrase), f"honest sentence caught: {phrase!r} -> {_claims(phrase)}")

    def test_a_claim_buried_in_a_long_reply_is_still_found(self):
        """The reason :data:`~services.undx_response_intelligence.MAX_SCANNED_REPLY_CHARS`
        exists. ``completion_claim`` bounds its input at ``MAX_EXPLANATION_CHARS`` (1200),
        which is right for a composed explanation and wrong for provider output the
        transport allows up to 6000 characters: a model could have put the narration in a
        fourth paragraph and walked straight past a guard that only read the first."""
        filler = "Here is some general background about how the feed ranks posts. " * 40
        self.assertGreater(len(filler), ri.MAX_EXPLANATION_CHARS)
        buried = filler + "Anyway, I've deleted it for you."
        # The default cap is unchanged, so the original caller behaves exactly as before.
        self.assertEqual(ri.completion_claim(buried), "")
        self.assertTrue(_claims(buried))
        # And the seam, which is what actually protects the person, catches it.
        self.assertTrue(pulse_ai_service._strip_unsupported_execution_claims(buried)[1])

    def test_the_detectors_answer_the_two_different_questions(self):
        """In-progress and completed are separate claims and stay separately detectable.

        Folding them together would be tidier and would lose the distinction the runtime
        needs elsewhere: a completion claim is checked against whether an action
        *verified*, an execution narration against whether one was *attempted at all*.
        """
        self.assertTrue(ri.execution_narration("[Executing action...]"))
        self.assertFalse(ri.completion_claim("[Executing action...]"),
                         "an in-progress narration is not a completion claim")
        self.assertTrue(ri.completion_claim("I have already unfollowed them."))

    def test_empty_and_junk_input_claims_nothing(self):
        for value in ("", "   ", "\n\n"):
            with self.subTest(value=value):
                self.assertEqual(ri.execution_narration(value), "")
                self.assertEqual(ri.completion_claim(value), "")


class ReplyGuardTests(unittest.TestCase):
    """The seam, :func:`services.pulse_ai_service._strip_unsupported_execution_claims`."""

    def _guard(self, reply):
        return pulse_ai_service._strip_unsupported_execution_claims(reply)

    def test_a_clean_reply_is_returned_byte_identical(self):
        """The overwhelmingly common case. A guard that reformats every reply on its way
        past is a guard that will be blamed for unrelated wording changes."""
        for phrase in PERMITTED:
            with self.subTest(phrase=phrase):
                text, removed = self._guard(phrase)
                self.assertEqual(text, phrase)
                self.assertEqual(removed, "")

    def test_a_narrated_execution_replaces_the_whole_reply(self):
        text, removed = self._guard("Sure! [Executing action: like_post] The post has been liked.")
        self.assertEqual(text, pulse_ai_service.UNDX_NO_ACTION_TAKEN_REPLY)
        self.assertTrue(removed.startswith("execution_narration:"), removed)
        # The point of replacing rather than redacting: no fragment of the original
        # survives to keep implying the action landed.
        self.assertNotIn("Executing", text)
        self.assertNotIn("has been liked", text)

    def test_the_replacement_itself_claims_nothing(self):
        """Otherwise the guard feeds its own output back into its own detector, and the
        fix would be a slower version of the bug."""
        for replacement in (pulse_ai_service.UNDX_NO_ACTION_TAKEN_REPLY,
                            pulse_ai_service.UNDX_NO_KNOWN_LIMITATION_REPLY):
            with self.subTest(replacement=replacement):
                self.assertEqual(_claims(replacement), "")
                self.assertEqual(self._guard(replacement), (replacement, ""))

    def test_every_forbidden_phrase_is_removed_at_the_seam(self):
        for phrase in FORBIDDEN:
            with self.subTest(phrase=phrase):
                text, removed = self._guard(phrase)
                self.assertTrue(removed, f"reached the person unchanged: {phrase!r}")
                self.assertEqual(text, pulse_ai_service.UNDX_NO_ACTION_TAKEN_REPLY)

    def test_an_invented_limitation_is_removed_when_nothing_is_limited(self):
        """The second half of the screenshot, and a different lie from the first.

        The model did not merely claim to have acted; it also told the person that
        finishing the job "still requires the current PulseSoc interface" — a sentence it
        was handed in its own system prompt by
        :func:`~services.undx_capability_lifecycle.capability_lifecycle_block`. That leak
        is closed at the source, but the phrase is also in provider training data, so the
        deterministic check stays.
        """
        reply = ("I can identify the post, but final execution still requires the "
                 "current PulseSoc interface.")
        counts = {status: 0 for status in lifecycle.CapabilityStatus.ALL}
        counts[lifecycle.CapabilityStatus.AVAILABLE] = 40
        with mock.patch.object(lifecycle, "lifecycle_counts", return_value=counts):
            text, removed = self._guard(reply)
        self.assertEqual(removed, "unsupported_limitation_claim")
        self.assertEqual(text, pulse_ai_service.UNDX_NO_KNOWN_LIMITATION_REPLY)

    def test_a_real_limitation_is_left_alone(self):
        """The half that keeps this from being a gag order. When policy really has
        suspended writes the sentence is true, and suppressing it would tell someone an
        action is coming that policy has actually stopped."""
        reply = ("I can identify the post, but final execution still requires the "
                 "current PulseSoc interface.")
        counts = {status: 0 for status in lifecycle.CapabilityStatus.ALL}
        counts[lifecycle.CapabilityStatus.LIMITED] = 120
        with mock.patch.object(lifecycle, "lifecycle_counts", return_value=counts):
            text, removed = self._guard(reply)
        self.assertEqual(removed, "")
        self.assertEqual(text, reply)

    def test_an_unreadable_policy_state_keeps_the_limitation(self):
        """Fail toward the cautious answer, which here is *not* the silent one. If the
        lifecycle read raises we do not know whether the limitation is real, and telling
        someone an action is available when policy has suspended it is the worse of the
        two errors."""
        reply = "That still requires the current PulseSoc interface."
        with mock.patch.object(lifecycle, "lifecycle_counts", side_effect=RuntimeError("boom")):
            text, removed = self._guard(reply)
        self.assertEqual(text, reply)
        self.assertEqual(removed, "")

    def test_execution_claims_are_refused_before_limitation_claims(self):
        """A reply that does both is an execution claim first: it is the more damaging
        of the two and its replacement is the more specific."""
        reply = ("I've liked it for you, though final execution still requires the "
                 "current PulseSoc interface.")
        text, removed = self._guard(reply)
        self.assertEqual(text, pulse_ai_service.UNDX_NO_ACTION_TAKEN_REPLY)
        self.assertTrue(removed.startswith(("execution_narration:", "completion_claim:")), removed)

    def test_the_guard_survives_the_response_layer_being_unavailable(self):
        """A broken import must not blank every conversational reply in production.

        This mirrors the runtime's own metacognitive self-check, which logs and lets the
        text through rather than replacing it. The trade is deliberate and worth naming:
        a guard that fails open leaks claims during an outage, and a guard that fails
        closed turns one broken import into a silent assistant.
        """
        with mock.patch.dict("sys.modules", {"services.undx_response_intelligence": None}):
            text, removed = self._guard("I've deleted it.")
        self.assertEqual(text, "I've deleted it.")
        self.assertEqual(removed, "")


class SeamWiringTests(unittest.TestCase):
    def test_the_guard_runs_after_identity_enforcement(self):
        """Ordering matters and is asserted rather than left to reading order.

        ``_enforce_undx_reply_identity`` rewrites provider text, so a guard placed before
        it would inspect a string the person never sees. Both are in the same statement
        pair in ``send_message``; this pins the direction.
        """
        import inspect

        source = inspect.getsource(pulse_ai_service.send_message)
        identity = source.index("reply = _enforce_undx_reply_identity(result.get(\"reply\")")
        guard = source.index("_strip_unsupported_execution_claims(reply)")
        self.assertLess(identity, guard)
        # And before persistence, so a stripped claim is never the stored assistant text.
        self.assertLess(guard, source.index("assistant_id = _insert_message", guard - 400))


class EndToEndTests(unittest.TestCase):
    """The whole path, with only the provider replaced.

    The tests above prove the detectors and the seam in isolation, which is not the same
    claim as "a hallucinating provider cannot reach the person". This drives the real
    :func:`services.pulse_ai_service.send_message` against a provider that returns the
    screenshot text verbatim, and looks at what the caller gets *and* at what was
    written to ``pulse_ai_messages`` — because a reply that is cleaned on the way out but
    stored dirty comes back on the next page load.
    """

    HALLUCINATION = ("[Executing action...] I've liked your most recent post. "
                     "Anything else I can help with?")

    def setUp(self) -> None:
        from tests.undx_agent.harness import AgentFixture, OWNER_ID

        self.owner_id = OWNER_ID
        self.fx = AgentFixture().start()
        _bootstrap.stub_bot(pulse_ai_service)
        pulse_ai_service.ensure_schema(self.fx.cur, self.fx.conn)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    #: Deliberately a question rather than the screenshot's "like my most recent post".
    #: That sentence is *handled* by the agent runtime, which short-circuits the provider
    #: entirely — the branch under test would never run. The hallucination being
    #: reproduced happens when the runtime declines and the turn falls through to a
    #: model, so the message has to be one the runtime declines.
    CONVERSATIONAL = "how does the feed decide what to show me?"

    def _send(self, provider_reply: str) -> dict:
        from services import pulse_ai_provider_router, pulse_ai_web_search

        with mock.patch.object(
            pulse_ai_provider_router, "generate_response",
            return_value={"ok": True, "reply": provider_reply, "provider": "openai",
                          "model": "gpt-test", "latency_ms": 12, "attempts": []},
        ), mock.patch.object(
            # Otherwise the turn reaches for live web search and the test spends thirty
            # seconds failing to leave the sandbox.
            pulse_ai_web_search, "search", return_value={},
        ):
            return pulse_ai_service.send_message(
                self.owner_id, {"message": self.CONVERSATIONAL})

    def test_the_message_under_test_really_does_reach_the_provider(self):
        """The premise. Without it the three tests below could all pass because the
        agent handled the turn and no model was ever consulted."""
        from services import pulse_ai_provider_router, pulse_ai_web_search

        with mock.patch.object(
            pulse_ai_provider_router, "generate_response",
            return_value={"ok": True, "reply": "ranking is based on recency and affinity",
                          "provider": "openai", "model": "gpt-test", "latency_ms": 1,
                          "attempts": []},
        ) as generate, mock.patch.object(pulse_ai_web_search, "search", return_value={}):
            pulse_ai_service.send_message(self.owner_id, {"message": self.CONVERSATIONAL})
        self.assertTrue(generate.called, "the agent handled this turn; pick another message")

    def _stored_reply(self) -> str:
        self.fx.cur.execute(
            "SELECT body FROM pulse_ai_messages WHERE role='assistant' ORDER BY id DESC LIMIT 1")
        row = self.fx.cur.fetchone()
        return row["body"] if row else ""

    def test_a_hallucinated_execution_never_reaches_the_person(self):
        result = self._send(self.HALLUCINATION)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["reply"], pulse_ai_service.UNDX_NO_ACTION_TAKEN_REPLY)
        self.assertNotIn("Executing action", result["reply"])
        self.assertNotIn("I've liked", result["reply"])

    def test_the_hallucination_is_not_stored_either(self):
        """Otherwise it returns on the next page load, and the transcript — which is what
        a person quotes back when they complain — still says the action happened."""
        self._send(self.HALLUCINATION)
        stored = self._stored_reply()
        self.assertEqual(stored, pulse_ai_service.UNDX_NO_ACTION_TAKEN_REPLY)
        self.assertNotIn("Executing", stored)

    def test_an_honest_reply_passes_through_unchanged(self):
        """The guard must be invisible on the ordinary turn, which is nearly all of them."""
        honest = "You can like a post by tapping the heart underneath it."
        result = self._send(honest)
        self.assertEqual(result["reply"], honest)
        self.assertEqual(self._stored_reply(), honest)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
