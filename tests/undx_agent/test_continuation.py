"""What happens to the answer, when the runtime has asked a question.

Batch 5 turned fifty-three schema errors into clarifying questions, which was the
right change and left the obvious thing undone. A question is only worth asking if
the answer can arrive, and the answer could not: ``match_capability`` returns nothing
for "9", for "95000", for "english", for "post 9" — correctly, because in an opening
message a lone number means nothing — so every reply fell through to a chat model with
no capability access. The system was asking questions into a channel it was not
listening on. That is the same defect Batch 5 fixed, displaced by one turn, and this
file is written against the fix.

The mechanism is a *continuation*: one row, minted when a question is asked, recovered
by account on the next message, and burned on use. It reuses the confirmation store,
which already provides expiry, single-use redemption, owner scoping and durability, and
that reuse is the reason a third of this file is about keeping the two kinds of row
apart. An approval says the person agreed to an action. A continuation says the runtime
asked them something. Sharing a table must not become sharing a meaning, and
``ContinuationIsNotAnApproval`` is where that is nailed down.

The rest divides into three properties, and the negatives carry as much weight as the
positives in each:

*It answers.* "Change my alert threshold" then "95000" must retarget the alert the
first message named — not an alert the second message happens to mention — and must
still stop at its confirmation card, because a continuation supplies a field and never
a permission.

*It declines.* A reply that is not an answer must leave the person exactly where they
would have been without a pending question. A permissive reader here would decide that
"good morning" is post 0, so most of ``AnswerShapeTests`` is about what returns
``None``.

*It does not linger.* The question is consulted only when the message routes nowhere,
burned on use rather than on success, and dropped outright when the person moves on.
Each of those is a separate way for a forgotten question to fire days later against a
sentence that was never a reply, and the second firing is the dangerous one, because by
then nobody remembers being asked.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap  # noqa: E402

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class _Fixture(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_runtime, undx_architecture

        self.rt = undx_agent_runtime
        self.arch = undx_architecture
        self.alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str, *, user_id: int = OWNER_ID):
        response = self.rt.handle(self.fx.cur, user_id=user_id, text=text)
        self.fx.commit()
        return response

    def pending(self, user_id: int = OWNER_ID) -> dict:
        return self.arch.pending_continuation(self.fx.cur, int(user_id))

    def approved_arguments(self) -> dict:
        """What the newest *approval* was minted for, read from the row.

        Deliberately read from the confirmation the turn produced rather than from
        anything the response says about itself. A card that agreed with itself about
        which alert it had chosen would prove nothing.
        """
        import json

        self.fx.cur.execute(
            "SELECT arguments_json FROM pulse_ai_confirmations WHERE user_id=? "
            "AND status='pending' AND action_id NOT LIKE ? ORDER BY id DESC LIMIT 1",
            (OWNER_ID, self.arch.CONTINUATION_PREFIX + "%"))
        row = self.fx.cur.fetchone()
        return json.loads(row["arguments_json"]) if row else {}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class StoreTests(_Fixture):
    """The row, on its own terms."""

    def mint(self, *, user_id: int = OWNER_ID, capability_id: str = "feed.posts.like",
             arguments: dict | None = None, missing=("post_id",), ttl: int = 180) -> str:
        return self.arch.create_continuation(
            self.fx.cur, user_id, capability_id=capability_id,
            arguments=arguments or {}, missing=missing, ttl_seconds=ttl)

    def test_what_was_asked_comes_back(self) -> None:
        self.mint(capability_id="crypto.alerts.update",
                  arguments={"alert_id": 41}, missing=("threshold",))
        pending = self.pending()
        self.assertEqual("crypto.alerts.update", pending["capability_id"])
        self.assertEqual(("threshold",), pending["missing"])
        # The partial work survives, which is the whole reason the row holds arguments
        # rather than just a field name. Without it the second turn would have to
        # re-derive the target from the reply, and "95000" names no alert.
        self.assertEqual({"alert_id": 41}, pending["arguments"])

    def test_a_question_is_spent_once(self) -> None:
        continuation_id = self.mint()
        self.assertTrue(self.arch.burn_continuation(self.fx.cur, OWNER_ID, continuation_id))
        self.assertFalse(self.arch.burn_continuation(self.fx.cur, OWNER_ID, continuation_id))
        self.assertEqual({}, self.pending())

    def test_only_the_owner_sees_it(self) -> None:
        """An account boundary, asserted rather than assumed.

        The lookup is by account instead of by token precisely so that no client has to
        hold a credential, which removes the thing that would otherwise be doing the
        scoping. What remains is the ``WHERE user_id=?``, so it is worth a test of its
        own — and the burn is checked too, since a foreign burn would be a way to
        cancel someone else's pending question.
        """
        continuation_id = self.mint()
        self.assertEqual({}, self.pending(OTHER_ID))
        self.assertFalse(self.arch.burn_continuation(self.fx.cur, OTHER_ID, continuation_id))
        self.assertTrue(self.pending(OWNER_ID))

    def test_an_expired_question_is_gone(self) -> None:
        self.mint()
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE status='pending'")
        self.assertEqual({}, self.pending())

    def test_the_newest_question_wins(self) -> None:
        """One outstanding question at a time, and it is the one on screen.

        Two live questions and an ambiguous "9" is a worse position than a forgotten
        question, because there is no honest way to choose. The runtime avoids getting
        here — it drops the old question before asking a new one — and the store
        settles it anyway, so the ordering does not depend on the caller being careful.
        """
        self.mint(capability_id="feed.posts.like", missing=("post_id",))
        self.mint(capability_id="crypto.alerts.update", arguments={"alert_id": 3},
                  missing=("threshold",))
        self.assertEqual("crypto.alerts.update", self.pending()["capability_id"])


class ContinuationIsNotAnApproval(_Fixture):
    """The two kinds of row share a table. They must not share an authority."""

    def test_a_question_cannot_be_redeemed_as_a_confirmation(self) -> None:
        """The escalation this design has to rule out.

        If a continuation could be consumed by the gateway's redemption path, then
        asking "which post?" would have handed out a grant to act on whatever post came
        back — a question minting a permission. The barrier is the namespaced
        ``action_id``: every redeeming caller states which action it believes it is
        executing, and ``consume_confirmation`` checks that binding before it burns
        anything, so a row named ``undx.continuation:feed.posts.like`` cannot satisfy a
        caller expecting ``feed.posts.like``.

        Asserted through the real ``consume_confirmation``, with the real token, under
        the exact expectation the gateway passes. A test that only compared the two
        strings would prove the prefix exists, not that it protects anything.
        """
        grant = self.arch.create_confirmation(
            self.fx.cur, OWNER_ID,
            {"action_id": self.arch.CONTINUATION_PREFIX + "feed.posts.like",
             "target_id": "post_id", "arguments": {}})
        token = grant["confirmation_token"]
        self.assertIsNone(self.arch.consume_confirmation(
            self.fx.cur, OWNER_ID, token,
            expect_action_id="feed.posts.like",
            expect_argument_hash=self.arch.argument_hash({})))
        # And the failed redemption did not destroy it, which is the property that
        # makes probing useless rather than merely unrewarding.
        self.assertTrue(self.pending())

    def test_the_prefix_is_not_a_prefix_of_any_capability(self) -> None:
        """Derived from the registry, so a capability added tomorrow is covered today.

        The namespace only separates the two kinds of row while no real capability id
        can collide with it. Checking the property rather than the spelling means a
        future capability named into the namespace fails here instead of silently
        becoming redeemable as a continuation.
        """
        from services.undx_capability_registry import REGISTRY

        for capability_id in REGISTRY:
            with self.subTest(capability_id):
                self.assertFalse(capability_id.startswith(self.arch.CONTINUATION_PREFIX))
                self.assertNotEqual(
                    capability_id,
                    self.arch.CONTINUATION_PREFIX + capability_id)


# ---------------------------------------------------------------------------
# Reading the reply
# ---------------------------------------------------------------------------


class AnswerShapeTests(unittest.TestCase):
    """``answer_for_field`` — and mostly, what it refuses.

    Extraction over a reply is allowed to be far more permissive than extraction over
    an opening message, because the question has already supplied the subject. That
    permission is exactly what makes the negative cases the important ones: the failure
    mode is not "the answer was missed", it is "something that was not an answer was
    read as one".
    """

    @classmethod
    def setUpClass(cls) -> None:
        from services import undx_agent_runtime
        from services.undx_capability_registry import REGISTRY

        cls.rt = undx_agent_runtime
        cls.REGISTRY = REGISTRY

    def field(self, capability_id: str, name: str):
        return next(item for item in self.REGISTRY[capability_id].fields
                    if item.name == name)

    def read(self, capability_id: str, name: str, text: str):
        return self.rt.answer_for_field(self.field(capability_id, name), text)

    def test_a_bare_number_answers_an_id(self) -> None:
        for reply in ("9", "post 9", "#9", "it's 9", "number 9", "  9  "):
            with self.subTest(reply):
                self.assertEqual(9, self.read("feed.posts.like", "post_id", reply))

    def test_a_reply_with_no_number_answers_no_id(self) -> None:
        for reply in ("good morning", "the blue one", "", "   ", "thanks!"):
            with self.subTest(reply):
                self.assertIsNone(self.read("feed.posts.like", "post_id", reply))

    def test_two_numbers_answer_no_id(self) -> None:
        """Choosing between them would be a guess wearing the clothes of a reading.

        This is the guard that keeps the permissiveness above honest. Dropping the noun
        requirement is safe only while "exactly one number" is doing the work the noun
        used to do.
        """
        for reply in ("9 or 12", "posts 3 and 4", "between 5 and 7"):
            with self.subTest(reply):
                self.assertIsNone(self.read("feed.posts.like", "post_id", reply))

    def test_a_price_answers_a_threshold(self) -> None:
        self.assertEqual(95000.0, self.read("crypto.alerts.update", "threshold", "95000"))
        self.assertEqual(100000.0, self.read("crypto.alerts.update", "threshold", "100k"))
        self.assertEqual(95000.0,
                         self.read("crypto.alerts.update", "threshold", "make it 95,000"))

    def test_an_enum_is_answered_in_the_words_it_was_offered_in(self) -> None:
        """The question said "English". The answer has to be allowed to say it back.

        Batch 5 stopped the runtime replying "preferred language has to be one of: en,
        es, fr", because that is a schema speaking. Having asked in product language,
        accepting only ``en`` would be a question the system cannot understand the
        answer to — worse than the error it replaced, since at least an error does not
        pretend to be a conversation.
        """
        for reply, expected in (("english", "en"), ("en", "en"), ("Spanish", "es"),
                                ("french please", "fr")):
            with self.subTest(reply):
                self.assertEqual(
                    expected,
                    self.read("profile.preferences.update", "preferred_language", reply))

    def test_an_unsupported_enum_value_is_not_an_answer(self) -> None:
        for reply in ("german", "klingon", "whatever you think"):
            with self.subTest(reply):
                self.assertIsNone(
                    self.read("profile.preferences.update", "preferred_language", reply))

    def test_a_coin_answers_a_symbol(self) -> None:
        self.assertEqual("ETH", self.read("crypto.alerts.create", "symbol", "ethereum"))
        self.assertEqual("ETH", self.read("crypto.alerts.create", "symbol", "ether"))
        # A ticker nobody has aliased yet. Alone in a reply, it is the shape of an
        # answer to "which coin?" and of very little else.
        self.assertEqual("LINK", self.read("crypto.alerts.create", "symbol", "link"))

    def test_two_coins_answer_no_symbol(self) -> None:
        self.assertIsNone(self.read("crypto.alerts.create", "symbol", "bitcoin or ethereum"))

    def test_free_text_takes_the_reply_whole(self) -> None:
        self.assertEqual("running late, be there at six",
                         self.read("messages.draft", "body",
                                   "running late, be there at six"))

    def test_walking_away_is_not_free_text(self) -> None:
        """The one field where nothing else can tell an answer from a withdrawal.

        For a number or an enum, "never mind" fails on its own — there is no number and
        no supported choice. For a message body every reply is syntactically valid, so
        "never mind" would become the message. The list is checked *only* here for that
        reason; applying it to the numeric fields would create a way to fail to give an
        answer that happens to contain the word "cancel".
        """
        for reply in ("never mind", "forget it", "cancel", "actually no"):
            with self.subTest(reply):
                self.assertIsNone(self.read("messages.draft", "body", reply))


class MergeTests(unittest.TestCase):
    """``answer_pending`` — all of the question, or none of it."""

    @classmethod
    def setUpClass(cls) -> None:
        from services import undx_agent_runtime
        from services.undx_capability_registry import REGISTRY

        cls.rt = undx_agent_runtime
        cls.REGISTRY = REGISTRY

    def test_a_partial_answer_is_not_an_answer(self) -> None:
        """Two fields asked, one supplied, and the runtime is where it started.

        Accepting the half would mean asking again with one field crossed off, which is
        a multi-turn negotiation the system is not built to hold and the person did not
        agree to enter.
        """
        spec = self.REGISTRY["crypto.alerts.create"]
        self.assertIsNone(self.rt.answer_pending(
            spec, {}, ("symbol", "threshold"), "bitcoin"))
        merged = self.rt.answer_pending(spec, {}, ("symbol", "threshold"),
                                        "bitcoin at 100k")
        self.assertEqual({"symbol": "BTC", "threshold": 100000.0}, merged)

    def test_the_reply_may_add_but_never_move_the_target(self) -> None:
        """The invariant that keeps the second turn from retargeting the first.

        "Change alert 3" then "95000" must change alert 3. If the reply could overwrite
        ``alert_id``, then any reply containing a number could point the write at a
        different alert, and the person would go on to approve a confirmation card for
        something they never named.
        """
        spec = self.REGISTRY["crypto.alerts.update"]
        merged = self.rt.answer_pending(spec, {"alert_id": 3}, ("threshold",), "95000")
        self.assertEqual({"alert_id": 3, "threshold": 95000.0}, merged)


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


class AnsweredTurnTests(_Fixture):
    """Two messages, end to end, through the real runtime and a real database."""

    def test_the_answer_completes_the_action_the_question_was_about(self) -> None:
        asked = self.say("change my alert threshold")
        self.assertEqual("crypto.alerts.update", asked.capability_id)
        self.assertTrue(asked.card["needs_answer"])
        self.assertEqual(["threshold"], asked.card["awaiting_fields"])

        answered = self.say("95000")
        self.assertTrue(answered.handled)
        self.assertEqual("crypto.alerts.update", answered.capability_id)
        # The alert named by the *first* message, carried across.
        self.assertEqual(str(self.alert_id),
                         str(answered.card.get("target") or self.alert_id))

    def test_a_continued_write_still_meets_its_confirmation_card(self) -> None:
        """A continuation supplies a field. It confers nothing.

        Worth stating as a test because the two mechanisms now live in one table, and
        the cheap implementation — reusing the approval row for both — would have made
        answering a question equivalent to approving the write. Here the answered turn
        arrives at ``confirmation_required``, which is what an unanswered one would have
        done had the field been present from the start.
        """
        self.say("change my alert threshold")
        answered = self.say("95000")
        self.assertEqual("confirmation_required", answered.status)
        # Unchanged until the person actually approves.
        self.assertEqual(90000.0, float(self.fx.alert_threshold(self.alert_id)))

    def test_the_question_is_spent_by_the_turn_that_answers_it(self) -> None:
        self.say("change my alert threshold")
        self.assertTrue(self.pending())
        self.say("95000")
        self.assertEqual({}, self.pending())

    def test_a_reply_that_is_not_an_answer_ends_the_question(self) -> None:
        """Burned on use, not on success.

        The person said something else, which is what walking away looks like. A
        question that survived this would sit waiting for the next stray number, and
        would fire against a sentence nobody remembers being asked about.
        """
        self.say("change my alert threshold")
        walked = self.say("good morning")
        self.assertFalse(walked.handled)
        self.assertEqual({}, self.pending())

    def test_one_unanswerable_question_does_not_renew_itself(self) -> None:
        """Otherwise a question re-arms for as long as the person keeps typing.

        The second reply is also not an answer. If the runtime minted a fresh
        continuation on that turn, the pair would loop indefinitely and every unrelated
        message afterwards would be read as a possible reply.
        """
        self.say("change my alert threshold")
        self.say("hmm")
        self.assertEqual({}, self.pending())


class NoHijackTests(_Fixture):
    """A pending question must not change what an ordinary message means."""

    def test_a_message_that_routes_wins_outright(self) -> None:
        """The consult rule, stated as the thing it prevents.

        With "which post?" outstanding and a naive implementation that consulted the
        continuation first, "delete alert 3" would be read as the number 3 — an answer
        — and would like post 3 instead of deleting an alert. Routing first is what
        makes the continuation layer strictly additive: it can only produce outcomes on
        turns that previously produced none.
        """
        self.say("like a post")
        self.assertTrue(self.pending())
        routed = self.say("show me my alerts")
        self.assertEqual("crypto.alerts.list", routed.capability_id)

    def test_moving_on_drops_the_question(self) -> None:
        """Not merely ignored on that turn — dropped.

        Left alive, a forgotten question would still be waiting when an unrelated "9"
        arrived two messages later, inside its expiry window. Closing it at the moment
        the person demonstrably moved on removes the window rather than shortening it.
        """
        self.say("like a post")
        self.say("show me my alerts")
        self.assertEqual({}, self.pending())

    def test_an_explicitly_named_capability_also_drops_it(self) -> None:
        """The planner path, which does not go through the matcher at all.

        A caller that names a capability has said what it wants as plainly as a message
        can; there is nothing left for a question to clarify. This is asserted
        separately because it is a different branch, and a guard written only against
        the matcher would leave it live.
        """
        self.say("like a post")
        self.rt.handle(self.fx.cur, user_id=OWNER_ID, text="",
                       capability_id="crypto.alerts.list", arguments={})
        self.fx.commit()
        self.assertEqual({}, self.pending())


class DegradationTests(_Fixture):
    """The layer is additive, so its failure must cost a sentence and not a turn."""

    def test_a_store_that_cannot_be_read_leaves_the_turn_intact(self) -> None:
        """Every turn that worked before this existed must still work without it.

        Simulated by breaking the lookup itself rather than by asserting on a flag,
        because the claim is about what happens when the store is genuinely
        unavailable. The routed turn completes; only the memory is lost.
        """
        def broken(*_args, **_kwargs):
            raise RuntimeError("store unavailable")

        original = self.arch.pending_continuation
        self.arch.pending_continuation = broken
        try:
            routed = self.say("show me my alerts")
            self.assertTrue(routed.handled)
            self.assertEqual("crypto.alerts.list", routed.capability_id)
            # And a question still gets asked, even though it cannot be remembered.
            asked = self.say("change my alert threshold")
            self.assertIn("price", asked.reply.lower())
        finally:
            self.arch.pending_continuation = original


# ---------------------------------------------------------------------------
# "Which one?" — the other road into the same question
# ---------------------------------------------------------------------------


#: A chooser as the runtime builds one: the rows in the order they were shown, each
#: stamped with the position the person sees. The ids are deliberately *not* 1 and 2.
#: Ids that coincide with positions make every reading agree, which would let a
#: confusion between "the id 2" and "the second one" pass every assertion here; the
#: cases that need that collision build it explicitly.
_SHOWN = [
    {"alert_id": 7, "symbol": "BTC", "display_name": "BTC alert", "choice_index": 1},
    {"alert_id": 4, "symbol": "ETH", "display_name": "ETH alert", "choice_index": 2},
]


class RenewalRuleTests(unittest.TestCase):
    """The no-renewal predicate, tested where it can actually be reached.

    Instrumenting the whole 517-test suite showed this branch taken zero times: every
    real journey arrives with the answered field already gone from the next question,
    because answering a value removes it from ``missing`` and choosing a candidate
    resolves the reference. So a mutation that forces the predicate to ``False`` kills
    no journey test, and that is a fact about the runtime's shape rather than a hole in
    the suite.

    It is still worth having and still worth testing. A future resolver that rejects the
    value it was just handed would make the branch live, and the difference between a
    backstop that works and one that has quietly rotted is a test that calls it directly.
    """

    def setUp(self) -> None:
        from services import undx_agent_runtime

        self.renewal = undx_agent_runtime.is_renewal

    def test_asking_again_for_what_was_just_supplied_is_a_renewal(self) -> None:
        self.assertTrue(self.renewal(("threshold",), ("threshold",)))

    def test_asking_for_something_else_is_progress(self) -> None:
        """The case the rule must not catch — "which one?" then "what price?"."""
        self.assertFalse(self.renewal(("threshold",), ("alert_id",)))

    def test_a_subset_of_what_was_supplied_is_still_a_renewal(self) -> None:
        """Stated as a subset rather than an equality, deliberately.

        A turn that answered two fields and is asked again for one of them has been
        told nothing new. Equality would let that through, and the loop it opens is
        the same loop, only reached by a longer road.
        """
        self.assertTrue(self.renewal(("alert_id",), ("alert_id", "threshold")))

    def test_asking_for_more_than_was_supplied_is_progress(self) -> None:
        self.assertFalse(self.renewal(("alert_id", "threshold"), ("alert_id",)))

    def test_a_turn_that_answered_nothing_never_renews(self) -> None:
        """A first question is not a repeat of anything, so it is always remembered."""
        self.assertFalse(self.renewal(("alert_id",), ()))

    def test_asking_nothing_is_not_a_renewal(self) -> None:
        """No question, nothing to suppress — the caller's own guard handles this."""
        self.assertFalse(self.renewal((), ("alert_id",)))


