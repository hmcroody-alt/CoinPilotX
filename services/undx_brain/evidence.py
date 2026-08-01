"""The one place where "it worked" is decided, and the two fields that decide it.

:mod:`services.undx_brain.truth` defines what the evidence states *mean*. Nothing wrote
to them. The runtime carried its own pair — an ``AgentOutcome`` describing what the turn
concluded and a ``VerificationResult`` describing whether a read-back confirmed it — and
those two can disagree.

That disagreement is the whole subject of this module. There is exactly one dangerous
direction:

    outcome says ``verified_success``, verification says ``verification_pending``

Every part of the system that has ever reported something falsely has reported it
through some version of that pair. It is not a hypothetical: the outcome is assembled
from what the executor believed, the verification is assembled from what a *second* read
actually saw, and they are produced by different code at different times. A gateway that
returns success on a 200 and a read-back that has not run yet produce exactly this. Read
the outcome and you say "your alert is paused". Read the verification and you say "I
sent that; I have not confirmed it".

:func:`derive` always reads the verification. The outcome tells it *which* state family
it is in; the verification decides whether the strongest member of that family is
available. There is no argument, flag or environment variable that reverses this, because
the failure it prevents is not a configuration mistake — it is a claim made to a person
about their own account.

The opposite direction is checked too and matters less: a verification of ``verified``
attached to an outcome that never executed anything does not manufacture a success.
Verification is a *veto*, never a promotion. :func:`contradiction` names either case
without deciding anything, for callers that want to log or alarm on the disagreement
rather than only resolve it.

This module reaches *up* into ``services.undx_agent_contracts`` for the two enums so
that the mapping cannot drift from the definitions it maps. The import is guarded: the
Brain must not fail to load because a lower layer moved, and if the contracts are absent
:func:`derive` answers ``UNKNOWN``, which is the fail-closed reading — no basis for any
claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .truth import (
    EvidenceState,
    state_requires_disclosure,
    state_supports_completion_claim,
)

__all__ = [
    "Assessment",
    "derive",
    "contradiction",
    "may_say_done",
    "OUTCOME_FAMILY",
    "VERIFIED",
]

#: The verification verdict that licenses the strongest state in a family. Held as a
#: literal rather than imported so that a contracts package that fails to load does not
#: silently make every verification compare equal to nothing.
VERIFIED = "verified"

#: What each terminal outcome says about the request *before* verification is consulted.
#:
#: Written as a table rather than a chain of ``if`` statements so that adding an outcome
#: to ``AgentOutcome`` and forgetting to handle it here is a missing key — visible, and
#: caught by the test that walks ``AgentOutcome.ALL`` — instead of falling through an
#: ``else`` into whatever the last branch happened to be.
OUTCOME_FAMILY: dict[str, EvidenceState] = {
    # Executed and claiming success. The claim is *not* honoured here; this is the
    # family, and ``derive`` decides whether the family's strongest member applies.
    "verified_success": EvidenceState.EXECUTED,
    # Executed, explicitly not verified. Already the honest reading.
    "accepted_unverified": EvidenceState.EXECUTED,
    "confirmation_required": EvidenceState.AWAITING_CONFIRMATION,
    # A question was asked. Nothing was attempted, so nothing is proposed either — the
    # runtime does not yet know what it would do.
    "clarification_required": EvidenceState.UNKNOWN,
    # The person withdrew a staged action. It was proposed and it stopped there. Not a
    # failure, and specifically not ``EXECUTED``.
    "cancelled": EvidenceState.PROPOSED,
    "permission_denied": EvidenceState.UNKNOWN,
    "unsupported_capability": EvidenceState.UNKNOWN,
    "recoverable_failure": EvidenceState.UNKNOWN,
    "terminal_failure": EvidenceState.UNKNOWN,
}


@dataclass(frozen=True)
class Assessment:
    """What a turn is entitled to claim, and why.

    ``state`` is the answer. The rest exists so that a caller writing a response, a log
    line or an alarm does not have to re-derive any of it and reach a different
    conclusion than the one that was enforced.
    """

    state: EvidenceState = EvidenceState.UNKNOWN
    #: True only for ``VERIFIED_SUCCESS``. Kept as a field rather than left to the
    #: caller to compute, because the caller computing it is where it gets computed
    #: differently.
    may_claim_done: bool = False
    requires_disclosure: bool = True
    #: Set when the outcome and the verification disagreed. Empty when they did not.
    contradiction: str = ""
    #: Set when the claimed state was reduced. Names both readings, so the log line
    #: shows what was refused rather than only what was allowed.
    downgraded_from: EvidenceState | None = None
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.may_claim_done


def derive(outcome: Any, verification: Any = None, *, is_write: bool = True) -> Assessment:
    """Resolve an outcome and a verification into the state a response may rest on.

    ``verification`` may be a :class:`VerificationResult`, a bare state string, or
    ``None``. ``None`` is not "fine" — it means no read-back happened, which is exactly
    the case where an executed mutation must not be reported as done.

    ``is_write`` matters more than it looks. The gateway reaches ``verified_success``
    for a *read* simply by the read succeeding — there is nothing to read back, and a
    lookup that worked is not a change that happened. Without this argument a
    successful read would be assessed as an unconfirmed mutation, which is wrong in the
    direction of alarm rather than of false comfort, but wrong. It defaults to ``True``
    because a caller that does not know what it ran should be held to the stricter
    reading of the two.

    Never raises. A caller that cannot get an assessment has no safe fallback of its
    own; the fail-closed answer is ``UNKNOWN``, and it is produced here rather than
    invented at the call site.
    """
    name = _outcome_name(outcome)
    verdict = _verification_state(verification)
    notes: list[str] = []

    if name not in OUTCOME_FAMILY:
        return _assessment(
            EvidenceState.UNKNOWN,
            reason=(
                f"{name!r} is not an outcome this system produces, so nothing is known "
                f"about the request"
            ),
        )

    family = OUTCOME_FAMILY[name]

    if not is_write:
        # A read has no completion claim to make. ``RETRIEVED`` is the strongest state
        # a lookup can reach and it does not license "done", which is the correct
        # answer: nothing about the account changed, so there is nothing to have
        # finished. A degraded read is ``DEGRADED`` — real rows, not the whole answer.
        if name == "verified_success":
            return _assessment(
                EvidenceState.RETRIEVED,
                reason="a read answered from the account; no change was made or claimed",
            )
        if name == "accepted_unverified":
            return _assessment(
                EvidenceState.DEGRADED,
                reason="a read answered partially, so it is not the whole answer",
            )

    if name == "verified_success":
        if verdict == VERIFIED:
            return _assessment(
                EvidenceState.VERIFIED_SUCCESS,
                reason="the mutation ran and an independent read-back confirmed it",
            )
        # The dangerous pair. The outcome claims a completed change; the read-back does
        # not support it. The executed fact is real and is kept — something was sent,
        # and pretending otherwise would be its own falsehood — but the completion claim
        # is not licensed.
        contradiction_text = (
            f"outcome claims verified success while verification is "
            f"{verdict or 'absent'}"
        )
        state = EvidenceState.EXECUTED if verdict != "verification_failed" else EvidenceState.VERIFIED_FAILURE
        return _assessment(
            state,
            contradiction=contradiction_text,
            downgraded_from=EvidenceState.VERIFIED_SUCCESS,
            reason=(
                "the change was sent and has not been confirmed by an independent read, "
                "so it is reported as sent rather than as done"
                if state is EvidenceState.EXECUTED
                else "the read-back did not find the intended value"
            ),
            notes=notes,
        )

    if verdict == VERIFIED and family is not EvidenceState.EXECUTED:
        # Verification is a veto, never a promotion. A read-back that says "verified"
        # attached to a turn that denied permission or asked a question is describing
        # something other than a completed mutation, and must not manufacture one.
        notes.append(
            f"a 'verified' verification arrived with a {name!r} outcome and was not "
            f"treated as evidence of a completed change"
        )
        return _assessment(
            family,
            contradiction=f"verification claims verified while outcome is {name!r}",
            reason="verification cannot promote an outcome that executed nothing",
            notes=notes,
        )

    if family is EvidenceState.EXECUTED and verdict == "verification_failed":
        return _assessment(
            EvidenceState.VERIFIED_FAILURE,
            reason="the read-back did not find the intended value",
        )

    return _assessment(family, reason=f"derived from a {name!r} outcome")


def contradiction(outcome: Any, verification: Any = None, *, is_write: bool = True) -> str:
    """Name the disagreement between an outcome and its verification, or return ``''``.

    Separate from :func:`derive` so a caller can alarm on the disagreement without
    having to care what it resolved to. The two must not be computed independently at
    the call site, which is why this delegates rather than reimplementing the check.
    """
    return derive(outcome, verification, is_write=is_write).contradiction


def may_say_done(outcome: Any, verification: Any = None, *, is_write: bool = True) -> bool:
    """Whether this turn may tell the person their change is complete.

    The single question the module exists to answer. Kept as its own function so that
    the answer at a call site is a call to this rather than a comparison somebody wrote
    from memory.
    """
    return derive(outcome, verification, is_write=is_write).may_claim_done


def _assessment(
    state: EvidenceState,
    *,
    reason: str = "",
    contradiction: str = "",
    downgraded_from: EvidenceState | None = None,
    notes: list[str] | None = None,
) -> Assessment:
    return Assessment(
        state=state,
        may_claim_done=state_supports_completion_claim(state),
        requires_disclosure=state_requires_disclosure(state),
        contradiction=contradiction,
        downgraded_from=downgraded_from,
        reason=reason,
        notes=tuple(notes or ()),
    )


def _outcome_name(outcome: Any) -> str:
    if outcome is None:
        return ""
    if isinstance(outcome, str):
        return outcome.strip().lower()
    # Enum-like and dataclass-like carriers, in that order of likelihood.
    for attribute in ("value", "status", "outcome"):
        candidate = getattr(outcome, attribute, None)
        if isinstance(candidate, str):
            return candidate.strip().lower()
    return ""


def _verification_state(verification: Any) -> str:
    """The verification verdict as a plain string, or ``''`` for "no read-back".

    Deliberately does not consult ``is_verified``. A duck-typed object with a truthy
    ``is_verified`` and no state would otherwise license a completion claim, and the
    one thing this module must not accept is a claim of verification from something
    that cannot show its verdict.
    """
    if verification is None:
        return ""
    if isinstance(verification, str):
        return verification.strip().lower()
    state = getattr(verification, "state", None)
    if isinstance(state, str):
        return state.strip().lower()
    return ""
