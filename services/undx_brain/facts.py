"""What a remembered fact is still worth, and what to do when a new one disagrees.

:mod:`services.undx_brain.truth` decides how well something is known.
:mod:`services.undx_brain.evidence` decides what one turn may claim. Neither has any
concept of *time*, and a fact store without a concept of time has a specific failure:
the thing it hands back is a sentence somebody wrote down once, and it is returned with
exactly the same weight on the day it was recorded and a month later.

Two behaviours are missing, and they are the same missing behaviour seen from two
directions.

**A fact ages.** ``pulse_ai_truth_facts`` records ``valid_from``, and nothing reads it.
A claim confirmed against a running system fifteen minutes ago and the same claim
confirmed six weeks ago are stored identically and retrieved identically. :func:`read`
gives the second one a different citation than the first: past its horizon a fact may be
quoted only *as of* the moment it was observed, never as a description of how things are
now. That is not a hedge added for politeness. Citing a six-week-old reading as current
state is how a system tells somebody their alert is set to a value they changed in
between.

**Facts disagree across time, and the one mechanism that claims to notice does not.**
:func:`services.undx_architecture.record_fact` returns a ``contradictions`` list. It is
built by selecting active rows with the *same owner and the same claim text*, then
keeping the ones whose ``source`` differs. Run against the real schema:

.. code-block:: text

    record_fact("btc alert threshold is 50000", source="crypto.alerts.get")
        -> contradictions=[],           status='active'
    record_fact("btc alert threshold is 50000", source="user_statement")
        -> contradictions=['undx_fact_66b0…'], status='review'
    record_fact("btc alert threshold is 60000", source="crypto.alerts.get")
        -> contradictions=[],           status='active'

The second call is two independent sources saying the identical thing — corroboration —
and it is the one flagged for review. The third call says the threshold is a different
number, which is the disagreement, and it is filed as ``active`` beside the first with
nothing marking either. Both are now retrievable and neither knows about the other. The
only mechanism named "contradiction" in the fact store detects agreement and lets
disagreement through, because it compares claim *strings*, and two claims that disagree
are by construction different strings.

So this module compares a **subject** and a **value** rather than a sentence.
``("crypto.alerts.7.threshold", "50000")`` and ``("crypto.alerts.7.threshold", "60000")``
disagree; ``("…threshold", "50000")`` recorded twice from two sources agrees. A stored
row that declares no subject cannot be compared to anything, and :func:`compare` says so
plainly rather than falling back on string similarity — a similarity score would invent
disagreements between unrelated claims that happen to share words, and inventing a
contradiction is not obviously safer than missing one.

**Three decisions here are load-bearing.**

*Resolution is never silent.* The honest outcome of a disagreement is not "overwrite" and
not "keep both and say nothing". Every non-agreeing comparison sets
:attr:`Disagreement.must_disclose`, including the case where the new observation wins.
A superseding write that nobody is told about is indistinguishable, from the outside,
from a fact that was never contradicted.

*Newer wins only when it is at least as well known.* A user typing a number is a real
observation and it does not overturn a read-back from the account. :func:`truth.meets`
decides, so the ordering used here is the same ordering used everywhere else in the
Brain. Newer-but-weaker leaves the stored fact standing **and** reports the conflict;
that is :attr:`Resolution.KEEP_STORED`, not silence.

*Nothing stored is ever current.* :attr:`Reading.may_cite_as_current` is always ``False``
and is computed by calling :func:`truth.may_claim_live_state`, not by writing ``False``
here. A stored fact is a past observation by definition. The only thing that establishes
present account state is :attr:`~services.undx_brain.truth.EvidenceState.VERIFIED_SUCCESS`
reached through a fresh read-back, and no amount of freshness or trust promotes a
remembered claim into one.

**The horizon table runs the way round that looks backwards, deliberately.** A fact at
``RUNTIME_CANONICAL`` gets five minutes; a fact at ``SOURCE_MAPPED`` gets thirty days.
Trust is not being punished. The horizon answers "how long does this remain evidence of
the thing it observed", and that is a property of *what was observed*, not of how well.
The high-trust levels are reached by looking at a live system, and a live system is the
thing most likely to have moved since. The low-trust levels describe code and layout,
which move on deploys — and they arrive so heavily qualified by
:func:`truth.hedge_for` that a long horizon buys them nothing anyway. Trust level is the
only provenance signal a stored fact carries, so it is used as the proxy, and the
inversion is written down here so that the next reader does not "fix" it.

Nothing calls this module. It is behind ``UNDX_BRAIN_FACTS_ENABLED``, which defaults
off, and with the flag off every entry point returns ``ok=False`` and answers nothing —
a disabled evaluator must not be mistaken for one that found no problem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from . import config as brain_config
from .truth import (
    TrustLevel,
    hedge_for,
    may_claim_live_state,
    meets,
    rank,
)

__all__ = [
    "SUBJECT_KEY",
    "TRUST_KEY",
    "HORIZON_SECONDS",
    "Citability",
    "Resolution",
    "Observation",
    "Reading",
    "Disagreement",
    "Reconciliation",
    "from_row",
    "metadata_for",
    "horizon_for",
    "parse_moment",
    "read",
    "compare",
    "reconcile",
]

#: Where a fact's subject lives inside ``pulse_ai_truth_facts.metadata_json``. Declared
#: once here rather than spelled at each call site, because a subject written under a
#: misspelt key is a fact that silently becomes uncomparable — the exact failure this
#: module exists to end.
SUBJECT_KEY = "undx_subject"

#: Where a fact's :class:`~services.undx_brain.truth.TrustLevel` lives in the same blob.
#: The table has a ``confidence`` REAL column and it is deliberately **not** read as a
#: trust level. ``record_fact`` stores whatever float its caller passed, with no rule
#: about what any particular number means, so mapping it onto the eight-level ordering
#: would manufacture provenance out of an arbitrary decimal. A fact with no declared
#: trust ranks as ``BLOCKED`` — see :func:`truth.rank` — and is not citable at all.
TRUST_KEY = "undx_trust"

#: How long an observation at each trust level remains evidence of the present.
#:
#: Zero means "never citable as anything but a historical reading". ``BLOCKED`` should
#: not have reached a response at all; ``DEPRECATED`` describes behaviour that has
#: already been removed, so citing it as current is wrong on the day it is written.
HORIZON_SECONDS: dict[TrustLevel, int] = {
    TrustLevel.BLOCKED: 0,
    TrustLevel.DEPRECATED: 0,
    # Read out of source layout. Stable until somebody deploys.
    TrustLevel.SOURCE_DISCOVERED: 30 * 86_400,
    TrustLevel.SOURCE_MAPPED: 30 * 86_400,
    # Documentation lags code, so a shorter window than the source it describes.
    TrustLevel.DOCUMENTED: 14 * 86_400,
    TrustLevel.TESTED: 7 * 86_400,
    # Both of these were reached by looking at something that was running. What was
    # looked at is exactly what changes between one request and the next.
    TrustLevel.LIVE_VERIFIED: 900,
    TrustLevel.RUNTIME_CANONICAL: 300,
}


class Citability(str, Enum):
    """How a stored fact may appear in a response. Three states, not a scale."""

    #: No provenance, no usable timestamp, or a blocked source. The fact may be held and
    #: may not be quoted.
    NOT_CITABLE = "not_citable"
    #: Past its horizon, or carrying a timestamp that cannot be trusted to the minute.
    #: Quotable only with an explicit "as of <time>".
    AS_OF = "as_of"
    #: Within its horizon. Quotable as a recorded fact, still carrying the hedge its
    #: trust level obliges — and still not as a statement about the present.
    RECORDED = "recorded"


class Resolution(str, Enum):
    """What comparing a new observation against a stored one concluded."""

    #: The two say the same thing. Corroboration, which the existing ``record_fact``
    #: reports as a contradiction.
    AGREEMENT = "agreement"
    #: The new observation is strictly newer and at least as well known. It supersedes,
    #: and the supersession is disclosed.
    PREFER_NEW = "prefer_new"
    #: They disagree and the new observation is less well known. The stored fact stands
    #: and the disagreement is still reported.
    KEEP_STORED = "keep_stored"
    #: They disagree and nothing orders them — unusable timestamps, the same instant, a
    #: stronger observation of an *earlier* moment, or neither side with provenance.
    #: Neither may be cited as settled.
    UNRESOLVED = "unresolved"
    #: One side declares no subject, or the subjects differ. Not a disagreement; a
    #: comparison that could not be made.
    UNCOMPARABLE = "uncomparable"
    #: :func:`reconcile` found no comparable stored fact about this subject.
    NOTHING_TO_COMPARE = "nothing_to_compare"


#: How cautious each resolution is, for combining several comparisons into one answer.
#: The most cautious present wins: one unresolved conflict is not cancelled out by three
#: agreements elsewhere.
_CAUTION: dict[Resolution, int] = {
    Resolution.NOTHING_TO_COMPARE: 0,
    Resolution.UNCOMPARABLE: 0,
    Resolution.AGREEMENT: 0,
    Resolution.PREFER_NEW: 1,
    Resolution.KEEP_STORED: 2,
    Resolution.UNRESOLVED: 3,
}


@dataclass(frozen=True)
class Observation:
    """One claim about one subject, with where it came from and when.

    Deliberately not a database row. This module decides; the caller persists. Keeping
    it pure is why it can be exercised without a schema, and why there is no second fact
    store hiding behind it.
    """

    subject: str = ""
    value: str = ""
    source: str = ""
    #: A :class:`~services.undx_brain.truth.TrustLevel` or its string spelling. Anything
    #: unrecognised ranks as ``BLOCKED``, which is the fail-closed reading.
    trust: str = ""
    #: ISO-8601. ``valid_from`` for a row from ``pulse_ai_truth_facts``.
    observed_at: str = ""
    fact_id: str = ""

    @property
    def comparable(self) -> bool:
        """Whether this can be lined up against another observation at all."""
        return bool(_norm(self.subject))


@dataclass(frozen=True)
class Reading:
    """What one stored fact is still worth, and the words a response must carry with it."""

    ok: bool = False
    citability: Citability = Citability.NOT_CITABLE
    trust: str = ""
    #: Seconds since the observation. ``None`` when the timestamp could not be read,
    #: which is not the same as zero and must not be rendered as "just now".
    age_seconds: float | None = None
    horizon_seconds: int = 0
    stale: bool = True
    #: Always ``False``. Held as a field so a caller cannot reach the opposite
    #: conclusion by computing it differently.
    may_cite_as_current: bool = False
    #: ``"as of <time>"`` when one is required, else empty.
    qualifier: str = ""
    hedge: str = ""
    #: The qualifier and the hedge as one phrase, so the response layer does not have to
    #: assemble it and occasionally assemble it weaker.
    citation: str = ""
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        """True only when the fact may be stated without a time qualifier."""
        return self.citability is Citability.RECORDED


@dataclass(frozen=True)
class Disagreement:
    """One new observation lined up against one stored fact."""

    ok: bool = False
    resolution: Resolution = Resolution.UNCOMPARABLE
    subject: str = ""
    stored_value: str = ""
    new_value: str = ""
    stored_trust: str = ""
    new_trust: str = ""
    stored_at: str = ""
    observed_at: str = ""
    stored_fact_id: str = ""
    #: True for every outcome that is not agreement. A resolved conflict is still a
    #: conflict, and the resolution is the part that has to be visible.
    must_disclose: bool = False
    #: The ``fact_id`` this observation supersedes, or empty. Set only for
    #: :attr:`Resolution.PREFER_NEW`.
    supersedes: str = ""
    #: A sentence naming both readings. Never one of them.
    disclosure: str = ""
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        """True when there is something here a person has to be told."""
        return self.must_disclose


@dataclass(frozen=True)
class Reconciliation:
    """One new observation against everything already stored about the same subject."""

    ok: bool = False
    subject: str = ""
    resolution: Resolution = Resolution.NOTHING_TO_COMPARE
    disagreements: tuple[Disagreement, ...] = ()
    #: ``fact_id`` values that declared no subject and so could not be lined up. Listed
    #: rather than counted as conflicts: every row written before this module existed is
    #: in this state, and treating them as conflicts would make the field meaningless.
    uncomparable: tuple[str, ...] = ()
    must_disclose: bool = False
    supersedes: tuple[str, ...] = ()
    disclosure: str = ""
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.must_disclose


def from_row(row: Any) -> Observation:
    """Adapt one ``pulse_ai_truth_facts`` row into an :class:`Observation`.

    Subject and trust are read from ``metadata_json``. A row written by the existing
    :func:`services.undx_architecture.record_fact` without them yields an observation
    with an empty subject and no trust, which is uncomparable and not citable — the
    accurate description of a claim whose provenance was never recorded, and the reason
    ``record_fact`` grew an optional ``metadata`` argument rather than this module
    guessing.

    Never raises. A malformed ``metadata_json`` yields the same empty result as an
    absent one.
    """
    try:
        data = dict(row)
    except (TypeError, ValueError):
        data = {}
    metadata = _metadata(data.get("metadata_json"))
    return Observation(
        subject=str(metadata.get(SUBJECT_KEY, "") or ""),
        value=str(data.get("claim", "") or ""),
        source=str(data.get("source", "") or ""),
        trust=str(metadata.get(TRUST_KEY, "") or ""),
        observed_at=str(data.get("valid_from") or data.get("created_at") or ""),
        fact_id=str(data.get("fact_id", "") or ""),
    )


def metadata_for(observation: Observation) -> dict[str, str]:
    """The metadata a writer must store for this observation to be comparable later.

    Returned as the pair of declared keys and nothing else, so a caller merging it into
    an existing blob cannot overwrite fields it did not mean to.
    """
    return {
        SUBJECT_KEY: _norm(observation.subject),
        TRUST_KEY: str(getattr(observation.trust, "value", observation.trust) or ""),
    }


def horizon_for(trust: Any) -> int:
    """Seconds an observation at this trust level stays evidence of the present.

    An unrecognised level ranks as ``BLOCKED`` and gets zero, so a fact whose provenance
    was lost in transit is stale immediately rather than inheriting the most generous
    row in the table.
    """
    try:
        return HORIZON_SECONDS.get(TrustLevel(trust), 0)
    except ValueError:
        return 0


def read(observation: Observation, *, now: Any = None,
         env: Mapping[str, str] | None = None) -> Reading:
    """How this stored fact may be cited, right now.

    Never raises. A caller that cannot get a reading has no safe fallback of its own,
    and the fail-closed answer — hold it, do not quote it — is produced here rather than
    improvised at the call site.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Reading(
            ok=False,
            reason="fact ageing is disabled; stored facts are read the way they are today",
            notes=notes,
        )

    trust_text = str(getattr(observation.trust, "value", observation.trust) or "")
    hedge = hedge_for(trust_text)
    horizon = horizon_for(trust_text)
    moment, naive = _parse_time(observation.observed_at)
    current, _ = _parse_time(now) if now is not None else (_utcnow(), False)
    if current is None:
        current = _utcnow()

    if rank(trust_text) == 0:
        return Reading(
            ok=True,
            citability=Citability.NOT_CITABLE,
            trust=trust_text,
            horizon_seconds=0,
            may_cite_as_current=may_claim_live_state(trust_text),
            hedge=hedge,
            citation=hedge,
            reason=(
                "the fact declares no usable trust level, so there is no basis for "
                "quoting it" if not trust_text else
                f"a fact at {trust_text!r} may be held and may not be quoted"
            ),
            notes=notes,
        )

    if moment is None:
        return Reading(
            ok=True,
            citability=Citability.NOT_CITABLE,
            trust=trust_text,
            horizon_seconds=horizon,
            may_cite_as_current=may_claim_live_state(trust_text),
            hedge=hedge,
            citation=hedge,
            reason=(
                "the fact carries no readable timestamp, so its age is unknown and an "
                "unknown age cannot be reported as recent"
            ),
            notes=notes,
        )

    age = (current - moment).total_seconds()
    stamp = moment.isoformat(timespec="seconds")
    qualifier = f"as of {stamp}"
    extra: tuple[str, ...] = ()

    if age < 0:
        # A fact recorded in the future is a clock disagreement between whatever wrote
        # it and whatever is reading it. Treating it as brand new is the one reading
        # that turns a clock problem into a false statement about the present.
        return Reading(
            ok=True,
            citability=Citability.AS_OF,
            trust=trust_text,
            age_seconds=age,
            horizon_seconds=horizon,
            stale=True,
            may_cite_as_current=may_claim_live_state(trust_text),
            qualifier=qualifier,
            hedge=hedge,
            citation=f"{qualifier}; {hedge}",
            reason="the recorded time is in the future, so the age cannot be trusted",
            notes=notes + (f"observed_at {stamp} is later than the current time",),
        )

    if naive:
        # A timestamp with no offset is ambiguous by hours. Against a five-minute
        # horizon that ambiguity is the whole horizon, so the fact is quotable only with
        # its time attached, whatever its age works out to.
        extra = (
            "the recorded time carried no UTC offset and was read as UTC; the fact is "
            "time-qualified regardless of its computed age",
        )
        return Reading(
            ok=True,
            citability=Citability.AS_OF,
            trust=trust_text,
            age_seconds=age,
            horizon_seconds=horizon,
            stale=True,
            may_cite_as_current=may_claim_live_state(trust_text),
            qualifier=qualifier,
            hedge=hedge,
            citation=f"{qualifier}; {hedge}",
            reason="the recorded time is ambiguous without an offset",
            notes=notes + extra,
        )

    if age > horizon:
        return Reading(
            ok=True,
            citability=Citability.AS_OF,
            trust=trust_text,
            age_seconds=age,
            horizon_seconds=horizon,
            stale=True,
            may_cite_as_current=may_claim_live_state(trust_text),
            qualifier=qualifier,
            hedge=hedge,
            citation=f"{qualifier}; {hedge}",
            reason=(
                f"the observation is {int(age)}s old against a {horizon}s horizon for "
                f"{trust_text!r}, so it describes what was true then"
            ),
            notes=notes,
        )

    return Reading(
        ok=True,
        citability=Citability.RECORDED,
        trust=trust_text,
        age_seconds=age,
        horizon_seconds=horizon,
        stale=False,
        may_cite_as_current=may_claim_live_state(trust_text),
        qualifier="",
        hedge=hedge,
        citation=hedge,
        reason=(
            f"the observation is {int(age)}s old, within the {horizon}s horizon for "
            f"{trust_text!r}"
        ),
        notes=notes,
    )