class ChoiceShapeTests(unittest.TestCase):
    """Reading "which one?" out of a reply, without a database in the way.

    The store is proven elsewhere. What is proven here is the reading, and most of
    these are negative on purpose: a chooser that answers confidently to the wrong
    thing is worse than one that asks twice, because the wrong answer arrives wearing
    a confirmation card that names it as the person's own choice.
    """

    def setUp(self) -> None:
        from services import undx_agent_runtime

        self.read = undx_agent_runtime.answer_for_choice

    def test_the_noun_and_the_number_name_a_row(self) -> None:
        self.assertEqual(7, self.read(_SHOWN, "alert 7"))
        self.assertEqual(4, self.read(_SHOWN, "alert #4"))

    def test_a_bare_id_outside_the_positions_is_still_a_row(self) -> None:
        """"7" cannot be a position in a two-item list, so it can only be an id."""
        self.assertEqual(7, self.read(_SHOWN, "7"))

    def test_a_position_is_an_index_into_what_was_shown(self) -> None:
        self.assertEqual(7, self.read(_SHOWN, "the first one"))
        self.assertEqual(4, self.read(_SHOWN, "the second one"))
        self.assertEqual(4, self.read(_SHOWN, "the last one"))

    def test_a_distinguishing_word_names_a_row(self) -> None:
        self.assertEqual(7, self.read(_SHOWN, "the bitcoin one"))
        self.assertEqual(4, self.read(_SHOWN, "ethereum"))

    def test_the_reason_a_reply_missed_is_kept(self) -> None:
        """``read_choice`` distinguishes three refusals ``answer_for_choice`` cannot.

        The wrapper collapses all of them to ``None``, which is correct for a caller
        asking "which row" — there isn't one. It was not correct for the caller asking
        "does this person deserve an answer", and that caller existed and was getting it
        wrong for six batches.

        Stated here at the unit rather than only through a journey because the three
        cases are cheap to enumerate and the journey tests can only reach two of them
        without a great deal of setup.
        """
        from services import undx_agent_runtime as rt

        rows = [{"alert_id": 7, "symbol": "BTC", "choice_index": 1},
                {"alert_id": 4, "symbol": "BTC", "choice_index": 2}]
        # A word both rows carry: aimed at the question, hit too much of it.
        self.assertEqual(rt.CHOICE_MISS_AMBIGUOUS, rt.read_choice(rows, "the bitcoin one").miss)
        # A number no row carries, bare and with the noun. Aimed, and missed.
        self.assertEqual(rt.CHOICE_MISS_NO_SUCH_ROW, rt.read_choice(rows, "9").miss)
        self.assertEqual(rt.CHOICE_MISS_NO_SUCH_ROW, rt.read_choice(rows, "alert 9").miss)
        # Not aimed at the question at all. The empty miss is what keeps a chooser from
        # swallowing the rest of the conversation to stay alive.
        self.assertEqual("", rt.read_choice(rows, "what is the weather").miss)
        self.assertEqual("", rt.read_choice(rows, "thanks").miss)
        # And a hit reports no miss, which is the invariant that makes `miss` readable
        # without checking `chosen` first.
        hit = rt.read_choice(rows, "1")
        self.assertEqual(7, hit.chosen)
        self.assertEqual("", hit.miss)

    def test_the_wrapper_still_answers_only_with_a_row(self) -> None:
        """``answer_for_choice`` must not start returning the new information.

        Every existing caller reads it as "the id, or nothing". A refactor that leaked a
        truthy miss object through this function would turn every unreadable reply into
        an id-shaped value at a dozen call sites at once.
        """
        from services import undx_agent_runtime as rt

        rows = [{"alert_id": 7, "symbol": "BTC", "choice_index": 1},
                {"alert_id": 4, "symbol": "BTC", "choice_index": 2}]
        for reply in ("the bitcoin one", "9", "alert 9", "what is the weather"):
            self.assertIsNone(rt.answer_for_choice(rows, reply), reply)
        self.assertEqual(7, rt.answer_for_choice(rows, "1"))

    def test_a_row_with_no_usable_id_is_not_answerable_by_its_position(self) -> None:
        """Written because the mutation harness found this property undefended.

        ``mutate12.py``'s ``zerorow`` mode drops the ``ids[position - 1] > 0`` guard and
        every other test still passed, which by this harness's own contract means the
        guard was decorative and had to be given a test rather than an argument.

        The situation is a row that reached the chooser without an ``alert_id`` the
        runtime can use — a tool result shaped slightly wrong, a join that came back
        empty. It is drawn, because the client draws what it is sent, so the person can
        pick it. Without the guard, picking it resolves to id 0, and 0 travels on to the
        owner-scoped read looking exactly like an id the person typed. It finds nothing
        and the turn ends in a failure that names no cause.

        Refusing here is not better because it succeeds — it does not succeed either.
        It is better because the reply falls through to the readings below and then out,
        which ends the question, rather than resolving to a value the rest of the
        pipeline will treat as a real choice.

        And it must be asserted through ``read_choice``, not through
        ``answer_for_choice``. That is the second finding, made when ``zerorow`` walked
        past this test *after* it was written for ``zerorow``: the wrapper ends with
        ``return reading.chosen or None``, and ``0`` is falsy, so a mutant that resolves
        position one to id ``0`` collapses to ``None`` at exactly the same place the
        guard would have sent it. Both spellings return ``None`` and the assertion cannot
        tell them apart. The difference is entirely in ``miss``:

            intact   chosen=0  miss="no_such_row"
            mutated  chosen=0  miss=""

        which is the runtime saying, in the first case, that a reply aimed at the list
        missed it — and in the second that nothing was aimed at all. Only the first ends
        the question with an explanation.
        """
        from services import undx_agent_runtime as rt

        broken = [{"alert_id": 0, "symbol": "BTC", "choice_index": 1},
                  {"alert_id": 4, "symbol": "ETH", "choice_index": 2}]
        reading = rt.read_choice(broken, "1")
        self.assertEqual(0, reading.chosen)
        self.assertEqual(rt.CHOICE_MISS_NO_SUCH_ROW, reading.miss)
        # The wrapper's view of the same reply, kept because it is what every caller
        # actually sees. Necessary, and — as above — not sufficient on its own.
        self.assertIsNone(self.read(broken, "1"))
        # The intact row beside it still answers. A malformed row is one bad row, not a
        # broken chooser.
        self.assertEqual(4, self.read(broken, "2"))
        self.assertEqual("", rt.read_choice(broken, "2").miss)

    def test_a_number_alone_is_the_position_it_was_shown_at(self) -> None:
        """Batch 7 asserted the opposite here, and Batch 12 reversed it on evidence.

        Ids 2 and 1, shown in that order. "1" is both the alert whose id is 1 — the
        second row — and the first row shown, whose id is 2. Batch 7 read that as two
        equally available readings with nothing to choose between them and declined.

        The premise was that both readings are available *to the person*, and they are
        not. The id is not on screen. The position is, or is about to be: the runtime
        stamps ``choice_index`` 1..N on every row it sends and the chooser is drawn
        numbered. So a lone number is an answer to the list in front of them, and
        declining it refused the one answer the card actually invites.

        It refused it unevenly, too, which is what made this worth reversing rather
        than documenting. Against three alerts created in order the shown list runs
        position 1 → id 3, 2 → id 2, 3 → id 1, so "2" resolved by arithmetic accident
        and "1" and "3" did not, with nothing on screen to say which was which.

        The contradiction rule is not gone. It applies to every reply carrying more
        than a number, which is the case below and the case it was written for.
        """
        collide = [{"alert_id": 2, "symbol": "BTC", "choice_index": 1},
                   {"alert_id": 1, "symbol": "ETH", "choice_index": 2}]
        self.assertEqual(2, self.read(collide, "1"))
        self.assertEqual(1, self.read(collide, "2"))
        # Written the way a numbered list is written, and meaning the same thing.
        self.assertEqual(2, self.read(collide, "1."))
        self.assertEqual(2, self.read(collide, "#1"))
        # Naming the noun still names the id, because "alert 1" is never a position.
        self.assertEqual(1, self.read(collide, "alert 1"))

    def test_an_ambiguous_number_inside_a_sentence_is_still_refused(self) -> None:
        """The half of the contradiction rule Batch 12 kept, stated on its own.

        A reply that is only a number is an answer to the numbered list. A reply that
        is a sentence is not — it may be about anything, and the number in it carries
        both readings with no way to choose. Reading those as positions too would
        widen a narrow, visible affordance into a guess, which is the trade the
        original rule refused and this still refuses.
        """
        collide = [{"alert_id": 2, "symbol": "BTC", "choice_index": 1},
                   {"alert_id": 1, "symbol": "ETH", "choice_index": 2}]
        self.assertIsNone(self.read(collide, "go with 1 please"))
        self.assertIsNone(self.read(collide, "i meant 2"))

    def test_a_number_outside_the_chooser_is_not_a_choice(self) -> None:
        self.assertIsNone(self.read(_SHOWN, "17"))
        self.assertIsNone(self.read(_SHOWN, "alert 17"))

    def test_a_word_two_candidates_share_chooses_neither(self) -> None:
        both = [{"alert_id": 7, "symbol": "BTC", "display_name": "BTC alert"},
                {"alert_id": 4, "symbol": "BTC", "display_name": "BTC alert"}]
        self.assertIsNone(self.read(both, "the bitcoin one"))

    def test_a_reply_about_something_else_is_not_a_choice(self) -> None:
        self.assertIsNone(self.read(_SHOWN, "what is the weather"))
        self.assertIsNone(self.read(_SHOWN, "never mind"))

    def test_a_reply_of_only_filler_chooses_nothing(self) -> None:
        """"That one" points at a screen, not at a row."""
        self.assertIsNone(self.read(_SHOWN, "that one"))
        self.assertIsNone(self.read(_SHOWN, "yes please"))

    def test_a_number_in_a_label_does_not_name_its_row(self) -> None:
        """A threshold that happens to appear in a label is not an id.

        Otherwise "95000" — much more plausibly an answer to a price question — would
        select whichever candidate's label mentioned that number.
        """
        priced = [{"alert_id": 7, "symbol": "BTC", "display_name": "BTC above 95000"},
                  {"alert_id": 4, "symbol": "ETH", "display_name": "ETH above 4000"}]
        self.assertIsNone(self.read(priced, "95000"))

    def test_an_empty_chooser_answers_nothing(self) -> None:
        self.assertIsNone(self.read([], "the first one"))
        self.assertIsNone(self.read(_SHOWN, ""))


