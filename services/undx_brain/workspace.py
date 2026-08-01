"""The bounded working context for one request, and nothing else.

Directive §5 asks for a global workspace: the small set of things UNDX is holding in
mind while it works on one objective — the goal, whose account it is, what screen the
person is on, which resource they mean, which skills are live, what constraints apply,
what has actually been observed, what is still unknown, and what the risk is.

The reason it is a module rather than a dictionary is the sentence that follows in the
directive: *do not load the full source corpus, all user memory, or all capabilities into
every request*. A dictionary cannot refuse. A dictionary that is handed everything
available is exactly how a system arrives at a prompt containing 1,682 corpus records,
an account's entire message history, and every registered capability — each addition
individually reasonable, the total unreviewable, and the cost paid on every single turn.

Six decisions here are load-bearing, and each is the *less* convenient option.

**A full slot refuses; it never evicts.** Eviction is the obvious implementation and it
is the dangerous one. The entry most likely to be pushed out by a busy retrieval is the
oldest one, and the oldest one is usually the constraint the person stated in their first
sentence — "don't touch my portfolio", "only the Bitcoin one". A workspace that quietly
drops that and carries on is worse than one that stops and says it is full, because the
first produces a confident wrong action and the second produces a question.

**Evidence must name the account it is about.** :meth:`Workspace.place` requires an
``owner`` for anything entering :attr:`Slot.EVIDENCE`, and refuses when it does not
resolve to this workspace's owner. The workspace is owner-scoped the same way memory is,
through the same resolver — :func:`services.undx_brain.memory.owner_id` — so an id that
memory would refuse to open a scope for is an id this module refuses to accept evidence
under. One request holding two accounts' observations is the failure that ends with UNDX
telling somebody about somebody else's alert.

**Nothing here is durable unless somebody said so, in the call.** ``retain`` defaults to
``False`` on every entry and :meth:`Workspace.close` carries forward only the entries
explicitly marked. This is not a flag — there is deliberately no
``UNDX_WORKSPACE_RETAIN_BY_DEFAULT``, for the reason
:mod:`services.undx_brain.memory` gives about configurable isolation: a setting that can
be switched to "keep everything" is a setting that will be, and a task's scratch becoming
durable knowledge is not a behaviour anybody would choose deliberately at 2am. It is also
not persistence. This module writes nothing anywhere; :class:`Summary` is a value the
caller must hand to the Memory Brain as a separate, visible act.

**Expiry is abandonment, not resumption.** Past ``UNDX_WORKSPACE_TTL_SECONDS`` the
workspace refuses every further entry and closes as abandoned, carrying *no* retainable
entries at all regardless of what was marked. This is the same rule
:mod:`services.undx_brain.bounds` applies to an expired plan and for the same reason: the
world moved while the request was waiting, and an observation of the account made before
the pause is no longer evidence of anything current.

**A contradiction is refused, not overwritten.** Placing a different value under a key
that is already occupied does not replace it. Silent replacement is how the goal changes
between the moment it is understood and the moment it is acted on, which is precisely the
mechanism by which a system pauses the wrong alert. A genuine correction goes through
:meth:`Workspace.revise`, which records that a revision happened so it appears in the
inspection.

**Inspection never returns values.** :meth:`Workspace.inspect` reports shape — slots,
counts, ceilings, where entries came from, how much budget is left — and no content, so
it is safe to log on every request. Reading the contents requires
:meth:`Workspace.items`, which is a deliberate second step. Values are additionally
screened against :data:`services.undx_brain.corpus.SECRET_PATTERNS` on the way in, reusing
the corpus filter rather than declaring a second list, so a token that reaches this layer
is refused at the door instead of being carefully not-printed afterwards.

Non-goals, stated so nobody wires them in later by accident. This module does not decide
what *should* be in the workspace — that is attention and goal interpretation, and they
live elsewhere. It does not render anything for a model; a workspace with a
``to_prompt()`` is a workspace that becomes one giant prompt. It does not persist. And it
never decides whether an action is permitted, which is ``undx_agent_policy``, or whether
something worked, which is :mod:`services.undx_brain.evidence`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from . import config as brain_config
from . import memory
from .bounds import Refusal
from .corpus import SECRET_PATTERNS

__all__ = [
    "Slot",
    "SlotBound",
    "Item",
    "Summary",
    "Workspace",
    "SLOTS",
    "MAX_KEY_CHARS",
    "MAX_REVISIONS",
    "MAX_VALUE_CHARS",
    "open_workspace",
]

#: A key is a label this system chooses — ``"alert_id"``, ``"screen"``. It is not user
#: text, so it is short by construction, and a long one means a caller is smuggling
#: content into the name.
MAX_KEY_CHARS = 64

#: One entry's value. Well below the whole-workspace character budget on purpose: a
#: single entry that can fill the budget by itself is a single entry that can crowd out
#: every constraint and every unknown.
MAX_VALUE_CHARS = 1200

#: How many times one key may be corrected. This was the last unbounded dimension in the
#: module: slots, totals, characters and time all had ceilings, and revision did not,
#: because a revision replaces rather than adds and so costs nothing in memory. It costs
#: something else. A working context that has changed its mind about the same key three
#: times is a caller resolving the same thing over and over — an attention or planning
#: loop — and the failure mode is not a crash but a request that spins quietly until the
#: TTL and then reports abandonment with no indication of why. Refusing the fourth
#: revision turns an invisible loop into a named refusal.
MAX_REVISIONS = 3


class Slot(str, Enum):
    """The nine things a working context holds, named in the directive's own terms.

    Nine and no more. The temptation with a structure like this is a tenth slot called
    ``extra`` or ``context``, and a tenth slot with no meaning is where the corpus ends
    up.
    """

    #: What the person is trying to achieve, in one line. Exactly one.
    GOAL = "goal"
    #: Whose account this is about, and in what capacity.
    ACTOR = "actor"
    #: Where they are in the app right now — the reason "this one" can mean anything.
    SCREEN = "screen"
    #: The specific thing being acted on: an alert, a chat, a saved item.
    RESOURCE = "resource"
    #: Capabilities currently in play. A handful, never the registry.
    SKILL = "skill"
    #: Limits stated by the person or imposed by policy. The slot that must not be lost.
    CONSTRAINT = "constraint"
    #: What has actually been observed for this account, this request.
    EVIDENCE = "evidence"
    #: What is still not known. First-class because a plan built on an unfilled unknown
    #: is a plan that guesses, and the guess is invisible if nothing recorded the gap.
    UNKNOWN = "unknown"
    #: What could go wrong if this proceeds, carried alongside the plan rather than
    #: recomputed at the confirmation step.
    RISK = "risk"


@dataclass(frozen=True)
class SlotBound:
    """One slot's ceiling and the reason it has that shape."""

    slot: Slot
    limit: int
    purpose: str


