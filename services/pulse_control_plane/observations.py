"""The production measurement, dated and citable.

Separate from :mod:`~services.pulse_control_plane.capabilities` on purpose.
That module holds *conclusions*, which are stable; this one holds *measurements*,
which go stale the moment production changes. Keeping them in one file would
make it impossible to tell whether a diff changed what we believe or what we
observed, and a reviewer needs to be able to distinguish those.

It is also what makes the reconciler testable. Because
:func:`~services.pulse_control_plane.reconciler.reconcile` takes signals as an
argument rather than reading them from here, the Stage 28 mutation proofs can
feed it a synthetic capability whose only support is four ``OPERATOR_CLAIM``
signals and assert that it still comes out ``UNKNOWN``.

Method
------

Measured 2026-09-23/24 against the live ``CoinPilotX`` Railway service and its
Postgres, at source tree ``e6b61c2de``:

* **Route probes** — unauthenticated ``GET`` from outside. 200/401/403/405, and
  302 to login, all prove the route is mounted. Paths were read out of ``bot.py``'s
  route table, never guessed; see the ``ROUTE_MISSING`` note in the reconciler for
  why that distinction is load-bearing.
* **Row counts** — a read-only Postgres session (``conn.set_session(readonly=True)``).
* **Environment** — ``railway variables --service CoinPilotX --kv``, 295 variables.
* **Source guards** — read at the cited line in the deployed tree.

``/admin/capability-matrix`` was deliberately **not** probed. A ``GET`` there
inserts one ``capability_audit_results`` row per feature, and that table having
zero rows is itself the evidence that the page has never been opened — so
probing it would have consumed the proof. It was still zero at the close of this
measurement.

What is *not* claimed here
--------------------------

No signal asserts a capability is gated. Signals establish reachability only; the
eligibility axis comes from the reviewed guard in
:mod:`~services.pulse_control_plane.capabilities`. The two are kept apart so that
an over-eager probe cannot promote a capability's exposure, and a mis-stated
policy cannot manufacture deployment evidence.
"""

from __future__ import annotations

from services.pulse_control_plane.reconciler import Signal

#: When these measurements were taken. Any report built from them should print
#: it, because "the config disagrees with production" means nothing without
#: saying when production was looked at.
MEASURED_AT = "2026-09-24"

#: The production service and commit the measurement describes.
MEASURED_AGAINST = "Railway service CoinPilotX @ e6b61c2de"

_PROBE = "unauthenticated GET, 2026-09-24"
_ENV = "railway variables --service CoinPilotX --kv"
_DB = "read-only Postgres session"

