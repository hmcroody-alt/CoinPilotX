"""Running a plan of more than one step, against ceilings that are actually spent.

:mod:`services.undx_brain.bounds` enforces four numbers. Until now one of them had a
caller: ``build_plan`` runs every plan through :func:`~services.undx_brain.bounds.admit`,
so the *step* ceiling refuses an over-long plan before it starts. The other three —
``UNDX_PLANNER_MAX_TOOL_CALLS``, ``UNDX_PLANNER_MAX_RETRIES``,
``UNDX_PLANNER_TASK_TIMEOUT_SECONDS`` — were enforced by :class:`~bounds.Ledger` and
nothing ran a plan through a Ledger, because execution was one step per request. A
ceiling with no caller is a comment with a type annotation. This module is the caller.

What it is not
--------------
It does not perform anything. :func:`execute` takes a ``perform`` callable and invokes
it; it never imports the gateway, never touches the registry, and has no idea what a
step *does*. That is deliberate and it is the whole safety argument: a module that both
decides how much budget remains and knows how to spend it at the gateway is a second
execution path, and a second execution path is a second place for the policy engine to
be forgotten. This one can only count.

It also does not plan. The list of steps arrives already built and already ordered.
Choosing them is :mod:`~services.undx_brain.selection`'s problem and building the plan
they came from is ``undx_architecture.build_plan``'s.

Four things it refuses to do
----------------------------
**It does not report success for a partial run.** A three-step plan that completes two
steps returns ``ok=False``. This sounds obvious and is the single most tempting rule to
break, because two-thirds of a goal feels like progress and reads well in a log. It is
not progress; it is a state the person did not ask for and has not been told about. So
``ok`` is true only when every admitted step succeeded, and :attr:`Run.landed_writes`
names exactly what did happen so the caller can say it out loud.

**It does not round an unknown outcome down to a failure.** If ``perform`` raises, or
returns something that is not a :class:`StepOutcome`, the step's outcome is
:attr:`StepOutcome.UNKNOWN` and — when the step was a write — its id goes into
:attr:`Run.writes_in_doubt` rather than into either the completed or the failed list.
This is the timeout case, and it is the only case that matters: a write that timed out
may well have landed. Calling it a failure invites the caller to retry it, and retrying
a write that already landed is how one instruction becomes two.

**It does not retry a write.** ``Ledger.may_retry`` refuses on ``write=True`` before it
looks at the count, and this module never asks for a retry it would be allowed to take
by describing the step differently. Reads are retried up to the ceiling.

**It does not resume after expiry.** An expired run stops where it is. It does not
carry its remaining steps forward for the next request, because the evidence gathered
before the pause stopped being evidence of anything current while the plan was waiting.

Spending
--------
One tool call is spent per *attempt*, not per step, so a plan cannot retry its way past
``UNDX_PLANNER_MAX_TOOL_CALLS``. The ledger is monotonic: nothing is refunded when a
step fails, because the call was still made.

Everything here is behind ``UNDX_BRAIN_EXECUTOR_ENABLED``, which defaults off and
additionally requires ``UNDX_BRAIN_ENABLED``. With the flag off :func:`execute` returns
a refusal and calls ``perform`` exactly zero times — the run does not happen quietly in
one step, it does not happen at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from . import bounds
from . import config as brain_config

__all__ = [
    "StepOutcome",
    "Step",
    "Attempt",
    "Run",
    "execute",
]


class StepOutcome(str, Enum):
    """What one attempt at one step is known to have done.

    Three values and not two. ``UNKNOWN`` is the reason this is an enum rather than a
    boolean: a caller that can only say yes or no has to answer a timeout with one of
    them, and both answers are wrong in a way that costs the person something — "no"
    invites a retry that duplicates a write that landed, "yes" claims a result nobody
    read back.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Step:
    """One unit of a plan, as far as the executor is concerned.

    Deliberately thin. The executor needs an id to attribute retries to, and it needs
    to know whether the step is a write, because that single bit decides whether a
    failure may be retried and whether an unknown outcome is a doubt worth carrying.
    Everything else about the step is the caller's business.

    ``is_write`` is keyword-only with no default for the same reason
    ``Ledger.may_retry`` takes one: a default would let the question go unasked, and
    the unasked answer is the dangerous one.
    """

    step_id: str
    is_write: bool = field(kw_only=True)
    capability_id: str = ""
    description: str = ""


@dataclass(frozen=True)
class Attempt:
    """The record of one call to ``perform``. Kept whether it worked or not."""

    step_id: str
    attempt: int
    outcome: StepOutcome
    detail: str = ""
    is_write: bool = False


