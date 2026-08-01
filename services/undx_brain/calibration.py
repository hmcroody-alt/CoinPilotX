"""Whether the answers were right, asked of the record rather than of the model.

Everything else in this package reasons about one turn. :mod:`~services.undx_brain.evidence`
decides how well a thing is known *now*, :mod:`~services.undx_brain.truth` decides what
may be said about it *now*, and both are finished by the time the response is sent. That
leaves the question nobody was asking: of the answers already given, which ones turned
out to be wrong.

The material for it exists. ``pulse_ai_service`` writes ``agent_action`` and
``message_answered`` when it produces something, and ``feedback_recorded`` when the
person says what they thought of it, and all three carry ``message_id`` in their
metadata. So a claim and a verdict about that claim can be put next to each other, which
is the whole of what this module does. :mod:`~services.undx_brain.learning` already reads
that table safely — owner-scoped through :mod:`~services.undx_brain.memory`, bounded,
parsed, with unattributable rows accounted for — so this reads through a
:class:`~services.undx_brain.learning.Window` and opens no connection of its own.

Four things this refuses to do, each of which is the easy version of the same mistake.

*It does not treat silence as approval.* Most answers are never rated. The unjudged are
counted and reported next to the rate, loudly, because a 90% approval rate over eleven
ratings out of four hundred answers is a fact about eleven people, and the number that
matters for reading it is four hundred.

*It does not fold "not helpful" into "wrong".* The five ratings the service accepts are
``helpful``, ``not_helpful``, ``wrong``, ``unsafe`` and ``outdated``. Three of those are
claims that the answer was incorrect; one is a claim that a correct answer did not help.
Rounding the second into the first inflates the error rate, and rounding it the other way
hides a real complaint, so it is its own verdict and it is outside the correctness
denominator — the same discipline :mod:`learning` applies to an event that could not have
carried a dimension.

*It does not report a rate without its interval.* :data:`MIN_JUDGED` is 12 because 12 is
the smallest number of judged answers at which the worst-case 95% Wilson half-width first
falls below ±0.25, and ±0.25 is the coarsest distinction this would ever be used to draw.
Below it nothing is concluded; above it the interval is reported alongside the point
estimate, because "80% correct" from ten ratings and from a thousand are the same
sentence and not the same evidence.

*It does not feed back into* :mod:`~services.undx_brain.selection`. Observing is not
steering. A module that noticed a capability was often corrected and then quietly
down-ranked it would be making a causal claim — that the capability caused the
correction — out of a correlation between two rows, and it would do it where nobody
reading the selection code would see it. The refusal is deliberate, and there is a test
that fails if an import appears.

One limitation to state rather than paper over: ``memory_corrected`` carries a
``memory_id`` and no ``message_id``, so a corrected memory cannot be joined to the answer
that produced it. That correction is invisible here. It is a gap in the writer, not
something to be inferred around.

Behind ``UNDX_BRAIN_CALIBRATION_ENABLED``, which defaults off. Off, every entry point
answers ``ok=False`` with a reason, so a disabled reader is never mistaken for one that
looked and found nothing wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from . import config as brain_config
from . import facts
from . import learning
from .learning import Event, Window

__all__ = [
    "CLAIM_EVENTS",
    "VERDICT_EVENT",
    "JOIN_KEY",
    "RATINGS",
    "MIN_JUDGED",
    "CONFIDENCE_Z",
    "WIDEST_USEFUL_HALF_WIDTH",
    "Verdict",
    "Answer",
    "Interval",
    "Calibration",
    "interval",
    "verdict_for",
    "pair",
    "calibrate",
    "by_capability",
]

#: The event types that record UNDX having claimed something. Both carry ``message_id``.
#: ``agent_action`` additionally carries ``capability_id``, which is what makes
#: :func:`by_capability` possible and is the only reason the two are not merged.
CLAIM_EVENTS = ("agent_action", "message_answered")

#: The one event type that records a person's judgement of a claim.
VERDICT_EVENT = "feedback_recorded"

#: The metadata key both sides carry. Named once so a second spelling cannot drift in.
JOIN_KEY = "message_id"

#: Every rating ``record_feedback`` accepts, mapped to what it says about correctness.
#: Taken from the writer's own allowlist rather than from a guess, and exhaustive: a
#: rating outside this map is treated as unreadable rather than as approval.
RATINGS: Mapping[str, str] = {
    "helpful": "approved",
    "not_helpful": "unhelpful",
    "wrong": "corrected",
    "unsafe": "corrected",
    "outdated": "corrected",
}

#: The ratings that say the answer was not merely unwelcome but harmful. Kept separate
#: from the rest of ``corrected`` because one ``unsafe`` is worth reading on its own and
#: an aggregate rate is exactly the thing that would bury it.
SEVERE_RATINGS = frozenset({"unsafe"})

#: 95%, two-sided.
CONFIDENCE_Z = 1.959963984540054

#: The coarsest distinction this module would ever be used to draw: better than half,
#: worse than half. An interval wider than this cannot even support that, so a rate
#: computed over fewer observations is arithmetic rather than evidence.
WIDEST_USEFUL_HALF_WIDTH = 0.25

#: The floor, derived rather than chosen. Worst-case 95% Wilson half-widths, maximised
#: over the number of successes: n=10 gives 0.2634, n=11 gives 0.2536, n=12 gives 0.2462.
#: Twelve is the first n whose widest possible interval fits inside
#: :data:`WIDEST_USEFUL_HALF_WIDTH`. There is a test that recomputes this rather than
#: trusting the comment, so the constant cannot drift away from its own justification.
MIN_JUDGED = 12


class Verdict(str, Enum):
    """What the record says about one answer.

    Four values and not two, because the two that would be dropped are the two that
    carry the most meaning. :attr:`UNJUDGED` is the common case and must be visible;
    :attr:`UNHELPFUL` is a real complaint that is not a claim of error.
    """

    APPROVED = "approved"
    CORRECTED = "corrected"
    UNHELPFUL = "unhelpful"
    UNJUDGED = "unjudged"

    @property
    def counts_toward_correctness(self) -> bool:
        """Whether this verdict belongs in the denominator of a correctness rate."""
        return self in (Verdict.APPROVED, Verdict.CORRECTED)


@dataclass(frozen=True)
class Answer:
    """One thing UNDX claimed, and what the person said about it afterwards."""

    message_id: str = ""
    claim_event: str = ""
    capability_id: str = ""
    claimed_at: str = ""
    verdict: Verdict = Verdict.UNJUDGED
    #: The raw rating string, kept so a caller can tell ``unsafe`` from ``outdated``
    #: after they have both become :attr:`Verdict.CORRECTED`.
    rating: str = ""
    judged_at: str = ""
    severe: bool = False
    notes: tuple[str, ...] = field(default=(), repr=False)

    @property
    def judged(self) -> bool:
        return self.verdict is not Verdict.UNJUDGED


@dataclass(frozen=True)
class Interval:
    """A Wilson score interval, and whether it is narrow enough to mean anything."""

    low: float = 0.0
    high: float = 1.0
    point: float = 0.0
    n: int = 0

    @property
    def half_width(self) -> float:
        return (self.high - self.low) / 2.0

    @property
    def useful(self) -> bool:
        return self.n > 0 and self.half_width <= WIDEST_USEFUL_HALF_WIDTH

    def __str__(self) -> str:
        return f"{self.point:.0%} (95% CI {self.low:.0%}–{self.high:.0%}, n={self.n})"


@dataclass(frozen=True)
class Calibration:
    """How often the answers on record turned out to be right.

    Truthiness is :attr:`conclusive` and not :attr:`ok`, for the same reason as
    :class:`~services.undx_brain.learning.Finding`: a correctly computed rate over eight
    ratings is a successful call and an unusable answer, and a caller that checks the
    wrong one of those reports a coin flip as a measurement.
    """

    ok: bool = False
    conclusive: bool = False
    scope: str = ""
    #: A sentence safe to say as-is: hedged, time-qualified, and already carrying the
    #: unjudged count, so a caller cannot quote the rate without the caveat.
    answer: str = ""
    answers: int = 0
    judged: int = 0
    #: Answers nobody rated. The number that decides what the rate is worth.
    unjudged: int = 0
    approved: int = 0
    corrected: int = 0
    #: Rated ``not_helpful``: judged, and deliberately outside the correctness
    #: denominator.
    unhelpful: int = 0
    severe: int = 0
    interval: Interval | None = None
    reading: facts.Reading | None = None
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.conclusive

    @property
    def coverage(self) -> float:
        """The share of answers anybody judged at all. Never the correctness rate."""
        return (self.judged / self.answers) if self.answers else 0.0


# ---------------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------------


def interval(successes: int, n: int) -> Interval:
    """The Wilson score interval for ``successes`` of ``n``.

    Wilson rather than the normal approximation, and not as a style preference: at the
    sample sizes this module actually sees, the normal interval around a rate of 1.0 has
    zero width, which would report perfect certainty from twelve ratings. Wilson does
    not, which is the entire reason :data:`MIN_JUDGED` could be derived at all.
    """
    total = max(0, int(n))
    hits = min(max(0, int(successes)), total)
    if total <= 0:
        return Interval(low=0.0, high=1.0, point=0.0, n=0)
    p = hits / total
    z = CONFIDENCE_Z
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2 * total)) / denominator
    spread = (z / denominator) * math.sqrt(p * (1.0 - p) / total + z2 / (4 * total * total))
    return Interval(
        low=max(0.0, center - spread),
        high=min(1.0, center + spread),
        point=p,
        n=total,
    )


def verdict_for(rating: Any) -> tuple[Verdict, str]:
    """Turn one rating string into a verdict, refusing to guess.

    Returns the verdict and the cleaned rating. An unrecognised rating becomes
    :attr:`Verdict.UNJUDGED` rather than anything else: the service's allowlist is the
    definition of what a rating is, and a value outside it is a row this module does not
    understand. Treating it as approval would be the one direction of error that quietly
    improves the number.
    """
    text = _text(rating).lower().replace(" ", "_")
    name = RATINGS.get(text)
    if name is None:
        return Verdict.UNJUDGED, text
    return Verdict(name), text


# ---------------------------------------------------------------------------------
# joining
# ---------------------------------------------------------------------------------


def pair(window: Window, *, env: Mapping[str, str] | None = None) -> tuple[Answer, ...]:
    """Every claim in the window, each carrying the verdict that was passed on it.

    Newest claim first, matching the window's own order. Three joining rules, each of
    which exists because the obvious version of it is wrong:

    A verdict must be *no earlier* than the claim it judges. Feedback timestamped before
    the answer it rates is incoherent data, and pairing it anyway would let a rating of
    one message be read as a rating of a later message that reused the id.

    Only the *newest* verdict per message counts. The feedback table takes repeated
    inserts, so one message can be rated twice; counting both would let one person move
    the rate by two. A disagreeing earlier rating is noted on the answer rather than
    dropped silently.

    A missing or non-positive ``message_id`` joins to nothing. Zero is what an absent id
    parses to, and letting zeros match zeros would collapse every unlabelled claim and
    every unlabelled verdict into one enormous false pairing.
    """
    enabled, _ = _enabled(env)
    if not enabled or not isinstance(window, Window) or not window.ok:
        return ()

    verdicts: dict[str, list[Event]] = {}
    for event in window.events:
        if event.event_type != VERDICT_EVENT:
            continue
        key = _message_id(event)
        if not key:
            continue
        verdicts.setdefault(key, []).append(event)

    answers: list[Answer] = []
    for event in window.events:
        if event.event_type not in CLAIM_EVENTS:
            continue
        key = _message_id(event)
        notes: list[str] = []
        if not key:
            answers.append(
                Answer(
                    claim_event=event.event_type,
                    capability_id=_text(event.metadata.get("capability_id")),
                    claimed_at=event.created_at,
                    notes=(
                        "the claim carries no message_id, so no verdict can ever be "
                        "joined to it",
                    ),
                )
            )
            continue

        eligible = [
            candidate
            for candidate in verdicts.get(key, ())
            if candidate.created_at >= event.created_at
        ]
        skipped = len(verdicts.get(key, ())) - len(eligible)
        if skipped:
            notes.append(
                f"{skipped} feedback row(s) for this message predate the answer and "
                "were not read as a judgement of it"
            )
        if not eligible:
            answers.append(
                Answer(
                    message_id=key,
                    claim_event=event.event_type,
                    capability_id=_text(event.metadata.get("capability_id")),
                    claimed_at=event.created_at,
                    notes=tuple(notes),
                )
            )
            continue

        eligible.sort(key=lambda item: (item.created_at, item.event_id))
        newest = eligible[-1]
        decision, rating = verdict_for(newest.metadata.get("rating"))
        if decision is Verdict.UNJUDGED and rating:
            notes.append(
                f"the rating {rating!r} is not one the service accepts, so it was not "
                "read as a judgement either way"
            )
        earlier = {
            verdict_for(item.metadata.get("rating"))[0]
            for item in eligible[:-1]
        }
        if earlier - {decision}:
            notes.append(
                "this message was rated more than once and the ratings disagree; only "
                "the newest is counted"
            )

        answers.append(
            Answer(
                message_id=key,
                claim_event=event.event_type,
                capability_id=_text(event.metadata.get("capability_id")),
                claimed_at=event.created_at,
                verdict=decision,
                rating=rating,
                judged_at=newest.created_at,
                severe=rating in SEVERE_RATINGS,
                notes=tuple(notes),
            )
        )

    return tuple(answers)


# ---------------------------------------------------------------------------------
# the question
# ---------------------------------------------------------------------------------


def calibrate(
    window: Window,
    *,
    capability_id: str = "",
    now: Any = None,
    env: Mapping[str, str] | None = None,
) -> Calibration:
    """How often this owner's answers were judged right, and how much that is worth.

    Pass ``capability_id`` to ask it of one capability. The floor applies to the subset,
    not to the window: a capability with three ratings is inconclusive even inside an
    account with four hundred.
    """
    enabled, notes = _enabled(env)
    scope = capability_id or "all answers"
    if not enabled:
        return Calibration(
            scope=scope,
            reason=(
                "calibration is disabled; nothing observes whether the answers were "
                "right"
            ),
            notes=tuple(notes),
        )
    if not isinstance(window, Window) or not window.ok:
        return Calibration(
            scope=scope,
            reason=getattr(window, "reason", "") or "no window to read",
            notes=tuple(notes) + tuple(getattr(window, "notes", ())),
        )

    everything = pair(window, env=env)
    if capability_id:
        selected = tuple(
            item for item in everything if item.capability_id == capability_id
        )
    else:
        selected = everything

    approved = sum(1 for item in selected if item.verdict is Verdict.APPROVED)
    corrected = sum(1 for item in selected if item.verdict is Verdict.CORRECTED)
    unhelpful = sum(1 for item in selected if item.verdict is Verdict.UNHELPFUL)
    severe = sum(1 for item in selected if item.severe)
    judged = approved + corrected
    unjudged = sum(1 for item in selected if not item.judged)

    finding_notes = list(notes) + list(window.notes)
    if unhelpful:
        finding_notes.append(
            f"{unhelpful} answer(s) were rated not_helpful, which is a complaint about "
            "usefulness rather than about correctness, and is outside the denominator"
        )
    if severe:
        finding_notes.append(
            f"{severe} answer(s) were rated unsafe; that is worth reading individually "
            "and an aggregate rate is where it would be buried"
        )
    if window.truncated:
        finding_notes.append(
            "the window was truncated, so older answers and older feedback exist unread"
        )

    reading = _reading(window, now=now, env=env)
    if not reading.ok:
        finding_notes.append(
            "the result could not be placed in time, so it is reported and not "
            f"concluded: {reading.reason}"
        )

    band = interval(approved, judged)
    coverage = (judged + unhelpful) / len(selected) if selected else 0.0

    if judged < MIN_JUDGED:
        return Calibration(
            ok=True, conclusive=False, scope=scope,
            answer=_not_yet(judged, scope, len(selected)),
            answers=len(selected), judged=judged, unjudged=unjudged,
            approved=approved, corrected=corrected, unhelpful=unhelpful, severe=severe,
            interval=band, reading=reading,
            reason=(
                f"{judged} judged answer(s) is below the floor of {MIN_JUDGED}; "
                f"{MIN_JUDGED - judged} more would reach it"
            ),
            notes=tuple(finding_notes),
        )

    sentence = (
        f"of {judged} answers about {scope} that someone judged, "
        f"{approved} were called correct: {band}"
    )
    sentence += (
        f"; {unjudged} more went unjudged, so this describes the "
        f"{coverage:.0%} of answers anyone rated and not the rest"
    )
    if severe:
        # The plural has to be conditional. "1 were rated unsafe" is the sentence a
        # person stops reading carefully, and this is the one clause in the whole
        # sentence that must survive being read carelessly.
        sentence += (
            f"; {severe} {'was' if severe == 1 else 'were'} rated unsafe"
        )

    return Calibration(
        ok=True,
        conclusive=reading.ok and band.useful,
        scope=scope,
        answer=_in_time(sentence, reading),
        answers=len(selected), judged=judged, unjudged=unjudged,
        approved=approved, corrected=corrected, unhelpful=unhelpful, severe=severe,
        interval=band, reading=reading,
        reason="" if (reading.ok and band.useful) else (
            reading.reason if not reading.ok
            else (
                f"the interval is ±{band.half_width:.0%}, wider than the "
                f"±{WIDEST_USEFUL_HALF_WIDTH:.0%} needed to distinguish anything"
            )
        ),
        notes=tuple(finding_notes),
    )


def by_capability(
    window: Window,
    *,
    now: Any = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Calibration, ...]:
    """One calibration per capability that appears in the window, worst first.

    Ordered by correction count and not by rate, because a rate over four ratings sorts
    above everything and means nothing. Every capability is returned, including the ones
    with too little evidence to conclude anything about — they come back with
    ``conclusive=False`` and a reason, which is a different and more useful answer than
    being absent from the list.
    """
    enabled, _ = _enabled(env)
    if not enabled or not isinstance(window, Window) or not window.ok:
        return ()
    names = sorted(
        {item.capability_id for item in pair(window, env=env) if item.capability_id}
    )
    results = [
        calibrate(window, capability_id=name, now=now, env=env) for name in names
    ]
    results.sort(key=lambda item: (-item.corrected, -item.severe, item.scope))
    return tuple(results)


# ---------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------


def _enabled(env: Mapping[str, str] | None) -> tuple[bool, tuple[str, ...]]:
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    on = bool(values.get("UNDX_BRAIN_ENABLED", False)) and bool(
        values.get("UNDX_BRAIN_CALIBRATION_ENABLED", False)
    )
    return on, tuple(resolution.notes)


def _reading(window: Window, *, now: Any, env: Mapping[str, str] | None) -> facts.Reading:
    """Place the result in time by asking :mod:`facts`, not by deciding here."""
    return facts.read(
        facts.Observation(
            subject=f"{learning.TABLE}.calibration",
            value=f"{len(window.events)} events",
            source=learning.TABLE,
            trust=learning.EVENT_TRUST.value,
            observed_at=window.last_at,
        ),
        now=now,
        env=env,
    )


#: What every assembled sentence says about itself. Borrowed deliberately from
#: :mod:`learning`, which chose it for the same reason: nothing here was confirmed
#: against a running system, it was counted off rows that were.
GROUNDING = learning.GROUNDING


def _in_time(answer: str, reading: facts.Reading) -> str:
    if reading.ok and reading.qualifier:
        return f"{answer}, {GROUNDING} {reading.qualifier}"
    if reading.ok:
        return f"{answer}, {GROUNDING}"
    return f"{answer}, {GROUNDING}, at a time that could not be established"


def _not_yet(judged: int, scope: str, answers: int) -> str:
    return (
        f"{judged} of {answers} answer(s) about {scope} have been judged, which is "
        f"below the {MIN_JUDGED} needed before a correctness rate distinguishes "
        f"anything from anything"
    )


def _message_id(event: Event) -> str:
    """The join key for one event, or empty when it has none that can join.

    Rendered as text so ``7`` and ``"7"`` are the same key, and refused when it is zero
    or negative, because zero is what a missing id parses to on the writing side.
    """
    raw = event.metadata.get(JOIN_KEY)
    if raw is None or isinstance(raw, bool):
        return ""
    try:
        value = int(raw)
    except Exception:
        text = _text(raw)
        return text if text else ""
    return str(value) if value > 0 else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
