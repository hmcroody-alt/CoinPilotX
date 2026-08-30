"""What a durable run is actually doing, said in one word, without rounding.

A queue row carries a storage status (``queued``, ``running``, ``succeeded`` …) that was
chosen for the claim query, not for a person. ``succeeded`` in particular means "the
worker finished without raising", which is a statement about the worker and not about the
account. Handed to a client unchanged it reads as "done", and a run that executed a write
nobody could read back would render under a receipt kicker. That is the exact failure the
verification chain exists to prevent, arriving by the one route the verification chain
does not watch.

So the projection lives here, once, rather than in the route that happens to need it
first. There will be more readers — a detail endpoint, a list endpoint, an eventual push
surface, the native client's own rendering — and the way this goes wrong is three of them
agreeing about ten states and disagreeing about the eleventh.

Two rules shape every entry in the table below.

**Nothing is compressed to "processing".** A person asking what happened to their request
is owed the difference between "no worker has taken this yet", "a worker is running it
right now", "it failed once and will be tried again" and "it is waiting for you". Those
are four different things to do next, and a single word covering all of them tells
somebody to wait when they should act.

**Nothing is promoted.** :data:`COMPLETED` is reachable from exactly one place: a stored
``succeeded`` whose outcome :mod:`services.undx_brain.evidence` licenses a completion
claim, which today means ``verified_success`` and nothing else. A run that executed and
could not be read back is :data:`PARTIAL` — it ran, and what it did to the account is not
confirmed — and a stored ``partial`` cannot be projected upward out of that no matter what
the Brain says about its outcome. The decision is delegated rather than reimplemented,
because a second copy of "may we say done" is a second thing to keep correct and the two
would diverge on the day one was patched.

Two declared states are still not observable, and that is recorded in
:data:`UNOBSERVABLE_STATUSES` rather than handled by leaving them out of the vocabulary.
Keeping them in means a client written today already renders everything the queue can
eventually produce, and means the gap is a fact a test holds rather than something to be
rediscovered. It shrinks as the queue learns to distinguish more: ``WAITING_CONFIRMATION``
and ``PARTIAL`` were on that list until the claim guard and the three-way settlement in
:mod:`services.undx_agent_runs` started producing them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from services.undx_agent_contracts import RunConfirmation

logger = logging.getLogger(__name__)

__all__ = [
    "ALL_STATUSES",
    "TERMINAL_STATUSES",
    "UNOBSERVABLE_STATUSES",
    "RunStatus",
    "Projection",
    "project",
    "describe",
]


class RunStatus:
    """The complete vocabulary a client may be shown for a durable run.

    Deliberately not the storage vocabulary. Two of these (``CLAIMED``, ``VERIFYING``)
    are narrower than any storage status and one (``UNKNOWN``) has no storage counterpart
    at all.
    """

    #: Accepted, written down, and not yet taken by any worker.
    QUEUED = "queued"
    #: A worker holds the lease and has not entered the executor.
    #:
    #: Not currently distinguishable from :data:`RUNNING`: ``claim_next`` writes
    #: ``status='running'`` in the same statement that takes the lease, so there is no
    #: instant at which the row says "claimed but not started". Declared anyway — see the
    #: module docstring.
    CLAIMED = "claimed"
    #: A worker is executing the action now.
    RUNNING = "running"
    #: Stopped, on purpose, until the person answers. Occupies no worker.
    WAITING_CONFIRMATION = "waiting_confirmation"
    #: The executor returned and the independent read-back is in progress.
    #:
    #: Also not currently distinguishable: verification happens inside the single
    #: :func:`services.undx_tool_gateway.execute` call, so from the row's point of view
    #: it is part of ``running``.
    VERIFYING = "verifying"
    #: Attempted, did not finish, and eligible to be attempted again. Distinct from
    #: :data:`QUEUED` because "not started yet" and "started and came back" are different
    #: news, and distinct from :data:`FAILED` because attempts remain.
    RETRY_WAIT = "retry_wait"
    #: Finished, and what it did to the account is not confirmed. Not a failure and
    #: emphatically not a success.
    PARTIAL = "partial"
    #: Finished, and an independent read confirmed it. The only status that licenses
    #: telling somebody their change is done.
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: The run outlived its own deadline — or its approval's — before a worker reached
    #: it. Nothing was executed.
    EXPIRED = "expired"
    #: The stored row does not project onto any of the above.
    #:
    #: Present so that :func:`project` is total. A row with a status this module does not
    #: recognise is a bug in some other module, and the two tempting ways to absorb it —
    #: calling it queued, calling it failed — are both assertions about the account that
    #: nothing supports. ``UNKNOWN`` is the fail-closed reading: it claims no progress,
    #: claims no completion, and is not terminal, so nothing stops watching it.
    UNKNOWN = "unknown"


ALL_STATUSES: tuple[str, ...] = (
    RunStatus.QUEUED, RunStatus.CLAIMED, RunStatus.RUNNING,
    RunStatus.WAITING_CONFIRMATION, RunStatus.VERIFYING, RunStatus.RETRY_WAIT,
    RunStatus.PARTIAL, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
    RunStatus.EXPIRED, RunStatus.UNKNOWN,
)

#: Nothing further will happen without a new request. ``UNKNOWN`` is absent on purpose:
#: an unreadable row has not been shown to be finished, and a client that stopped polling
#: it would stop watching a run that is still live.
TERMINAL_STATUSES = frozenset({
    RunStatus.PARTIAL, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
    RunStatus.EXPIRED,
})

#: Declared, rendered by clients, and not yet produced by :func:`project`. Kept as data
#: so the shortfall is asserted by a test rather than remembered.
UNOBSERVABLE_STATUSES = frozenset({RunStatus.CLAIMED, RunStatus.VERIFYING})

#: Storage statuses that mean the run stopped without executing anything the person
#: needs told about beyond the fact that it stopped.
_STORED_TERMINAL = {
    "cancelled": RunStatus.CANCELLED,
    "expired": RunStatus.EXPIRED,
    "failed": RunStatus.FAILED,
    # Attempts exhausted. Terminal, and reported as a failure rather than as its own
    # state, because "dead letter" is an operator's word for a queue and the person's
    # question is only whether their request happened.
    "dead_letter": RunStatus.FAILED,
}

#: Confirmation states that mean the run is parked on a person rather than on a worker.
#:
#: Imported rather than restated. :mod:`services.undx_agent_runs` reads the identical set
#: to decide that a worker may not claim such a row, and two literal copies of it would
#: drift in the one direction that is silently wrong: a run this module calls "waiting for
#: you" that the claim query picks up and executes anyway. The contracts module is
#: stdlib-only, so this costs a read path nothing.
_PENDING_CONFIRMATION = RunConfirmation.PENDING_STATES

#: What each status means in a sentence, for logs and for clients that would otherwise
#: write their own. Held here so that the word and its explanation cannot drift apart.
_DESCRIPTIONS: dict[str, str] = {
    RunStatus.QUEUED: "Accepted. No worker has picked this up yet.",
    RunStatus.CLAIMED: "A worker has taken this and is about to start.",
    RunStatus.RUNNING: "A worker is doing this now.",
    RunStatus.WAITING_CONFIRMATION: "Waiting for you to confirm before anything runs.",
    RunStatus.VERIFYING: "Done running. Checking the account to confirm what changed.",
    RunStatus.RETRY_WAIT: "An attempt did not finish. This will be tried again.",
    RunStatus.PARTIAL: "This ran, and the result could not be confirmed.",
    RunStatus.COMPLETED: "Done, and confirmed by a separate read of your account.",
    RunStatus.FAILED: "This did not happen.",
    RunStatus.CANCELLED: "Cancelled before it ran.",
    RunStatus.EXPIRED: "This expired before a worker reached it. Nothing was done.",
    RunStatus.UNKNOWN: "The state of this request could not be read.",
}

#: The ``COMPLETED`` sentence for a run that read rather than wrote.
#:
#: The general sentence promises a read-back — "confirmed by a separate read of your
#: account" — which a read-only capability never performs: it declares no verifier, so its
#: receipt records ``impossible_to_verify`` and nothing was checked a second time. The
#: promise went unnoticed because until the settlement predicate was fixed no run of any
#: kind reached ``COMPLETED``, so this string had never been shown to anyone. Widening the
#: read path made it reachable and false in the same change, which is why it is fixed in
#: the same change: a status line that invents a verification is the same class of untruth
#: as the ``failed`` it replaced, pointed the other way.
_COMPLETED_READ_DESCRIPTION = "Done. This looked something up and changed nothing."


@dataclass(frozen=True)
class Projection:
    """One run's public state, and the two booleans callers otherwise recompute wrongly."""

    status: str
    terminal: bool
    #: Whether a **change** to the account may be reported as done. This is the Brain's
    #: :func:`services.undx_brain.evidence.may_say_done` verbatim, so it is ``False`` for
    #: a successful read — not because the read went badly but because a lookup completes
    #: nothing, and a client that said "done" about one would be describing a change that
    #: never happened. Pair it with ``status``, which is what finished; this is what
    #: changed.
    may_claim_completed: bool
    #: Whether the client must hedge. ``True`` means the honest sentence contains "I sent
    #: that and could not confirm it"; ``False`` means the result stands on its own. This
    #: is the field that separates a verified write and a successful read (both ``False``)
    #: from an unverified write (``True``), which ``may_claim_completed`` cannot do alone.
    requires_disclosure: bool
    description: str
    #: Why this projection and not the neighbouring one. Written for a log line, not for
    #: a person; the person's sentence is ``description``.
    reason: str = ""