#: Per-slot ceilings. Structural rather than configurable: these express what a working
#: context *is* — one goal, one actor, one screen — not a deployment's appetite. An
#: operator who needs a bigger workspace raises ``UNDX_WORKSPACE_MAX_ITEMS``, which
#: bounds the total; no setting turns "one goal" into "several goals", because a request
#: with two goals is two requests and should be handled as such.
SLOTS: tuple[SlotBound, ...] = (
    SlotBound(Slot.GOAL, 1, "One objective per workspace. Two goals is two requests."),
    SlotBound(Slot.ACTOR, 1, "One account. The scope is the workspace, not a field on it."),
    SlotBound(Slot.SCREEN, 1, "Where the person is now, not where they have been."),
    SlotBound(Slot.RESOURCE, 4, "The things being acted on, few enough to name in a sentence."),
    SlotBound(Slot.SKILL, 6, "Capabilities in play. Never the registry; that is the point."),
    SlotBound(Slot.CONSTRAINT, 8, "Stated limits. Generous because losing one is the worst failure here."),
    SlotBound(Slot.EVIDENCE, 12, "Observations for this account, this request."),
    SlotBound(Slot.UNKNOWN, 6, "Recorded gaps. More than six unknowns is a request to ask, not to plan."),
    SlotBound(Slot.RISK, 4, "What could go wrong, carried rather than recomputed."),
)

