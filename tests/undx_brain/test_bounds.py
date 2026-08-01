"""Ceilings, tested at the point where exceeding one would be convenient.

Every assertion here answers the same question: when the plan wants more than it is
allowed, what happens? There are only two honest answers — refuse, or expire — and the
tempting third answer, "do as much as fits", is what most of this file exists to
prevent. A plan truncated to fit its budget is the failure mode where UNDX creates the
alert, never attaches the threshold, and reports that it is done.

The four bounds are tested separately because they fail differently: steps refuse
before anything runs, tool calls refuse partway through, retries refuse per step and
refuse *unconditionally* for writes, and the timeout refuses everything at once.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain import bounds as b  # noqa: E402


#: Multi-step reasoning on, so the step ceiling under test is the configured one rather
#: than the single-step collapse.
MULTI = {"UNDX_BRAIN_REASONING_ENABLED": "1"}


class FakeClock:
    """A clock the tests move by hand, so expiry is measured rather than waited for."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TheCeilingsAreActuallyRead(unittest.TestCase):
    """The whole point of the module. These fail if the flags go unread again."""

    def test_each_flag_reaches_the_budget(self):
        limits = b.budget({
            "UNDX_BRAIN_REASONING_ENABLED": "1",
            "UNDX_PLANNER_MAX_STEPS": "9",
            "UNDX_PLANNER_MAX_TOOL_CALLS": "11",
            "UNDX_PLANNER_MAX_RETRIES": "3",
            "UNDX_PLANNER_TASK_TIMEOUT_SECONDS": "45",
        })
        self.assertEqual(limits.max_steps, 9)
        self.assertEqual(limits.max_tool_calls, 11)
        self.assertEqual(limits.max_retries, 3)
        self.assertEqual(limits.timeout_seconds, 45)
        self.assertTrue(limits.multi_step)

    def test_an_empty_environment_produces_the_documented_defaults(self):
        limits = b.budget({})
        self.assertEqual(limits.max_steps, 6)
        self.assertEqual(limits.max_tool_calls, 8)
        self.assertEqual(limits.max_retries, 1)
        self.assertEqual(limits.timeout_seconds, 120)

    def test_an_out_of_range_ceiling_is_clamped_rather_than_obeyed(self):
        # config clamps; this test exists so that stays true through this path, since a
        # planner handed max_steps=100000 is an unbounded planner with extra steps.
        limits = b.budget({"UNDX_BRAIN_REASONING_ENABLED": "1", "UNDX_PLANNER_MAX_STEPS": "9999"})
        self.assertLessEqual(limits.max_steps, 32)
        self.assertGreaterEqual(limits.max_steps, 1)

    def test_budget_never_raises_on_hostile_values(self):
        limits = b.budget({
            "UNDX_PLANNER_MAX_STEPS": "\x00",
            "UNDX_PLANNER_MAX_TOOL_CALLS": "eight",
            "UNDX_PLANNER_MAX_RETRIES": "-",
            "UNDX_PLANNER_TASK_TIMEOUT_SECONDS": "9" * 200,
        })
        self.assertGreaterEqual(limits.max_steps, 1)
        self.assertGreaterEqual(limits.max_tool_calls, 1)
        self.assertGreaterEqual(limits.max_retries, 0)


class ReasoningIsOffUntilItIsTurnedOn(unittest.TestCase):
    def test_the_default_collapses_every_plan_to_one_step(self):
        self.assertEqual(b.budget({}).effective_max_steps, b.SINGLE_STEP)

    def test_a_two_step_plan_is_refused_while_reasoning_is_off(self):
        outcome = b.admit(2, env={})
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.refusal.bound, "steps")
        self.assertIn("multi-step reasoning is off", outcome.refusal.message)

    def test_a_one_step_plan_still_runs_with_reasoning_off(self):
        # Otherwise the fail-closed default would take the product offline rather than
        # returning it to its previous behaviour.
        self.assertTrue(b.admit(1, env={}).ok)

    def test_the_configured_ceiling_stays_visible_when_it_is_not_in_effect(self):
        # An operator who raised MAX_STEPS and sees plans capped at one needs to be able
        # to tell that their number was read and a *different* flag is the reason.
        limits = b.budget({"UNDX_PLANNER_MAX_STEPS": "12"})
        self.assertEqual(limits.max_steps, 12)
        self.assertEqual(limits.effective_max_steps, 1)

    def test_the_switch_actually_switches(self):
        self.assertEqual(b.budget(MULTI).effective_max_steps, 6)