class NamedIdShapeTests(unittest.TestCase):
    """Reading an id out of a first message, without a database in the way.

    The negatives are the point. This pattern runs against every alert-bearing sentence
    in the corpus, most of which contain numbers that are not ids — a threshold, a
    price, a percentage — and a pattern loose enough to take one of those would convert
    the most common phrasing there is into a write against an arbitrary row.
    """

    def setUp(self) -> None:
        from services import undx_agent_runtime

        self.ids = undx_agent_runtime._ids_named_in

    def test_the_noun_and_the_number_name_a_row(self) -> None:
        self.assertEqual([4], self.ids("pause alert 4"))
        self.assertEqual([4], self.ids("pause alert #4"))
        self.assertEqual([4], self.ids("mute alert id 4"))
        self.assertEqual([4], self.ids("pause my alert 4"))

    def test_a_price_after_the_noun_is_not_an_id(self) -> None:
        """The sentence the whole pattern is shaped around.

        "Change my alert to 95000" is the single most common thing a person says to
        this capability. A pattern that allowed a word between the noun and the number
        would read 95000 as the alert's id, fail to find it, and report that the alert
        is not on the account — for a sentence that is perfectly well formed.
        """
        self.assertEqual([], self.ids("change my alert to 95000"))
        self.assertEqual([], self.ids("set my alert at 4000"))
        self.assertEqual([], self.ids("alert me when bitcoin goes above 90000"))

    def test_a_number_with_no_noun_is_not_an_id(self) -> None:
        self.assertEqual([], self.ids("95000"))
        self.assertEqual([], self.ids("pause it"))

    def test_a_hash_names_a_row_on_its_own(self) -> None:
        """"#4" is how the app itself labels them, so it is how people type them back."""
        self.assertEqual([4], self.ids("pause #4"))

    def test_several_ids_are_all_reported(self) -> None:
        """So the caller can tell "one row" from "more than one", not just find one."""
        self.assertEqual([4, 7], self.ids("pause alerts 4 and 7"))

    def test_the_same_id_twice_is_one_row(self) -> None:
        self.assertEqual([4], self.ids("alert 4, i mean alert 4"))

    def test_zero_is_not_a_row(self) -> None:
        """Ids are positive, and 0 is what an empty argument coerces to."""
        self.assertEqual([], self.ids("pause alert 0"))


