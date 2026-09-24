"""The canonical capability model: two axes, because one was never enough.

Mission 1 tried to describe PulseSoc's capabilities with a single flat state and
hit the wall immediately. ``premium_identity`` is deployed, reachable, and
working — and also unavailable to most users. ``admin_command`` is deployed on
199 live routes and available to almost nobody. A vocabulary with one axis has
to either call those "live" (true, and dangerously misleading) or "restricted"
(also true, and equally misleading). Mission 1 wrote ``LIVE_CONDITIONAL`` for
both and then had nowhere to record *conditional on what*.

So this module splits the question in two:

``DeploymentState``
    Is the code deployed and reachable in production at all? A property of the
    *system*. Nothing about any particular user.

``EligibilityPolicy``
    Among subjects who can reach it, who may use it — and, crucially, **which
    existing authority decides**. A property of the *policy*, and deliberately
    not of this package.

The second axis names an authority; it never becomes one. That distinction is
the entire safety argument of this package and it is enforced mechanically
below, not by convention: a policy that requires an authority cannot produce a
usable verdict unless the caller supplies that authority's answer. There is no
code path in which this module can decide, by itself, that somebody is Premium.

The invariant that would have caught Mission 1's error
------------------------------------------------------

Mission 1 classified ``creator_cockpit`` as ``LIVE_CONDITIONAL`` on the evidence
that 19 of 41 users had a growth workspace. That ratio is organic usage, not a
gate: ``/pulse/growth`` calls ``require_account()`` and nothing else, and
``build_growth_state`` auto-provisions a workspace for any user who lacks one
(``services/pulsesoc_growth_engine.py:718``). There was never an eligibility
rule to be conditional *on*. The row named no gate, and nothing objected.

:class:`Capability` now refuses that combination: ``LIVE_CONDITIONAL`` requires
a policy that names a real authority, and ``LIVE_GLOBAL`` refuses one. A
conditional claim must say what the condition is, or it will not construct.
Re-running Mission 1's inventory through this constructor is what surfaced the
error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: Bumped whenever the meaning of a state or policy changes, so a stored
#: decision can be told apart from one this code would produce today. Carried on
#: every :class:`CapabilityDecision`.
MODEL_VERSION = "2.0"


# ---------------------------------------------------------------------------
# Axis 1 — deployment state
# ---------------------------------------------------------------------------

#: Is the capability deployed and reachable? Nothing here is about *who*.
#:
#: These are not interchangeable with the legacy ``feature_flags.state``
#: vocabulary (``enabled``/``beta``/``internal-only``/...). The legacy words
#: conflated both axes, which is why ``beta`` silently meant "everyone" and
#: ``enabled`` silently meant "everyone including on the admin surface".
#: Translation between the two vocabularies happens once, explicitly, in
#: :mod:`services.pulse_control_plane.migration` — never at runtime.
DEPLOYMENT_STATES = (
    "ABSENT",           # not deployed: no route, no client, nothing to reach
    "DISABLED",         # deployed, deliberately switched off by an authority
    "LIVE_CONDITIONAL", # deployed and reachable; an authority decides per subject
    "LIVE_GLOBAL",      # deployed and reachable by every subject the policy admits
    "EXPERIMENTAL",     # deployed, deliberately incomplete; exposure limited by intent
    "LEGACY",           # deployed but superseded; retained for compatibility
    "DEAD",             # configuration exists, deployed code does not
    "UNKNOWN",          # evidence insufficient or contradictory
)

#: States in which the capability is reachable at all. Everything else denies
#: before eligibility is even consulted — which is why an unresolvable state
#: cannot leak access through a permissive policy.
REACHABLE_STATES = frozenset({"LIVE_CONDITIONAL", "LIVE_GLOBAL", "EXPERIMENTAL", "LEGACY"})

#: States that deny. Kept explicit rather than derived as "not reachable" so
#: that adding a ninth state forces a decision here instead of defaulting into
#: one bucket or the other.
DENYING_STATES = frozenset({"ABSENT", "DISABLED", "DEAD", "UNKNOWN"})

assert REACHABLE_STATES | DENYING_STATES == set(DEPLOYMENT_STATES)
assert not (REACHABLE_STATES & DENYING_STATES)


# ---------------------------------------------------------------------------
# Axis 2 — eligibility policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityPolicy:
    """Who may use a reachable capability, and who decides.

    ``authority`` is a dotted reference to the code that actually answers the
    question in production. It is documentation with teeth: when
    ``requires_authority`` is true, :func:`evaluate` will not return
    ``usable=True`` unless the caller has supplied that authority's verdict.
    The control plane therefore cannot drift into *being* the entitlement
    system, which is the failure this whole design is arranged to prevent.

    ``denied_visibility`` distinguishes two genuinely different product
    behaviours that a single boolean would flatten. A non-Premium user should
    *see* a Premium feature — that is the upsell, and hiding it would be a
    product regression. A non-admin should not see the admin surface at all;
    showing it would be an information leak. Same denial, opposite correct
    rendering.
    """

    key: str
    description: str
    authority: Optional[str] = None
    requires_authority: bool = True
    denied_visibility: bool = False

    def __post_init__(self) -> None:
        if self.requires_authority and not self.authority:
            raise ValueError(
                f"eligibility policy {self.key!r} requires an authority but names none. "
                "A policy that restricts access must say what does the restricting."
            )


#: Any authenticated account. No further gate — and saying so is a claim about
#: production that the reconciler checks, not a default to fall back into.
STANDARD = EligibilityPolicy(
    key="STANDARD",
    description="Any authenticated account.",
    requires_authority=False,
)

#: No account needed. Public marketing and browse surfaces.
ANONYMOUS = EligibilityPolicy(
    key="ANONYMOUS",
    description="Reachable without an account.",
    requires_authority=False,
)

#: Premium membership. Production runs ``BUSINESS_OS_ENTITLEMENTS=canonical``,
#: so ``business_os.entitlements.premium`` is the live resolver; it reads the
#: legacy tables and the ``users`` identity columns and resolves
#: ``business_os_ent_grants`` as canonical, with an account hold beating any
#: grant. Denied subjects still see the feature: that is the upsell.
PREMIUM_ENTITLEMENT = EligibilityPolicy(
    key="PREMIUM_ENTITLEMENT",
    description="PulseSoc Premium membership, resolved by the canonical entitlement service.",
    authority="services.business_os.entitlements.premium.is_premium",
    denied_visibility=True,
)

#: Admin console. ``require_admin_page`` validates ``session['admin_user_id']``
#: against ``admin_users`` (status must be active) and checks the named
#: permission against the role tables. Denied subjects see nothing.
ADMIN_ONLY = EligibilityPolicy(
    key="ADMIN_ONLY",
    description="Authenticated admin with the required role permission.",
    authority="bot.require_admin_page",
)

#: Strictly above ADMIN_ONLY: role in {owner, super_admin}. Used for changes to
#: public exposure, including changes to this control plane itself.
OWNER_ONLY = EligibilityPolicy(
    key="OWNER_ONLY",
    description="Owner or super_admin role.",
    authority="bot.admin_is_owner_level",
)

#: Staff-only surfaces that are not the admin console.
INTERNAL_ONLY = EligibilityPolicy(
    key="INTERNAL_ONLY",
    description="Internal staff only; not exposed to any customer.",
    authority="bot.require_admin_page",
)

ELIGIBILITY_POLICIES: dict[str, EligibilityPolicy] = {
    p.key: p
    for p in (STANDARD, ANONYMOUS, PREMIUM_ENTITLEMENT, ADMIN_ONLY, OWNER_ONLY, INTERNAL_ONLY)
}

#: Policies that admit every subject. Used by the invariant below: a capability
#: cannot claim to be conditional while pointing at one of these.
UNGATED_POLICIES = frozenset({"STANDARD", "ANONYMOUS"})


# ---------------------------------------------------------------------------
# Protected domains (Stage 29)
# ---------------------------------------------------------------------------

#: Domains where capability configuration may hide or show a surface but must
#: never be able to rewrite the underlying truth. A flag may remove a checkout
#: button; it may not decide whether a payment succeeded, who owns an order, or
#: whether a session is authenticated.
#:
#: Membership has a mechanical consequence, not just a documentary one:
#: percentage rollout is refused outright for these capabilities
#: (:mod:`services.pulse_control_plane.rollout`), because a partial rollout of a
#: payment path means some customers can pay and some cannot, decided by a hash.
PROTECTED_DOMAINS = (
    "AUTHENTICATION",
    "AUTHORIZATION",
    "PAYMENT",
    "ORDER_INTEGRITY",
    "PAYOUT",
    "SELLER_OWNERSHIP",
    "PRIVACY",
    "FRAUD",
)


# ---------------------------------------------------------------------------
# A capability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    """One capability, on both axes, with the evidence that placed it there."""

    key: str
    deployment_state: str
    eligibility: EligibilityPolicy
    evidence: str
    #: Where exposure is really decided today — an environment variable, a
    #: guard, an entitlement service. ``None`` means nothing gates it beyond
    #: the eligibility policy, which is itself a claim the reconciler checks.
    runtime_authority: Optional[str] = None
    #: Protected domains this capability touches. Non-empty forbids rollout.
    protected: tuple[str, ...] = field(default_factory=tuple)
    #: 0-100. Only meaningful for reachable states; refused when ``protected``.
    rollout_percentage: int = 100
    #: How strongly production reality was established. Drives whether the
    #: migration is allowed to write this row.
    confidence: str = "MEASURED"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.deployment_state not in DEPLOYMENT_STATES:
            raise ValueError(f"unknown deployment state {self.deployment_state!r} for {self.key!r}")
        if self.confidence not in ("MEASURED", "INFERRED", "UNVERIFIED"):
            raise ValueError(f"unknown confidence {self.confidence!r} for {self.key!r}")
        if not 0 <= self.rollout_percentage <= 100:
            raise ValueError(f"rollout_percentage out of range for {self.key!r}")

        # The invariant that catches an unexplained conditional claim.
        if self.deployment_state == "LIVE_CONDITIONAL" and self.eligibility.key in UNGATED_POLICIES:
            raise ValueError(
                f"{self.key!r} is LIVE_CONDITIONAL but its eligibility policy "
                f"{self.eligibility.key!r} admits everyone. A conditional capability must "
                "name the authority it is conditional on; if there is no gate, the state is "
                "LIVE_GLOBAL. (Mission 1 recorded creator_cockpit this way on the strength of "
                "a usage ratio, and there was no gate.)"
            )
        # ...and its mirror, which catches the opposite lie.
        if self.deployment_state == "LIVE_GLOBAL" and self.eligibility.key not in UNGATED_POLICIES:
            raise ValueError(
                f"{self.key!r} claims LIVE_GLOBAL while its eligibility policy "
                f"{self.eligibility.key!r} restricts access. A capability behind an authority "
                "is LIVE_CONDITIONAL however widely deployed it is."
            )
        if self.protected and self.rollout_percentage != 100:
            raise ValueError(
                f"{self.key!r} touches protected domain(s) {self.protected} and cannot be "
                "partially rolled out. Deciding by hash which customers may pay is not a "
                "rollout, it is an outage for the remainder."
            )

    @property
    def reachable(self) -> bool:
        return self.deployment_state in REACHABLE_STATES


# ---------------------------------------------------------------------------
# Stage 3 — the decision contract
# ---------------------------------------------------------------------------

#: Every reason a decision can be reached. Explicit codes rather than prose so
#: a caller can branch on them and a test can assert on them.
REASON_CODES = (
    "AVAILABLE",
    "NOT_DEPLOYED",
    "DEPLOYMENT_DISABLED",
    "DEAD_CONFIG",
    "UNRESOLVABLE_STATE",
    "ELIGIBILITY_NOT_SUPPLIED",
    "ELIGIBILITY_DENIED",
    "ROLLOUT_EXCLUDED",
    "LEGACY_RETAINED",
)


@dataclass(frozen=True)
class CapabilityDecision:
    """The single result shape. No bare booleans without a reason beside them."""

    capability_key: str
    deployment_state: str
    visible: bool
    usable: bool
    eligibility_policy: str
    reason_code: str
    source: str
    model_version: str
    evaluated_at: str

    def __post_init__(self) -> None:
        if self.reason_code not in REASON_CODES:
            raise ValueError(f"unknown reason code {self.reason_code!r}")
        if self.usable and not self.visible:
            raise ValueError(
                f"{self.capability_key!r}: usable without visible is incoherent — a subject "
                "cannot use what is not shown to them."
            )


@dataclass(frozen=True)
class EligibilityVerdict:
    """An authority's answer about one subject, handed in from outside.

    This type exists so that "the authority said yes" and "nobody asked the
    authority" cannot be represented by the same value. A missing verdict is
    ``None`` and denies; a present verdict carries the name of whoever produced
    it, so a decision can be traced back to a real gate.
    """

    allowed: bool
    authority: str
    detail: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evaluate(
    capability: Capability,
    *,
    subject_id: Optional[str] = None,
    eligibility: Optional[EligibilityVerdict] = None,
    source: str = "control_plane",
) -> CapabilityDecision:
    """Decide visibility and usability for one subject. Fails closed throughout.

    The ``eligibility`` argument is how the separation in Stage 2 is enforced
    rather than merely documented. When the capability's policy requires an
    authority and no verdict is supplied, this returns
    ``ELIGIBILITY_NOT_SUPPLIED`` and denies. It does not guess, and it has no
    access to the entitlement tables with which it could guess.

    Note what is *not* here: no ``force``, no environment override, no "admin
    bypass" argument. Those are the affordances that get reached for during an
    incident and then stay.
    """

    def decide(visible: bool, usable: bool, reason: str) -> CapabilityDecision:
        return CapabilityDecision(
            capability_key=capability.key,
            deployment_state=capability.deployment_state,
            visible=visible,
            usable=usable,
            eligibility_policy=capability.eligibility.key,
            reason_code=reason,
            source=source,
            model_version=MODEL_VERSION,
            evaluated_at=_now(),
        )

    state = capability.deployment_state

    if state == "ABSENT":
        return decide(False, False, "NOT_DEPLOYED")
    if state == "DISABLED":
        return decide(False, False, "DEPLOYMENT_DISABLED")
    if state == "DEAD":
        return decide(False, False, "DEAD_CONFIG")
    if state == "UNKNOWN":
        # The whole reason this model exists. The legacy engine mapped anything
        # it did not recognise onto `beta` and granted full access; here an
        # unresolvable state denies, and says so.
        return decide(False, False, "UNRESOLVABLE_STATE")

    policy = capability.eligibility

    if policy.requires_authority:
        if eligibility is None:
            return decide(policy.denied_visibility, False, "ELIGIBILITY_NOT_SUPPLIED")
        if not eligibility.allowed:
            return decide(policy.denied_visibility, False, "ELIGIBILITY_DENIED")

    # Rollout is applied last, and only to subjects the policy already admits,
    # so narrowing a rollout can never widen an entitlement.
    if capability.rollout_percentage < 100:
        from services.pulse_control_plane.rollout import in_rollout

        if not in_rollout(capability.key, subject_id, capability.rollout_percentage):
            return decide(False, False, "ROLLOUT_EXCLUDED")

    if state == "LEGACY":
        return decide(True, True, "LEGACY_RETAINED")
    return decide(True, True, "AVAILABLE")