def project(row: Mapping[str, Any], *, now: datetime | None = None) -> Projection:
    """Read one stored run row as the state a client may be shown.

    Never raises and never returns ``None``. A caller holding a row it cannot project has
    no better fallback available to it than this module's, and inventing one at the call
    site is how a status gets rounded up.
    """
    try:
        return _project(row, now=now or datetime.now(timezone.utc))
    except Exception:  # pragma: no cover - a projection must not break a read
        logger.warning("undx_run_projection_failed run=%s",
                       (row or {}).get("run_id"), exc_info=True)
        return _make(RunStatus.UNKNOWN, reason="the stored row could not be projected")


def describe(status: str) -> str:
    """The sentence for a status, or the ``UNKNOWN`` sentence for anything unrecognised."""
    return _DESCRIPTIONS.get(str(status or ""), _DESCRIPTIONS[RunStatus.UNKNOWN])


def _project(row: Mapping[str, Any], *, now: datetime) -> Projection:
    stored = str(row.get("status") or "").strip().lower()

    if stored in _STORED_TERMINAL:
        return _make(_STORED_TERMINAL[stored], reason=f"stored status is {stored!r}")

    if stored == "partial":
        # Settled, and settled as unconfirmed. The queue already decided this — see
        # :func:`services.undx_agent_runs._settled_status` — so the status is not in
        # question here and the Brain is consulted only for the two claim booleans that
        # travel with it. Notably this branch cannot reach ``COMPLETED`` whatever the
        # Brain says: nothing is promoted, and a stored ``partial`` that projected as done
        # would be the rounding this module exists to make unavailable.
        return _make(
            RunStatus.PARTIAL, _assess(str(row.get("outcome") or ""), row),
            reason="the run was settled as executed-but-unconfirmed",
        )

    if stored == "succeeded":
        # The one place a completion claim can be reached, and it is not reached by
        # reading ``succeeded``. The outcome is put to the Brain, which answers the same
        # question here that it answers for an in-request turn.
        outcome = str(row.get("outcome") or "")
        assessment = _assess(outcome, row)
        if assessment.requires_disclosure:
            return _make(
                RunStatus.PARTIAL, assessment,
                reason=(f"the run finished with outcome {outcome or 'unrecorded'!r}, "
                        f"which the Brain reads as {assessment.state} — a result that "
                        f"cannot be stated without a hedge"),
            )
        # ``requires_disclosure`` rather than ``may_claim_done`` is what separates these
        # two, and the difference is a read. A successful lookup reaches ``retrieved``,
        # which licenses no *completion* claim — there was no change to complete — but
        # needs no hedge either, and calling it ``PARTIAL`` would tell somebody their
        # summary half-arrived. ``may_claim_done`` alone cannot make that distinction; it
        # is ``False`` for a healthy read and for a lost write alike.
        is_write = _is_write(row)
        return _make(
            RunStatus.COMPLETED, assessment,
            reason=(f"the run finished as {assessment.state} and needs no disclosure"),
            description="" if is_write else _COMPLETED_READ_DESCRIPTION,
        )

    if _awaiting_confirmation(row):
        # Checked ahead of the queued/running split because a run parked on a person is
        # not waiting on capacity, and telling them it is queued invites them to wait for
        # something that will never arrive on its own.
        return _make(RunStatus.WAITING_CONFIRMATION,
                     reason="the run holds an unanswered confirmation")

    if stored == "running":
        if _lease_lapsed(row, now):
            # The lease is gone and the row still says running: whichever container held
            # it is not coming back. It is reclaimable, so this is a wait, not a failure
            # — and specifically not ``RUNNING``, which would report a worker that does
            # not exist as busy on the person's behalf.
            return _make(RunStatus.RETRY_WAIT,
                         reason="the worker lease lapsed and the run is reclaimable")
        return _make(RunStatus.RUNNING, reason="a worker holds a live lease")

    if stored == "queued":
        if _int(row.get("attempt_count")) > 0:
            return _make(RunStatus.RETRY_WAIT,
                         reason="the run has been attempted and returned to the queue")
        return _make(RunStatus.QUEUED, reason="the run has never been attempted")

    return _make(RunStatus.UNKNOWN, reason=f"{stored!r} is not a stored run status")


