"""HTTP surface for the canonical fact store's read model.

``GET  /api/private-office/facts/overview``
    Counts. Exact ones for what the database stores, observed ones for what
    only exists once a row is in memory, and a ``complete`` flag saying whether
    the second kind describes the whole store.

``GET  /api/private-office/facts/list``
    One page, with ``has_more`` and ``next_offset``. Filterable by domain,
    fact type and subject.

``GET  /api/private-office/facts/<id>``
    One fact in full: value, provenance, verification, evidence with per-source
    availability, the supersession chain, and the reasons it would appear in
    the review queue.

``GET  /api/private-office/facts/<id>/evidence``
    The evidence slice of the above, for a screen that only needs the sources.

``GET  /api/private-office/facts/<id>/timeline``
    Everything that has happened across the fact's whole supersession chain,
    newest first, each event tagged with whether it happened to this row or to
    one it replaced.

``GET  /api/private-office/facts/sources``
    Every document the member's facts cite, grouped by document, ordered by how
    much rests on each.

``GET  /api/private-office/facts/conflicts``
    Disagreements nobody has settled.

``GET  /api/private-office/facts/review``
    What needs attention, ranked, each item carrying its own explanation.

``GET  /api/private-office/facts/expiring``
    Validity windows closing soon — and ones that have already closed, which
    sort to the top because they are the most overdue, not the least relevant.

Why this is a separate pack
---------------------------
``private_office_routes`` already owns ``/api/private-office/facts`` for GET and
POST. Those two stay exactly as they are: the native client in production reads
them, and the wire contract test pins their vocabulary. This pack adds the
sub-paths beneath them. Flask's ``int`` converter keeps ``/facts/<int:fact_id>``
from swallowing ``/facts/overview``, so the two surfaces coexist without either
having to know about the other.

Nothing here writes
-------------------
Every route in this file is GET, and every one of them delegates to
``read_model``, which is deliberately absent from the write boundary's
``WRITER_MODULES``. The one thing these routes do change is the audit log,
through the canonical recorder — a read of a private store is itself an event
worth keeping, and it is the only reason any of these handlers takes a cursor
that can write at all.

Bounds are forwarded, never re-decided
--------------------------------------
Every function in the read model truncates, and every one of them says so:
``complete``, ``has_more``, ``truncated``, ``unavailable``. These handlers pass
those flags through untouched. A route that dropped one would turn "the ceiling
bit" into "that is everything you have", which is the single failure mode this
whole layer is arranged to prevent — and it would do it silently, because a
short list looks exactly like a small store.

Clamping is likewise left to the module. Parsing ``?limit=`` here and enforcing
a ceiling here would create a second opinion about the maximum, and the two
would drift the first time one of them was tuned. The handlers pass the raw
value down; ``read_model._clamp`` rejects what it does not like.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services.private_office import audit as po_audit
from services.private_office import model as po_model
from services.private_office import read_model as po_read
from services.private_office import review as po_review

LOGGER = logging.getLogger(__name__)

private_office_facts_blueprint = Blueprint("private_office_facts", __name__)

#: The same matrix row, and therefore the same kill switch, as the fact routes
#: in the pack this one extends. Imported rather than restated: a second literal
#: would let this surface stay up after ``PRIVATE_FACTS_ENABLED=false`` had
#: taken the other one down, which is the partial-off the kill-switch suite
#: exists to prevent.
FACTS_FEATURE_ID = po_http.FACTS_FEATURE_ID

#: Truthful capability edges, in the shape the other Private Office packs use.
PROVIDER_STATUS = {
    "source": "private_office_facts",
    "inference": "none",
    "note": (
        "Every count, list and timeline here is computed from the member's own "
        "recorded facts and the evidence they cite. Nothing is inferred, and "
        "nothing is fetched from outside the Private Office."
    ),
}


def _entry():
    """Auth + tier gate + second lock, shared by every route in this pack.

    Identical in shape to the relationships pack's ``_entry`` and delegating to
    the same helpers. The Office lock is applied here rather than per-route so a
    route added later cannot be the one that forgets it.
    """
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store(
            {"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, FACTS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


def _unavailable(message: str):
    """The 503 shape.

    Deliberately carries no data key. A failure payload that also contained
    ``"items": []`` would let a client render an empty state over an error —
    the member is told they have nothing on the one occasion we cannot tell
    whether they have anything at all.
    """
    return po_http._no_store(
        {"ok": False, "state": "unavailable", "message": message}, 503)


def _int_arg(name: str):
    """A query integer, or ``None`` when absent or unparseable.

    ``None`` rather than a substituted default, because the read model's own
    defaults are the canonical ones and passing a value through here would
    quietly become a second place they are declared.
    """
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _domains_arg():
    """``?domain=`` as a validated list, or a 400 refusal.

    An unrecognised domain is answered as a client error rather than dropped.
    Silently ignoring the filter would return the member's whole store to a
    request that asked for one heading — the same rule the existing facts list
    already follows, restated here because this pack accepts several.
    """
    raw = [part.strip() for part in
           (request.args.get("domain") or "").split(",") if part.strip()]
    if not raw:
        return None, None
    normalized = []
    for value in raw:
        known = po_model.normalize_domain(value)
        if not known:
            return None, po_http._no_store(
                {"ok": False, "message": "Unknown domain.",
                 "domains": list(po_model.DOMAINS)}, 400)
        normalized.append(known)
    return normalized, None


def _read(user, work, *, failure_log: str, message: str):
    """Run a cursor read, translating any failure into the 503 shape."""
    try:
        return po_http._with_cursor(work), None
    except Exception:  # noqa: BLE001
        LOGGER.exception(failure_log)
        return None, _unavailable(message)


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

@private_office_facts_blueprint.route(
    "/api/private-office/facts/overview", methods=["GET"])
def api_private_office_facts_overview():
    """Exact counts, observed counts, and the boundary between them.

    The ``observed`` block is forwarded whole, ``complete`` included. A client
    rendering "38% of your facts need review" is only entitled to do so when
    that flag is true; stripping it here would leave every such percentage
    silently computed over the first ``overview_scan`` rows.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_read.facts_overview(cur, owner_user_id=user["user_id"])
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="FACT_OVERVIEW",
            purpose="user_request", result_count=int(data.get("total") or 0),
        )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_OVERVIEW_FAILED",
        message="We could not summarise your information just now.")
    if failed:
        return failed
    return po_http._no_store(
        {"ok": True, **data, "provider_status": PROVIDER_STATUS})


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@private_office_facts_blueprint.route(
    "/api/private-office/facts/list", methods=["GET"])
