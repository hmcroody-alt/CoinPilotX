"""HTTP surface for Relationship Intelligence — the Private Office's people.

``GET  /api/private-office/relationships``
    The member's directory: every person they have recorded, newest first,
    with open-commitment and connection counts computed from the same rows
    the profile shows — a number a tap-through always substantiates.

``GET  /api/private-office/relationships?q=&sort=&favorites=``
    The same directory, filtered and ordered. Filtering runs after the
    owner-scoped read, so a query can never be a probe for someone else's
    contact.

``POST /api/private-office/relationships``
    ``{"name", "role"?, "phone"?, "email"?, "username"?, "photo_media_id"?,
    "notes"?, "source"?}`` — add a person, or recognise one the member
    already has. Identity is resolved on identifiers only (account id,
    username, email, phone) and **never on the name**; the response says
    ``status`` and ``matched_on`` so a client can tell the member which it
    was. A ``username`` is confirmed against a real account before anything
    is linked.

``PATCH /api/private-office/relationships/<id>``
    Edit the member's private record. Only fields the body mentions change;
    ``""`` clears one. A linked account's own name and photo are not
    writable here.

``DELETE /api/private-office/relationships/<id>``
    Remove from the directory. Archives the node and cascades into nothing —
    meetings, messages and the account itself are untouched.

``POST /api/private-office/relationships/<id>/favorite``
    Pin or unpin. Ordering only.

``GET  /api/private-office/relationships/lookup?username=``
    Whose account is that handle? The confirmation step before a link, so a
    member never silently links to the wrong person.

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
from services.private_office import accounts as po_accounts
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


# ---------------------------------------------------------------------------
# The platform side of identity
# ---------------------------------------------------------------------------
#
# Reads go through :mod:`services.private_office.accounts`, which is the one
# sanctioned window from the Office onto a PulseSoc account and explains in its
# own docstring why it is so narrow. The direction is one-way: a route reads an
# account to *confirm* a link the member asked for and hands the private
# substrate a canonical id. Nothing flows back — being recorded in someone's
# Private Office is not an event on the account, and the account is never told.
# See §20, §47 and §48 of the product rules.


def _accounts_for(cur, people: list[dict]) -> dict[int, dict]:
    """``pulsesoc_user_id`` → account row, for the linked people in a list."""
    return po_accounts.lookup_many(
        cur, [p.get("pulsesoc_user_id") for p in people])


def _decorate(person: dict, accounts: dict[int, dict]) -> dict:
    """Attach the linked account's public face, and say where the photo came from.

    ``photo_source`` is on the wire so the client never has to guess, and so
    the avatar it draws is the one this decision produced rather than whichever
    field happened to be non-empty. The order is §3's: the linked PulseSoc
    profile image, then a photo the member assigned, then initials — and
    initials are a real rendering, not the empty box that shows up when a
    component is handed a blank URL and left to cope.
    """
    account = accounts.get(int(person.get("pulsesoc_user_id") or 0)) or {}
    avatar = str(account.get("avatar_url") or "")
    custom = str(person.get("photo_media_id") or "")
    person = dict(person)
    person["linked_account"] = {
        "user_id": int(account.get("user_id") or 0),
        "username": str(account.get("username") or ""),
        "display_name": str(account.get("display_name") or ""),
        "avatar_url": avatar,
    } if account else None
    if avatar:
        person["photo_source"] = "pulsesoc_profile"
    elif custom:
        person["photo_source"] = "custom"
    else:
        person["photo_source"] = "initials"
    return person


def _link_target(cur, body: dict) -> tuple[int, str, dict | None]:
    """The account a save is asking to link to. Raises when it names nobody.

    §8: a handle that resolves to no account is refused rather than stored.
    Keeping it would leave a contact card showing an ``@name`` that looks like
    a link, taps like a link, and points at nothing — or worse, points at
    whoever registers that handle next.
    """
    handle = po_relationships.normalize_username(body.get("username"))
    claimed = 0
    try:
        claimed = int(body.get("pulsesoc_user_id") or 0)
    except (TypeError, ValueError):
        claimed = 0
    if not handle and claimed <= 0:
        return 0, "", None

    account = po_accounts.lookup(cur, user_id=claimed, username=handle)
    if account is None or not int(account.get("user_id") or 0):
        raise po_relationships.PrivateRelationshipRejected(
            "No PulseSoc member has that username.")
    # The *canonical* spelling is stored, not the one that was typed. A handle
    # the member wrote in a different case must not become a second identifier.
    return (int(account["user_id"]),
            po_relationships.normalize_username(account.get("username")),
            account)


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

    query = str(request.args.get("q") or "")
    sort = str(request.args.get("sort") or po_relationships.SORT_RECENT)
    favorites_only = str(request.args.get("favorites") or "").strip() in {"1", "true", "yes"}

    def work(cur):
        rows = po_relationships.directory(
            cur, owner_user_id=user["user_id"], query=query, sort=sort,
            favorites_only=favorites_only)
        accounts = _accounts_for(cur, rows)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_GRAPH_READ, object_type="PERSON_DIRECTORY",
            purpose="user_request", result_count=len(rows),
        )
        return [_decorate(row, accounts) for row in rows]

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
        "query": query,
        "sort": sort if sort in po_relationships.SORTS else po_relationships.SORT_RECENT,
        "provider_status": PROVIDER_STATUS,
    })


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/lookup", methods=["GET"])
def api_private_office_relationships_lookup():
    """Whose account is ``@handle``? The confirmation step before a link.

    Deliberately not a search: it answers about one exact handle the member
    already typed, and it answers the same way — 404 — for a handle nobody has
    and for one that exists but has no username set. It is behind the same
    session, tier and Office lock as everything else here, so it is not a
    username oracle for anyone who is not already inside a Private Office.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    handle = po_relationships.normalize_username(request.args.get("username"))
    if not handle:
        return po_http._no_store({"ok": False, "message": "Enter a username."}, 400)

    try:
        account = po_http._with_cursor(lambda cur: po_accounts.lookup(cur, username=handle))
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_LOOKUP_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not check that username just now."}, 503)

    if not account or not int(account.get("user_id") or 0):
        return po_http._no_store(
            {"ok": False, "message": "No PulseSoc member has that username."}, 404)
    return po_http._no_store({"ok": True, "account": {
        "user_id": int(account["user_id"]),
        "username": str(account.get("username") or ""),
        "display_name": str(account.get("display_name") or ""),
        "avatar_url": str(account.get("avatar_url") or ""),
    }})


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships", methods=["POST"])
def api_private_office_relationships_add():
    """Add a person, or recognise one the member already has.

    The response always carries ``status`` — ``created``, ``updated`` or
    ``unchanged`` — and ``matched_on`` when an identifier decided it. A client
    that posts the same contact twice gets one person back both times and can
    say so, which is the difference between an idempotent write and a duplicate
    the member finds later.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}
    return _save(user, body, node_id=None, created=True)


def _field(body: dict, key: str):
    """``None`` when the client did not mention the field at all.

    JSON has no third state, so absence is the signal. A client that means
    "clear this" sends ``""``; one that means "leave it" omits the key. Without
    the distinction, every partial save from a meeting invite would blank the
    details the member typed by hand.
    """
    return body[key] if key in body else None


def _save(user, body: dict, *, node_id, created: bool):
    def work(cur):
        # Resolved inside the write's own cursor: the account check and the
        # person write must see one database state, or a handle validated
        # against an account that vanished between two transactions would be
        # stored as a link to nothing.
        account_id, canonical_handle, _account = _link_target(cur, body)
        person = po_relationships.save_person(
            cur, owner_user_id=user["user_id"],
            name=_field(body, "name"),
            role=_field(body, "role"),
            phone=_field(body, "phone"),
            email=_field(body, "email"),
            username=(canonical_handle if account_id else _field(body, "username")),
            pulsesoc_user_id=account_id,
            photo_media_id=_field(body, "photo_media_id"),
            notes=_field(body, "notes"),
            source=body.get("source") or po_relationships.SOURCE_MANUAL,
            node_id=node_id,
            domain=(str(body.get("domain") or "").strip() or None),
            sensitivity=(str(body.get("sensitivity") or "").strip() or None),
            actor_user_id=user["user_id"],
        )
        return _decorate(person, _accounts_for(cur, [person]))

    try:
        person = po_http._with_cursor(work)
    except _REJECTIONS as exc:
        status = 404 if "not found" in str(exc) else 400
        return po_http._no_store({"ok": False, "message": str(exc)}, status)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_SAVE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not save this person just now."}, 503)

    # 201 only for a person who did not exist a moment ago. A repeat post that
    # resolved onto an existing contact is a 200: nothing was created, and
    # saying otherwise is how a client learns to expect two.
    code = 201 if (created and person.get("status") == po_relationships.SAVE_CREATED) else 200
    return po_http._no_store({
        "ok": True, "person": person,
        "status": person.get("status") or "",
        "matched_on": person.get("matched_on") or "",
    }, code)


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>", methods=["PATCH"])
def api_private_office_relationships_edit(node_id: int):
    """Edit one person. Only the fields the body mentions.

    What is edited here is the member's **private** record. A linked account's
    display name and profile photo are not writable from this screen and never
    will be — they belong to the person whose account it is, and a Private
    Office that could edit them would be writing into someone else's profile
    from inside a room they cannot see.
    """
    user, refusal = _entry()
    if refusal:
        return refusal
    return _save(user, request.get_json(silent=True) or {},
                 node_id=node_id, created=False)


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>", methods=["DELETE"])
def api_private_office_relationships_remove(node_id: int):
    """Remove a person from the directory. Archives; cascades into nothing.

    Their meetings, their messages and their PulseSoc account are untouched —
    this feature records who the member knows, and un-recording that is not a
    licence to delete the things they did together.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_relationships.remove_person(
            cur, owner_user_id=user["user_id"], node_id=node_id,
            actor_user_id=user["user_id"])

    try:
        removed = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_REMOVE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not remove this person just now."}, 503)

    if not removed:
        return po_http._no_store({"ok": False, "message": "Person not found."}, 404)
    return po_http._no_store({"ok": True, "removed": True})


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>/favorite", methods=["POST"])
def api_private_office_relationships_favorite(node_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}
    favorite = bool(body.get("favorite", True))

    def work(cur):
        person = po_relationships.set_favorite(
            cur, owner_user_id=user["user_id"], node_id=node_id,
            favorite=favorite, actor_user_id=user["user_id"])
        return _decorate(person, _accounts_for(cur, [person]))

    try:
        person = po_http._with_cursor(work)
    except _REJECTIONS as exc:
        status = 404 if "not found" in str(exc) else 400
        return po_http._no_store({"ok": False, "message": str(exc)}, status)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RELATIONSHIPS_FAVORITE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not update this person just now."}, 503)

    return po_http._no_store({"ok": True, "person": person})


@private_office_relationships_blueprint.route(
    "/api/private-office/relationships/<int:node_id>", methods=["GET"])
def api_private_office_relationships_profile(node_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_relationships.profile(
            cur, owner_user_id=user["user_id"], node_id=node_id)
        if data is None:
            return None
        return _decorate(data, _accounts_for(cur, [data]))

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
