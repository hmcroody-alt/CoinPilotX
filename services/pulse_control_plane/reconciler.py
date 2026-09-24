"""Read-only reconciliation: what the config claims vs. what production does.

This module changes nothing. It compares two descriptions of the same fifteen
capabilities — the stored ``feature_flags`` row and measured production reality —
and names the gap. Everything downstream (the migration manifest, the drift
detector, the CI gate) consumes its verdicts; nothing consumes its opinions,
because it has none that are not traceable to a listed signal.

Why evidence is weighted rather than counted
--------------------------------------------

Stage 16's constraint — "do not let one weak signal declare something live" — is
not hypothetical here. Mission 1 declared ``premium_advanced_tools`` deployed on
the strength of seven ``enabled`` rows in ``pulse_premium_feature_flags``, a
table whose only reader renders it as HTML. That is a configuration row being
offered as evidence for itself. Counting signals would have accepted it;
weighting them does not.

So each signal carries a strength, and the aggregation has three properties that
matter more than the specific numbers:

1. **Weak evidence saturates.** All weak signals together contribute at most
   :data:`WEAK_CONTRIBUTION_CAP`, which is below :data:`DEPLOYED_THRESHOLD`. No
   quantity of documentation, seeded config or operator assertion can reach the
   bar on its own. Something must have *executed* — a route must answer, rows
   must exist, a guard must be in the deployed source.
2. **Absence beats presence.** A strong negative (a 404, a falsy env gate, a
   missing implementation) is not subtracted from the positives; it decides. A
   capability whose route is not mounted is not "mostly live".
3. **Contradiction is a real answer.** A strong positive and a strong negative
   together yield ``UNKNOWN``, not a tie-break. ``UNKNOWN`` denies in
   :func:`~services.pulse_control_plane.model.evaluate`, so an unresolved
   contradiction fails closed all the way through.

What "understated" and "overstated" each cost
---------------------------------------------

The two directions are not symmetric and the report should never imply they are.

``OVERSTATED`` — config claims more exposure than production grants. Wiring it
up hands access to subjects production currently refuses. ``admin_command``
stored as ``enabled``, the one legacy word meaning "unconditionally visible and
usable", against 199 permission-checked routes, is this case.

``UNDERSTATED`` — config claims less exposure than production grants. Wiring it
up **withdraws a working feature**. ``marketplace_checkout`` stored
``internal-only`` against 32 real orders is this case: the first read of that
flag removes the checkout button from every non-admin who is currently buying.

Both are outages. Only one is a security incident. The severity in
:class:`Reconciliation` says which.

On the inventory auditing itself
--------------------------------

:func:`reconcile` also checks the signal-derived reachability against the
deployment state that :mod:`~services.pulse_control_plane.capabilities` declares.
A mismatch there is not drift in the stored config — it is this package being
wrong about production, which is the failure mode Mission 1 shipped. It is
reported as ``inventory_contradicted`` and the Stage 27 suite asserts the flag is
clear for all fifteen rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from services.pulse_control_plane.capabilities import (
    CAPABILITIES,
    LEGACY_EXPOSURE,
    by_key,
)
from services.pulse_control_plane.model import (
    DENYING_STATES,
    REACHABLE_STATES,
    Capability,
    EligibilityPolicy,
)

# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

STRONG = 3
MODERATE = 2
WEAK = 1

#: The bar a capability's positive evidence must clear to count as reachable.
#: Equal to one strong signal, or two moderate ones.
DEPLOYED_THRESHOLD = 3

#: The most that weak evidence can ever contribute, in total, however much of it
#: there is. Strictly below :data:`DEPLOYED_THRESHOLD`, which is the point: weak
#: signals can corroborate a conclusion, never carry one.
WEAK_CONTRIBUTION_CAP = 1

assert WEAK_CONTRIBUTION_CAP < DEPLOYED_THRESHOLD

#: ``kind -> (strength, polarity, what observing it actually proves)``.
#:
#: The asymmetry between ``DATA_PRESENT`` (moderate, positive) and ``DATA_EMPTY``
#: (weak, negative) is deliberate and is the single most useful thing in this
#: table. A row in a production table can only exist because the code that
#: writes it ran; an empty table proves only that nobody used the feature, which
#: on a platform with 11 monthly actives is the expected state of a perfectly
#: healthy capability.
SIGNAL_KINDS: dict[str, tuple[int, int, str]] = {
    "ROUTE_OK": (STRONG, +1, "unauthenticated probe got 200; the route is mounted and answering"),
    "ROUTE_AUTH_GATED": (
        STRONG,
        +1,
        "unauthenticated probe got 401/403, or 302 to login; mounted, and guarding itself",
    ),
    "ROUTE_WRONG_METHOD": (STRONG, +1, "unauthenticated probe got 405; mounted, wrong verb"),
    "ROUTE_MISSING": (STRONG, -1, "probe got 404 at a path taken from the route table"),
    "SOURCE_GUARD": (MODERATE, +1, "a guard at a cited source line decides exposure in deployed code"),
    "SOURCE_MISSING": (STRONG, -1, "the cited implementation is not in the deployed tree"),
    "ENV_GATE_ON": (MODERATE, +1, "a named environment gate is truthy in the production service"),
    "ENV_GATE_OFF": (STRONG, -1, "a named environment gate is falsy in the production service"),
    "DATA_PRESENT": (MODERATE, +1, "rows exist in production that only this capability writes"),
    "DATA_EMPTY": (WEAK, -1, "the capability's own table is empty"),
    "CLIENT_SHIPPED": (WEAK, +1, "the shipped client exposes a surface for it"),
    "OPERATOR_CLAIM": (WEAK, +1, "a document, a seeded row or a person asserts it"),
}

#: A 404 is only ``ROUTE_MISSING`` when the path was read out of the route table.
#: A 404 at a path someone guessed from the capability's name proves that the
#: guess was wrong and nothing else — and since ``ROUTE_MISSING`` is a veto, that
#: mistake would not merely weaken a verdict, it would flip one to ``ABSENT``.
#: Three of the first probe paths tried during this reconciliation (``…/reactions``,
#: ``…/ai/chat``, ``…/marketplace/checkout``) returned 404 and every one of them
#: was an invented path; the real routes all answer 401 or 405. None of those
#: 404s is recorded as a signal.
#:
#: Negative signals that decide rather than subtract, and the state each implies.
#: ``DATA_EMPTY`` is pointedly not here.
VETOING_NEGATIVES: dict[str, str] = {
    "ROUTE_MISSING": "ABSENT",
    "SOURCE_MISSING": "ABSENT",
    "ENV_GATE_OFF": "DISABLED",
}


@dataclass(frozen=True)
class Signal:
    """One observation about production, with where it came from.

    ``source`` is required. A signal without a citation is an opinion, and this
    module's whole value is that its conclusions can be re-checked by someone who
    does not trust it.
    """

    kind: str
    source: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SIGNAL_KINDS:
            raise ValueError(f"unknown signal kind {self.kind!r}")
        if not self.source.strip():
            raise ValueError(f"signal {self.kind!r} carries no source; evidence must be citable")

    @property
    def strength(self) -> int:
        return SIGNAL_KINDS[self.kind][0]

    @property
    def polarity(self) -> int:
        return SIGNAL_KINDS[self.kind][1]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedReality:
    """What the signals, taken together, say production is doing."""

    deployment_state: str
    score: int
    confidence: str
    rationale: str
    signals: tuple[Signal, ...] = field(default_factory=tuple)

    @property
    def reachable(self) -> bool:
        return self.deployment_state in REACHABLE_STATES


def aggregate(signals: tuple[Signal, ...], *, gated: bool) -> ObservedReality:
    """Fold signals into one deployment state. Bounded, and fails to UNKNOWN.

    ``gated`` comes from the eligibility axis — whether an authority decides per
    subject — and only chooses between the two reachable states. It cannot make
    an unreachable capability reachable, so a mis-stated eligibility policy
    cannot manufacture deployment evidence.
    """
    positives = [s for s in signals if s.polarity > 0]
    negatives = [s for s in signals if s.polarity < 0]

    strong_positive = [s for s in positives if s.strength == STRONG]
    vetoes = [s for s in negatives if s.kind in VETOING_NEGATIVES]

    if vetoes:
        if strong_positive:
            # Both a mounted route and a falsy gate, or similar. Refusing to pick
            # a winner is the honest outcome, and UNKNOWN denies downstream.
            return ObservedReality(
                deployment_state="UNKNOWN",
                score=0,
                confidence="UNVERIFIED",
                rationale=(
                    "contradictory strong evidence: "
                    + ", ".join(sorted({s.kind for s in vetoes + strong_positive}))
                ),
                signals=signals,
            )
        implied = VETOING_NEGATIVES[vetoes[0].kind]
        return ObservedReality(
            deployment_state=implied,
            score=0,
            confidence="MEASURED",
            rationale=f"{vetoes[0].kind} at {vetoes[0].source}",
            signals=signals,
        )

    firm = sum(s.strength for s in positives if s.strength > WEAK)
    weak = min(sum(s.strength for s in positives if s.strength == WEAK), WEAK_CONTRIBUTION_CAP)
    score = firm + weak

    if score < DEPLOYED_THRESHOLD:
        return ObservedReality(
            deployment_state="UNKNOWN",
            score=score,
            confidence="UNVERIFIED",
            rationale=(
                f"evidence score {score} is below the threshold of {DEPLOYED_THRESHOLD}"
                + (
                    "; weak evidence was capped at "
                    f"{WEAK_CONTRIBUTION_CAP} and cannot reach it alone"
                    if weak and firm < DEPLOYED_THRESHOLD
                    else ""
                )
            ),
            signals=signals,
        )

    state = "LIVE_CONDITIONAL" if gated else "LIVE_GLOBAL"
    confidence = "MEASURED" if score >= DEPLOYED_THRESHOLD + 2 else "INFERRED"
    return ObservedReality(
        deployment_state=state,
        score=score,
        confidence=confidence,
        rationale=f"evidence score {score} from {len(positives)} positive signal(s)",
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Exposure comparison
# ---------------------------------------------------------------------------

#: How widely each policy admits, on one ordinal scale, so "more exposed than"
#: is a comparison rather than a judgement call. Only the ordering is meaningful;
#: the gaps are not distances.
_EXPOSURE_RANK: dict[str, int] = {
    "ANONYMOUS": 5,
    "STANDARD": 4,
    "PREMIUM_ENTITLEMENT": 3,
    "INTERNAL_ONLY": 2,
    "ADMIN_ONLY": 2,
    "OWNER_ONLY": 1,
}


def exposure_rank(deployment_state: str, eligibility: Optional[EligibilityPolicy]) -> int:
    """0 for anything that denies; otherwise how widely the policy admits.

    Collapsing every denying state to 0 is what makes ``DISABLED`` and ``ABSENT``
    compare equal, which is correct for this purpose: both expose nobody, and the
    difference between them is a fact about the code, not about exposure.
    """
    if deployment_state in DENYING_STATES:
        return 0
    if eligibility is None:
        return 0
    return _EXPOSURE_RANK[eligibility.key]


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

RECONCILIATION_VERDICTS = (
    "MATCH",
    "UNDERSTATED",
    "OVERSTATED",
    "UNKNOWN",
    "DEAD_CONFIG",
    "LIVE_WITHOUT_CONFIG",
)

#: Severity per verdict. ``OVERSTATED`` outranks ``UNDERSTATED`` because one is a
#: potential access grant and the other a potential outage — see the module
#: docstring. ``DEAD_CONFIG`` is LOW on its own: a row controlling nothing is
#: clutter until somebody wires it up.
VERDICT_SEVERITY: dict[str, str] = {
    "MATCH": "NONE",
    "OVERSTATED": "CRITICAL",
    "UNDERSTATED": "HIGH",
    "UNKNOWN": "MEDIUM",
    "DEAD_CONFIG": "LOW",
    "LIVE_WITHOUT_CONFIG": "MEDIUM",
}


@dataclass(frozen=True)
class Reconciliation:
    """One capability's configured claim set against measured production."""

    capability_key: str
    verdict: str
    severity: str
    configured_state: Optional[str]
    configured_exposure: int
    observed_state: str
    observed_exposure: int
    observed_confidence: str
    rationale: str
    #: True when measured reality contradicts what ``capabilities.py`` declares.
    #: Not stored-config drift — this package being wrong.
    inventory_contradicted: bool = False
    signals: tuple[Signal, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.verdict not in RECONCILIATION_VERDICTS:
            raise ValueError(f"unknown verdict {self.verdict!r}")
        if self.severity != VERDICT_SEVERITY[self.verdict]:
            raise ValueError(
                f"{self.capability_key!r}: severity {self.severity!r} does not match "
                f"verdict {self.verdict!r}"
            )


def reconcile(
    capability: Capability,
    stored_state: Optional[str],
    signals: tuple[Signal, ...],
) -> Reconciliation:
    """Compare one stored row against one set of production observations.

    ``stored_state`` is the raw legacy word from ``feature_flags.state``, or
    ``None`` when no row exists. It is looked up in
    :data:`~services.pulse_control_plane.capabilities.LEGACY_EXPOSURE` rather
    than parsed, because the legacy vocabulary is deliberately unparseable at
    runtime and this is the one place it is given meaning.
    """
    gated = capability.eligibility.requires_authority
    observed = aggregate(signals, gated=gated)

    observed_exposure = exposure_rank(observed.deployment_state, capability.eligibility)

    # Does measured reality agree with what this package asserts? Checked before
    # the config comparison so a wrong inventory is never laundered into a clean
    # MATCH against an equally wrong stored row.
    declared_reachable = capability.deployment_state in REACHABLE_STATES
    contradicted = observed.deployment_state != "UNKNOWN" and (
        observed.reachable != declared_reachable
    )

    if stored_state is None:
        if observed.reachable:
            return Reconciliation(
                capability_key=capability.key,
                verdict="LIVE_WITHOUT_CONFIG",
                severity=VERDICT_SEVERITY["LIVE_WITHOUT_CONFIG"],
                configured_state=None,
                configured_exposure=0,
                observed_state=observed.deployment_state,
                observed_exposure=observed_exposure,
                observed_confidence=observed.confidence,
                rationale=(
                    "reachable in production with no row in feature_flags: "
                    + observed.rationale
                ),
                inventory_contradicted=contradicted,
                signals=signals,
            )
        return Reconciliation(
            capability_key=capability.key,
            verdict="UNKNOWN",
            severity=VERDICT_SEVERITY["UNKNOWN"],
            configured_state=None,
            configured_exposure=0,
            observed_state=observed.deployment_state,
            observed_exposure=observed_exposure,
            observed_confidence=observed.confidence,
            rationale="no stored row and insufficient evidence: " + observed.rationale,
            inventory_contradicted=contradicted,
            signals=signals,
        )

    translated = LEGACY_EXPOSURE.get(stored_state)
    if translated is None:
        # A stored value outside the legacy vocabulary. Not guessed at: the row
        # is unreadable, which is a MEDIUM finding and a hard stop for migration.
        return Reconciliation(
            capability_key=capability.key,
            verdict="UNKNOWN",
            severity=VERDICT_SEVERITY["UNKNOWN"],
            configured_state=stored_state,
            configured_exposure=0,
            observed_state=observed.deployment_state,
            observed_exposure=observed_exposure,
            observed_confidence=observed.confidence,
            rationale=(
                f"stored state {stored_state!r} is not in the legacy vocabulary; "
                "refusing to infer what was meant"
            ),
            inventory_contradicted=contradicted,
            signals=signals,
        )

    configured_state, configured_policy = translated
    configured_exposure = exposure_rank(configured_state, configured_policy)

    def finish(verdict: str, rationale: str) -> Reconciliation:
        return Reconciliation(
            capability_key=capability.key,
            verdict=verdict,
            severity=VERDICT_SEVERITY[verdict],
            configured_state=stored_state,
            configured_exposure=configured_exposure,
            observed_state=observed.deployment_state,
            observed_exposure=observed_exposure,
            observed_confidence=observed.confidence,
            rationale=rationale,
            inventory_contradicted=contradicted,
            signals=signals,
        )

    if observed.deployment_state == "UNKNOWN":
        return finish(
            "UNKNOWN",
            f"stored {stored_state!r}, but production evidence is insufficient: "
            + observed.rationale,
        )

    if not observed.reachable:
        # Production has nothing behind the row. Config controlling absent code.
        return finish(
            "DEAD_CONFIG",
            f"stored {stored_state!r}, but production is {observed.deployment_state}: "
            + observed.rationale,
        )

    if configured_exposure == observed_exposure:
        return finish(
            "MATCH",
            f"stored {stored_state!r} and production agree at exposure rank "
            f"{observed_exposure}",
        )

    if configured_exposure > observed_exposure:
        return finish(
            "OVERSTATED",
            f"stored {stored_state!r} claims exposure rank {configured_exposure}; "
            f"production admits only rank {observed_exposure} via "
            f"{capability.eligibility.key}. Wiring this row up would grant access "
            "production currently refuses.",
        )

    return finish(
        "UNDERSTATED",
        f"stored {stored_state!r} claims exposure rank {configured_exposure}; "
        f"production admits rank {observed_exposure} via {capability.eligibility.key}. "
        "Wiring this row up would withdraw a working feature.",
    )


def reconcile_all(
    observations: dict[str, tuple[Signal, ...]],
    stored: dict[str, Optional[str]],
) -> tuple[Reconciliation, ...]:
    """Reconcile the whole inventory. Read-only; writes nothing anywhere.

    Ordered by the inventory rather than by severity, so two runs against the
    same inputs produce byte-identical output and a diff of two reports is
    meaningful.
    """
    results = []
    for capability in CAPABILITIES:
        results.append(
            reconcile(
                capability,
                stored.get(capability.key),
                observations.get(capability.key, ()),
            )
        )
    return tuple(results)


def orphaned_config_keys(stored: dict[str, Optional[str]]) -> tuple[str, ...]:
    """Stored keys with no capability in the inventory.

    Kept separate from :func:`reconcile_all` because there is nothing to
    reconcile: an unrecognised key has no observations and no model row, and
    pretending otherwise would produce a verdict about a capability that this
    package has never described.
    """
    return tuple(sorted(k for k in stored if by_key(k) is None))
