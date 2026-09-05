"""Document Intelligence — the private vault and its claim pipeline.

What this module is
-------------------
The owner-scoped document store for the Private Office: upload validation,
durable storage, deterministic text extraction, and the claim-review path by
which a document's contents become private facts. It follows the package's
standing rules:

* **Single writer.** Documents and claims are written here and nowhere else.
  A claim that is *accepted* becomes a fact via ``facts.record_fact`` — the
  one sanctioned fact writer — with ``DOCUMENT_EXTRACTED`` provenance and a
  locator pointing back into the document, and the document itself is placed
  in the capital graph through ``graph.upsert_node``. This module never
  touches those tables directly.

* **Nothing is asserted unreviewed.** Extraction *proposes* claims; only the
  member's explicit review turns a proposal into a fact. An extractor's guess
  landing directly in the fact store would put a model's (or a parser's)
  paraphrase behind the "why does PulseSoc know this?" guarantee.

* **Truthful capability edges.** Extraction here is deterministic and covers
  text formats (txt, md, csv, json). PDF and image files are stored, listed
  and streamed back — but their extraction state is ``PROVIDER_REQUIRED``,
  because there is no OCR/PDF library in this repository and a fabricated
  extraction would be worse than none. The state is per-document and visible,
  never a silent clean screen.

Storage
-------
Bytes land under the private upload root (never the public static tree) and
are mirrored to R2/S3 when object storage is configured, under a
``private-office/<owner>/...`` key that no public URL scheme serves. Content
leaves only through the authenticated, owner-gated streaming route.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from services import media_storage
from services.private_office import audit
from services.private_office import evidence
from services.private_office import facts as facts_mod
from services.private_office import graph as graph_mod
from services.private_office import jobs
from services.private_office import model

DOCUMENTS_TABLE = "private_documents"
CLAIMS_TABLE = "private_document_claims"

#: Upload vocabulary. Extension decides the served content type — the client's
#: declared MIME is advisory and is not stored as truth.
ALLOWED_EXTENSIONS: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "heic": "image/heic",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
}
#: Formats the deterministic extractor can actually read. Everything else is
#: honest about needing a provider.
EXTRACTABLE_EXTENSIONS: tuple[str, ...] = ("txt", "md", "csv", "json")

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_CLAIMS_PER_DOCUMENT = 20
MAX_CLAIM_VALUE_CHARS = 200

EXTRACTION_EXTRACTED = "EXTRACTED"
EXTRACTION_NO_CLAIMS = "NO_CLAIMS"
EXTRACTION_PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
EXTRACTION_FAILED = "FAILED"
EXTRACTION_STATES: tuple[str, ...] = (
    EXTRACTION_EXTRACTED, EXTRACTION_NO_CLAIMS,
    EXTRACTION_PROVIDER_REQUIRED, EXTRACTION_FAILED,
)

CLAIM_PROPOSED = "PROPOSED"
CLAIM_ACCEPTED = "ACCEPTED"
CLAIM_REJECTED = "REJECTED"
CLAIM_STATUSES: tuple[str, ...] = (CLAIM_PROPOSED, CLAIM_ACCEPTED, CLAIM_REJECTED)

LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_DELETED = "DELETED"

DOCUMENTS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {DOCUMENTS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL DEFAULT '',
    extension TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    storage_provider TEXT NOT NULL DEFAULT 'local',
    storage_key TEXT NOT NULL DEFAULT '',
    extraction_state TEXT NOT NULL DEFAULT '',
    extraction_note TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT 'GENERAL',
    sensitivity TEXT NOT NULL DEFAULT 'CONFIDENTIAL',
    lifecycle_state TEXT NOT NULL DEFAULT '{LIFECYCLE_ACTIVE}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

CLAIMS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {CLAIMS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL DEFAULT '',
    value_type TEXT NOT NULL DEFAULT 'STRING',
    proposed_value TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT 'GENERAL',
    status TEXT NOT NULL DEFAULT '{CLAIM_PROPOSED}',
    fact_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_po_documents_owner "
    f"ON {DOCUMENTS_TABLE} (owner_user_id, lifecycle_state, created_at)",
    f"CREATE INDEX IF NOT EXISTS idx_po_doc_claims_owner "
    f"ON {CLAIMS_TABLE} (owner_user_id, document_id, status)",
)

_SCHEMA_READY = False


class PrivateDocumentRejected(ValueError):
    """An upload or review this module refuses."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reset_documents_schema_cache() -> None:
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_documents_schema(cur, *, force: bool = False) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    cur.execute(DOCUMENTS_TABLE_DDL)
    cur.execute(CLAIMS_TABLE_DDL)
    for ddl in INDEX_DDL:
        cur.execute(ddl)
    # Extraction runs under a job record, so the vault's schema is not ready
    # until the jobs table is too — an upload that failed on the bookkeeping
    # INSERT would 503 a perfectly good document.
    jobs.ensure_jobs_schema(cur)
    _SCHEMA_READY = True


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def _extension_of(filename: str) -> str:
    name = str(filename or "").strip().lower()
    return name.rsplit(".", 1)[1] if "." in name else ""


