"""One pair of fields, tested from both directions.

The dangerous pair is an outcome of ``verified_success`` carried alongside a
verification that is pending, failed or absent. Every false report this system could
make about somebody's account passes through it. So most of this file is that pair,
enumerated, with the assertion that the completion claim is refused each time.

The safe-looking direction is checked too: a ``verified`` verdict attached to a turn
that executed nothing must not manufacture a success. Verification is a veto, not a
promotion.

The mapping is walked against ``AgentOutcome.ALL`` rather than a list written here, so
a tenth outcome added to the contracts fails this file rather than falling through to a
default nobody chose.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap  # noqa: E402

bootstrap.install()

from services.undx_agent_contracts import (  # noqa: E402
    AgentOutcome,
    VerificationResult,
    VerificationState,
)
from services.undx_brain import evidence as e  # noqa: E402
from services.undx_brain.truth import EvidenceState  # noqa: E402


#: Every verdict that is not "the read-back confirmed it".
UNCONFIRMED = (
    VerificationState.PENDING,
    VerificationState.FAILED,
    VerificationState.IMPOSSIBLE,
)


def verification(state: str) -> VerificationResult:
    return VerificationResult(state=state, expected="paused", observed=None)


class ADoneClaimNeedsAReadBack(unittest.TestCase):
    """The property the whole module exists for."""

    def test_a_success_outcome_with_an_unconfirmed_read_back_may_not_say_done(self):
        for state in UNCONFIRMED:
            with self.subTest(verification=state):
                assessment = e.derive(AgentOutcome.VERIFIED_SUCCESS, verification(state))
                self.assertFalse(
                    assessment.may_claim_done,
                    f"a {state} verification licensed a completion claim",
                )
                self.assertIsNot(assessment.state, EvidenceState.VERIFIED_SUCCESS)

    def test_a_success_outcome_with_no_verification_at_all_may_not_say_done(self):
        # ``None`` is the case that looks like an omission and is actually the answer:
        # no read-back happened, so nothing confirmed the change.
        self.assertFalse(e.may_say_done(AgentOutcome.VERIFIED_SUCCESS, None))
        self.assertFalse(e.may_say_done(AgentOutcome.VERIFIED_SUCCESS))

    def test_a_confirmed_read_back_does_license_the_claim(self):
        # Without this the module could pass everything else by always refusing, and
        # UNDX would never be able to tell anyone anything worked.
        assessment = e.derive(
            AgentOutcome.VERIFIED_SUCCESS, verification(VerificationState.VERIFIED)
        )
        self.assertIs(assessment.state, EvidenceState.VERIFIED_SUCCESS)
        self.assertTrue(assessment.may_claim_done)
        self.assertTrue(assessment)

    def test_exactly_one_state_ever_licenses_the_claim(self):
        licensed = set()
        for outcome in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL) + [""]:
                assessment = e.derive(outcome, verdict or None)
                if assessment.may_claim_done:
                    licensed.add(assessment.state)
        self.assertEqual(licensed, {EvidenceState.VERIFIED_SUCCESS})

    def test_the_only_route_to_a_done_claim_is_success_plus_verified(self):
        allowed = {
            (outcome, verdict)
            for outcome in sorted(AgentOutcome.ALL)
            for verdict in sorted(VerificationState.ALL) + [""]
            if e.may_say_done(outcome, verdict or None)
        }
        self.assertEqual(
            allowed,
            {(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)},
            "some other pair became a completion claim",
        )


class TheDowngradeIsVisible(unittest.TestCase):
    """A silent downgrade is a downgrade nobody investigates."""

    def test_the_disagreement_is_named(self):
        assessment = e.derive(
            AgentOutcome.VERIFIED_SUCCESS, verification(VerificationState.PENDING)
        )
        self.assertTrue(assessment.contradiction)
        self.assertIn("verified success", assessment.contradiction)
        self.assertIs(assessment.downgraded_from, EvidenceState.VERIFIED_SUCCESS)

    def test_the_executed_fact_survives_the_downgrade(self):
        # Something really was sent. Reporting nothing happened would be a second
        # falsehood pointing the other way.
        assessment = e.derive(
            AgentOutcome.VERIFIED_SUCCESS, verification(VerificationState.PENDING)
        )
        self.assertIs(assessment.state, EvidenceState.EXECUTED)
        self.assertIn("sent rather than as done", assessment.reason)

    def test_a_failed_read_back_is_not_merely_unconfirmed(self):
        # "I could not confirm it" and "I looked and it was not there" are different
        # findings, and collapsing them loses the one worth acting on.
        assessment = e.derive(
            AgentOutcome.VERIFIED_SUCCESS, verification(VerificationState.FAILED)
        )
        self.assertIs(assessment.state, EvidenceState.VERIFIED_FAILURE)

    def test_agreement_produces_no_contradiction(self):
        for outcome, verdict in (
            (AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED),
            (AgentOutcome.ACCEPTED_UNVERIFIED, VerificationState.PENDING),
            (AgentOutcome.CONFIRMATION_REQUIRED, VerificationState.PENDING),
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(e.contradiction(outcome, verdict), "")

    def test_contradiction_agrees_with_derive(self):
        for outcome in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                with self.subTest(outcome=outcome, verdict=verdict):
                    self.assertEqual(
                        e.contradiction(outcome, verdict),
                        e.derive(outcome, verdict).contradiction,
                    )


class VerificationIsAVetoNotAPromotion(unittest.TestCase):
    def test_a_verified_verdict_does_not_rescue_a_denied_request(self):
        assessment = e.derive(AgentOutcome.PERMISSION_DENIED, VerificationState.VERIFIED)
        self.assertFalse(assessment.may_claim_done)
        self.assertIs(assessment.state, EvidenceState.UNKNOWN)
        self.assertTrue(assessment.contradiction)

    def test_a_verified_verdict_does_not_turn_a_question_into_a_change(self):
        for outcome in (AgentOutcome.CLARIFICATION_REQUIRED, AgentOutcome.CONFIRMATION_REQUIRED):
            with self.subTest(outcome=outcome):
                self.assertFalse(e.may_say_done(outcome, VerificationState.VERIFIED))

    def test_a_verified_verdict_does_not_undo_a_cancellation(self):
        # The person said no. A read-back that finds the old value is not a success.
        assessment = e.derive(AgentOutcome.CANCELLED, VerificationState.VERIFIED)
        self.assertIs(assessment.state, EvidenceState.PROPOSED)
        self.assertFalse(assessment.may_claim_done)

    def test_the_promotion_attempt_is_recorded_in_the_notes(self):
        notes = e.derive(AgentOutcome.PERMISSION_DENIED, VerificationState.VERIFIED).notes
        self.assertTrue(any("not treated as evidence" in note for note in notes))


class EveryOutcomeIsHandledDeliberately(unittest.TestCase):
    def test_the_table_covers_every_outcome_the_contracts_declare(self):
        self.assertEqual(set(e.OUTCOME_FAMILY), set(AgentOutcome.ALL))

    def test_an_unknown_outcome_is_unknown_rather_than_defaulted(self):
        for junk in ("succeeded", "", None, 7, object()):
            with self.subTest(junk=junk):
                assessment = e.derive(junk, VerificationState.VERIFIED)
                self.assertIs(assessment.state, EvidenceState.UNKNOWN)
                self.assertFalse(assessment.may_claim_done)

    def test_a_failure_outcome_never_lands_in_an_executed_state(self):
        # Reporting a failed turn as EXECUTED would put it one legal transition away
        # from VERIFIED_SUCCESS.
        for outcome in (AgentOutcome.TERMINAL_FAILURE, AgentOutcome.RECOVERABLE_FAILURE,
                        AgentOutcome.PERMISSION_DENIED, AgentOutcome.UNSUPPORTED_CAPABILITY):
            with self.subTest(outcome=outcome):
                for verdict in sorted(VerificationState.ALL):
                    self.assertIsNot(
                        e.derive(outcome, verdict).state, EvidenceState.EXECUTED
                    )

    def test_an_accepted_unverified_outcome_is_executed_and_discloses(self):
        assessment = e.derive(
            AgentOutcome.ACCEPTED_UNVERIFIED, verification(VerificationState.PENDING)
        )
        self.assertIs(assessment.state, EvidenceState.EXECUTED)
        self.assertTrue(assessment.requires_disclosure)
        self.assertFalse(assessment.contradiction)

    def test_every_derived_state_is_a_real_evidence_state(self):
        for outcome in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                with self.subTest(outcome=outcome, verdict=verdict):
                    self.assertIsInstance(e.derive(outcome, verdict).state, EvidenceState)


class NothingCanTalkItsWayIntoAClaim(unittest.TestCase):
    def test_a_truthy_is_verified_without_a_state_proves_nothing(self):
        # The one thing this module must not accept: a claim of verification from an
        # object that cannot show its verdict.
        class Liar:
            is_verified = True

        self.assertFalse(e.may_say_done(AgentOutcome.VERIFIED_SUCCESS, Liar()))

    def test_a_verification_shaped_string_still_has_to_say_verified(self):
        for text in ("VERIFIED_SUCCESS", "true", "yes", "ok", "verified!"):
            with self.subTest(text=text):
                self.assertFalse(e.may_say_done(AgentOutcome.VERIFIED_SUCCESS, text))

    def test_case_and_padding_do_not_change_the_answer(self):
        self.assertTrue(e.may_say_done("  Verified_Success  ", "  VERIFIED  "))

    def test_nothing_raises_on_junk(self):
        for outcome in (None, 0, [], object(), "x"):
            for verdict in (None, 0, [], object(), "x"):
                with self.subTest(outcome=type(outcome).__name__, verdict=type(verdict).__name__):
                    self.assertIsInstance(e.derive(outcome, verdict), e.Assessment)


class AReadIsNotAChange(unittest.TestCase):
    """The gateway reaches ``verified_success`` for a read by the read working.

    There is nothing to read back on a lookup, so the same outcome value means
    something different depending on whether a mutation was attempted. Assessing a
    successful read as an unconfirmed write would be wrong in the safe direction, and
    still wrong.
    """

    def test_a_successful_read_is_retrieved_and_claims_nothing(self):
        assessment = e.derive(
            AgentOutcome.VERIFIED_SUCCESS, VerificationState.IMPOSSIBLE, is_write=False
        )
        self.assertIs(assessment.state, EvidenceState.RETRIEVED)
        self.assertFalse(assessment.may_claim_done)
        self.assertFalse(assessment.contradiction)

    def test_a_degraded_read_is_degraded_and_discloses(self):
        assessment = e.derive(
            AgentOutcome.ACCEPTED_UNVERIFIED, VerificationState.IMPOSSIBLE, is_write=False
        )
        self.assertIs(assessment.state, EvidenceState.DEGRADED)
        self.assertTrue(assessment.requires_disclosure)

    def test_no_read_can_ever_say_done(self):
        for outcome in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                with self.subTest(outcome=outcome, verdict=verdict):
                    self.assertFalse(e.may_say_done(outcome, verdict, is_write=False))

    def test_the_default_is_the_stricter_reading(self):
        # A caller that does not say which it ran gets held to the write rules, where
        # an unconfirmed success is downgraded rather than believed.
        self.assertIs(
            e.derive(AgentOutcome.VERIFIED_SUCCESS, VerificationState.PENDING).state,
            EvidenceState.EXECUTED,
        )


class ItAgreesWithTheGatewayThatProducesTheOutcomes(unittest.TestCase):
    """Cross-check against the real thing rather than against my model of it.

    Everything above tests ``derive`` against pairs written by hand. If the gateway
    cannot actually produce a pair, the test proves nothing; if it produces one nobody
    wrote down, the test misses it. So this class enumerates what the gateway really
    emits and asserts the assessment over each.
    """

    def _gateway_outcomes(self):
        from services import undx_tool_gateway as gw
        from services.undx_agent_contracts import ToolResult

        for is_write in (True, False):
            for ok in (True, False):
                for retryable in (True, False):
                    for degraded in ((), ("prices",)):
                        for verdict in sorted(VerificationState.ALL):
                            result = ToolResult(
                                ok=ok, tool_name="t", capability_id="c",
                                retryable=retryable,
                                degraded_sources=list(degraded),
                            )
                            spec = _FakeSpec(is_write)
                            status = gw._status_for(spec, result, verification(verdict))
                            yield is_write, status, verdict

    def test_every_pair_the_gateway_emits_is_assessable(self):
        seen = 0
        for is_write, status, verdict in self._gateway_outcomes():
            seen += 1
            with self.subTest(is_write=is_write, status=status, verdict=verdict):
                assessment = e.derive(status, verdict, is_write=is_write)
                self.assertIsInstance(assessment.state, EvidenceState)
        self.assertGreater(seen, 20, "the enumeration collapsed to almost nothing")

    def test_the_gateway_never_produces_a_done_claim_without_a_read_back(self):
        # The property stated over the real producer: if the assessment says done, the
        # gateway must have had a verified read-back of a write in hand.
        for is_write, status, verdict in self._gateway_outcomes():
            if not e.may_say_done(status, verdict, is_write=is_write):
                continue
            with self.subTest(status=status, verdict=verdict):
                self.assertTrue(is_write)
                self.assertEqual(verdict, VerificationState.VERIFIED)

    def test_a_verified_write_does_reach_a_done_claim_through_the_gateway(self):
        # The positive case, so the test above cannot pass by nothing ever qualifying.
        qualifying = [
            (w, s, v) for w, s, v in self._gateway_outcomes()
            if e.may_say_done(s, v, is_write=w)
        ]
        self.assertTrue(qualifying, "no gateway path reaches a completion claim at all")


class _FakeSpec:
    """The two attributes ``_status_for`` reads. Not a CapabilitySpec: building a real
    one drags in a registry entry and would test the registry instead."""

    def __init__(self, is_write: bool) -> None:
        self.is_write = is_write
        self.capability_id = "c"
        self.description = "d"


def _receipt(status: str, verification_state: str, capability_id: str = "crypto.alerts.pause"):
    """A settled receipt carrying exactly the two fields both derivations read."""
    from services.undx_agent_contracts import AgentReceipt

    return AgentReceipt(
        task_id="t", request_id="r", capability_id=capability_id, action="a",
        status=status, owner_user_id=7, verification_state=verification_state,
        user_explanation="ok",
    )


class TheGatewayActuallyConsultsThisModule(unittest.TestCase):
    """The wiring, not the mapping. Everything above passes with nothing calling it.

    That was the whole of the Foundation gap: ``derive`` was correct, complete and
    unreached, so a real turn's completion claim still rested on the receipt's own rule
    and this module could have been deleted without changing a single answer given to a
    person. These tests fail if the call site goes away.
    """

    def _gw(self, status, verification_state, *, is_write=True):
        """Not ``_outcome``: :class:`unittest.TestCase` already owns that attribute, and
        shadowing it replaces the runner's own result object with a bound method."""
        from services import undx_tool_gateway

        return undx_tool_gateway.GatewayOutcome(
            _receipt(status, verification_state), is_write=is_write)

    def test_the_gateway_outcome_produces_an_assessment_at_all(self):
        found = self._gw(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED).assessment
        self.assertIsInstance(found, e.Assessment)
        self.assertIs(found.state, EvidenceState.VERIFIED_SUCCESS)

    def test_the_write_that_verified_may_still_be_claimed(self):
        """The narrowing has to leave the true claims alone or it is just a mute button.

        A safety check that refuses everything is trivially safe and useless. This is the
        one path that must survive it.
        """
        outcome = self._gw(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)
        self.assertTrue(outcome.succeeded)
        self.assertTrue(outcome.may_claim_done)

    def test_the_dangerous_pair_is_refused_by_both_halves(self):
        for verdict in UNCONFIRMED:
            with self.subTest(verdict=verdict):
                outcome = self._gw(AgentOutcome.VERIFIED_SUCCESS, verdict)
                self.assertFalse(outcome.succeeded)
                self.assertFalse(outcome.may_claim_done)
                self.assertTrue(
                    outcome.assessment.contradiction,
                    "the outcome claimed success and the read-back did not support it; "
                    "that disagreement must be named, not merely resolved",
                )

    def test_the_two_derivations_never_disagree_on_a_write(self):
        """Every reachable pair, both readings, one assertion.

        ``AgentReceipt.may_claim_completed`` and ``evidence.derive`` encode the same rule
        in code written at different times for different reasons. Enumerating the whole
        product is cheap and it is the only way to know they have not drifted apart in
        some corner nobody exercises by hand.
        """
        for status in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                with self.subTest(status=status, verification=verdict):
                    outcome = self._gw(status, verdict, is_write=True)
                    self.assertEqual(
                        outcome.receipt.may_claim_completed,
                        outcome.assessment.may_claim_done,
                        f"the receipt and the evidence module disagree on "
                        f"({status}, {verdict}) for a write; one of them has drifted",
                    )

    def test_a_read_is_the_one_place_they_are_supposed_to_disagree(self):
        """And the disagreement runs the safe way.

        A lookup that verified perfectly satisfies ``may_claim_completed`` — the status is
        ``verified_success`` and a read-back confirmed the value — and completed nothing
        the person could be told about. ``derive`` calls that ``RETRIEVED``. The
        conjunction takes the narrower reading, which is the correct one: there is no
        change to announce.
        """
        outcome = self._gw(
            AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED, is_write=False)
        self.assertTrue(outcome.receipt.may_claim_completed)
        self.assertIs(outcome.assessment.state, EvidenceState.RETRIEVED)
        self.assertFalse(outcome.may_claim_done)

    def test_the_composition_can_only_narrow_and_never_widen(self):
        """The property that makes this safe to run unconditionally rather than behind a flag.

        A default-off flag on a check that can only remove claims would leave it unreached
        in every environment that matters, which is the same as not having written it. The
        licence for that is this assertion: across every reachable pair and both write
        readings, ``may_claim_done`` is never true where the receipt alone was false. A
        defect in ``derive`` can cost a true claim; it cannot buy a false one.
        """
        for status in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                for is_write in (True, False):
                    with self.subTest(status=status, verification=verdict, is_write=is_write):
                        outcome = self._gw(status, verdict, is_write=is_write)
                        if outcome.may_claim_done:
                            self.assertTrue(
                                outcome.receipt.may_claim_completed,
                                "the composed answer allowed a claim the receipt refused; "
                                "the conjunction is supposed to be incapable of that",
                            )

    def test_it_reads_the_receipts_verdict_when_no_verification_object_came_back(self):
        """Both derivations must be looking at the same two facts or the comparison is noise.

        Several settled paths carry no ``VerificationResult`` object — an idempotent replay
        holds the earlier operation's verdict on the receipt, a refusal holds ``impossible``
        — and reading ``None`` there would have this module assessing a different pair than
        ``may_claim_completed`` assesses. Every one of those turns would then log a
        divergence that is really just two functions being handed different inputs.
        """
        outcome = self._gw(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)
        self.assertIsNone(outcome.verification)
        self.assertIs(outcome.assessment.state, EvidenceState.VERIFIED_SUCCESS)
        self.assertEqual(outcome.assessment.contradiction, "")

    def test_a_gateway_outcome_defaults_to_the_stricter_reading(self):
        from services import undx_tool_gateway

        outcome = undx_tool_gateway.GatewayOutcome(
            _receipt(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED))
        self.assertTrue(outcome.is_write,
                        "a caller that did not say what it ran must be held to the write "
                        "rules; assessing a mutation as a lookup is the error that ends "
                        "with somebody being told a change happened")

    def test_a_broken_brain_leaves_the_gateway_exactly_where_it_was(self):
        """Degradation, not collapse, and specifically not a widened claim.

        If ``derive`` cannot be reached the gateway falls back to the receipt alone — which
        is where the whole system already stood before this batch — rather than raising or,
        far worse, treating the missing second opinion as agreement.
        """
        def explode(*_args, **_kwargs):
            raise RuntimeError("brain down")

        outcome = self._gw(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)
        original = e.derive
        try:
            e.derive = explode
            self.assertIsNone(outcome.assessment)
            # Falls back to the receipt, which still says yes here...
            self.assertTrue(outcome.may_claim_done)
            # ...and still says no where it always did. The fallback is the old behaviour,
            # not a bypass.
            self.assertFalse(
                self._gw(AgentOutcome.VERIFIED_SUCCESS, VerificationState.PENDING)
                .may_claim_done)
        finally:
            e.derive = original
        self.assertIsNotNone(outcome.assessment)

    def test_succeeded_and_may_claim_done_are_deliberately_different_questions(self):
        """Collapsing these two names is how "the lookup worked" becomes "your change is done"."""
        read = self._gw(
            AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED, is_write=False)
        self.assertTrue(read.succeeded)
        self.assertFalse(read.may_claim_done)


