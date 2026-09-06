"""Document Intelligence — the vault, its claim pipeline, and its HTTP surface.

Run either way::

    python -m pytest tests/private_office/test_private_documents.py
    python tests/private_office/test_private_documents.py

What these tests defend
-----------------------
* **The gate order holds over HTTP.** No session is 401, no tier is 403, the
  kill switch is 404, a locked Office is 423 — before any document bytes move.
* **Extraction proposes; only review asserts.** A text upload yields PROPOSED
  claims and zero facts. Accepting one claim writes exactly one fact, through
  the canonical writer, with ``DOCUMENT_EXTRACTED`` provenance and a locator a
  human can follow back into the file. Rejecting writes nothing.
* **The provider edge is truthful, per document.** A PDF is stored and served
  but its extraction state is ``PROVIDER_REQUIRED`` with a note that says why
  — never a fabricated "nothing found".
* **An accepted fact is reachable, and classified like its source.** The fact
  is subjected to the document's graph node — the only subject
  ``retrieval.retrieve`` looks for — and it comes back from a real gated
  retrieval, not just from a flat scan. It carries the document's own
  sensitivity, so a RESTRICTED document cannot be declassified on the way into
  the graph. A claim whose document has been deleted cannot be accepted at all.
* **Owner isolation everywhere.** Another member's document, claim, or bytes
  are indistinguishable from ones that never existed: list, detail, content,
  delete and review all answer the not-found shape.
* **The payload is the projection.** Storage provider and key never appear in
  JSON; audit rows carry identity, never a proposed value.
"""

import io
import json
import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_documents_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route packs can import it -----------------
_stub = types.ModuleType("bot")
_stub._test_user = None


def _api_account_user():
    return _stub._test_user


def _require_admin_api(permission):
    return (None, ("DENIED", 403))


_stub.api_account_user = _api_account_user
_stub.require_admin_api = _require_admin_api
sys.modules["bot"] = _stub

from pathlib import Path  # noqa: E402

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from services import db  # noqa: E402
from services import media_storage  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services import private_office_documents_routes as doc_routes  # noqa: E402
from services.private_office import documents as documents_mod  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts as facts_mod  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import graph as graph_mod  # noqa: E402
from services.private_office import jobs  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

# Private bytes must never land in the repo's instance tree during a test run.
_TMP_UPLOADS = Path(tempfile.mkdtemp(prefix="private_documents_uploads_"))
media_storage.PRIVATE_UPLOAD_ROOT = _TMP_UPLOADS

USER_A = 9901
USER_B = 9902
USER_C = 9903  # a real session with no Private tier — the 403 case

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


_GRANTS: dict[int, str] = {}
PASSCODE = "638194"


class _GrantClient(FlaskClient):
    def open(self, *args, **kwargs):
        user = _stub._test_user or {}
        token = _GRANTS.get(int(user.get("user_id") or 0), "")
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(routes.GRANT_HEADER, token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _app():
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    routes.register(app)
    doc_routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active", "access_enabled": 1}


def _unlock(user_id):
    app = Flask(__name__)
    routes.register(app)
    client = app.test_client()
    _as(user_id)
    client.post("/api/private-office/security/setup",
                json={"passcode": PASSCODE, "confirm_passcode": PASSCODE})
    resp = client.post("/api/private-office/security/unlock",
                       json={"passcode": PASSCODE})
    token = (resp.get_json() or {}).get("grant_token") or ""
    if token:
        _GRANTS[int(user_id)] = token
    return token


def setup_environment():
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B, USER_C):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        documents_mod.reset_documents_schema_cache()
        documents_mod.ensure_documents_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


def _upload(client, filename, payload, title=""):
    data = {"file": (io.BytesIO(payload), filename)}
    if title:
        data["title"] = title
    return client.post("/api/private-office/documents", data=data,
                       content_type="multipart/form-data")