def api_private_office_facts_list():
    """One page of facts, with an honest answer about whether there are more."""
    user, refusal = _entry()
    if refusal:
        return refusal

    domains, bad_domain = _domains_arg()
    if bad_domain:
        return bad_domain

    fact_types = [part.strip() for part in
                  (request.args.get("fact_type") or "").split(",")
                  if part.strip()] or None
    subject_type = (request.args.get("subject_type") or "").strip() or None
    subject_id = (request.args.get("subject_id") or "").strip() or None
    # Superseded rows are off unless asked for. They are the member's own
    # history and they are theirs to see, but a list that mixed corrected values
    # in with current ones by default would show two answers to one question
    # with nothing on the row saying which is in force.
    include_superseded = (
        (request.args.get("include_superseded") or "").strip().lower()
        in ("1", "true", "yes"))

    def work(cur):
        data = po_read.facts_page(
            cur, owner_user_id=user["user_id"], subject_type=subject_type,
            subject_id=subject_id, fact_types=fact_types, domains=domains,
            include_superseded=include_superseded,
            limit=_int_arg("limit") or po_read.DEFAULT_PAGE,
            offset=_int_arg("offset") or 0)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="DOMAIN",
            object_id=",".join(domains) if domains else "ALL",
            purpose="user_request", result_count=len(data["items"]),
        )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_LIST_FAILED",
        message="We could not load your information just now.")
    if failed:
        return failed
    return po_http._no_store({"ok": True, **data})


# ---------------------------------------------------------------------------
# One fact
# ---------------------------------------------------------------------------

