"""HTTP surface for the canonical Private Office entitlement truth and the
first real Private Office capability.

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

``GET /api/private-office/overview``
    The product entry state plus this member's per-domain fact counts. One
    call, because the landing screen needs both and two calls would let them
    disagree — a screen that knows it can open the room but not yet what is in
    it renders a heading over nothing.

``GET /api/private-office/facts``
    One domain's facts for the signed-in member, projected for display.

``POST /api/private-office/facts``
    The member records one fact about themselves.

``GET /api/private-office/capital-graph``
``GET /api/private-office/entities/<node_id>``
``GET /api/private-office/entities/<node_id>/relationships``
    The Capital Graph: what the member's private graph holds, and how well each
    part of it is known. All three delegate to ``private_office.capital_graph``,
    which reads only through ``retrieval.retrieve``. None of them takes an owner
    parameter and none of them computes a total — see that module for why a net
    worth is a feature this surface declines to have rather than one it has not
    got round to.

``GET /api/private-office/records/<view>``
``POST /api/private-office/records/<view>``
``POST /api/private-office/records/<view>/<id>/status``
``GET /api/private-office/attention``
    Operations: the six record primitives (obligations, events, decisions,
    requests, risks, opportunities) and the "what needs me" summary the Office
    Home renders. All owner-scoped by shape; writes go through the canonical
    ``records`` writers only.

There is deliberately no POST that grants a tier. Granting is an entitlement
operation and belongs to the existing admin entitlement paths; adding one here
would create a second granting authority, which is precisely the drift the
ownership contract forbids. The fact POST is a different kind of write: it
adds a row the member owns to the member's own store, through the canonical
writer, and it can never change what the member is entitled to.

Every member-facing route below is gated on the *server's* answer, not on a
client's claim. The gate asks ``feature_matrix.is_entitled`` rather than
comparing tiers itself, so an unbuilt capability is refused even to the top of
the ladder, and the refusal names which of the two reasons applies. A client
that hid the button would still be talking to an endpoint that says no.
"""

from __future__ import annotations

import datetime as _dt
import logging

from flask import Blueprint, jsonify, request

from services import auth_service
from services import db
from services.private_office import access as po_access
from services.private_office import audit as po_audit
from services.private_office import capital_graph as po_capital
from services.private_office import facts as po_facts
from services.private_office import feature_matrix as po_matrix
from services.private_office import model as po_model
from services.private_office import office as po_office
from services.private_office import records as po_records
from services.private_office import retrieval as po_retrieval
from services.private_office import schema as po_schema
from services.private_office import security as po_security
from services.private_office import status as po_status
from services.private_office import tiers as po_tiers

#: The capability every member-facing route in this pack depends on. Named once
#: so the gate and the product state can never drift onto different feature ids.
FACTS_FEATURE_ID = "private_facts"

#: The Capital Graph rows are gated separately from the fact store. They are
#: different matrix rows at different tiers, and a member may hold one without
#: the other; gating both on ``private_facts`` would make the fact kill switch
#: silently take the graph down with it.
CAPITAL_FEATURE_ID = po_capital.FEATURE_ID

#: How many facts one list call may return. The reader bounds this too; the
#: route states its own ceiling so the contract is readable from the endpoint.
MAX_PAGE = 100

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


# --- member-facing capability surface ---------------------------------------


def _resolve_for(user) -> dict:
    """The caller's own tier. Never takes a user parameter from the request."""
    return po_tiers.resolve_tier(user["user_id"], context=_context_from(user) or None)


def _gate(resolved: dict, feature_id: str):
    """Render the canonical access decision as an HTTP refusal.

    Returns ``None`` when the caller may proceed, or a ready-to-return refusal.

    The decision itself belongs to ``private_office.access`` and is shared with
    the UNDX capability executor, so the screen and the agent cannot disagree
    about what this member may reach. What lives here is only the translation
    into status codes.

    Three refusals, and they are deliberately three rather than one. A resolver
    that did not answer is 503 with ``state: "unavailable"``, because "we could
    not look" must not be served with the same shape as "you may not have
    this" — the person most likely to hit a degraded resolve is the person who
    paid, and telling them they lack access is the worse of the two errors. An
    unbuilt capability is 404, not 402: there is nothing to sell, so an upgrade
    prompt would be a lie. Only a real capability out of reach is 403 with an
    upgrade path.
    """
    decision = po_access.decide(resolved, feature_id)
    verdict = decision["decision"]

    if verdict == po_access.ALLOW:
        return None

    if verdict == po_access.UNAVAILABLE:
        return _no_store(
            {
                "ok": False,
                "state": "unavailable",
                "message": "We could not confirm your access just now.",
            },
            503,
        )

    if verdict in (po_access.NOT_IMPLEMENTED, po_access.FEATURE_DISABLED):
        return _no_store(
            {
                "ok": False,
                "state": verdict,
                "implementation": decision["implementation"],
                "feature_id": feature_id,
                "message": "This is not available yet.",
            },
            404,
        )

    return _no_store(
        {
            "ok": False,
            "state": po_access.NOT_ENTITLED,
            "feature_id": feature_id,
            "minimum_tier": decision["minimum_tier"],
            "message": "Your plan does not include this.",
        },
        403,
    )