BY_SLOT: dict[Slot, SlotBound] = {bound.slot: bound for bound in SLOTS}


@dataclass(frozen=True)
class Item:
    """One entry. Text, not an object.

    The workspace holds rendered text because an object in here is an object whose
    ``repr`` eventually reaches a log, whose contents can be mutated by whoever else
    holds a reference after it was admitted, and whose size cannot be counted against a
    character budget without guessing. Text is inspectable, immutable, and countable.

    ``source`` is required and free-form by design — ``"user"``,
    ``"tool:pulse.alerts.list"``, ``"memory:preference"``, ``"knowledge:services/x.py"``.
    An entry whose origin nobody recorded is an entry that cannot be re-checked when it
    turns out to have been wrong.
    """

    slot: Slot
    key: str
    value: str
    source: str
    confidence: float = 0.0
    #: Explicit opt-in to surviving :meth:`Workspace.close`. Never defaulted on.
    retain: bool = False
    #: How many times this entry has been corrected through :meth:`Workspace.revise`.
    #: A count rather than a flag so the ceiling has something to compare against and
    #: so "corrected once" and "corrected repeatedly" stay distinguishable in a report.
    revisions: int = 0
    at: float = 0.0

    @property
    def revised(self) -> bool:
        return self.revisions > 0

    @property
    def chars(self) -> int:
        return len(self.key) + len(self.value)


@dataclass(frozen=True)
class Summary:
    """What one workspace leaves behind, which is deliberately very little.

    Returned by :meth:`Workspace.close` and written nowhere. Persisting any of it is the
    caller's decision, made through :mod:`services.undx_brain.memory` with its owner
    scope, which is a visible act with a flag in front of it.
    """

    owner_id: int = 0
    goal: str = ""
    completed: bool = False
    abandoned: bool = False
    reason: str = ""
    placed: int = 0
    refused: int = 0
    revised: int = 0
    elapsed_seconds: float = 0.0
    #: Only entries placed with ``retain=True``, and never any at all from an abandoned
    #: workspace.
    retainable: tuple[Item, ...] = ()
    #: Carried in words because an unanswered question and an unmitigated risk are the
    #: two things worth knowing about a request that did not finish.
    unknowns: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.completed


