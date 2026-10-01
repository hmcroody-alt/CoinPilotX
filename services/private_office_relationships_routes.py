"""HTTP surface for Relationship Intelligence — the Private Office's people.

``GET  /api/private-office/relationships``
    The member's directory: every person they have recorded, newest first,
    with open-commitment and connection counts computed from the same rows
    the profile shows — a number a tap-through always substantiates.

``POST /api/private-office/relationships``
    ``{"name": ..., "role"?: ..., "domain"?: ..., "sensitivity"?: ...}`` —
    one new person. The node goes through the canonical graph writer, the
    name and role through the canonical fact writer with ``USER_ASSERTED``
    provenance. Every call creates a new person; merging is the member's
    decision, never the server's.

``GET  /api/private-office/relationships/<id>``
    Everything held about one person: identity, facts, connections,
    commitments, and a merged timeline where every line carries the evidence
    ref of the row behind it.

``POST /api/private-office/relationships/<id>/facts``
    ``{"fact_type": ..., "value": ..., "value_type"?: ...}`` — a
    member-asserted fact about their own person, into the fact store.

``GET  /api/private-office/relationships/<id>/briefing``
    The deterministic "before you meet them" aggregation. Persists nothing,
    asserts nothing, cites everything.

Every route runs the shared entry: session auth, the server-side feature gate
on ``relationship_intelligence``, and the Office second lock. Gate helpers are
imported from the canonical entitlement pack rather than copied.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services.private_office import audit as po_audit
from services.private_office import facts as po_facts
from services.private_office import graph as po_graph
from services.private_office import model as po_model
from services.private_office import relationships as po_relationships

#: What a person write can be refused with. All three are ``ValueError``
#: subclasses raised deliberately with a member-safe message.
_REJECTIONS = (
    po_relationships.PrivateRelationshipRejected,
    po_facts.PrivateFactRejected,
    po_graph.PrivateGraphRejected,
)

RELATIONSHIPS_FEATURE_ID = "relationship_intelligence"

LOGGER = logging.getLogger(__name__)

private_office_relationships_blueprint = Blueprint(
    "private_office_relationships", __name__)

#: Truthful capability edges. There is no inference layer and no external
#: provider anywhere in this feature — every line a screen renders traces to a
#: row the member (or a reviewed extraction) put there.
PROVIDER_STATUS = {
    "source": "private_office_records",
    "inference": "none",
    "note": (
        "Profiles, timelines and briefings are composed from the member's own "
        "recorded facts, connections and records. Nothing is inferred or "
        "fetched from outside the Private Office."
    ),
}


def _entry():
    """Auth + tier gate + second lock shared by every relationships route."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, RELATIONSHIPS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships", methods=["GET"])
def api_private_office_relationships_directory():
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        rows = po_relationships.directory(cur, owner_user_id=user["user_id"])
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_GRAPH_READ, object_type="PERSON_DIRECTORY",
            purpose="user_request", result_count=len(rows),
        )
        return rows

    try:
        rows = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_DIRECTORY_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your people just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "people": rows,
        "count": len(rows),
        "provider_status": PROVIDER_STATUS,
    })


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships", methods=["POST"])
def api_private_office_relationships_add():
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}

    def work(cur):
        return po_relationships.add_person(
            cur, owner_user_id=user["user_id"],
            name=str(body.get("name") or ""),
            role=str(body.get("role") or ""),
            domain=(str(body.get("domain") or "").strip() or None),
            sensitivity=(str(body.get("sensitivity") or "").strip() or None),
            actor_user_id=user["user_id"],
        )

    try:
        person = po_http._with_cursor(work)
    except _REJECTIONS as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_ADD_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not save this person just now."}, 503)

    return po_http._no_store({"ok": True, "person": person}, 201)


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>", methods=["GET"])
def api_private_office_relationships_profile(node_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_relationships.profile(
            cur, owner_user_id=user["user_id"], node_id=node_id)

    try:
        data = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_PROFILE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load this person just now."}, 503)

    if data is None:
        return po_http._no_store({"ok": False, "message": "Person not found."}, 404)
    return po_http._no_store({
        "ok": True,
        "person": data,
        "provider_status": PROVIDER_STATUS,
    })


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>/facts", methods=["POST"])
def api_private_office_relationships_record_fact(node_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}
    value_type = str(body.get("value_type") or po_model.VALUE_STRING)

    def work(cur):
        return po_relationships.record_person_fact(
            cur, owner_user_id=user["user_id"], node_id=node_id,
            fact_type=str(body.get("fact_type") or ""),
            value=body.get("value"), value_type=value_type,
            actor_user_id=user["user_id"],
        )

    try:
        outcome = po_http._with_cursor(work)
    except _REJECTIONS as exc:
        status = 404 if "not found" in str(exc) else 400
        return po_http._no_store({"ok": False, "message": str(exc)}, status)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_FACT_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not record that just now."}, 503)

    return po_http._no_store({"ok": True, **outcome}, 201)


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>/briefing", methods=["GET"])
def api_private_office_relationships_briefing(node_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_relationships.prepare_briefing(
            cur, owner_user_id=user["user_id"], node_id=node_id)

    try:
        data = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_BRIEFING_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not prepare this briefing just now."}, 503)

    if data is None:
        return po_http._no_store({"ok": False, "message": "Person not found."}, 404)
    return po_http._no_store({
        "ok": True,
        "briefing": data,
        "provider_status": PROVIDER_STATUS,
    })


def register(app) -> None:
    app.register_blueprint(private_office_relationships_blueprint)