class TheCardCarriesTheAssessment(unittest.TestCase):
    """The responder half. The gateway settling through ``derive`` is only half the gap.

    The entry named both: the gateway had to settle through ``derive`` *and* the response
    layer had to read the resulting ``Assessment``. A card that still reports only
    ``verified`` leaves every client free to re-derive "done" from the narrower fact and
    reach a different answer than the one the gateway enforced.
    """

    def _card(self, status, verification_state, *, is_write=True):
        from services import undx_agent_runtime, undx_tool_gateway

        spec = _FakeSpec(is_write)
        # ``build_card`` reads two attributes ``_status_for`` does not. Set here rather
        # than folded into ``_FakeSpec`` so the older helper keeps describing exactly the
        # surface its own tests rely on.
        spec.result_card = "action_result"
        spec.risk = "medium"
        outcome = undx_tool_gateway.GatewayOutcome(
            _receipt(status, verification_state), is_write=is_write)
        return undx_agent_runtime.build_card(spec, outcome)

    def test_the_card_reports_the_evidence_state(self):
        card = self._card(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)
        self.assertEqual(card["evidence_state"], EvidenceState.VERIFIED_SUCCESS.value)
        self.assertTrue(card["may_claim_done"])

    def test_an_unconfirmed_write_reaches_the_client_as_sent_and_not_as_done(self):
        card = self._card(AgentOutcome.VERIFIED_SUCCESS, VerificationState.PENDING)
        self.assertEqual(card["evidence_state"], EvidenceState.EXECUTED.value)
        self.assertFalse(card["may_claim_done"])
        self.assertFalse(card["verified"])
        self.assertTrue(card["requires_disclosure"])

    def test_the_disagreement_travels_only_when_there_was_one(self):
        """An always-present field that is usually empty stops being read."""
        clean = self._card(AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED)
        self.assertNotIn("evidence_contradiction", clean)
        conflicted = self._card(AgentOutcome.VERIFIED_SUCCESS, VerificationState.PENDING)
        self.assertIn("evidence_contradiction", conflicted)
        self.assertIn("verification", conflicted["evidence_contradiction"])

    def test_a_verified_read_is_verified_and_still_not_done(self):
        """The two card fields answer two different questions and must be allowed to differ."""
        card = self._card(
            AgentOutcome.VERIFIED_SUCCESS, VerificationState.VERIFIED, is_write=False)
        self.assertTrue(card["verified"])
        self.assertFalse(card["may_claim_done"])
        self.assertEqual(card["evidence_state"], EvidenceState.RETRIEVED.value)

    def test_the_card_stays_json_serialisable(self):
        import json

        card = self._card(AgentOutcome.VERIFIED_SUCCESS, VerificationState.PENDING)
        json.loads(json.dumps(card))

    def test_no_card_ever_says_done_where_the_receipt_refused(self):
        for status in sorted(AgentOutcome.ALL):
            for verdict in sorted(VerificationState.ALL):
                with self.subTest(status=status, verification=verdict):
                    card = self._card(status, verdict)
                    if card["may_claim_done"]:
                        self.assertEqual(status, AgentOutcome.VERIFIED_SUCCESS)
                        self.assertEqual(verdict, VerificationState.VERIFIED)