class Workspace:
    """One request's working context. Mutable, bounded, owner-scoped, temporary.

    A class rather than a frozen dataclass because it accumulates, and the accumulation
    is the thing being governed. Like :class:`services.undx_brain.bounds.Ledger` it
    spends and does not refund: there is no ``remove``, no ``clear`` that leaves the
    workspace usable, and no way to raise a ceiling after opening.
    """

    __slots__ = (
        "_owner_id", "_ok", "_reason", "_max_items", "_max_chars", "_ttl",
        "_clock", "_started", "_items", "_chars", "_placed", "_refused",
        "_revised", "_closed", "_summary", "_notes",
    )

    def __init__(
        self,
        owner: int = 0,
        *,
        ok: bool = False,
        reason: str = "",
        max_items: int = 24,
        max_chars: int = 8000,
        ttl_seconds: int = 300,
        clock: Callable[[], float] | None = None,
        notes: tuple[str, ...] = (),
    ) -> None:
        self._owner_id = int(owner)
        self._ok = bool(ok)
        self._reason = str(reason)
        self._max_items = int(max_items)
        self._max_chars = int(max_chars)
        self._ttl = int(ttl_seconds)
        # Monotonic, and injectable for the same reason the Ledger's is: a wall-clock
        # adjustment must not extend or shorten a request's working context, and the
        # expiry tests should measure the rule rather than the test suite's patience.
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._items: list[Item] = []
        self._chars = 0
        self._placed = 0
        self._refused = 0
        self._revised = 0
        self._closed = False
        self._summary: Summary | None = None
        self._notes = tuple(notes)

    # ------------------------------------------------------------------ state ----

    def __bool__(self) -> bool:
        return self._ok and not self._closed and not self.expired()

    @property
    def owner_id(self) -> int:
        return self._owner_id

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def notes(self) -> tuple[str, ...]:
        return self._notes

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    def expired(self) -> bool:
        return self._ok and self.elapsed >= float(self._ttl)

    @property
    def chars(self) -> int:
        return self._chars

    def __len__(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------ entry ----

    def place(
        self,
        slot: Any,
        key: str,
        value: str,
        *,
        source: str,
        confidence: float = 0.0,
        retain: bool = False,
        owner: Any = None,
    ) -> Refusal:
        """Put one entry in the workspace, or refuse and say which bound was hit.

        Returns a :class:`services.undx_brain.bounds.Refusal` — empty when the entry was
        accepted — rather than raising or returning a bare ``False``. Reused rather than
        redeclared, because a second refusal type in the same package is a second thing
        for a caller to remember to check, and the one they forget is the one that
        matters.

        ``owner`` is required for :attr:`Slot.EVIDENCE` and refused when it does not
        resolve to this workspace's account. It is not required elsewhere because a goal
        or a constraint has no account, and demanding one there would make it a value
        callers pass reflexively — which is how a required argument stops being a check.
        """
        return self._place(
            slot, key, value, source=source, confidence=confidence,
            retain=retain, owner=owner, revising=False,
        )

    def revise(
        self,
        slot: Any,
        key: str,
        value: str,
        *,
        source: str,
        confidence: float = 0.0,
        retain: bool = False,
        owner: Any = None,
    ) -> Refusal:
        """Replace an existing entry, on purpose and on the record.

        The separate method exists so that changing what UNDX believes is a different
        call from establishing it. A correction is legitimate and common — the person
        says "no, the Ethereum one" — and it must be possible. What must not be possible
        is a correction that happens because two callers both wrote to ``resource`` and
        the second one won.

        A revision replaces in place, keeps the slot's occupancy unchanged, and marks the
        entry so :meth:`inspect` and :class:`Summary` both report that the workspace
        changed its mind.
        """
        return self._place(
            slot, key, value, source=source, confidence=confidence,
            retain=retain, owner=owner, revising=True,
        )

    # ------------------------------------------------------------------- read ----

    def items(self, slot: Any = None) -> tuple[Item, ...]:
        """The entries, in the order they were placed. Contents, so a deliberate call."""
        if slot is None:
            return tuple(self._items)
        resolved = _slot(slot)
        if resolved is None:
            return ()
        return tuple(item for item in self._items if item.slot is resolved)

    def value(self, slot: Any, key: str) -> str:
        """One entry's text, or ``""``. Never raises: a missing entry is a normal state."""
        resolved = _slot(slot)
        if resolved is None:
            return ""
        for item in self._items:
            if item.slot is resolved and item.key == str(key):
                return item.value
        return ""

    def inspect(self) -> dict[str, Any]:
        """Shape without content. Safe to log on every request.

        No values, no keys — a key is chosen by this system rather than by a person, but
        "which keys are set" is still a description of what somebody is doing, and this
        is the surface that ends up in a log line that outlives the request. Slots,
        counts, ceilings, budgets and sources are enough to debug "why did it refuse"
        without any of it being about the person.
        """
        counts = {bound.slot.value: 0 for bound in SLOTS}
        for item in self._items:
            counts[item.slot.value] += 1
        return {
            "owner_scoped": bool(self._owner_id),
            "ok": self._ok,
            "reason": self._reason,
            "closed": self._closed,
            "expired": self.expired(),
            "items": len(self._items),
            "max_items": self._max_items,
            "chars": self._chars,
            "max_chars": self._max_chars,
            "counts": counts,
            "limits": {bound.slot.value: bound.limit for bound in SLOTS},
            # Sorted so two identical workspaces produce identical log lines and a diff
            # between two requests means something.
            "sources": sorted({item.source for item in self._items}),
            "retained": sum(1 for item in self._items if item.retain),
            "placed": self._placed,
            "refused": self._refused,
            "revised": self._revised,
            "elapsed_seconds": round(self.elapsed, 3),
            "ttl_seconds": self._ttl,
            "notes": list(self._notes),
        }

    # ------------------------------------------------------------------- exit ----

    def close(self, *, completed: bool) -> Summary:
        """End the workspace, empty it, and return what survives.

        ``completed`` is keyword-only with no default, the same discipline
        :meth:`services.undx_brain.bounds.Ledger.may_retry` applies to ``write``. A
        caller must state whether the objective was actually met, because a default
        would make "finished" the thing that happens when nobody thought about it, and
        a workspace that reports completion it was never told about is a false success
        claim with extra steps.

        Closing is idempotent: a second call returns the same summary rather than a
        second, emptier one.
        """
        if self._summary is not None:
            return self._summary

        abandoned = self.expired()
        # An expired workspace carries nothing forward even if entries were marked to
        # retain. Bounds says it plainly for plans and it is true here: what this
        # observed about the account before the pause is no longer evidence of anything
        # current, and promoting a stale observation to durable memory is worse than
        # losing it.
        retainable: tuple[Item, ...] = (
            () if abandoned else tuple(item for item in self._items if item.retain)
        )
        goals = [item.value for item in self._items if item.slot is Slot.GOAL]
        summary = Summary(
            owner_id=self._owner_id,
            # Read by slot rather than by a hardcoded key name. Assuming the key is
            # literally ``"goal"`` would make the summary silently goal-less for every
            # caller who labelled it anything else, and a summary that reports no
            # objective is one nobody notices is wrong.
            goal=goals[0] if goals else "",
            # Three things must all hold before this says "completed": the caller said
            # so, the context did not expire, and it was actually open. The last is the
            # subtle one — a workspace that was never opened held nothing, observed
            # nothing and bounded nothing, so a summary of it reporting success is a
            # success claim about work this module has no evidence of.
            completed=bool(completed) and not abandoned and self._ok,
            abandoned=abandoned,
            reason=(
                f"the working context passed its {self._ttl}s bound and was abandoned"
                if abandoned
                else (self._reason if not self._ok else "")
            ),
            placed=self._placed,
            refused=self._refused,
            revised=self._revised,
            elapsed_seconds=round(self.elapsed, 3),
            retainable=retainable,
            unknowns=tuple(item.value for item in self._items if item.slot is Slot.UNKNOWN),
            risks=tuple(item.value for item in self._items if item.slot is Slot.RISK),
        )
        self._items = []
        self._chars = 0
        self._closed = True
        self._summary = summary
        return summary

    # -------------------------------------------------------------- internals ----

    def _place(
        self,
        slot: Any,
        key: str,
        value: str,
        *,
        source: str,
        confidence: float,
        retain: bool,
        owner: Any,
        revising: bool,
    ) -> Refusal:
        # Order matters and is the same order a reviewer would want to read it in:
        # lifecycle, then shape, then safety, then identity, then budget. Budget is last
        # so that a malformed or unsafe entry is never the thing that fills the
        # workspace up.
        refusal = self._lifecycle_refusal()
        if refusal:
            self._refused += 1
            return refusal

        resolved = _slot(slot)
        if resolved is None:
            self._refused += 1
            return Refusal(
                bound="slot", limit=len(SLOTS), requested=0,
                message=(
                    f"{slot!r} is not one of the nine working-context slots; there is no "
                    f"general-purpose slot on purpose"
                ),
            )

        name = str(key or "").strip()
        text = str(value or "").strip()
        origin = str(source or "").strip()
        if not name:
            self._refused += 1
            return Refusal(bound="key", limit=MAX_KEY_CHARS, requested=0,
                           message="an entry without a key cannot be found again")
        if len(name) > MAX_KEY_CHARS:
            self._refused += 1
            return Refusal(
                bound="key", limit=MAX_KEY_CHARS, requested=len(name),
                message=(
                    f"the key is {len(name)} characters and the limit is {MAX_KEY_CHARS}; "
                    f"a key that long is content wearing a label's name"
                ),
            )
        if not text:
            self._refused += 1
            return Refusal(
                bound="value", limit=MAX_VALUE_CHARS, requested=0,
                message=(
                    f"{resolved.value}.{name} has no value; an empty entry reads as "
                    f"'known and empty' rather than 'not known'"
                ),
            )
        if len(text) > MAX_VALUE_CHARS:
            self._refused += 1
            return Refusal(
                bound="value", limit=MAX_VALUE_CHARS, requested=len(text),
                message=(
                    f"{resolved.value}.{name} is {len(text)} characters and one entry may "
                    f"be {MAX_VALUE_CHARS}; it is refused rather than cut, because a "
                    f"truncated observation still reads as a whole one"
                ),
            )
        if not origin:
            self._refused += 1
            return Refusal(
                bound="source", limit=0, requested=0,
                message=(
                    f"{resolved.value}.{name} names no source; an entry nobody can trace "
                    f"cannot be re-checked when it turns out to be wrong"
                ),
            )

        secret = _secret_shaped(f"{name} {text} {origin}")
        if secret:
            self._refused += 1
            # Deliberately does not quote the value. A refusal message that echoes the
            # thing it refused has moved the secret into the log rather than out of it.
            return Refusal(
                bound="secret", limit=0, requested=0,
                message=(
                    f"{resolved.value}.{name} looks like a credential and is refused at "
                    f"the door; the value is not repeated here"
                ),
            )

        if resolved is Slot.EVIDENCE:
            refusal = self._owner_refusal(name, owner)
            if refusal:
                self._refused += 1
                return refusal
        elif owner is not None:
            refusal = self._owner_refusal(name, owner)
            if refusal:
                self._refused += 1
                return refusal

        position = self._find(resolved, name)
        existing = self._items[position] if position >= 0 else None
        if existing is not None and not revising:
            if existing.value == text:
                # Placing the same thing twice is not a contradiction and must not cost
                # budget; a caller that re-establishes a known constraint is being
                # careful, not wasteful.
                return Refusal()
            self._refused += 1
            return Refusal(
                bound="conflict", limit=1, requested=2,
                message=(
                    f"{resolved.value}.{name} already holds a different value and is not "
                    f"overwritten; call revise() if this is a correction, because a value "
                    f"that changes silently between understanding and acting is how the "
                    f"wrong thing gets changed"
                ),
            )
        if existing is None and revising:
            self._refused += 1
            return Refusal(
                bound="conflict", limit=1, requested=0,
                message=(
                    f"{resolved.value}.{name} is not set, so there is nothing to revise; "
                    f"place() establishes, revise() corrects"
                ),
            )

        if existing is not None and existing.revisions + 1 > MAX_REVISIONS:
            self._refused += 1
            return Refusal(
                bound="revisions", limit=MAX_REVISIONS, requested=existing.revisions + 1,
                message=(
                    f"{resolved.value}.{name} has already been corrected "
                    f"{existing.revisions} times, which is its limit; a value that keeps "
                    f"being re-resolved is a loop, and the refusal is here so it surfaces "
                    f"as a failure rather than as a request that quietly runs out of time"
                ),
            )

        entry = Item(
            slot=resolved, key=name, value=text, source=origin,
            confidence=_confidence(confidence), retain=bool(retain),
            revisions=(existing.revisions + 1) if existing is not None else 0,
            at=self.elapsed,
        )

        if existing is not None:
            # A revision changes the character count by the difference, and does not
            # consume a slot, because the slot is already occupied by the thing being
            # corrected.
            projected = self._chars - existing.chars + entry.chars
            if projected > self._max_chars:
                self._refused += 1
                return Refusal(
                    bound="chars", limit=self._max_chars, requested=projected,
                    message=(
                        f"revising {resolved.value}.{name} would take the working context "
                        f"to {projected} characters and the budget is {self._max_chars}"
                    ),
                )
            self._items[position] = entry
            self._chars = projected
            self._revised += 1
            self._placed += 1
            return Refusal()

        bound = BY_SLOT[resolved]
        occupied = sum(1 for item in self._items if item.slot is resolved)
        if occupied + 1 > bound.limit:
            self._refused += 1
            return Refusal(
                bound=f"slot:{resolved.value}", limit=bound.limit, requested=occupied + 1,
                message=(
                    f"the {resolved.value} slot holds {bound.limit} and is full: "
                    f"{bound.purpose} The new entry is refused rather than displacing an "
                    f"older one, because the oldest entry is usually the one the person "
                    f"stated first"
                ),
            )
        if len(self._items) + 1 > self._max_items:
            self._refused += 1
            return Refusal(
                bound="items", limit=self._max_items, requested=len(self._items) + 1,
                message=(
                    f"the working context already holds {len(self._items)} entries, which "
                    f"is its budget; this is the ceiling that stops the whole corpus, all "
                    f"of memory, and every capability arriving one reasonable addition at "
                    f"a time"
                ),
            )
        projected = self._chars + entry.chars
        if projected > self._max_chars:
            self._refused += 1
            return Refusal(
                bound="chars", limit=self._max_chars, requested=projected,
                message=(
                    f"adding {resolved.value}.{name} would take the working context to "
                    f"{projected} characters and the budget is {self._max_chars}"
                ),
            )

        self._items.append(entry)
        self._chars = projected
        self._placed += 1
        return Refusal()

    def _lifecycle_refusal(self) -> Refusal:
        if not self._ok:
            return Refusal(
                bound="disabled", limit=0, requested=0,
                message=self._reason or "this working context was never opened",
            )
        if self._closed:
            return Refusal(
                bound="closed", limit=0, requested=0,
                message=(
                    "this working context has been closed; a request that needs one "
                    "opens a new one rather than reopening a summarised one"
                ),
            )
        if self.expired():
            return Refusal(
                bound="lifetime", limit=self._ttl, requested=int(self.elapsed),
                message=(
                    f"this working context passed its {self._ttl}s bound and is abandoned "
                    f"rather than resumed; what it observed is no longer evidence of "
                    f"anything current"
                ),
            )
        return Refusal()

    def _owner_refusal(self, name: str, owner: Any) -> Refusal:
        if owner is None:
            return Refusal(
                bound="owner", limit=self._owner_id, requested=0,
                message=(
                    f"evidence.{name} does not say whose account it is about; the "
                    f"argument is required so that carrying one request's observation "
                    f"into another account's context takes a deliberate lie rather than "
                    f"an omission"
                ),
            )
        resolved = memory.owner_id(owner)
        if resolved is None:
            return Refusal(
                bound="owner", limit=self._owner_id, requested=0,
                message=(
                    f"{owner!r} does not resolve to an account, so an entry cannot be "
                    f"attributed to one"
                ),
            )
        if resolved != self._owner_id:
            return Refusal(
                bound="owner", limit=self._owner_id, requested=resolved,
                message=(
                    f"this working context belongs to one account and the entry belongs "
                    f"to another; two accounts in one request is how one person is told "
                    f"about another person's alert"
                ),
            )
        return Refusal()

    def _find(self, slot: Slot, key: str) -> int:
        """The position of an entry, or ``-1``. A position rather than the item itself
        because replacing by ``list.index`` would look the entry up by *equality*, and
        two entries that compare equal are two entries either of which might be the one
        overwritten."""
        for position, item in enumerate(self._items):
            if item.slot is slot and item.key == key:
                return position
        return -1


def open_workspace(
    owner: Any,
    *,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], float] | None = None,
) -> Workspace:
    """Open a working context for one account and one request.

    Always returns a :class:`Workspace`, never ``None`` and never an exception. A caller
    that cannot get one has no safe fallback — it would assemble context by hand, which
    is the unbounded behaviour this module exists to replace — so a refusal arrives as a
    workspace that refuses every entry and says why, which the caller can inspect and
    log.

    There is no cache and no registry of open workspaces, deliberately. A function that
    returned an existing workspace for a returning user would make expiry resumable, and
    the whole point of the timeout is that it is not.
    """
    resolution = brain_config.resolve(env)
    values = resolution.values
    notes = tuple(resolution.notes)

    max_items = _positive(values.get("UNDX_WORKSPACE_MAX_ITEMS"), 24)
    max_chars = _positive(values.get("UNDX_WORKSPACE_MAX_CHARS"), 8000)
    ttl = _positive(values.get("UNDX_WORKSPACE_TTL_SECONDS"), 300)

    # Same resolver the Memory Brain uses, so an id it would refuse a scope for is an id
    # this refuses a workspace for. Two answers to "is this a valid account?" in one
    # package is one answer too many.
    resolved = memory.owner_id(owner)
    common = dict(max_items=max_items, max_chars=max_chars, ttl_seconds=ttl,
                  clock=clock, notes=notes)

    if resolved is None:
        return Workspace(
            0, ok=False,
            reason=f"no account could be resolved from {owner!r}, so nothing is scoped",
            **common,
        )
    if not bool(values.get("UNDX_BRAIN_ENABLED", False)):
        return Workspace(resolved, ok=False, reason="the Brain layer is disabled", **common)
    if not bool(values.get("UNDX_BRAIN_WORKSPACE_ENABLED", False)):
        return Workspace(
            resolved, ok=False,
            reason="the bounded working context is disabled; context is assembled by "
                   "each call site as it is today",
            **common,
        )
    return Workspace(resolved, ok=True, **common)


