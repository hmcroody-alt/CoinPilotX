"""Stage 22 — evaluate the new model beside the legacy engine, and act on neither.

What this answers
-----------------

One question, and it is the question the cutover turns on: **if
``feature_flags`` were wired into production feature access tomorrow, whose
access would change, in which direction, and is each change intended?**

Everything else in this package argues that the new model is *more correct*.
That is not sufficient. A more correct model still changes behaviour when it is
switched on, and "more correct" is not a defence to an unreviewed access grant.
So before activation the difference is enumerated cell by cell rather than
summarised.

Why this is exhaustive rather than live
---------------------------------------

Stage 22 asks for evaluation alongside the legacy engine on the request path.
There is no request path. ``feature_flags`` gates nothing in production today —
that is Mission 1's central finding — so there is no live decision to shadow,
and manufacturing one would mean wiring the control plane into request handling,
which is precisely what this mission forbids until it passes.

You cannot shadow a control plane that controls nothing.

The replacement is stronger, not weaker. The capability set is closed at fifteen
and the subject classes that either engine can distinguish are closed at five —
neither engine can see anything about a subject beyond authentication, premium,
admin and owner. That makes the whole behaviour space seventy-five cells, and
seventy-five cells can simply be enumerated. Live shadowing would sample
whichever cells production traffic happened to exercise during the observation
window and would report nothing about the rest; a capability nobody used that
week would look like agreement. Here every cell is evaluated, deterministically,
with no production risk and no waiting.

Why it cannot act
-----------------

:class:`ShadowComparison` deliberately has no ``visible`` or ``usable`` field.
There is no attribute on the result that answers "so what do I do" — a caller
must reach into ``.legacy`` or ``.candidate`` and thereby name which engine it
is trusting. The intermediate shape that would make this module usable as a
decision path does not exist, which is a stronger guarantee than a comment
asking people not to use it as one.

The enforcement above that is mechanical: nothing in ``services/`` or ``bot.py``
imports this module, and a test asserts it. Shadow evaluation that something on
the request path can reach is not shadow evaluation.

What the enumeration found
--------------------------

Eleven narrowings and three widenings, and the shape is the point:

* every narrowing is the withdrawal of access the legacy row granted by
  accident — ``premium_identity`` and ``premium_advanced_tools`` are stored as
  words meaning "everyone", and ``admin_command`` is stored as the one word
  meaning "unconditionally visible and usable" across 199 permission-checked
  routes;
* **the only widening in the entire matrix is ``marketplace_checkout``** — the
  capability that has already taken thirty-two real orders while stored
  ``internal-only``.

A cutover policy of "refuse every widening" is the obvious safe-sounding rule
and it would be wrong here: it would preserve the single row that is lying about
money. The direction of a change is not its justification. What matters is
whether the new value matches measured production, which is why every comparison
below carries the reconciler's verdict for that capability beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.feature_flag_engine import evaluate_flag
from services.pulse_control_plane.capabilities import CAPABILITIES
from services.pulse_control_plane.model import (
    Capability,
    CapabilityDecision,
    EligibilityPolicy,
    EligibilityVerdict,
    evaluate,
)

# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """One class of user, described in the only terms either engine can see.

    The legacy engine reads exactly three keys off the user dict —
    ``is_admin``, ``is_owner``, ``is_premium`` — and nothing else. It cannot
    distinguish two members, or a new account from an old one. That is what
    bounds the matrix: a subject axis with more classes than this would be
    describing distinctions neither engine is capable of making, and the extra
    rows would all be duplicates.
    """

    key: str
    description: str
    authenticated: bool = True
    is_premium: bool = False
    is_admin: bool = False
    is_owner: bool = False

    def __post_init__(self) -> None:
        if self.is_owner and not self.is_admin:
            raise ValueError(
                f"{self.key!r}: an owner who is not an admin is not a subject class that "
                "exists. admin_is_owner_level (bot.py:16111) is reached through "
                "require_admin_page, so owner is strictly narrower than admin."
            )
        if not self.authenticated and (self.is_premium or self.is_admin):
            raise ValueError(f"{self.key!r}: an unauthenticated subject holds no entitlement")

    def legacy_user(self) -> dict:
        """The dict shape ``evaluate_flag`` expects.

        Note what is absent: the legacy row's ``premium_required``,
        ``owner_only``, ``internal_only`` and ``rollout_percentage`` columns
        play no part, because ``evaluate_flag`` never reads them. Four stored
        columns that look like access controls and are consulted by nothing.
        """
        return {
            "is_admin": self.is_admin,
            "is_owner": self.is_owner,
            "is_premium": self.is_premium,
        }


#: The five classes, ordered from least to most privileged. ``anonymous`` is
#: included even though neither engine gates on authentication, precisely
#: because neither engine gates on it: the cell is worth seeing rather than
#: assuming.
SUBJECTS: tuple[Subject, ...] = (
    Subject("anonymous", "No account.", authenticated=False),
    Subject("member", "Authenticated, no entitlement."),
    Subject("premium", "Authenticated, holds Premium.", is_premium=True),
    Subject("admin", "Admin with role permissions, not Premium.", is_admin=True),
    Subject("owner", "Owner or super_admin, not Premium.", is_admin=True, is_owner=True),
)


# ---------------------------------------------------------------------------
# Feeding both engines the same facts
# ---------------------------------------------------------------------------

#: Which of the subject's facts each policy's authority would return. This is
#: the fairness condition of the whole comparison: the new model must be asked
#: about the *same* subject the legacy engine was asked about. If this mapping
#: were more generous than ``evaluate_flag``'s own branches, the new model would
#: look better by being fed better answers, and every number below would be
#: worthless.
_POLICY_FACT: dict[str, str] = {
    "PREMIUM_ENTITLEMENT": "is_premium",
    "ADMIN_ONLY": "is_admin",
    "INTERNAL_ONLY": "is_admin",
    "OWNER_ONLY": "is_owner",
}


def verdict_for(policy: EligibilityPolicy, subject: Subject) -> Optional[EligibilityVerdict]:
    """Stand in for the authority the policy names, using only the subject's facts.

    Returns ``None`` for a policy that needs no authority, which is what
    :func:`~services.pulse_control_plane.model.evaluate` expects — not a
    permissive verdict. The distinction matters: a ``None`` here means "nobody
    was asked because nobody needed to be", and a fabricated ``allowed=True``
    would mean "this module decided", which it must never do even in a
    simulation.

    One legacy behaviour is deliberately **not** reproduced. ``evaluate_flag``
    treats an owner as premium (``is_premium or is_owner``) for ``premium-only``
    rows, an implicit superuser rule with no authority behind it. The new model
    has no such rule, and the canonical entitlement service does not grant
    premium by role. Today no row is stored ``premium-only``, so the difference
    is invisible in this matrix — but it would surface the moment one was, and
    recording it here is cheaper than rediscovering it during a cutover.
    """
    if not policy.requires_authority:
        return None
    fact = _POLICY_FACT.get(policy.key)
    if fact is None:
        # A policy this module does not know how to simulate. Denying is the
        # only safe answer, and saying so beats silently omitting the cell.
        return EligibilityVerdict(
            allowed=False,
            authority=policy.authority or "unknown",
            detail=f"shadow has no simulation for policy {policy.key!r}",
        )
    return EligibilityVerdict(
        allowed=bool(getattr(subject, fact)),
        authority=policy.authority or "unknown",
        detail=f"subject.{fact}",
    )


# ---------------------------------------------------------------------------
# Exposure levels
# ---------------------------------------------------------------------------

#: The three outcomes either engine can produce, ordered. ``usable`` implies
#: ``visible`` on both sides, so these three exhaust the space and a single
#: ordinal is enough to say which engine is more permissive. There is no case
#: where one axis widens while the other narrows.
EXPOSURE_LEVELS = ("HIDDEN", "VISIBLE_ONLY", "USABLE")


def _level(visible: bool, usable: bool) -> str:
    if usable:
        if not visible:  # pragma: no cover - both engines refuse to produce this
            raise ValueError("usable without visible")
        return "USABLE"
    return "VISIBLE_ONLY" if visible else "HIDDEN"


#: AGREE is not a synonym for "safe" and WIDER is not a synonym for "wrong".
#: See the module docstring: the only widening here is the row that has been
#: understating a live payment path since May.
DIVERGENCE_CLASSES = ("AGREE", "CANDIDATE_WIDER", "CANDIDATE_NARROWER")


@dataclass(frozen=True)
class ShadowComparison:
    """One capability × one subject, under both engines.

    Note the fields that do not exist: there is no ``visible``, no ``usable``,
    no ``decision``. A caller cannot read an answer off this object without
    first choosing ``legacy`` or ``candidate``, which is the point.
    """

    capability_key: str
    subject_key: str
    #: The raw legacy word in ``feature_flags.state`` that drove the left side.
    stored_state: Optional[str]
    legacy_level: str
    candidate_level: str
    divergence: str
    #: The reconciler's verdict for this capability, carried so that a reviewer
    #: reading a divergence can see immediately whether the new side matches
    #: measured production or merely differs from the old side.
    reconciliation_verdict: str
    legacy_reason: str
    candidate_reason_code: str

    def __post_init__(self) -> None:
        for name in ("legacy_level", "candidate_level"):
            if getattr(self, name) not in EXPOSURE_LEVELS:
                raise ValueError(f"unknown exposure level for {name}")
        if self.divergence not in DIVERGENCE_CLASSES:
            raise ValueError(f"unknown divergence class {self.divergence!r}")
        expected = _classify(self.legacy_level, self.candidate_level)
        if self.divergence != expected:
            raise ValueError(
                f"{self.capability_key}/{self.subject_key}: divergence {self.divergence!r} "
                f"does not follow from {self.legacy_level!r} -> {self.candidate_level!r}"
            )

    @property
    def changes_behaviour(self) -> bool:
        return self.divergence != "AGREE"


def _classify(legacy_level: str, candidate_level: str) -> str:
    left = EXPOSURE_LEVELS.index(legacy_level)
    right = EXPOSURE_LEVELS.index(candidate_level)
    if right == left:
        return "AGREE"
    return "CANDIDATE_WIDER" if right > left else "CANDIDATE_NARROWER"


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def compare(
    capability: Capability,
    stored_state: Optional[str],
    subject: Subject,
    *,
    reconciliation_verdict: str = "UNKNOWN",
) -> ShadowComparison:
    """Run both engines over one cell and classify the difference.

    The legacy side is fed ``{"state": stored_state}`` and nothing else, which
    is not a simplification — it is everything ``evaluate_flag`` reads.
    """
    legacy = evaluate_flag({"state": stored_state}, subject.legacy_user())
    candidate: CapabilityDecision = evaluate(
        capability,
        subject_id=subject.key,
        eligibility=verdict_for(capability.eligibility, subject),
        source="shadow",
    )

    legacy_level = _level(bool(legacy["visible"]), bool(legacy["usable"]))
    candidate_level = _level(candidate.visible, candidate.usable)

    return ShadowComparison(
        capability_key=capability.key,
        subject_key=subject.key,
        stored_state=stored_state,
        legacy_level=legacy_level,
        candidate_level=candidate_level,
        divergence=_classify(legacy_level, candidate_level),
        reconciliation_verdict=reconciliation_verdict,
        legacy_reason=str(legacy.get("reason", "")),
        candidate_reason_code=candidate.reason_code,
    )


def compare_all(
    *,
    capabilities: tuple[Capability, ...] = CAPABILITIES,
    stored: Optional[dict[str, Optional[str]]] = None,
    subjects: tuple[Subject, ...] = SUBJECTS,
    verdicts: Optional[dict[str, str]] = None,
) -> tuple[ShadowComparison, ...]:
    """Every capability against every subject. No sampling, no ordering surprises.

    ``stored`` and ``verdicts`` are arguments rather than imports so that the
    non-vacuity proofs can drive this with a synthetic production state. A
    module that could only ever describe today's tree could not be shown to
    notice a change.
    """
    if stored is None:
        from services.pulse_control_plane.observations import STORED_STATES

        stored = dict(STORED_STATES)
    if verdicts is None:
        verdicts = _todays_verdicts()

    return tuple(
        compare(
            capability,
            stored.get(capability.key),
            subject,
            reconciliation_verdict=verdicts.get(capability.key, "UNKNOWN"),
        )
        for capability in capabilities
        for subject in subjects
    )


def _todays_verdicts() -> dict[str, str]:
    """Reconcile once so each comparison can carry its capability's verdict."""
    from services.pulse_control_plane.observations import (
        PRODUCTION_SIGNALS,
        STORED_STATES,
    )
    from services.pulse_control_plane.reconciler import reconcile_all

    return {
        r.capability_key: r.verdict
        for r in reconcile_all(PRODUCTION_SIGNALS, STORED_STATES)
    }


