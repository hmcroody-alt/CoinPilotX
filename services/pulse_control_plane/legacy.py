"""The fifteen ``feature_flags`` rows, reconciled against production.

Every verdict below was measured against production on 2026-09-23, not inferred
from the seed and not copied from an earlier document. Two independent proofs
establish that the seeded values describe nothing:

1. All fifteen rows carry the identical ``updated_at`` of
   ``2026-05-22T11:51:57``. No row has ever been edited.
2. ``capability_audit_results`` holds **zero** rows, while every ``GET`` of
   ``/admin/capability-matrix`` inserts one row per feature and nothing in the
   codebase ever deletes from that table. The page has therefore never been
   opened in production.

The second proof is the stronger one, and it is also the reason this module was
written from database counts and route probes rather than by loading the admin
page: opening it to look would have written the fifteen rows that prove it had
never been opened.

What the reconciliation found
-----------------------------

The matrix is wrong in **both** directions, which is why "just enable
everything" is not the fix either:

* **six** rows understate reality — ``marketplace_checkout`` is seeded
  ``internal-only`` while production has taken 32 orders; ``pulse_reels``,
  ``pulse_livestream``, ``ai_assistant``, ``premium_advanced_tools`` and
  ``creator_cockpit`` are seeded ``beta`` while all five are shipped and in use;
* **two** rows overstate it — ``premium_identity`` and ``admin_command`` are
  seeded ``enabled``, which ``evaluate_flag`` reads as unconditionally visible
  and usable, when both are in fact gated (by entitlement and by admin role
  respectively). Their real gates live elsewhere and are not represented here at
  all;
* seven rows happen to be right.

``marketplace_checkout`` is the row that makes this urgent rather than untidy.
``evaluate_flag`` maps ``internal-only`` to ``{"visible": False, "usable":
False}`` for any user without ``is_admin``. Wiring the engine to the seeded
table would withdraw checkout from every customer on the platform, and the
change would look like a configuration cleanup in review.

What happened to these rows
---------------------------

Nothing, and that is deliberate. Mission 2 built the replacement in
:mod:`services.pulse_control_plane.model` and migrated the reconciled truth
into *new* columns, leaving every ``state`` value below exactly as recorded
here. The intuitive tidying — writing ``deprecated`` into the column to mark it
dead — would have been the worst move available: ``normalize_state`` maps every
word it does not recognise to ``beta``, and ``beta`` is the most permissive
state the legacy engine has. So that edit would have *widened* all fifteen
rows. There is no value meaning "this no longer decides anything".

The column therefore still holds its original words, and what makes that safe
is only that ``evaluate_flag`` has no call sites. This module is not history:
it describes fifteen values sitting in production right now, one act of wiring
away from mattering again, which is why
:func:`services.pulse_control_plane.activation.readiness` re-counts those call
sites on every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: Timestamp shared by all fifteen production rows. Pinned by a test: if a row
#: ever diverges from this, somebody has edited the matrix and the
#: never-been-touched argument above has to be re-established rather than
#: assumed.
SEED_TIMESTAMP = "2026-05-22T11:51:57"


@dataclass(frozen=True)
class LegacyRow:
    """One ``feature_flags`` row and what production says about it."""

    feature_key: str
    seeded_state: str
    verdict: str
    evidence: str
    #: The gate that really decides this feature's exposure today, if any.
    #: ``None`` means the feature is ungated in production.
    real_gate: Optional[str] = None
    #: True when applying ``evaluate_flag`` to the seeded state would change
    #: what a user can see or do.
    regression_if_wired: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reconciled(self) -> bool:
        """Whether reality has been established for this row.

        ``UNKNOWN`` is the only unreconciled verdict. A row may disagree wildly
        with its seed and still be reconciled — disagreement that is measured
        and written down is exactly what this module is for.
        """
        return self.verdict != "UNKNOWN"


LEGACY_ROWS: tuple[LegacyRow, ...] = (
    LegacyRow(
        feature_key="pulse_posts",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence="2,465 rows in pulse_posts; /api/pulse/posts answers 405 to GET (live, wrong method).",
    ),
    LegacyRow(
        feature_key="pulse_comments_reactions",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence="40 rows in pulse_comments, 113 in pulse_reactions.",
    ),
    LegacyRow(
        feature_key="pulse_messenger",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence=(
            "/api/pulse/communications/conversations answers 401 (live, auth-gated); "
            "7 rows in comm_v2_conversation_settings."
        ),
    ),
    LegacyRow(
        feature_key="pulse_spaces",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence=(
            "/api/pulse/spaces/join answers 405 (live); 2 rows in pulse_space_members. "
            "There is no pulse_spaces table — membership is the only store — so a "
            "count against the obvious table name would have wrongly read as absent."
        ),
    ),
    LegacyRow(
        feature_key="pulse_groups",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence="2 rows in pulse_groups; /api/pulse/groups answers 401 (live, auth-gated).",
    ),
    LegacyRow(
        feature_key="pulse_reels",
        seeded_state="beta",
        verdict="LIVE_GLOBAL",
        evidence=(
            "71 rows in pulse_reels; /api/pulse/reels/feed answers 401 (live); "
            "Reels is a shipped tab in the App Store build."
        ),
        tags=("seed_understates",),
    ),
    LegacyRow(
        feature_key="pulse_livestream",
        seeded_state="beta",
        verdict="LIVE_GLOBAL",
        evidence=(
            "284 rows in pulse_live_streams, 167 in pulse_live_reactions; "
            "/api/pulse/live-now answers 401 (live). The Agora->Mux path is production "
            "realtime infrastructure under a HARD LOCK, not a beta."
        ),
        real_gate="LIVESTREAM_AUDIO_V2_ENABLED=true (env)",
        tags=("seed_understates", "protected"),
    ),
    LegacyRow(
        feature_key="marketplace_browse",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence="47 rows in marketplace_listings; /api/pulse/marketplace/commercial/terms answers 401.",
        real_gate="BUSINESS_OS_MARKETPLACE=true (env)",
    ),
    LegacyRow(
        feature_key="merchant_applications",
        seeded_state="enabled",
        verdict="LIVE_GLOBAL",
        evidence="5 rows in marketplace_merchant_applications.",
        real_gate="BUSINESS_OS_MERCHANT_AUTOMATION=true (env)",
    ),
    LegacyRow(
        feature_key="marketplace_checkout",
        seeded_state="internal-only",
        verdict="LIVE_GLOBAL",
        evidence=(
            "32 rows in seller_transactions — real orders. "
            "MARKETPLACE_CARD_PAYMENTS_ENABLED=true and BUSINESS_OS_ORDERS=true in "
            "production. The seed says 'internal design only' and 'not exposed'; "
            "both are false."
        ),
        real_gate="MARKETPLACE_CARD_PAYMENTS_ENABLED=true, BUSINESS_OS_ORDERS=true (env)",
        regression_if_wired=True,
        tags=("seed_understates", "payment", "critical"),
    ),
    LegacyRow(
        feature_key="premium_identity",
        seeded_state="enabled",
        verdict="LIVE_CONDITIONAL",
        evidence=(
            "6 rows in pulse_premium_entitlements; /api/pulse/premium/status-center "
            "answers 401. Exposure is decided per user by entitlement, not globally, "
            "so the seeded 'enabled' overstates it: evaluate_flag would report "
            "usable=True for a user with no entitlement."
        ),
        real_gate="pulse_premium_entitlements + premium_visibility_engine.is_premium_user",
        tags=("seed_overstates", "entitlement"),
    ),
    LegacyRow(
        feature_key="premium_advanced_tools",
        seeded_state="beta",
        verdict="LIVE_CONDITIONAL",
        evidence=(
            "All 7 rows of pulse_premium_feature_flags carry enabled=1, including "
            "premium_analytics_enabled and premium_studio_enabled. Entitlement-gated "
            "rather than beta."
        ),
        real_gate="pulse_premium_entitlements",
        tags=("seed_understates", "entitlement"),
    ),
    LegacyRow(
        feature_key="ai_assistant",
        seeded_state="beta",
        verdict="LIVE_GLOBAL",
        evidence=(
            "782 rows in pulse_ai_messages, 1,977 in pulse_ai_posts, 368 safety events, "
            "156 registered tools. This is the busiest subsystem on the platform."
        ),
        real_gate="UNDX_ROUTER_ENABLED=true, UNDX_AGENT_ENABLED=true (env)",
        tags=("seed_understates",),
    ),
    LegacyRow(
        feature_key="creator_cockpit",
        seeded_state="beta",
        verdict="LIVE_CONDITIONAL",
        evidence=(
            "19 rows each in pulse_creator_growth_profiles, pulse_growth_workspaces "
            "and pulse_growth_ai_sessions — 19 of 41 users have one."
        ),
        tags=("seed_understates",),
    ),
    LegacyRow(
        feature_key="admin_command",
        seeded_state="enabled",
        verdict="INTERNAL_ONLY",
        evidence=(
            "199 static /admin/ routes, all behind require_admin_page. "
            "COMMAND_CENTER_ENABLED=true in production. Seeded 'enabled' is the one "
            "state evaluate_flag treats as unconditionally visible and usable, which "
            "is precisely wrong for the admin surface."
        ),
        real_gate="require_admin_page / admin_is_owner_level",
        regression_if_wired=False,
        tags=("seed_overstates", "protected"),
    ),
)


def by_key(feature_key: str) -> Optional[LegacyRow]:
    for row in LEGACY_ROWS:
        if row.feature_key == feature_key:
            return row
    return None


def unreconciled() -> tuple[LegacyRow, ...]:
    """Rows whose production reality has not been established.

    The activation gate in :mod:`services.pulse_control_plane.reconcile` refuses
    while this is non-empty.
    """
    return tuple(row for row in LEGACY_ROWS if not row.reconciled)


def regressions_if_wired() -> tuple[LegacyRow, ...]:
    """Rows that would change user-visible behaviour if the engine were wired.

    Kept separate from :func:`unreconciled` because they are a different kind of
    blocker: these rows *are* understood, and that understanding is exactly what
    says not to proceed.
    """
    return tuple(row for row in LEGACY_ROWS if row.regression_if_wired)