# ---------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------


def _slot(raw: Any) -> Slot | None:
    """Resolve a slot, or ``None``. Accepts the enum or its exact string name.

    Not case-insensitive and not fuzzy. ``"Evidence"`` failing loudly is better than it
    quietly creating nothing, and a near-miss that resolves is a near-miss that hides a
    typo in a caller until the day the entry it was meant to place is the one that
    mattered.
    """
    if isinstance(raw, Slot):
        return raw
    if isinstance(raw, str):
        try:
            return Slot(raw)
        except ValueError:
            return None
    return None


def _secret_shaped(text: str) -> str:
    """The name of the pattern a value matched, or ``""``.

    Reuses the corpus filter rather than declaring a second list, for the reason the
    corpus module gives about its own overlap with the audit script: a defence that
    exists in only one place is a defence that a later change walks past.
    """
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return ""


def _confidence(raw: Any) -> float:
    """A confidence in 0.0-1.0. An unreadable one is 0.0, never 1.0.

    The recurring rule in this package, applied to a number instead of a flag: a value
    nobody can parse must land on the cautious reading. A confidence that defaulted high
    would let a malformed float become the reason UNDX stated something as certain.
    """
    if isinstance(raw, bool):
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN, which compares false against every bound below
        return 0.0
    return max(0.0, min(1.0, value))


def _positive(raw: Any, fallback: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback
