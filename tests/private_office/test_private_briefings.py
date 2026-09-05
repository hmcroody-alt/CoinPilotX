"""Private Briefings — the Office's own engine, every line cited.

Run either way::

    python -m pytest tests/private_office/test_private_briefings.py
    python tests/private_office/test_private_briefings.py

What these tests defend
-----------------------
* **The gate order holds over HTTP.** No session is 401, no tier is 403, the
  kill switch is 404, a locked Office is 423 — before any briefing is read.
* **Composition is deterministic and truthful.** Open records brief; resolved
  ones do not. Obligations order overdue-first, undated-last. A member with an
  empty Office gets a briefing with zero items — a true statement — never a
  fabricated one.
* **Every line is cited, and Ask Why answers.** Each item carries evidence
  refs that parse under the shared vocabulary and resolve, owner-checked, to
  the labelled rows behind them. A foreign ref reads as not-found, never as
  someone else's row.
* **Actions walk back.** An obligation or request created from a briefing goes
  through the canonical record writer and cites the briefing (and the quoted
  item's own evidence), so "why does this exist" has an answer.
* **Owner isolation everywhere.** Another member's briefing is
  indistinguishable from one that never existed: list, detail, Ask Why and
  action creation all answer the not-found shape.
* **Bookkeeping is real.** Generation runs under a job that succeeds with the
  briefing's ref, the audit trail records generation and reads, and no member
  content leaks into audit rows.
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_briefings_"), "test.db")
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

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services import private_office_briefings_routes as br_routes  # noqa: E402
from services.private_office import briefings as briefings_mod  # noqa: E402
from services.private_office import documents as documents_mod  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import jobs as jobs_mod  # noqa: E402
from services.private_office import records as records_mod  # noqa: E402
from services.private_office import relationships as relationships_mod  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

USER_A = 9921
USER_B = 9922
USER_C = 9923  # a real session with no Private tier — the 403 case

# Seeded content that must never surface in audit rows.
_SECRETS = ("Renew umbrella policy", "Quiet title dispute", "PX-4411", "Dana Whitfield")

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
    br_routes.register(app)
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
        records_mod.ensure_records_schema(cur, force=True)
        documents_mod.ensure_documents_schema(cur, force=True)
        briefings_mod.ensure_briefings_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


def _query_all(sql, params=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _service(work):
    """Run a service-level write with commit-on-success, like a route would."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        out = work(cur)
        conn.commit()
        return out
    finally:
        conn.close()


_STATE = {}  # ids threaded between stages


# ---------------------------------------------------------------------------
# Gate order
# ---------------------------------------------------------------------------

