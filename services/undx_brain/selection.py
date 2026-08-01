"""Choosing between operations, rather than scoring one and discarding the rest.

:func:`services.undx_agent_runtime.match_capability` walks every registered phrasing,
keeps a running best, and returns it. Everything it rejected is gone by the time the
function returns, and that loss is not visible at the call site: a runner-up one point
behind reads exactly like no runner-up at all. So nothing downstream can prefer the
reversible operation over the irreversible one, or the narrower over the wider, because
nothing downstream ever holds two candidates at the same moment.

The cost of that is measurable on the live eighty-capability registry rather than
hypothetical. "stop my alerts" scores ``crypto.alerts.pause`` at 10 and
``crypto.alerts.list`` at 9. One point — the difference between pausing somebody's
alerts and showing them — and the caller is handed the pause with no indication that
anything else was close. "show my alerts" is the same shape between two reads, at 11
and 10.

What this module adds is small and deliberately so.

**The whole ranking survives.** :func:`rank` runs the matcher's own scoring and returns
every capability that scored, in order, with each one's distance from the leader. It is
not a second matcher and must never become one: the scoring functions are imported from
:mod:`services.undx_agent_runtime` rather than reimplemented, and a test holds
``rank(text)[0]`` against ``match_capability(text)`` across every phrasing the registry
declares. A ranking that disagrees with the thing that actually runs would be worse than
no ranking.

**A near-tie is named.** Candidates within :data:`NEAR_TIE` of the leader are *contested*
rather than beaten. The threshold is one point, and one is not a taste: at one, every
sentence in the module's corpus that plainly asks for a write still gets that write, and
both genuinely ambiguous sentences are caught. At two, "update the threshold on my btc
alert" — which is not ambiguous by any reading — stops being an update. The test carries
the corpus and the sweep, so raising the number later means watching which sentences it
costs.

**A contested band is separated on declared data, or not at all.** Four rules, in order,
each reading only what the registry declares and what :mod:`~services.undx_brain.prediction`
derives from it. A read beats a write. A reversible write beats one that cannot be taken
back. A narrower blast radius beats a wider one. A cheap undo beats an expensive one.

**Two contested writes that no rule separates are returned undecided.** Not the
best-scoring one with a note attached — undecided, with both named. This is the same
shape as :func:`services.undx_brain.goals.understand` returning an unsettled goal, and
for the same reason: a selector that cannot abstain is not selecting, it is ranking and
then obeying the ranking. Guessing between two writes at one point of separation is the
failure this module exists to prevent.

Three things it deliberately does not do.

It does not execute, and it does not authorise. A named candidate is not permission to
run it; ``policy.evaluate`` and the gateway are unchanged and remain the only path.

It does not read account state. Which of ``crypto.alerts.pause`` and ``crypto.alerts.list``
a person meant may well be settled by whether they have one alert or thirty, and this
module cannot see that. It says the two are contested and lets a layer that can read
decide — which is why deferring to the read is a useful answer rather than a cop-out: the
read *is* the thing that would settle it.

It does not rescue a bad match. If the matcher scores nothing, this scores nothing. The
gap between "no capability was named" and "a capability was named badly" is not one that
more ranking closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from . import config as brain_config
from . import prediction as _prediction
from .prediction import Reversal


__all__ = [
    "Separator",
    "Candidate",
    "Selection",
    "NEAR_TIE",
    "MAX_CANDIDATES",
    "rank",
    "select",
]


#: How far behind the leader a candidate may score and still be treated as contesting
#: rather than beaten.
#:
#: One point, and the number is measured. Over a corpus of sentences that plainly ask
#: for a write — "silence my alerts", "edit my alert", "update the threshold on my btc
#: alert", "delete my bitcoin alert" and the rest, all carried in the test — a band of
#: one defers none of them and still catches both sentences that are genuinely poised
#: between doing something and looking at something. A band of two immediately costs
#: "update the threshold on my btc alert", which nobody would call ambiguous; three
#: costs "edit my alert"; four costs "pause my bitcoin alert". The cost curve is steep
#: on one side of one and flat on the other, which is what makes it the knee rather
#: than a value chosen because it looked conservative.
#:
#: It is also consistent with how the registry's own vocabulary scores. Of the 254
#: declared intent phrasings, exactly one whose best match is a write has any runner-up
#: at all, and it leads by 15. Designed write phrasings are not close to anything. A
#: near-tie involving a write is therefore never something the registry authored — it
#: only ever arises from a sentence a person typed, which is precisely the case the
#: matcher alone cannot report.
NEAR_TIE = 1

#: How many ranked candidates are returned. Beyond a handful the tail is capabilities
#: that share a single common word, and carrying it invites a caller to treat the list
#: as a menu. The contested band is a property of the top of the list, so truncating
#: the bottom cannot change any decision this module makes — a test pins that.
MAX_CANDIDATES = 5


class Separator(str, Enum):
    """Why the winner won — or the fact that nothing made it one."""

    #: Only one candidate scored, or the leader was clear of the near-tie band. The
    #: matcher's answer stands untouched, which is the common case and should be.
    UNCONTESTED = "uncontested"
    #: A write and a read were contested, and the read was taken. The write is named in
    #: :attr:`Selection.displaced_writes` — it was not rejected, it was not run yet.
    READ_OVER_WRITE = "read_over_write"
    #: Among contested writes, one could be taken back with nothing that does not yet
    #: exist and the others could not.
    REVERSIBLE_OVER_NOT = "reversible_over_not"
    #: Among contested writes of equal reversibility, one touches fewer of the
    #: resource's fields and collides with fewer other writes.
    NARROWER_BLAST_RADIUS = "narrower_blast_radius"
    #: Among contested writes otherwise alike, one's undo is itself a reversible write
    #: needing no confirmation, and the other's is not.
    CHEAPER_UNDO = "cheaper_undo"
    #: Contested writes that none of the rules above told apart. The selection is
    #: undecided and names them; this is a result, not an error.
    NOTHING_SEPARATED_THEM = "nothing_separated_them"
    #: Nothing scored. Also a result — the matcher declining is how small talk stays
    #: small talk.
    NO_CANDIDATE = "no_candidate"


@dataclass(frozen=True)
class Candidate:
    """One capability the message could name, and how well."""

    capability_id: str
    score: int
    #: Points behind the leader. Zero for the leader itself.
    margin: int
    is_write: bool
    risk: str = ""
    confirmation: str = ""
    #: True when this candidate is within :data:`NEAR_TIE` of the leader.
    contested: bool = False

    def __bool__(self) -> bool:
        return bool(self.capability_id)


@dataclass(frozen=True)
class Selection:
    """What to run, or the honest statement that the message does not say."""

    ok: bool = False
    #: False when candidates were contested and no rule separated them. The caller must
    #: ask rather than proceed.
    decided: bool = False
    #: Empty whenever :attr:`decided` is false. There is no "best guess" field on
    #: purpose: a caller that wanted one would use ``match_capability``, which is still
    #: there and still returns exactly that.
    capability_id: str = ""
    separator: Separator = Separator.NO_CANDIDATE
    #: Ranked, best first, truncated to :data:`MAX_CANDIDATES`.
    candidates: tuple[Candidate, ...] = ()
    #: The ids within the near-tie band, leader included, when there is more than one.
    #: Empty when the leader was clear.
    contested: tuple[str, ...] = ()
    #: Writes that were in the contested band and not selected. Named so a caller can
    #: say "did you mean to pause them?" instead of silently doing something else.
    displaced_writes: tuple[str, ...] = ()
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        """True only when a single capability was chosen and nothing contests it."""
        return self.ok and self.decided and bool(self.capability_id)


# ------------------------------------------------------------------------------ rank --

def rank(text: str, *, env: Mapping[str, str] | None = None) -> tuple[Candidate, ...]:
    """Every capability the message scores against, best first.

    Deliberately *not* a reimplementation. ``_tokens``, ``_words``,
    ``_subsequence_score``, ``asks_for_the_action`` and ``_negation_blocks`` are the
    matcher's, imported and called here, so a change to how a phrase is scored changes
    both at once. Their names being private is a real risk — a rename would silently
    strand this module — which is why a test asserts each of them exists rather than
    letting an ``AttributeError`` surface at request time.

    The write exclusions are applied identically: a message that is not asking for the
    action drops writes from consideration entirely, and a negated phrasing drops that
    phrasing. Applying them here rather than filtering afterwards matters, because a
    write excluded by ``asks_for_the_action`` must not appear in the contested band and
    be reported as displaced. It was never a candidate.
    """
    enabled, _ = _enabled(env)
    if not enabled:
        return ()
    return _rank_unguarded(text)


def _rank_unguarded(text: str) -> tuple[Candidate, ...]:
    """The ranking itself, with no flag check. See :func:`rank`."""
    from services import undx_agent_runtime as runtime
    from services.undx_capability_registry import REGISTRY

    message_tokens = runtime._tokens(text)
    if not message_tokens:
        return ()
    message_words = runtime._words(text)
    writes_allowed = runtime.asks_for_the_action(text)

    scored: list[tuple[int, str, Any]] = []
    for spec in REGISTRY.values():
        if spec.is_write and not writes_allowed:
            continue
        best = 0
        for phrase in spec.intents:
            phrase_tokens = runtime._tokens(phrase)
            if spec.is_write and runtime._negation_blocks(message_tokens, phrase_tokens):
                continue
            score = runtime._subsequence_score(
                phrase_tokens, message_tokens, runtime._words(phrase), message_words
            )
            if score > best:
                best = score
        if best:
            scored.append((best, spec.capability_id, spec))

    if not scored:
        return ()
    # Descending score, then ascending capability id — the same tie-break
    # ``match_capability`` uses, so the head of this list is its answer and not merely
    # usually its answer.
    scored.sort(key=lambda row: (-row[0], row[1]))
    leader = scored[0][0]
    return tuple(
        Candidate(
            capability_id=capability_id,
            score=score,
            margin=leader - score,
            is_write=spec.is_write,
            risk=spec.risk,
            confirmation=spec.confirmation,
            contested=(leader - score) <= NEAR_TIE,
        )
        for score, capability_id, spec in scored[:MAX_CANDIDATES]
    )


# ---------------------------------------------------------------------------- select --

def select(text: str, *, env: Mapping[str, str] | None = None) -> Selection:
    """Choose one capability from everything the message could name, or decline to.

    Returns a refusal rather than raising for an empty or unmatched message, for the
    same reason :func:`~services.undx_brain.prediction.predict` does: "this names no
    operation" is an answer, and turning it into an exception forces every caller to
    handle it to avoid failing the whole turn.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Selection(
            ok=False,
            separator=Separator.NO_CANDIDATE,
            reason="selection is disabled; match_capability is unchanged",
            notes=notes,
        )

    candidates = _rank_unguarded(text)
    if not candidates:
        return Selection(
            ok=True,
            decided=False,
            separator=Separator.NO_CANDIDATE,
            reason="no registered phrasing scored against this message",
            notes=notes,
        )

    contested = tuple(item.capability_id for item in candidates if item.contested)
    leader = candidates[0]

    if len(contested) < 2:
        return Selection(
            ok=True,
            decided=True,
            capability_id=leader.capability_id,
            separator=Separator.UNCONTESTED,
            candidates=candidates,
            reason=_uncontested_reason(candidates),
            notes=notes,
        )

    band = [item for item in candidates if item.contested]
    reads = [item for item in band if not item.is_write]
    writes = [item for item in band if item.is_write]

    # Rule one, and the only one that is not a preference: when doing something and
    # looking at something score alike, look. Not because reads are better, but because
    # at one point of separation the message has not said which, and of the two the read
    # is both recoverable and the thing that would settle the question. Placed first and
    # unconditionally, so no later rule can promote a write back over it.
    if reads and writes:
        chosen = reads[0]
        return Selection(
            ok=True,
            decided=True,
            capability_id=chosen.capability_id,
            separator=Separator.READ_OVER_WRITE,
            candidates=candidates,
            contested=contested,
            displaced_writes=tuple(sorted(item.capability_id for item in writes)),
            reason=(
                f"{chosen.capability_id} and "
                f"{', '.join(sorted(item.capability_id for item in writes))} scored within "
                f"{NEAR_TIE} of each other; the read was taken because the message does "
                f"not say which, and reading is what would say"
            ),
            notes=notes,
        )

    if not writes:
        # Contested reads. Nothing here can damage anything, and refusing to answer
        # "show my alerts" because ``get`` scored one point behind ``list`` would be
        # pedantry rather than caution. The matcher's leader stands, and the fact that
        # it was contested is still reported so a caller can offer the other.
        return Selection(
            ok=True,
            decided=True,
            capability_id=leader.capability_id,
            separator=Separator.UNCONTESTED,
            candidates=candidates,
            contested=contested,
            reason=(
                f"{' and '.join(contested)} scored within {NEAR_TIE} of each other and "
                f"are all reads; the leader stands because neither choice changes anything"
            ),
            notes=notes,
        )

    return _separate_writes(writes, candidates, contested, notes, env)


