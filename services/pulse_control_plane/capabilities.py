"""The fifteen capabilities, re-reconciled under the two-axis model.

This supersedes ``legacy.py`` from Mission 1. Re-deriving each row through
:class:`~services.pulse_control_plane.model.Capability` — whose constructor
refuses a conditional claim that names no authority — changed two verdicts and
corrected the stated evidence for three more. Both corrections are recorded
here rather than quietly fixed, because the *way* they were wrong is the
reusable lesson.

Correction 1 — ``creator_cockpit`` was not conditional
-------------------------------------------------------

Mission 1: ``LIVE_CONDITIONAL``, on the evidence that 19 of 41 users had a
growth workspace, with ``real_gate=None``.

That ratio is organic usage, not a gate. ``/pulse/growth`` calls
``require_account()`` and nothing further (``bot.py:12547``), and
``build_growth_state`` provisions a workspace for any user who arrives without
one (``services/pulsesoc_growth_engine.py:718-721``). The 22 users without a
row are the ones who never opened the page. There was no eligibility rule for
the row to be conditional *on*, and the row said so by leaving ``real_gate``
empty — but nothing checked, so a usage statistic silently became an access
claim. Now ``LIVE_GLOBAL`` / ``STANDARD``.

Correction 2 — ``admin_command`` was cited against the wrong gate
------------------------------------------------------------------

Mission 1 supported it with ``COMMAND_CENTER_ENABLED=true``. That variable is
real and is read (``services/command_center_client.py:63``), but it gates
presence and notification dispatch — not one admin route. The actual gate is
``require_admin_page``. The verdict was right; the reason was not, and a reason
that does not hold is how a correct row later gets "fixed" into a wrong one.

Correction 3 — the premium evidence pointed at an inert table
--------------------------------------------------------------

Mission 1 justified ``premium_advanced_tools`` with "all 7 rows of
``pulse_premium_feature_flags`` carry ``enabled=1``". That table has exactly one
reader in the entire repository: an admin page that renders it as HTML
(``bot.py:87181``). It gates nothing, so it cannot be evidence that anything is
deployed. The real evidence is ``build_premium_center``
(``services/pulsesoc_dashboard_centers.py:1222-1236``), which gates seven named
benefits on ``premium_identity_engine.has_active_premium(user)``.

On which authority decides premium
-----------------------------------

Production runs ``BUSINESS_OS_ENTITLEMENTS=canonical``, so
``services.business_os.entitlements.premium`` is authoritative: it resolves
``business_os_ent_grants``, falls back to the legacy tables when canonical is
silent, and lets an account hold beat any grant. Its own docstring names four
historical premium authorities that could disagree. This package records *which*
one decides and never becomes a fifth.
"""

from __future__ import annotations

from typing import Optional

from services.pulse_control_plane.model import (
    ADMIN_ONLY,
    INTERNAL_ONLY,
    OWNER_ONLY,
    PREMIUM_ENTITLEMENT,
    STANDARD,
    Capability,
    EligibilityPolicy,
)

#: What each capability was stored as in ``feature_flags`` before migration.
#: Kept beside the reconciled truth so the size of the gap stays visible; the
#: migration refuses to write a row whose stored value is not one of these.
SEEDED_STATES: dict[str, str] = {
    "pulse_posts": "enabled",
    "pulse_comments_reactions": "enabled",
    "pulse_messenger": "enabled",
    "pulse_spaces": "enabled",
    "pulse_groups": "enabled",
    "pulse_reels": "beta",
    "pulse_livestream": "beta",
    "marketplace_browse": "enabled",
    "merchant_applications": "enabled",
    "marketplace_checkout": "internal-only",
    "premium_identity": "enabled",
    "premium_advanced_tools": "beta",
    "ai_assistant": "beta",
    "creator_cockpit": "beta",
    "admin_command": "enabled",
}