def widenings(comparisons: tuple[ShadowComparison, ...]) -> tuple[ShadowComparison, ...]:
    """Cells where activation would grant access the legacy engine withheld.

    Each of these is an access grant, and an access grant needs a reviewer even
    when — especially when — it is the correct repair.
    """
    return tuple(c for c in comparisons if c.divergence == "CANDIDATE_WIDER")


def narrowings(comparisons: tuple[ShadowComparison, ...]) -> tuple[ShadowComparison, ...]:
    """Cells where activation would withdraw access the legacy engine granted.

    Not automatically safe. Withdrawing something users have been using is an
    outage, and these are only correct here because the legacy grants were
    accidents of a vocabulary in which ``beta`` meant "everyone".
    """
    return tuple(c for c in comparisons if c.divergence == "CANDIDATE_NARROWER")


def authentication_gated_cells(
    comparisons: tuple[ShadowComparison, ...],
) -> tuple[str, ...]:
    """Capabilities where the new model treats anonymous differently from member.

    Expected to be **empty**, and the emptiness is the useful result.

    The report shows ``marketplace_checkout`` moving ``HIDDEN -> USABLE`` for an
    anonymous visitor, which reads alarmingly until you notice the same is true
    of ``member``: the control plane is not answering "is this person logged in"
    at all. It has no session, no request, and no way to find out. What denies
    an anonymous visitor at ``/api/business-os/orders`` is ``require_account()``
    on the route, which probed 401 and is not going anywhere.

    So this function exists to make the absence checkable rather than assumed.
    If it ever returns a key, some capability has started deriving access from
    authentication inside this package — and the failure mode of believing that
    is someone deleting a ``require_account()`` on the grounds that "the
    capability model handles it", which would open the route to everyone.

    The legacy engine has exactly the same blind spot, which is why every
    anonymous cell below agrees with its member cell. Activation neither creates
    this property nor fixes it.
    """
    by_cell = {(c.capability_key, c.subject_key): c.candidate_level for c in comparisons}
    keys = sorted({c.capability_key for c in comparisons})
    return tuple(
        key
        for key in keys
        if ("anonymous" in {c.subject_key for c in comparisons})
        and by_cell.get((key, "anonymous")) != by_cell.get((key, "member"))
    )