def _separate_writes(
    writes: list[Candidate],
    candidates: tuple[Candidate, ...],
    contested: tuple[str, ...],
    notes: tuple[str, ...],
    env: Mapping[str, str] | None,
) -> Selection:
    """Two or more contested writes. Separate them on declared data, or decline.

    Every rule below reads a :class:`~services.undx_brain.prediction.Prediction`, which
    is itself derived from nothing but the registry. If prediction is unavailable —
    its own flag is off, or it declined — there is no ground to prefer one write over
    another, and the answer is undecided. Falling back to the score would be exactly
    the behaviour this module was built to replace, dressed as a fallback.
    """
    predictions: dict[str, Any] = {}
    for item in writes:
        outcome = _prediction.predict(item.capability_id, {}, env=env)
        if not outcome.ok:
            return Selection(
                ok=True,
                decided=False,
                separator=Separator.NOTHING_SEPARATED_THEM,
                candidates=candidates,
                contested=contested,
                displaced_writes=tuple(sorted(w.capability_id for w in writes)),
                reason=(
                    f"{', '.join(sorted(w.capability_id for w in writes))} are contested "
                    f"writes and prediction is unavailable, so nothing can be said about "
                    f"which is safer; the score alone is not grounds to choose"
                ),
                notes=notes + outcome.notes,
            )
        predictions[item.capability_id] = outcome

    ordered = sorted(writes, key=lambda item: item.capability_id)

    def _winner(key, separator: Separator, why) -> Selection | None:
        scores = {item.capability_id: key(predictions[item.capability_id])
                  for item in ordered}
        best = min(scores.values())
        winners = [cid for cid, value in scores.items() if value == best]
        if len(winners) != 1:
            return None
        chosen = winners[0]
        losers = tuple(sorted(cid for cid in scores if cid != chosen))
        return Selection(
            ok=True,
            decided=True,
            capability_id=chosen,
            separator=separator,
            candidates=candidates,
            contested=contested,
            displaced_writes=losers,
            reason=(
                f"{chosen} was preferred over {', '.join(losers)}: "
                f"{why(predictions[chosen], [predictions[cid] for cid in losers])}"
            ),
            notes=notes,
        )

    # Ordered by how much of the decision each rule can be trusted to carry. Whether a
    # call can be taken back at all dominates how wide it is, and both dominate what
    # undoing it costs.
    #
    # Each rule's explanation is built from the predictions rather than written as a
    # constant, because a constant is a claim that has to be true of every pair the rule
    # ever separates. "It can be taken back" would be false of ``crypto.alerts.update``
    # winning over ``crypto.alerts.delete``: update is only recoverable if its prior
    # values were read first. What is true, and all that is being asserted, is that one
    # is *easier to take back than the other*.
    rules = (
        (
            lambda p: _reversal_rank(p.reversal),
            Separator.REVERSIBLE_OVER_NOT,
            lambda win, lost: (
                f"reversing it is {win.reversal.value}, against "
                f"{', '.join(sorted({p.reversal.value for p in lost}))} for the rest"
            ),
        ),
        (
            lambda p: (len(p.conflicting_writes), len(p.also_writes_this_resource),
                       len(p.pre_read_fields)),
            Separator.NARROWER_BLAST_RADIUS,
            lambda win, lost: (
                f"it collides with {len(win.conflicting_writes)} other writes on the same "
                f"resource and destroys {len(win.pre_read_fields)} unrecorded prior values, "
                f"fewer than the rest"
            ),
        ),
        (
            lambda p: 0 if p.undo_is_cheap else 1,
            Separator.CHEAPER_UNDO,
            lambda win, lost: (
                f"undoing it via {win.undo_capability_id} is itself a reversible write "
                f"needing no confirmation, and undoing the rest is not"
            ),
        ),
    )
    for key, separator, why in rules:
        found = _winner(key, separator, why)
        if found is not None:
            return found

    return Selection(
        ok=True,
        decided=False,
        separator=Separator.NOTHING_SEPARATED_THEM,
        candidates=candidates,
        contested=contested,
        displaced_writes=tuple(sorted(item.capability_id for item in writes)),
        reason=(
            f"{', '.join(sorted(item.capability_id for item in writes))} scored within "
            f"{NEAR_TIE} of each other and are alike in reversibility, blast radius and "
            f"undo cost; the message has to say which"
        ),
        notes=notes,
    )