def _with_cursor(work):
    """Run ``work(cur)`` on one connection, committing only on success.

    The commit is here rather than inside the handlers so a handler cannot
    return a success body for a transaction that then fails to land. A write
    that half-happened and reported ``ok: true`` is the failure this shape
    exists to make impossible.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        po_schema.ensure_private_schema(cur)
        result = work(cur)
        conn.commit()
        return result
    finally:
        conn.close()


# --- the second lock ---------------------------------------------------------
#
# Being signed in opens the app. It does not open the Office. Every route below
# that returns or writes Office data stands behind ``_office_lock_gate`` as
# well as the tier gate, and the two are different questions on purpose: the
# tier gate asks "did this member pay for the room", the lock gate asks "did
# the person holding the phone just prove they are the member". The threat the
# second one addresses is precisely a valid session in the wrong hands.

GRANT_HEADER = po_security.GRANT_HEADER
DEVICE_HEADER = po_security.DEVICE_HEADER

#: Extraction is owned by the security module so the binding a grant is minted
#: against here is byte-identical to the one the UNDX surface later checks.
_office_bindings = po_security.request_bindings


def _locked_refusal(*, setup_required: bool):
    """423 Locked, one shape, machine-readable. The client renders the unlock
    screen (or the setup flow) from ``code`` + ``setup_required`` and nothing
    else — no Office data rides along with a refusal."""
    return _no_store(
        {
            "ok": False,
            "state": po_security.ERR_LOCKED,
            "code": po_security.ERR_LOCKED,
            "setup_required": setup_required,
            "message": "Unlock Private Office to continue.",
        },
        423,
    )


def _office_lock_gate(user):
    """``None`` when this request carries a valid unlock grant; a 423 refusal
    otherwise. Fails closed: a database problem while checking the lock is a
    locked Office, never an open one."""
    session_binding, device_binding = _office_bindings()
    grant_token = (request.headers.get(GRANT_HEADER) or "").strip()

    def work(cur):
        state = po_security.security_state(cur, user["user_id"])
        if not state["passcode_set"]:
            return {"ok": False, "setup_required": True}
        verdict = po_security.validate_grant(
            cur,
            user["user_id"],
            grant_token,
            session_binding=session_binding,
            device_binding=device_binding,
        )
        return {"ok": bool(verdict.get("ok")), "setup_required": False}

    try:
        outcome = _with_cursor(work)
    except Exception:  # noqa: BLE001 — a broken lock check is a locked door
        LOGGER.exception("PRIVATE_OFFICE_LOCK_CHECK_FAILED")
        return _locked_refusal(setup_required=False)

    if outcome["ok"]:
        return None
    return _locked_refusal(setup_required=outcome["setup_required"])


@private_office_blueprint.route("/api/private-office/overview", methods=["GET"])
def api_private_office_overview():
    """Product entry state and this member's per-domain counts, in one answer."""
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    resolved = _resolve_for(user)
    trustworthy = resolved.get("resolver_state") == po_tiers.RESOLVER_OK
    product = po_office.product_state(
        resolved.get("effective_tier"), resolver_ok=trustworthy
    )

    # The domain summary is only computed when the member can actually read
    # facts. Returning counts to somebody the gate would refuse would make the
    # overview a way to read the store without going through the store. The
    # same holds for the second lock: counts are Office data, so a locked
    # request gets the product state and the lock state — enough to render the
    # landing screen and the unlock prompt — and nothing counted.
    domains: list = []
    entitled = trustworthy and po_matrix.is_entitled(
        FACTS_FEATURE_ID, resolved.get("effective_tier")
    )
    lock_refusal = _office_lock_gate(user) if entitled else None
    if entitled and lock_refusal is not None:
        return _no_store(
            {
                "ok": trustworthy,
                "private_office": product,
                "locked": True,
                "setup_required": bool(
                    (lock_refusal[0].get_json(silent=True) or {}).get("setup_required")
                ),
                "domains": [],
                "verified_at": resolved.get("verified_at", ""),
            }
        )
    if entitled:
        try:
            domains = _with_cursor(
                lambda cur: po_office.domain_summary(cur, owner_user_id=user["user_id"])
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("PRIVATE_OFFICE_OVERVIEW_SUMMARY_FAILED")
            # An unreadable store is not an empty store. Say nothing about the
            # counts rather than render seven confident zeros over real data.
            return _no_store(
                {
                    "ok": False,
                    "state": "unavailable",
                    "private_office": product,
                    "message": "We could not load your information just now.",
                },
                503,
            )

    return _no_store(
        {
            "ok": trustworthy,
            "private_office": product,
            "domains": domains,
            "verified_at": resolved.get("verified_at", ""),
        }
    )


@private_office_blueprint.route("/api/private-office/facts", methods=["GET"])
def api_private_office_facts():
    """One domain's facts, for the signed-in member only."""
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    resolved = _resolve_for(user)
    refusal = _gate(resolved, FACTS_FEATURE_ID)
    if refusal:
        return refusal
    locked = _office_lock_gate(user)
    if locked:
        return locked

    raw_domain = (request.args.get("domain") or "").strip()
    domain = po_model.normalize_domain(raw_domain) if raw_domain else None
    if raw_domain and not domain:
        # An unrecognised domain is a client error, answered as one. Silently
        # dropping the filter would return the member's whole store to a
        # request that asked for one heading.
        return _no_store(
            {"ok": False, "message": "Unknown domain.", "domains": list(po_model.DOMAINS)},
            400,
        )

    try:
        limit = int(request.args.get("limit") or MAX_PAGE)
    except (TypeError, ValueError):
        limit = MAX_PAGE
    limit = max(1, min(limit, MAX_PAGE))

    try:
        offset = int(request.args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)

    def work(cur):
        rows = po_facts.list_facts(
            cur,
            owner_user_id=user["user_id"],
            domains=[domain] if domain else None,
            limit=limit,
            offset=offset,
        )
        po_audit.record(
            cur,
            actor_user_id=user["user_id"],
            owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ,
            object_type="DOMAIN",
            object_id=domain or "ALL",
            purpose="user_request",
            result_count=len(rows),
        )
        return rows

    try:
        rows = _with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_FACTS_READ_FAILED")
        return _no_store(
            {"ok": False, "state": "unavailable", "message": "We could not load your information just now."},
            503,
        )

    return _no_store(
        {
            "ok": True,
            "domain": domain or "",
            "facts": po_office.project_facts(rows),
            "count": len(rows),
            "limit": limit,
            "offset": offset,
        }
    )


@private_office_blueprint.route("/api/private-office/facts", methods=["POST"])
def api_private_office_create_fact():
    """The member records one fact about themselves, through the canonical writer.

    The owner is taken from the session and is never read from the body. There
    is no ``owner_user_id`` parameter to send, so no request can write into
    another member's store — the isolation is a property of the shape of this
    endpoint rather than of a check that could be forgotten.

    Provenance is fixed at ``USER_ASSERTED`` here and cannot be supplied by the
    client. A client that could name its own provenance could label its own
    typing ``VERIFIED``, which would make the whole verification vocabulary
    worthless on the first request that tried.
    """
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    resolved = _resolve_for(user)
    refusal = _gate(resolved, FACTS_FEATURE_ID)
    if refusal:
        return refusal
    locked = _office_lock_gate(user)
    if locked:
        return locked

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _no_store({"ok": False, "message": "Invalid request body."}, 400)

    domain = po_model.normalize_domain(body.get("domain"))
    if not domain:
        return _no_store(
            {"ok": False, "message": "Unknown domain.", "domains": list(po_model.DOMAINS)},
            400,
        )

    value_type = po_model.normalize_value_type(body.get("value_type"))
    if not value_type:
        return _no_store(
            {
                "ok": False,
                "message": "Unknown value type.",
                "value_types": list(po_model.VALUE_TYPES),
            },
            400,
        )

    sensitivity = po_model.normalize_sensitivity(body.get("sensitivity"))

    def work(cur):
        return po_facts.record_fact(
            cur,
            owner_user_id=user["user_id"],
            subject_type=po_facts.SUBJECT_NODE,
            subject_id=str(body.get("subject_id") or user["user_id"]),
            fact_type=body.get("fact_type"),
            value=body.get("value"),
            value_type=value_type,
            provenance_type=po_model.PROVENANCE_USER_ASSERTED,
            domain=domain,
            sensitivity=sensitivity,
            actor_user_id=user["user_id"],
            purpose="user_request",
        )

    try:
        written = _with_cursor(work)
    except po_facts.PrivateFactRejected as exc:
        # The writer's rejections are validation, not failure. Its reason is
        # returned verbatim because it is written for a person and because a
        # generic "invalid input" would leave the member unable to fix it.
        return _no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_FACT_WRITE_FAILED")
        return _no_store(
            {"ok": False, "state": "unavailable", "message": "We could not save that just now."},
            503,
        )

    return _no_store(
        {
            "ok": True,
            "status": written.get("status"),
            "fact_id": written.get("fact_id"),
            "domain": written.get("domain"),
            "sensitivity": written.get("sensitivity"),
        },
        201,
    )


# --- the Capital Graph ------------------------------------------------------
#
# Three reads, one shape. Each resolves the caller's own tier, gates on
# ``capital_graph``, picks a view, and hands the whole job to
# ``private_office.capital_graph`` — which reads only through
# ``retrieval.retrieve``. Nothing below touches ``po_graph`` or issues a query,
# and that is the point: the shortest path from a handler to a node row is
# ``graph.get_node``, and a handler that reaches for it has walked around the
# owner, authorization, sensitivity, domain and purpose gates without noticing,
# because the row comes back and looks correct.
#
# There is no audit call here either. ``retrieval.retrieve`` records the read
# itself, so a second record from the route would double-count every traversal
# and make the audit trail disagree with the store about how often the member's
# graph was looked at.


def _requested_view():
    """The view named by the query string, or a 400 that lists the real ones.

    An unknown view is answered rather than defaulted, for the same reason an
    unknown domain is on the facts route: a client that asked for something this
    server has never heard of has a bug, and quietly serving it a different view
    hides the bug behind data that looks plausible.
    """
    raw = (request.args.get("view") or "").strip()
    if not raw:
        return po_capital.DEFAULT_VIEW, None
    view = po_capital.normalize_view(raw)
    if not view:
        return None, _no_store(
            {"ok": False, "message": "Unknown view.", "views": list(po_capital.VIEWS)},
            400,
        )
    return view, None


def _capital_failure():
    """An unreadable graph is not an empty graph.

    Reported as 503 rather than as an empty payload, because ``nodes: []`` with
    ``ok: true`` renders as "you have nothing recorded" over a store that may be
    full — and a member who believes their records are gone will act on it.
    """
    return _no_store(
        {
            "ok": False,
            "state": "unavailable",
            "message": "We could not load your information just now.",
        },
        503,
    )


@private_office_blueprint.route("/api/private-office/capital-graph", methods=["GET"])
def api_private_office_capital_graph():
    """The member's own Capital Graph overview for one view."""
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    resolved = _resolve_for(user)
    refusal = _gate(resolved, CAPITAL_FEATURE_ID)
    if refusal:
        return refusal
    locked = _office_lock_gate(user)
    if locked:
        return locked

    view, bad_view = _requested_view()
    if bad_view:
        return bad_view

    try:
        payload = _with_cursor(
            lambda cur: po_capital.summary(
                cur,
                owner_user_id=user["user_id"],
                actor_user_id=user["user_id"],
                view=view,
                purpose="user_request",
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_CAPITAL_GRAPH_READ_FAILED")
        return _capital_failure()

    # A retrieval refusal is 403, not 200-with-nothing. The member asked a
    # question the policy would not answer, and saying so is more useful than an
    # empty graph they would read as "nothing recorded".
    if payload["denied"]:
        return _no_store(
            {"ok": False, "state": "denied", "reason": payload["denied"],
             "view": view},
            403,
        )

    return _no_store({"ok": True, "capital_graph": payload,
                      "views": list(po_capital.VIEWS)})


@private_office_blueprint.route(
    "/api/private-office/entities/<node_id>", methods=["GET"])
def api_private_office_entity(node_id):
    """One entity, its immediate neighbourhood, and what is asserted about it.

    ``node_id`` is a path parameter and the owner is the session. A node that is
    absent, belongs to another member, or is outside this view's domain all
    return the same 404 — the route must not un-collapse those, or the
    difference between the answers becomes a way to test whether an id exists.
    """
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    resolved = _resolve_for(user)
    refusal = _gate(resolved, CAPITAL_FEATURE_ID)
    if refusal:
        return refusal
    locked = _office_lock_gate(user)
    if locked:
        return locked

    view, bad_view = _requested_view()
    if bad_view:
        return bad_view

    try:
        payload = _with_cursor(
            lambda cur: po_capital.entity(
                cur,
                owner_user_id=user["user_id"],
                actor_user_id=user["user_id"],
                node_id=node_id,
                view=view,
                purpose="user_request",
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_CAPITAL_ENTITY_READ_FAILED")
        return _capital_failure()

    if payload["denied"]:
        return _no_store(
            {"ok": False, "state": "not_found", "view": view,
             "message": "No such entity."},
            404,
        )

    return _no_store({"ok": True, "entity": payload["entity"],
                      "capital_graph": payload})


@private_office_blueprint.route(
    "/api/private-office/entities/<node_id>/relationships", methods=["GET"])
def api_private_office_entity_relationships(node_id):
    """The edges touching one entity, with the far end named.

    A projection of the entity read rather than a second traversal, so this
    endpoint and the one above can never show different edges.
    """
    user = _current_user()
    if not user:
        return _no_store({"ok": False, "message": "Login required."}, 401)

    resolved = _resolve_for(user)
    refusal = _gate(resolved, CAPITAL_FEATURE_ID)
    if refusal:
        return refusal
    locked = _office_lock_gate(user)
    if locked:
        return locked

    view, bad_view = _requested_view()
    if bad_view:
        return bad_view

    try:
        payload = _with_cursor(
            lambda cur: po_capital.relationships(
                cur,
                owner_user_id=user["user_id"],
                actor_user_id=user["user_id"],
                node_id=node_id,
                view=view,
                purpose="user_request",
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_CAPITAL_RELATIONSHIPS_READ_FAILED")
        return _capital_failure()

    if payload["denied"]:
        return _no_store(
            {"ok": False, "state": "not_found", "view": view,
             "message": "No such entity."},
            404,
        )

    return _no_store(
        {
            "ok": True,
            "entity": payload["entity"],
            "relationships": payload["relationships"],
            "view": payload["view"],
            "complete": payload["complete"],
        }
    )


# --- Operations: the six record primitives -----------------------------------
#
# Four routes over the Batch C record store, all owner-scoped by shape: the
# owner is the session and there is no parameter to name anyone else. Reads go
# through ``records.list_records`` — the owner-scoped reader in the one module
# permitted to name these tables — rather than ``retrieval.retrieve_records``,
# because the retrieval intents are deliberately narrow context windows for
# agents, and a member looking at their own screen is not a context window: an
# intent ceiling that hid the member's own financial obligations from their own
# Operations list would be enforcing a rule written for a different caller.
# Writes go through ``records.create_record`` / ``records.update_record``,
# which audit themselves; the list route audits here, as the facts route does.

OPERATIONS_FEATURE_ID = "private_office.operations"

#: One page of records. The store bounds harder (records.MAX_LIMIT); the route
#: states its own ceiling so the contract is readable from the endpoint.
MAX_RECORDS_PAGE = 100

#: Body fields a member may supply when creating a record. Allowlist, not
#: passthrough: ``source_type`` is fixed at USER below and ``provenance_type``
#: is absent entirely, for the same reason the fact POST pins USER_ASSERTED —
#: a client that could name its own provenance could label its own typing
#: verified. ``relevance_score`` is also absent: that column is only ever what
#: a named source supplied, and the member's own enthusiasm is not a score.
_RECORD_BODY_FIELDS: tuple[str, ...] = (
    "title", "summary", "description", "domain", "sensitivity", "status",
    "obligation_type", "due_at", "amount", "currency",
    "event_type", "occurred_at",
    "question", "assumptions", "deadline_at", "outcome",
    "category", "priority", "confidentiality",
    "risk_type", "severity", "coverage_state", "review_required",
    "opportunity_type",
)


def _record_view(view: str):
    """(record_type, refusal). An unknown view is a 404 that names the real
    ones — a client asking for a view this server has never heard of has a bug,
    and quietly serving a different collection would hide it."""
    wanted = str(view or "").strip().lower()
    record_type = po_retrieval.RECORD_VIEWS.get(wanted)
    if not record_type:
        return None, _no_store(
            {"ok": False, "message": "Unknown view.",
             "views": sorted(po_retrieval.RECORD_VIEWS)},
            404,
        )
    return record_type, None


def _operations_entry():
    """Auth + tier gate + second lock shared by every operations route."""
    user = _current_user()
    if not user:
        return None, _no_store({"ok": False, "message": "Login required."}, 401)
    resolved = _resolve_for(user)
    refusal = _gate(resolved, OPERATIONS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = _office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


@private_office_blueprint.route(
    "/api/private-office/records/<view>", methods=["GET"])
def api_private_office_records(view):
    """One view's records for the signed-in member, newest first."""
    user, refusal = _operations_entry()
    if refusal:
        return refusal
    record_type, bad_view = _record_view(view)
    if bad_view:
        return bad_view

    statuses = (request.args.get("status") or "").strip() or None

    try:
        limit = int(request.args.get("limit") or MAX_RECORDS_PAGE)
    except (TypeError, ValueError):
        limit = MAX_RECORDS_PAGE
    limit = max(1, min(limit, MAX_RECORDS_PAGE))

    try:
        before_id = int(request.args.get("before_id") or 0)
    except (TypeError, ValueError):
        before_id = 0

    def work(cur):
        rows = po_records.list_records(
            cur,
            record_type=record_type,
            owner_user_id=user["user_id"],
            statuses=statuses,
            limit=limit,
            before_id=max(0, before_id),
        )
        open_count = po_records.count_open(
            cur, record_type=record_type, owner_user_id=user["user_id"]
        )
        po_audit.record(
            cur,
            actor_user_id=user["user_id"],
            owner_user_id=user["user_id"],
            action=po_audit.ACTION_RECORD_READ,
            object_type="RECORD_VIEW",
            object_id=str(view).strip().lower(),
            purpose="user_request",
            result_count=len(rows),
        )
        return rows, open_count

    try:
        rows, open_count = _with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_RECORDS_READ_FAILED")
        return _no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your information just now."},
            503,
        )

    return _no_store(
        {
            "ok": True,
            "view": str(view).strip().lower(),
            "records": rows,
            "count": len(rows),
            "open_count": open_count,
            "limit": limit,
            "statuses": list(po_records.SPECS[record_type]["statuses"]),
        }
    )


@private_office_blueprint.route(
    "/api/private-office/records/<view>", methods=["POST"])
def api_private_office_create_record(view):
    """The member records one obligation, event, decision, request, risk or
    opportunity of their own. Owner from the session; source pinned to USER."""
    user, refusal = _operations_entry()
    if refusal:
        return refusal
    record_type, bad_view = _record_view(view)
    if bad_view:
        return bad_view

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _no_store({"ok": False, "message": "Invalid request body."}, 400)

    fields = {
        name: body[name]
        for name in _RECORD_BODY_FIELDS
        if name in body and body[name] is not None
    }
    fields["source_type"] = po_records.SOURCE_USER

    def work(cur):
        return po_records.create_record(
            cur,
            record_type=record_type,
            owner_user_id=user["user_id"],
            actor_user_id=user["user_id"],
            purpose="user_request",
            **fields,
        )

    try:
        written = _with_cursor(work)
    except po_records.PrivateRecordRejected as exc:
        # The writer's rejections are validation, not failure, and its reason
        # is written for a person; a generic "invalid input" would leave the
        # member unable to fix it.
        return _no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_RECORD_WRITE_FAILED")
        return _no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not save that just now."},
            503,
        )

    return _no_store(
        {
            "ok": True,
            "status": written.get("status"),
            "record_id": written.get("record_id"),
            "record": written.get("record"),
            "view": str(view).strip().lower(),
        },
        201,
    )


@private_office_blueprint.route(
    "/api/private-office/records/<view>/<int:record_id>/status",
    methods=["POST"])
def api_private_office_record_status(view, record_id):
    """Move one record's status (and, for a decision, its outcome).

    Deliberately as narrow as ``records.update_record`` beneath it: the
    substance of a record is not reachable from this endpoint, so the decision
    log's history cannot be rewritten from a phone.
    """
    user, refusal = _operations_entry()
    if refusal:
        return refusal
    record_type, bad_view = _record_view(view)
    if bad_view:
        return bad_view

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not str(body.get("status") or "").strip():
        return _no_store(
            {"ok": False, "message": "A status is required.",
             "statuses": list(po_records.SPECS[record_type]["statuses"])},
            400,
        )

    fields: dict = {"status": body["status"]}
    if str(body.get("outcome") or "").strip():
        fields["outcome"] = body["outcome"]

    def work(cur):
        return po_records.update_record(
            cur,
            record_type=record_type,
            owner_user_id=user["user_id"],
            record_id=int(record_id),
            actor_user_id=user["user_id"],
            purpose="user_request",
            **fields,
        )

    try:
        outcome = _with_cursor(work)
    except po_records.PrivateRecordRejected as exc:
        return _no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_RECORD_UPDATE_FAILED")
        return _no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not save that just now."},
            503,
        )

    if outcome.get("status") == "absent":
        # Same answer for "not yours" and "never existed" — the store already
        # collapsed the two, and this route must not reinflate the difference.
        return _no_store({"ok": False, "message": "Not found."}, 404)

    return _no_store(
        {
            "ok": True,
            "status": outcome.get("status"),
            "record": outcome.get("record"),
            "view": str(view).strip().lower(),
        }
    )


@private_office_blueprint.route("/api/private-office/attention", methods=["GET"])
def api_private_office_attention():
    """What needs the member's eyes: open counts per view, and the obligations
    due soonest. One call, so the Office Home cannot render counts and a
    due-soon list that disagree about the same store."""
    user, refusal = _operations_entry()
    if refusal:
        return refusal

    horizon = (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    def work(cur):
        counts = {
            view: po_records.count_open(
                cur, record_type=record_type, owner_user_id=user["user_id"]
            )
            for view, record_type in po_retrieval.RECORD_VIEWS.items()
        }
        due_soon = po_records.list_records(
            cur,
            record_type=po_records.TYPE_OBLIGATION,
            owner_user_id=user["user_id"],
            statuses=("OPEN",),
            due_before=horizon,
            limit=5,
        )
        po_audit.record(
            cur,
            actor_user_id=user["user_id"],
            owner_user_id=user["user_id"],
            action=po_audit.ACTION_RECORD_READ,
            object_type="RECORD_VIEW",
            object_id="attention",
            purpose="user_request",
            result_count=len(due_soon),
        )
        return counts, due_soon

    try:
        counts, due_soon = _with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_ATTENTION_FAILED")
        # An unreadable store is not a quiet one. Refusing beats rendering
        # confident zeros over real obligations.
        return _no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your information just now."},
            503,
        )

    return _no_store(
        {
            "ok": True,
            "counts": counts,
            "due_soon": due_soon,
            "due_horizon": horizon,
        }
    )


# --- second-lock management routes ------------------------------------------
#
# Ordering, once, because Stage 29 makes it a gate: ENTITLEMENT BEFORE
# PASSCODE. Every route here checks auth, then the tier gate, and only then
# touches the lock. A member who is not entitled to the Office cannot create,
# probe, or exercise an Office passcode — the lock is a property of the room,
# and a member with no room gets the same 403 the room itself would give.
#
# The passcode arrives in a JSON body over TLS, is passed straight to the
# security module, and is never logged, echoed, or placed in a URL or token.


def _security_entry(min_feature: str = FACTS_FEATURE_ID):
    """Auth + tier gate shared by every security route. Returns (user, refusal)."""
    user = _current_user()
    if not user:
        return None, _no_store({"ok": False, "message": "Login required."}, 401)
    resolved = _resolve_for(user)
    refusal = _gate(resolved, min_feature)
    if refusal:
        return None, refusal
    return user, None


@private_office_blueprint.route("/api/private-office/security/status", methods=["GET"])
def api_office_security_status():
    """Setup state, cooldown, biometric preference — and whether THIS request
    is currently unlocked. No hash material, no counters, no grant list."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    session_binding, device_binding = _office_bindings()
    grant_token = (request.headers.get(GRANT_HEADER) or "").strip()

    def work(cur):
        state = po_security.security_state(cur, user["user_id"])
        unlocked = False
        if state["passcode_set"] and grant_token:
            unlocked = bool(
                po_security.validate_grant(
                    cur, user["user_id"], grant_token,
                    session_binding=session_binding,
                    device_binding=device_binding,
                ).get("ok")
            )
        return {**state, "unlocked": unlocked}

    try:
        state = _with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_STATUS_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    return _no_store({"ok": True, **state, "setup_required": not state["passcode_set"]})


@private_office_blueprint.route("/api/private-office/security/setup", methods=["POST"])
def api_office_security_setup():
    """First-entry passcode creation (Stage 1-3). Refuses if one exists."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}
    passcode = str(body.get("passcode") or "")
    if str(body.get("confirm_passcode") or "") != passcode:
        return _no_store({"ok": False, "error": "confirm_mismatch"}, 400)

    try:
        result = _with_cursor(
            lambda cur: po_security.create_passcode(cur, user["user_id"], passcode)
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_SETUP_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    if not result["ok"]:
        status = 409 if result["error"] == po_security.ERR_ALREADY_SET else 400
        return _no_store({"ok": False, **result}, status)
    return _no_store({"ok": True}, 201)


@private_office_blueprint.route("/api/private-office/security/unlock", methods=["POST"])
def api_office_security_unlock():
    """Prove the passcode, receive one bounded grant. The server's answer is
    the only unlock there is — Face ID success on the device ends up here too."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}
    session_binding, device_binding = _office_bindings()

    try:
        result = _with_cursor(
            lambda cur: po_security.verify_and_unlock(
                cur, user["user_id"], str(body.get("passcode") or ""),
                session_binding=session_binding, device_binding=device_binding,
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_UNLOCK_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    if not result["ok"]:
        status = 429 if result["error"] == po_security.ERR_COOLDOWN else (
            409 if result["error"] == po_security.ERR_NOT_SET else 401
        )
        return _no_store({"ok": False, **result}, status)
    return _no_store({"ok": True, **result})


@private_office_blueprint.route("/api/private-office/security/lock", methods=["POST"])
def api_office_security_lock():
    """Manual lock. With the grant header, that grant dies; with
    ``{"all": true}``, every live grant for this member dies (every device)."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}
    grant_token = (request.headers.get(GRANT_HEADER) or "").strip()
    revoke_all = bool(body.get("all"))

    try:
        revoked = _with_cursor(
            lambda cur: po_security.revoke_grants(
                cur, user["user_id"], reason="manual_lock",
                token=None if revoke_all else (grant_token or None),
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_LOCK_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    return _no_store({"ok": True, "revoked": revoked})


@private_office_blueprint.route("/api/private-office/security/change", methods=["POST"])
def api_office_security_change():
    """Rotate the passcode. Proof of the current one is the authorization;
    every existing grant on every device is revoked on success (Stage 12)."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}
    new_passcode = str(body.get("new_passcode") or "")
    if str(body.get("confirm_passcode") or "") != new_passcode:
        return _no_store({"ok": False, "error": "confirm_mismatch"}, 400)

    try:
        result = _with_cursor(
            lambda cur: po_security.change_passcode(
                cur, user["user_id"],
                str(body.get("current_passcode") or ""), new_passcode,
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_CHANGE_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    if not result["ok"]:
        status = {
            po_security.ERR_COOLDOWN: 429,
            po_security.ERR_NOT_SET: 409,
            po_security.ERR_WRONG_PASSCODE: 401,
        }.get(result["error"], 400)
        return _no_store({"ok": False, **result}, status)
    return _no_store({"ok": True})


@private_office_blueprint.route("/api/private-office/security/reset", methods=["POST"])
def api_office_security_reset():
    """Forgotten passcode (Stage 11). Elevated re-verification: the member must
    prove the ACCOUNT PASSWORD in this request. A logged-in session alone is
    exactly the credential the second lock distrusts, so it never suffices.
    Failed proofs feed the same server-side rate limit as failed passcodes.
    Office data is never destroyed by this path."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}
    new_passcode = str(body.get("new_passcode") or "")
    if str(body.get("confirm_passcode") or "") != new_passcode:
        return _no_store({"ok": False, "error": "confirm_mismatch"}, 400)

    account_hash = (user or {}).get("password_hash") or ""
    if not account_hash:
        # No account password on file (e.g. a social-only account): there is no
        # elevated proof available here, and downgrading to "you are logged in"
        # would erase the lock's whole point. Refuse; support recovery flows
        # can re-establish an account password first.
        return _no_store(
            {"ok": False, "error": po_security.ERR_REVERIFY,
             "message": "Set an account password first, then reset your Office passcode."},
            403,
        )

    reverified = auth_service.verify_password(
        account_hash, str(body.get("account_password") or "")
    )

    def work(cur):
        if not reverified:
            # A failed account-password proof feeds the same server-side
            # cooldown as a failed passcode: reset must not be the cheap
            # surface to brute-force.
            po_security.register_external_failure(cur, user["user_id"])
            return {"ok": False, "error": po_security.ERR_REVERIFY}
        return po_security.reset_passcode(
            cur, user["user_id"], new_passcode, reverified=True
        )

    try:
        result = _with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_RESET_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    if not result["ok"]:
        status = 403 if result["error"] == po_security.ERR_REVERIFY else 400
        return _no_store({"ok": False, **result}, status)
    return _no_store({"ok": True})


@private_office_blueprint.route("/api/private-office/security/biometric", methods=["POST"])
def api_office_security_biometric():
    """Record the Face ID preference. A flag for truthful settings rendering —
    never an unlock path; the grant still comes from /unlock."""
    user, refusal = _security_entry()
    if refusal:
        return refusal
    body = request.get_json(silent=True) or {}

    try:
        result = _with_cursor(
            lambda cur: po_security.set_biometric_preference(
                cur, user["user_id"], bool(body.get("enabled"))
            )
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_SECURITY_BIOMETRIC_FAILED")
        return _no_store({"ok": False, "state": "unavailable"}, 503)
    if not result["ok"]:
        return _no_store({"ok": False, **result}, 409)
    return _no_store({"ok": True, **result})


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