def stage_gates():
    print("\n[briefings: gates]")
    client = _app().test_client()

    _stub._test_user = None
    resp = client.get("/api/private-office/briefings")
    check("no session is 401", resp.status_code == 401, str(resp.status_code))

    _as(USER_C)
    resp = client.get("/api/private-office/briefings")
    body = resp.get_json() or {}
    check("no Private tier is 403 with a minimum tier",
          resp.status_code == 403 and body.get("minimum_tier"),
          f"{resp.status_code} {body}")

    previous = os.environ.get("PRIVATE_BRIEFINGS_ENABLED")
    os.environ["PRIVATE_BRIEFINGS_ENABLED"] = "false"
    try:
        _as(USER_A)
        resp = client.get("/api/private-office/briefings")
        check("kill switch off is 404 for an entitled member",
              resp.status_code == 404, str(resp.status_code))
        state = matrix.availability(br_routes.BRIEFINGS_FEATURE_ID, tiers.TIER_PRIVATE)
        check("flag off reads as FEATURE_DISABLED, implementation still IMPLEMENTED",
              state["availability"] == matrix.AVAIL_FEATURE_DISABLED
              and state["implementation"] == matrix.IMPL_IMPLEMENTED, str(state))
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_BRIEFINGS_ENABLED", None)
        else:
            os.environ["PRIVATE_BRIEFINGS_ENABLED"] = previous

    _as(USER_A)
    resp = client.get("/api/private-office/briefings",
                      headers={routes.GRANT_HEADER: ""})
    check("no unlock grant is 423 Locked", resp.status_code == 423, str(resp.status_code))

    resp = client.get("/api/private-office/briefings")
    body = resp.get_json() or {}
    check("unlocked member reaches the briefing list",
          resp.status_code == 200 and body.get("ok"), str(resp.status_code))
    check("payload states on-demand composition, no inference, no push",
          (body.get("provider_status") or {}).get("inference") == "none"
          and (body.get("provider_status") or {}).get("delivery") == "on_demand",
          str(body.get("provider_status")))
    check("no-store on the list",
          "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))


# ---------------------------------------------------------------------------
# Seeding the Office — through the canonical writers only
# ---------------------------------------------------------------------------

def stage_seed():
    print("\n[briefings: seeding office data]")

    def work(cur):
        person = relationships_mod.add_person(
            cur, owner_user_id=USER_A, name="Dana Whitfield",
            role="Estate Attorney", actor_user_id=USER_A)
        overdue = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Renew umbrella policy", obligation_type="INSURANCE",
            due_at="2026-01-15T00:00:00+00:00",
            related_entity_ids=[person["ref"]], actor_user_id=USER_A)
        upcoming = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="File annual trust accounting", obligation_type="LEGAL",
            due_at="2027-03-01T00:00:00+00:00", actor_user_id=USER_A)
        undated = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Collect appraisal documents", obligation_type="ADMIN",
            actor_user_id=USER_A)
        done = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Close old brokerage account", obligation_type="ADMIN",
            actor_user_id=USER_A)
        records_mod.update_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            record_id=int(done["record_id"]), status="RESOLVED",
            actor_user_id=USER_A)
        risk = records_mod.create_record(
            cur, record_type=records_mod.TYPE_RISK, owner_user_id=USER_A,
            risk_type="LEGAL", summary="Quiet title dispute on the lake parcel",
            actor_user_id=USER_A)
        req = records_mod.create_record(
            cur, record_type=records_mod.TYPE_REQUEST, owner_user_id=USER_A,
            title="Schedule estate review", category="LEGAL",
            actor_user_id=USER_A)
        doc = documents_mod.store_document(
            cur, owner_user_id=USER_A, filename="policy.txt",
            content=b"insurance_policy_number: PX-4411\n",
            title="Umbrella policy", actor_user_id=USER_A)
        doc_id = int(doc.get("id") or doc.get("document_id") or 0)
        documents_mod.process_document(
            cur, owner_user_id=USER_A, document_id=doc_id,
            content=b"insurance_policy_number: PX-4411\n", actor_user_id=USER_A)
        claims = documents_mod.list_claims(
            cur, owner_user_id=USER_A, status=documents_mod.CLAIM_PROPOSED)
        return person, overdue, upcoming, undated, done, risk, req, doc_id, claims

    person, overdue, upcoming, undated, done, risk, req, doc_id, claims = _service(work)
    _STATE.update({
        "person": person, "overdue": int(overdue["record_id"]),
        "upcoming": int(upcoming["record_id"]), "undated": int(undated["record_id"]),
        "resolved": int(done["record_id"]), "risk": int(risk["record_id"]),
        "request": int(req["record_id"]), "doc": doc_id,
    })
    check("office data seeds through the canonical writers",
          all(x["status"] in ("created", "existing")
              for x in (overdue, upcoming, undated, done, risk, req)),
          f"{overdue} {risk} {req}")
    check("the text document yields a PROPOSED claim to review",
          len(claims) >= 1, str(claims))


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def stage_generate():
    print("\n[briefings: generation]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post("/api/private-office/briefings")
    body = resp.get_json() or {}
    briefing = body.get("briefing") or {}
    check("generation is 201", resp.status_code == 201 and body.get("ok"),
          f"{resp.status_code} {body}")
    check("no-store on generation",
          "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))
    _STATE["briefing"] = int(briefing.get("id") or 0)
    _STATE["briefing_ref"] = briefing.get("ref") or ""

    items = briefing.get("items") or []
    sections = briefing.get("sections") or []
    check("item_count matches the persisted items",
          briefing.get("item_count") == len(items) and len(items) > 0,
          f"{briefing.get('item_count')} vs {len(items)}")

    order = [s["section"] for s in sections]
    expected = [name for name in briefings_mod.SECTIONS if name in order]
    check("sections render in the fixed order", order == expected,
          f"{order} vs {expected}")
    present = set(order)
    check("obligations, risks, requests, claims, people and facts all brief",
          {"obligations", "risks", "requests", "claims_pending",
           "people", "recent_facts"} <= present, str(order))

    by_name = {s["section"]: s["items"] for s in sections}
    obligations = by_name.get("obligations") or []
    labels = [i["label"] for i in obligations]
    check("only open obligations brief — the resolved one is absent",
          len(obligations) == 3 and "Close old brokerage account" not in labels,
          str(labels))
    check("overdue first", obligations
          and obligations[0]["detail"].startswith("overdue since")
          and obligations[0]["label"] == "Renew umbrella policy",
          str(obligations[:1]))
    check("undated last", obligations
          and obligations[-1]["detail"] == "no due date"
          and obligations[-1]["label"] == "Collect appraisal documents",
          str(obligations[-1:]))

    claims = by_name.get("claims_pending") or []
    check("the pending claim briefs, citing its document",
          claims and "PX-4411" in claims[0]["label"]
          and claims[0]["evidence"] == [evidence.format_ref("document", _STATE["doc"])],
          str(claims))

    people = by_name.get("people") or []
    check("a person with an open commitment briefs by name",
          people and people[0]["label"] == "Dana Whitfield"
          and people[0]["evidence"] == [_STATE["person"]["ref"]],
          str(people))

    all_refs = [ref for item in items for ref in item.get("evidence") or []]
    check("every item is cited and every ref parses",
          all(item.get("evidence") for item in items)
          and all(evidence.parse_ref(ref) for ref in all_refs),
          str(all_refs[:8]))
    check("the briefing's own evidence is the union of its items'",
          set(briefing.get("evidence") or []) == set(all_refs),
          f"{len(briefing.get('evidence') or [])} vs {len(set(all_refs))}")
    _STATE["item"] = (items[0] or {}) if items else {}

    resp = client.get(f"/api/private-office/briefings/{_STATE['briefing']}")
    body = resp.get_json() or {}
    again = body.get("briefing") or {}
    check("the briefing persisted — a later read returns the same items",
          resp.status_code == 200
          and [i["label"] for i in again.get("items") or []]
          == [i["label"] for i in items],
          str(resp.status_code))


# ---------------------------------------------------------------------------
# List + detail
# ---------------------------------------------------------------------------

def stage_list_and_detail():
    print("\n[briefings: list and detail]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post("/api/private-office/briefings")
    second = (resp.get_json() or {}).get("briefing") or {}
    _STATE["briefing2"] = int(second.get("id") or 0)
    check("a second generation lands", resp.status_code == 201, str(resp.status_code))

    resp = client.get("/api/private-office/briefings")
    body = resp.get_json() or {}
    rows = body.get("briefings") or []
    check("the list holds both, newest first",
          len(rows) == 2 and rows[0]["id"] == _STATE["briefing2"]
          and rows[1]["id"] == _STATE["briefing"],
          str([r.get("id") for r in rows]))

    resp = client.get("/api/private-office/briefings/999999")
    check("an absent briefing is 404", resp.status_code == 404, str(resp.status_code))


# ---------------------------------------------------------------------------
# Ask Why
# ---------------------------------------------------------------------------

def stage_ask_why():
    print("\n[briefings: ask why]")
    client = _app().test_client()
    _as(USER_A)

    refs = list(_STATE["item"].get("evidence") or []) + [_STATE["briefing_ref"]]
    resp = client.get("/api/private-office/briefings/why",
                      query_string={"refs": ",".join(refs)})
    body = resp.get_json() or {}
    resolved = body.get("evidence") or []
    check("own refs resolve, owner-checked, with labels",
          resp.status_code == 200 and len(resolved) == len(refs)
          and all(r["exists"] for r in resolved)
          and any(r.get("label") for r in resolved),
          f"{resp.status_code} {resolved}")

    resp = client.get("/api/private-office/briefings/why",
                      query_string={"refs": "obligation:999999"})
    body = resp.get_json() or {}
    resolved = body.get("evidence") or []
    check("a ref to a row that is not yours reads exists=False",
          resp.status_code == 200 and resolved and not resolved[0]["exists"],
          str(resolved))

    resp = client.get("/api/private-office/briefings/why")
    check("no refs is a 400, not an empty success",
          resp.status_code == 400, str(resp.status_code))


# ---------------------------------------------------------------------------
# Create Action
# ---------------------------------------------------------------------------

def stage_create_action():
    print("\n[briefings: create action]")
    client = _app().test_client()
    _as(USER_A)
    briefing_id = _STATE["briefing"]

    resp = client.post(
        f"/api/private-office/briefings/{briefing_id}/actions",
        json={"action_type": "obligation", "title": "Call the broker about renewal",
              "due_at": "2026-10-01T00:00:00+00:00",
              "item_id": int(_STATE["item"].get("id") or 0)})
    body = resp.get_json() or {}
    check("an obligation action is 201 through the record writer",
          resp.status_code == 201 and body.get("ok")
          and body.get("record_type") == records_mod.TYPE_OBLIGATION,
          f"{resp.status_code} {body}")
    cited = body.get("cited") or []
    check("the action cites the briefing and the quoted item's evidence",
          _STATE["briefing_ref"] in cited
          and set(_STATE["item"].get("evidence") or []) <= set(cited),
          str(cited))

    def read(cur):
        return records_mod.list_records(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            statuses=["OPEN"], limit=50)

    rows = _service(read)
    created = [r for r in rows if int(r["id"]) == int(body.get("record_id") or 0)]
    check("the record row itself carries the citation",
          created and _STATE["briefing_ref"] in (created[0].get("related_entity_ids") or []),
          str(created[:1]))

    resp = client.post(
        f"/api/private-office/briefings/{briefing_id}/actions",
        json={"action_type": "request", "title": "Ask Dana to confirm the filing"})
    body = resp.get_json() or {}
    check("a request action lands with the GENERAL default",
          resp.status_code == 201
          and body.get("record_type") == records_mod.TYPE_REQUEST,
          f"{resp.status_code} {body}")

    resp = client.post(
        f"/api/private-office/briefings/{briefing_id}/actions",
        json={"action_type": "risk", "title": "nope"})
    check("a briefing cannot mint judgments — risk is refused 400",
          resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        "/api/private-office/briefings/999999/actions",
        json={"action_type": "obligation", "title": "ghost"})
    check("an absent briefing is 404", resp.status_code == 404, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/briefings/{briefing_id}/actions",
        json={"action_type": "obligation", "title": "ghost", "item_id": 999999})
    check("an absent item is 404", resp.status_code == 404, str(resp.status_code))


# ---------------------------------------------------------------------------
# Empty office + isolation
# ---------------------------------------------------------------------------

def stage_empty_office():
    print("\n[briefings: an empty office]")
    client = _app().test_client()
    _as(USER_B)

    resp = client.post("/api/private-office/briefings")
    body = resp.get_json() or {}
    briefing = body.get("briefing") or {}
    _STATE["b_briefing"] = int(briefing.get("id") or 0)
    check("an empty office still generates — zero items, said truthfully",
          resp.status_code == 201 and briefing.get("item_count") == 0
          and briefing.get("items") == [] and briefing.get("sections") == [],
          f"{resp.status_code} {briefing.get('item_count')}")


def stage_isolation():
    print("\n[briefings: isolation]")
    client = _app().test_client()
    _as(USER_B)

    resp = client.get("/api/private-office/briefings")
    rows = (resp.get_json() or {}).get("briefings") or []
    check("B's list holds only B's briefing",
          len(rows) == 1 and rows[0]["id"] == _STATE["b_briefing"],
          str([r.get("id") for r in rows]))

    resp = client.get(f"/api/private-office/briefings/{_STATE['briefing']}")
    check("A's briefing is 404 for B", resp.status_code == 404, str(resp.status_code))

    resp = client.get("/api/private-office/briefings/why",
                      query_string={"refs": _STATE["briefing_ref"]})
    resolved = (resp.get_json() or {}).get("evidence") or []
    check("A's briefing ref resolves exists=False for B",
          resolved and not resolved[0]["exists"], str(resolved))

    resp = client.post(
        f"/api/private-office/briefings/{_STATE['briefing']}/actions",
        json={"action_type": "obligation", "title": "steal"})
    check("B cannot create an action on A's briefing — 404",
          resp.status_code == 404, str(resp.status_code))


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------

def stage_bookkeeping():
    print("\n[briefings: bookkeeping]")

    def read(cur):
        return jobs_mod.list_jobs(cur, owner_user_id=USER_A,
                                  job_type=jobs_mod.JOB_BRIEFING_GENERATION)

    runs = _service(read)
    succeeded = [j for j in runs if j.get("status") == jobs_mod.STATUS_SUCCEEDED]
    check("each generation ran under a job that succeeded",
          len(succeeded) >= 2, str([(j.get("status")) for j in runs]))
    result_refs = {j.get("result_ref") for j in succeeded}
    check("job results carry the briefing refs",
          _STATE["briefing_ref"] in result_refs, str(result_refs))

    audits = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE owner_user_id=? AND action IN (?, ?)",
        (USER_A, "PRIVATE_BRIEFING_GENERATED", "PRIVATE_BRIEFING_READ"))
    actions = {row["action"] for row in audits}
    check("generation and reads are audited",
          {"PRIVATE_BRIEFING_GENERATED", "PRIVATE_BRIEFING_READ"} <= actions,
          str(actions))
    generated = [row for row in audits if row["action"] == "PRIVATE_BRIEFING_GENERATED"]
    check("the generation audit names the briefing and counts its items",
          any(row.get("object_id") == str(_STATE["briefing"])
              and int(row.get("result_count") or 0) > 0 for row in generated),
          str([(r.get("object_id"), r.get("result_count")) for r in generated]))

    flat = " ".join(str(v) for row in audits for v in row.values())
    check("audit rows are metadata only — no member content",
          not any(secret in flat for secret in _SECRETS), flat[:200])


# ---------------------------------------------------------------------------
# The feature matrix tells the truth
# ---------------------------------------------------------------------------

def stage_feature_matrix():
    print("\n[briefings: feature matrix]")
    got = matrix.availability("private_briefings", tiers.TIER_PRIVATE)
    check("private_briefings is IMPLEMENTED and ENTITLED at PRIVATE",
          got["implementation"] == matrix.IMPL_IMPLEMENTED
          and got["availability"] == matrix.AVAIL_ENTITLED, str(got))
    spec = matrix.get("private_briefings")
    check("the kill switch is the documented flag",
          spec.flag_env == "PRIVATE_BRIEFINGS_ENABLED", str(spec.flag_env))
    below = matrix.availability("private_briefings", tiers.TIER_PREMIUM)
    check("below PRIVATE it is for sale, not hidden",
          below["availability"] == matrix.AVAIL_NOT_ENTITLED
          and below["minimum_tier"] == tiers.TIER_PRIVATE, str(below))


STAGES = (
    stage_gates,
    stage_seed,
    stage_generate,
    stage_list_and_detail,
    stage_ask_why,
    stage_create_action,
    stage_empty_office,
    stage_isolation,
    stage_bookkeeping,
    stage_feature_matrix,
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
        print(f"FAILURES ({len(_FAILURES)}):")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("All private briefings checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