def validate_upload(filename: str, size_bytes: int) -> str:
    """The extension, when this file may enter the vault; raises otherwise."""
    extension = _extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise PrivateDocumentRejected(f"unsupported file type; allowed: {allowed}")
    if int(size_bytes or 0) <= 0:
        raise PrivateDocumentRejected("the file is empty")
    if int(size_bytes) > MAX_DOCUMENT_BYTES:
        raise PrivateDocumentRejected(
            f"the file exceeds the {MAX_DOCUMENT_BYTES // (1024 * 1024)}MB limit")
    return extension


def _clean_title(title: object, fallback: str) -> str:
    text = " ".join(str(title or "").split())[:120]
    return text or " ".join(str(fallback or "document").split())[:120]


def store_document(
    cur,
    *,
    owner_user_id: int,
    filename: str,
    content: bytes,
    title: object = "",
    domain: object = None,
    sensitivity: object = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """Validate, persist and register one document. Returns its projection.

    Re-uploading identical bytes is answered with the existing row
    (``duplicate=True``) rather than a second copy — a vault with two rows for
    one document is a vault whose claim review can be done twice and disagree.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateDocumentRejected("owner_user_id is required")
    extension = validate_upload(filename, len(content or b""))
    ensure_documents_schema(cur)

    normalized_domain = model.normalize_domain(domain) or model.DOMAIN_GENERAL
    normalized_sensitivity = (model.normalize_sensitivity(sensitivity)
                              or model.SENSITIVITY_CONFIDENTIAL)

    digest = hashlib.sha256(content).hexdigest()
    cur.execute(
        f"""SELECT id FROM {DOCUMENTS_TABLE}
        WHERE owner_user_id=? AND sha256=? AND lifecycle_state=?""",
        (owner, digest, LIFECYCLE_ACTIVE),
    )
    existing = cur.fetchone()
    if existing is not None:
        doc_id = int(existing["id"] if isinstance(existing, dict) else existing[0])
        found = get_document(cur, owner_user_id=owner, document_id=doc_id)
        found["duplicate"] = True
        return found

    safe_name = media_storage.safe_media_name(filename)
    storage_key = f"private-office/{owner}/{digest[:16]}/{safe_name}"
    local_path = media_storage.PRIVATE_UPLOAD_ROOT / storage_key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)

    storage_provider = "local"
    current_provider = media_storage.provider()
    if current_provider in {"r2", "s3"}:
        uploaded, _error = media_storage._upload_to_object_storage(
            local_path, storage_key, ALLOWED_EXTENSIONS[extension])
        if uploaded:
            storage_provider = current_provider

    now = _now_iso()
    cur.execute(
        f"""INSERT INTO {DOCUMENTS_TABLE}
        (owner_user_id, title, original_name, extension, mime_type, size_bytes,
         sha256, storage_provider, storage_key, extraction_state, extraction_note,
         domain, sensitivity, lifecycle_state, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, _clean_title(title, safe_name), str(filename or "")[:160], extension,
         ALLOWED_EXTENSIONS[extension], len(content), digest, storage_provider,
         storage_key, "", "", normalized_domain, normalized_sensitivity,
         LIFECYCLE_ACTIVE, now, now),
    )
    doc_id = int(getattr(cur, "lastrowid", 0) or 0)
    if not doc_id:
        cur.execute(
            f"""SELECT id FROM {DOCUMENTS_TABLE}
            WHERE owner_user_id=? AND sha256=? ORDER BY id DESC LIMIT 1""",
            (owner, digest),
        )
        row = cur.fetchone()
        doc_id = int(row["id"] if isinstance(row, dict) else row[0])

    audit.record(
        cur, actor_user_id=actor_user_id or owner, owner_user_id=owner,
        action=audit.ACTION_DOCUMENT_CREATE, object_type="DOCUMENT",
        object_id=str(doc_id), purpose="user_request",
    )
    found = get_document(cur, owner_user_id=owner, document_id=doc_id)
    found["duplicate"] = False
    return found