#: What each legacy word *claimed*, expressed on the two axes.
#:
#: Read off ``feature_flag_engine.evaluate_flag`` rather than off the word, which
#: is the only honest way to do it. Two entries surprise people:
#:
#: * ``beta`` is **not** a restriction. ``evaluate_flag`` returns
#:   ``visible=True, usable=True`` for it, unconditionally, for every user. It is
#:   a label rendered as a chip. Seven of the fifteen rows are stored ``beta``,
#:   and every one of them means "fully public".
#: * ``enabled`` and ``beta`` are therefore *the same claim* — which is why
#:   ``admin_command`` being stored ``enabled`` and ``pulse_reels`` being stored
#:   ``beta`` express identical exposure, despite one of them sitting behind 199
#:   permission-checked routes.
#:
#: This mapping exists so the reconciler can compare a stored row against
#: measured reality on a single scale. It is the *only* place the legacy
#: vocabulary is given meaning; :mod:`~services.pulse_control_plane.parsing`
#: refuses these words at runtime by design.
LEGACY_EXPOSURE: dict[str, tuple[str, EligibilityPolicy]] = {
    "enabled": ("LIVE_GLOBAL", STANDARD),
    "beta": ("LIVE_GLOBAL", STANDARD),
    "disabled": ("DISABLED", STANDARD),
    "internal-only": ("LIVE_CONDITIONAL", INTERNAL_ONLY),
    "premium-only": ("LIVE_CONDITIONAL", PREMIUM_ENTITLEMENT),
    "owner-only": ("LIVE_CONDITIONAL", OWNER_ONLY),
}

