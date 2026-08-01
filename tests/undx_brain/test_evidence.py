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


class TheFoundationMapNamesThisModule(unittest.TestCase):
    def test_evidence_state_machine_names_the_bridge(self):
        from services.undx_brain import foundation

        item = foundation.by_key("evidence_state_machine")
        self.assertIsNotNone(item)
        self.assertIn(("services.undx_brain.evidence", "derive"), item.owners)
        self.assertNotIn("nothing writes to it yet", item.gap)


if __name__ == "__main__":
    unittest.main()
