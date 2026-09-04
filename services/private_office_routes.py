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

import logging

from flask import Blueprint, jsonify, request

from services import db
from services.private_office import access as po_access
from services.private_office import audit as po_audit
from services.private_office import facts as po_facts
from services.private_office import feature_matrix as po_matrix
from services.private_office import model as po_model
from services.private_office import office as po_office
from services.private_office import schema as po_schema
from services.private_office import status as po_status
from services.private_office import tiers as po_tiers

#: The capability every member-facing route in this pack depends on. Named once
#: so the gate and the product state can never drift onto different feature ids.
FACTS_FEATURE_ID = "private_facts"

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
    # overview a way to read the store without going through the store.
    domains: list = []
    if trustworthy and po_matrix.is_entitled(
        FACTS_FEATURE_ID, resolved.get("effective_tier")
    ):
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
