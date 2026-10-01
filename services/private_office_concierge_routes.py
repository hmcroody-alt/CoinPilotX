"""HTTP surface for the Human Concierge — a staffed desk, or the truth.

Member surface (session auth + ``human_concierge`` gate + Office lock):

``GET  /api/private-office/concierge``
    Desk status (staffed or honestly not) plus the member's own requests.

``POST /api/private-office/concierge/requests``
    Submit a request. Accepted even when the desk is unstaffed — the row is
    stored and the response says no human has seen it — because staffing is
    an operational state that changes without a deploy, and a member's
    request should not evaporate because it arrived at 3am.

``GET  /api/private-office/concierge/requests/<id>``
    One request with its message thread.

``POST /api/private-office/concierge/requests/<id>/messages``
    The member speaks in their own thread.

``POST /api/private-office/concierge/requests/<id>/cancel``
    The member withdraws the request. CANCELED belongs to the member alone.

Operator surface (session auth + roster membership — *not* the member gate;
an operator is staff acting across the boundary, and every read and write is
audited with the operator as actor and the member as owner):

``GET  /api/private-office/concierge/desk``
    The open-request queue across members, oldest first.

``GET  /api/private-office/concierge/desk/<owner_id>/<id>``
    One member's request as the console sees it.

``POST /api/private-office/concierge/desk/<owner_id>/<id>``
    ``{"status"?, "note"?, "evidence_refs"?}`` — message, status move, or
    both. COMPLETED requires a note saying what was done.

Non-operators get 404 from every desk route: the console's existence is not
advertised to accounts that cannot use it.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services.private_office import audit as po_audit
from services.private_office import concierge as po_concierge

CONCIERGE_FEATURE_ID = "human_concierge"

LOGGER = logging.getLogger(__name__)

private_office_concierge_blueprint = Blueprint("private_office_concierge", __name__)

#: Truthful capability edges. Staffing is dynamic and rides separately as
#: ``desk`` on every payload — this block states what the code itself is.
PROVIDER_STATUS = {
    "fulfilment": "human_operators",
    "inference": "none",
    "automation": "none",
    "note": (
        "Concierge requests are fulfilled by human operators on a named "
        "roster. No reply is ever generated; when the desk is unstaffed, "
        "the payload's desk block says exactly that."
    ),
}


def _entry():
    """Auth + tier gate + second lock shared by every member route."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, CONCIERGE_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


