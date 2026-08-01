"""When a sentence names what is wanted, and when it only names what is wrong.

Directive §7 states the acceptance test as three sentences about the same object. "Find
my Bitcoin alert" is retrieval. "Fix my Bitcoin alert" has no knowable goal until the
alert's state has been read. "Help me manage my alerts" is a scope, and may take more
than one step. All three are pinned here.

The second is the one that matters, and the test that carries the weight is
:meth:`AnUnsettledGoalNeverBecomesAWrite.test_fixing_a_broken_alert_does_not_delete_it`.
Every other component in the request path is built to converge on an operation, and given
"my alert is broken, fix it" a scoring matcher will find one — the words are there. If
what it finds is ``crypto.alerts.delete``, somebody who asked for help loses their alert.
That is the failure this module exists to make impossible, so the tests do not check that
the right operation is chosen; they check that *no* operation is chosen, and that what is
offered instead is a read.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import undx_agent_runtime as runtime  # noqa: E402
from services.undx_brain import attention as a  # noqa: E402
from services.undx_brain import goals as g  # noqa: E402

#: Everything on. Individual tests turn things off; a base of "off" would mean most tests
#: measured the disabled path by accident.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_ATTENTION_ENABLED": "1",
    "UNDX_BRAIN_GOALS_ENABLED": "1",
}

FIND = "Find my Bitcoin alert"
FIX = "Fix my Bitcoin alert"
MANAGE = "Help me manage my alerts"

#: Sentences used wherever a test needs to hold across the whole shape of the module
#: rather than at one point.
CORPUS = (
    FIND, FIX, MANAGE,
    "pause alert 3", "delete all my alerts", "unfollow user 7",
    "my alerts are not working", "my marketplace order is messed up",
    "clean up my saved posts", "what devices am I signed in on",
    "why is my account acting strange", "should i pause my alert",
    "do not delete alert 3", "asdfgh qwerty", "",
)


def env(**overrides: str) -> dict[str, str]:
    settings = dict(ON)
    settings.update(overrides)
    return settings


def understand(request: str, **overrides: str) -> g.Goal:
    return g.understand(request, env=env(**overrides))


def is_read(capability_id: str) -> bool:
    record = a._RECORD_OF.get(capability_id)
    return record is not None and record.risk_class == "read_only"


class ItIsOffUntilItIsTurnedOn(unittest.TestCase):
    """An unconfigured deployment must read intent exactly as it does today."""

    def test_the_master_switch_closes_it(self):
        goal = g.understand(FIX, env={"UNDX_BRAIN_GOALS_ENABLED": "1"})
        self.assertFalse(goal.ok)
        self.assertIs(goal.shape, g.Shape.UNKNOWN)
        self.assertIn("disabled", goal.reason)

    def test_its_own_switch_closes_it(self):
        goal = g.understand(FIX, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(goal.ok)
        self.assertIn("disabled", goal.reason)

    def test_a_closed_goal_names_no_operation(self):
        # The dangerous shape of "disabled" is one that still hands back a capability,
        # because the caller cannot tell that from a real decision.
        goal = g.understand(FIX, env={})
        self.assertEqual(goal.capability_id, "")
        self.assertEqual(goal.inspect_with, ())


class TheSeventhDirectiveExample(unittest.TestCase):
    """§7's three sentences about one alert."""

    def test_find_is_retrieval(self):
        goal = understand(FIND)
        self.assertIs(goal.shape, g.Shape.RETRIEVE)
        self.assertTrue(goal.settled)
        self.assertTrue(goal.single_step)
        self.assertTrue(goal.capability_id.startswith("crypto.alerts."),
                        f"retrieval landed on {goal.capability_id!r}")
        self.assertTrue(is_read(goal.capability_id))

    def test_fix_has_no_knowable_goal_yet(self):
        goal = understand(FIX)
        self.assertIs(goal.shape, g.Shape.REPAIR)
        self.assertFalse(goal.settled)
        self.assertTrue(goal.needs_inspection)
        self.assertEqual(goal.capability_id, "")

    def test_fix_says_what_would_settle_it(self):
        # "I do not know what you want" is only half an answer. The other half is what
        # would make it knowable.
        goal = understand(FIX)
        self.assertTrue(goal.inspect_with, "repair named nothing to inspect")
        for cid in goal.inspect_with:
            self.assertTrue(is_read(cid), f"{cid} was offered as an inspection and is a write")

    def test_fix_explains_itself(self):
        goal = understand(FIX)
        self.assertIn("current state", goal.reason)
        self.assertEqual(goal.frame, "fix my")

    def test_manage_is_a_scope_and_may_take_more_than_one_step(self):
        goal = understand(MANAGE)
        self.assertIs(goal.shape, g.Shape.MANAGE)
        self.assertFalse(goal.settled)
        self.assertFalse(goal.single_step,
                         "a scope was reported as satisfiable by one operation")
        self.assertEqual(goal.capability_id, "")
        self.assertTrue(goal.inspect_with)

    def test_the_three_sentences_are_three_different_answers(self):
        shapes = {understand(s).shape for s in (FIND, FIX, MANAGE)}
        self.assertEqual(len(shapes), 3, f"§7's three sentences collapsed to {shapes}")