# ----------------------------------------------------------------------------- parts --

#: Lower is preferred. Ordered by what reversal actually costs the person, which is not
#: the order the enum is declared in and should not be inferred from it: needing an id
#: that does not exist yet is worse than a clean inverse but better than having to have
#: read the old values first, and everything is better than a call that cannot be
#: reversed at all.
_REVERSAL_ORDER = {
    Reversal.NOT_A_WRITE: 0,
    Reversal.EXACT_INVERSE: 1,
    Reversal.PENDING_IDENTITY: 2,
    Reversal.REQUIRES_PRE_READ: 3,
    Reversal.IRRECOVERABLE: 4,
}


def _reversal_rank(reversal: Reversal) -> int:
    """Where a reversal class sits, with an unknown one sorting last rather than first.

    A ``Reversal`` member added later and not added here would otherwise default to
    zero and be *preferred* over an exact inverse, which is the wrong direction to fail
    in. A test walks the enum and asserts the table is complete, so this default is a
    second line rather than the only one.
    """
    return _REVERSAL_ORDER.get(reversal, max(_REVERSAL_ORDER.values()) + 1)


def _uncontested_reason(candidates: tuple[Candidate, ...]) -> str:
    leader = candidates[0]
    if len(candidates) == 1:
        return f"{leader.capability_id} is the only capability this message scores against"
    runner_up = candidates[1]
    return (
        f"{leader.capability_id} leads {runner_up.capability_id} by "
        f"{runner_up.margin}, which is clear of the {NEAR_TIE}-point near-tie band"
    )


def _enabled(env: Mapping[str, str] | None) -> tuple[bool, tuple[str, ...]]:
    """Both flags, fail-closed. The package flag alone does not turn this on."""
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    on = bool(values.get("UNDX_BRAIN_ENABLED", False)) and bool(
        values.get("UNDX_BRAIN_SELECTION_ENABLED", False)
    )
    return on, tuple(resolution.notes)