def unexplained(comparisons: tuple[ShadowComparison, ...]) -> tuple[ShadowComparison, ...]:
    """Behaviour changes whose capability reconciled as ``MATCH``.

    This is the combination that should not exist and is worth failing over: if
    the stored row already agreed with measured production, then switching
    engines must not move anybody. A cell here means the new model differs from
    both the old engine *and* the production it claims to describe — a bug in
    this package, not drift in the configuration.
    """
    return tuple(
        c
        for c in comparisons
        if c.changes_behaviour and c.reconciliation_verdict == "MATCH"
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def report(comparisons: Optional[tuple[ShadowComparison, ...]] = None) -> str:
    """The cutover diff, byte-stable, ordered, and listing only what moves.

    Agreeing cells are counted rather than printed. Sixty-one lines saying
    "nothing changes" would bury the fourteen that matter, and a report people
    skim is a report that stops being read before the interesting part.
    """
    if comparisons is None:
        comparisons = compare_all()

    changed = tuple(c for c in comparisons if c.changes_behaviour)
    agreed = len(comparisons) - len(changed)

    lines = [
        "SHADOW EVALUATION — legacy feature_flag_engine vs. the two-axis model",
        "",
        f"{len(comparisons)} cells ({len(set(c.capability_key for c in comparisons))} "
        f"capabilities x {len(set(c.subject_key for c in comparisons))} subject classes)",
        f"  agree ................ {agreed}",
        f"  candidate narrower ... {len(narrowings(comparisons))}",
        f"  candidate wider ...... {len(widenings(comparisons))}",
        "",
        "Neither column is production. feature_flags gates nothing today, so the",
        "left column is what the legacy engine WOULD say if it were consulted, and",
        "the right is what the new model WOULD say. The verdict column is the only",
        "one measured against production, and it is how to tell which side is right.",
        "",
    ]

    unauthenticated = authentication_gated_cells(comparisons)
    if unauthenticated:
        lines.append(
            "WARNING: these capabilities now resolve differently for an anonymous "
            "subject than for a member, which means this package has begun deciding "
            "authentication: " + ", ".join(unauthenticated)
        )
    else:
        lines.append(
            "Every anonymous cell matches its member cell, under both engines. Neither "
            "gates on authentication and neither can — that is require_account() on the "
            "route, and an 'anonymous -> USABLE' row below means 'the control plane does "
            "not deny this', never 'the route does not deny this'."
        )
    lines.append("")

    if not changed:
        lines.append("No cell changes. Activation would be a no-op.")
        return "\n".join(lines)

    lines.append("CELLS THAT MOVE")
    current = None
    for c in changed:
        if c.capability_key != current:
            current = c.capability_key
            lines.append("")
            lines.append(f"  {c.capability_key}  [stored {c.stored_state!r}, reconciled {c.reconciliation_verdict}]")
        arrow = "WIDER " if c.divergence == "CANDIDATE_WIDER" else "narrow"
        lines.append(
            f"    {arrow}  {c.subject_key:<10} {c.legacy_level:>12} -> {c.candidate_level:<12} "
            f"({c.candidate_reason_code})"
        )

    problems = unexplained(comparisons)
    if problems:
        lines.append("")
        lines.append("UNEXPLAINED — capability reconciled MATCH but behaviour still moves:")
        lines.extend(f"    {c.capability_key}/{c.subject_key}" for c in problems)

    return "\n".join(lines)