def compare(stored: Observation, new: Observation, *,
            env: Mapping[str, str] | None = None) -> Disagreement:
    """Line up a new observation against one already stored.

    The comparison is on subject and value, never on claim text. Two rows whose claim
    strings differ may be saying the same thing about the same subject, and two rows
    whose claim strings match are saying the same thing twice — which is agreement, not
    the contradiction the existing fact store reports it as.

    Never raises.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Disagreement(
            ok=False,
            reason="cross-time fact comparison is disabled",
            notes=notes,
        )

    stored_subject, new_subject = _norm(stored.subject), _norm(new.subject)
    base = dict(
        ok=True,
        subject=new_subject or stored_subject,
        stored_value=stored.value,
        new_value=new.value,
        stored_trust=_trust_text(stored.trust),
        new_trust=_trust_text(new.trust),
        stored_at=stored.observed_at,
        observed_at=new.observed_at,
        stored_fact_id=stored.fact_id,
        notes=notes,
    )

    if not stored_subject or not new_subject:
        missing = "the stored fact" if not stored_subject else "the new observation"
        return Disagreement(
            **base,
            resolution=Resolution.UNCOMPARABLE,
            reason=(
                f"{missing} declares no subject, so there is nothing to line the two up "
                f"by; comparing the claim text instead would invent agreements and "
                f"disagreements out of shared words"
            ),
        )
    if stored_subject != new_subject:
        return Disagreement(
            **base,
            resolution=Resolution.UNCOMPARABLE,
            reason=(
                f"the two are about different subjects ({stored_subject!r} and "
                f"{new_subject!r}), which is not a disagreement"
            ),
        )

    if _norm(stored.value) == _norm(new.value):
        return Disagreement(
            **base,
            resolution=Resolution.AGREEMENT,
            reason=(
                "both observations report the same value for this subject, which is "
                "corroboration"
            ),
        )

    disclosure = (
        f"{new_subject} was recorded as {stored.value!r} "
        f"({_trust_text(stored.trust) or 'unknown provenance'}"
        f"{', ' + stored.observed_at if stored.observed_at else ''}) and observed as "
        f"{new.value!r} ({_trust_text(new.trust) or 'unknown provenance'}"
        f"{', ' + new.observed_at if new.observed_at else ''})"
    )
    base["disclosure"] = disclosure

    stored_rank, new_rank = rank(stored.trust), rank(new.trust)
    if stored_rank == 0 and new_rank == 0:
        return Disagreement(
            **base,
            resolution=Resolution.UNRESOLVED,
            must_disclose=True,
            reason=(
                "the two disagree and neither declares a usable trust level, so nothing "
                "orders them"
            ),
        )

    if not meets(new.trust, stored.trust):
        return Disagreement(
            **base,
            resolution=Resolution.KEEP_STORED,
            must_disclose=True,
            reason=(
                f"the new observation is less well known "
                f"({_trust_text(new.trust) or 'no trust level'} below "
                f"{_trust_text(stored.trust)}), so it does not overturn the stored fact "
                f"— but the disagreement stands and is reported"
            ),
        )

    stored_moment, _ = _parse_time(stored.observed_at)
    new_moment, _ = _parse_time(new.observed_at)
    if stored_moment is None or new_moment is None:
        return Disagreement(
            **base,
            resolution=Resolution.UNRESOLVED,
            must_disclose=True,
            reason=(
                "the two disagree and at least one carries no readable timestamp, so "
                "which one is later cannot be established"
            ),
        )
    if new_moment <= stored_moment:
        return Disagreement(
            **base,
            resolution=Resolution.UNRESOLVED,
            must_disclose=True,
            reason=(
                "the new observation is at least as well known but is not newer, so it "
                "describes an earlier or identical moment and settles nothing about now"
            ),
        )

    if not stored.fact_id:
        # The stored fact wins the ordering but cannot be named, so the caller has
        # nothing to mark superseded. Saying so is the difference between "supersedes
        # nothing" and "supersedes a row I cannot identify", and only one of those is
        # safe to act on.
        base["notes"] = base["notes"] + (
            "the superseded fact carries no fact_id, so the supersession can be "
            "reported but not applied to a row",
        )
    return Disagreement(
        **base,
        resolution=Resolution.PREFER_NEW,
        must_disclose=True,
        supersedes=stored.fact_id,
        reason=(
            "the new observation is strictly newer and at least as well known, so it "
            "supersedes the stored fact; the supersession is disclosed rather than "
            "applied quietly"
        ),
    )


def reconcile(stored: Iterable[Observation], new: Observation, *,
              env: Mapping[str, str] | None = None) -> Reconciliation:
    """Line up a new observation against everything already stored about its subject.

    The overall resolution is the most cautious of the individual ones. One unresolved
    conflict is not averaged away by agreements elsewhere, because the caller acts on
    the overall answer and the thing it must not do is act as though the conflict were
    absent.

    Never raises.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Reconciliation(
            ok=False,
            reason="cross-time fact comparison is disabled",
            notes=notes,
        )

    subject = _norm(new.subject)
    comparisons: list[Disagreement] = []
    uncomparable: list[str] = []

    for item in stored or ():
        outcome = compare(item, new, env=env)
        if outcome.resolution is Resolution.UNCOMPARABLE:
            uncomparable.append(item.fact_id or "<no fact_id>")
            continue
        comparisons.append(outcome)

    if not comparisons:
        return Reconciliation(
            ok=True,
            subject=subject,
            resolution=Resolution.NOTHING_TO_COMPARE,
            uncomparable=tuple(uncomparable),
            reason=(
                f"no stored fact about {subject!r} could be compared"
                + (
                    f"; {len(uncomparable)} stored row(s) declare no subject"
                    if uncomparable else ""
                )
            ),
            notes=notes,
        )

    overall = max(comparisons, key=lambda item: _CAUTION[item.resolution]).resolution
    disclose = [item for item in comparisons if item.must_disclose]
    return Reconciliation(
        ok=True,
        subject=subject,
        resolution=overall,
        disagreements=tuple(comparisons),
        uncomparable=tuple(uncomparable),
        must_disclose=bool(disclose),
        supersedes=tuple(
            item.supersedes for item in comparisons
            if item.resolution is Resolution.PREFER_NEW and item.supersedes
        ),
        disclosure="; ".join(item.disclosure for item in disclose if item.disclosure),
        reason=(
            f"{len(comparisons)} stored fact(s) compared, {len(disclose)} disagreeing"
            + (f", {len(uncomparable)} uncomparable" if uncomparable else "")
        ),
        notes=notes,
    )


def _enabled(env: Mapping[str, str] | None) -> tuple[bool, tuple[str, ...]]:
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    on = bool(values.get("UNDX_BRAIN_ENABLED", False)) and bool(
        values.get("UNDX_BRAIN_FACTS_ENABLED", False)
    )
    return on, tuple(resolution.notes)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _trust_text(trust: Any) -> str:
    return str(getattr(trust, "value", trust) or "")


def _metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(str(raw or "") or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_moment(value: Any) -> tuple[datetime | None, bool]:
    """An aware UTC datetime and whether the input lacked an offset.

    The naive flag is returned rather than swallowed because a timestamp with no offset
    is ambiguous by hours, and the caller needs to treat it differently from one that is
    merely old.

    Public because the rest of the package has to read the same timestamps this module
    ages, and a second parser is how two modules end up disagreeing about when something
    happened. There is one here; other modules call it rather than writing another.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc), True
        return value.astimezone(timezone.utc), False
    text = str(value or "").strip()
    if not text:
        return None, False
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc), True
    return parsed.astimezone(timezone.utc), False


#: The spelling this module's own internals were written against, kept so promoting the
#: function to the public surface changed no call site inside this file.
_parse_time = parse_moment
