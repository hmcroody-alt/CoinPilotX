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

**A profile's maximum is a maximum.** :func:`budget` resolves whatever the environment
says, which is correct for an operator tuning one deployment and wrong as the only
answer available, because it means the ceiling that applies to a *write* turn is
whatever the ceiling for a research turn happened to be set to. :data:`PROFILES` names a
few bounded shapes, and :func:`profile` intersects the named shape with the environment:
configuration may lower any number in a profile and may raise none of them. That
asymmetry is the whole point — it is the same asymmetry that lets the completion
conjunction in :mod:`services.undx_tool_gateway` run unflagged. A mistake in the
environment, or an environment an attacker influenced, can make UNDX do less than the
profile allows and can never make it do more.

Non-goals, stated so nobody wires them in later by accident: this module does not
execute anything, does not talk to the gateway, and does not decide whether a step is
*allowed* — that is policy, and it lives in :mod:`services.undx_policy_engine`. Bounds
answer "how much", never "whether".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from . import config as brain_config

__all__ = [
    "Budget",
    "Ledger",
    "Admission",
    "Refusal",
    "PROFILES",
    "DEFAULT_PROFILE",
    "budget",
    "profile",
    "profile_names",
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


#: Named bounded shapes. Each value is a *fixed maximum*: :func:`profile` may lower any
#: of these numbers from the environment and may raise none of them, so the worst a
#: misconfiguration can do to a profile is make it stricter.
#:
#: The names describe the turn, not the caller, because the same subsystem answers both
#: kinds of turn and the shape that matters is what the turn is about to do.
#:
#: ``write`` is deliberately the tightest and deliberately single-step. A turn that is
#: about to change something the person owns gets one step, one call, and no retries —
#: not because writes are slow, but because every extra step is another place for the
#: plan to decide the goal changed, and the operation at the end of it is the one that
#: cannot be taken back. ``multi_step=False`` here is a fixed maximum like the others: no
#: value of ``UNDX_BRAIN_REASONING_ENABLED`` turns it on.
PROFILES: Mapping[str, Budget] = MappingProxyType({
    "write": Budget(
        max_steps=1, max_tool_calls=2, max_retries=0, timeout_seconds=30,
        multi_step=False,
        notes=("a turn that changes state gets one step and no retries",),
    ),
    "read": Budget(
        max_steps=2, max_tool_calls=4, max_retries=1, timeout_seconds=60,
        multi_step=False,
        notes=("a lookup and, at most, one follow-up read to resolve a reference",),
    ),
    "explain": Budget(
        max_steps=3, max_tool_calls=6, max_retries=1, timeout_seconds=90,
        multi_step=True,
        notes=("explanation may gather from several places; it may not write",),
    ),
    "research": Budget(
        max_steps=6, max_tool_calls=8, max_retries=1, timeout_seconds=120,
        multi_step=True,
        notes=("the widest shape, and still bounded by the module's own numbers",),
    ),
})

#: What an unrecognised or absent profile name resolves to. ``read`` rather than
#: ``research``: an unknown name is a bug or a typo, and the safe reading of "I don't
#: know what kind of turn this is" is the narrow one, not the wide one.
DEFAULT_PROFILE = "read"


def profile_names() -> tuple[str, ...]:
    """The declared profile names, in ascending order of what they permit."""
    return ("write", "read", "explain", "research")


def profile(name: Any = DEFAULT_PROFILE, *, env: Mapping[str, str] | None = None) -> Budget:
    """Resolve the named profile against the environment, narrowing only.

    A second constructor beside :func:`budget`, not a modification of it. ``budget``
    reads the environment and nothing else, which is right for an operator tuning a
    deployment and insufficient as the only answer available: it hands the same four
    numbers to a turn that is about to write as to a turn that is about to read a
    dashboard. This function starts from a fixed shape and lets configuration lower it.

    Every numeric ceiling is ``min(profile, environment)`` and ``multi_step`` is
    ``profile and environment``, so:

    * an operator who tightens ``UNDX_PLANNER_MAX_TOOL_CALLS`` tightens every profile;
    * an operator who widens it widens nothing beyond the profile's own maximum;
    * and no environment, however it was populated, turns multi-step reasoning on for a
      profile whose declared shape is single-step.

    An unknown name resolves to :data:`DEFAULT_PROFILE` and records that it did, rather
    than raising. The caller is a conversation; the right response to a typo'd profile
    name is a narrower turn and a note, not no answer at all.
    """
    key = str(name or "").strip().lower()
    notes: list[str] = []
    if key not in PROFILES:
        notes.append(
            f"profile {key!r} is not declared; using the narrower {DEFAULT_PROFILE!r} "
            f"rather than assuming the wider one was meant"
        )
        key = DEFAULT_PROFILE
    fixed = PROFILES[key]
    configured = budget(env)
    narrowed = Budget(
        max_steps=min(fixed.max_steps, configured.max_steps),
        max_tool_calls=min(fixed.max_tool_calls, configured.max_tool_calls),
        max_retries=min(fixed.max_retries, configured.max_retries),
        timeout_seconds=min(fixed.timeout_seconds, configured.timeout_seconds),
        # ``and``, not ``or``. Both must permit it, so the profile is a ceiling on the
        # switch exactly as it is a ceiling on the counts.
        multi_step=bool(fixed.multi_step and configured.multi_step),
        notes=tuple([f"profile={key}", *fixed.notes, *notes, *configured.notes]),
    )
    return narrowed


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
    profile_name: str | None = None,
) -> Ledger:
    """A ledger for one plan, optionally bounded by a named profile.

    ``profile_name`` is optional and defaults to ``None`` rather than to
    :data:`DEFAULT_PROFILE`, so that every existing caller keeps the environment-resolved
    budget it already had. Passing a name is the visible act of choosing a narrower shape;
    it cannot be used to choose a wider one, because :func:`profile` only narrows.
    """
    limits = budget(env) if profile_name is None else profile(profile_name, env=env)
    return Ledger(limits, clock=clock)


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