@private_office_facts_blueprint.route(
    "/api/private-office/facts/<int:fact_id>", methods=["GET"])
def api_private_office_fact_detail(fact_id: int):
    """Everything held about one fact.

    A fact belonging to somebody else and a fact that was never issued get the
    same 404 with the same message. The read model already refuses to
    distinguish them; the route must not reintroduce the difference, because a
    distinguishable "forbidden" confirms that an id exists, which is the whole
    of what an enumerating caller is trying to learn.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_read.fact_detail(
            cur, owner_user_id=user["user_id"], fact_id=fact_id)
        if data is not None:
            po_audit.record(
                cur, actor_user_id=user["user_id"],
                owner_user_id=user["user_id"],
                action=po_audit.ACTION_FACT_READ, object_type="FACT",
                object_id=str(fact_id), purpose="user_request", result_count=1,
            )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_DETAIL_FAILED",
        message="We could not load this information just now.")
    if failed:
        return failed
    if data is None:
        return po_http._no_store({"ok": False, "message": "Fact not found."}, 404)
    return po_http._no_store(
        {"ok": True, "fact": data, "provider_status": PROVIDER_STATUS})


@private_office_facts_blueprint.route(
    "/api/private-office/facts/<int:fact_id>/evidence", methods=["GET"])
def api_private_office_fact_evidence(fact_id: int):
    """The sources behind one fact, and whether each is still there.

    Sliced out of ``fact_detail`` rather than queried separately. A second path
    to the same answer would be a second opinion about ``available``, and the
    two would disagree the first time either changed — with the evidence screen
    and the fact screen each able to be the one that is right.

    ``available`` is three-valued and stays that way over the wire: true, false,
    or null for a citation this build cannot parse. A client must not read null
    as false; the ``missing`` list below is the only thing that means "looked,
    and it is gone".
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_read.fact_detail(
            cur, owner_user_id=user["user_id"], fact_id=fact_id)
        if data is not None:
            po_audit.record(
                cur, actor_user_id=user["user_id"],
                owner_user_id=user["user_id"],
                action=po_audit.ACTION_FACT_READ, object_type="FACT_EVIDENCE",
                object_id=str(fact_id), purpose="user_request",
                result_count=len(data.get("evidence") or []),
            )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_EVIDENCE_FAILED",
        message="We could not load these sources just now.")
    if failed:
        return failed
    if data is None:
        return po_http._no_store({"ok": False, "message": "Fact not found."}, 404)
    return po_http._no_store({
        "ok": True,
        "fact_id": data["fact_id"],
        "evidence": data["evidence"],
        "count": data["evidence_count"],
        "missing": data["missing_sources"],
        "verification": data["verification"],
        "provider_status": PROVIDER_STATUS,
    })


@private_office_facts_blueprint.route(
    "/api/private-office/facts/<int:fact_id>/timeline", methods=["GET"])
def api_private_office_fact_timeline(fact_id: int):
    """The fact's whole history, across every correction that led to it.

    A fact the caller does not own yields the same empty timeline as one that
    does not exist — 200 with no events rather than 404, matching what the read
    model returns, so that the response is identical in both cases down to the
    status line.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_read.fact_timeline(
            cur, owner_user_id=user["user_id"], fact_id=fact_id,
            limit=_int_arg("limit") or po_read.MAX_TIMELINE)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="FACT_TIMELINE",
            object_id=str(fact_id), purpose="user_request",
            result_count=len(data["events"]),
        )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_TIMELINE_FAILED",
        message="We could not load this history just now.")
    if failed:
        return failed
    return po_http._no_store(
        {"ok": True, **data, "provider_status": PROVIDER_STATUS})


# ---------------------------------------------------------------------------
# Cross-cutting views
# ---------------------------------------------------------------------------

@private_office_facts_blueprint.route(
    "/api/private-office/facts/sources", methods=["GET"])
def api_private_office_facts_sources():
    """Every document the member's facts cite, grouped by document.

    ``unavailable`` is forwarded when the evidence table itself could not be
    read. That is a 200 carrying an admission rather than a 503, because the
    rest of the answer is sound and the distinction the member needs is between
    "you cite nothing" and "we could not check what you cite" — which is exactly
    what the flag says.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_read.source_index(
            cur, owner_user_id=user["user_id"],
            limit=_int_arg("limit") or po_read.MAX_SOURCES)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="FACT_SOURCES",
            purpose="user_request", result_count=len(data["sources"]),
        )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_SOURCES_FAILED",
        message="We could not load your sources just now.")
    if failed:
        return failed
    return po_http._no_store(
        {"ok": True, **data, "count": len(data["sources"]),
         "provider_status": PROVIDER_STATUS})


