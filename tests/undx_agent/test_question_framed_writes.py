"""A question is never answered by performing the thing it asks about.

The property under test is one sentence: if the person framed a request as a question
about a subject, or as a report that something is broken, or as a scope to be tidied,
then UNDX does not satisfy it by running a capability that changes data. The person can
always have the write — by asking for it in the imperative, or by approving a
confirmation card — and this suite is about the gap between those two things and a
sentence that merely mentions the same noun.

**Why the coverage is exhaustive rather than illustrative.** The suppression this
locks in was already believed to exist before this suite was written. It lived in
:mod:`services.undx_brain.goals`, it was deliberate, it was documented, and it was
conditional on an unrelated fact — :func:`services.undx_agent_runtime.handle` honoured
an unsettled goal only when the goal also carried reads to offer, so wherever the
activated product areas happened to contain nothing readable the refusal evaporated and
the legacy matcher's write ran instead. Nothing about that was visible from reading the
goal layer, and a suite of hand-picked crypto-alert examples would have passed
throughout, because crypto alerts have plenty of reads. So the central test here does
not pick examples. It enumerates every registered write, crosses it with every
question frame, and drives each sentence through the real :func:`handle` against a real
database — 400-odd turns — asserting that not one of them reaches a write. A defect
that survives a list of examples cannot survive a product.

The suite runs the whole sweep twice, with the Brain flags on and off, because the flags
off configuration is the one most users are in and was the worse of the two: four
capabilities carry ``confirmation="never"``, so a mis-routed sentence reached the
executor with nothing in front of it at all.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OWNER_ID  # noqa: E402


BRAIN_ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_ATTENTION_ENABLED": "1",
    "UNDX_BRAIN_GOALS_ENABLED": "1",
    "UNDX_BRAIN_SELECTION_ENABLED": "1",
}
BRAIN_OFF = {key: "0" for key in BRAIN_ON}

#: One frame from each family, chosen so the three families are all represented and so
#: the two that were actually broken in the field are present verbatim. ``"break down "``
#: is here because it was missing from ``EXPLAIN_FRAMES`` entirely while ``"break it
#: down"`` and ``"break this down"`` were present — the concept looked covered and one of
#: its surface forms was not.
FRAMES = (
    "explain ", "why did ", "why was ", "help me understand ", "walk me through ",
    "break down ", "tell me about ", "make sense of ",
    "fix ", "what's wrong with ", "not working ",
    "clean up ", "manage my ", "help me with my ",
)


def _write_specs():
    from services import undx_capability_registry as registry

    return [registry.get(cid) for cid in registry.write_capability_ids()]


class AQuestionNeverPerformsTheThingItAsksAbout(unittest.TestCase):
    """The sweep. Every write, every frame, both flag configurations, real database."""

    def sweep(self, **flags) -> list[tuple[str, str]]:
        """Drive every frame-over-write sentence through ``handle`` and return breaches.

        A breach is any turn whose receipt or card names a capability the registry
        classifies as a write. Asserted against the registry rather than against a
        hand-kept list of dangerous ids, so a capability pack added next year is covered
        on the day it is registered and not on the day somebody remembers this file.
        """
        from services import undx_agent_runtime as runtime
        from services import undx_capability_registry as registry

        fixture = AgentFixture(**flags).start()
        breaches: list[tuple[str, str]] = []
        try:
            for spec in _write_specs():
                for intent in list(getattr(spec, "intents", ()) or ())[:4]:
                    for frame in FRAMES:
                        response = runtime.handle(
                            fixture.cur, user_id=OWNER_ID, text=frame + intent,
                            correlation_id="question-frame-sweep",
                        )
                        fixture.commit()
                        card = response.card or {}
                        reached = (getattr(response.receipt, "capability_id", "")
                                   or card.get("capability_id") or "")
                        touched = registry.get(reached) if reached else None
                        if touched is not None and touched.is_write:
                            breaches.append((frame + intent, reached))
        finally:
            fixture.stop()
        return breaches

    def test_no_question_frame_reaches_a_write_with_the_brain_on(self):
        breaches = self.sweep(**BRAIN_ON)
        self.assertEqual([], breaches[:8], f"{len(breaches)} question-framed writes")

    def test_no_question_frame_reaches_a_write_with_the_brain_off(self):
        """The configuration that was actually shipping, and the worse of the two.

        With the goal layer disabled the legacy matcher routes on vocabulary alone, and
        ``social.follow``, ``social.unfollow``, ``feed.posts.like`` and
        ``saved.post.set`` all carry ``confirmation="never"`` — so a mis-routed sentence
        did not stop at a card the person could decline, it reached the executor. This
        is why the refusal is not behind the Brain flags: gating it would have left the
        worst case ungoverned in the default configuration.
        """
        breaches = self.sweep(**BRAIN_OFF)
        self.assertEqual([], breaches[:8], f"{len(breaches)} question-framed writes")

    def test_the_sweep_is_actually_covering_something(self):
        """Guards the guard: a sweep of zero sentences would also report zero breaches.

        Without this, deleting ``intents`` from every spec — or a registry helper
        quietly returning an empty list — would turn both tests above green while
        removing all their meaning.
        """
        specs = _write_specs()
        self.assertGreaterEqual(len(specs), 12)
        sentences = sum(len(list(getattr(s, "intents", ()) or ())[:4]) for s in specs)
        self.assertGreaterEqual(sentences * len(FRAMES), 300)

    def test_the_writes_are_spread_across_the_product_and_not_one_domain(self):
        """The mission asked for more than crypto alerts, so the sweep is checked for it.

        The original suppression was written and tested against alerts, which is a
        domain rich in reads — and richness in reads was precisely the accident the
        refusal depended on. A sweep confined to it would have proved nothing about
        anywhere else.
        """
        prefixes = {spec.capability_id.split(".")[0] for spec in _write_specs()}
        self.assertGreaterEqual(len(prefixes), 6, sorted(prefixes))
        for expected in ("social", "saved", "reels", "notifications", "profile", "feed"):
            self.assertIn(expected, prefixes)


class TheRefusalSaysSomethingUseful(unittest.TestCase):
    """A refusal that stops the write and leaves the person stuck is half a fix."""

    def setUp(self) -> None:
        self.fx = AgentFixture(**BRAIN_ON).start()
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC")
        from services import undx_agent_runtime
        self.runtime = undx_agent_runtime

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, **kwargs):
        response = self.runtime.handle(
            self.fx.cur, user_id=OWNER_ID, text=text,
            correlation_id="question-frame-test", **kwargs,
        )
        self.fx.commit()
        return response

    def test_the_two_refusals_partition_rather_than_compete(self):
        """Which branch answers is decided by whether there was anything to suggest.

        Two different refusals exist and it matters that they do not overlap. When the
        goal layer has reads to offer, ``handle`` diverts and says "here is what I could
        look at" — the better sentence, and the one the person can act on. This guard
        runs on the complement: the turns where there was nothing to offer, and the
        turns where the goal layer was off. Asserted here because the first draft of the
        guard tried to offer reads too, and the offer was unreachable by construction —
        the case it was written for never arrives.
        """
        # "my followers" is what puts a read on offer. The utterance used to be
        # "tell me about unfollow user 42": the reads it carried arrived through
        # the term "user", and the sweep that gave every registered capability a
        # knowledge-map record pushed "user" over the common-term ceiling —
        # correctly, it is now in more than a quarter of records. The branch
        # under test is unchanged; the sentence that reaches it needed a cue
        # that is still informative.
        with_reads = (self.say("tell me about unfollow user 42 and my followers").card or {})
        self.assertEqual("clarification_required", with_reads.get("status"))
        self.assertTrue(with_reads.get("inspect_with"))
        self.assertNotIn("declined_capability_id", with_reads)

        without = (self.say("why did notify me when bitcoin goes above 90000").card or {})
        self.assertEqual("clarification_required", without.get("status"))
        self.assertEqual("crypto.alerts.create", without.get("declined_capability_id"))
        self.assertNotIn("inspect_with", without)

    def test_it_names_the_frame_that_made_it_decline(self):
        card = self.say("why did notify me when bitcoin goes above 90000").card or {}
        self.assertEqual("why did", card.get("question_frame"))
        self.assertEqual("crypto.alerts.create", card.get("declined_capability_id"))
        self.assertFalse(card.get("may_claim_done"))

    def test_it_says_what_it_declined_to_do_in_words(self):
        """The card is for the client; the sentence is for the person reading it."""
        reply = self.say("why did notify me when bitcoin goes above 90000").reply.lower()
        self.assertIn("asked about this rather than for it", reply)
        # It has to name the operation, or "I found something that matches" is a riddle.
        self.assertIn("alert", reply)
        # It has to say nothing happened, and how to get the thing done, or it is a
        # dead end that also leaves the person unsure whether it ran.
        self.assertIn("i have not done it", reply)
        self.assertIn("tell me plainly", reply)

    def test_the_refusal_is_not_a_claim_that_anything_happened(self):
        before = self.fx.alert_status(self.alert_id)
        self.say("tell me about pause alert %d" % self.alert_id)
        self.assertEqual(before, self.fx.alert_status(self.alert_id))

    def test_an_imperative_still_gets_the_write(self):
        """The whole point is the distinction, so the other side of it is asserted too.

        A guard that suppressed both would pass every test above while making the
        product useless, and that failure mode is easier to ship than it sounds: it
        looks identical from the safety side.
        """
        response = self.say("pause my bitcoin alert")
        self.assertEqual("verified_success", response.status)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id))

    def test_a_capability_the_caller_named_is_not_second_guessed(self):
        """A confirmation tap carries the original sentence, question frame and all.

        Re-reading the text at that point and refusing would make every confirmation for
        a question-framed request unapprovable — the person would be told they had not
        asked for the thing they had just approved.
        """
        response = self.say("tell me about pause alert %d" % self.alert_id,
                            capability_id="crypto.alerts.pause")
        self.assertEqual("verified_success", response.status)
        self.assertEqual("paused", self.fx.alert_status(self.alert_id))

    def test_a_read_is_never_suppressed_however_it_was_framed(self):
        """The guard is scoped to writes, and reads are the entire point of an account."""
        for text in ("explain my alerts", "why are my alerts not firing",
                     "tell me about my alerts", "break down my alerts"):
            with self.subTest(text=text):
                response = self.say(text)
                self.assertTrue(response.handled, text)
                self.assertNotEqual("clarification_required",
                                    (response.card or {}).get("status"), text)


class TheFrameReaderIsPureAndUngated(unittest.TestCase):
    """``asks_about_rather_than_for`` is a string test, and is relied on as one."""

    def reader(self):
        from services.undx_brain.goals import asks_about_rather_than_for
        return asks_about_rather_than_for

    def test_it_answers_the_same_way_with_every_brain_flag_off(self):
        """It is deliberately outside the flags, so this asserts the deliberate part.

        Everything else in the goals module returns ``ok=False`` when the Brain is
        disabled. This does not, because its only caller uses it to decline to mutate
        data, and a flag whose off position permits that is a defect with a switch
        attached rather than a rollout control.
        """
        reader = self.reader()
        saved = {key: os.environ.get(key) for key in
                 ("UNDX_BRAIN_ENABLED", "UNDX_BRAIN_GOALS_ENABLED")}
        try:
            for key in saved:
                os.environ[key] = "0"
            self.assertEqual("explain", reader("explain why my alert was deleted"))
            self.assertEqual("fix my", reader("fix my alerts"))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_it_returns_the_longest_frame_rather_than_the_first(self):
        """Otherwise the answer depends on the order somebody happened to append in."""
        self.assertEqual("what's wrong with",
                         self.reader()("so what's wrong with my alerts"))

    def test_an_instruction_carries_no_frame(self):
        for text in ("pause my bitcoin alert", "unfollow user 42", "show me my alerts",
                     "delete alert 3", "what is my alert threshold"):
            with self.subTest(text=text):
                self.assertEqual("", self.reader()(text))

    def test_the_transitive_break_down_is_read_as_a_frame(self):
        """The specific omission the sweep found.

        ``"break it down"`` and ``"break this down"`` were both present, so the concept
        looked covered; the form that names its subject directly was not, so "break down
        my notification settings" read as a bare instruction and routed to
        ``notifications.preference.update``.
        """
        self.assertEqual("break down",
                         self.reader()("break down my notification settings"))

    def test_it_survives_junk_without_raising(self):
        for value in (None, "", "   ", 12345, "x" * 10000):
            with self.subTest(value=repr(value)[:20]):
                self.assertIsInstance(self.reader()(value), str)


def _is_write(capability_id: str) -> bool:
    from services import undx_capability_registry as registry

    spec = registry.get(capability_id)
    return bool(spec is not None and spec.is_write)


if __name__ == "__main__":
    unittest.main()
