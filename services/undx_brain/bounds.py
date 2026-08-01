"""Ceilings on one plan, enforced rather than described.

PART 9 asks that reasoning be bounded. The four numbers that bound it
(``UNDX_PLANNER_MAX_STEPS`` and its siblings) have been declared in
:mod:`services.undx_brain.config` since the Foundation was mapped, and until now
nothing read them. A ceiling nobody reads is a comment.

Four decisions are load-bearing here, and each of them is the *less* convenient option:

**An over-budget plan is refused, not truncated.** Truncating is the obvious
implementation and it is the dangerous one. A plan that says "create the alert, then
attach the threshold, then enable it" cut to two steps leaves an alert that exists and
does nothing, and — worse — leaves UNDX believing it finished. Half of a multi-write goal
is not a smaller version of that goal; it is a state no user asked for. So
:func:`admit` returns a refusal carrying the number that was exceeded, and the caller
tells the person the request is too large rather than silently doing part of it.

**A write is never retried, whatever the retry ceiling says.** ``UNDX_PLANNER_MAX_RETRIES``
governs reads. The gateway enforces idempotency, but idempotency protects against a
duplicate arriving; it does not make a planner that resends a write on timeout correct,
because a timeout is the one case where the planner does not know whether the first
attempt landed. :meth:`Ledger.may_retry` refuses on ``write=True`` before it even looks
at the count.

**Budget is monotonic.** A :class:`Ledger` spends and never refunds. There is no
``release``, no ``reset``, and the counters are read-only properties, because the failure
this class exists to prevent is a long-running plan that keeps finding reasons its last
call "didn't really count".

**Expiry is not resumption.** ``UNDX_PLANNER_TASK_TIMEOUT_SECONDS`` expires a plan. An
expired plan does not continue where it left off when someone asks again — its remaining
steps are abandoned and a fresh plan is built, because the world moved while the plan was
waiting and the evidence it gathered before the pause is no longer evidence of anything
current.

Non-goals, stated so nobody wires them in later by accident: this module does not
execute anything, does not talk to the gateway, and does not decide whether a step is
*allowed* — that is policy, and it lives in :mod:`services.undx_policy_engine`. Bounds
answer "how much", never "whether".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from . import config as brain_config

__all__ = [
    "Budget",
    "Ledger",
    "Admission",
    "Refusal",
    "budget",
    "admit",
    "ledger_for",
    "SINGLE_STEP",
]

#: What a plan collapses to when multi-step reasoning is switched off. Not zero — a
#: request still gets answered; it just gets answered in one step, which is the
#: behaviour that shipped before this module existed.
SINGLE_STEP = 1


@dataclass(frozen=True)
class Refusal:
    """Why a plan was not admitted, in terms the caller can put in front of a person.

    ``limit`` and ``requested`` are separate fields rather than baked into ``message``
    so a caller can say "12 steps, and the ceiling is 6" without parsing prose.
    """

    bound: str = ""
    limit: int = 0
    requested: int = 0
    message: str = ""

    def __bool__(self) -> bool:
        # Truthy means "there is a refusal here", which reads correctly at the call
        # site as ``if refusal:``.
        return bool(self.bound)


@dataclass(frozen=True)
class Budget:
    """The four ceilings, resolved once for one plan.

    Frozen because a budget that can be edited after admission is not a budget. A
    caller who needs different numbers resolves a new one from a different environment,
    which is a visible act.
    """

    max_steps: int = 6
    max_tool_calls: int = 8
    max_retries: int = 1
    timeout_seconds: int = 120
    #: Whether multi-step plans are permitted at all. Off is the shipped default and
    #: means every admitted plan is exactly one step.
    multi_step: bool = False
    notes: tuple[str, ...] = field(default=(), repr=False)

    @property
    def effective_max_steps(self) -> int:
        """The ceiling that actually applies, after the reasoning switch.

        Kept as a property rather than folded into ``max_steps`` at construction so the
        configured number stays visible in a report. An operator who sets
        ``UNDX_PLANNER_MAX_STEPS=12`` and sees an effective ceiling of 1 has been told
        something useful: the flag they need is a different one.
        """
        return self.max_steps if self.multi_step else SINGLE_STEP


@dataclass(frozen=True)
class Admission:
    """The outcome of checking a plan against a budget.

    ``ok`` and ``refusal`` are both present because "admitted" and "refused, and here is
    the number you exceeded" are different enough that collapsing them into a bare
    boolean loses the only part the person needs.
    """

    ok: bool = False
    budget: Budget = field(default_factory=Budget)
    steps: int = 0
    refusal: Refusal = field(default_factory=Refusal)

    def __bool__(self) -> bool:
        return self.ok


def budget(env: Mapping[str, str] | None = None) -> Budget:
    """Resolve the four ceilings from the environment.

    Never raises and never returns ``None``. A caller that cannot get a budget has no
    safe fallback available to it — it would either invent numbers or run unbounded —
    so the resolution goes through :func:`config.resolve`, which clamps out-of-range
    values to the declared bounds and reports what it corrected.
    """
    resolution = brain_config.resolve(env)
    values = resolution.values
    return Budget(
        max_steps=_positive(values.get("UNDX_PLANNER_MAX_STEPS"), 6),
        max_tool_calls=_positive(values.get("UNDX_PLANNER_MAX_TOOL_CALLS"), 8),
        max_retries=_non_negative(values.get("UNDX_PLANNER_MAX_RETRIES"), 1),
        timeout_seconds=_positive(values.get("UNDX_PLANNER_TASK_TIMEOUT_SECONDS"), 120),
        multi_step=bool(values.get("UNDX_BRAIN_REASONING_ENABLED", False)),
        notes=tuple(resolution.notes),
    )


def admit(steps: Any, limits: Budget | None = None, *, env: Mapping[str, str] | None = None) -> Admission:
    """Decide whether a plan of ``steps`` steps may run at all.

    ``steps`` may be a count or any sized collection of them, so a caller does not have
    to remember which. A plan of zero steps is refused too: an empty plan that reports
    success is a plan that claims a goal was met by doing nothing, which is precisely
    the false completion claim PART 8 exists to prevent.
    """
    limits = limits if isinstance(limits, Budget) else budget(env)
    count = _count(steps)
    if count is None:
        return Admission(
            ok=False,
            budget=limits,
            steps=0,
            refusal=Refusal(
                bound="steps",
                limit=limits.effective_max_steps,
                requested=0,
                message="the plan's length could not be determined, so it cannot be bounded",
            ),
        )
    if count < 1:
        return Admission(
            ok=False,
            budget=limits,
            steps=count,
            refusal=Refusal(
                bound="steps",
                limit=limits.effective_max_steps,
                requested=count,
                message="an empty plan cannot succeed at anything, so it is not admitted",
            ),
        )
    ceiling = limits.effective_max_steps
    if count > ceiling:
        detail = (
            f"this needs {count} steps and the limit is {ceiling}"
            if limits.multi_step
            else (
                f"this needs {count} steps and multi-step reasoning is off, so the limit "
                f"is {SINGLE_STEP}"
            )
        )
        return Admission(
            ok=False,
            budget=limits,
            steps=count,
            refusal=Refusal(
                bound="steps",
                limit=ceiling,
                requested=count,
                message=(
                    f"{detail}. The request is refused rather than shortened, because a "
                    f"plan cut in half leaves the goal half done"
                ),
            ),
        )
    return Admission(ok=True, budget=limits, steps=count)


class Ledger:
    """Budget actually spent by one running plan. Spends only; never refunds.

    Every method that grants something is a *question* — ``may_call``, ``may_retry`` —
    and the grant is recorded as it is given. There is deliberately no way to ask
    without spending, because a caller that can look before it leaps will eventually
    look, leap, and look again.
    """

    __slots__ = ("_budget", "_started", "_clock", "_tool_calls", "_retries", "_steps")

    def __init__(
        self,
        limits: Budget | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._budget = limits if isinstance(limits, Budget) else Budget()
        # Injectable so the expiry tests measure the rule rather than the test's
        # patience. Monotonic in production because wall-clock adjustments must not
        # extend or shorten a plan's life.
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._tool_calls = 0
        self._retries: dict[str, int] = {}
        self._steps = 0

    @property
    def budget(self) -> Budget:
        return self._budget

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    @property
    def steps(self) -> int:
        return self._steps

    def retries_for(self, step: str) -> int:
        return self._retries.get(str(step), 0)

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    def expired(self) -> bool:
        return self.elapsed >= float(self._budget.timeout_seconds)

    def begin_step(self, step: str = "") -> Refusal:
        """Record entering a step. Returns a refusal if the plan may not continue."""
        if self.expired():
            return self._expiry()
        ceiling = self._budget.effective_max_steps
        if self._steps + 1 > ceiling:
            return Refusal(
                bound="steps",
                limit=ceiling,
                requested=self._steps + 1,
                message=(
                    f"the plan has already run {self._steps} of its {ceiling} steps and "
                    f"stops here rather than continuing past its bound"
                ),
            )
        self._steps += 1
        return Refusal()

    def may_call(self) -> Refusal:
        """Spend one governed tool call, or refuse.

        Retries count against this ceiling as well as their own, which is why the
        config purpose says "counting retries". A plan that retries its way to forty
        calls has not stayed within a budget of eight.
        """
        if self.expired():
            return self._expiry()
        if self._tool_calls + 1 > self._budget.max_tool_calls:
            return Refusal(
                bound="tool_calls",
                limit=self._budget.max_tool_calls,
                requested=self._tool_calls + 1,
                message=(
                    f"this plan has already made {self._tool_calls} tool calls, which is "
                    f"its budget"
                ),
            )
        self._tool_calls += 1
        return Refusal()

    def may_retry(self, step: str, *, write: bool) -> Refusal:
        """Spend one retry of ``step``, or refuse.

        The ``write`` argument is keyword-only and has no default. A caller must state
        which kind of operation it is retrying, because the answer for a write is
        always no and a default would let that question go unasked.
        """
        if write:
            return Refusal(
                bound="retries",
                limit=0,
                requested=1,
                message=(
                    "a write is never retried by the planner: on a timeout it is not "
                    "known whether the first attempt landed, and resending is how one "
                    "instruction becomes two"
                ),
            )
        if self.expired():
            return self._expiry()
        key = str(step)
        spent = self._retries.get(key, 0)
        if spent + 1 > self._budget.max_retries:
            return Refusal(
                bound="retries",
                limit=self._budget.max_retries,
                requested=spent + 1,
                message=f"step {key!r} has used its {self._budget.max_retries} retries",
            )
        self._retries[key] = spent + 1
        return Refusal()

    def _expiry(self) -> Refusal:
        return Refusal(
            bound="timeout",
            limit=int(self._budget.timeout_seconds),
            requested=int(self.elapsed),
            message=(
                f"this plan passed its {self._budget.timeout_seconds}s bound and expires "
                f"rather than resuming; anything it learned before now is stale"
            ),
        )

    def report(self) -> dict[str, Any]:
        """A flat summary safe to log or attach to a mission record."""
        return {
            "steps": self._steps,
            "max_steps": self._budget.effective_max_steps,
            "tool_calls": self._tool_calls,
            "max_tool_calls": self._budget.max_tool_calls,
            "retries": dict(self._retries),
            "max_retries": self._budget.max_retries,
            "elapsed_seconds": round(self.elapsed, 3),
            "timeout_seconds": self._budget.timeout_seconds,
            "expired": self.expired(),
            "multi_step": self._budget.multi_step,
        }


def ledger_for(
    env: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], float] | None = None,
) -> Ledger:
    return Ledger(budget(env), clock=clock)


def _count(steps: Any) -> int | None:
    if isinstance(steps, bool):
        # Same trap as an owner id: ``True`` would count as a one-step plan.
        return None
    if isinstance(steps, int):
        return steps
    if isinstance(steps, (str, bytes, bytearray)):
        # A string is Sized, so falling through would read ``"six"`` as a three-step
        # plan and ``"12"`` as a two-step one. Both are admitted, both are nonsense,
        # and neither looks wrong in a log. A plan is never a string.
        return None
    if isinstance(steps, Sequence) or isinstance(steps, (set, frozenset, dict)):
        try:
            return len(steps)
        except TypeError:  # pragma: no cover - len on a Sized that lies
            return None
    try:
        return len(steps)
    except TypeError:
        return None


def _positive(raw: Any, fallback: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _non_negative(raw: Any, fallback: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value >= 0 else fallback
