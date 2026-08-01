"""A write is described before it happens, and the description is checked afterwards.

The test carrying the most weight is
:meth:`TheExistingSimulatorDoesNotDiscriminate.test_simulate_operation_answers_identically_for_opposite_cases`,
because it pins the behaviour this module exists to replace rather than the behaviour it
adds. It runs the real :func:`services.undx_architecture.simulate_operation` against a
call that can be reversed by negating one boolean and a call that destroys a row with no
undo at all, and shows the two answers are identical. If that ever stops being true, the
argument in this module's docstring has changed and the docstring is stale — which is
exactly the case a test should catch.

Everything else divides in three. The *derivation* tests check that a prediction is
different for capabilities that differ in the registry, and specifically that the four
:class:`Reversal` outcomes each fall out of real declared data rather than a hardcoded
table. The *honesty* tests check the two refusals: a value that cannot be derived is
named as implied rather than guessed, and a call whose undo needs an id that does not
exist yet is not called reversible. The *falsifiability* tests check that
:func:`~services.undx_brain.prediction.check` can actually fail — a prediction nothing
can contradict is not a prediction, and the test suite has to demonstrate the
contradiction path rather than assert it exists.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import undx_architecture as architecture  # noqa: E402
from services import undx_capability_registry as registry  # noqa: E402
from services.undx_agent_contracts import ConfirmationPolicy, RiskLevel  # noqa: E402
from services.undx_brain import config as brain_config  # noqa: E402
from services.undx_brain import prediction as p  # noqa: E402

#: The Brain on and prediction on. Both are needed: the master switch alone must not turn
#: this on, and a test that set only the second would pass against a module that had
#: forgotten to read the first.
ON = {"UNDX_BRAIN_ENABLED": "1", "UNDX_BRAIN_PREDICTION_ENABLED": "1"}

#: One call per reversal outcome, chosen because the registry genuinely classifies them
#: differently — not because the module was written to sort them this way.
SET_SAVED = ("saved.post.set", {"post_id": "9", "saved": True})
CREATE_ALERT = ("crypto.alerts.create", {"symbol": "BTC", "condition": "above", "threshold": 50000})
UPDATE_ALERT = ("crypto.alerts.update", {"alert_id": "7", "threshold": 60000, "condition": "below"})
DELETE_ALERT = ("crypto.alerts.delete", {"alert_id": "7"})


class TheExistingSimulatorDoesNotDiscriminate(unittest.TestCase):
    """What the module replaces, pinned so the claim about it cannot go stale."""

    def test_simulate_operation_answers_identically_for_opposite_cases(self) -> None:
        # These two calls are as far apart as the registry allows a pair of writes to
        # be. One is undone by negating a boolean it was already handed; the other
        # destroys a row and declares no undo capability at all. If a function called
        # "simulate" cannot tell them apart, it is not simulating anything.
        saved_tool = registry.require("saved.post.set").tool_name
        delete_tool = registry.require("crypto.alerts.delete").tool_name

        a = architecture.simulate_operation(saved_tool, {"post_id": "9", "saved": True})
        b = architecture.simulate_operation(delete_tool, {"alert_id": "7"})

        differing = {key for key in set(a) | set(b) if a.get(key) != b.get(key)}
        # Only the fields that echo the input back may differ. Every field that purports
        # to say something *about* the operation is the same for both.
        self.assertEqual(
            differing, {"tool_name", "arguments_preview"},
            "simulate_operation has started discriminating; this module's premise changed",
        )
        self.assertEqual(a["predicted_outcome"], b["predicted_outcome"])
        self.assertEqual(a["mitigation"], b["mitigation"])
        self.assertEqual(a["assumptions"], b["assumptions"])

    def test_it_is_empty_rather_than_wrong_so_nothing_has_to_be_retracted(self) -> None:
        # The distinction the module docstring rests on. Every field is either a fact or
        # a stated absence of knowledge; none is a claim that turns out to be false.
        tool = registry.require("saved.post.set").tool_name
        result = architecture.simulate_operation(tool, {"post_id": "9", "saved": True})
        self.assertIs(result["production_write"], False)
        self.assertIs(result["simulated"], True)
        self.assertIn("requires an authorized tool result", result["uncertainty"])


class TheFlagGatesEverything(unittest.TestCase):

    def test_both_entry_points_refuse_with_the_flag_off(self) -> None:
        refused = p.predict(*SET_SAVED)
        self.assertFalse(refused.ok)
        self.assertIn("UNDX_BRAIN_PREDICTION_ENABLED", refused.reason)

        # check() has to be gated separately and not merely inherit the gate from
        # predict(): a caller holding a prediction made while the flag was on must not
        # be able to score it after the flag goes off.
        prediction = p.predict(*SET_SAVED, env=ON)
        self.assertTrue(prediction.ok)
        scored = p.check(prediction, {"saved": True})
        self.assertFalse(scored.ok)
        self.assertIn("UNDX_BRAIN_PREDICTION_ENABLED", scored.reason)

    def test_the_master_switch_alone_is_not_enough(self) -> None:
        result = p.predict(*SET_SAVED, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(result.ok)

    def test_the_subsystem_switch_alone_is_not_enough(self) -> None:
        result = p.predict(*SET_SAVED, env={"UNDX_BRAIN_PREDICTION_ENABLED": "1"})
        self.assertFalse(result.ok)

    def test_the_flag_is_declared_and_defaults_off_and_fails_closed(self) -> None:
        flag = next(
            (item for item in brain_config.CATALOG if item.name == "UNDX_BRAIN_PREDICTION_ENABLED"),
            None,
        )
        self.assertIsNotNone(flag, "the flag must be declared, not read inline")
        self.assertEqual(flag.default, "0")
        self.assertEqual(flag.fail, "closed")
        # An undeclared UNDX_BRAIN_* variable is reported by config; this asserts the
        # new flag did not arrive as one.
        self.assertEqual(brain_config.unknown_undx_brain_vars(dict(ON)), ())


class ThePredictionIsDerivedNotHardcoded(unittest.TestCase):
    """Different registry entries must produce different answers, for registry reasons."""

    def test_the_four_reversal_outcomes_each_have_a_real_capability(self) -> None:
        cases = (
            (SET_SAVED, p.Reversal.EXACT_INVERSE),
            (CREATE_ALERT, p.Reversal.PENDING_IDENTITY),
            (UPDATE_ALERT, p.Reversal.REQUIRES_PRE_READ),
            (DELETE_ALERT, p.Reversal.IRRECOVERABLE),
        )
        # Every outcome must be reachable. A classifier with a branch no real capability
        # takes is a branch that has never been exercised, and this asserts the four are
        # genuinely distinct rather than three plus a default.
        self.assertEqual(len({outcome for _, outcome in cases}), 4)
        for (capability_id, arguments), expected in cases:
            with self.subTest(capability=capability_id):
                self.assertIs(p.predict(capability_id, arguments, env=ON).reversal, expected)

    def test_every_registered_write_gets_a_write_reversal_and_every_read_gets_none(self) -> None:
        # A sweep rather than a sample: if a capability is added whose declarations the
        # classifier cannot place, this fails rather than quietly returning a default.
        writes = set(registry.write_capability_ids())
        self.assertTrue(writes, "the registry has no writes; the sweep would be vacuous")
        for capability_id in registry.capability_ids():
            spec = registry.require(capability_id)
            arguments = {item.name: _sample(item) for item in spec.fields}
            result = p.predict(capability_id, arguments, env=ON)
            with self.subTest(capability=capability_id):
                self.assertTrue(result.ok)
                if capability_id in writes:
                    self.assertIsNot(result.reversal, p.Reversal.NOT_A_WRITE)
                    self.assertTrue(result.is_write)
                else:
                    self.assertIs(result.reversal, p.Reversal.NOT_A_WRITE)
                    self.assertFalse(result.is_write)

    def test_the_expected_post_state_is_what_the_verifier_will_read_back(self) -> None:
        # Not a paraphrase of the request: the field list must equal the capability's
        # own declared verified_fields, intersected with what was actually passed.
        capability_id, arguments = UPDATE_ALERT
        spec = registry.require(capability_id)
        result = p.predict(capability_id, arguments, env=ON)
        predicted = {item.field_name: item.value for item in result.expected}
        self.assertEqual(
            set(predicted), set(spec.verified_fields) & set(arguments),
            "the prediction covers fields the verifier does not read, or misses ones it does",
        )
        self.assertEqual(predicted["threshold"], 60000)
        self.assertEqual(predicted["condition"], "below")

    def test_the_undo_argument_set_is_the_registry_s_own_answer(self) -> None:
        # The module must not build undo arguments itself. It asks the spec, which
        # already knows how, and asks it at the only time the answer can still matter.
        capability_id, arguments = SET_SAVED
        spec = registry.require(capability_id)
        result = p.predict(capability_id, arguments, env=ON)
        self.assertEqual(result.undo_arguments, spec.undo_arguments(arguments, canonical_ids=[]))
        self.assertEqual(result.undo_arguments, {"post_id": "9", "saved": False})

    def test_conflicting_writes_are_narrower_than_writes_on_the_same_resource(self) -> None:
        result = p.predict(*UPDATE_ALERT, env=ON)
        self.assertTrue(set(result.conflicting_writes) <= set(result.also_writes_this_resource))
        # crypto.alerts.create targets ``symbol``, not ``alert_id``. It makes a new
        # alert; it does not contend for this one. A blast radius that swept in every
        # capability in the audit category would wrongly include it.
        self.assertNotIn("crypto.alerts.create", result.also_writes_this_resource)
        self.assertIn("crypto.alerts.delete", result.conflicting_writes)

    def test_a_capability_does_not_conflict_with_itself(self) -> None:
        for capability_id in registry.write_capability_ids():
            spec = registry.require(capability_id)
            arguments = {item.name: _sample(item) for item in spec.fields}
            result = p.predict(capability_id, arguments, env=ON)
            with self.subTest(capability=capability_id):
                self.assertNotIn(capability_id, result.also_writes_this_resource)
                self.assertNotIn(capability_id, result.conflicting_writes)


class ThePredictionRefusesToInvent(unittest.TestCase):

    def test_a_verified_field_with_no_argument_is_named_not_guessed(self) -> None:
        # ``reels.like`` declares verified_fields=('liked',) and no field called
        # ``liked``. The expected value follows from which capability was chosen, not
        # from anything passed. Putting a plausible ``True`` in ``expected`` would let
        # check() confirm the prediction against a value the predictor made up.
        spec = registry.require("reels.like")
        self.assertIn("liked", spec.verified_fields)
        self.assertNotIn("liked", {item.name for item in spec.fields})

        result = p.predict("reels.like", {"reel_id": "3"}, env=ON)
        self.assertIn("liked", result.implied_fields)
        self.assertNotIn("liked", {item.field_name for item in result.expected})

    def test_a_call_whose_undo_needs_an_id_that_does_not_exist_is_not_reversible(self) -> None:
        result = p.predict(*CREATE_ALERT, env=ON)
        self.assertIs(result.reversal, p.Reversal.PENDING_IDENTITY)
        self.assertFalse(result.reversal.reversible_now)
        self.assertFalse(bool(result), "truthiness must not promise a reversal that needs a future id")
        # None, not {}. An empty mapping would read as "undo takes no arguments".
        self.assertIsNone(result.undo_arguments)
        self.assertTrue(any("canonical id" in item for item in result.assumes))

    def test_an_undo_that_costs_more_than_the_original_is_not_called_cheap(self) -> None:
        # crypto.alerts.create undoes with crypto.alerts.delete, which is itself a
        # consequential write requiring explicit confirmation. "Undo" implies a cheap
        # escape hatch and this one launches a second confirmation prompt.
        undo = registry.require(registry.require("crypto.alerts.create").undo_capability_id)
        self.assertEqual(undo.risk, RiskLevel.CONSEQUENTIAL_WRITE)
        self.assertEqual(undo.confirmation, ConfirmationPolicy.ALWAYS)
        self.assertFalse(p.predict(*CREATE_ALERT, env=ON).undo_is_cheap)

        # notifications.preference.update reverses itself exactly, and is still not
        # cheap, because reversing it requires its own confirmation.
        result = p.predict("notifications.preference.update", {"category": "crypto", "push": False}, env=ON)
        self.assertIs(result.reversal, p.Reversal.EXACT_INVERSE)
        self.assertFalse(result.undo_is_cheap)

        # social.follow is the case where the word means what it looks like.
        self.assertTrue(p.predict("social.follow", {"target_user_id": "42"}, env=ON).undo_is_cheap)

    def test_values_destroyed_by_the_write_are_named_before_it_happens(self) -> None:
        result = p.predict(*UPDATE_ALERT, env=ON)
        self.assertEqual(set(result.pre_read_fields), {"threshold", "condition"})
        self.assertTrue(
            any("unrecoverable unless read before this call" in item for item in result.assumes)
        )
        # And the case where there is nothing to pre-read, because the resource itself
        # is going away and its fields go with it.
        self.assertEqual(p.predict(*DELETE_ALERT, env=ON).pre_read_fields, ())

    def test_it_states_that_it_queried_nothing(self) -> None:
        result = p.predict(*UPDATE_ALERT, env=ON)
        self.assertTrue(
            any("nothing was queried" in item for item in result.assumes),
            "a predictor that reads no state must say so, or it will be read as having checked",
        )

    def test_an_unregistered_capability_declines_rather_than_raising(self) -> None:
        result = p.predict("not.a.capability", {"x": 1}, env=ON)
        self.assertFalse(result.ok)
        self.assertIn("not registered", result.reason)


class ThePredictionCanBeFalsified(unittest.TestCase):
    """A prediction nothing can contradict is a description. This shows the failure path."""

    def setUp(self) -> None:
        self.prediction = p.predict(*UPDATE_ALERT, env=ON)

    def test_a_matching_read_back_confirms(self) -> None:
        outcome = p.check(self.prediction, {"threshold": 60000, "condition": "below"}, env=ON)
        self.assertIs(outcome.fidelity, p.Fidelity.CONFIRMED)
        self.assertTrue(bool(outcome))
        self.assertEqual(set(outcome.confirmed), {"threshold", "condition"})

    def test_a_differing_read_back_contradicts_and_says_both_values(self) -> None:
        outcome = p.check(self.prediction, {"threshold": 50000, "condition": "below"}, env=ON)
        self.assertIs(outcome.fidelity, p.Fidelity.CONTRADICTED)
        self.assertFalse(bool(outcome))
        self.assertEqual(outcome.contradicted, (("threshold", 60000, 50000),))
        self.assertIn("predicted 60000 observed 50000", outcome.reason)

    def test_a_field_that_was_not_read_back_is_neither_hit_nor_miss(self) -> None:
        outcome = p.check(self.prediction, {"threshold": 60000}, env=ON)
        self.assertIs(outcome.fidelity, p.Fidelity.UNOBSERVED)
        self.assertEqual(outcome.unobserved, ("condition",))
        self.assertEqual(outcome.confirmed, ("threshold",))
        self.assertEqual(outcome.contradicted, ())

    def test_a_json_or_sqlite_round_trip_is_not_a_contradiction(self) -> None:
        # Read-back crosses a JSON boundary and a SQLite boundary and both turn True
        # into something that is not True. Reporting that as a contradiction would
        # train whoever reads the record to ignore it.
        prediction = p.predict(*SET_SAVED, env=ON)
        for observed in ({"saved": 1}, {"saved": "true"}, {"saved": True}):
            with self.subTest(observed=observed):
                self.assertIs(p.check(prediction, observed, env=ON).fidelity, p.Fidelity.CONFIRMED)
        # But a genuinely opposite value still contradicts.
        self.assertIs(p.check(prediction, {"saved": False}, env=ON).fidelity, p.Fidelity.CONTRADICTED)
        self.assertIs(p.check(prediction, {"saved": 0}, env=ON).fidelity, p.Fidelity.CONTRADICTED)

    def test_numbers_compare_across_string_and_numeric_spellings(self) -> None:
        outcome = p.check(self.prediction, {"threshold": "60000", "condition": "Below"}, env=ON)
        self.assertIs(outcome.fidelity, p.Fidelity.CONFIRMED)

    def test_a_pending_undo_that_never_arrives_is_reported(self) -> None:
        prediction = p.predict(*CREATE_ALERT, env=ON)
        observed = {"condition": "above", "threshold": 50000}
        unverified = p.check(prediction, observed, canonical_ids=[], env=ON)
        verified = p.check(prediction, observed, canonical_ids=["alert_rule:42"], env=ON)
        self.assertTrue(unverified.undo_expected_but_unavailable)
        self.assertFalse(verified.undo_expected_but_unavailable)
        self.assertIn("undo was expected", unverified.reason)

    def test_a_write_with_nothing_to_predict_is_not_scored_as_a_success(self) -> None:
        # crypto.alerts.delete declares no verified fields. Reporting CONFIRMED for it
        # would mean the record filled up with successes for calls that predicted
        # nothing, which is how a scoreboard becomes worthless.
        prediction = p.predict(*DELETE_ALERT, env=ON)
        self.assertEqual(prediction.expected, ())
        outcome = p.check(prediction, {}, env=ON)
        self.assertIs(outcome.fidelity, p.Fidelity.NOTHING_PREDICTED)
        self.assertFalse(bool(outcome))

    def test_checking_a_failed_prediction_refuses(self) -> None:
        failed = p.predict("not.a.capability", {}, env=ON)
        outcome = p.check(failed, {"anything": 1}, env=ON)
        self.assertFalse(outcome.ok)
        self.assertIn("did not itself succeed", outcome.reason)


def _sample(spec_field) -> object:
    """A value of the right shape for one declared field.

    Used only by the registry-wide sweeps, where the point is that every capability can
    be predicted at all, not what any particular value produces.
    """
    if spec_field.choices:
        return spec_field.choices[0]
    if spec_field.kind == "bool":
        return True
    if spec_field.kind in {"int", "float"}:
        return spec_field.minimum if spec_field.minimum is not None else 1
    return "sample-1"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