@dataclass(frozen=True)
class Run:
    """What happened, in enough detail to be told to a person without guessing.

    ``ok`` is not the only thing worth reading here and is deliberately the least
    informative field. The interesting ones are the three lists, because between them
    they say what the world looks like now: ``completed`` and ``landed_writes`` are what
    definitely happened, ``writes_in_doubt`` is what might have, and ``stopped_at`` is
    where it ended.
    """

    ok: bool = False
    completed: tuple[str, ...] = ()
    landed_writes: tuple[str, ...] = ()
    writes_in_doubt: tuple[str, ...] = ()
    stopped_at: str = ""
    refusal: bounds.Refusal = field(default_factory=bounds.Refusal)
    attempts: tuple[Attempt, ...] = ()
    report: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.ok

    @property
    def finished_cleanly(self) -> bool:
        """Every step succeeded and nothing is in doubt.

        Separate from ``ok`` so that the stricter question is available without a
        caller having to reconstruct it — and so that ``ok`` can never be quietly
        widened to mean this.
        """
        return self.ok and not self.writes_in_doubt

    def summary(self) -> str:
        """One line safe to log. Never says "done" unless everything is."""
        if self.ok:
            return f"all {len(self.completed)} steps completed"
        parts = [f"stopped at {self.stopped_at or 'the start'}"]
        if self.completed:
            count = len(self.completed)
            parts.append(
                f"{count} step{'' if count == 1 else 's'} had already completed"
            )
        if self.landed_writes:
            parts.append(f"writes that landed: {', '.join(self.landed_writes)}")
        if self.writes_in_doubt:
            parts.append(f"writes of unknown outcome: {', '.join(self.writes_in_doubt)}")
        if self.refusal:
            parts.append(self.refusal.message)
        return "; ".join(parts)


def execute(
    steps: Sequence[Step],
    perform: Callable[[Step, int], Any],
    *,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], float] | None = None,
) -> Run:
    """Walk ``steps`` in order, spending against the plan ceilings, and stop honestly.

    ``perform`` is called as ``perform(step, attempt)`` where ``attempt`` counts from 1.
    It should return a :class:`StepOutcome`, optionally as ``(outcome, detail)``.
    Anything else — including ``None``, ``True`` and a bare string — is read as
    :attr:`StepOutcome.UNKNOWN`, never as success. A callable that has been changed to
    return something new should fail loudly on the safe side rather than silently
    reporting that every step worked.
    """
    on, notes = _enabled(env)
    if not on:
        return Run(
            ok=False,
            refusal=bounds.Refusal(
                bound="flag",
                limit=0,
                requested=len(steps or ()),
                message=(
                    "multi-step execution is off; UNDX_BRAIN_EXECUTOR_ENABLED and "
                    "UNDX_BRAIN_ENABLED both have to be set for a plan to run as more "
                    "than one step"
                ),
            ),
            notes=notes,
        )

    ordered = tuple(step for step in (steps or ()) if isinstance(step, Step))
    if len(ordered) != len(tuple(steps or ())):
        # Not a warning. A plan containing something that is not a Step has not been
        # understood, and running the part of it that was understood is exactly the
        # partial execution this module refuses everywhere else.
        return Run(
            ok=False,
            refusal=bounds.Refusal(
                bound="steps",
                limit=0,
                requested=len(tuple(steps or ())),
                message=(
                    "the plan contains something that is not a Step, so it cannot be "
                    "bounded and is not run at all"
                ),
            ),
            notes=notes,
        )

    limits = bounds.budget(env)
    admission = bounds.admit(ordered, limits)
    if not admission.ok:
        return Run(ok=False, refusal=admission.refusal, notes=notes)

    ledger = bounds.Ledger(limits, clock=clock)
    completed: list[str] = []
    landed: list[str] = []
    doubtful: list[str] = []
    attempts: list[Attempt] = []

    for step in ordered:
        refusal = ledger.begin_step(step.step_id)
        if refusal:
            return _stopped(step, refusal, completed, landed, doubtful, attempts, ledger, notes)

        outcome, detail, refusal = _run_one(step, perform, ledger, attempts)
        if refusal:
            return _stopped(step, refusal, completed, landed, doubtful, attempts, ledger, notes)

        if outcome is StepOutcome.SUCCEEDED:
            completed.append(step.step_id)
            if step.is_write:
                landed.append(step.step_id)
            continue

        if outcome is StepOutcome.UNKNOWN and step.is_write:
            doubtful.append(step.step_id)

        # A step that did not succeed ends the run. There is no "carry on and see" —
        # later steps in a plan are there because earlier ones were supposed to have
        # happened, and running step three against a world where step two did not is
        # how a plan produces a state no one designed.
        return _stopped(
            step,
            bounds.Refusal(
                bound="step",
                limit=0,
                requested=0,
                message=(
                    f"step {step.step_id!r} reported {outcome.value}"
                    + (f": {detail}" if detail else "")
                    + (
                        "; it is a write and its outcome is not known, so it must not be "
                        "retried or reported as failed"
                        if outcome is StepOutcome.UNKNOWN and step.is_write
                        else ""
                    )
                ),
            ),
            completed,
            landed,
            doubtful,
            attempts,
            ledger,
            notes,
        )

    return Run(
        ok=True,
        completed=tuple(completed),
        landed_writes=tuple(landed),
        writes_in_doubt=(),
        stopped_at="",
        attempts=tuple(attempts),
        report=ledger.report(),
        notes=notes,
    )


