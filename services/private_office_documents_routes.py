"""HTTP surface for Document Intelligence — the Private Office vault.

``GET  /api/private-office/documents``
    The member's own documents, newest first, with each one's truthful
    extraction state. The payload also carries the capability's provider
    picture (deterministic text extraction implemented; OCR/PDF awaiting a
    provider) so a screen never has to invent that sentence.

``POST /api/private-office/documents``
    Multipart upload into the vault. Validated (type allowlist, size cap),
    deduplicated by content hash, stored privately, and processed eagerly —
    the response already says what extraction found or why it could not look.

``GET  /api/private-office/documents/<id>``
    One document with its claims, PROPOSED first.

``GET  /api/private-office/documents/<id>/content``
    The stored bytes, streamed to the owner and nobody else. ``no-store``:
    a cached private document is a leaked private document.

``DELETE /api/private-office/documents/<id>``
    Removes the content and retires the row.

``POST /api/private-office/claims/<id>/review``
    ``{"decision": "accept"|"reject"}`` — the deliberate act that turns a
    proposed claim into a private fact (accept) or records that it is wrong
    (reject). This is the only path from extraction to the fact store.

Every route runs the shared entry: session auth, the server-side feature gate
on ``private_office.document.extraction``, and the Office second lock. The
gate helpers are imported from the canonical entitlement pack rather than
copied — one implementation of the refusal translation, everywhere.
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, request

from services import private_office_routes as po_http
from services.private_office import audit as po_audit
from services.private_office import documents as po_documents

DOCUMENTS_FEATURE_ID = "private_office.document.extraction"

LOGGER = logging.getLogger(__name__)

private_office_documents_blueprint = Blueprint("private_office_documents", __name__)

#: Truthful capability edges, stated once for every payload that renders them.
PROVIDER_STATUS = {
    "text_extraction": "implemented",
    "text_formats": sorted(po_documents.EXTRACTABLE_EXTENSIONS),
    "ocr_extraction": "provider_required",
    "ocr_note": (
        "PDF and image text extraction requires an OCR provider that is not "
        "integrated. Those files are stored and readable; nothing is extracted "
        "from them."
    ),
}


def _entry():
    """Auth + tier gate + second lock shared by every documents route."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store({"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, DOCUMENTS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


@private_office_documents_blueprint.route(
    "/api/private-office/documents", methods=["GET"])
def api_private_office_documents_list():
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        rows = po_documents.list_documents(cur, owner_user_id=user["user_id"])
        po_audit.record(
            cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
            action=po_audit.ACTION_DOCUMENT_READ, object_type="DOCUMENT_LIST",
            purpose="user_request", result_count=len(rows),
        )
        return rows

    try:
        rows = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_DOCUMENTS_LIST_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your documents just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "documents": [po_documents.public_view(row) for row in rows],
        "count": len(rows),
        "provider_status": PROVIDER_STATUS,
        "limits": {
            "max_bytes": po_documents.MAX_DOCUMENT_BYTES,
            "allowed_extensions": sorted(po_documents.ALLOWED_EXTENSIONS),
        },
    })


@private_office_documents_blueprint.route(
    "/api/private-office/documents", methods=["POST"])
def api_private_office_documents_upload():
    user, refusal = _entry()
    if refusal:
        return refusal

    upload = request.files.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return po_http._no_store(
            {"ok": False, "message": "Attach the document as the 'file' field."}, 400)

    content = upload.read(po_documents.MAX_DOCUMENT_BYTES + 1)
    try:
        po_documents.validate_upload(upload.filename, len(content))
    except po_documents.PrivateDocumentRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)

    title = (request.form.get("title") or "").strip()
    domain = (request.form.get("domain") or "").strip()
    sensitivity = (request.form.get("sensitivity") or "").strip()

    def work(cur):
        stored = po_documents.store_document(
            cur, owner_user_id=user["user_id"], filename=upload.filename,
            content=content, title=title, domain=domain or None,
            sensitivity=sensitivity or None, actor_user_id=user["user_id"],
        )
        if stored.get("duplicate"):
            outcome = {"extraction_state": stored.get("extraction_state"),
                       "extraction_note": stored.get("extraction_note"),
                       "claims_proposed": 0, "duplicate": True}
        else:
            processed = po_documents.process_document(
                cur, owner_user_id=user["user_id"], document_id=stored["id"],
                content=content, actor_user_id=user["user_id"],
            )
            outcome = {**processed, "duplicate": False}
            stored = po_documents.get_document(
                cur, owner_user_id=user["user_id"], document_id=stored["id"])
        claims = po_documents.list_claims(
            cur, owner_user_id=user["user_id"], document_id=stored["id"])
        return stored, outcome, claims

    try:
        stored, outcome, claims = po_http._with_cursor(work)
    except po_documents.PrivateDocumentRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_DOCUMENTS_UPLOAD_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not store your document just now."}, 503)

    return po_http._no_store({
        "ok": True,
        "document": po_documents.public_view(stored),
        "duplicate": bool(outcome.get("duplicate")),
        "extraction": {
            "state": stored.get("extraction_state") or outcome.get("extraction_state") or "",
            "note": stored.get("extraction_note") or outcome.get("extraction_note") or "",
            "claims_proposed": int(outcome.get("claims_proposed") or 0),
        },
        "claims": claims,
    }, 201)