#: Measured against production on 2026-09-23/24: Railway service ``CoinPilotX``
#: variables, a read-only Postgres session, unauthenticated route probes, and
#: the source tree at ``e6b61c2de``.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="pulse_posts",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence="2,465 rows in pulse_posts; /api/pulse/posts answers 405 to GET (live, wrong method).",
    ),
    Capability(
        key="pulse_comments_reactions",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence="40 rows in pulse_comments, 113 in pulse_reactions.",
    ),
    Capability(
        key="pulse_messenger",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "/api/pulse/communications/conversations answers 401 (live, auth-gated); "
            "7 rows in comm_v2_conversation_settings."
        ),
    ),
    Capability(
        key="pulse_spaces",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "/api/pulse/spaces/join answers 405 (live); 2 rows in pulse_space_members. "
            "There is no pulse_spaces table — membership is the only store — so a count "
            "against the obvious table name would have wrongly read as absent."
        ),
    ),
    Capability(
        key="pulse_groups",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence="2 rows in pulse_groups; /api/pulse/groups answers 401 (live, auth-gated).",
    ),
    Capability(
        key="pulse_reels",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "71 rows in pulse_reels; /api/pulse/reels/feed answers 401 (live); "
            "Reels is a shipped tab in the App Store build."
        ),
    ),
    Capability(
        key="pulse_livestream",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "284 rows in pulse_live_streams, 167 in pulse_live_reactions; "
            "/api/pulse/live-now answers 401 (live). The Agora->Mux path is production "
            "realtime infrastructure under the repository's hard lock, not a beta."
        ),
        runtime_authority="LIVESTREAM_AUDIO_V2_ENABLED=true (env)",
        # Not one of Stage 29's eight domains, added because the consequence is
        # identical: half a live audio rollout is a broadcast that fails for an
        # arbitrary half of its audience, and docs/realtime_audio_change_policy.md
        # already forbids incidental changes to this path.
        protected=("REALTIME_MEDIA",),
    ),
    Capability(
        key="marketplace_browse",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence="47 rows in marketplace_listings; /api/pulse/marketplace/commercial/terms answers 401.",
        runtime_authority="BUSINESS_OS_MARKETPLACE=true (env)",
    ),
    Capability(
        key="merchant_applications",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "5 rows in marketplace_merchant_applications. /pulse/merchant/apply "
            "(bot.py:60177) calls require_account() and nothing else — any authenticated "
            "user may apply, so the 5-of-41 ratio is submission volume, not a gate."
        ),
        runtime_authority="BUSINESS_OS_MERCHANT_AUTOMATION=true (env)",
    ),
    Capability(
        key="marketplace_checkout",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "32 rows in seller_transactions — real orders. "
            "MARKETPLACE_CARD_PAYMENTS_ENABLED=true and BUSINESS_OS_ORDERS=true in "
            "production. The seed says 'internal design only' and 'not exposed'; both "
            "are false, and evaluate_flag would have withdrawn it from every non-admin."
        ),
        runtime_authority="MARKETPLACE_CARD_PAYMENTS_ENABLED=true, BUSINESS_OS_ORDERS=true (env)",
        protected=("PAYMENT", "ORDER_INTEGRITY"),
    ),
    Capability(
        key="premium_identity",
        deployment_state="LIVE_CONDITIONAL",
        eligibility=PREMIUM_ENTITLEMENT,
        evidence=(
            "6 rows in pulse_premium_entitlements; 'premium_identity' is itself a granted "
            "entitlement key (bot.py:84914). /api/pulse/premium/identity-effects and "
            "/profile-theme resolve per user through the premium engines. Exposure is "
            "decided per subject, never globally — the seeded 'enabled' would have "
            "reported usable=True for a user holding no entitlement at all."
        ),
        runtime_authority="services.business_os.entitlements.premium (BUSINESS_OS_ENTITLEMENTS=canonical)",
    ),
    Capability(
        key="premium_advanced_tools",
        deployment_state="LIVE_CONDITIONAL",
        eligibility=PREMIUM_ENTITLEMENT,
        evidence=(
            "build_premium_center (services/pulsesoc_dashboard_centers.py:1222-1236) gates "
            "premium_analytics, premium_labs, premium_communities, premium_marketplace, "
            "advanced_search, ai_advisor and undx on has_active_premium(user), rendering "
            "each as Active or Locked. Mission 1 cited pulse_premium_feature_flags instead; "
            "that table's only reader is an admin HTML page (bot.py:87181) and it gates "
            "nothing."
        ),
        runtime_authority="services.business_os.entitlements.premium (BUSINESS_OS_ENTITLEMENTS=canonical)",
    ),
    Capability(
        key="ai_assistant",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "782 rows in pulse_ai_messages, 1,977 in pulse_ai_posts, 368 safety events, "
            "156 registered tools — the busiest subsystem on the platform."
        ),
        runtime_authority="UNDX_ROUTER_ENABLED=true, UNDX_AGENT_ENABLED=true (env)",
    ),
    Capability(
        key="creator_cockpit",
        deployment_state="LIVE_GLOBAL",
        eligibility=STANDARD,
        evidence=(
            "CORRECTED from Mission 1's LIVE_CONDITIONAL. /pulse/growth (bot.py:12547) "
            "calls require_account() and nothing else; build_growth_state auto-provisions "
            "a workspace for any user lacking one (pulsesoc_growth_engine.py:718-721). "
            "The 19-of-41 split is who opened the page, not who was admitted. No creator "
            "tier, follower threshold or approval status is consulted anywhere."
        ),
    ),
    Capability(
        key="admin_command",
        deployment_state="LIVE_CONDITIONAL",
        eligibility=ADMIN_ONLY,
        evidence=(
            "199 static /admin/ routes behind require_admin_page, which validates "
            "session['admin_user_id'] against admin_users (status must be active) and "
            "checks the named permission against the role tables; owner-level actions "
            "additionally require role in {owner, super_admin}. Deployment is thoroughly "
            "live and eligibility is near-nobody — the distinction this model exists for. "
            "Seeded 'enabled' is the one legacy state meaning unconditionally visible and "
            "usable, which is precisely inverted for the admin surface."
        ),
        runtime_authority="bot.require_admin_page / bot.admin_is_owner_level",
        protected=("AUTHORIZATION",),
    ),
)


def by_key(capability_key: str) -> Optional[Capability]:
    for capability in CAPABILITIES:
        if capability.key == capability_key:
            return capability
    return None


def unreconciled() -> tuple[Capability, ...]:
    """Capabilities whose production reality has not been established."""
    return tuple(c for c in CAPABILITIES if c.deployment_state == "UNKNOWN")


def protected_capabilities() -> tuple[Capability, ...]:
    return tuple(c for c in CAPABILITIES if c.protected)


def conditional_capabilities() -> tuple[Capability, ...]:
    """Capabilities whose exposure another authority decides per subject.

    Every one of these must name that authority — the model constructor will
    not build a conditional capability without one — so this doubles as the
    list of external authorities the control plane depends on and must never
    reimplement.
    """
    return tuple(c for c in CAPABILITIES if c.deployment_state == "LIVE_CONDITIONAL")
