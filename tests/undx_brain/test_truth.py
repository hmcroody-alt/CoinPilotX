"""The two-axis truth model, tested at the points where collapsing it would be easy.

Every test here corresponds to a specific wrong answer a user could be given:

* told their alert is paused because the corpus documents a pause endpoint
* told an action succeeded because the service accepted the request
* given a confident answer built on a file nobody has ever run

The model's job is to make each of those a type error rather than a judgement call.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain.truth import (  # noqa: E402
    EvidenceState,
    EvidenceTransitionError,
    TrustLevel,
    hedge_for,
    may_claim_live_state,
    may_explain_product,
    meets,
    rank,
    state_requires_disclosure,
    state_supports_completion_claim,
    transition,
    weakest,
)


class TrustNeverBecomesEvidence(unittest.TestCase):
    """The single rule the package exists to protect."""

    def test_no_trust_level_licenses_a_claim_about_account_state(self):
        # Exhaustive on purpose. A future contributor adding RUNTIME_CANONICAL_PLUS and
        # wiring it to True would have to delete this loop to do it.
        for level in TrustLevel:
            with self.subTest(level=level):
                self.assertFalse(
                    may_claim_live_state(level),
                    f"{level.value} must not license a claim about a user's account",
                )

    def test_the_highest_trust_level_still_cannot(self):
        self.assertFalse(may_claim_live_state(TrustLevel.RUNTIME_CANONICAL))

    def test_unknown_trust_strings_fail_closed(self):
        # A corpus record with a garbled category must not become the most trusted thing
        # in the system by accident.
        self.assertEqual(rank("not_a_real_level"), rank(TrustLevel.BLOCKED))
        self.assertFalse(may_explain_product("not_a_real_level"))
        self.assertFalse(meets("not_a_real_level", TrustLevel.SOURCE_MAPPED))

    def test_rank_does_not_raise_on_garbage(self):
        for value in ("", None, 0, [], "SOURCE_MAPPED "):
            with self.subTest(value=value):
                self.assertIsInstance(rank(value), int)


class TrustOrdering(unittest.TestCase):
    def test_levels_are_ordered_from_blocked_to_canonical(self):
        ordered = [
            TrustLevel.BLOCKED,
            TrustLevel.DEPRECATED,
            TrustLevel.SOURCE_DISCOVERED,
            TrustLevel.SOURCE_MAPPED,
            TrustLevel.DOCUMENTED,
            TrustLevel.TESTED,
            TrustLevel.LIVE_VERIFIED,
            TrustLevel.RUNTIME_CANONICAL,
        ]
        self.assertEqual(sorted(ordered, key=rank), ordered)

    def test_blocked_meets_nothing_including_itself(self):
        for minimum in TrustLevel:
            with self.subTest(minimum=minimum):
                self.assertFalse(meets(TrustLevel.BLOCKED, minimum))

    def test_a_level_meets_itself(self):
        for level in TrustLevel:
            if level is TrustLevel.BLOCKED:
                continue
            with self.subTest(level=level):
                self.assertTrue(meets(level, level))

    def test_every_level_carries_a_hedge(self):
        for level in TrustLevel:
            with self.subTest(level=level):
                self.assertTrue(hedge_for(level).strip())


class EvidenceTransitions(unittest.TestCase):
    """Only a verified read-back licenses "it is done"."""

    def test_verified_success_is_reachable_only_from_executed(self):
        sources = [s for s in EvidenceState if _can(s, EvidenceState.VERIFIED_SUCCESS)]
        self.assertEqual(sources, [EvidenceState.EXECUTED])

    def test_executed_alone_does_not_support_a_completion_claim(self):
        # "The service accepted the mutation" is a statement about the request.
        # "Your alert is paused" is a statement about the alert. This is the seam.
        self.assertFalse(state_supports_completion_claim(EvidenceState.EXECUTED))
        self.assertTrue(state_supports_completion_claim(EvidenceState.VERIFIED_SUCCESS))

    def test_exactly_one_state_supports_a_completion_claim(self):
        supporting = [s for s in EvidenceState if state_supports_completion_claim(s)]
        self.assertEqual(supporting, [EvidenceState.VERIFIED_SUCCESS])

    def test_terminal_states_are_terminal(self):
        for terminal in (EvidenceState.VERIFIED_SUCCESS, EvidenceState.VERIFIED_FAILURE):
            for target in EvidenceState:
                with self.subTest(terminal=terminal, target=target):
                    with self.assertRaises(EvidenceTransitionError):
                        transition(terminal, target)

    def test_an_illegal_transition_raises_rather_than_returning_falsy(self):
        # A caller that ignores a return value would silently proceed. An exception
        # cannot be ignored by accident.
        with self.assertRaises(EvidenceTransitionError):
            transition(EvidenceState.PROPOSED, EvidenceState.VERIFIED_SUCCESS)

    def test_a_legal_transition_returns_the_target(self):
        self.assertIs(
            transition(EvidenceState.EXECUTED, EvidenceState.VERIFIED_SUCCESS),
            EvidenceState.VERIFIED_SUCCESS,
        )

    def test_degraded_and_unknown_require_disclosure(self):
        self.assertTrue(state_requires_disclosure(EvidenceState.DEGRADED))
        self.assertTrue(state_requires_disclosure(EvidenceState.UNKNOWN))
        self.assertFalse(state_requires_disclosure(EvidenceState.VERIFIED_SUCCESS))


class WeakestIsAFloor(unittest.TestCase):
    def test_weakest_returns_the_floor_not_an_average(self):
        self.assertIs(
            weakest([EvidenceState.VERIFIED_SUCCESS, EvidenceState.DEGRADED]),
            EvidenceState.DEGRADED,
        )

    def test_empty_is_unknown_not_success(self):
        self.assertIs(weakest([]), EvidenceState.UNKNOWN)

    def test_one_degraded_source_pulls_the_whole_answer_down(self):
        states = [EvidenceState.VERIFIED_SUCCESS] * 9 + [EvidenceState.DEGRADED]
        self.assertFalse(state_supports_completion_claim(weakest(states)))


def _can(source: EvidenceState, target: EvidenceState) -> bool:
    try:
        transition(source, target)
    except EvidenceTransitionError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()