class AnUnsettledGoalNeverBecomesAWrite(unittest.TestCase):
    """The safety property. Nothing else in this file matters as much."""

    def test_fixing_a_broken_alert_does_not_delete_it(self):
        for phrasing in ("my alert is broken, fix it",
                         "my bitcoin alert is not working, sort it out",
                         "something is wrong with my alerts",
                         "my alerts stopped working"):
            goal = understand(phrasing)
            self.assertFalse(goal.settled, f"{phrasing!r} was reported as settled")
            self.assertEqual(goal.capability_id, "",
                             f"{phrasing!r} resolved to {goal.capability_id!r}")
            for cid in goal.inspect_with:
                self.assertTrue(is_read(cid), f"{phrasing!r} offered the write {cid}")

    def test_no_unsettled_goal_anywhere_carries_an_operation(self):
        for request in CORPUS:
            goal = understand(request)
            if not goal.settled:
                self.assertEqual(goal.capability_id, "",
                                 f"{request!r} is unsettled and carries {goal.capability_id!r}")

    def test_nothing_offered_for_inspection_is_ever_a_write(self):
        for request in CORPUS:
            for cid in understand(request).inspect_with:
                self.assertTrue(is_read(cid), f"{request!r} offered the write {cid}")

    def test_a_goal_inferred_from_vocabulary_alone_has_no_write_in_range(self):
        # When no registered phrasing matches, a retrieval may be inferred from what
        # attention pointed at — but only when *nothing* in range could be executed as a
        # write. Otherwise the inference is refused and the goal stays unknown, because
        # choosing among capabilities by vocabulary overlap is routing, not selection.
        for request in CORPUS:
            goal = understand(request)
            if goal.shape is not g.Shape.RETRIEVE or goal.settled:
                continue
            focus = a.attend(request, env=env())
            for area in focus.areas:
                for cid in area.capability_ids + area.deferred:
                    self.assertTrue(
                        is_read(cid),
                        f"{request!r} was inferred as a retrieval with {cid} in range",
                    )

    def test_the_device_question_is_inferred_rather_than_guessed(self):
        # "What devices am I signed in on" reaches nothing through the registry's intent
        # phrasings, because nobody wrote that sentence down, and reaches
        # ``security.device.list`` through the map. Reporting it as UNKNOWN would throw
        # away something the system demonstrably knows.
        goal = understand("what devices am I signed in on")
        self.assertIs(goal.shape, g.Shape.RETRIEVE)
        self.assertFalse(goal.settled)
        self.assertEqual(goal.capability_id, "")
        self.assertIn("security.device.list", goal.inspect_with)

    def test_a_repair_frame_names_nothing_executable(self):
        # The membership test for the frame list, enforced rather than described: a frame
        # that names a real operation would turn a clear instruction into a question.
        for frame in g.REPAIR_FRAMES:
            spec = runtime.match_capability(frame.strip())
            if spec is not None:
                self.assertFalse(
                    getattr(spec, "is_write", False),
                    f"the repair frame {frame!r} names the write {spec.capability_id}",
                )


class ANamedOperationIsStillANamedOperation(unittest.TestCase):
    """The filter must narrow, not silence. A module that answered "unsettled" to
    everything would pass every safety test above."""

    def test_a_write_instruction_is_an_act(self):
        goal = understand("pause alert 3")
        self.assertIs(goal.shape, g.Shape.ACT)
        self.assertTrue(goal.settled)
        self.assertEqual(goal.capability_id, "crypto.alerts.pause")

    def test_a_read_instruction_is_a_retrieval(self):
        goal = understand("show me my alerts")
        self.assertIs(goal.shape, g.Shape.RETRIEVE)
        self.assertTrue(goal.settled)
        self.assertTrue(is_read(goal.capability_id))

    def test_the_shape_follows_the_registry_not_a_local_opinion(self):
        for request in CORPUS:
            goal = understand(request)
            if not goal.capability_id:
                continue
            spec = runtime.get(goal.capability_id) if hasattr(runtime, "get") else None
            spec = spec or runtime.REGISTRY.get(goal.capability_id)
            self.assertIsNotNone(spec)
            expected = g.Shape.ACT if spec.is_write else g.Shape.RETRIEVE
            self.assertIs(goal.shape, expected)