def _run_one(
    step: Step,
    perform: Callable[[Step, int], Any],
    ledger: bounds.Ledger,
    attempts: list[Attempt],
) -> tuple[StepOutcome, str, bounds.Refusal]:
    """One step, including any retries it is entitled to.

    Returns the last outcome, its detail, and a refusal if the ledger stopped the run
    rather than the step failing on its own terms. The distinction matters: "your plan
    ran out of tool calls" and "the call came back an error" are different sentences to
    put in front of a person.
    """
    attempt = 0
    while True:
        attempt += 1
        # Spent per attempt, before the call. A retry that is granted by the retry
        # ceiling still has to be affordable under the tool-call ceiling, and spending
        # first means a call that raises has still been counted — it was still made.
        refusal = ledger.may_call()
        if refusal:
            return StepOutcome.UNKNOWN, "", refusal

        outcome, detail = _invoke(step, perform, attempt)
        attempts.append(
            Attempt(
                step_id=step.step_id,
                attempt=attempt,
                outcome=outcome,
                detail=detail,
                is_write=step.is_write,
            )
        )
        if outcome is StepOutcome.SUCCEEDED:
            return outcome, detail, bounds.Refusal()

        # An unknown outcome is never retried, for either kind of step. For a write
        # that is the rule; for a read it is the honest reading of "I do not know what
        # happened" — a retry is a decision made on information, and there is none.
        if outcome is StepOutcome.UNKNOWN:
            return outcome, detail, bounds.Refusal()

        retry = ledger.may_retry(step.step_id, write=step.is_write)
        if retry:
            # Out of retries, or a write. Either way this is the step's final answer,
            # not a ledger-level stop: the plan is not being cut short by its budget,
            # the step genuinely failed.
            return outcome, detail, bounds.Refusal()


def _invoke(
    step: Step, perform: Callable[[Step, int], Any], attempt: int
) -> tuple[StepOutcome, str]:
    """Call ``perform`` and read its answer conservatively.

    Every path out of here that is not an explicit ``SUCCEEDED`` is ``FAILED`` or
    ``UNKNOWN``. There is no shape of return value, and no exception, that this
    function reads as success by accident.
    """
    try:
        result = perform(step, attempt)
    except Exception as exc:  # noqa: BLE001 - the caller's failure mode is data here
        # Deliberately swallowed rather than propagated. An exception escaping would
        # take the ledger's record with it, and the record is the only thing that knows
        # a write went out. The exception's type and message survive in ``detail``.
        return StepOutcome.UNKNOWN, f"{type(exc).__name__}: {exc}"

    if isinstance(result, StepOutcome):
        return result, ""
    if (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], StepOutcome)
    ):
        return result[0], str(result[1] or "")
    return (
        StepOutcome.UNKNOWN,
        f"perform returned {type(result).__name__}, which is not a StepOutcome",
    )


def _stopped(
    step: Step,
    refusal: bounds.Refusal,
    completed: list[str],
    landed: list[str],
    doubtful: list[str],
    attempts: list[Attempt],
    ledger: bounds.Ledger,
    notes: tuple[str, ...],
) -> Run:
    return Run(
        ok=False,
        completed=tuple(completed),
        landed_writes=tuple(landed),
        writes_in_doubt=tuple(doubtful),
        stopped_at=step.step_id,
        refusal=refusal,
        attempts=tuple(attempts),
        report=ledger.report(),
        notes=notes,
    )


def _enabled(env: Mapping[str, str] | None) -> tuple[bool, tuple[str, ...]]:
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    on = bool(values.get("UNDX_BRAIN_ENABLED", False)) and bool(
        values.get("UNDX_BRAIN_EXECUTOR_ENABLED", False)
    )
    return on, tuple(resolution.notes)