def _operator_entry():
    """Auth + roster membership for the desk. 404s hide the console."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    if not po_concierge.is_operator(user["user_id"]):
        return None, po_http._no_store({"ok": False, "message": "Not found."}, 404)
    return user, None


# ---------------------------------------------------------------------------
# Member surface
# ---------------------------------------------------------------------------

@private_office_concierge_blueprint.route(
    "/api/private-office/concierge", methods=["GET"])
def api_private_office_concierge_home():
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        rows = po_concierge.list_requests(cur, owner_user_id=user["user_id"])
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_CONCIERGE_READ, object_type="REQUEST_LIST",
            purpose="user_request", result_count=len(rows),
        )
        return rows

    try:
        rows = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_HOME_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your concierge just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "desk": po_concierge.desk_status(),
        "requests": rows,
        "count": len(rows),
        "provider_status": PROVIDER_STATUS,
    })


@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/requests", methods=["POST"])
def api_private_office_concierge_submit():
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}

    def work(cur):
        return po_concierge.submit_request(
            cur, owner_user_id=user["user_id"],
            title=str(body.get("title") or ""),
            description=str(body.get("description") or ""),
            category=str(body.get("category") or "GENERAL"),
            priority=str(body.get("priority") or "NORMAL"),
            deadline_at=body.get("deadline_at"),
            actor_user_id=user["user_id"],
        )

    try:
        outcome = po_http._with_cursor(work)
    except po_concierge.PrivateConciergeRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_SUBMIT_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not file this request just now."}, 503)

    return po_http._no_store({
        "ok": True, **outcome, "provider_status": PROVIDER_STATUS}, 201)


@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/requests/<int:request_id>", methods=["GET"])
def api_private_office_concierge_detail(request_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_concierge.get_request(
            cur, owner_user_id=user["user_id"], request_id=request_id)
        if data is not None:
            po_audit.record(
                cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
                action=po_audit.ACTION_CONCIERGE_READ, object_type="REQUEST",
                object_id=request_id, purpose="user_request",
                result_count=len(data["thread"]),
            )
        return data

    try:
        data = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_DETAIL_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load this request just now."}, 503)

    if data is None:
        return po_http._no_store({"ok": False, "message": "Request not found."}, 404)
    return po_http._no_store({
        "ok": True, **data, "provider_status": PROVIDER_STATUS})


@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/requests/<int:request_id>/messages",
    methods=["POST"])
def api_private_office_concierge_member_message(request_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}

    def work(cur):
        return po_concierge.post_member_message(
            cur, owner_user_id=user["user_id"], request_id=request_id,
            body=str(body.get("body") or ""), actor_user_id=user["user_id"])

    try:
        message = po_http._with_cursor(work)
    except po_concierge.PrivateConciergeRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_MESSAGE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not send this message just now."}, 503)

    if message is None:
        return po_http._no_store({"ok": False, "message": "Request not found."}, 404)
    return po_http._no_store(
        {"ok": True, "message_sent": message, "desk": po_concierge.desk_status()},
        201)


@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/requests/<int:request_id>/cancel",
    methods=["POST"])
def api_private_office_concierge_cancel(request_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_concierge.cancel_request(
            cur, owner_user_id=user["user_id"], request_id=request_id,
            actor_user_id=user["user_id"])

    try:
        outcome = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_CANCEL_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not cancel this request just now."}, 503)

    if outcome is None:
        return po_http._no_store({"ok": False, "message": "Request not found."}, 404)
    return po_http._no_store({"ok": True, **outcome})


# ---------------------------------------------------------------------------
# Operator surface
# ---------------------------------------------------------------------------

@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/desk", methods=["GET"])
def api_private_office_concierge_desk():
    user, refusal = _operator_entry()
    if refusal:
        return refusal

    def work(cur):
        return po_concierge.desk_queue(cur, operator_user_id=user["user_id"])

    try:
        rows = po_http._with_cursor(work)
    except po_concierge.PrivateConciergeRejected:
        return po_http._no_store({"ok": False, "message": "Not found."}, 404)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_DESK_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load the desk just now."}, 503)

    return po_http._no_store({
        "ok": True, "queue": rows, "count": len(rows),
        "desk": po_concierge.desk_status(),
    })


@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/desk/<int:owner_id>/<int:request_id>",
    methods=["GET"])
def api_private_office_concierge_desk_detail(owner_id: int, request_id: int):
    user, refusal = _operator_entry()
    if refusal:
        return refusal

    def work(cur):
        return po_concierge.operator_get_request(
            cur, operator_user_id=user["user_id"], owner_user_id=owner_id,
            request_id=request_id)

    try:
        data = po_http._with_cursor(work)
    except po_concierge.PrivateConciergeRejected:
        return po_http._no_store({"ok": False, "message": "Not found."}, 404)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_DESK_DETAIL_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load this request just now."}, 503)

    if data is None:
        return po_http._no_store({"ok": False, "message": "Request not found."}, 404)
    return po_http._no_store({"ok": True, **data})


@private_office_concierge_blueprint.route(
    "/api/private-office/concierge/desk/<int:owner_id>/<int:request_id>",
    methods=["POST"])
def api_private_office_concierge_desk_update(owner_id: int, request_id: int):
    user, refusal = _operator_entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}

    def work(cur):
        return po_concierge.operator_update(
            cur, operator_user_id=user["user_id"], owner_user_id=owner_id,
            request_id=request_id,
            status=str(body.get("status") or ""),
            note=str(body.get("note") or ""),
            evidence_refs=body.get("evidence_refs") or (),
        )

    try:
        outcome = po_http._with_cursor(work)
    except po_concierge.PrivateConciergeRejected as exc:
        if "not an operator" in str(exc):
            return po_http._no_store({"ok": False, "message": "Not found."}, 404)
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONCIERGE_DESK_UPDATE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not update this request just now."}, 503)

    if outcome is None:
        return po_http._no_store({"ok": False, "message": "Request not found."}, 404)
    return po_http._no_store({"ok": True, **outcome})


def register(app) -> None:
    app.register_blueprint(private_office_concierge_blueprint)
