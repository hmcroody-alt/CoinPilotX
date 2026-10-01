"""HTTP surface for Private Conversations.

``GET    /api/private-office/conversations``
    The member's Private Office threads, newest activity first, each carrying
    its Office classification and its cross-domain links.

``POST   /api/private-office/conversations``
    Create a thread at a given Office scope. The canonical conversation is
    created by the messaging foundation; this route only classifies it.

``GET    /api/private-office/conversations/<ref>``
    One thread: the canonical control-center payload (media, links, pins,
    export) plus the Office classification.

``GET    /api/private-office/conversations/<ref>/messages``
``POST   /api/private-office/conversations/<ref>/messages``
``POST   /api/private-office/conversations/<ref>/read``
``POST   /api/private-office/conversations/<ref>/typing``
    Straight delegations to ``pulse_communications_v2``. These exist so the
    Office client can stay on one base path and one auth story; they add a gate
    and nothing else. Not one of them writes a message row.

``POST   /api/private-office/conversations/<ref>/sensitivity``
``POST   /api/private-office/conversations/<ref>/links``
``DELETE /api/private-office/conversations/<ref>/links``
    The three operations that are genuinely this package's own.

``GET    /api/private-office/conversations/links/<link_type>/<target_id>``
    The reverse of the two link routes: which of the member's threads reference
    a given document, record, fact or meeting. Filtered by conversation
    membership, never by the link table alone — see ``list_for_target``.

``GET    /api/private-office/conversations/capabilities``
    What this subsystem can and cannot do, including the flat statement that
    nothing here is end-to-end encrypted.

Why the message routes are thin on purpose
------------------------------------------
Every one of them resolves the thread through
:func:`conversations.require_member_view` — which asks the *canonical* service
whether this member may see the conversation — and then hands the request
straight to the canonical service. There is no second membership check, no
second block check, no second unread counter. A gate here plus a second
implementation underneath would be two answers to one question, and the day
they disagree is the day a removed participant keeps receiving messages.

Errors
------
A refusal from the policy layer arrives as ``PrivateConversationRejected`` and
carries its own status and code. A failure of anything else is 503 with
``state: "unavailable"`` — never an empty list. "No conversations yet" rendered
over a failed fetch is the specific lie this shape exists to prevent.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from pulse_communications_v2 import service as comm_service
from services import private_office_routes as po_http
from services.private_office import conversations as po_conversations

CONVERSATIONS_FEATURE_ID = po_conversations.FEATURE_ID

LOGGER = logging.getLogger(__name__)

private_office_conversations_blueprint = Blueprint(
    "private_office_conversations", __name__
)

BASE = "/api/private-office/conversations"


def _entry():
    """Auth + tier gate + second lock, shared by every route here.

    Copied in shape from the documents pack and importing the same helpers, so
    there is one implementation of the refusal translation across the whole
    Private Office surface.

    Note what this gate protects: the *Office view* of a thread. A participant
    with no Private Office entitlement is refused here and still reaches the
    same conversation through ordinary Messenger, which is the intended
    behaviour — the classification labels a thread, it does not imprison it.
    """
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, CONVERSATIONS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


def _rejected(exc: po_conversations.PrivateConversationRejected):
    return po_http._no_store(
        {"ok": False, "code": exc.code, "message": str(exc)}, exc.status
    )


def _unavailable(message: str):
    return po_http._no_store(
        {"ok": False, "state": "unavailable", "message": message}, 503
    )


def _envelope(result: dict, *, fallback: str):
    """Return a canonical service envelope as-is, or translate its failure.

    The canonical service already speaks ``{"ok": ...}``, so a successful
    delegation is passed through unchanged rather than re-shaped. Re-shaping
    would mean this package owning a message payload format, which is the first
    step towards owning messages.

    On failure, note where the HTTP code actually lives: ``service._err`` puts
    the *code string* in ``status`` and the numeric code in ``http_status``.
    Reading ``status`` as a number here raised ``ValueError`` and converted a
    plain 400 from the messaging authority into a 503 — the client then retried
    a request that could never succeed.
    """
    if isinstance(result, dict) and result.get("ok"):
        return po_http._no_store(result, 200)

    result = result if isinstance(result, dict) else {}
    status = 502
    for candidate in (result.get("http_status"), result.get("status")):
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            status = value
            break

    code = result.get("code") or result.get("status") or "messaging_error"
    if not isinstance(code, str) or not code.strip():
        code = "messaging_error"
    return po_http._no_store(
        {
            "ok": False,
            "code": str(code),
            "message": str(result.get("message") or fallback),
        },
        status,
    )


def _payload() -> dict:
    try:
        return dict(request.get_json(silent=True) or {})
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Capability truth — deliberately reachable before any thread exists, so a
# client can render honest empty states without guessing.
# ---------------------------------------------------------------------------

@private_office_conversations_blueprint.route(
    f"{BASE}/capabilities", methods=["GET"])
def api_private_office_conversations_capabilities():
    user, refusal = _entry()
    if refusal:
        return refusal
    return po_http._no_store(
        {"ok": True, "capabilities": po_conversations.capability_states()}
    )


# ---------------------------------------------------------------------------
# List and create
# ---------------------------------------------------------------------------

@private_office_conversations_blueprint.route(BASE, methods=["GET"])
def api_private_office_conversations_list():
    user, refusal = _entry()
    if refusal:
        return refusal

    scope = (request.args.get("scope") or "").strip()
    try:
        limit = int(request.args.get("limit") or po_conversations.DEFAULT_LIST_LIMIT)
    except (TypeError, ValueError):
        limit = po_conversations.DEFAULT_LIST_LIMIT

    def work(cur):
        return po_conversations.list_for_member(
            cur, actor_user_id=user["user_id"], limit=limit, office_scope=scope
        )

    try:
        listed = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_LIST_FAILED")
        return _unavailable("We could not load your conversations just now.")

    return po_http._no_store({
        "ok": True,
        "conversations": listed["items"],
        "count": listed["count"],
        "capabilities": po_conversations.capability_states(),
    })


@private_office_conversations_blueprint.route(BASE, methods=["POST"])
def api_private_office_conversations_create():
    user, refusal = _entry()
    if refusal:
        return refusal

    body = _payload()
    scope = str(body.get("office_scope") or body.get("scope") or "").strip()

    def work(cur):
        return po_conversations.create(
            cur, actor_user_id=user["user_id"], office_scope=scope, payload=body
        )

    try:
        created = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_CREATE_FAILED")
        return _unavailable("We could not start that conversation just now.")

    return po_http._no_store({"ok": True, **created})


# ---------------------------------------------------------------------------
# One thread
# ---------------------------------------------------------------------------

@private_office_conversations_blueprint.route(f"{BASE}/<ref>", methods=["GET"])
def api_private_office_conversation_detail(ref):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_conversations.require_member_view(
            cur, actor_user_id=user["user_id"], conversation_ref=ref
        )

    try:
        view = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_DETAIL_FAILED")
        return _unavailable("We could not open that conversation just now.")

    return po_http._no_store({
        "ok": True,
        "conversation": view["conversation"],
        "private_office": view["private_office"],
        "links": view["links"],
        "control": view["control"],
        "capabilities": po_conversations.capability_states(),
    })


# ---------------------------------------------------------------------------
# Delegated message operations. Each one gates, confirms the thread is an
# Office thread this member may see, then hands off. None of them writes.
# ---------------------------------------------------------------------------

def _guarded_delegate(ref, fallback: str, delegate):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_conversations.require_member_view(
            cur, actor_user_id=user["user_id"], conversation_ref=ref
        )

    try:
        view = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_RESOLVE_FAILED")
        return _unavailable(fallback)

    try:
        result = delegate(user["user_id"], view["conversation_id"])
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_DELEGATE_FAILED")
        return _unavailable(fallback)

    return _envelope(result, fallback=fallback)


@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/messages", methods=["GET"])
def api_private_office_conversation_messages(ref):
    filters = {k: v for k, v in request.args.items()}
    return _guarded_delegate(
        ref,
        "We could not load those messages just now.",
        lambda uid, cid: comm_service.list_messages(uid, cid, filters),
    )


@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/messages", methods=["POST"])
def api_private_office_conversation_send(ref):
    body = _payload()
    # `client_message_id` is passed through untouched. It is the canonical
    # service's idempotency identity, backed by a real partial unique index, and
    # rewriting or defaulting it here would break the retry guarantee the native
    # client depends on after a dropped connection.
    return _guarded_delegate(
        ref,
        "We could not send that message just now.",
        lambda uid, cid: comm_service.send_message(uid, cid, body),
    )


@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/read", methods=["POST"])
def api_private_office_conversation_mark_read(ref):
    return _guarded_delegate(
        ref,
        "We could not update your read state just now.",
        lambda uid, cid: comm_service.mark_read(uid, cid),
    )


@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/typing", methods=["POST"])
def api_private_office_conversation_typing(ref):
    body = _payload()
    is_typing = bool(body.get("is_typing", True))
    return _guarded_delegate(
        ref,
        "We could not update typing just now.",
        lambda uid, cid: comm_service.set_typing(uid, cid, is_typing),
    )


# ---------------------------------------------------------------------------
# The operations that are genuinely this package's own
# ---------------------------------------------------------------------------

@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/sensitivity", methods=["POST"])
def api_private_office_conversation_sensitivity(ref):
    user, refusal = _entry()
    if refusal:
        return refusal
    body = _payload()

    def work(cur):
        view = po_conversations.require_member_view(
            cur, actor_user_id=user["user_id"], conversation_ref=ref
        )
        row = view["classification"]
        if int(row.get("owner_user_id") or 0) != int(user["user_id"]):
            # 403 rather than 404: the member can already see this thread, so
            # hiding its existence here would be theatre, and a wrong status
            # teaches the client the wrong retry.
            raise po_conversations.PrivateConversationRejected(
                "Only the conversation owner can change its sensitivity.",
                status=403,
                code="not_owner",
            )
        return po_conversations.set_sensitivity(
            cur,
            conversation_id=view["conversation_id"],
            actor_user_id=user["user_id"],
            sensitivity=body.get("sensitivity"),
        )

    try:
        row = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_SENSITIVITY_FAILED")
        return _unavailable("We could not update that conversation just now.")

    return po_http._no_store({
        "ok": True,
        "private_office": po_conversations.classification_payload(row),
    })


@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/links", methods=["POST"])
def api_private_office_conversation_link(ref):
    user, refusal = _entry()
    if refusal:
        return refusal
    body = _payload()

    def work(cur):
        view = po_conversations.require_member_view(
            cur, actor_user_id=user["user_id"], conversation_ref=ref
        )
        po_conversations.link(
            cur,
            conversation_id=view["conversation_id"],
            actor_user_id=user["user_id"],
            link_type=body.get("link_type"),
            target_id=body.get("target_id"),
        )
        return po_conversations.list_links(cur, view["conversation_id"])

    try:
        links = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_LINK_FAILED")
        return _unavailable("We could not link that just now.")

    return po_http._no_store({"ok": True, "links": links})


@private_office_conversations_blueprint.route(
    f"{BASE}/<ref>/links", methods=["DELETE"])
def api_private_office_conversation_unlink(ref):
    user, refusal = _entry()
    if refusal:
        return refusal
    body = _payload()
    link_type = body.get("link_type") or request.args.get("link_type")
    target_id = body.get("target_id") or request.args.get("target_id")

    def work(cur):
        view = po_conversations.require_member_view(
            cur, actor_user_id=user["user_id"], conversation_ref=ref
        )
        po_conversations.unlink(
            cur,
            conversation_id=view["conversation_id"],
            actor_user_id=user["user_id"],
            link_type=link_type,
            target_id=target_id,
        )
        return po_conversations.list_links(cur, view["conversation_id"])

    try:
        links = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_UNLINK_FAILED")
        return _unavailable("We could not remove that link just now.")

    return po_http._no_store({"ok": True, "links": links})


# ---------------------------------------------------------------------------
# The reverse direction
# ---------------------------------------------------------------------------

@private_office_conversations_blueprint.route(
    f"{BASE}/links/<link_type>/<path:target_id>", methods=["GET"])
def api_private_office_conversations_for_target(link_type, target_id):
    """Which of the member's threads reference one object.

    Sits under this blueprint rather than under documents or records because the
    answer is about conversations: it is filtered by conversation membership,
    audited as a conversation read, and gated by the conversations feature flag.
    A copy of it living in the documents pack would be a second place that
    decides who may see a thread.

    ``target_id`` takes ``path:`` because record and document identifiers are
    opaque strings that already contain slashes in some domains. The value is
    still run through the same ``safe_object_id`` shape check as a write, so a
    permissive converter does not become a permissive validator.

    A member with no linked threads gets ``200`` and an empty list. The refusal
    codes are the ones the rest of this surface uses, so the client's existing
    tagged union covers this route without a new branch — and an empty list here
    can only ever mean "nothing is linked", never "the read failed".
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    try:
        limit = int(request.args.get("limit") or po_conversations.DEFAULT_LIST_LIMIT)
    except (TypeError, ValueError):
        limit = po_conversations.DEFAULT_LIST_LIMIT

    def work(cur):
        return po_conversations.list_for_target(
            cur,
            actor_user_id=user["user_id"],
            link_type=link_type,
            target_id=target_id,
            limit=limit,
        )

    try:
        listed = po_http._with_cursor(work)
    except po_conversations.PrivateConversationRejected as exc:
        return _rejected(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_CONVERSATIONS_FOR_TARGET_FAILED")
        return _unavailable("We could not check that for linked conversations.")

    return po_http._no_store({
        "ok": True,
        "conversations": listed["items"],
        "count": listed["count"],
        "capabilities": po_conversations.capability_states(),
    })


def register(app) -> None:
    """Mount the pack. Called by ``bot._load_route_pack``.

    Registration is deliberately the only thing this does. Schema creation is
    NOT done here and NOT done at import: the two classification tables are
    created by ``ensure_conversations_schema`` inside the request that needs
    them, and by ``bot.init_db`` at boot. Creating them in a route-pack
    registration would mean a worker process — which never imports this pack —
    running against tables that only the web process believes exist, which is
    exactly the Stage 176B failure the schema module's docstring records.
    """
    app.register_blueprint(private_office_conversations_blueprint)


__all__ = [
    "private_office_conversations_blueprint",
    "register",
    "CONVERSATIONS_FEATURE_ID",
]
