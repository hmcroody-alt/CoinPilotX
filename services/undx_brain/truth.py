"""Two axes that get confused with each other, kept apart on purpose.

The first axis is **trust**: how well do we know this *about PulseSoc*? A route found by
a regex over source is known less well than one covered by a passing test, which is
known less well than one a live request just returned. That is
:class:`TrustLevel`, and it orders.

The second axis is **evidence**: what happened *in this request, for this account*? A
proposal is not an execution, an execution is not a verified outcome, and an accepted
service request is not either of them. That is :class:`EvidenceState`, and it does not
order — it is a state machine with legal transitions.

Keeping them separate is the whole point of the module, because collapsing them is the
most expensive mistake this system can make and it is an easy one. The corpus knows that
``POST /api/alerts/<id>/pause`` exists. It has 1,516 routes and it is right about them.
Nothing in that knowledge — at any trust level, including ``runtime_canonical`` — says
anything whatsoever about whether *this user's* alert is currently paused. A model that
has both facts in context and is asked "is my BTC alert paused?" will happily answer from
the wrong one, because the wrong one is right there and reads like an answer.

So the rule is expressed as code rather than as guidance:
:func:`may_claim_live_state` returns ``False`` for every trust level that exists, and
``True`` only for :attr:`EvidenceState.VERIFIED_SUCCESS`. There is no trust level high
enough. ``runtime_canonical`` means "this is how PulseSoc works, confirmed against the
running system" — it does not mean "and here is what your account looks like".

The inverse is also enforced, and matters less but costs nothing:
:func:`may_explain_product` is true for source-derived knowledge and false for evidence
states, because a verified read of one alert is not a description of how alerts work.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class TrustLevel(str, Enum):
    """How well a piece of *product* knowledge is known. Ordered by :func:`rank`.

    The two ends are not "bad" and "good" — they are "cheap to obtain and cheap to be
    wrong about" and "expensive to obtain and expensive to be wrong about". A corpus of
    1,682 ``source_discovered`` records is genuinely useful for orientation and
    genuinely unsuitable for assertion, and both halves of that sentence are why the
    level is recorded rather than discarded.
    """

    #: Explicitly denied. Never retrievable, never rankable upward. Used for records a
    #: safety classifier rejected, and for paths an operator has named as off-limits.
    BLOCKED = "blocked"
    #: Superseded. Retrievable only when a caller asks for history on purpose, because
    #: describing a removed feature as present is a specific and common way to be wrong.
    DEPRECATED = "deprecated"
    #: Found in source. A path, a size, a hash, a summary. Enough to know something
    #: exists; not enough to know it is reachable, enabled, authorised, or current.
    SOURCE_DISCOVERED = "source_discovered"
    #: Found in source *and* attached to a canonical PulseSoc domain, so the record can
    #: be placed in the product rather than only in the filesystem.
    SOURCE_MAPPED = "source_mapped"
    #: Described in prose written for humans — a report, a README, a migration note.
    DOCUMENTED = "documented"
    #: Covered by a test that passes in this tree.
    TESTED = "tested"
    #: Observed answering correctly from a running system during a live validation.
    LIVE_VERIFIED = "live_verified"
    #: Read back from the canonical runtime registry rather than from any artifact —
    #: the capability registry, the policy ledger, the knowledge map.
    RUNTIME_CANONICAL = "runtime_canonical"


_ORDER: tuple[TrustLevel, ...] = (
    TrustLevel.BLOCKED,
    TrustLevel.DEPRECATED,
    TrustLevel.SOURCE_DISCOVERED,
    TrustLevel.SOURCE_MAPPED,
    TrustLevel.DOCUMENTED,
    TrustLevel.TESTED,
    TrustLevel.LIVE_VERIFIED,
    TrustLevel.RUNTIME_CANONICAL,
)

_RANK: dict[TrustLevel, int] = {level: index for index, level in enumerate(_ORDER)}


def rank(level: TrustLevel | str) -> int:
    """Position in the ordering. Unknown strings rank as ``BLOCKED`` rather than raising.

    Unknown-ranks-lowest is the fail-closed reading. A trust level invented by a later
    corpus generator, or corrupted in transit, must not become retrievable by being
    unrecognised — which is what would happen if an unknown value sorted high or if the
    comparison threw and the caller caught broadly.
    """
    try:
        return _RANK[TrustLevel(level)]
    except (ValueError, KeyError):
        return 0


def meets(level: TrustLevel | str, minimum: TrustLevel | str) -> bool:
    """Whether ``level`` is at least ``minimum``. ``BLOCKED`` never meets anything."""
    if rank(level) == 0:
        return False
    return rank(level) >= rank(minimum)


def may_explain_product(level: TrustLevel | str) -> bool:
    """Whether knowledge at this level may be used to describe how PulseSoc works.

    Everything above ``BLOCKED`` may, including ``SOURCE_DISCOVERED`` — with the
    provenance attached, so the explanation can say where it came from. What varies by
    level is not permission but the hedge the response is obliged to carry, which is
    :func:`hedge_for`.
    """
    return rank(level) > 0


def may_claim_live_state(level: TrustLevel | str) -> bool:
    """Always ``False``. Product knowledge never establishes account state.

    Written as a function rather than omitted so that the claim is testable and so that
    a future author looking for "the place where high-trust knowledge becomes assertable
    about a user" finds this docstring instead of adding one.

    The only thing that establishes account state is
    :attr:`EvidenceState.VERIFIED_SUCCESS`, reached through the governed gateway and an
    independent read-back. See :func:`state_supports_completion_claim`.
    """
    return False


_HEDGE: dict[TrustLevel, str] = {
    # A blocked record should never reach a response at all, so the only way this entry
    # is read is a bug upstream. That is precisely why it must not be empty: an empty
    # hedge means the leaked claim goes out unqualified, turning a retrieval defect into
    # a confident false statement.
    TrustLevel.BLOCKED: "this source is blocked and must not be relied on",
    TrustLevel.DEPRECATED: "this describes behaviour that has since been removed or replaced",
    TrustLevel.SOURCE_DISCOVERED: "this comes from reading the source and has not been checked against a running system",
    TrustLevel.SOURCE_MAPPED: "this comes from the source layout rather than from a live check",
    TrustLevel.DOCUMENTED: "this comes from project documentation, which can lag the code",
    TrustLevel.TESTED: "this is covered by tests",
    TrustLevel.LIVE_VERIFIED: "this was confirmed against a running system",
    TrustLevel.RUNTIME_CANONICAL: "this is read from the live registry",
}


def hedge_for(level: TrustLevel | str) -> str:
    """The qualification a response must carry when leaning on knowledge at this level.

    Returned as a phrase rather than a boolean because the response layer needs to say
    it, and because a caller that has to invent the wording will eventually invent
    wording that overstates.
    """
    unknown = "the origin of this information could not be established"
    try:
        return _HEDGE.get(TrustLevel(level)) or unknown
    except ValueError:
        return unknown


class EvidenceState(str, Enum):
    """What is known about *this* request, for *this* account, right now.

    Deliberately not ordered. ``DEGRADED`` is not "worse than ``EXECUTED``" — it is a
    different thing entirely, and a comparison operator would invite code that treats it
    as a near-miss for success. Movement between states is governed by
    :data:`TRANSITIONS`.
    """

    #: Read out of the source corpus. About the product, never about the account.
    UNVERIFIED_SOURCE = "unverified_source"
    #: Read out of human-written documentation.
    DOCUMENTED = "documented"
    #: Fetched from a canonical live service, owner-scoped. True when it was read.
    RETRIEVED = "retrieved"
    #: UNDX has decided what it would do. Nothing has been sent anywhere.
    PROPOSED = "proposed"
    #: A confirmation has been minted and shown. The user has not yet answered.
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    #: The governed gateway ran the mutation. The service accepted it. Whether it took
    #: effect is not yet known — this is the state that gets mistaken for success.
    EXECUTED = "executed"
    #: Executed *and* independently read back as the intended value. The only state that
    #: licenses "done".
    VERIFIED_SUCCESS = "verified_success"
    #: Executed and read back as *not* the intended value, or the read-back failed. The
    #: account may or may not have changed; that ambiguity is the finding, not a bug.
    VERIFIED_FAILURE = "verified_failure"
    #: A source answered, partially or from a fallback. Usable with disclosure.
    DEGRADED = "degraded"
    #: No basis for any claim. The honest terminal state, and a legitimate answer.
    UNKNOWN = "unknown"


#: Legal moves. Absent pairs are refused by :func:`transition`.
#:
#: Two properties are worth naming because they are the ones that would otherwise be
#: quietly relaxed:
#:
#: * ``EXECUTED`` cannot reach ``VERIFIED_SUCCESS`` except through verification, and
#:   nothing reaches ``VERIFIED_SUCCESS`` from anywhere else at all. There is no path
#:   from ``RETRIEVED``, none from ``PROPOSED``, and none from ``DEGRADED``.
#: * ``VERIFIED_SUCCESS`` and ``VERIFIED_FAILURE`` are terminal. A verified outcome is
#:   not revisable within one request; a later request forms its own evidence.
TRANSITIONS: dict[EvidenceState, frozenset[EvidenceState]] = {
    EvidenceState.UNVERIFIED_SOURCE: frozenset({
        EvidenceState.DOCUMENTED, EvidenceState.RETRIEVED,
        EvidenceState.PROPOSED, EvidenceState.UNKNOWN,
    }),
    EvidenceState.DOCUMENTED: frozenset({
        EvidenceState.RETRIEVED, EvidenceState.PROPOSED, EvidenceState.UNKNOWN,
    }),
    EvidenceState.RETRIEVED: frozenset({
        EvidenceState.PROPOSED, EvidenceState.DEGRADED, EvidenceState.UNKNOWN,
    }),
    EvidenceState.PROPOSED: frozenset({
        EvidenceState.AWAITING_CONFIRMATION, EvidenceState.EXECUTED,
        EvidenceState.UNKNOWN, EvidenceState.DEGRADED,
    }),
    EvidenceState.AWAITING_CONFIRMATION: frozenset({
        EvidenceState.EXECUTED, EvidenceState.UNKNOWN,
    }),
    EvidenceState.EXECUTED: frozenset({
        EvidenceState.VERIFIED_SUCCESS, EvidenceState.VERIFIED_FAILURE,
        EvidenceState.DEGRADED, EvidenceState.UNKNOWN,
    }),
    EvidenceState.VERIFIED_SUCCESS: frozenset(),
    EvidenceState.VERIFIED_FAILURE: frozenset(),
    EvidenceState.DEGRADED: frozenset({
        EvidenceState.RETRIEVED, EvidenceState.UNKNOWN,
    }),
    EvidenceState.UNKNOWN: frozenset({
        EvidenceState.RETRIEVED, EvidenceState.PROPOSED,
    }),
}


class EvidenceTransitionError(RuntimeError):
    """An illegal evidence-state move was attempted.

    Raised rather than returned. A caller that wanted ``VERIFIED_SUCCESS`` and did not
    earn it must not be able to proceed by ignoring a falsy return value, which is the
    failure mode a boolean would permit.
    """


def transition(current: EvidenceState | str, target: EvidenceState | str) -> EvidenceState:
    """Move between evidence states, or refuse.

    Raises :class:`EvidenceTransitionError` for anything not in :data:`TRANSITIONS`,
    including unrecognised state names.
    """
    try:
        here, there = EvidenceState(current), EvidenceState(target)
    except ValueError as exc:
        raise EvidenceTransitionError(f"unrecognised evidence state: {exc}") from exc
    if there not in TRANSITIONS.get(here, frozenset()):
        raise EvidenceTransitionError(
            f"{here.value} -> {there.value} is not a legal evidence transition"
        )
    return there


def state_supports_completion_claim(state: EvidenceState | str) -> bool:
    """Whether UNDX may say an action is done.

    Exactly one state qualifies. ``EXECUTED`` does not, and that is the entire reason
    this function is not simply ``state in {EXECUTED, VERIFIED_SUCCESS}``: the service
    accepting a mutation is a statement about the request, and "your alert is paused" is
    a statement about the alert.
    """
    try:
        return EvidenceState(state) is EvidenceState.VERIFIED_SUCCESS
    except ValueError:
        return False


def state_requires_disclosure(state: EvidenceState | str) -> bool:
    """Whether a response resting on this state must say something about its own limits."""
    try:
        return EvidenceState(state) in {
            EvidenceState.UNVERIFIED_SOURCE, EvidenceState.DOCUMENTED,
            EvidenceState.DEGRADED, EvidenceState.UNKNOWN,
            EvidenceState.EXECUTED, EvidenceState.VERIFIED_FAILURE,
        }
    except ValueError:
        return True


def weakest(states: Iterable[EvidenceState | str]) -> EvidenceState:
    """The state a response built from all of ``states`` must be described by.

    A response is only as good as its weakest supporting evidence, so this is a floor
    rather than an average. An empty iterable is ``UNKNOWN``, not an error: a response
    resting on no evidence is precisely the thing ``UNKNOWN`` names.
    """
    floor = [
        EvidenceState.UNKNOWN, EvidenceState.VERIFIED_FAILURE, EvidenceState.DEGRADED,
        EvidenceState.UNVERIFIED_SOURCE, EvidenceState.DOCUMENTED,
        EvidenceState.AWAITING_CONFIRMATION, EvidenceState.PROPOSED,
        EvidenceState.EXECUTED, EvidenceState.RETRIEVED, EvidenceState.VERIFIED_SUCCESS,
    ]
    seen: list[int] = []
    for state in states:
        try:
            seen.append(floor.index(EvidenceState(state)))
        except ValueError:
            return EvidenceState.UNKNOWN
    return floor[min(seen)] if seen else EvidenceState.UNKNOWN
