"""HTTP surface for the structured record store — templates, records, reveal.

``GET  /api/private-office/record-templates``
    The server-owned template manifest: contract version, field kinds, mask
    strategies, reference lists, and every template's field schema. This is
    what lets a native form be *rendered* rather than *hardcoded*, and it is
    why the client never needs a copy of the validation rules. A client reads
    ``contract_version`` first and refuses a manifest it does not understand.

``GET  /api/private-office/record-domains``
    The thirteen information-architecture domains with their i18n label keys
    and how many templates and records sit in each. The Private Facts home
    screen renders from this — it is one call because a heading count and the
    list under it that disagree is a bug the user sees.

``GET  /api/private-office/structured-records``
    The member's records: masked summaries only, filterable by template, IA
    domain, verification state, needs-review and expiry. No field values.

``POST /api/private-office/structured-records``
    Create one record from a template key and a payload of field paths.
    Validation, duplicate detection and idempotency belong to the store; this
    route translates their answers into status codes.

``GET  /api/private-office/structured-records/<id>``
    One record: envelope plus fields in *masked* form. There is no query
    parameter that returns a raw value, because the store has no argument that
    would produce one.

``PATCH  /api/private-office/structured-records/<id>``
    Patch fields or envelope metadata under optimistic concurrency.

``DELETE /api/private-office/structured-records/<id>``
    Archives. Nothing here destroys a record — see the handler.

``GET  /api/private-office/structured-records/<id>/history``
    Who changed what, and when. Changed paths, never changed values.

``POST /api/private-office/structured-records/<id>/reveal``
    The step-up read. Requires the passcode again, in this request, and hands
    back exactly one field of one record.

``GET  /api/private-office/structured-records/search``
``GET  /api/private-office/structured-records/expiring``
    Masked search over the field index, and the expiry sweep the reminder
    surfaces render.

On the prefix: ``/structured-records`` and not ``/records``, because Operations
already owns ``/api/private-office/records/<view>`` for its six primitives.
Werkzeug would match ``/records/1`` and ``/records/search`` against that rule —
same shape, registered first — and this pack's routes would simply never be
reached. The failure is silent and it is a 404 from the wrong handler, which is
the kind of thing that costs an afternoon. If Operations ever moves to
``/operations/<view>``, this prefix can follow; until then it stays distinct.

Two properties are worth naming because they are the ones a reviewer should
check rather than trust.

**Nothing here decrypts except the reveal route.** Every other handler calls a
store function that has no parameter capable of returning a restricted value.
That is not a convention this module maintains; it is a shape the store has.

**A record that is not yours and a record that does not exist answer
identically** — 404, same body. The store returns ``None``/raises the same
refusal for both, and this module does not add a distinguishing branch. An
attacker enumerating ids learns nothing, including whether they guessed right.

The gate helpers are imported from the canonical entitlement pack rather than
copied, for the same reason the documents pack imports them: one implementation
of the refusal translation, everywhere.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services.private_office import record_template_catalog as po_catalog
from services.private_office import record_templates as po_templates
from services.private_office import security as po_security
from services.private_office import structured_records as po_store

RECORDS_FEATURE_ID = "private_office.records"

LOGGER = logging.getLogger(__name__)

private_office_structured_records_blueprint = Blueprint(
    "private_office_structured_records", __name__
)

#: One page. The store bounds harder (``structured_records.MAX_LIMIT``); the
#: route states its own ceiling so the contract is readable from the endpoint.
MAX_RECORDS_PAGE = 100

#: Envelope fields a member may supply on create. An allowlist, not a
#: passthrough. Three absences are deliberate:
#:
#: ``verification_state`` — a client that could name its own verification state
#:     could label its own typing "user verified", which is the one claim the
#:     whole provenance model exists to keep honest. The store pins it from the
#:     actor kind, and the actor kind here is always the signed-in member.
#: ``provenance_type`` / ``source_type`` — same reason, one level down: these
#:     say where a value came from, and a client asserting DOCUMENT_EXTRACTED
#:     for something a person typed would corrupt the only signal that
#:     distinguishes an extraction needing review from a fact somebody checked.
#: ``office_id`` — resolved from the session, never accepted. It is the tenant
#:     boundary; accepting it from a body would make the boundary a suggestion.
_CREATE_BODY_FIELDS = (
    "title", "description", "tags", "evidence_ids",
    "effective_date", "review_at", "reminder_policy",
)

#: Envelope fields a member may patch. ``status`` is here and
#: ``verification_state`` is not, for the reason above.
_PATCH_BODY_FIELDS = _CREATE_BODY_FIELDS + ("status",)


def _entry():
    """Auth + tier gate + second lock, shared by every route in this pack."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, RECORDS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