class TheFoundationMapNamesThisModule(unittest.TestCase):
    def test_evidence_state_machine_names_the_bridge(self):
        from services.undx_brain import foundation

        item = foundation.by_key("evidence_state_machine")
        self.assertIsNotNone(item)
        self.assertIn(("services.undx_brain.evidence", "derive"), item.owners)
        self.assertNotIn("nothing writes to it yet", item.gap)

    def test_the_entry_no_longer_claims_nothing_calls_it(self):
        """A withdrawn claim is a promise about the rest of the codebase, so pin it.

        The gap said "nothing calls it on the live path" for as long as that was true. It
        is the kind of sentence that rots quietly in either direction — the wiring gets
        reverted, or the wiring grows past what the entry describes — so both the
        withdrawal and the named call site are held here.
        """
        from services.undx_brain import foundation

        gap = foundation.by_key("evidence_state_machine").gap
        self.assertNotIn("nothing calls it on the live path", gap)
        self.assertIn("no longer true", gap)
        self.assertIn("GatewayOutcome.assessment", gap)
        self.assertIn("build_card", gap)

    def test_the_entry_is_honest_about_what_is_still_missing(self):
        """It is still PARTIAL, and the reasons have to be the real ones."""
        from services.undx_brain import foundation

        item = foundation.by_key("evidence_state_machine")
        self.assertEqual(item.ownership, foundation.Ownership.PARTIAL)
        self.assertIn("_compose_response", item.gap)
        self.assertIn("carried and unread", item.gap)

    def test_the_call_sites_the_entry_names_are_real(self):
        from services import undx_agent_runtime, undx_tool_gateway
        from services.undx_brain import foundation

        item = foundation.by_key("evidence_state_machine")
        self.assertIn(("services.undx_tool_gateway", "GatewayOutcome"), item.owners)
        self.assertIn(("services.undx_agent_runtime", "build_card"), item.owners)
        self.assertTrue(hasattr(undx_tool_gateway.GatewayOutcome, "assessment"))
        self.assertTrue(hasattr(undx_tool_gateway.GatewayOutcome, "may_claim_done"))
        self.assertTrue(callable(undx_agent_runtime.build_card))


if __name__ == "__main__":
    unittest.main()
