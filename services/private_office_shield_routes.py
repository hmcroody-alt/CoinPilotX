"""HTTP surface for Private Shield — internal monitoring, honest edges.

``GET  /api/private-office/shield``
    Posture: open findings by severity, the last scan job, which internal
    checks exist — and the external coverage block stating, truthfully, that
    breach monitoring has no provider and nothing external has been checked.

``POST /api/private-office/shield/scan``
    Run the deterministic internal scan now. Job-wrapped, audited,
    deduplicating: an existing open finding is refreshed rather than
    duplicated, a dismissed one stays dismissed, and one whose condition is
    gone is resolved with a note that says exactly that.

``GET  /api/private-office/shield/findings``
    The findings list, newest first, optionally filtered by ``status``.

``POST /api/private-office/shield/findings/<id>``
    ``{"status": "ACKNOWLEDGED"|"RESOLVED"|"DISMISSED", "note"?: ...}`` — the
    member's decision about one finding. The only path that moves status.

Every route runs the shared entry: session auth, the server-side feature gate
on ``private_shield``, and the Office second lock.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services.private_office import audit as po_audit
from services.private_office import shield as po_shield

SHIELD_FEATURE_ID = "private_shield"

LOGGER = logging.getLogger(__name__)

private_office_shield_blueprint = Blueprint("private_office_shield", __name__)

#: Truthful capability edges, repeated on every payload that could otherwise
#: be read as a security assurance.
PROVIDER_STATUS = {
    "source": "private_office_records",
    "inference": "none",
    "external_monitoring": "none",
    "note": (
        "Shield findings come from deterministic checks over the member's own "
        "Private Office data. No breach, identity or dark-web provider is "
        "integrated; no external exposure has been checked."
    ),
}


def _entry():
    """Auth + tier gate + second lock shared by every shield route."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, SHIELD_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


@private_office_shield_blueprint.route(
    "/api/private-office/shield", methods=["GET"])
def api_private_office_shield_posture():
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_shield.posture(cur, owner_user_id=user["user_id"])
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_SHIELD_READ, object_type="SHIELD_POSTURE",
            purpose="user_request", result_count=data["open_findings"],
        )
        return data

    try:
        data = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_SHIELD_POSTURE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your Shield just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "posture": data,
        "provider_status": PROVIDER_STATUS,
    })


@private_office_shield_blueprint.route(
    "/api/private-office/shield/scan", methods=["POST"])
def api_private_office_shield_scan():
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_shield.run_scan(
            cur, owner_user_id=user["user_id"], actor_user_id=user["user_id"])

    try:
        report = po_http._with_cursor(work)
    except po_shield.PrivateShieldRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_SHIELD_SCAN_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not run this scan just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "scan": report,
        "provider_status": PROVIDER_STATUS,
    }, 201)


@private_office_shield_blueprint.route(
    "/api/private-office/shield/findings", methods=["GET"])
def api_private_office_shield_findings():
    user, refusal = _entry()
    if refusal:
        return refusal

    raw = str(request.args.get("status") or "")
    statuses = [piece.strip().upper() for piece in raw.split(",") if piece.strip()]

    def work(cur):
        rows = po_shield.list_findings(
            cur, owner_user_id=user["user_id"], statuses=statuses or None)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_SHIELD_READ, object_type="FINDING_LIST",
            purpose="user_request", result_count=len(rows),
        )
        return rows

    try:
        rows = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_SHIELD_FINDINGS_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your findings just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "findings": rows,
        "count": len(rows),
        "provider_status": PROVIDER_STATUS,
    })


@private_office_shield_blueprint.route(
    "/api/private-office/shield/findings/<int:finding_id>", methods=["POST"])
def api_private_office_shield_update_finding(finding_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}

    def work(cur):
        return po_shield.update_finding(
            cur, owner_user_id=user["user_id"], finding_id=finding_id,
            status=str(body.get("status") or ""),
            note=body.get("note") or "",
            actor_user_id=user["user_id"],
        )

    try:
        finding = po_http._with_cursor(work)
    except po_shield.PrivateShieldRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_SHIELD_UPDATE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not update this finding just now."}, 503)

    if finding is None:
        return po_http._no_store({"ok": False, "message": "Finding not found."}, 404)
    return po_http._no_store({"ok": True, "finding": finding})


def register(app) -> None:
    app.register_blueprint(private_office_shield_blueprint)