@dataclass(frozen=True)
class _Verdict:
    """The Brain's reading of a finished run, or the cautious stand-in for it."""

    state: str
    may_claim_done: bool
    requires_disclosure: bool


#: What a run is read as when the Brain cannot be consulted. Fail-closed in both fields:
#: no completion claim, and the client must hedge.
_UNREADABLE = _Verdict(state="unknown", may_claim_done=False, requires_disclosure=True)


def _assess(outcome: str, row: Mapping[str, Any]) -> _Verdict:
    """Put a finished run's outcome to the Brain and carry back its whole answer.

    Delegated rather than compared against ``"verified_success"`` here. The literal is
    right today; the point is that when the rule changes it changes in one module — and
    that the two facts this projection needs, "may we say done" and "must we hedge", come
    from one derivation rather than from two comparisons that can disagree.

    Guarded, because this sits on a read path: a Brain that fails to import must degrade
    to the cautious answer rather than to a 500 on somebody's run list.
    """
    try:
        from services.undx_brain import evidence
    except Exception:  # pragma: no cover - the Brain is optional at import time
        logger.warning("undx_run_status_brain_unavailable", exc_info=True)
        return _UNREADABLE
    try:
        assessment = evidence.derive(
            outcome,
            # The row records no separate verification verdict, because the stored
            # outcome already *is* the gateway's answer after its read-back ran. Passing
            # ``verified`` lets ``derive`` do the family check it exists to do; passing
            # ``None`` would mean "no read-back happened", which is false here and would
            # downgrade every finished write to ``executed``.
            "verified",
            is_write=_is_write(row),
        )
    except Exception:  # pragma: no cover
        logger.warning("undx_run_status_evidence_failed", exc_info=True)
        return _UNREADABLE
    return _Verdict(
        state=getattr(assessment.state, "value", str(assessment.state)),
        may_claim_done=bool(assessment.may_claim_done),
        requires_disclosure=bool(assessment.requires_disclosure),
    )


