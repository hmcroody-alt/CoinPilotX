"""HTTP surface for the canonical Private Office entitlement truth.

Two endpoints, both GET, both read-only.

``GET /api/private-office/entitlement``
    The caller's own resolved tier and feature availability. This is the
    endpoint that makes client-side tier inference unnecessary, and therefore
    the endpoint that makes it removable. A client that reads ``plan`` or
    ``subscription_status`` and decides for itself is not just duplicating
    logic — it is a *second authority* that will eventually disagree with this
    one, and the user will be the one who finds out.

    It answers for ``request``'s authenticated user and takes no user
    parameter, so it cannot be used to read anyone else's tier.

``GET /api/admin/private-office/status``
    The Stage 5 operational verification surface, behind ``system.view``.
    Subsystem health, provider availability, resolver self-check and counts by
    tier — no secrets, no user rows, no way to name a user.

There is deliberately no POST here. Granting a tier is an entitlement
operation and belongs to the existing admin entitlement paths; adding a write
to this pack would create a second granting authority, which is precisely the
drift the ownership contract forbids.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from services.private_office import status as po_status
from services.private_office import tiers as po_tiers

LOGGER = logging.getLogger(__name__)

private_office_blueprint = Blueprint("private_office", __name__)


def _bot():
    import bot

    return bot


def _current_user():
    try:
        return _bot().api_account_user()
    except Exception:  # noqa: BLE001 — an auth lookup failure is "not signed in"
        return None


def _no_store(payload, status=200):
    """Entitlement answers must never sit in an HTTP cache.

    A cached tier is a stale tier: a cancellation or a suspension that a proxy
    keeps serving for five minutes is access the business already revoked.
    """
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _context_from(user) -> dict:
    """Reuse the account row the auth layer already loaded.

    Only forwarded when the hold-relevant field is actually present; passing a
    dict with a missing ``account_status`` would make the resolver treat
    "unknown" as "supplied", and the hold check would silently stop checking.
    """
    if not isinstance(user, dict):
        return {}
    if "account_status" not in user:
        return {}
    return {
        "account_status": user.get("account_status"),
        "access_enabled": user.get("access_enabled"),
    }


@private_office_blueprint.route("/api/private-office/entitlement", methods=["GET"])
def api_private_office_entitlement():
    """The one canonical tier answer for the signed-in caller."""
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    context = _context_from(user)
    resolved = po_tiers.resolve_tier(
        user["user_id"], context=context or None
    )

    # ``ok`` reflects whether the ANSWER is trustworthy, not whether the HTTP
    # call succeeded. A degraded resolve returns 200 with ok=False so the client
    # renders "temporarily unavailable" rather than either an error screen or,
    # far worse, a confident "you are on Free".
    trustworthy = resolved.get("resolver_state") == po_tiers.RESOLVER_OK
    return _no_store({"ok": trustworthy, **resolved})


@private_office_blueprint.route("/api/admin/private-office/status", methods=["GET"])
def api_private_office_status():
    """Stage 5 operational verification. Admin-gated; still not public."""
    bot = _bot()
    admin, denied = bot.require_admin_api("system.view")
    if denied:
        return denied
    try:
        payload = po_status.subsystem_status()
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_STATUS_FAILED")
        return _no_store(
            {"ok": False, "error": exc.__class__.__name__}, 500
        )
    return _no_store({"ok": True, **payload})


def register(app) -> None:
    app.register_blueprint(private_office_blueprint)