@private_office_facts_blueprint.route(
    "/api/private-office/facts/conflicts", methods=["GET"])
def api_private_office_facts_conflicts():
    """Disagreements nobody has settled."""
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        data = po_read.open_conflicts(
            cur, owner_user_id=user["user_id"],
            limit=_int_arg("limit") or po_read.MAX_CONFLICT_GROUPS)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="FACT_CONFLICTS",
            purpose="user_request", result_count=len(data["conflicts"]),
        )
        return data

    data, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_CONFLICTS_FAILED",
        message="We could not check for conflicts just now.")
    if failed:
        return failed
    return po_http._no_store(
        {"ok": True, **data, "count": len(data["conflicts"]),
         "provider_status": PROVIDER_STATUS})


@private_office_facts_blueprint.route(
    "/api/private-office/facts/review", methods=["GET"])
def api_private_office_facts_review():
    """What needs attention, ranked, each item carrying its own explanation.

    ``reasons`` are published alongside the items so a screen can offer the
    filter without hardcoding the vocabulary, and so an item's placement is
    always answerable — a ranked list whose ordering cannot be explained is one
    the member has to take on trust.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    wanted = [part.strip().upper() for part in
              (request.args.get("reason") or "").split(",") if part.strip()]
    unknown = [r for r in wanted if r not in po_model.REVIEW_REASONS]
    if unknown:
        # Same rule as an unknown domain: a filter we cannot honour is a client
        # error, not a licence to return the unfiltered queue.
        return po_http._no_store(
            {"ok": False, "message": "Unknown review reason.",
             "reasons": list(po_model.REVIEW_REASONS)}, 400)

    def work(cur):
        items = po_review.review_queue(
            cur, owner_user_id=user["user_id"],
            limit=_int_arg("limit") or po_review.MAX_REVIEW_ITEMS,
            reasons=wanted or None)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="FACT_REVIEW",
            purpose="user_request", result_count=len(items),
        )
        return items

    items, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_REVIEW_FAILED",
        message="We could not work out what needs your attention just now.")
    if failed:
        return failed
    return po_http._no_store({
        "ok": True,
        "items": items,
        "count": len(items),
        "reasons": list(po_model.REVIEW_REASONS),
        "provider_status": PROVIDER_STATUS,
    })


@private_office_facts_blueprint.route(
    "/api/private-office/facts/expiring", methods=["GET"])
def api_private_office_facts_expiring():
    """Validity windows closing soon, and ones that already closed.

    The already-closed items are included and sort first. A fact still marked
    active whose window shut last March is asserting something it has itself
    declared out of date, so dropping it for being past the horizon would remove
    exactly the rows most in need of the member's attention.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        items = po_read.expiring_facts(
            cur, owner_user_id=user["user_id"],
            within_days=_int_arg("days") or po_read.DEFAULT_EXPIRING_DAYS,
            limit=_int_arg("limit") or po_read.MAX_EXPIRING)
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_FACT_READ, object_type="FACT_EXPIRING",
            purpose="user_request", result_count=len(items),
        )
        return items

    items, failed = _read(
        user, work, failure_log="PRIVATE_FACTS_EXPIRING_FAILED",
        message="We could not check what is expiring just now.")
    if failed:
        return failed
    return po_http._no_store({
        "ok": True,
        "items": items,
        "count": len(items),
        "already_expired": sum(1 for i in items if i["already_expired"]),
        "provider_status": PROVIDER_STATUS,
    })


def register(app) -> None:
    app.register_blueprint(private_office_facts_blueprint)