PRODUCTION_SIGNALS: dict[str, tuple[Signal, ...]] = {
    "pulse_posts": (
        Signal("ROUTE_WRONG_METHOD", _PROBE, "GET /api/pulse/posts -> 405"),
        Signal("DATA_PRESENT", _DB, "pulse_posts: 2,465 rows"),
    ),
    "pulse_comments_reactions": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/posts/1/comments -> 401"),
        Signal("DATA_PRESENT", _DB, "pulse_comments: 40 rows"),
        Signal("DATA_PRESENT", _DB, "pulse_reactions: 113 rows"),
    ),
    "pulse_messenger": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/communications/conversations -> 401"),
        Signal("DATA_PRESENT", _DB, "comm_v2_conversation_settings: 7 rows"),
    ),
    "pulse_spaces": (
        Signal("ROUTE_WRONG_METHOD", _PROBE, "GET /api/pulse/spaces/join -> 405"),
        # There is no pulse_spaces table; membership is the only store. A count
        # against the obvious table name would have read as DATA_EMPTY and
        # weakened a capability that is plainly live.
        Signal("DATA_PRESENT", _DB, "pulse_space_members: 2 rows"),
    ),
    "pulse_groups": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/groups -> 401"),
        Signal("DATA_PRESENT", _DB, "pulse_groups: 2 rows"),
    ),
    "pulse_reels": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/reels/feed -> 401"),
        Signal("DATA_PRESENT", _DB, "pulse_reels: 71 rows"),
        Signal("CLIENT_SHIPPED", "mobile-native, App Store build", "Reels is a shipped tab"),
    ),
    "pulse_livestream": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/live-now -> 401"),
        Signal("DATA_PRESENT", _DB, "pulse_live_streams: 284 rows"),
        Signal("DATA_PRESENT", _DB, "pulse_live_reactions: 167 rows"),
        Signal("ENV_GATE_ON", _ENV, "LIVESTREAM_AUDIO_V2_ENABLED=true"),
    ),
    "marketplace_browse": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/marketplace/commercial/terms -> 401"),
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/marketplace/search -> 401"),
        Signal("DATA_PRESENT", _DB, "marketplace_listings: 47 rows"),
        Signal("ENV_GATE_ON", _ENV, "BUSINESS_OS_MARKETPLACE=true"),
    ),
    "merchant_applications": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /pulse/merchant/apply -> 302 to login"),
        Signal("SOURCE_GUARD", "bot.py:60177", "require_account() and nothing further"),
        Signal("DATA_PRESENT", _DB, "marketplace_merchant_applications: 5 rows"),
        Signal("ENV_GATE_ON", _ENV, "BUSINESS_OS_MERCHANT_AUTOMATION=true"),
    ),
    "marketplace_checkout": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/business-os/orders -> 401"),
        Signal("ROUTE_WRONG_METHOD", _PROBE, "GET /api/payments/checkout/premium/monthly -> 405"),
        # The decisive signal. Rows here are completed orders: this code did not
        # merely deploy, it took money. The seeded row says "internal design
        # only" and "not exposed".
        Signal("DATA_PRESENT", _DB, "seller_transactions: 32 rows (real orders)"),
        Signal("ENV_GATE_ON", _ENV, "MARKETPLACE_CARD_PAYMENTS_ENABLED=true"),
        Signal("ENV_GATE_ON", _ENV, "BUSINESS_OS_ORDERS=true"),
    ),
    "premium_identity": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/premium/identity-effects -> 401"),
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/pulse/premium/profile-theme -> 401"),
        Signal("DATA_PRESENT", _DB, "pulse_premium_entitlements: 6 rows"),
        Signal("SOURCE_GUARD", "bot.py:84914", "'premium_identity' is a granted entitlement key"),
        Signal("ENV_GATE_ON", _ENV, "BUSINESS_OS_ENTITLEMENTS=canonical"),
    ),
    "premium_advanced_tools": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /dashboard/economy/premium -> 302 to login"),
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/premium/status-center -> 401"),
        Signal("ROUTE_WRONG_METHOD", _PROBE, "GET /api/dashboard/premium/explore -> 405"),
        Signal(
            "SOURCE_GUARD",
            "services/pulsesoc_dashboard_centers.py:1222-1236",
            "build_premium_center gates 7 named benefits on has_active_premium(user)",
        ),
        Signal("ENV_GATE_ON", _ENV, "BUSINESS_OS_ENTITLEMENTS=canonical"),
        # Mission 1's evidence — 7 enabled rows in pulse_premium_feature_flags —
        # is absent, not downgraded. That table's only reader renders it as HTML
        # (bot.py:87181). It is configuration, and configuration is not evidence
        # that the thing it claims to configure exists.
    ),
    "ai_assistant": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /api/dashboard/ai/state -> 401"),
        Signal("DATA_PRESENT", _DB, "pulse_ai_messages: 782 rows"),
        Signal("DATA_PRESENT", _DB, "pulse_ai_posts: 1,977 rows"),
        Signal("ENV_GATE_ON", _ENV, "UNDX_ROUTER_ENABLED=true"),
        Signal("ENV_GATE_ON", _ENV, "UNDX_AGENT_ENABLED=true"),
    ),
    "creator_cockpit": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /pulse/growth -> 302 to login"),
        Signal(
            "SOURCE_GUARD",
            "bot.py:12547",
            "require_account() only; no tier, threshold or approval is consulted",
        ),
        Signal(
            "SOURCE_GUARD",
            "services/pulsesoc_growth_engine.py:718-721",
            "build_growth_state auto-provisions a workspace for any user lacking one",
        ),
        # The 19-of-41 workspace ratio Mission 1 cited is not here. Given the
        # auto-provisioning above it measures who opened the page, and a usage
        # statistic is not a signal about deployment in either direction.
    ),
    "admin_command": (
        Signal("ROUTE_AUTH_GATED", _PROBE, "GET /admin/system -> 302 to login"),
        Signal(
            "SOURCE_GUARD",
            "bot.py:19556",
            "require_admin_page guards 199 static /admin/ routes",
        ),
        Signal(
            "SOURCE_GUARD",
            "bot.py:16111",
            "admin_is_owner_level additionally required for owner-level actions",
        ),
    ),
}

#: ``feature_flags.state`` as production stores it today, read in the same
#: read-only session. Identical to
#: :data:`~services.pulse_control_plane.capabilities.SEEDED_STATES` because every
#: row still carries its seeded value — all 15 share one ``updated_at`` and no
#: operator has ever changed one. Kept as a separate name anyway: the day they
#: diverge, the reconciler must compare against what is *stored*, not against
#: what was *seeded*, and a single dict would quietly conflate them.
STORED_STATES: dict[str, str] = {
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