# ---------------------------------------------------------------------------
# Extraction — deterministic, text formats only
# ---------------------------------------------------------------------------

_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9 _\-]{0,63}$")


def _claim_fact_type(key: str) -> str:
    token = re.sub(r"[^a-z0-9_]", "", str(key or "").strip().lower().replace(" ", "_").replace("-", "_"))
    return token[:64]


def _claim_value(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())[:MAX_CLAIM_VALUE_CHARS]


def _claim_value_type(value: str) -> str:
    try:
        float(value.replace(",", ""))
        return model.VALUE_NUMBER
    except (TypeError, ValueError):
        return model.VALUE_STRING


def _pairs_from_text(text: str) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if ":" not in line:
            continue
        key, _sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if _KEY_PATTERN.match(key) and value:
            pairs.append((key, value, f"line={line_no}"))
    return pairs


def _pairs_from_csv(text: str) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    try:
        for row_no, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if len(row) < 2:
                continue
            key, value = row[0].strip(), row[1].strip()
            if _KEY_PATTERN.match(key) and value:
                pairs.append((key, value, f"row={row_no}"))
    except csv.Error:
        return pairs
    return pairs


def _pairs_from_json(text: str) -> list[tuple[str, str, str]]:
    try:
        loaded = json.loads(text)
    except ValueError:
        return []
    if not isinstance(loaded, dict):
        return []
    pairs: list[tuple[str, str, str]] = []
    for key, value in loaded.items():
        if isinstance(value, (str, int, float, bool)) and _KEY_PATTERN.match(str(key)):
            pairs.append((str(key), str(value), f"key={_claim_fact_type(str(key))}"))
    return pairs


def extract_pairs(extension: str, content: bytes) -> list[tuple[str, str, str]]:
    """(key, value, locator) tuples the deterministic extractor found."""
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        return []
    if extension in ("txt", "md"):
        return _pairs_from_text(text)
    if extension == "csv":
        return _pairs_from_csv(text)
    if extension == "json":
        return _pairs_from_json(text)
    return []