class NamedIdJourneyTests(_Fixture):
    """An id written into a first message must select that row, or no row at all.

    Until this batch the resolver read ids only from ``explicit_id``, which is empty on
    a first turn because extraction runs after resolution. So the id in the sentence was
    never consulted and the listing path answered instead — and the listing path answers
    a different question, one about the account rather than about the sentence.
    """

    def setUp(self) -> None:
        super().setUp()
        self.second = self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=4000.0)
        self.fx.commit()

    def test_a_named_id_is_not_ambiguous(self) -> None:
        """Two alerts on the account, one named in the sentence. That is not ambiguity.

        This reported "more than one of your alerts matches that description" — a claim
        about a sentence that named exactly one.
        """
        turn = self.say("change alert %d to 95000" % self.alert_id)
        self.assertTrue(turn.handled)
        self.assertEqual("confirmation_required", turn.receipt.status)
        approved = self.approved_arguments()
        self.assertEqual(self.alert_id, approved.get("alert_id"))
        self.assertEqual(95000.0, float(approved.get("threshold") or 0))

    def test_the_named_id_wins_over_a_symbol_in_the_same_sentence(self) -> None:
        """"Pause alert 2" where alert 2 is the ETH one and "bitcoin" is also present.

        An id is the most specific thing a sentence can carry, so it outranks a coin
        name that would otherwise have selected the other row.
        """
        turn = self.say("pause alert %d, not the bitcoin one" % self.second)
        self.assertTrue(turn.handled)
        self.assertEqual("paused", self.fx.alert_status(self.second, OWNER_ID))
        self.assertEqual("active", self.fx.alert_status(self.alert_id, OWNER_ID))

    def test_an_id_the_account_does_not_own_writes_nothing(self) -> None:
        """The severe half of the defect, stated as the row it must not touch.

        On an account with a single alert, "pause alert 999" used to fall through to
        the listing path, find exactly one alert, pause it, and read it back — so the
        turn reported ``verified_success`` for a row the person had not named. The
        verification engine confirmed it because the wrong row is what it was handed;
        reading back the thing you just wrote cannot detect that you wrote the wrong
        thing.
        """
        before = self.fx.alert_status(self.alert_id, OWNER_ID)
        turn = self.say("pause alert 999")
        self.assertEqual(before, self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertEqual(before, self.fx.alert_status(self.second, OWNER_ID))
        if turn.handled:
            self.assertNotEqual("verified_success", turn.receipt.status)

    def test_another_accounts_alert_is_not_reachable_by_naming_it(self) -> None:
        """Precision is not permission. The id is read back through the owner's scope."""
        foreign = self.fx.make_alert(OTHER_ID, symbol="SOL", threshold=200.0)
        self.fx.commit()
        turn = self.say("pause alert %d" % foreign)
        self.assertEqual("active", self.fx.alert_status(foreign, OTHER_ID))
        if turn.handled:
            self.assertNotEqual("verified_success", turn.receipt.status)

    def test_an_unowned_id_is_refused_before_a_card_is_offered(self) -> None:
        """Where the read-back actually earns its cost, as opposed to where I assumed.

        Removing the owner-scoped read-back and trusting a named id outright does *not*
        produce a wrong write — the service layer's own owner scope stops that, and it
        stopped it before this batch existed. What it produces instead is a
        ``confirmation_required`` card for alert 999: the person is asked to approve a
        change to a row that does not exist, agrees, and only then finds out. An
        approval minted against a row nobody owns is a real defect even though nothing
        is written, because the confirmation card is the runtime's one honest promise
        that the thing described is the thing that will happen.

        Written after a mutation caught less than expected. The two tests above assert
        the row is untouched, which stays true with the read-back removed, so neither of
        them was measuring this. Naming that plainly is better than leaving a passing
        mutation to imply a guarantee the suite never checked.
        """
        turn = self.say("change alert 999 to 12345")
        self.assertNotEqual("confirmation_required",
                            getattr(turn.receipt, "status", None))
        self.assertEqual({}, self.approved_arguments())

    def test_naming_two_alerts_asks_which_rather_than_taking_the_first(self) -> None:
        """One capability, one row. Two named is a question, not a licence to pick.

        Taking the first would do half of what was asked and report complete success
        for it, which is worse than asking — the person has no way to see that the
        second alert was ignored.
        """
        turn = self.say("pause alerts %d and %d" % (self.alert_id, self.second))
        self.assertEqual("active", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertEqual("active", self.fx.alert_status(self.second, OWNER_ID))
        self.assertEqual(("alert_id",), self.pending()["missing"])
        # And the question it asked is answerable, by the machinery Batch 7 built.
        # Asserted against the approval rather than the alert's status, because a
        # continued write stops at its confirmation card by design — the continuation
        # supplied the missing field, and a field is not a permission. Watching the row
        # here would be testing that rule rather than this one.
        self.say("alert %d" % self.second)
        self.assertEqual(self.second, self.approved_arguments().get("alert_id"))
        self.assertEqual("active", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertEqual("active", self.fx.alert_status(self.second, OWNER_ID))

    def test_naming_two_where_only_one_is_owned_is_not_a_question(self) -> None:
        """The unowned id drops out, leaving exactly one — so there is nothing to ask.

        Offering it as a chooser entry would be worse than useless: it would fail the
        moment it was chosen, having first told the person it was available.
        """
        foreign = self.fx.make_alert(OTHER_ID, symbol="SOL", threshold=200.0)
        self.fx.commit()
        self.say("pause alerts %d and %d" % (self.alert_id, foreign))
        self.assertEqual("paused", self.fx.alert_status(self.alert_id, OWNER_ID))
        self.assertEqual("active", self.fx.alert_status(foreign, OTHER_ID))

    def test_a_sentence_with_no_id_still_reaches_the_chooser(self) -> None:
        """The Batch 7 path must survive the Batch 8 one being added in front of it."""
        turn = self.say("pause my alert")
        self.assertTrue((turn.card or {})["needs_disambiguation"])
        self.assertEqual(("alert_id",), self.pending()["missing"])


class AmbiguousReferenceTests(_Fixture):
    """The two-turn journey, against a real database and the real alert engine."""

    def setUp(self) -> None:
        super().setUp()
        # A second alert, so "my alert" stops naming exactly one.
        self.other_alert = self.fx.make_alert(OWNER_ID, symbol="ETH", threshold=4000.0)

    def shown(self) -> list[int]:
        """The candidate ids in the order the question presented them."""
        return [int(choice["alert_id"]) for choice in self.pending().get("choices", [])]


    def test_an_ambiguous_reference_is_remembered_as_a_question(self) -> None:
        asked = self.say("pause my alert")
        self.assertTrue(asked.handled)
        card = asked.card or {}
        self.assertTrue(card["needs_disambiguation"])
        self.assertTrue(card["needs_answer"])
        self.assertEqual(["alert_id"], card["awaiting_fields"])
        pending = self.pending()
        self.assertEqual("crypto.alerts.pause", pending["capability_id"])
        self.assertEqual(("alert_id",), pending["missing"])
        self.assertEqual({self.alert_id, self.other_alert}, set(self.shown()))

    def test_the_candidates_are_numbered_in_the_order_they_are_shown(self) -> None:
        """A position is only answerable if both sides agree what first means."""
        asked = self.say("pause my alert")
        candidates = (asked.card or {})["candidates"]
        self.assertEqual([1, 2], [item["choice_index"] for item in candidates])
        self.assertEqual([item["alert_id"] for item in candidates], self.shown())

    def test_naming_a_candidate_completes_the_action(self) -> None:
        self.say("pause my alert")
        chosen = self.shown()[0]
        answered = self.say(f"alert {chosen}")
        self.assertTrue(answered.handled)
        self.assertEqual("crypto.alerts.pause", answered.capability_id)
        self.assertEqual(chosen, self.approved_arguments().get("alert_id"))

    def test_a_position_completes_the_action_the_person_could_see(self) -> None:
        self.say("pause my alert")
        expected = self.shown()[1]
        self.say("the second one")
        self.assertEqual(expected, self.approved_arguments().get("alert_id"))

    def test_the_number_on_the_card_completes_the_action(self) -> None:
        """The whole journey for the reply the card most obviously invites.

        ``AnswerShapeTests`` proves ``answer_for_choice`` reads a lone number as the
        published position. This proves the rest of it: that the number the *card*
        carries is the number that resolves, through the real store, the real
        owner-scoped read and the real argument resolver.

        Asserting it against ``shown()`` rather than a literal id is the point. The rows
        are ordered by the engine, not by this test, and hard-coding an id would let the
        assertion pass by the same arithmetic coincidence that made only the middle row
        answerable before this batch — position 2 → id 2 against three alerts, with 1
        and 3 refused and nothing on screen to say which was which.
        """
        self.say("pause my alert")
        shown = self.shown()
        self.assertEqual(2, len(shown))
        answered = self.say("2")
        self.assertTrue(answered.handled)
        self.assertEqual("crypto.alerts.pause", answered.capability_id)
        self.assertEqual(shown[1], self.approved_arguments().get("alert_id"))

    def test_the_answered_number_is_shown_back_before_anything_is_written(self) -> None:
        """The safety argument for reading a bare number as a position, stated as a test.

        A misread position is only recoverable if the person sees which row it landed on
        before the write happens. That holds because a continued turn is never
        ``is_explicit`` — so the answer lands on a confirmation card, not a receipt.

        If that ever stops being true, reading a lone digit as a position becomes a
        guess that writes, which is a different and much worse trade than the one this
        batch made. This test is the tripwire on it.
        """
        self.say("pause my alert")
        first_row = self.shown()[0]
        answered = self.say("1")
        card = answered.card or {}
        self.assertEqual("confirmation_required", card["status"])
        self.assertFalse(card.get("verified"))
        self.assertTrue(card.get("confirmation_token"))
        # And nothing has been written: the row the number named is still running.
        self.assertEqual(first_row, self.approved_arguments().get("alert_id"))
        self.assertNotEqual("paused", self.fx.alert_status(first_row))

    def test_a_number_no_row_carries_is_not_an_answer(self) -> None:
        """Two rows shown, "5" typed. There is no fifth row and no alert with that id.

        The position reading is bounded by the list actually sent, so this resolves to
        nothing rather than to the nearest row. A chooser that rounded would be worse
        than one that refused, and that half is unchanged.

        Batch 12 wrote this asserting ``handled=False`` and a spent question, and Batch
        13 changed both. Not because the reading changed — "5" still names no row — but
        because ending the turn there was the runtime declining to mention that the
        person had just answered its own question. A bare number against a live chooser
        cannot be a message about anything else, so it is now told that 5 is not one of
        these, and the rows are still there to answer.
        """
        self.say("pause my alert")
        stray = self.say("5")
        self.assertTrue(stray.handled)
        card = stray.card or {}
        self.assertEqual("clarification_required", card["status"])
        self.assertFalse(card.get("verified"))
        # Nothing was chosen, so nothing is awaiting approval.
        self.assertEqual({}, self.approved_arguments())
        # And the question survived, so the next reply can still answer it.
        self.assertEqual(("alert_id",), self.pending()["missing"])
        self.assertEqual(self.shown(), [int(c["alert_id"]) for c in self.pending()["choices"]])

    def test_a_distinguishing_word_completes_the_action(self) -> None:
        self.say("pause my alert")
        self.say("the ethereum one")
        self.assertEqual(self.other_alert, self.approved_arguments().get("alert_id"))

    def test_a_word_that_matches_every_row_is_answered_rather_than_ignored(self) -> None:
        """The defect Batch 13 was built for, as the person meets it.

        A second Bitcoin alert, so "pause my bitcoin alert" narrows to two rows that are
        both Bitcoin — the fixture's ETH alert is filtered out by the symbol, which is
        why the count here is two and not three. UNDX asks which one. The person says
        "the bitcoin one", which is not a bad answer; it is the answer that works
        whenever the rows differ, and ``test_a_distinguishing_word_completes_the_action``
        proves it works one line above. Here it matches both.

        Before this batch the turn came back ``handled=False`` with no card at all, and
        the question was already burned on the way through. The person answered a
        question and got silence, and the question they answered was gone — so trying
        again meant retyping the request that produced it. The runtime knew: the code
        that declined has said "not a reply about anything else either" since Batch 7,
        and then returned the same ``None`` it returns for a reply about something else.

        Both halves are asserted, because either alone would be a worse fix than none.
        Speaking without keeping the question leaves the person told off with nothing to
        answer. Keeping the question without speaking leaves an invisible one open.
        """
        third = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=91000.0)
        self.say("pause my bitcoin alert")
        shown = self.shown()
        self.assertIn(third, shown)
        missed = self.say("the bitcoin one")
        self.assertTrue(missed.handled)
        card = missed.card or {}
        self.assertEqual("clarification_required", card["status"])
        self.assertTrue(card["needs_answer"])
        self.assertEqual(["alert_id"], card["awaiting_fields"])
        # The rows come back, in the order they were first shown. A re-ask that
        # renumbered would make the number the person is about to type mean a different
        # row than the one they are looking at.
        self.assertEqual(shown, [int(item["alert_id"]) for item in card["candidates"]])
        self.assertEqual([1, 2], [item["choice_index"] for item in card["candidates"]])
        # Nothing was chosen and nothing is waiting to be approved.
        self.assertEqual({}, self.approved_arguments())
        # And the second attempt lands, which is the only thing that makes the first
        # attempt's failure recoverable rather than merely narrated.
        self.say("2")
        self.assertEqual(shown[1], self.approved_arguments().get("alert_id"))

    def test_naming_a_row_that_is_not_there_is_answered_rather_than_ignored(self) -> None:
        """"Alert 8" against two rows: the right kind of thing, the wrong number.

        Weaker evidence than a word matching several rows, and still conclusive. The
        reply names the resource the question is about and gives a number no row on the
        card carries. Nothing else in a conversation looks like that, so reading it as a
        message about some other subject — which is what falling through means — was a
        worse guess than the one this batch makes.
        """
        self.say("pause my alert")
        shown = self.shown()
        missed = self.say("alert 8")
        self.assertTrue(missed.handled)
        self.assertEqual("clarification_required", (missed.card or {})["status"])
        self.assertEqual({}, self.approved_arguments())
        self.assertEqual(shown, [int(c["alert_id"]) for c in self.pending()["choices"]])

    def test_a_re_ask_does_not_revive_the_question_it_replaced(self) -> None:
        """The re-armed question is a new one, and the old one stays spent.

        This is the whole of the safety argument against burn-on-use, so it is asserted
        rather than reasoned about. If the re-ask un-burned the original continuation,
        the store would hold a question that had already been acted on once — which is
        the exact state the burn rule exists to prevent, reintroduced by the fix for a
        different problem.
        """
        self.say("pause my alert")
        original = self.pending()["continuation_id"]
        self.say("alert 8")
        revived = self.pending()
        self.assertTrue(revived)
        self.assertNotEqual(original, revived["continuation_id"])
        self.assertNotEqual(self.pending()["row_id"], 0)

    def test_the_first_sentences_other_work_survives_the_choice(self) -> None:
        """The reply supplies the row and nothing else.

        "Change my alert to 95000" carries a threshold and an unresolvable target. When
        the person then says which alert, the price they already gave must still be the
        price on the card — re-deriving arguments from a reply that mentions no number
        would quietly turn a priced update into an empty one.
        """
        self.say("change my alert to trigger at 95000")
        self.assertEqual(("alert_id",), self.pending()["missing"])
        self.say("the bitcoin one")
        approved = self.approved_arguments()
        self.assertEqual(self.alert_id, approved.get("alert_id"))
        self.assertEqual(95000.0, float(approved.get("threshold") or 0))
        # And nothing has been written yet: the card is a request, not a receipt.
        self.assertEqual(90000.0, self.fx.alert_threshold(self.alert_id))

    def test_the_question_is_spent_by_the_turn_that_answers_it(self) -> None:
        self.say("pause my alert")
        self.say("the first one")
        self.assertEqual({}, self.pending())

    def test_a_reply_that_chooses_nothing_ends_the_question(self) -> None:
        """Burned on use, not on success — the same rule the other road follows.

        The boundary on Batch 13, and the reason that batch is narrow. A re-ask is owed
        only where the runtime can *prove* the reply was aimed at the question: a word
        matching several rows, a number matching none, the resource noun with a wrong
        number. "What is the weather" is none of those. Widening the rule to cover it
        would mean deciding from vocabulary whether a sentence sounds like a reply, and
        a chooser that swallowed unrelated sentences to keep itself alive would be the
        Batch 6 hijack defect wearing a helpful face.
        """
        self.say("pause my alert")
        stray = self.say("what is the weather")
        self.assertFalse(stray.handled)
        self.assertEqual({}, self.pending())

    def test_a_routable_message_is_never_read_as_a_choice(self) -> None:
        """Routing wins outright, and the pending question ends rather than lingering."""
        self.say("pause my alert")
        routed = self.say("show me my alerts")
        self.assertTrue(routed.handled)
        self.assertEqual("crypto.alerts.list", routed.capability_id)
        self.assertEqual({}, self.pending())

    def test_a_choice_is_re_checked_against_the_account_that_made_it(self) -> None:
        """A stored candidate is a memory, not a permission.

        The candidate list is rewritten to name an alert belonging to somebody else —
        which is what a tampered or stale store would look like — and the answering
        turn must still refuse, because the id it recovers is put back through the same
        owner-scoped read as an id the person had typed.
        """
        foreign = self.fx.make_alert(OTHER_ID, symbol="SOL", threshold=100.0)
        self.say("pause my alert")
        row_id = self.pending()["row_id"]
        self.fx.cur.execute(
            "UPDATE pulse_ai_confirmations SET arguments_json=? WHERE id=?",
            ('{"undx_pending_arguments": {}, "undx_pending_choices": '
             '[{"alert_id": %d, "symbol": "SOL", "choice_index": 1}]}' % foreign, row_id))
        self.fx.commit()
        answered = self.say("alert %d" % foreign)
        self.assertNotEqual(foreign, self.approved_arguments().get("alert_id"))
        self.assertEqual("active", self.fx.alert_status(foreign, OTHER_ID))
        if answered.handled:
            self.assertNotEqual("confirmation_required", answered.receipt.status)

    def test_one_unanswerable_chooser_does_not_renew_itself(self) -> None:
        """A reply that is a choice but still leaves the turn asking must not re-arm.

        Here the reply is genuinely ambiguous between an id and a position, so the
        question is used up without being answered. The turn that follows must find
        nothing outstanding rather than a freshly minted copy of the same question.
        """
        self.say("pause my alert")
        self.say("1")
        self.assertEqual({}, self.pending())

    def test_a_second_question_may_follow_the_first_if_it_is_a_different_one(self) -> None:
        """Which one, then what price, then done.

        "Change my alert" is short of two things at once, and the runtime asks for them
        one at a time because a chooser and a price are not one question. The rule that
        stops a question renewing itself is about the *question*, not the turn: asking
        again about the same field would be a loop, asking about the next field is the
        conversation working. Getting this wrong in the safe direction is what would
        have happened if the no-renewal rule had been written per-turn — the person
        chooses their alert and is then told the price is missing, with nowhere to say
        it.
        """
        self.say("change my alert")
        self.assertEqual(("alert_id",), self.pending()["missing"])
        second = self.say("the bitcoin one")
        self.assertTrue(second.handled)
        self.assertIn("price", (second.reply or "").lower())
        still = self.pending()
        self.assertEqual(("threshold",), still["missing"])
        # The alert chosen a moment ago is carried, not re-derived from "95000".
        self.assertEqual(self.alert_id, still["arguments"].get("alert_id"))
        self.say("95000")
        approved = self.approved_arguments()
        self.assertEqual(self.alert_id, approved.get("alert_id"))
        self.assertEqual(95000.0, float(approved.get("threshold") or 0))
        self.assertEqual({}, self.pending())

    def test_asking_the_same_question_twice_does_not_re_arm_it(self) -> None:
        """The other half of the same rule, and the reason it is stated per-question."""
        self.say("change my alert")
        self.say("the bitcoin one")
        self.assertEqual(("threshold",), self.pending()["missing"])
        # A reply that is not a price. The question is spent, and asking it again must
        # not mint a fresh copy.
        self.say("hmm")
        self.assertEqual({}, self.pending())

    def test_no_matching_alert_is_not_a_chooser(self) -> None:
        """Zero candidates is a statement, not a question.

        "Pause my dogecoin alert" against an account with none has nothing to choose
        between, so no question is remembered and the next stray number stays stray.
        """
        told = self.say("pause my dogecoin alert")
        self.assertTrue(told.handled)
        self.assertFalse((told.card or {})["needs_answer"])
        self.assertEqual({}, self.pending())


if __name__ == "__main__":
    unittest.main(verbosity=2)