class AnOversizedPlanIsRefusedNotShortened(unittest.TestCase):
    def test_a_plan_over_the_ceiling_does_not_run_at_all(self):
        outcome = b.admit(7, env=MULTI)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.refusal.limit, 6)
        self.assertEqual(outcome.refusal.requested, 7)

    def test_the_refusal_says_why_shortening_was_not_the_answer(self):
        # The reason is the whole design decision. If this string goes, so does the
        # explanation somebody needs when they ask why UNDX refused instead of trying.
        message = b.admit(7, env=MULTI).refusal.message
        self.assertIn("half done", message)

    def test_admission_never_reports_fewer_steps_than_it_was_given(self):
        # A truncating implementation would pass every other test in this class by
        # admitting a shortened plan. It cannot pass this one.
        for count in (1, 5, 6, 7, 20):
            with self.subTest(count=count):
                outcome = b.admit(count, env=MULTI)
                self.assertEqual(outcome.steps, count)
                self.assertEqual(outcome.ok, count <= 6)

    def test_a_plan_exactly_at_the_ceiling_is_admitted(self):
        self.assertTrue(b.admit(6, env=MULTI).ok)

    def test_an_empty_plan_is_refused(self):
        # An empty plan that reports success is a completion claim for work nobody did.
        outcome = b.admit(0, env=MULTI)
        self.assertFalse(outcome.ok)
        self.assertIn("cannot succeed", outcome.refusal.message)

    def test_a_collection_may_be_passed_instead_of_a_count(self):
        self.assertTrue(b.admit(["a", "b"], env=MULTI).ok)
        self.assertFalse(b.admit(["x"] * 7, env=MULTI).ok)

    def test_an_unmeasurable_plan_is_refused_rather_than_assumed_small(self):
        for junk in (None, object(), True, "six"):
            with self.subTest(junk=type(junk).__name__):
                self.assertFalse(b.admit(junk, env=MULTI).ok)

    def test_a_refusal_is_truthy_and_an_admission_is_not_refused(self):
        self.assertTrue(b.admit(99, env=MULTI).refusal)
        self.assertFalse(b.admit(1, env=MULTI).refusal)


class ToolCallsAreSpentOnce(unittest.TestCase):
    def test_the_ledger_refuses_past_its_call_budget(self):
        ledger = b.Ledger(b.Budget(max_tool_calls=3))
        for _ in range(3):
            self.assertFalse(ledger.may_call())
        refusal = ledger.may_call()
        self.assertTrue(refusal)
        self.assertEqual(refusal.bound, "tool_calls")

    def test_a_refused_call_is_not_charged(self):
        # Otherwise a caller that keeps asking drives the counter arbitrarily high and
        # the report becomes fiction.
        ledger = b.Ledger(b.Budget(max_tool_calls=1))
        ledger.may_call()
        for _ in range(5):
            ledger.may_call()
        self.assertEqual(ledger.tool_calls, 1)

    def test_there_is_no_way_to_hand_budget_back(self):
        # The counters are read-only and no method refunds. A plan that could refund
        # would be a plan with no ceiling at all, reached one excuse at a time.
        ledger = b.Ledger(b.Budget(max_tool_calls=2))
        ledger.may_call()
        for name in dir(ledger):
            with self.subTest(name=name):
                self.assertNotIn(name, ("release", "refund", "reset", "clear"))
        with self.assertRaises(AttributeError):
            ledger.tool_calls = 0  # type: ignore[misc]

    def test_steps_are_counted_against_the_effective_ceiling(self):
        ledger = b.Ledger(b.Budget(max_steps=2, multi_step=True))
        self.assertFalse(ledger.begin_step("a"))
        self.assertFalse(ledger.begin_step("b"))
        self.assertTrue(ledger.begin_step("c"))
        self.assertEqual(ledger.steps, 2)