def process_document(cur, *, owner_user_id: int, document_id: int,
                     content: bytes, actor_user_id: int | None = None) -> dict[str, Any]:
    """Run extraction for one stored document, under a job record.

    Text formats yield PROPOSED claims for the member to review. PDF and image
    formats are marked ``PROVIDER_REQUIRED`` with a note that says exactly why
    — the state a screen must render instead of a clean "nothing found".
    """
    owner = int(owner_user_id or 0)
    document = get_document(cur, owner_user_id=owner, document_id=document_id)
    if document is None:
        raise PrivateDocumentRejected("document not found")

    job_id = jobs.create_job(
        cur, owner_user_id=owner, job_type=jobs.JOB_DOCUMENT_EXTRACTION,
        subject_ref=evidence.format_ref("document", document_id),
    )
    jobs.start_job(cur, owner_user_id=owner, job_id=job_id)

    extension = document["extension"]
    now = _now_iso()
    if extension not in EXTRACTABLE_EXTENSIONS:
        state = EXTRACTION_PROVIDER_REQUIRED
        note = ("Text extraction for this format requires an OCR/PDF provider "
                "that is not integrated. The document is stored and readable; "
                "nothing has been extracted from it.")
        claims_created = 0
    else:
        try:
            pairs = extract_pairs(extension, content)[:MAX_CLAIMS_PER_DOCUMENT]
            for key, value, locator in pairs:
                fact_type = _claim_fact_type(key)
                value_text = _claim_value(value)
                if not fact_type or not value_text:
                    continue
                cur.execute(
                    f"""INSERT INTO {CLAIMS_TABLE}
                    (owner_user_id, document_id, fact_type, value_type,
                     proposed_value, locator, domain, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (owner, int(document_id), fact_type,
                     _claim_value_type(value_text), value_text, locator[:80],
                     document["domain"], CLAIM_PROPOSED, now),
                )
            cur.execute(
                f"SELECT COUNT(*) AS n FROM {CLAIMS_TABLE} WHERE owner_user_id=? AND document_id=?",
                (owner, int(document_id)),
            )
            row = cur.fetchone()
            claims_created = int(row["n"] if isinstance(row, dict) else row[0])
            state = EXTRACTION_EXTRACTED if claims_created else EXTRACTION_NO_CLAIMS
            note = ("" if claims_created else
                    "The extractor read the file but found no key/value lines to propose.")
        except Exception:
            state, note, claims_created = EXTRACTION_FAILED, "extraction error", 0

    cur.execute(
        f"""UPDATE {DOCUMENTS_TABLE} SET extraction_state=?, extraction_note=?, updated_at=?
        WHERE id=? AND owner_user_id=?""",
        (state, note, now, int(document_id), owner),
    )
    if state == EXTRACTION_FAILED:
        jobs.fail_job(cur, owner_user_id=owner, job_id=job_id, outcome_note=note)
    else:
        jobs.finish_job(
            cur, owner_user_id=owner, job_id=job_id,
            result_ref=evidence.format_ref("document", document_id),
            outcome_note=f"{state.lower()} claims={claims_created}",
        )
    return {"extraction_state": state, "extraction_note": note,
            "claims_proposed": claims_created, "job_id": job_id}


# ---------------------------------------------------------------------------
# Review — the only path from claim to fact
# ---------------------------------------------------------------------------

def review_claim(cur, *, owner_user_id: int, claim_id: int, decision: str,
                 actor_user_id: int | None = None) -> dict[str, Any]:
    """Accept or reject one PROPOSED claim.

    Acceptance is the deliberate act that writes the fact — through the
    canonical writer, with ``DOCUMENT_EXTRACTED`` provenance and a locator a
    human can follow back into the document — and places the document in the
    capital graph. Rejection records the decision and writes nothing.
    """
    owner = int(owner_user_id or 0)
    verb = str(decision or "").strip().lower()
    if verb not in ("accept", "reject"):
        raise PrivateDocumentRejected("decision must be accept or reject")

    cur.execute(
        f"SELECT * FROM {CLAIMS_TABLE} WHERE id=? AND owner_user_id=?",
        (int(claim_id or 0), owner),
    )
    row = cur.fetchone()
    if row is None:
        raise PrivateDocumentRejected("claim not found")
    claim = dict(row) if not isinstance(row, dict) else row
    if claim.get("status") != CLAIM_PROPOSED:
        raise PrivateDocumentRejected("claim is already reviewed")

    now = _now_iso()
    fact_id = 0
    if verb == "accept":
        document_id = int(claim["document_id"])
        outcome = facts_mod.record_fact(
            cur,
            owner_user_id=owner,
            subject_type="OWNER",
            subject_id=str(owner),
            fact_type=claim["fact_type"],
            value=claim["proposed_value"],
            value_type=claim["value_type"],
            provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED,
            provenance=facts_mod.ProvenanceRef(
                source_type="DOCUMENT",
                source_id=evidence.format_ref("document", document_id),
                locator=str(claim.get("locator") or ""),
            ),
            domain=claim.get("domain"),
            actor_user_id=actor_user_id or owner,
            purpose="document_processing",
        )
        fact_id = int(outcome.get("fact_id") or 0)
        graph_mod.upsert_node(
            cur, owner_user_id=owner, node_type=model.NODE_DOCUMENT,
            external_ref=evidence.format_ref("document", document_id),
            domain=claim.get("domain"),
            actor_user_id=actor_user_id or owner,
            purpose="document_processing",
        )

    cur.execute(
        f"""UPDATE {CLAIMS_TABLE} SET status=?, fact_id=?, reviewed_at=?
        WHERE id=? AND owner_user_id=? AND status=?""",
        (CLAIM_ACCEPTED if verb == "accept" else CLAIM_REJECTED, fact_id, now,
         int(claim_id), owner, CLAIM_PROPOSED),
    )
    audit.record(
        cur, actor_user_id=actor_user_id or owner, owner_user_id=owner,
        action=audit.ACTION_CLAIM_REVIEWED, object_type="DOCUMENT_CLAIM",
        object_id=str(int(claim_id)), purpose="document_processing",
        outcome=audit.OUTCOME_OK,
    )
    return {"claim_id": int(claim_id),
            "status": CLAIM_ACCEPTED if verb == "accept" else CLAIM_REJECTED,
            "fact_id": fact_id}


# ---------------------------------------------------------------------------
# Reads and content
# ---------------------------------------------------------------------------

def _project(row) -> dict[str, Any]:
    data = dict(row) if not isinstance(row, dict) else row
    return {
        "id": int(data.get("id") or 0),
        "title": data.get("title") or "",
        "original_name": data.get("original_name") or "",
        "extension": data.get("extension") or "",
        "mime_type": data.get("mime_type") or "",
        "size_bytes": int(data.get("size_bytes") or 0),
        "sha256": data.get("sha256") or "",
        "extraction_state": data.get("extraction_state") or "",
        "extraction_note": data.get("extraction_note") or "",
        "domain": data.get("domain") or "",
        "sensitivity": data.get("sensitivity") or "",
        "created_at": data.get("created_at") or "",
        "updated_at": data.get("updated_at") or "",
        # Internal fields — the HTTP projection drops them. A storage key in a
        # client payload is a map to the bucket layout nobody outside needs.
        "storage_provider": data.get("storage_provider") or "local",
        "storage_key": data.get("storage_key") or "",
    }


#: What a client may see of a document. Allowlist, same as office.project_fact.
PUBLIC_DOCUMENT_FIELDS: tuple[str, ...] = (
    "id", "title", "original_name", "extension", "mime_type", "size_bytes",
    "extraction_state", "extraction_note", "domain", "sensitivity",
    "created_at", "updated_at",
)


def public_view(document: dict[str, Any]) -> dict[str, Any]:
    return {key: document[key] for key in PUBLIC_DOCUMENT_FIELDS if key in document}


def _project_claim(row) -> dict[str, Any]:
    data = dict(row) if not isinstance(row, dict) else row
    return {
        "id": int(data.get("id") or 0),
        "document_id": int(data.get("document_id") or 0),
        "fact_type": data.get("fact_type") or "",
        "value_type": data.get("value_type") or "",
        "proposed_value": data.get("proposed_value") or "",
        "locator": data.get("locator") or "",
        "domain": data.get("domain") or "",
        "status": data.get("status") or "",
        "fact_id": int(data.get("fact_id") or 0),
        "created_at": data.get("created_at") or "",
        "reviewed_at": data.get("reviewed_at"),
    }


def get_document(cur, *, owner_user_id: int, document_id: int) -> dict[str, Any] | None:
    ensure_documents_schema(cur)
    cur.execute(
        f"""SELECT * FROM {DOCUMENTS_TABLE}
        WHERE id=? AND owner_user_id=? AND lifecycle_state=?""",
        (int(document_id or 0), int(owner_user_id or 0), LIFECYCLE_ACTIVE),
    )
    row = cur.fetchone()
    return _project(row) if row is not None else None


def list_documents(cur, *, owner_user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    ensure_documents_schema(cur)
    cur.execute(
        f"""SELECT * FROM {DOCUMENTS_TABLE}
        WHERE owner_user_id=? AND lifecycle_state=?
        ORDER BY id DESC LIMIT ?""",
        (int(owner_user_id or 0), LIFECYCLE_ACTIVE, max(1, min(int(limit or 50), 200))),
    )
    return [_project(row) for row in cur.fetchall()]


def list_claims(cur, *, owner_user_id: int, document_id: int = 0,
                status: str = "") -> list[dict[str, Any]]:
    ensure_documents_schema(cur)
    owner = int(owner_user_id or 0)
    clauses, params = ["owner_user_id=?"], [owner]
    if int(document_id or 0):
        clauses.append("document_id=?")
        params.append(int(document_id))
    wanted = str(status or "").strip().upper()
    if wanted:
        if wanted not in CLAIM_STATUSES:
            return []
        clauses.append("status=?")
        params.append(wanted)
    cur.execute(
        f"""SELECT * FROM {CLAIMS_TABLE} WHERE {' AND '.join(clauses)}
        ORDER BY id ASC LIMIT 200""",
        tuple(params),
    )
    return [_project_claim(row) for row in cur.fetchall()]


def fetch_content(document: dict[str, Any]) -> bytes:
    """The stored bytes for a document projection this module produced.

    Durable storage is authoritative when the document was mirrored there;
    the local private tree serves the rest. There is no public URL for either
    — content leaves through the owner-gated route only.
    """
    storage_key = _storage_key_of(document) if document else ""
    if not storage_key:
        raise PrivateDocumentRejected("document has no stored content")
    provider = str(document.get("storage_provider") or "local")
    if provider in {"r2", "s3"}:
        result = media_storage.get_object(storage_key)
        body = result.get("Body")
        return body.read() if body is not None else b""
    local_path = media_storage.PRIVATE_UPLOAD_ROOT / storage_key
    if not local_path.exists():
        raise PrivateDocumentRejected("stored content is unavailable")
    return local_path.read_bytes()


def _storage_key_of(document: dict[str, Any]) -> str:
    return str(document.get("storage_key") or "")


def delete_document(cur, *, owner_user_id: int, document_id: int,
                    actor_user_id: int | None = None) -> bool:
    """Soft-delete the row and remove the stored bytes, best-effort.

    The row survives (lifecycle DELETED) so accepted facts keep a resolvable
    provenance trail; the *content* is what the member asked to be rid of.
    """
    owner = int(owner_user_id or 0)
    document = get_document(cur, owner_user_id=owner, document_id=document_id)
    if document is None:
        return False
    cur.execute(
        f"""UPDATE {DOCUMENTS_TABLE} SET lifecycle_state=?, updated_at=?
        WHERE id=? AND owner_user_id=? AND lifecycle_state=?""",
        (LIFECYCLE_DELETED, _now_iso(), int(document_id), owner, LIFECYCLE_ACTIVE),
    )
    if not getattr(cur, "rowcount", 0):
        return False

    storage_key = _storage_key_of(document)
    try:
        local_path = media_storage.PRIVATE_UPLOAD_ROOT / storage_key
        if storage_key and local_path.exists():
            local_path.unlink()
    except Exception:
        pass
    if document.get("storage_provider") in {"r2", "s3"}:
        try:
            client = media_storage.object_client()
            if client is not None:
                import os as _os
                client.delete_object(
                    Bucket=_os.getenv("R2_BUCKET") or _os.getenv("S3_BUCKET"),
                    Key=storage_key)
        except Exception:
            pass

    audit.record(
        cur, actor_user_id=actor_user_id or owner, owner_user_id=owner,
        action=audit.ACTION_DOCUMENT_DELETE, object_type="DOCUMENT",
        object_id=str(int(document_id)), purpose="user_request",
    )
    return True