def _unavailable(message: str):
    return po_http._no_store(
        {"ok": False, "state": "unavailable", "message": message}, 503)


def _not_found():
    """The one 404 body this module has.

    Deliberately a function rather than a literal at each call site: two 404s
    that drifted apart in wording would re-create the existence oracle that
    returning 404 for both cases was meant to close.
    """
    return po_http._no_store(
        {"ok": False, "state": "not_found", "message": "No such record."}, 404)


def _page_args():
    """``(limit, offset)`` from the query string, clamped, never raising."""
    try:
        limit = int(request.args.get("limit") or MAX_RECORDS_PAGE)
    except (TypeError, ValueError):
        limit = MAX_RECORDS_PAGE
    try:
        offset = int(request.args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, MAX_RECORDS_PAGE)), max(0, offset)


def _record_id(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _body():
    """``(body, refusal)``. A non-object body is a 400, not a silent ``{}``."""
    body = request.get_json(silent=True)
    if body is None:
        return {}, None
    if not isinstance(body, dict):
        return None, po_http._no_store(
            {"ok": False, "message": "Invalid request body."}, 400)
    return body, None


# ---------------------------------------------------------------------------
# Templates and domains — what the client renders forms from
# ---------------------------------------------------------------------------

@private_office_structured_records_blueprint.route(
    "/api/private-office/record-templates", methods=["GET"])
def api_private_office_record_templates():
    """The template manifest, optionally narrowed to one IA domain.

    Gated like everything else. The manifest holds no member data, but it does
    describe the shape of a member's most sensitive records, and an unauthenticated
    map of "here are the fields a passport record has, here is which one is
    encrypted" is a reconnaissance document.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    ia_domain = (request.args.get("domain") or "").strip().lower()
    if ia_domain and ia_domain not in po_catalog.IA_DOMAIN_KEYS:
        return po_http._no_store(
            {"ok": False, "message": "Unknown domain.",
             "domains": list(po_catalog.IA_DOMAIN_KEYS)}, 400)

    try:
        manifest = po_templates.manifest(ia_domain=ia_domain or None)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORD_TEMPLATES_FAILED")
        return _unavailable("We could not load the record types just now.")

    return po_http._no_store({"ok": True, **manifest})


@private_office_structured_records_blueprint.route(
    "/api/private-office/record-domains", methods=["GET"])
def api_private_office_record_domains():
    """The thirteen domains, their label keys, and this member's counts.

    ``label_key`` plus ``label_fallback`` rather than a translated string: the
    server does not know the member's locale and guessing one would put an
    English heading on a French screen. The fallback exists so a client missing
    a translation renders a word rather than a key.
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        po_store.ensure_structured_schema(cur)
        counts = po_store.domain_counts(cur, owner_user_id=user["user_id"])
        return counts

    try:
        counts = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORD_DOMAINS_FAILED")
        return _unavailable("We could not load your record categories just now.")

    templates_by_domain: dict[str, int] = {}
    for template in po_templates.latest_templates():
        templates_by_domain[template.ia_domain] = (
            templates_by_domain.get(template.ia_domain, 0) + 1)

    domains = [
        {
            "key": key,
            "label_key": label_key,
            "label_fallback": fallback,
            "template_count": templates_by_domain.get(key, 0),
            "record_count": int(counts.get(key, 0)),
        }
        for key, label_key, fallback in po_catalog.IA_DOMAINS
    ]
    return po_http._no_store({
        "ok": True,
        "contract_version": po_templates.CONTRACT_VERSION,
        "domains": domains,
        "total": sum(d["record_count"] for d in domains),
    })


# ---------------------------------------------------------------------------
# Records — list, create, read, patch, archive
# ---------------------------------------------------------------------------

@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records", methods=["GET"])
def api_private_office_records_list():
    """Masked summaries. No field values cross this route at all."""
    user, refusal = _entry()
    if refusal:
        return refusal

    limit, offset = _page_args()
    template_key = (request.args.get("template") or "").strip() or None
    ia_domain = (request.args.get("domain") or "").strip().lower() or None
    verification = (request.args.get("verification") or "").strip().upper() or None
    expiring_before = (request.args.get("expiring_before") or "").strip() or None
    needs_review = str(request.args.get("needs_review") or "").strip().lower() in (
        "1", "true", "yes")

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.list_records(
            cur,
            owner_user_id=user["user_id"],
            template_key=template_key,
            ia_domain=ia_domain,
            verification_state=verification,
            needs_review_only=needs_review,
            expiring_before=expiring_before,
            limit=limit,
            offset=offset,
            actor_user_id=user["user_id"],
            purpose="user_request",
        )

    try:
        listing = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_LIST_FAILED")
        return _unavailable("We could not load your records just now.")

    return po_http._no_store({"ok": True, **listing})


@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records", methods=["POST"])
def api_private_office_records_create():
    """Create one record from a template key and a payload of field paths."""
    user, refusal = _entry()
    if refusal:
        return refusal
    body, bad = _body()
    if bad:
        return bad

    template_key = str(body.get("template_key") or "").strip()
    if not template_key:
        return po_http._no_store(
            {"ok": False, "message": "A record type is required."}, 400)

    payload = body.get("payload")
    if payload is not None and not isinstance(payload, dict):
        return po_http._no_store(
            {"ok": False, "message": "Invalid payload."}, 400)

    fields = {
        name: body[name] for name in _CREATE_BODY_FIELDS
        if name in body and body[name] is not None
    }

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.create_record(
            cur,
            owner_user_id=user["user_id"],
            template_key=template_key,
            template_version=body.get("template_version"),
            payload=payload or {},
            idempotency_key=str(body.get("idempotency_key") or ""),
            allow_duplicate=bool(body.get("allow_duplicate")),
            actor_user_id=user["user_id"],
            actor_kind=po_store.ACTOR_USER,
            purpose="user_request",
            **fields,
        )

    try:
        written = po_http._with_cursor(work)
    except po_store.StructuredRecordRejected as exc:
        # The store refuses for reasons a member can act on — an unknown
        # template, a restricted field with no key configured — so the message
        # is the store's rather than a generic one.
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_CREATE_FAILED")
        return _unavailable("We could not save that just now.")

    status = written.get("status")

    if status == po_store.STATUS_INVALID:
        # 422, not 400: the request was well-formed and the *content* failed
        # the template's rules. The client renders these against the fields
        # that produced them, which needs path-level errors, not a sentence.
        return po_http._no_store(
            {"ok": False, "state": "invalid", "errors": written.get("errors") or [],
             "message": "Some entries need attention."}, 422)

    if status == po_store.STATUS_DUPLICATE:
        # 409 and the member decides. The store deliberately refuses rather
        # than merging, because two passports with the same number are either a
        # mistake or a renewal, and only the member knows which.
        return po_http._no_store(
            {"ok": False, "state": "duplicate",
             "duplicates": written.get("duplicates") or [],
             "message": "You may already have recorded this.",
             "retry_with": {"allow_duplicate": True}}, 409)

    return po_http._no_store(
        {
            "ok": True,
            "status": status,
            "record_id": written.get("record_id"),
            "record": written.get("record"),
        },
        # An idempotent replay is 200: nothing was created this time, and a
        # client retrying after a dropped response should be able to tell.
        201 if status == po_store.STATUS_CREATED else 200,
    )


@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/<record_id>", methods=["GET"])
def api_private_office_records_get(record_id):
    """One record, fields in masked form."""
    user, refusal = _entry()
    if refusal:
        return refusal
    rid = _record_id(record_id)
    if rid <= 0:
        return _not_found()

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.get_record(
            cur, owner_user_id=user["user_id"], record_id=rid,
            actor_user_id=user["user_id"], purpose="user_request",
        )

    try:
        record = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_READ_FAILED")
        return _unavailable("We could not load that record just now.")

    if record is None:
        return _not_found()
    return po_http._no_store({"ok": True, "record": record})


@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/<record_id>", methods=["PATCH"])
def api_private_office_records_patch(record_id):
    """Patch fields or envelope metadata under optimistic concurrency.

    A field path present in ``payload`` with an empty value *clears* that
    field; a path absent from ``payload`` is left alone. That distinction is
    the store's and is preserved here by passing the body's payload through
    untouched — normalising empties away at this layer would make clearing a
    field impossible over HTTP.
    """
    user, refusal = _entry()
    if refusal:
        return refusal
    rid = _record_id(record_id)
    if rid <= 0:
        return _not_found()
    body, bad = _body()
    if bad:
        return bad

    payload = body.get("payload")
    if payload is not None and not isinstance(payload, dict):
        return po_http._no_store({"ok": False, "message": "Invalid payload."}, 400)

    fields = {name: body[name] for name in _PATCH_BODY_FIELDS if name in body}
    expected = body.get("expected_revision")

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.update_record(
            cur,
            owner_user_id=user["user_id"],
            record_id=rid,
            payload=payload,
            expected_revision=(None if expected is None else int(expected)),
            reason_code=str(body.get("reason_code") or ""),
            actor_user_id=user["user_id"],
            actor_kind=po_store.ACTOR_USER,
            purpose="user_request",
            **fields,
        )

    try:
        written = po_http._with_cursor(work)
    except po_store.StructuredRecordDenied:
        return _not_found()
    except po_store.StructuredRecordConflict as exc:
        # 409 with the authoritative revision, so a client can re-read and
        # re-submit rather than guess. The message names the number it found
        # because "someone else changed this" without saying what it is now
        # leaves the client no way forward but a blind retry loop.
        return po_http._no_store(
            {"ok": False, "state": "conflict", "message": str(exc)}, 409)
    except po_store.StructuredRecordRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except (TypeError, ValueError):
        return po_http._no_store(
            {"ok": False, "message": "Invalid expected_revision."}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_PATCH_FAILED")
        return _unavailable("We could not save that just now.")

    if written.get("status") == po_store.STATUS_INVALID:
        return po_http._no_store(
            {"ok": False, "state": "invalid", "errors": written.get("errors") or [],
             "message": "Some entries need attention."}, 422)

    return po_http._no_store({
        "ok": True,
        "status": written.get("status"),
        "record": written.get("record"),
    })


@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/<record_id>", methods=["DELETE"])
def api_private_office_records_archive(record_id):
    """Archives. Nothing on this surface destroys a record.

    DELETE is the verb a client already knows for "remove this from my list",
    and it is answered by an archive because the alternative — a real delete —
    would destroy the audit trail and the revision history along with the row.
    The response says ``archived`` rather than ``deleted`` so nothing downstream
    can believe the data is gone.
    """
    user, refusal = _entry()
    if refusal:
        return refusal
    rid = _record_id(record_id)
    if rid <= 0:
        return _not_found()
    body, bad = _body()
    if bad:
        return bad

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.archive_record(
            cur, owner_user_id=user["user_id"], record_id=rid,
            reason_code=str(body.get("reason_code") or ""),
            actor_user_id=user["user_id"], actor_kind=po_store.ACTOR_USER,
            purpose="user_request",
        )

    try:
        written = po_http._with_cursor(work)
    except po_store.StructuredRecordDenied:
        return _not_found()
    except po_store.StructuredRecordRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_ARCHIVE_FAILED")
        return _unavailable("We could not archive that just now.")

    return po_http._no_store({
        "ok": True,
        "status": "archived",
        "record": written.get("record"),
    })


@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/<record_id>/history", methods=["GET"])
def api_private_office_records_history(record_id):
    """Who changed what, and when. Changed paths, never changed values."""
    user, refusal = _entry()
    if refusal:
        return refusal
    rid = _record_id(record_id)
    if rid <= 0:
        return _not_found()

    def work(cur):
        po_store.ensure_structured_schema(cur)
        if po_store.get_record(cur, owner_user_id=user["user_id"], record_id=rid,
                               audit=False) is None:
            return None
        return po_store.record_history(
            cur, owner_user_id=user["user_id"], record_id=rid)

    try:
        entries = po_http._with_cursor(work)
    except po_store.StructuredRecordDenied:
        return _not_found()
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_HISTORY_FAILED")
        return _unavailable("We could not load that history just now.")

    if entries is None:
        return _not_found()
    return po_http._no_store(
        {"ok": True, "history": entries, "count": len(entries)})


# ---------------------------------------------------------------------------
# Search and expiry
# ---------------------------------------------------------------------------

@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/search", methods=["GET"])
def api_private_office_records_search():
    """Masked search over the field index.

    What comes back is what the member is already shown: a match on ``1234``
    is a match on the four digits at the end of a masked identifier, because
    the index never held the whole number. A query shorter than two characters
    returns nothing rather than everything — the store's rule, so the first
    keystroke of a search is not a full read of every record a member owns.
    """
    user, refusal = _entry()
    if refusal:
        return refusal
    limit, _ = _page_args()
    query = request.args.get("q") or request.args.get("query") or ""

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.search_records(
            cur, owner_user_id=user["user_id"], query=query, limit=limit,
            actor_user_id=user["user_id"], purpose="user_request",
        )

    try:
        found = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_SEARCH_FAILED")
        return _unavailable("We could not search your records just now.")

    return po_http._no_store({"ok": True, **found})


@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/expiring", methods=["GET"])
def api_private_office_records_expiring():
    """Records expiring on or before a date. What the reminder surfaces read."""
    user, refusal = _entry()
    if refusal:
        return refusal
    limit, _ = _page_args()
    before = (request.args.get("before") or "").strip()
    if not before:
        return po_http._no_store(
            {"ok": False, "message": "A date is required."}, 400)

    def work(cur):
        po_store.ensure_structured_schema(cur)
        return po_store.expiring_records(
            cur, owner_user_id=user["user_id"], before=before, limit=limit)

    try:
        rows = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_EXPIRING_FAILED")
        return _unavailable("We could not load your reminders just now.")

    return po_http._no_store(
        {"ok": True, "records": rows, "count": len(rows), "before": before})


# ---------------------------------------------------------------------------
# The reveal — the only route on this surface that decrypts anything
# ---------------------------------------------------------------------------

@private_office_structured_records_blueprint.route(
    "/api/private-office/structured-records/<record_id>/reveal", methods=["POST"])
def api_private_office_records_reveal(record_id):
    """Hand back one field of one record, behind a second proof.

    POST rather than GET, and the field path is in the body rather than the
    path or query string, for one reason each: a GET is cached, logged and
    kept in history by things this application does not control, and a URL
    containing ``/reveal?field=issuance.document_number`` writes the member's
    intent into every access log between here and their device. The value
    itself would not be in that URL — but the fact that this member revealed
    their passport number at 14:02 would be.

    The passcode is required again here even though the request already carries
    a valid unlock grant. Those answer different questions: the grant says the
    Office was opened recently, which is what reading a masked list needs; this
    says the person holding the device right now knows the passcode, which is
    what handing over a passport number needs. Requiring only the grant would
    mean a stolen one bought the values behind every screen it could already
    see masked.

    One field per call, named explicitly. A bulk reveal endpoint would make the
    audit trail's most important row — "this value left storage" — a summary,
    and would let a single mistaken tap unwrap an entire record.
    """
    user, refusal = _entry()
    if refusal:
        return refusal
    rid = _record_id(record_id)
    if rid <= 0:
        return _not_found()
    body, bad = _body()
    if bad:
        return bad

    field_path = str(body.get("field_path") or "").strip()
    if not field_path:
        return po_http._no_store(
            {"ok": False, "message": "A field is required."}, 400)
    passcode = str(body.get("passcode") or "")
    if not passcode:
        return po_http._no_store(
            {"ok": False, "state": "step_up_required",
             "message": "Enter your Office passcode to reveal this."}, 401)

    def work(cur):
        po_store.ensure_structured_schema(cur)
        # The step-up runs first and independently of whether the record
        # exists. Verifying the record before the passcode would make a wrong
        # passcode answer 404 for a stranger's id and 401 for the member's
        # own — an existence oracle that costs nothing to query.
        proof = po_security.verify_step_up(cur, user["user_id"], passcode)
        if not proof.get("ok"):
            return {"step_up": proof}
        return {
            "step_up": proof,
            "field": po_store.reveal_field(
                cur, owner_user_id=user["user_id"], record_id=rid,
                field_path=field_path, step_up_verified=True,
                actor_user_id=user["user_id"], purpose="user_request",
            ),
        }

    try:
        outcome = po_http._with_cursor(work)
    except po_store.StructuredRecordDenied:
        return _not_found()
    except po_store.StructuredRecordRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_RECORDS_REVEAL_FAILED")
        return _unavailable("We could not reveal that just now.")

    proof = outcome["step_up"]
    if not proof.get("ok"):
        payload = {
            "ok": False,
            "state": "step_up_failed",
            "code": proof.get("error"),
            "message": "That passcode was not correct.",
        }
        if proof.get("retry_after_seconds"):
            payload["retry_after_seconds"] = proof["retry_after_seconds"]
            payload["message"] = "Too many attempts. Try again shortly."
        # 401, not 403: the member may retry with the right passcode. A 403
        # would tell a client this is never going to work and hide the
        # retry-after that makes the cooldown legible.
        return po_http._no_store(payload, 401)

    # The commit already happened inside `_with_cursor`, which matters here
    # more than anywhere else on this surface: the audit row recording that
    # this value left storage is written in the same transaction as the read,
    # so there is no ordering in which the value is returned and the record of
    # it returning is lost.
    return po_http._no_store({"ok": True, **outcome["field"]})


def register(app) -> None:
    app.register_blueprint(private_office_structured_records_blueprint)