class AWriteIsNeverRetried(unittest.TestCase):
    def test_a_write_retry_is_refused_even_with_retries_available(self):
        ledger = b.Ledger(b.Budget(max_retries=5))
        refusal = ledger.may_retry("step-1", write=True)
        self.assertTrue(refusal)
        self.assertEqual(refusal.limit, 0)

    def test_the_write_refusal_explains_the_timeout_case(self):
        message = b.Ledger(b.Budget(max_retries=5)).may_retry("s", write=True).message
        self.assertIn("one instruction becomes two", message)

    def test_a_refused_write_retry_does_not_consume_a_read_retry(self):
        ledger = b.Ledger(b.Budget(max_retries=1))
        ledger.may_retry("step-1", write=True)
        self.assertFalse(ledger.may_retry("step-1", write=False))

    def test_the_write_question_cannot_go_unasked(self):
        # ``write`` is keyword-only with no default on purpose: a default would let a
        # caller retry a write by forgetting to mention that it was one.
        with self.assertRaises(TypeError):
            b.Ledger().may_retry("step-1")  # type: ignore[call-arg]

    def test_read_retries_are_bounded_per_step(self):
        ledger = b.Ledger(b.Budget(max_retries=1))
        self.assertFalse(ledger.may_retry("a", write=False))
        self.assertTrue(ledger.may_retry("a", write=False))
        # A different step has its own allowance; the ceiling is per step, not per plan.
        self.assertFalse(ledger.may_retry("b", write=False))

    def test_zero_retries_means_zero(self):
        ledger = b.Ledger(b.Budget(max_retries=0))
        self.assertTrue(ledger.may_retry("a", write=False))


class ExpiryEndsThePlanRatherThanPausingIt(unittest.TestCase):
    def test_everything_refuses_once_the_bound_has_passed(self):
        clock = FakeClock()
        ledger = b.Ledger(b.Budget(max_steps=9, max_tool_calls=9, max_retries=9,
                                   timeout_seconds=10, multi_step=True), clock=clock)
        self.assertFalse(ledger.may_call())
        clock.advance(10)
        self.assertTrue(ledger.expired())
        for refusal in (ledger.may_call(), ledger.begin_step("s"),
                        ledger.may_retry("s", write=False)):
            with self.subTest(bound=refusal.bound):
                self.assertEqual(refusal.bound, "timeout")

    def test_the_expiry_message_rules_out_resuming(self):
        clock = FakeClock()
        ledger = b.Ledger(b.Budget(timeout_seconds=5), clock=clock)
        clock.advance(6)
        message = ledger.may_call().message
        self.assertIn("expires", message)
        self.assertIn("stale", message)

    def test_time_does_not_run_backwards_into_extra_budget(self):
        # A wall clock that steps backwards over a DST boundary must not resurrect an
        # expired plan, which is why the default clock is monotonic.
        clock = FakeClock()
        ledger = b.Ledger(b.Budget(timeout_seconds=5), clock=clock)
        clock.advance(6)
        self.assertTrue(ledger.expired())
        clock.now = -100.0
        self.assertGreaterEqual(ledger.elapsed, 0.0)

    def test_the_default_clock_is_monotonic(self):
        import time as _time
        self.assertIs(b.Ledger()._clock, _time.monotonic)


class TheReportIsHonest(unittest.TestCase):
    def test_the_report_shows_what_was_spent_and_what_was_allowed(self):
        clock = FakeClock()
        ledger = b.Ledger(b.Budget(max_steps=4, max_tool_calls=4, max_retries=2,
                                   timeout_seconds=60, multi_step=True), clock=clock)
        ledger.begin_step("a")
        ledger.may_call()
        ledger.may_retry("a", write=False)
        clock.advance(3)
        report = ledger.report()
        self.assertEqual(report["steps"], 1)
        self.assertEqual(report["tool_calls"], 1)
        self.assertEqual(report["retries"], {"a": 1})
        self.assertEqual(report["max_steps"], 4)
        self.assertEqual(report["elapsed_seconds"], 3.0)
        self.assertFalse(report["expired"])

    def test_the_report_shows_the_effective_ceiling_not_the_configured_one(self):
        report = b.Ledger(b.Budget(max_steps=6, multi_step=False)).report()
        self.assertEqual(report["max_steps"], b.SINGLE_STEP)

    def test_mutating_the_report_does_not_mutate_the_ledger(self):
        ledger = b.Ledger(b.Budget(max_retries=2))
        ledger.may_retry("a", write=False)
        ledger.report()["retries"]["a"] = 99
        self.assertEqual(ledger.retries_for("a"), 1)