class ItConsultsTheExistingMatcherRatherThanReplacingIt(unittest.TestCase):
    """§7 asks for integration with intent and argument resolution, not a second copy
    of it. A parallel matcher would drift, and the drift would be invisible until the
    two disagreed about a write."""

    def test_the_operation_is_the_matchers_operation(self):
        for request in ("pause alert 3", "show me my alerts", "unfollow user 7",
                        "delete all my alerts"):
            spec = runtime.match_capability(request)
            self.assertIsNotNone(spec)
            self.assertEqual(understand(request).capability_id, spec.capability_id)

    def test_the_action_framing_is_read_from_the_runtime_not_decided_again(self):
        for request in CORPUS:
            if not request:
                continue
            self.assertEqual(understand(request).asks_for_action,
                             runtime.asks_for_the_action(request),
                             f"the two readings of {request!r} disagree")

    def test_explicitness_is_read_from_the_runtime_not_decided_again(self):
        for request in CORPUS:
            if not request:
                continue
            self.assertEqual(understand(request).explicit, runtime.is_explicit(request))

    def test_a_hedged_write_does_not_become_an_act(self):
        # "Should I pause my alert?" is a question about pausing, not an instruction to
        # pause, and the runtime already excludes writes for it. Goal understanding must
        # not undo that.
        goal = understand("should i pause my alert")
        self.assertFalse(goal.asks_for_action)
        self.assertIsNot(goal.shape, g.Shape.ACT)

    def test_a_negated_write_does_not_become_an_act(self):
        # Blocked by the runtime's *second* negation mechanism, the verb-scoped one
        # inside the matcher, which is why ``asks_for_action`` is still true here. The
        # field reports one of the two and is named after the function it reports, so
        # this test is what stops it being read as "no write can result".
        goal = understand("do not delete alert 3")
        self.assertTrue(goal.asks_for_action)
        self.assertIsNot(goal.shape, g.Shape.ACT)
        self.assertEqual(goal.capability_id, "")


class ItReusesAttentionRatherThanRoutingTwice(unittest.TestCase):
    def test_the_areas_are_attentions_areas(self):
        for request in (FIX, MANAGE, "why is my account acting strange"):
            self.assertEqual(understand(request).areas,
                             a.attend(request, env=env()).area_names)

    def test_a_supplied_focus_is_used(self):
        focus = a.attend(FIX, env=env())
        self.assertEqual(g.understand(FIX, env=env(), focus=focus).areas, focus.area_names)

    def test_the_request_length_limit_is_shared(self):
        # One request, truncated at one length, in one place.
        self.assertIs(g.MAX_REQUEST_CHARS, a.MAX_REQUEST_CHARS)

    def test_the_inspection_list_is_bounded(self):
        for request in CORPUS:
            self.assertLessEqual(len(understand(request).inspect_with), g.MAX_INSPECTIONS)


class NothingReadableIsSaidPlainly(unittest.TestCase):
    def test_gibberish_is_unknown_not_a_guess(self):
        goal = understand("asdfgh qwerty zxcvbn")
        self.assertIs(goal.shape, g.Shape.UNKNOWN)
        self.assertTrue(goal.ok)
        self.assertFalse(bool(goal))
        self.assertEqual(goal.capability_id, "")

    def test_an_empty_request_is_unknown(self):
        goal = understand("")
        self.assertIs(goal.shape, g.Shape.UNKNOWN)
        self.assertTrue(goal.ok)
        self.assertIn("empty", goal.reason)

    def test_a_long_request_is_still_read(self):
        goal = understand(FIX + " " + ("please " * 400))
        self.assertIs(goal.shape, g.Shape.REPAIR)
        self.assertTrue(any("first" in note for note in goal.notes))

    def test_every_unsettled_goal_says_why(self):
        for request in CORPUS:
            goal = understand(request)
            if goal.needs_inspection:
                self.assertTrue(goal.reason, f"{request!r} is unsettled and gave no reason")

    def test_inspection_carries_no_request_text(self):
        goal = understand("fix my alert, my password is hunter2")
        self.assertNotIn("hunter2", repr(goal.inspect()))


class TheFramesAreSmallAndJustified(unittest.TestCase):
    def test_scope_frames_do_not_swallow_ordinary_reads(self):
        # "Review my alerts" and "check my alerts" are questions with straightforward
        # answers, and routing them to an open-ended engagement would be a regression
        # dressed as helpfulness.
        for request in ("review my alerts", "check my alerts", "list my alerts"):
            goal = understand(request)
            self.assertIsNot(goal.shape, g.Shape.MANAGE, f"{request!r} became a scope")

    def test_the_longest_frame_wins_across_both_lists(self):
        self.assertIs(understand("help me fix my alerts").shape, g.Shape.REPAIR)
        self.assertIs(understand("help me manage my alerts").shape, g.Shape.MANAGE)

    def test_frames_are_deduplicated_against_each_other(self):
        self.assertEqual(set(g.REPAIR_FRAMES) & set(g.SCOPE_FRAMES), set())

    def test_the_same_request_reads_the_same_way_twice(self):
        for request in CORPUS:
            self.assertEqual(understand(request).inspect(), understand(request).inspect())


if __name__ == "__main__":
    unittest.main()
