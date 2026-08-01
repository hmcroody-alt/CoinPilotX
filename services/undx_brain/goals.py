"""What the person wants to be true afterwards — and when that is not yet knowable.

Directive §7 asks for structured goal understanding, and states it through three
sentences about the same object:

* "Find my Bitcoin alert" is retrieval. The answer *is* the goal.
* "Fix my Bitcoin alert" has **no knowable goal yet**. "Fix" names a desired end state,
  not an operation. Which operation reaches that state depends entirely on what is wrong
  — a paused alert wants resuming, a wrong threshold wants updating, a deleted one wants
  recreating — and the sentence contains none of that.
* "Help me manage my alerts" is a scope, not an instruction, and may take more than one
  step.

The second one is the whole point of this module. Every other component in the request
path is built to converge: :func:`services.undx_agent_runtime.match_capability` returns
its best capability, argument resolution fills the fields, the gateway runs it. Given
"fix my alert" that machinery will find *something*, because the words "my" and "alert"
are enough to score a match, and it will then proceed with confidence. This module exists
to say the honest thing instead: the goal is not determined yet, here is the read that
would determine it, and no write may be selected until it has been done.

Four decisions here are load-bearing.

**An unsettled goal is a result, not a failure.** :attr:`Goal.settled` is ``False`` and
:attr:`Goal.ok` is ``True`` for "fix my Bitcoin alert", because the system understood the
sentence perfectly — it understood that the sentence does not name an operation. Treating
that as an error would push the caller back to guessing, which is the behaviour being
replaced.

**An unsettled goal never resolves to a write.** :attr:`Goal.inspect_with` contains only
read-only capabilities, filtered on the map's own ``risk_class``, and
:attr:`Goal.capability_id` is empty whenever the goal is unsettled. This is the safety
property: the most plausible reading of "my alert is broken, fix it" that a scoring
matcher can produce is ``crypto.alerts.delete``, and deleting somebody's alert because
they said it was broken is the failure this module is here to make impossible.

**Naming a goal is not matching a capability.** They answer different questions.
:func:`match_capability` answers "which registered operation do these words name";
this answers "what does the person want to be true when the turn ends". For "find my
Bitcoin alert" the two coincide. For "fix my Bitcoin alert" the matcher's answer is at
best the *inspection*, never the goal, and treating a match as a goal is exactly how the
conflation happens.

**Multi-step is a claim about scope, not about ambition.** :attr:`Goal.single_step` is
``False`` for "help me manage my alerts" because no one registered capability satisfies
it — not because the request is large. A caller that papers over that by picking the
best-scoring single capability has answered a different question, quietly.

What this module deliberately does not do:

* It does not plan. Naming a goal is not sequencing one, and :attr:`inspect_with` is a
  list of reads that would settle the question, not a plan to run them.
* It does not replace intent matching. ``match_capability``, ``is_explicit`` and
  ``asks_for_the_action`` in :mod:`services.undx_agent_runtime` remain the only readers
  of the registry's intent phrasings; this consults them and adds the layer above.
* It does not decide permission, and it does not execute. An unsettled goal naming three
  reads is not authorisation to perform them.
* It does not read account state — which is precisely why "fix" cannot be settled here.
  Settling it requires the inspection this module can only name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from . import attention as _attention
from . import config as brain_config
from .attention import Focus


__all__ = [
    "Shape", "Goal", "MAX_INSPECTIONS", "MAX_REQUEST_CHARS",
    "REPAIR_FRAMES", "SCOPE_FRAMES", "understand",
]


#: How many reads an unsettled goal may name. Three is enough to cover "is it there, what
#: state is it in, what happened to it" and small enough that the caller cannot mistake
#: the list for a plan.
MAX_INSPECTIONS = 3

#: Shared with attention rather than restated, so one request is truncated at one length.
MAX_REQUEST_CHARS = _attention.MAX_REQUEST_CHARS


class Shape(str, Enum):
    """What kind of thing the person wants. Not what capability answers it."""

    #: The person wants to know something. The answer is the goal.
    RETRIEVE = "retrieve"
    #: The person wants one named operation performed.
    ACT = "act"
    #: The person wants an end state. Which operation reaches it is unknown until the
    #: current state has been read.
    REPAIR = "repair"
    #: The person named a scope. No single operation satisfies it.
    MANAGE = "manage"
    #: Nothing readable. An honest outcome, and the correct response is to ask.
    UNKNOWN = "unknown"


#: Frames that name a desired end state and no operation.
#:
#: Membership has one test, and it is the reason this list is short: a frame belongs here
#: only if it names *nothing that could be executed*. "Fix", "sort out" and "not working"
#: pass — there is no fix operation in the registry and there could not be one, because
#: what fixes a thing depends on how it is broken. "Restore", "reset" and "turn back on"
#: fail the test: they name operations, they route through the ordinary matcher, and
#: putting them here would turn a clear instruction into a question.
REPAIR_FRAMES: tuple[str, ...] = (
    "fix ", "fix my", "fix the", "unbreak", "repair ",
    "sort out", "sort it out",
    "not working", "isn't working", "isnt working", "is not working",
    "stopped working", "doesn't work", "doesnt work", "does not work",
    "won't work", "wont work", "no longer works", "not work",
    "acting up", "acting strange", "acting weird", "acting funny",
    "messed up", "screwed up", "playing up",
    "what's wrong with", "whats wrong with", "wrong with",
    "something wrong", "something is wrong", "gone wrong",
    "make it work", "get it working", "get them working",
)

#: Frames that name a scope rather than an operation.
#:
#: Same test, one step wider: a frame belongs here only if it names a *body of things* to
#: be dealt with, without saying what dealing with them means. "Review" and "check" are
#: deliberately absent — both are ordinary reads, and routing "review my alerts" here
#: would turn a question with a straightforward answer into an open-ended engagement.
SCOPE_FRAMES: tuple[str, ...] = (
    "help me manage", "manage my", "manage the", "manage these", "manage them",
    "clean up", "cleanup", "tidy up", "tidy my",
    "sort through", "go through my", "go through the",
    "organise my", "organize my", "organise the", "organize the",
    "audit my", "take care of my", "deal with my", "deal with these",
    "help me with my", "help with my",
    "what should i do about", "what do i do about",
)


@dataclass(frozen=True)
class Goal:
    """What one request is for. Possibly not yet knowable, which is a real answer."""

    shape: Shape = Shape.UNKNOWN
    #: Whether the sentence alone names what is wanted. ``False`` for :attr:`Shape.REPAIR`
    #: and :attr:`Shape.MANAGE`, and it is the flag a caller must read before acting.
    settled: bool = False
    #: Whether one operation could satisfy this. ``False`` says the caller must not answer
    #: by picking its best single capability.
    single_step: bool = True
    #: The operation the request names, when the goal is settled. Deliberately empty
    #: whenever it is not: an unsettled goal with a capability attached is an invitation
    #: to run it.
    capability_id: str = ""
    #: Read-only capabilities that would settle an unsettled goal. Never writes, never a
    #: plan, and bounded by :data:`MAX_INSPECTIONS`.
    inspect_with: tuple[str, ...] = ()
    #: The product areas attention activated, carried through so the caller does not have
    #: to route the same sentence twice.
    areas: tuple[str, ...] = ()
    #: Directly ``undx_agent_runtime.asks_for_the_action``: whether a sentence that names
    #: a write is actually asking for it. ``False`` means the sentence is a negation, a
    #: deliberation, or a question about what an action does.
    #:
    #: Named after the function rather than after the outcome, deliberately. The runtime
    #: has *two* negation mechanisms — this frame-level one and a verb-scoped one inside
    #: the matcher — and "do not delete alert 3" passes this one and is blocked by the
    #: other. A field called ``writes_excluded`` would therefore have been false for a
    #: sentence whose write was excluded, which is the kind of near-miss a caller reads
    #: once and trusts forever.
    asks_for_action: bool = True
    #: Whether the sentence is phrased as an instruction rather than an exploration, from
    #: ``undx_agent_runtime.is_explicit``.
    explicit: bool = False
    #: The frame that produced an unsettled shape, so "why are you asking me this?" is
    #: answerable from the value.
    frame: str = ""
    ok: bool = True
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        """Whether a shape was read at all — *not* whether it was settled."""
        return self.shape is not Shape.UNKNOWN

    @property
    def needs_inspection(self) -> bool:
        """A shape was read and the sentence did not settle it."""
        return bool(self) and not self.settled

    def inspect(self) -> dict[str, Any]:
        """Shape, for logging. No request text."""
        return {
            "ok": self.ok,
            "shape": self.shape.value,
            "settled": self.settled,
            "single_step": self.single_step,
            "capability": self.capability_id,
            "inspect_with": list(self.inspect_with),
            "areas": list(self.areas),
            "asks_for_action": self.asks_for_action,
            "explicit": self.explicit,
            "frame": self.frame,
            "reason": self.reason,
            "notes": list(self.notes),
        }


def understand(request: Any, *, env: Mapping[str, str] | None = None,
               focus: Focus | None = None) -> Goal:
    """Read what one request is for.

    Always returns a :class:`Goal`, never ``None`` and never an exception, for the same
    reason :func:`services.undx_brain.attention.attend` does: the caller's fallback for an
    exception here would be to carry on with no goal understanding at all, which is the
    behaviour this replaces.

    ``focus`` may be supplied by a caller that has already attended to the request, so one
    sentence is not routed twice. If it is omitted, attention is consulted directly.
    """
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    notes = tuple(resolution.notes)

    if not bool(values.get("UNDX_BRAIN_ENABLED", False)):
        return Goal(ok=False, reason="the Brain layer is disabled", notes=notes)
    if not bool(values.get("UNDX_BRAIN_GOALS_ENABLED", False)):
        return Goal(
            ok=False,
            reason="goal understanding is disabled; each call site reads intent the way "
                   "it does today",
            notes=notes,
        )

    text = str(request or "")
    extra: tuple[str, ...] = ()
    if len(text) > MAX_REQUEST_CHARS:
        extra = (f"only the first {MAX_REQUEST_CHARS} characters were read",)
        text = text[:MAX_REQUEST_CHARS]
    lowered = " ".join(text.lower().split())
    if not lowered:
        return Goal(ok=True, reason="the request was empty", notes=notes + extra)

    if focus is None or not focus.ok:
        focus = _attention.attend(text, env=env)
    areas = focus.area_names if focus.ok else ()

    # The frame, if any. Longest match wins across both lists rather than one list being
    # consulted first: "help me fix my alerts" contains a repair frame and no scope frame
    # and is a repair, while "help me manage my alerts" is the reverse, and an ordering
    # rule would have to be right about cases nobody has thought of yet.
    frame, shape = _framed(lowered)

    spec = _match(text)
    asks_for_action = _asks_for_the_action(text)
    explicit = _is_explicit(text)

    if shape is not None:
        reads, _ = _reads_in(focus)
        return Goal(
            shape=shape,
            settled=False,
            single_step=shape is Shape.REPAIR,
            capability_id="",
            inspect_with=reads,
            areas=areas,
            asks_for_action=asks_for_action,
            explicit=explicit,
            frame=frame,
            ok=True,
            reason=_UNSETTLED_REASON[shape] if reads else _NO_READS_REASON[shape],
            notes=notes + extra,
        )

    if spec is None:
        # No registered phrasing matches. Attention may still have narrowed the request
        # to somewhere real — "what devices am I signed in on" reaches
        # ``security.device.list`` through the map and reaches nothing at all through the
        # intent phrasings, because nobody wrote that sentence down. Naming it a
        # retrieval whose operation is undetermined is more useful than ``UNKNOWN`` and
        # is safe under exactly one condition: every executable capability the request
        # points at is a read. If a single write is in range, the inference is refused
        # and the goal stays unknown — routing on vocabulary overlap is attention's job,
        # and letting it choose a write would make it selection instead.
        reads, only_reads = _reads_in(focus, require_all_reads=True)
        if reads and only_reads:
            return Goal(
                shape=Shape.RETRIEVE, settled=False, single_step=True,
                inspect_with=reads, areas=areas, asks_for_action=asks_for_action,
                explicit=explicit, ok=True,
                reason="no registered phrasing matches, and everything the request "
                       "points at is a read",
                notes=notes + extra,
            )
        return Goal(
            shape=Shape.UNKNOWN, areas=areas, asks_for_action=asks_for_action,
            explicit=explicit, ok=True,
            reason="no registered operation matches this request",
            notes=notes + extra,
        )

    return Goal(
        shape=Shape.ACT if getattr(spec, "is_write", False) else Shape.RETRIEVE,
        settled=True,
        single_step=True,
        capability_id=spec.capability_id,
        areas=areas,
        asks_for_action=asks_for_action,
        explicit=explicit,
        ok=True,
        notes=notes + extra,
    )


# ---------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------


_UNSETTLED_REASON: dict[Shape, str] = {
    Shape.REPAIR: (
        "the request names a desired end state and no operation; which operation "
        "reaches it depends on the current state, which has not been read"
    ),
    Shape.MANAGE: (
        "the request names a scope rather than an instruction; no single registered "
        "operation satisfies it"
    ),
}

#: The same situation with nothing to inspect. Kept distinct because "I do not know what
#: you want and here is how to find out" and "I do not know what you want and I have no
#: way to find out" are different sentences to say to somebody.
_NO_READS_REASON: dict[Shape, str] = {
    Shape.REPAIR: (
        "the request names a desired end state and no operation, and nothing in the "
        "activated areas can read the current state"
    ),
    Shape.MANAGE: (
        "the request names a scope rather than an instruction, and nothing in the "
        "activated areas can read what is in it"
    ),
}


def _framed(lowered: str) -> tuple[str, Shape | None]:
    """The longest frame present, and the shape it implies."""
    best = ""
    shape: Shape | None = None
    for candidate in REPAIR_FRAMES:
        if candidate in lowered and len(candidate) > len(best):
            best, shape = candidate, Shape.REPAIR
    for candidate in SCOPE_FRAMES:
        if candidate in lowered and len(candidate) > len(best):
            best, shape = candidate, Shape.MANAGE
    return best.strip(), shape


def _reads_in(focus: Focus, *, require_all_reads: bool = False) -> tuple[tuple[str, ...], bool]:
    """Read-only capabilities in the activated areas, and whether *everything* there is one.

    Drawn from ``capability_ids`` *and* ``deferred`` — both are executable, and an
    inspection dropped by attention's capability budget is still the read that would
    settle the question. Filtered on the map's own ``risk_class`` rather than on a local
    notion of what counts as a read, so a capability reclassified as a write stops being
    offered here the moment the map says so.

    The second return value answers a different question from the first and is why they
    are computed together: "are there reads here" and "is there anything *but* reads
    here" have different answers, and the caller that infers a goal from vocabulary alone
    needs the second one.
    """
    if not focus.ok:
        return (), False
    found: list[str] = []
    only_reads = True
    for area in focus.areas:
        for cid in area.capability_ids + area.deferred:
            record = _attention._RECORD_OF.get(cid)
            if record is None:
                continue
            if record.risk_class != "read_only":
                only_reads = False
                if require_all_reads:
                    return (), False
                continue
            if cid not in found:
                found.append(cid)
    return tuple(found[:MAX_INSPECTIONS]), only_reads


def _runtime() -> Any:
    """The agent runtime, imported late and never fatally.

    Late because the runtime reaches the tool gateway and the database, and goal
    understanding must not be the reason the Brain layer cannot be imported in a context
    that has neither. Never fatally because the degraded behaviour is correct: without the
    matcher a settled goal cannot be named, so every request reads as ``UNKNOWN``, which
    is the outcome that makes the caller ask rather than guess.
    """
    try:
        from services import undx_agent_runtime as runtime
    except Exception:  # pragma: no cover - exercised only in stripped environments
        return None
    return runtime


def _match(text: str) -> Any:
    runtime = _runtime()
    if runtime is None:
        return None
    try:
        return runtime.match_capability(text)
    except Exception:  # pragma: no cover
        return None


def _asks_for_the_action(text: str) -> bool:
    runtime = _runtime()
    if runtime is None:
        return True
    try:
        return bool(runtime.asks_for_the_action(text))
    except Exception:  # pragma: no cover
        return True


def _is_explicit(text: str) -> bool:
    runtime = _runtime()
    if runtime is None:
        return False
    try:
        return bool(runtime.is_explicit(text))
    except Exception:  # pragma: no cover
        return False