class TheBudgetCannotBeEdited(unittest.TestCase):
    def test_a_budget_is_frozen(self):
        with self.assertRaises(Exception):
            b.budget({}).max_steps = 100  # type: ignore[misc]

    def test_a_ledger_holds_its_budget_rather_than_a_copy_it_can_drift_from(self):
        limits = b.Budget(max_tool_calls=2)
        self.assertIs(b.Ledger(limits).budget, limits)

    def test_ledger_for_resolves_from_the_environment(self):
        ledger = b.ledger_for({"UNDX_BRAIN_REASONING_ENABLED": "1",
                               "UNDX_PLANNER_MAX_TOOL_CALLS": "2"})
        self.assertEqual(ledger.budget.max_tool_calls, 2)

    def test_junk_in_place_of_a_budget_falls_back_to_the_defaults(self):
        for junk in (None, "budget", 7):
            with self.subTest(junk=junk):
                self.assertIsInstance(b.Ledger(junk).budget, b.Budget)  # type: ignore[arg-type]
                self.assertIsInstance(b.admit(1, junk).budget, b.Budget)  # type: ignore[arg-type]


class ThePlannerActuallyConsultsTheBounds(unittest.TestCase):
    """The wiring, not the module. Everything above passes with nothing calling it."""

    def setUp(self):
        from tests.undx_agent import bootstrap
        bootstrap.install()
        from services import undx_architecture
        self.arch = undx_architecture

    def _plan(self, steps: int) -> dict:
        return {"nodes": [{"node_type": "call_tool"}] * steps, "status": "ready"}

    def test_a_real_plan_carries_its_bounds(self):
        plan = self.arch.build_plan(
            7, "publish a Reel",
            {"tool_names": ["pulsesoc.create_reel"], "requires_confirmation": True}, "r1",
        )
        self.assertIn("bounds", plan)
        self.assertTrue(plan["bounds"]["admitted"])

    def test_todays_plans_are_unchanged_by_the_new_ceiling(self):
        # The bound has to be enforceable without taking the product offline. Every
        # plan build_plan produces today has one acting node, so the single-step
        # default admits all of them.
        for message, context in (
            ("what is my name", {}),
            ("publish a Reel", {"tool_names": ["pulsesoc.create_reel"]}),
            ("send it", {"tool_names": ["pulsesoc.send_message"], "requires_confirmation": True}),
        ):
            with self.subTest(message=message):
                plan = self.arch.build_plan(7, message, context, "r")
                self.assertTrue(plan["bounds"]["admitted"])
                self.assertNotEqual(plan["status"], "blocked")

    def test_scaffolding_nodes_do_not_spend_the_step_budget(self):
        # understand/retrieve/verify are on every plan. Counting them as steps would
        # mean a one-step ceiling refused every request UNDX has ever served.
        plan = {"nodes": [{"node_type": t} for t in ("understand", "retrieve", "verify")],
                "status": "ready"}
        self.assertEqual(self.arch.plan_steps(plan), 0)
        self.assertTrue(self.arch.apply_bounds(plan)["bounds"]["admitted"])

    def test_an_over_budget_plan_is_blocked(self):
        plan = self.arch.apply_bounds(self._plan(4), {})
        self.assertEqual(plan["status"], "blocked")
        self.assertFalse(plan["bounds"]["admitted"])
        self.assertEqual(plan["bounds"]["refusal"]["bound"], "steps")

    def test_a_blocked_plan_keeps_every_node_it_asked_for(self):
        # The refusal must not become a truncation on the way out. If the record only
        # showed the nodes that fit, nobody could tell a refused plan from a small one.
        plan = self.arch.apply_bounds(self._plan(9), {"UNDX_BRAIN_REASONING_ENABLED": "1"})
        self.assertEqual(len(plan["nodes"]), 9)
        self.assertEqual(plan["bounds"]["steps"], 9)
        self.assertEqual(plan["status"], "blocked")

    def test_raising_the_ceiling_admits_a_plan_that_was_refused(self):
        # Proves the refusal came from the flag rather than from something incidental.
        self.assertEqual(self.arch.apply_bounds(self._plan(4), {})["status"], "blocked")
        self.assertEqual(
            self.arch.apply_bounds(
                self._plan(4), {"UNDX_BRAIN_REASONING_ENABLED": "1"}
            )["status"],
            "ready",
        )

    def test_a_blocked_status_is_one_the_rest_of_the_system_understands(self):
        self.assertIn("blocked", self.arch.MISSION_STATUSES)


class TheFoundationMapNamesThisModule(unittest.TestCase):
    def test_planning_names_the_bounds_module(self):
        from tests.undx_agent import bootstrap
        bootstrap.install()
        from services.undx_brain import foundation
        item = foundation.by_key("planning")
        self.assertIsNotNone(item)
        self.assertIn(("services.undx_brain.bounds", "admit"), item.owners)
        self.assertNotIn(
            "nothing reads them yet", item.gap,
            "the gap text still says the ceilings are unread",
        )


if __name__ == "__main__":
    unittest.main()