def _is_write(row: Mapping[str, Any]) -> bool:
    """Whether this run's capability mutates anything.

    Defaults to ``True``. A capability the registry no longer declares is held to the
    stricter of the two readings, because the failure of guessing "read" is reporting an
    unconfirmed mutation as a completed one.
    """
    try:
        from services import undx_capability_registry

        spec = undx_capability_registry.get(str(row.get("capability_id") or ""))
    except Exception:  # pragma: no cover
        return True
    if spec is None:
        return True
    return bool(getattr(spec, "is_write", True))


def _awaiting_confirmation(row: Mapping[str, Any]) -> bool:
    return str(row.get("confirmation_state") or "").strip().lower() in _PENDING_CONFIRMATION


def _lease_lapsed(row: Mapping[str, Any], now: datetime) -> bool:
    """True when the recorded lease deadline has passed.

    An absent or unparseable deadline is *not* treated as lapsed. A row that says running
    with no readable lease is ambiguous, and the two errors are not equal: calling a live
    worker lapsed tells the person their request stalled while it is in fact executing.
    """
    raw = str(row.get("lease_expires_at") or "").strip()
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _make(status: str, verdict: _Verdict | None = None, *, reason: str = "",
          description: str = "") -> Projection:
    """Assemble a projection, taking the two claim fields from the Brain where there is one.

    ``verdict`` is ``None`` for every status that is not a finished run — nothing queued,
    running or cancelled has an outcome to assess — and those all take the fail-closed
    pair: no completion claim, and a hedge required. That is the right default for a run
    still in flight, whose result genuinely is not known yet.

    ``description`` overrides the status's stock sentence for the one case where the status
    alone underdetermines it: a completed read, where the stock sentence would claim a
    read-back that a read-only capability cannot perform. Empty means "use the stock
    sentence", which is every other branch.
    """
    resolved = verdict or _UNREADABLE
    return Projection(
        status=status,
        terminal=status in TERMINAL_STATUSES,
        may_claim_completed=resolved.may_claim_done,
        requires_disclosure=resolved.requires_disclosure,
        description=description or describe(status),
        reason=reason,
    )