def _query_all(sql, params=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _fact_count(owner):
    conn = db.connect()
    try:
        return facts_mod.count_facts(conn.cursor(), owner_user_id=owner)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Gate order
# ---------------------------------------------------------------------------

def stage_gates():
    print("\n[documents: gates]")
    client = _app().test_client()

    _stub._test_user = None
    resp = client.get("/api/private-office/documents")
    check("no session is 401", resp.status_code == 401, str(resp.status_code))

    _as(USER_C)
    resp = client.get("/api/private-office/documents")
    body = resp.get_json() or {}
    check("no Private tier is 403 with a minimum tier",
          resp.status_code == 403 and body.get("minimum_tier"),
          f"{resp.status_code} {body}")

    previous = os.environ.get("PRIVATE_DOCUMENTS_ENABLED")
    os.environ["PRIVATE_DOCUMENTS_ENABLED"] = "false"
    try:
        _as(USER_A)
        resp = client.get("/api/private-office/documents")
        check("kill switch off is 404 for an entitled member",
              resp.status_code == 404, str(resp.status_code))
        state = matrix.availability(doc_routes.DOCUMENTS_FEATURE_ID, tiers.TIER_PRIVATE)
        check("flag off reads as FEATURE_DISABLED, implementation still IMPLEMENTED",
              state["availability"] == matrix.AVAIL_FEATURE_DISABLED
              and state["implementation"] == matrix.IMPL_IMPLEMENTED, str(state))
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_DOCUMENTS_ENABLED", None)
        else:
            os.environ["PRIVATE_DOCUMENTS_ENABLED"] = previous

    _as(USER_A)
    resp = client.get("/api/private-office/documents",
                      headers={routes.GRANT_HEADER: ""})
    check("no unlock grant is 423 Locked", resp.status_code == 423, str(resp.status_code))

    resp = client.get("/api/private-office/documents")
    body = resp.get_json() or {}
    check("unlocked member reaches the vault", resp.status_code == 200 and body.get("ok"),
          f"{resp.status_code}")
    check("payload carries truthful provider status",
          (body.get("provider_status") or {}).get("ocr_extraction") == "provider_required"
          and (body.get("provider_status") or {}).get("text_extraction") == "implemented",
          str(body.get("provider_status")))
    check("no-store on the vault list",
          "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

def stage_upload_validation():
    print("\n[documents: upload validation]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post("/api/private-office/documents", data={},
                       content_type="multipart/form-data")
    check("missing file field is 400", resp.status_code == 400, str(resp.status_code))

    resp = _upload(client, "malware.exe", b"MZ...")
    check("disallowed extension is 400", resp.status_code == 400, str(resp.status_code))

    resp = _upload(client, "empty.txt", b"")
    check("empty file is 400", resp.status_code == 400, str(resp.status_code))

    try:
        documents_mod.validate_upload("big.pdf", documents_mod.MAX_DOCUMENT_BYTES + 1)
        check("oversize file is rejected", False)
    except documents_mod.PrivateDocumentRejected:
        check("oversize file is rejected", True)

    check("no extension is rejected without a stack trace",
          _upload(client, "README", b"hello").status_code == 400)


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------

_STATE = {}  # doc ids and claim ids threaded between stages


def stage_text_extraction():
    print("\n[documents: text extraction]")
    client = _app().test_client()
    _as(USER_A)

    payload = (b"Insurance Provider: Acme Mutual\n"
               b"Annual Premium: 4200\n"
               b"just prose, no pair here\n"
               b"# heading without value:\n")
    resp = _upload(client, "policy-notes.txt", payload, title="Policy notes")
    body = resp.get_json() or {}
    check("txt upload lands as 201", resp.status_code == 201, str(resp.status_code))
    extraction = body.get("extraction") or {}
    check("state is EXTRACTED with two proposals",
          extraction.get("state") == documents_mod.EXTRACTION_EXTRACTED
          and extraction.get("claims_proposed") == 2, str(extraction))

    claims = body.get("claims") or []
    by_type = {c["fact_type"]: c for c in claims}
    check("both claims are PROPOSED",
          all(c["status"] == documents_mod.CLAIM_PROPOSED for c in claims), str(claims))
    check("keys normalise to fact types",
          set(by_type) == {"insurance_provider", "annual_premium"}, str(sorted(by_type)))
    check("locators point at the source line",
          by_type.get("insurance_provider", {}).get("locator") == "line=1"
          and by_type.get("annual_premium", {}).get("locator") == "line=2")
    check("numeric values are typed NUMBER, prose STRING",
          by_type.get("annual_premium", {}).get("value_type") == model.VALUE_NUMBER
          and by_type.get("insurance_provider", {}).get("value_type") == model.VALUE_STRING)
    check("extraction proposed, asserted nothing", _fact_count(USER_A) == 0,
          str(_fact_count(USER_A)))

    document = body.get("document") or {}
    _STATE["txt_doc_id"] = document.get("id")
    _STATE["accept_claim_id"] = by_type.get("annual_premium", {}).get("id")
    _STATE["reject_claim_id"] = by_type.get("insurance_provider", {}).get("id")
    _STATE["txt_payload"] = payload

    check("HTTP projection has no storage internals",
          "storage_key" not in document and "storage_provider" not in document
          and "owner_user_id" not in document, str(sorted(document)))

    csv_resp = _upload(client, "holdings.csv", b"Broker,Meridian\nAccount Type,Taxable\n,orphan\n")
    csv_body = csv_resp.get_json() or {}
    check("csv rows become claims with row locators",
          (csv_body.get("extraction") or {}).get("claims_proposed") == 2
          and all(c["locator"].startswith("row=") for c in csv_body.get("claims") or []),
          str(csv_body.get("claims")))

    json_resp = _upload(client, "profile.json",
                        json.dumps({"advisor": "R. Chen", "retainer": 1500,
                                    "nested": {"skip": "me"}}).encode())
    json_body = json_resp.get_json() or {}
    check("flat json scalars become claims, nested values do not",
          (json_body.get("extraction") or {}).get("claims_proposed") == 2,
          str(json_body.get("extraction")))

    plain = _upload(client, "diary.md", b"no key value pairs in this one\n")
    plain_body = plain.get_json() or {}
    check("a pairless text file is NO_CLAIMS with an honest note",
          (plain_body.get("extraction") or {}).get("state") == documents_mod.EXTRACTION_NO_CLAIMS
          and (plain_body.get("extraction") or {}).get("note"),
          str(plain_body.get("extraction")))


def stage_provider_required():
    print("\n[documents: provider edge]")
    client = _app().test_client()
    _as(USER_A)

    resp = _upload(client, "deed.pdf", b"%PDF-1.4 fake body")
    body = resp.get_json() or {}
    extraction = body.get("extraction") or {}
    check("pdf is stored but PROVIDER_REQUIRED",
          resp.status_code == 201
          and extraction.get("state") == documents_mod.EXTRACTION_PROVIDER_REQUIRED,
          str(extraction))
    check("the state carries a why, not a clean screen",
          "provider" in (extraction.get("note") or "").lower(), str(extraction.get("note")))
    check("no claims were invented", extraction.get("claims_proposed") == 0)
    _STATE["pdf_doc_id"] = (body.get("document") or {}).get("id")

    detail = client.get(f"/api/private-office/documents/{_STATE['pdf_doc_id']}")
    dbody = detail.get_json() or {}
    check("detail repeats the per-document truth",
          (dbody.get("document") or {}).get("extraction_state")
          == documents_mod.EXTRACTION_PROVIDER_REQUIRED)


def stage_dedupe():
    print("\n[documents: dedupe]")
    client = _app().test_client()
    _as(USER_A)

    before = len((client.get("/api/private-office/documents").get_json() or {}).get("documents") or [])
    resp = _upload(client, "policy-notes-again.txt", _STATE["txt_payload"])
    body = resp.get_json() or {}
    after = len((client.get("/api/private-office/documents").get_json() or {}).get("documents") or [])
    check("identical bytes answer with the existing row",
          body.get("duplicate") is True
          and (body.get("document") or {}).get("id") == _STATE["txt_doc_id"],
          str(body.get("document")))
    check("no second copy entered the vault", before == after, f"{before} -> {after}")


# ---------------------------------------------------------------------------
# Review — the only path from claim to fact
# ---------------------------------------------------------------------------

def stage_claim_review():
    print("\n[documents: claim review]")
    client = _app().test_client()
    _as(USER_A)
    doc_id = _STATE["txt_doc_id"]

    resp = client.post(f"/api/private-office/claims/{_STATE['accept_claim_id']}/review",
                       json={"decision": "accept"})
    body = resp.get_json() or {}
    check("accept answers with the fact id",
          resp.status_code == 200 and body.get("status") == documents_mod.CLAIM_ACCEPTED
          and int(body.get("fact_id") or 0) > 0, str(body))
    fact_id = int(body.get("fact_id") or 0)

    conn = db.connect()
    try:
        cur = conn.cursor()
        rows = facts_mod.list_facts(cur, owner_user_id=USER_A)
        nodes = graph_mod.list_nodes(cur, owner_user_id=USER_A,
                                     node_types=[model.NODE_DOCUMENT])
    finally:
        conn.close()
    fact = next((f for f in rows if int(f.get("id") or 0) == fact_id), None)
    check("exactly one fact exists and it is the accepted claim",
          len(rows) == 1 and fact is not None, str(rows))
    if fact:
        check("provenance is DOCUMENT_EXTRACTED",
              fact.get("provenance_type") == model.PROVENANCE_DOCUMENT_EXTRACTED,
              str(fact.get("provenance_type")))
        prov = fact.get("provenance") or {}
        check("provenance points back into the document with a locator",
              prov.get("source_type") == "DOCUMENT"
              and prov.get("source_id") == evidence.format_ref("document", doc_id)
              and prov.get("locator") == "line=2", str(prov))
    check("the document entered the capital graph",
          any(n.get("external_ref") == evidence.format_ref("document", doc_id)
              for n in nodes), str(nodes))

    resp = client.post(f"/api/private-office/claims/{_STATE['reject_claim_id']}/review",
                       json={"decision": "reject"})
    body = resp.get_json() or {}
    check("reject records the decision", body.get("status") == documents_mod.CLAIM_REJECTED
          and int(body.get("fact_id") or 0) == 0, str(body))
    check("reject wrote no fact", _fact_count(USER_A) == 1, str(_fact_count(USER_A)))

    resp = client.post(f"/api/private-office/claims/{_STATE['accept_claim_id']}/review",
                       json={"decision": "accept"})
    check("a reviewed claim cannot be reviewed again", resp.status_code == 400,
          str(resp.status_code))

    resp = client.post(f"/api/private-office/claims/{_STATE['reject_claim_id']}/review",
                       json={"decision": "maybe"})
    check("an unknown decision is refused", resp.status_code == 400, str(resp.status_code))


# ---------------------------------------------------------------------------
# Content streaming
# ---------------------------------------------------------------------------

def stage_content():
    print("\n[documents: content]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.get(f"/api/private-office/documents/{_STATE['txt_doc_id']}/content")
    check("the owner streams the exact stored bytes",
          resp.status_code == 200 and resp.data == _STATE["txt_payload"],
          str(resp.status_code))
    check("content is no-store", "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))
    check("content is inline with a safe name",
          (resp.headers.get("Content-Disposition") or "").startswith("inline"),
          str(resp.headers.get("Content-Disposition")))
    check("mime comes from the stored extension, not the client",
          (resp.mimetype or "") == "text/plain", str(resp.mimetype))


# ---------------------------------------------------------------------------
# Owner isolation
# ---------------------------------------------------------------------------

def stage_isolation():
    print("\n[documents: isolation]")
    client = _app().test_client()

    _as(USER_B)
    body = client.get("/api/private-office/documents").get_json() or {}
    check("B's vault is empty of A's documents", body.get("count") == 0, str(body.get("count")))

    detail = client.get(f"/api/private-office/documents/{_STATE['txt_doc_id']}")
    check("A's document detail is not-found for B", detail.status_code == 404,
          str(detail.status_code))

    content = client.get(f"/api/private-office/documents/{_STATE['txt_doc_id']}/content")
    check("A's bytes are not-found for B", content.status_code == 404,
          str(content.status_code))

    deleted = client.delete(f"/api/private-office/documents/{_STATE['txt_doc_id']}")
    check("B cannot delete A's document", deleted.status_code == 404,
          str(deleted.status_code))

    review = client.post(f"/api/private-office/claims/{_STATE['reject_claim_id']}/review",
                         json={"decision": "accept"})
    check("A's claim is indistinguishable from a missing one for B",
          review.status_code == 400
          and "not found" in ((review.get_json() or {}).get("message") or ""),
          str(review.status_code))
    check("B's failed review wrote nothing for anyone",
          _fact_count(USER_A) == 1 and _fact_count(USER_B) == 0)

    conn = db.connect()
    try:
        cur = conn.cursor()
        check("service-level reads carry the owner predicate",
              documents_mod.get_document(cur, owner_user_id=USER_B,
                                         document_id=_STATE["txt_doc_id"]) is None
              and documents_mod.list_claims(cur, owner_user_id=USER_B) == [])
    finally:
        conn.close()

    _as(USER_A)
    still = client.get(f"/api/private-office/documents/{_STATE['txt_doc_id']}")
    check("A's document survived B's attempts", still.status_code == 200,
          str(still.status_code))


# ---------------------------------------------------------------------------
# Delete lifecycle
# ---------------------------------------------------------------------------

def stage_delete():
    print("\n[documents: delete]")
    client = _app().test_client()
    _as(USER_A)
    doc_id = _STATE["pdf_doc_id"]

    rows = _query_all(
        f"SELECT storage_key FROM {documents_mod.DOCUMENTS_TABLE} WHERE id=?", (doc_id,))
    local_path = media_storage.PRIVATE_UPLOAD_ROOT / rows[0]["storage_key"]
    check("stored bytes exist before delete", local_path.exists(), str(local_path))

    resp = client.delete(f"/api/private-office/documents/{doc_id}")
    body = resp.get_json() or {}
    check("delete acknowledges", resp.status_code == 200 and body.get("deleted") is True,
          str(body))
    check("the content is gone from disk", not local_path.exists())
    check("the document no longer lists",
          client.get(f"/api/private-office/documents/{doc_id}").status_code == 404)

    survivors = _query_all(
        f"SELECT lifecycle_state FROM {documents_mod.DOCUMENTS_TABLE} WHERE id=?", (doc_id,))
    check("the row is retired, not erased — provenance stays resolvable",
          survivors and survivors[0]["lifecycle_state"] == documents_mod.LIFECYCLE_DELETED,
          str(survivors))

    check("deleting twice answers not-found",
          client.delete(f"/api/private-office/documents/{doc_id}").status_code == 404)


# ---------------------------------------------------------------------------
# Bookkeeping — jobs and audit
# ---------------------------------------------------------------------------

def stage_bookkeeping():
    print("\n[documents: jobs and audit]")
    conn = db.connect()
    try:
        cur = conn.cursor()
        job_rows = jobs.list_jobs(cur, owner_user_id=USER_A,
                                  job_type=jobs.JOB_DOCUMENT_EXTRACTION)
    finally:
        conn.close()
    check("every upload ran under a job record", len(job_rows) >= 5, str(len(job_rows)))
    check("jobs finished with a truthful outcome",
          all(j["status"] == jobs.STATUS_SUCCEEDED for j in job_rows), str(job_rows))
    check("job refs use the evidence vocabulary",
          all(j["subject_ref"].startswith("document:") for j in job_rows))

    audit_rows = _query_all(
        f"SELECT action, object_type, object_id FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id=?", (USER_A,))
    actions = {row["action"] for row in audit_rows}
    check("create, read, review and delete are all audited",
          {"PRIVATE_DOCUMENT_CREATE", "PRIVATE_DOCUMENT_READ",
           "PRIVATE_DOCUMENT_CLAIM_REVIEWED", "PRIVATE_DOCUMENT_DELETE"} <= actions,
          str(sorted(actions)))
    leaked = [r for r in audit_rows
              if "Acme" in str(r.get("object_id")) or "4200" == str(r.get("object_id"))]
    check("audit rows carry identity, never a proposed value", not leaked, str(leaked))


def stage_feature_matrix():
    print("\n[documents: feature matrix truth]")
    spec = matrix.get(doc_routes.DOCUMENTS_FEATURE_ID)
    check("the row is IMPLEMENTED with a kill switch",
          spec is not None and spec.implementation == matrix.IMPL_IMPLEMENTED
          and spec.flag_env == "PRIVATE_DOCUMENTS_ENABLED", str(spec))
    state = matrix.availability(doc_routes.DOCUMENTS_FEATURE_ID, tiers.TIER_PRIVATE)
    check("a Private member is entitled by default",
          state["availability"] == matrix.AVAIL_ENTITLED, str(state))


# ---------------------------------------------------------------------------
# Reachability — which subject the fact was written against
#
# This stage exists because of a defect the stages above could not see. They
# assert that accepting a claim writes one fact with the right provenance and
# that the document enters the graph. Both were true while the fact was written
# against `subject_type=OWNER` and the node it was supposedly about held
# nothing — and `retrieval.retrieve` reads facts by `subject_type=NODE` keyed by
# the nodes it walked, so no document fact was ever reachable through the gated
# path. Provenance was correct and the fact was still invisible.
#
# So the assertions here are deliberately about the join rather than about
# either side of it: the subject the fact carries, and an actual retrieval that
# has to traverse to find it. It runs last because it uploads and accepts, and
# the earlier stages assert exact fact counts.
# ---------------------------------------------------------------------------

def _document_node(owner, document_id):
    """The DOCUMENT node for one document, read at the widest ceiling."""
    ref = evidence.format_ref("document", document_id)
    conn = db.connect()
    try:
        nodes = graph_mod.list_nodes(
            conn.cursor(), owner_user_id=owner,
            node_types=[model.NODE_DOCUMENT],
            sensitivity_ceiling=model.SENSITIVITY_RESTRICTED)
    finally:
        conn.close()
    return next((n for n in nodes if n.get("external_ref") == ref), None)


def _fact_for_document(owner, document_id):
    """The accepted fact citing one document, found by its provenance ref."""
    ref = evidence.format_ref("document", document_id)
    conn = db.connect()
    try:
        rows = facts_mod.list_facts(
            conn.cursor(), owner_user_id=owner,
            sensitivity_ceiling=model.SENSITIVITY_RESTRICTED)
    finally:
        conn.close()
    return next((f for f in rows
                 if (f.get("provenance") or {}).get("source_id") == ref), None)


def _retrieve_documents(owner):
    """A real gated read over the document nodes, as UNDX would issue it."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        context = retrieval.retrieve(
            cur, owner_user_id=owner, intent=retrieval.INTENT_LEGAL_DOCUMENTS,
            seed_node_types=[model.NODE_DOCUMENT],
            purpose="document_processing")
        conn.commit()
    finally:
        conn.close()
    return context


def _upload_with_sensitivity(client, filename, payload, sensitivity):
    data = {"file": (io.BytesIO(payload), filename), "sensitivity": sensitivity}
    return client.post("/api/private-office/documents", data=data,
                       content_type="multipart/form-data")


def stage_fact_reachability():
    print("\n[documents: fact subject and reachability]")
    client = _app().test_client()
    _as(USER_A)
    doc_id = _STATE["txt_doc_id"]

    node = _document_node(USER_A, doc_id)
    fact = _fact_for_document(USER_A, doc_id)
    check("the accepted claim left a document node and a fact",
          node is not None and fact is not None, f"node={node} fact={fact}")
    if not (node and fact):
        return

    check("the fact is subjected to the document node, not to the owner",
          fact.get("subject_type") == facts_mod.SUBJECT_NODE
          and str(fact.get("subject_id")) == str(node["id"]),
          f"{fact.get('subject_type')}:{fact.get('subject_id')} "
          f"vs NODE:{node['id']}")

    context = _retrieve_documents(USER_A)
    check("the gated retrieval was not refused",
          context.get("denied") == "", str(context.get("denied")))
    reached = {int(f.get("id") or 0) for f in context.get("relevant_facts") or []}
    check("a real retrieval reaches the fact end to end",
          int(fact["id"]) in reached, f"{fact['id']} not in {sorted(reached)}")
    cited = {(p.get("ref") or {}).get("source_id")
             for p in context.get("provenance") or []}
    check("the retrieved context can name the document it came from",
          evidence.format_ref("document", doc_id) in cited, str(sorted(cited)))

    # --- classification travels with the fact ------------------------------
    resp = _upload_with_sensitivity(
        client, "trust-deed.txt", b"Trustee: Halcyon Nominees\n",
        model.SENSITIVITY_RESTRICTED)
    body = resp.get_json() or {}
    restricted_doc = (body.get("document") or {}).get("id")
    restricted_claims = body.get("claims") or []
    check("a RESTRICTED upload is stored at the sensitivity it was given",
          resp.status_code == 201
          and (body.get("document") or {}).get("sensitivity")
          == model.SENSITIVITY_RESTRICTED and len(restricted_claims) == 1,
          str(body.get("document")))
    if not restricted_claims:
        return

    review = client.post(
        f"/api/private-office/claims/{restricted_claims[0]['id']}/review",
        json={"decision": "accept"})
    check("the RESTRICTED claim is accepted", review.status_code == 200,
          str(review.status_code))

    r_node = _document_node(USER_A, restricted_doc)
    r_fact = _fact_for_document(USER_A, restricted_doc)
    check("the node inherits the document's classification",
          r_node is not None
          and r_node.get("sensitivity") == model.SENSITIVITY_RESTRICTED,
          str(r_node))
    check("the fact inherits it too — node and fact agree",
          r_fact is not None
          and r_fact.get("sensitivity") == model.SENSITIVITY_RESTRICTED,
          str(r_fact and r_fact.get("sensitivity")))

    context = _retrieve_documents(USER_A)
    reached = {int(f.get("id") or 0) for f in context.get("relevant_facts") or []}
    check("an intent capped at CONFIDENTIAL still reaches the confidential fact",
          int(fact["id"]) in reached, str(sorted(reached)))
    check("but it does not reach the RESTRICTED one — no declassification",
          r_fact is not None and int(r_fact["id"]) not in reached,
          str(sorted(reached)))

    # --- a claim cannot outlive the document that justifies it -------------
    resp = _upload_with_sensitivity(
        client, "codicil.txt", b"Executor: M. Alvarez\n",
        model.SENSITIVITY_CONFIDENTIAL)
    body = resp.get_json() or {}
    orphan_doc = (body.get("document") or {}).get("id")
    orphan_claims = body.get("claims") or []
    if not orphan_claims:
        check("the orphan fixture produced a claim", False, str(body))
        return
    orphan_claim = orphan_claims[0]["id"]

    check("the document is deleted before its claim is reviewed",
          client.delete(f"/api/private-office/documents/{orphan_doc}").status_code == 200)

    before = _fact_count(USER_A)
    review = client.post(f"/api/private-office/claims/{orphan_claim}/review",
                         json={"decision": "accept"})
    message = (review.get_json() or {}).get("message") or ""
    check("accepting a claim whose document is gone is refused",
          review.status_code == 400 and "no longer available" in message,
          f"{review.status_code} {message}")
    check("the refusal minted no fact", _fact_count(USER_A) == before,
          f"{before} -> {_fact_count(USER_A)}")
    surviving = _query_all(
        f"SELECT status FROM {documents_mod.CLAIMS_TABLE} WHERE id=?", (orphan_claim,))
    check("and it did not consume the claim — still PROPOSED",
          surviving and surviving[0]["status"] == documents_mod.CLAIM_PROPOSED,
          str(surviving))


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

STAGES = (
    stage_gates,
    stage_upload_validation,
    stage_text_extraction,
    stage_provider_required,
    stage_dedupe,
    stage_claim_review,
    stage_content,
    stage_isolation,
    stage_delete,
    stage_bookkeeping,
    stage_feature_matrix,
    stage_fact_reachability,
)


def test_everything():
    setup_environment()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    setup_environment()
    for stage in STAGES:
        stage()
    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}):")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("ALL STAGES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
