"""Batch 11: a question has to look like a question.

Batch 6 made the clarifying question answerable and Batch 7 built the chooser, and
both were verified against the runtime alone. Neither asked what the native client
would *draw*, and that turned out to be where the damage was.

``mobile-native/src/undx/actionCards.ts`` classifies every card into one of a handful
of kinds and picks a kicker from the kind. Running its rules over real cards from this
runtime, before this batch, gave:

    chooser (two matching alerts)  component=crypto_alert_card  kind=receipt  kicker=RESULT
    missing field                  component=action_failure     kind=failure  kicker=NOT DONE
    truncated (too many alerts)    component=action_failure     kind=failure  kicker=NOT DONE
    no match (a statement)         component=action_failure     kind=failure  kicker=NOT DONE

The last line is right. The first three are not, and the first is the worst of them:
``_unresolved_response`` reached for ``spec.result_card`` when it had candidates, which
for crypto alerts is ``crypto_alert_card`` — a *receipt* card. So "which of these two
alerts do you mean?" was drawn under the kicker this client reserves for something that
already happened. That is the silent-degradation class this programme has spent ten
batches removing, found on the other side of the wire.

The status was wrong too, and had been named as wrong since Batch 6: every question
reported ``terminal_failure``, so anything counting terminal failures counted every
question the runtime asked as something breaking, and the metric got worse the more
carefully the runtime behaved.

Why it was deferred, and why the deferral is now spent. The recorded reason was that a
native client meeting an unknown enum value "renders nothing at all", so shipping one
unverified would trade a wrong label for a blank screen. That was an assumption, and
reading the client refuted it: ``kindOf`` returns ``"failure"`` for anything it does not
recognise — which is exactly what a question renders as today. An old client is
therefore no worse off than it is now, a new one is right, and ``contractParity.test.ts``
already fails when the server adds a card the client has no home for. Four batches of
caution rested on a sentence nobody had checked.

Two things this file deliberately does not test. It does not assert the *kicker* — that
string lives in the client and is asserted there, in ``actionCards.test.ts``; asserting
a copy of it here would be a second source of truth for the thing this batch exists to
give one source of truth to. And it does not test the simulator. The card shape is
provable from both sides in CI; that it looks right on a phone is not, and claiming
otherwise would be the kind of unobserved claim this programme forbids.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap  # noqa: E402

bootstrap.install()

from tests.undx_agent.harness import AgentFixture, OTHER_ID, OWNER_ID  # noqa: E402


class QuestionShape(unittest.TestCase):
    """Every shape of question this runtime can ask, against a real account."""

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import undx_agent_contracts, undx_agent_runtime

        self.contracts = undx_agent_contracts
        self.runtime = undx_agent_runtime

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str):
        turn = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text,
                                   correlation_id="b11")
        self.fx.commit()
        return turn

    def alerts(self, count: int, symbol: str = "BTC") -> list[int]:
        ids = [self.fx.make_alert(OWNER_ID, symbol=symbol, threshold=90000.0 + n)
               for n in range(count)]
        self.fx.commit()
        return ids

    # -- the chooser -------------------------------------------------------------

    def test_a_chooser_is_not_a_receipt(self) -> None:
        """The defect, named as narrowly as it deserves.

        ``crypto_alert_card`` is in the client's receipt bucket. Any question that
        arrives wearing it is drawn as something that happened, so the assertion is
        against that specific string and not merely against "not the success card" —
        the next capability to acquire a result card would reintroduce this by a
        different name, and this fails when it does.
        """
        self.alerts(2)
        turn = self.say("pause my bitcoin alert")
        card = turn.card or {}
        self.assertEqual(self.contracts.CardType.CHOICE_REQUIRED, card.get("component"))
        self.assertNotEqual("crypto_alert_card", card.get("component"))
        self.assertEqual(2, len(card.get("candidates") or []))

    def test_a_chooser_carries_the_rows_it_is_asking_about(self) -> None:
        """A chooser with no rows is a prompt for nothing, so the pair travels together."""
        self.alerts(2)
        card = self.say("pause my bitcoin alert").card or {}
        self.assertEqual(self.contracts.CardType.CHOICE_REQUIRED, card.get("component"))
        self.assertTrue(card.get("candidates"))
        self.assertEqual(len(card["candidates"]), card.get("record_count"))
        self.assertTrue(card.get("needs_disambiguation"))

    # -- the clarification -------------------------------------------------------

    def test_a_missing_field_asks_rather_than_reports_a_failure(self) -> None:
        self.alerts(1)
        turn = self.say("change my bitcoin alert")
        card = turn.card or {}
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         turn.receipt.status)
        self.assertEqual(self.contracts.CardType.CLARIFICATION_REQUIRED,
                         card.get("component"))
        self.assertEqual([], card.get("candidates"))

    def test_more_rows_than_can_be_compared_is_a_clarification_not_a_chooser(self) -> None:
        """The branch Batch 7 left named and unclosed.

        Above the scan limit uniqueness cannot be established, so the runtime asks —
        but it has nothing to offer, because it could not read the whole account. That
        is a clarification, not an empty chooser, and the two are different cards
        precisely so this case does not have to be inferred from an empty list.

        Batch 14 narrowed *when* this fires without changing what it does. Sixty
        Bitcoin alerts is still more Bitcoin alerts than the scan permits, so the
        refusal is still correct and still empty. Drawing the fifty rows it managed to
        read was tried and reverted: a card listing fifty when sixty exist implies the
        set is complete, and fifty alerts on the same coin cannot be told apart by eye
        any better than the runtime can tell them apart. That is the empty-chooser
        defect with more scrolling.
        """
        self.alerts(60)
        turn = self.say("pause my bitcoin alert")
        card = turn.card or {}
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         turn.receipt.status)
        self.assertEqual(self.contracts.CardType.CLARIFICATION_REQUIRED,
                         card.get("component"))
        self.assertEqual([], card.get("candidates"))

    def test_a_crowded_account_does_not_make_one_alert_ambiguous(self) -> None:
        """The turn Batch 14 gave back, asserted end to end rather than at the resolver.

        Sixty alerts, exactly one of them Bitcoin. The sentence names one row and the
        account holds one row matching it, so there is nothing to ask about — and yet
        this was the card above, word for word, until the narrowing moved into the
        store. The person was told UNDX could not compare their alerts, about a
        comparison that had not happened, because the scan gave up before the filter
        ran.

        Asserted as a completed turn rather than as "not a clarification". The distance
        between the two is the point: the fix is not that a different question gets
        asked, it is that no question is needed, and the alert ends up paused and read
        back from PulseSoc.
        """
        wanted = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        self.alerts(59, symbol="ETH")
        turn = self.say("pause my bitcoin alert")
        card = turn.card or {}
        self.assertEqual(self.contracts.AgentOutcome.VERIFIED_SUCCESS, turn.receipt.status)
        self.assertNotEqual(self.contracts.CardType.CLARIFICATION_REQUIRED,
                            card.get("component"))
        self.assertEqual("paused", self.fx.alert_status(wanted))

    # -- the boundary ------------------------------------------------------------

    def test_a_statement_is_still_a_failure(self) -> None:
        """The edge that keeps this honest.

        "You do not have an alert matching that" is not a question — there is nothing
        the next message can supply that would make it one, and the runtime remembers
        no continuation for it. Reporting it as ``clarification_required`` would put an
        invitation on the account that nobody issued and leave the next unrelated
        number free to accept it. A guard with no boundary would pass every test above
        while doing exactly that, which is why this one is here.
        """
        self.alerts(1, symbol="BTC")
        turn = self.say("pause my dogecoin alert")
        card = turn.card or {}
        self.assertEqual(self.contracts.AgentOutcome.TERMINAL_FAILURE,
                         turn.receipt.status)
        self.assertEqual(self.contracts.CardType.ACTION_FAILURE, card.get("component"))
        self.assertFalse(card.get("needs_answer"))
        self.assertEqual([], card.get("awaiting_fields"))

    def test_a_confirmation_is_not_a_clarification(self) -> None:
        """Both are open questions; only one of them is asking for permission.

        ``AWAITING_USER`` holds both, which is what makes a failure metric correct.
        Collapsing them into one outcome would not: a continuation supplies a field, a
        confirmation supplies consent, and the whole write-guard layer rests on the
        second never being minted by the first.

        The phrasing is not incidental and was corrected after this test failed. "Pause
        my bitcoin alert" against a single alert reaches ``verified_success`` on the
        first turn — ``is_explicit`` reads it as an instruction rather than a
        suggestion, and an explicit instruction to perform a reversible write is not
        asked about twice. Reaching the confirmation therefore requires a sentence that
        is genuinely a suggestion, which is what "could you" supplies. Asserting the
        distinction on a phrasing that never produces a confirmation would have been a
        test of nothing.
        """
        self.alerts(1)
        turn = self.say("could you pause my bitcoin alert")
        self.assertEqual(self.contracts.AgentOutcome.CONFIRMATION_REQUIRED,
                         turn.receipt.status)
        self.assertIn(turn.receipt.status, self.contracts.AgentOutcome.AWAITING_USER)
        self.assertEqual(self.contracts.CardType.ACTION_CONFIRMATION,
                         (turn.card or {}).get("component"))

    # -- properties that must hold across all of them -----------------------------

    def test_no_question_may_claim_the_change_is_done(self) -> None:
        for label, count, text in (("chooser", 2, "pause my bitcoin alert"),
                                   ("clarification", 1, "change my bitcoin alert"),
                                   ("truncated", 60, "pause my bitcoin alert")):
            with self.subTest(question=label):
                self.tearDown()
                self.setUp()
                self.alerts(count)
                turn = self.say(text)
                self.assertFalse(turn.receipt.may_claim_completed)
                self.assertFalse((turn.card or {}).get("verified"))
                self.assertNotIn(turn.receipt.status,
                                 self.contracts.AgentOutcome.COMPLETED)

    def test_a_question_is_still_answerable(self) -> None:
        """The status change must not have cost the thing Batch 6 built.

        Renaming the outcome is only safe if the continuation layer never read the old
        one, and the cheapest way to be sure of that is to answer the question rather
        than to go and check.

        The reply is ``"alert 1"`` and not ``"1"``, which is a finding rather than a
        convenience. A bare ``"1"`` against this chooser is refused: the candidates
        arrive ordered ``[id 2, id 1]``, so ``answer_for_choice`` reads the digit as
        both "the alert whose id is 1" and "the first row shown" — two different rows —
        and its contradiction rule declines rather than guess. That rule is right, and
        the situation it is protecting against is one the runtime creates for itself,
        because it numbers the rows it sends and then refuses the number. Named here,
        carried, and not fixed by this batch: this file is about what a question looks
        like, and that is about what answers one.
        """
        self.alerts(2)
        first = self.say("pause my bitcoin alert")
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         first.receipt.status)
        second = self.say("alert 1")
        self.assertEqual(self.contracts.AgentOutcome.CONFIRMATION_REQUIRED,
                         second.receipt.status)

    def test_a_reply_that_rules_a_row_out_does_not_pause_that_row(self) -> None:
        """Batch 15's defect as a whole turn, because the reading alone understates it.

        ``read_choice`` returning the wrong id sounds like a parsing bug. What it was, end
        to end, is this: three alerts, a chooser, the person types "not the first one",
        and the runtime answers with a confirmation card for the first one. The card's
        message is the generic "I need you to confirm this before I make the change", so
        nothing on screen contradicts the misreading — the person has to notice the row
        themselves, and the reply they just typed says they were trying not to pick it.

        Two assertions, and the second is the one that matters. The turn is a question
        rather than a confirmation, *and* nothing was paused. A card that merely looked
        different while still staging the wrong alert would pass the first and fail the
        second.
        """
        ids = self.alerts(3)
        first = self.say("pause my bitcoin alert")
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         first.receipt.status)
        second = self.say("not the first one")
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         second.receipt.status)
        self.assertIn("rules one out", str((second.card or {}).get("message") or ""))
        for alert_id in ids:
            self.assertEqual("active", self.fx.alert_status(alert_id))

    def test_an_ordering_word_the_list_cannot_answer_still_gets_a_sentence(self) -> None:
        """"The newest one" was the purest silence left after Batch 13.

        It produced ``handled=False``, no card, and the question already burned — so the
        person could not answer again without retyping the request that produced the
        chooser. It reached none of Batch 13's evidence signals: no word matches several
        rows, and there is no number to be out of range.

        It is a reported miss now rather than a reading, and the distinction is the whole
        argument. The rows are drawn active-first then by ``updated_at``, so row 1 is not
        reliably the newest; reading it as row 1 would be a guess that is right often
        enough to be trusted and wrong exactly when the account is interesting. Saying
        the list is not in date order and asking for the number is a sentence the person
        can act on, and it re-arms the question they were answering.
        """
        self.alerts(3)
        self.say("pause my bitcoin alert")
        turn = self.say("the newest one")
        card = turn.card or {}
        self.assertTrue(turn.handled)
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         turn.receipt.status)
        self.assertIn("not listed by date", str(card.get("message") or ""))
        self.assertTrue(card.get("needs_answer"))
        # The rows come back with it, so the number it asks for is on screen to be read.
        self.assertEqual(3, len(card.get("candidates") or card.get("options") or []))

    def test_a_missing_apostrophe_does_not_pause_the_row_it_rules_out(self) -> None:
        """The Batch 15 wrong write, still live, reachable by dropping one character.

        The reading-level test proves "dont pause the first one" comes back as an
        exclusion. This proves what it was doing before that: three alerts, a chooser,
        the person types the sentence their keyboard did not autocorrect, and the
        runtime answers with a confirmation card staging the row they were trying to
        avoid. The card's message is the generic "I need you to confirm this before I
        make the change", so nothing on screen contradicts it — the person has to catch
        the row themselves, having just typed the words that say they do not want it.

        Two assertions and the second carries the test. A card that merely looked
        different while still staging row one would pass the first and fail the second,
        and it is the write that is the harm here, not the card.
        """
        ids = self.alerts(3)
        self.say("pause my bitcoin alert")
        turn = self.say("dont pause the first one")
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         turn.receipt.status)
        self.assertIn("rules one out", str((turn.card or {}).get("message") or ""))
        for alert_id in ids:
            self.assertEqual("active", self.fx.alert_status(alert_id))

    def test_saying_you_cannot_answer_leaves_the_question_open(self) -> None:
        """The honest reply that was punished hardest, end to end.

        "I don't know" was classified exactly as "what is my account health" would have
        been: the turn declined, no card came back, and the pending question burned. The
        numbered rows were still on the person's screen, so the natural next move was to
        go and look and then type a number — and that number now answered nothing,
        because the question it belonged to no longer existed. Two honest messages in a
        row and the conversation was over.

        The recovery is the half of this that cannot be asserted at the reading, so it
        is asserted here: after the re-ask, "2" resolves to a confirmation card for row
        2. That is the property the batch is actually about. A sentence that reads well
        and leaves the continuation burned would pass every assertion but the last two.
        """
        ids = self.alerts(3)
        self.say("pause my bitcoin alert")
        turn = self.say("i don't know")
        card = turn.card or {}
        self.assertTrue(turn.handled)
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         turn.receipt.status)
        message = str(card.get("message") or "")
        self.assertIn("Nothing has changed", message)
        # It tells them where to look, so the sentence has to arrive with the link that
        # makes looking one tap rather than a hunt through the app.
        self.assertIn("alerts", str(card.get("deep_link") or ""))
        self.assertTrue(card.get("needs_answer"))
        self.assertEqual(3, len(card.get("candidates") or []))
        for alert_id in ids:
            self.assertEqual("active", self.fx.alert_status(alert_id))

        after = self.say("2").card or {}
        self.assertEqual(self.contracts.AgentOutcome.CONFIRMATION_REQUIRED,
                         after.get("status"))
        self.assertTrue(str(after.get("resource_label") or ""))

    def test_asking_for_all_of_them_is_refused_rather_than_multiplied(self) -> None:
        """The reply this batch had to be careful about rather than generous with.

        "All of them" is an answer — the person read the rows and told the runtime what
        they wanted — and the wanted thing does not exist. Every alert write in the
        registry takes one ``alert_id``; there is no bulk capability among the eighty.
        So the only two ways to serve this are to fan one phrase out into three writes
        behind a single confirmation card, or to say what UNDX can do and ask for one.

        The first is the exact shape of consent this layer exists to refuse, so the
        assertion that carries this test is the last one: three alerts, still active,
        no matter how the sentence reads. The card is checked for being a question and
        not a confirmation for the same reason — a staged bulk approval would satisfy
        "handled" and "says something sensible" perfectly.
        """
        ids = self.alerts(3)
        self.say("pause my bitcoin alert")
        turn = self.say("all of them")
        card = turn.card or {}
        self.assertTrue(turn.handled)
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         turn.receipt.status)
        self.assertIn("one at a time", str(card.get("message") or ""))
        self.assertIn(card.get("component"), self.contracts.CardType.QUESTIONS)
        self.assertNotEqual(self.contracts.AgentOutcome.CONFIRMATION_REQUIRED,
                            card.get("status"))
        self.assertEqual(3, len(card.get("candidates") or []))
        for alert_id in ids:
            self.assertEqual("active", self.fx.alert_status(alert_id))

    def test_every_question_card_declares_what_it_is_waiting_for(self) -> None:
        """``needs_answer`` predates the card types and is now redundant on purpose.

        It stays because it is the field an older client reads, and because the two
        disagreeing is a defect worth failing on: a question card that says it needs no
        answer, or a failure card that says it does, is a card whose component and whose
        payload describe different situations.
        """
        for label, count, text in (("chooser", 2, "pause my bitcoin alert"),
                                   ("clarification", 1, "change my bitcoin alert"),
                                   ("truncated", 60, "pause my bitcoin alert"),
                                   ("statement", 1, "pause my dogecoin alert")):
            with self.subTest(question=label):
                self.tearDown()
                self.setUp()
                self.alerts(count)
                card = self.say(text).card or {}
                is_question = card.get("component") in self.contracts.CardType.QUESTIONS
                self.assertEqual(is_question, bool(card.get("needs_answer")))
                self.assertEqual(is_question, bool(card.get("awaiting_fields")))

    def test_the_row_that_was_picked_is_the_row_the_confirmation_names(self) -> None:
        """The chooser and the card have to be about visibly the same alert.

        This is the join the whole two-turn conversation rests on. The person reads a
        numbered list, types a number, and is shown an approval; if the approval does
        not repeat the words of the row they picked, the only thing connecting the
        two is their memory of which number they typed. So the words are compared
        directly — the label the card carries has to be the label the chooser drew
        for that row, not merely consistent with it.
        """
        self.alerts(3)
        chooser = (self.say("pause my bitcoin alert").card or {})
        rows = chooser.get("candidates") or []
        self.assertEqual(3, len(rows))
        picked = next(row for row in rows if int(row.get("choice_index") or 0) == 2)

        card = self.say("2").card or {}
        self.assertEqual(self.contracts.AgentOutcome.CONFIRMATION_REQUIRED,
                         card.get("status"))
        label = str(card.get("resource_label") or "")
        self.assertTrue(label, "a confirmation card must name what it changes")
        self.assertEqual(self.runtime.describe_alert(picked), label)
        # And the identifier survives alongside it, since that is what the approval
        # is bound to and what the deep link is built from.
        self.assertEqual(str(picked.get("alert_id")), str(card.get("target")))


class WithdrawnInWords(unittest.TestCase):
    """Batch 19: saying "never mind" to a live approval has to end it.

    The defect this class is written against, stated as a turn rather than as a rule.
    One alert, "pause my bitcoin alert", a confirmation card on screen. The person
    reads it, changes their mind, and types "never mind". The runtime returns
    ``handled=False`` — no card, no sentence, nothing — because "never mind" routes to
    no capability and answers no question, and the two consult paths were the only two
    that existed. So the screen shows an approval that still works, backed by a token
    that stays redeemable for the rest of its five minutes.

    That is worse than it sounds, because the silence is indistinguishable from the
    agent not being switched on. There is no reason for the person to type it twice,
    and the button is right there.

    Both halves are asserted here and the second is the one that matters. A card saying
    "cancelled" over a token that still redeems would pass a shape test and be a lie.
    """

    def setUp(self) -> None:
        self.fx = AgentFixture().start()
        from services import pulse_ai_service, undx_agent_contracts, undx_agent_runtime

        self.contracts = undx_agent_contracts
        self.runtime = undx_agent_runtime
        self.svc = pulse_ai_service
        bootstrap.stub_bot(pulse_ai_service)
        self.svc.ensure_schema(self.fx.cur, self.fx.conn)
        self.fx.commit()

    def tearDown(self) -> None:
        self.fx.stop()

    def say(self, text: str):
        turn = self.runtime.handle(self.fx.cur, user_id=OWNER_ID, text=text,
                                   correlation_id="b19")
        self.fx.commit()
        return turn

    def staged(self) -> tuple[int, str]:
        """One alert, one live approval. Returns the alert id and its token.

        Delete rather than pause, and that is a finding rather than a preference:
        pausing is not consequential enough to require an approval, so it executes on
        the first turn and there is nothing left to withdraw. The action this batch is
        about is by definition one the runtime staged instead of running.
        """
        alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        card = self.say("delete my bitcoin alert").card or {}
        self.assertEqual(self.contracts.AgentOutcome.CONFIRMATION_REQUIRED,
                         card.get("status"))
        token = str(card.get("confirmation_token") or "")
        self.assertTrue(token, "a confirmation card must carry a token to withdraw")
        return alert_id, token

    # -- the defect --------------------------------------------------------------

    def test_declining_in_words_kills_the_token(self) -> None:
        """The half that is a security property rather than a presentation one.

        Redeeming after the withdrawal must not succeed, and the alert must still be
        active. Both are asserted through ``pulse_ai_service`` and the alert service
        rather than through the agent's own card, because a runtime that agreed with
        itself about having cancelled something would prove nothing at all.
        """
        alert_id, token = self.staged()
        self.say("never mind")
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertNotEqual("verified_success", str(result.get("status") or ""))
        self.assertFalse(result.get("ok"), result)
        self.assertEqual("active", self.fx.alert_status(alert_id))

    def test_declining_in_words_is_answered_rather_than_ignored(self) -> None:
        """The half the person can see. Silence was the whole complaint."""
        self.staged()
        turn = self.say("never mind")
        self.assertTrue(turn.handled)
        card = turn.card or {}
        self.assertEqual(self.contracts.CardType.ACTION_CANCELLED, card.get("component"))
        self.assertEqual(self.contracts.AgentOutcome.CANCELLED, card.get("status"))
        self.assertTrue(str(turn.reply or ""))

    def test_a_cancellation_is_neither_a_failure_nor_a_receipt(self) -> None:
        """Batch 11's lesson applied to a new outcome, asserted against the names.

        ``action_failure`` would draw this under "NOT DONE", telling someone who
        successfully changed their mind that something went wrong; a receipt card would
        claim a write that never happened. The assertion is against those specific
        strings for the same reason Batch 11's was: the next name to be reached for
        would reintroduce the defect quietly.
        """
        self.staged()
        card = self.say("never mind").card or {}
        self.assertNotEqual(self.contracts.CardType.ACTION_FAILURE, card.get("component"))
        self.assertNotIn(card.get("component"), self.contracts.CardType.QUESTIONS)
        self.assertFalse(card.get("verified"))

    def test_the_cancellation_names_what_it_called_off(self) -> None:
        """Batch 16's argument, one turn later.

        Someone who has just changed their mind is precisely the person least sure what
        was about to happen, so "cancelled" without a subject is only reassuring if you
        can remember what you staged.
        """
        self.staged()
        card = self.say("never mind").card or {}
        label = str(card.get("resource_label") or "")
        self.assertTrue(label, "a cancellation should name the row it called off")
        self.assertIn(label, str(card.get("message") or ""))

    # -- the guard ---------------------------------------------------------------

    def test_a_correction_is_not_a_withdrawal(self) -> None:
        """"No, make it 95000" changes a number; it does not abandon one.

        The overshoot this batch had to avoid. A substring search for "no" would treat
        every correction as a cancellation, and the person would watch their approval
        disappear because they tried to adjust it. The whole-message anchor is what
        separates the two, and this is the case that proves it does.
        """
        alert_id = self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        self.say("change my bitcoin alert to 91000")
        turn = self.say("no, make it 95000")
        self.assertNotEqual(self.contracts.CardType.ACTION_CANCELLED,
                            (turn.card or {}).get("component"))
        self.assertEqual(90000.0, self.fx.alert_threshold(alert_id))

    def test_a_reply_to_an_open_question_is_not_a_withdrawal(self) -> None:
        """With a chooser on screen, "no" belongs to the chooser.

        This is the ordering that makes bare "no" safe to admit at all. Batch 15 already
        settled what a negation means against a list of rows, and reading it as a
        withdrawal here would take that meaning away — while cancelling an approval that,
        with a question outstanding, does not exist.
        """
        ids = self.alerts_for(3)
        first = self.say("pause my bitcoin alert")
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         first.receipt.status)
        turn = self.say("not the first one")
        self.assertNotEqual(self.contracts.CardType.ACTION_CANCELLED,
                            (turn.card or {}).get("component"))
        for alert_id in ids:
            self.assertEqual("active", self.fx.alert_status(alert_id))

    def test_a_live_question_outranks_a_live_approval(self) -> None:
        """The only turn where both are outstanding, and the ordering that decides it.

        Reachable, and reachable without contrivance: an approval lives for five
        minutes, and asking for something else in the meantime opens a question without
        touching it. So the person has a delete staged *and* a chooser on screen, and
        types "never mind" — which of the two did they mean?

        The question, because that is what they were just looking at and what the
        runtime just asked them. Answering the approval instead would cancel something
        off-screen in response to a reply aimed at something on-screen, and it would
        take away the meaning bare "no" has against a chooser, which Batch 15 settled.

        This is also the only assertion that can see the ordering at all. Every other
        route into ``_withdraw_pending`` is guarded twice over, so the check is invisible
        end to end unless both are live at once — which is exactly this turn.
        """
        alert_id, token = self.staged()
        self.alerts_for(2)  # now three alerts, so the next request is ambiguous
        chooser = self.say("pause my bitcoin alert")
        self.assertEqual(self.contracts.AgentOutcome.CLARIFICATION_REQUIRED,
                         chooser.receipt.status)

        turn = self.say("never mind")
        self.assertNotEqual(self.contracts.CardType.ACTION_CANCELLED,
                            (turn.card or {}).get("component"))
        # And the delete is still staged, because nothing addressed it.
        result = self.svc.confirm_action(OWNER_ID, {"confirmation_token": token})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("deleted", self.fx.alert_status(alert_id))

    def test_withdrawing_with_nothing_staged_stays_a_conversation(self) -> None:
        """"Never mind" with no approval outstanding is small talk, not a card.

        Answering it would be its own kind of wrong: a cancellation card for a write
        nobody staged tells the person something was about to happen to their account.
        """
        self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0)
        turn = self.say("never mind")
        self.assertFalse(turn.handled)
        self.assertIsNone(turn.card)

    def test_a_withdrawal_does_not_reach_another_accounts_approval(self) -> None:
        """Owner scope, asserted on the path that has no bearer token to scope it.

        Every other revoke is addressed by the token itself, which only the holder has.
        This one is addressed by account, so the ``WHERE user_id=?`` is the entire
        boundary and is worth a test of its own.
        """
        alert_id = self.fx.make_alert(OTHER_ID, symbol="BTC", threshold=90000.0)
        staged = self.runtime.handle(self.fx.cur, user_id=OTHER_ID,
                                     text="delete my bitcoin alert", correlation_id="b19")
        self.fx.commit()
        token = str((staged.card or {}).get("confirmation_token") or "")
        self.assertTrue(token)

        self.say("never mind")  # OWNER_ID, who has nothing staged

        result = self.svc.confirm_action(OTHER_ID, {"confirmation_token": token})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("deleted", self.fx.alert_status(alert_id, OTHER_ID))

    def alerts_for(self, count: int) -> list[int]:
        ids = [self.fx.make_alert(OWNER_ID, symbol="BTC", threshold=90000.0 + n)
               for n in range(count)]
        self.fx.commit()
        return ids


class CardContractTests(unittest.TestCase):
    """Properties of the enums themselves, independent of any one turn."""

    def setUp(self) -> None:
        from services import undx_agent_contracts

        self.contracts = undx_agent_contracts

    def test_the_new_card_types_are_declared(self) -> None:
        for name in self.contracts.CardType.QUESTIONS:
            self.assertIn(name, self.contracts.CardType.ALL)

    def test_a_question_is_never_a_completed_outcome(self) -> None:
        self.assertEqual(frozenset(),
                         self.contracts.AgentOutcome.AWAITING_USER
                         & self.contracts.AgentOutcome.COMPLETED)

    def test_no_capability_uses_a_question_card_as_its_result_card(self) -> None:
        """A success card and a question card are not interchangeable in either direction.

        The bug this batch fixed was a question borrowing a result card. This asserts
        the mirror image cannot be configured — a capability whose *success* renders as
        a question would leave the person answering something already done.
        """
        from services import undx_capability_registry

        for capability_id, spec in undx_capability_registry.REGISTRY.items():
            with self.subTest(capability=capability_id):
                self.assertNotIn(spec.result_card, self.contracts.CardType.QUESTIONS)

    def test_every_card_type_the_runtime_can_choose_is_canonical(self) -> None:
        from services import undx_agent_runtime, undx_capability_registry

        spec = undx_capability_registry.REGISTRY["crypto.alerts.pause"]
        for outcome in sorted(self.contracts.AgentOutcome.ALL):
            with self.subTest(outcome=outcome):
                chosen = undx_agent_runtime._card_type(spec, outcome)
                self.assertIn(chosen, self.contracts.CardType.ALL)

    def test_a_clarification_never_falls_through_to_the_success_card(self) -> None:
        """The fall-through in ``_card_type`` is what produced the original defect."""
        from services import undx_agent_runtime, undx_capability_registry

        for capability_id, spec in undx_capability_registry.REGISTRY.items():
            with self.subTest(capability=capability_id):
                chosen = undx_agent_runtime._card_type(
                    spec, self.contracts.AgentOutcome.CLARIFICATION_REQUIRED)
                self.assertEqual(self.contracts.CardType.CLARIFICATION_REQUIRED, chosen)


if __name__ == "__main__":
    unittest.main()
