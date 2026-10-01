"""HTTP surface for Private Briefings — the Office's own engine, member-triggered.

``POST /api/private-office/briefings``
    Generate a briefing now. Runs the deterministic composition over the
    member's own open records, pending document claims, people with open
    commitments and newest facts; persists the briefing with its items; wraps
    the run in a job record; audits the generation. Nothing is scheduled and
    nothing is pushed — a briefing exists because the member asked for one.

``GET  /api/private-office/briefings``
    Past briefings, newest first — "what did my Office tell me on Tuesday".

``GET  /api/private-office/briefings/<id>``
    One briefing with its items grouped into sections, every line carrying
    the evidence refs of the rows it quotes.

``GET  /api/private-office/briefings/why?refs=a,b,c``
    Ask Why: owner-checked resolution of evidence refs into the labelled rows
    behind them. A ref that no longer resolves says so instead of pretending.

``POST /api/private-office/briefings/<id>/actions``
    ``{"action_type": "obligation"|"request", "title": ..., "due_at"?: ...,
    "item_id"?: ...}`` — turn a briefing line into a record the member owns,
    written through the canonical record writer and citing the briefing (and
    the named item's evidence) it came from.

Every route runs the shared entry: session auth, the server-side feature gate
on ``private_briefings``, and the Office second lock.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services.private_office import audit as po_audit
from services.private_office import briefings as po_briefings

BRIEFINGS_FEATURE_ID = "private_briefings"

LOGGER = logging.getLogger(__name__)

private_office_briefings_blueprint = Blueprint(
    "private_office_briefings", __name__)

#: Truthful capability edges. This engine composes; it does not infer,
#: schedule, or push. There is no external provider in the path.
PROVIDER_STATUS = {
    "source": "private_office_records",
    "inference": "none",
    "delivery": "on_demand",
    "note": (
        "Briefings are composed on request from the member's own open "
        "records, pending document claims, people and recorded facts. "
        "Nothing is inferred, scheduled, or pushed."
    ),
}


def _entry():
    """Auth + tier gate + second lock shared by every briefings route."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, BRIEFINGS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


@private_office_briefings_blueprint.route(
    "/api/private-office/briefings", methods=["POST"])
def api_private_office_briefings_generate():
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_briefings.generate_briefing(
            cur, owner_user_id=user["user_id"], actor_user_id=user["user_id"])

    try:
        briefing = po_http._with_cursor(work)
    except po_briefings.PrivateBriefingRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_BRIEFINGS_GENERATE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not prepare your briefing just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "briefing": briefing,
        "provider_status": PROVIDER_STATUS,
    }, 201)


@private_office_briefings_blueprint.route(
    "/api/private-office/briefings", methods=["GET"])
def api_private_office_briefings_list():
    user, refusal = _entry()
    if refusal:
        return refusal

    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20

    def work(cur):
        rows = po_briefings.list_briefings(
            cur, owner_user_id=user["user_id"], limit=limit)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_BRIEFING_READ, object_type="BRIEFING_LIST",
            purpose="user_request", result_count=len(rows),
        )
        return rows

    try:
        rows = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_BRIEFINGS_LIST_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your briefings just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "briefings": rows,
        "count": len(rows),
        "provider_status": PROVIDER_STATUS,
    })


@private_office_briefings_blueprint.route(
    "/api/private-office/briefings/<int:briefing_id>", methods=["GET"])
def api_private_office_briefings_detail(briefing_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        briefing = po_briefings.get_briefing(
            cur, owner_user_id=user["user_id"], briefing_id=briefing_id)
        if briefing is not None:
            po_audit.record(
                cur, actor_user_id=user["user_id"],
                owner_user_id=user["user_id"],
                action=po_audit.ACTION_BRIEFING_READ, object_type="BRIEFING",
                object_id=str(briefing["id"]), purpose="user_request",
                result_count=len(briefing["items"]),
            )
        return briefing

    try:
        briefing = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_BRIEFINGS_DETAIL_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load this briefing just now."}, 503)

    if briefing is None:
        return po_http._no_store({"ok": False, "message": "Briefing not found."}, 404)
    return po_http._no_store({
        "ok": True,
        "briefing": briefing,
        "provider_status": PROVIDER_STATUS,
    })


@private_office_briefings_blueprint.route(
    "/api/private-office/briefings/why", methods=["GET"])
def api_private_office_briefings_why():
    user, refusal = _entry()
    if refusal:
        return refusal

    raw = str(request.args.get("refs") or "")
    refs = [piece.strip() for piece in raw.split(",") if piece.strip()]
    if not refs:
        return po_http._no_store(
            {"ok": False, "message": "Pass refs=a,b,c to resolve."}, 400)

    def work(cur):
        return po_briefings.explain(
            cur, owner_user_id=user["user_id"], refs=refs)

    try:
        resolved = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_BRIEFINGS_WHY_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not resolve that evidence just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "evidence": resolved,
        "count": len(resolved),
    })


@private_office_briefings_blueprint.route(
    "/api/private-office/briefings/<int:briefing_id>/actions", methods=["POST"])
def api_private_office_briefings_create_action(briefing_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}

    def work(cur):
        return po_briefings.create_action(
            cur, owner_user_id=user["user_id"], briefing_id=briefing_id,
            action_type=str(body.get("action_type") or ""),
            title=str(body.get("title") or ""),
            due_at=body.get("due_at"),
            item_id=int(body.get("item_id") or 0),
            actor_user_id=user["user_id"],
        )

    try:
        outcome = po_http._with_cursor(work)
    except po_briefings.PrivateBriefingRejected as exc:
        status = 404 if "not found" in str(exc) else 400
        return po_http._no_store({"ok": False, "message": str(exc)}, status)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_BRIEFINGS_ACTION_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not create that action just now."}, 503)

    return po_http._no_store({"ok": True, **outcome}, 201)


def register(app) -> None:
    app.register_blueprint(private_office_briefings_blueprint)
