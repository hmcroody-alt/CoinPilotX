"""Three ceilings that had no caller, spent for the first time.

``bounds`` enforced four numbers and one of them was reached: ``build_plan`` admits
against the step ceiling. ``UNDX_PLANNER_MAX_TOOL_CALLS``, ``UNDX_PLANNER_MAX_RETRIES``
and ``UNDX_PLANNER_TASK_TIMEOUT_SECONDS`` were enforced by ``Ledger`` and nothing ran a
plan through a ``Ledger``. :class:`TheCeilingsThatHadNoCaller` is the class that makes
that no longer true, and it asserts on the numbers rather than on a boolean, because
"the run stopped" is not evidence that it stopped at the right place.

The rest is about a single failure mode, approached from four sides: a plan that stops
part-way and lets somebody believe it finished. :class:`APartialRunIsNeverSuccess` pins
``ok``; :class:`AnUnknownOutcomeIsNotAFailure` pins the timeout case, which is the one
that costs a person real money if it is rounded the wrong way;
:class:`AWriteIsNeverRetried` pins the rule that follows from it; and
:class:`TheExecutorPerformsNothingItself` pins the structural reason none of this can be
quietly bypassed — the module has no route to the gateway to bypass it with.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = Path(__file__).resolve().parents[2]

from services.undx_brain import bounds  # noqa: E402
from services.undx_brain import config as brain_config  # noqa: E402
from services.undx_brain import execution as e  # noqa: E402

S = e.StepOutcome

#: The Brain on, the executor on, and multi-step reasoning on. The third is not
#: optional decoration: with ``UNDX_BRAIN_REASONING_ENABLED`` off the effective step
#: ceiling is 1, so a suite that forgot it would refuse every multi-step plan and pass
#: while testing nothing but the refusal.
ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_EXECUTOR_ENABLED": "1",
    "UNDX_BRAIN_REASONING_ENABLED": "1",
}


def _steps(*specs) -> list[e.Step]:
    """``_steps("a", ("w", True))`` — a read called a, a write called w."""
    built = []
    for spec in specs:
        if isinstance(spec, tuple):
            built.append(e.Step(spec[0], is_write=bool(spec[1])))
        else:
            built.append(e.Step(str(spec), is_write=False))
    return built


class _Recorder:
    """A ``perform`` that records every call and answers from a script."""

    def __init__(self, script=None, default=S.SUCCEEDED):
        self.script = dict(script or {})
        self.default = default
        self.calls: list[tuple[str, int]] = []

    def __call__(self, step, attempt):
        self.calls.append((step.step_id, attempt))
        answer = self.script.get((step.step_id, attempt), self.script.get(step.step_id))
        if answer is None:
            return self.default
        if isinstance(answer, Exception):
            raise answer
        return answer

    def attempts_at(self, step_id: str) -> int:
        return sum(1 for name, _ in self.calls if name == step_id)


class TheCeilingsThatHadNoCaller(unittest.TestCase):
    def test_the_tool_call_ceiling_stops_a_run_and_says_which_number_it_was(self):
        # Four steps, a budget of two calls. The interesting assertions are the two
        # steps that did complete: the run is not abandoned wholesale, it is stopped at
        # the first step it could not afford, and it says so.
        recorder = _Recorder()
        run = e.execute(
            _steps("a", "b", "c", "d"),
            recorder,
            env=dict(ON, UNDX_PLANNER_MAX_TOOL_CALLS="2"),
        )
        self.assertFalse(run.ok)
        self.assertEqual(run.completed, ("a", "b"))
        self.assertEqual(run.stopped_at, "c")
        self.assertEqual(run.refusal.bound, "tool_calls")
        self.assertEqual(run.refusal.limit, 2)
        self.assertEqual(run.refusal.requested, 3)
        # And it stopped before making the call, not after.
        self.assertEqual(len(recorder.calls), 2)
        self.assertEqual(run.report["tool_calls"], 2)

    def test_the_retry_ceiling_is_spent_per_step_and_reported_per_step(self):
        # ``x`` fails twice; one retry is allowed, so it is attempted twice and no more.
        recorder = _Recorder(script={("x", 1): S.FAILED, ("x", 2): S.FAILED})
        run = e.execute(
            _steps("x"), recorder, env=dict(ON, UNDX_PLANNER_MAX_RETRIES="1")
        )
        self.assertFalse(run.ok)
        self.assertEqual(recorder.attempts_at("x"), 2)
        self.assertEqual(run.report["retries"], {"x": 1})
        self.assertEqual(run.report["max_retries"], 1)

    def test_a_retry_is_still_paid_for_out_of_the_tool_call_budget(self):
        # The rule that stops a plan retrying its way to forty calls on a budget of
        # eight. Two steps, each failing once, two retries allowed, but only three
        # calls in the bank: the second step's retry is the one that cannot be bought.
        recorder = _Recorder(
            script={("a", 1): S.FAILED, ("a", 2): S.SUCCEEDED, ("b", 1): S.FAILED}
        )
        run = e.execute(
            _steps("a", "b"),
            recorder,
            env=dict(ON, UNDX_PLANNER_MAX_RETRIES="2", UNDX_PLANNER_MAX_TOOL_CALLS="3"),
        )
        self.assertFalse(run.ok)
        self.assertEqual(run.refusal.bound, "tool_calls")
        self.assertEqual(recorder.calls, [("a", 1), ("a", 2), ("b", 1)])
        self.assertEqual(run.report["tool_calls"], 3)

    def test_the_timeout_ceiling_stops_a_run_mid_plan(self):
        clock = {"now": 0.0}

        def tick():
            return clock["now"]

        def perform(step, attempt):
            clock["now"] += 60.0
            return S.SUCCEEDED

        run = e.execute(
            _steps("a", "b", "c", "d"),
            perform,
            env=dict(ON, UNDX_PLANNER_TASK_TIMEOUT_SECONDS="120"),
            clock=tick,
        )
        self.assertFalse(run.ok)
        self.assertEqual(run.completed, ("a", "b"))
        self.assertEqual(run.stopped_at, "c")
        self.assertEqual(run.refusal.bound, "timeout")
        self.assertEqual(run.refusal.limit, 120)
        self.assertTrue(run.report["expired"])

    def test_the_step_ceiling_is_still_the_one_that_refuses_before_anything_runs(self):
        # The ceiling that already had a caller, checked here too because the executor
        # is now a second caller of it and a second caller is a second chance to get it
        # wrong. Nothing is performed at all — this is a refusal, not a truncation.
        recorder = _Recorder()
        run = e.execute(
            _steps("a", "b", "c"), recorder, env=dict(ON, UNDX_PLANNER_MAX_STEPS="2")
        )
        self.assertFalse(run.ok)
        self.assertEqual(run.refusal.bound, "steps")
        self.assertEqual(run.refusal.limit, 2)
        self.assertEqual(run.refusal.requested, 3)
        self.assertEqual(recorder.calls, [])
        self.assertEqual(run.completed, ())

    def test_every_ceiling_in_the_budget_now_has_a_way_of_stopping_a_run(self):
        # The point of the batch, asserted as one statement rather than inferred from
        # the four tests above: each of the four bounds ``Budget`` declares appears as
        # the ``bound`` of some refusal this module can produce. A fifth ceiling added
        # to ``Budget`` without a caller here fails this.
        numeric = {"max_steps", "max_tool_calls", "max_retries", "timeout_seconds"}
        declared = {
            field
            for field in bounds.Budget().__dataclass_fields__
            if field in numeric
        }
        self.assertEqual(declared, numeric, "Budget's ceilings changed")

        reached = set()
        recorder = _Recorder()
        reached.add(
            e.execute(_steps("a", "b"), recorder, env=dict(ON, UNDX_PLANNER_MAX_STEPS="1")).refusal.bound
        )
        reached.add(
            e.execute(_steps("a", "b"), recorder, env=dict(ON, UNDX_PLANNER_MAX_TOOL_CALLS="1")).refusal.bound
        )

        clock = {"now": 0.0}

        def perform(step, attempt):
            clock["now"] += 999.0
            return S.SUCCEEDED

        reached.add(
            e.execute(
                _steps("a", "b"),
                perform,
                env=dict(ON, UNDX_PLANNER_TASK_TIMEOUT_SECONDS="10"),
                clock=lambda: clock["now"],
            ).refusal.bound
        )
        # Retries do not stop a *run* — they stop a step, which then stops the run with
        # a step-level refusal. That asymmetry is real and is recorded rather than
        # papered over: the retry ceiling shows up in the ledger report, not the bound.
        exhausted = e.execute(
            _steps("x"), _Recorder(script={"x": S.FAILED}), env=dict(ON, UNDX_PLANNER_MAX_RETRIES="1")
        )
        self.assertEqual(exhausted.report["retries"], {"x": 1})
        self.assertEqual({"steps", "tool_calls", "timeout"}, reached)


class APartialRunIsNeverSuccess(unittest.TestCase):
    def test_two_of_three_steps_is_not_ok(self):
        run = e.execute(
            _steps("a", "b", "c"), _Recorder(script={"c": S.FAILED}), env=ON
        )
        self.assertFalse(run.ok)
        self.assertFalse(bool(run))
        self.assertEqual(run.completed, ("a", "b"))

    def test_the_writes_that_landed_are_named_so_they_can_be_said_out_loud(self):
        run = e.execute(
            _steps(("w1", True), ("w2", True), ("w3", True)),
            _Recorder(script={"w3": S.FAILED}),
            env=ON,
        )
        self.assertFalse(run.ok)
        self.assertEqual(run.landed_writes, ("w1", "w2"))
        self.assertIn("w1", run.summary())
        self.assertIn("w2", run.summary())

    def test_the_summary_of_a_partial_run_does_not_contain_the_word_done(self):
        run = e.execute(
            _steps(("w1", True), ("w2", True)), _Recorder(script={"w2": S.FAILED}), env=ON
        )
        summary = run.summary().lower()
        for word in ("done", "completed successfully", "finished"):
            with self.subTest(word=word):
                self.assertNotIn(word, summary)
        self.assertIn("stopped at w2", summary)

    def test_a_run_that_completes_everything_is_ok_and_says_so_plainly(self):
        run = e.execute(_steps("a", ("w", True), "c"), _Recorder(), env=ON)
        self.assertTrue(run.ok)
        self.assertTrue(run.finished_cleanly)
        self.assertEqual(run.completed, ("a", "w", "c"))
        self.assertEqual(run.landed_writes, ("w",))
        self.assertEqual(run.writes_in_doubt, ())
        self.assertEqual(run.stopped_at, "")
        self.assertFalse(run.refusal)
        self.assertEqual(run.summary(), "all 3 steps completed")

    def test_a_later_step_is_not_attempted_after_an_earlier_one_fails(self):
        recorder = _Recorder(script={"b": S.FAILED})
        run = e.execute(_steps("a", "b", "c"), recorder, env=ON)
        self.assertFalse(run.ok)
        self.assertNotIn("c", [name for name, _ in recorder.calls])
        self.assertEqual(run.stopped_at, "b")

    def test_an_empty_plan_is_refused_rather_than_reported_as_a_success(self):
        # A zero-step plan that returned ``ok`` would be a claim that a goal was met by
        # doing nothing, which is the exact false-completion this layer exists to stop.
        recorder = _Recorder()
        run = e.execute([], recorder, env=ON)
        self.assertFalse(run.ok)
        self.assertEqual(run.refusal.bound, "steps")
        self.assertEqual(recorder.calls, [])

    def test_a_plan_containing_something_that_is_not_a_step_runs_none_of_it(self):
        recorder = _Recorder()
        run = e.execute([e.Step("a", is_write=False), "b"], recorder, env=ON)
        self.assertFalse(run.ok)
        self.assertEqual(recorder.calls, [], "the understood part of the plan was run")
        self.assertIn("not a Step", run.refusal.message)


class AnUnknownOutcomeIsNotAFailure(unittest.TestCase):
    def test_a_write_that_raises_goes_into_doubt_and_not_into_failed_or_landed(self):
        run = e.execute(
            _steps(("a", True), ("b", True)),
            _Recorder(script={"b": TimeoutError("gateway did not answer")}),
            env=ON,
        )
        self.assertFalse(run.ok)
        self.assertEqual(run.landed_writes, ("a",))
        self.assertEqual(run.writes_in_doubt, ("b",))
        self.assertNotIn("b", run.completed)

    def test_the_exception_survives_as_detail_instead_of_escaping(self):
        # Swallowed on purpose: an exception propagating out of ``execute`` would take
        # the ledger with it, and the ledger is the only thing that knows a write went
        # out. The type and message are kept so nothing is actually hidden.
        run = e.execute(
            _steps(("w", True)),
            _Recorder(script={"w": RuntimeError("connection reset")}),
            env=ON,
        )
        self.assertEqual(len(run.attempts), 1)
        self.assertIs(run.attempts[0].outcome, S.UNKNOWN)
        self.assertIn("RuntimeError", run.attempts[0].detail)
        self.assertIn("connection reset", run.attempts[0].detail)

    def test_the_refusal_says_the_write_must_not_be_retried_or_called_failed(self):
        run = e.execute(
            _steps(("w", True)), _Recorder(script={"w": TimeoutError("no answer")}), env=ON
        )
        self.assertIn("not known", run.refusal.message)
        self.assertIn("must not be", run.refusal.message)

    def test_finished_cleanly_is_false_when_a_write_is_in_doubt(self):
        run = e.execute(
            _steps(("w", True)), _Recorder(script={"w": TimeoutError("x")}), env=ON
        )
        self.assertFalse(run.finished_cleanly)

    def test_a_read_of_unknown_outcome_is_not_recorded_as_a_doubtful_write(self):
        run = e.execute(_steps("r"), _Recorder(script={"r": TimeoutError("x")}), env=ON)
        self.assertFalse(run.ok)
        self.assertEqual(run.writes_in_doubt, ())

    def test_nothing_a_perform_can_return_is_read_as_success_by_accident(self):
        # The values a caller most plausibly returns after a refactor. None of them may
        # mean success, and in particular ``True`` may not: a ``perform`` rewritten to
        # return a boolean would otherwise silently start reporting every step done.
        for value in (None, True, 1, "succeeded", "ok", object(), (S.SUCCEEDED,), []):
            with self.subTest(value=repr(value)):
                run = e.execute(_steps(("w", True)), lambda s, a: value, env=ON)
                self.assertFalse(run.ok, f"{value!r} was read as success")
                self.assertIs(run.attempts[0].outcome, S.UNKNOWN)

    def test_an_outcome_with_a_detail_is_accepted_as_a_pair(self):
        run = e.execute(
            _steps("a"), lambda s, a: (S.SUCCEEDED, "read 3 alerts"), env=ON
        )
        self.assertTrue(run.ok)
        self.assertEqual(run.attempts[0].detail, "read 3 alerts")

    def test_an_unknown_outcome_is_not_retried_even_for_a_read(self):
        # Not the write rule — this one is about information. A retry is a decision
        # made on evidence, and "I do not know what happened" is not evidence.
        recorder = _Recorder(script={"r": TimeoutError("no answer")})
        run = e.execute(_steps("r"), recorder, env=dict(ON, UNDX_PLANNER_MAX_RETRIES="3"))
        self.assertFalse(run.ok)
        self.assertEqual(recorder.attempts_at("r"), 1)
        self.assertEqual(run.report["retries"], {})


class AWriteIsNeverRetried(unittest.TestCase):
    def test_a_failed_write_is_attempted_exactly_once_whatever_the_ceiling_says(self):
        for ceiling in ("0", "1", "3", "10"):
            with self.subTest(ceiling=ceiling):
                recorder = _Recorder(script={"w": S.FAILED})
                run = e.execute(
                    _steps(("w", True)),
                    recorder,
                    env=dict(ON, UNDX_PLANNER_MAX_RETRIES=ceiling),
                )
                self.assertFalse(run.ok)
                self.assertEqual(recorder.attempts_at("w"), 1)
                self.assertEqual(run.report["retries"], {})

    def test_a_failed_read_in_the_same_plan_is_retried(self):
        # The contrast that shows the write rule is a rule and not a bug: same plan,
        # same ceiling, different answer, decided by ``is_write`` alone.
        recorder = _Recorder(script={("r", 1): S.FAILED, ("r", 2): S.SUCCEEDED})
        run = e.execute(_steps("r", ("w", True)), recorder, env=ON)
        self.assertTrue(run.ok)
        self.assertEqual(recorder.attempts_at("r"), 2)
        self.assertEqual(recorder.attempts_at("w"), 1)

    def test_the_bounds_layer_is_the_one_refusing_and_not_a_second_copy_of_the_rule(self):
        # If this module reimplemented "never retry a write" the two could drift. The
        # rule lives in ``Ledger.may_retry``; asserted here directly so that a change
        # there is visible as a change to this behaviour.
        ledger = bounds.Ledger(bounds.Budget(max_retries=5))
        self.assertTrue(ledger.may_retry("w", write=True))
        self.assertFalse(ledger.may_retry("r", write=False))
        source = (ROOT / "services" / "undx_brain" / "execution.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("may_retry(step.step_id, write=step.is_write)", source)
        # Counted on ``may_retry`` rather than on the keyword: ``write=step.is_write``
        # is a substring of ``is_write=step.is_write``, which appears in the Attempt
        # record and has nothing to do with the decision. A near-miss like that is
        # exactly how a structural assertion becomes noise.
        self.assertEqual(
            source.count("may_retry("), 1,
            "there is more than one place deciding whether this step may be retried",
        )

    def test_is_write_has_no_default_so_nobody_can_forget_to_answer(self):
        with self.assertRaises(TypeError):
            e.Step("x")


class ExpiryIsNotResumption(unittest.TestCase):
    def test_an_expired_run_reports_the_steps_it_did_not_reach_as_not_done(self):
        clock = {"now": 0.0}

        def perform(step, attempt):
            clock["now"] += 100.0
            return S.SUCCEEDED

        run = e.execute(
            _steps("a", "b", "c"),
            perform,
            env=dict(ON, UNDX_PLANNER_TASK_TIMEOUT_SECONDS="150"),
            clock=lambda: clock["now"],
        )
        self.assertFalse(run.ok)
        # Expiry is noticed on entering a step, not on leaving one, so the step that
        # carried the clock past the bound is allowed to finish and be counted. It has
        # already happened; pretending otherwise would put a completed write in the
        # not-done column, which is the same lie as the partial-success one wearing the
        # opposite hat.
        self.assertEqual(run.completed, ("a", "b"))
        self.assertEqual(run.stopped_at, "c")
        self.assertEqual(run.refusal.bound, "timeout")

    def test_the_run_carries_no_field_that_could_be_used_to_resume_it(self):
        # Structural, not behavioural. A ``remaining`` or ``resume_token`` on ``Run``
        # would be picked up eventually, and the point of expiry is that the world
        # moved while the plan waited.
        fields = set(e.Run.__dataclass_fields__)
        for forbidden in ("remaining", "resume", "resume_token", "continuation", "next_step"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, fields)

    def test_expiry_is_checked_before_the_call_and_not_after_it(self):
        clock = {"now": 500.0}
        recorder = _Recorder()
        run = e.execute(
            _steps("a"),
            recorder,
            env=dict(ON, UNDX_PLANNER_TASK_TIMEOUT_SECONDS="1"),
            clock=lambda: clock["now"],
        )
        # The ledger starts at 500 and the clock never moves, so nothing has expired —
        # elapsed is measured from the ledger's own start, not from zero.
        self.assertTrue(run.ok)
        self.assertEqual(len(recorder.calls), 1)


class TheLedgerIsMonotonic(unittest.TestCase):
    def test_a_failed_call_is_still_a_spent_call(self):
        recorder = _Recorder(script={("a", 1): S.FAILED, ("a", 2): S.SUCCEEDED})
        run = e.execute(_steps("a", "b"), recorder, env=ON)
        self.assertTrue(run.ok)
        self.assertEqual(run.report["tool_calls"], 3, "the failed attempt was refunded")

    def test_a_call_that_raised_is_still_a_spent_call(self):
        run = e.execute(
            _steps("a", "b"), _Recorder(script={"a": TimeoutError("x")}), env=ON
        )
        self.assertEqual(run.report["tool_calls"], 1)

    def test_the_report_names_every_ceiling_alongside_what_was_spent(self):
        run = e.execute(_steps("a"), _Recorder(), env=ON)
        for key in (
            "steps", "max_steps", "tool_calls", "max_tool_calls",
            "retries", "max_retries", "elapsed_seconds", "timeout_seconds",
            "expired", "multi_step",
        ):
            with self.subTest(key=key):
                self.assertIn(key, run.report)


class TheExecutorPerformsNothingItself(unittest.TestCase):
    #: The structural argument for why this module cannot become a second execution
    #: path: it has no way to reach one. Asserted against the source rather than
    #: against behaviour, because the failure being prevented is a future import.
    FORBIDDEN = (
        "undx_tool_gateway",
        "undx_capability_registry",
        "undx_policy_engine",
        "undx_agent_runtime",
        "undx_verification",
        "sqlite3",
        "requests",
    )

    def test_it_imports_nothing_that_could_execute_or_authorise_anything(self):
        source = (ROOT / "services" / "undx_brain" / "execution.py").read_text(
            encoding="utf-8"
        )
        for name in self.FORBIDDEN:
            with self.subTest(name=name):
                self.assertNotIn(
                    f"import {name}", source,
                    f"execution.py imports {name}; it is supposed to be able only to "
                    f"count, and a module that can count and call is a second gateway",
                )

    def test_every_side_effect_it_has_came_through_the_callable_it_was_given(self):
        seen = []
        run = e.execute(
            _steps("a", ("w", True)),
            lambda step, attempt: (seen.append(step.step_id), S.SUCCEEDED)[1],
            env=ON,
        )
        self.assertTrue(run.ok)
        self.assertEqual(seen, ["a", "w"])

    def test_it_does_not_reorder_the_plan_it_was_given(self):
        recorder = _Recorder()
        e.execute(_steps(("w", True), "r", ("w2", True)), recorder, env=ON)
        self.assertEqual([name for name, _ in recorder.calls], ["w", "r", "w2"])


class TheFlagGatesEverything(unittest.TestCase):
    def test_with_the_flag_off_perform_is_called_zero_times(self):
        recorder = _Recorder()
        run = e.execute(_steps("a", "b"), recorder, env={"UNDX_BRAIN_ENABLED": "1"})
        self.assertFalse(run.ok)
        self.assertEqual(recorder.calls, [], "the executor ran with its flag off")
        self.assertEqual(run.refusal.bound, "flag")

    def test_the_brain_switch_alone_is_not_enough_and_neither_is_the_module_switch(self):
        for env in (
            {},
            {"UNDX_BRAIN_ENABLED": "1"},
            {"UNDX_BRAIN_EXECUTOR_ENABLED": "1"},
        ):
            with self.subTest(env=env):
                recorder = _Recorder()
                run = e.execute(_steps("a"), recorder, env=dict(env))
                self.assertFalse(run.ok)
                self.assertEqual(recorder.calls, [])

    def test_the_flag_is_declared_fail_closed_and_defaults_off(self):
        flag = next(
            item for item in brain_config.CATALOG
            if item.name == "UNDX_BRAIN_EXECUTOR_ENABLED"
        )
        self.assertEqual(flag.default, "0")
        self.assertEqual(flag.fail, "closed")
        self.assertIn("UNDX_PLANNER_MAX_TOOL_CALLS", flag.purpose)
        self.assertIn("UNDX_PLANNER_MAX_RETRIES", flag.purpose)
        self.assertIn("UNDX_PLANNER_TASK_TIMEOUT_SECONDS", flag.purpose)

    def test_a_refusal_from_the_flag_is_told_apart_from_a_refusal_from_a_ceiling(self):
        off = e.execute(_steps("a"), _Recorder(), env={})
        ceiling = e.execute(
            _steps("a", "b"), _Recorder(), env=dict(ON, UNDX_PLANNER_MAX_STEPS="1")
        )
        self.assertNotEqual(off.refusal.bound, ceiling.refusal.bound)
        self.assertEqual(off.refusal.bound, "flag")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