@private_office_documents_blueprint.route(
    "/api/private-office/documents/<int:document_id>", methods=["GET"])
def api_private_office_document_detail(document_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        document = po_documents.get_document(
            cur, owner_user_id=user["user_id"], document_id=document_id)
        claims = [] if document is None else po_documents.list_claims(
            cur, owner_user_id=user["user_id"], document_id=document_id)
        if document is not None:
            po_audit.record(
                cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
                action=po_audit.ACTION_DOCUMENT_READ, object_type="DOCUMENT",
                object_id=str(document_id), purpose="user_request",
            )
        return document, claims

    try:
        document, claims = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_DOCUMENTS_DETAIL_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not load your document just now."}, 503)

    if document is None:
        return po_http._no_store({"ok": False, "message": "Document not found."}, 404)
    return po_http._no_store({
        "ok": True,
        "document": po_documents.public_view(document),
        "claims": claims,
        "provider_status": PROVIDER_STATUS,
    })


@private_office_documents_blueprint.route(
    "/api/private-office/documents/<int:document_id>/content", methods=["GET"])
def api_private_office_document_content(document_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        document = po_documents.get_document(
            cur, owner_user_id=user["user_id"], document_id=document_id)
        if document is not None:
            po_audit.record(
                cur, actor_user_id=user["user_id"], owner_user_id=user["user_id"],
                action=po_audit.ACTION_DOCUMENT_READ, object_type="DOCUMENT_CONTENT",
                object_id=str(document_id), purpose="user_request",
            )
        return document

    try:
        document = po_http._with_cursor(work)
        if document is None:
            return po_http._no_store({"ok": False, "message": "Document not found."}, 404)
        content = po_documents.fetch_content(document)
    except po_documents.PrivateDocumentRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 404)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_DOCUMENTS_CONTENT_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not read your document just now."}, 503)

    response = Response(content, mimetype=document["mime_type"] or "application/octet-stream")
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    response.headers["Content-Disposition"] = (
        f'inline; filename="{po_documents.media_storage.safe_media_name(document["original_name"] or "document")}"')
    return response


@private_office_documents_blueprint.route(
    "/api/private-office/documents/<int:document_id>", methods=["DELETE"])
def api_private_office_document_delete(document_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return po_documents.delete_document(
            cur, owner_user_id=user["user_id"], document_id=document_id,
            actor_user_id=user["user_id"])

    try:
        removed = po_http._with_cursor(work)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_DOCUMENTS_DELETE_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not delete your document just now."}, 503)

    if not removed:
        return po_http._no_store({"ok": False, "message": "Document not found."}, 404)
    return po_http._no_store({"ok": True, "deleted": True, "document_id": document_id})


@private_office_documents_blueprint.route(
    "/api/private-office/claims/<int:claim_id>/review", methods=["POST"])
def api_private_office_claim_review(claim_id: int):
    user, refusal = _entry()
    if refusal:
        return refusal

    body = request.get_json(silent=True) or {}
    decision = str(body.get("decision") or "").strip().lower()

    def work(cur):
        return po_documents.review_claim(
            cur, owner_user_id=user["user_id"], claim_id=claim_id,
            decision=decision, actor_user_id=user["user_id"])

    try:
        outcome = po_http._with_cursor(work)
    except po_documents.PrivateDocumentRejected as exc:
        return po_http._no_store({"ok": False, "message": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_DOCUMENTS_REVIEW_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not record your review just now."}, 503)

    return po_http._no_store({"ok": True, **outcome})


def register(app) -> None:
    app.register_blueprint(private_office_documents_blueprint)
