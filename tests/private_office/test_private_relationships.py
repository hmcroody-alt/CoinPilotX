"""Relationship Intelligence — people, profiles, cited timelines, briefings.

Run either way::

    python -m pytest tests/private_office/test_private_relationships.py
    python tests/private_office/test_private_relationships.py

What these tests defend
-----------------------
* **The gate order holds over HTTP.** No session is 401, no tier is 403, the
  kill switch is 404, a locked Office is 423 — before any person is named.
* **People live in the substrate, not beside it.** A person is a PERSON node;
  their name and role are facts with ``USER_ASSERTED`` provenance. There is no
  people table for this module to have invented, and the write-boundary suite
  keeps it that way structurally.
* **Counts are counts.** The directory's ``open_commitments`` is computed from
  the same records the profile lists — resolve an obligation and the number
  moves; a LIKE-style ``node:1``/``node:12`` collision never inflates it.
* **Every line is cited.** Profile timelines and briefings carry evidence refs
  that parse under the shared vocabulary, and preparing a briefing writes
  nothing — it is a view, audited as a retrieval.
* **Owner isolation everywhere.** Another member's person is indistinguishable
  from one that never existed: directory, profile, fact writes and briefings
  all answer the not-found shape.
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_relationships_"), "test.db")
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
from services import private_office_relationships_routes as rel_routes  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts as facts_mod  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import graph as graph_mod  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import records as records_mod  # noqa: E402
from services.private_office import relationships as relationships_mod  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

USER_A = 9911
USER_B = 9912
USER_C = 9913  # a real session with no Private tier — the 403 case

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
    rel_routes.register(app)
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


def _fact_count(owner):
    conn = db.connect()
    try:
        return facts_mod.count_facts(conn.cursor(), owner_user_id=owner)
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
    print("\n[relationships: gates]")
    client = _app().test_client()

    _stub._test_user = None
    resp = client.get("/api/private-office/relationships")
    check("no session is 401", resp.status_code == 401, str(resp.status_code))

    _as(USER_C)
    resp = client.get("/api/private-office/relationships")
    body = resp.get_json() or {}
    check("no Private tier is 403 with a minimum tier",
          resp.status_code == 403 and body.get("minimum_tier"),
          f"{resp.status_code} {body}")

    previous = os.environ.get("PRIVATE_RELATIONSHIPS_ENABLED")
    os.environ["PRIVATE_RELATIONSHIPS_ENABLED"] = "false"
    try:
        _as(USER_A)
        resp = client.get("/api/private-office/relationships")
        check("kill switch off is 404 for an entitled member",
              resp.status_code == 404, str(resp.status_code))
        state = matrix.availability(rel_routes.RELATIONSHIPS_FEATURE_ID, tiers.TIER_PRIVATE)
        check("flag off reads as FEATURE_DISABLED, implementation still IMPLEMENTED",
              state["availability"] == matrix.AVAIL_FEATURE_DISABLED
              and state["implementation"] == matrix.IMPL_IMPLEMENTED, str(state))
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_RELATIONSHIPS_ENABLED", None)
        else:
            os.environ["PRIVATE_RELATIONSHIPS_ENABLED"] = previous

    _as(USER_A)
    resp = client.get("/api/private-office/relationships",
                      headers={routes.GRANT_HEADER: ""})
    check("no unlock grant is 423 Locked", resp.status_code == 423, str(resp.status_code))

    resp = client.get("/api/private-office/relationships")
    body = resp.get_json() or {}
    check("unlocked member reaches the directory",
          resp.status_code == 200 and body.get("ok"), str(resp.status_code))
    check("payload states there is no inference and no provider",
          (body.get("provider_status") or {}).get("inference") == "none"
          and (body.get("provider_status") or {}).get("source") == "private_office_records",
          str(body.get("provider_status")))
    check("no-store on the directory",
          "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))


# ---------------------------------------------------------------------------
# Adding people
# ---------------------------------------------------------------------------

def stage_add_person():
    print("\n[relationships: adding people]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post("/api/private-office/relationships",
                       json={"name": "Dana Whitfield", "role": "Estate Attorney"})
    body = resp.get_json() or {}
    person = body.get("person") or {}
    check("adding a person is 201", resp.status_code == 201 and body.get("ok"),
          f"{resp.status_code} {body}")
    check("the person carries a node id and a parseable ref",
          person.get("node_id") and evidence.parse_ref(person.get("ref")) == ("node", person["node_id"]),
          str(person))
    check("name and role echo back normalized",
          person.get("name") == "Dana Whitfield" and person.get("role") == "Estate Attorney",
          str(person))
    _STATE["dana"] = int(person.get("node_id") or 0)
    _STATE["dana_ref"] = person.get("ref") or ""

    resp = client.post("/api/private-office/relationships", json={"name": "   "})
    check("a person without a name is 400", resp.status_code == 400, str(resp.status_code))

    resp = client.post("/api/private-office/relationships", json={"name": "Dana Whitfield"})
    twin = (resp.get_json() or {}).get("person") or {}
    check("a same-name add is a second person, never a silent merge",
          resp.status_code == 201 and twin.get("node_id")
          and twin["node_id"] != _STATE["dana"], str(twin))
    _STATE["twin"] = int(twin.get("node_id") or 0)

    resp = client.post("/api/private-office/relationships",
                       json={"name": "Marcus Osei", "role": "Accountant"})
    marcus = (resp.get_json() or {}).get("person") or {}
    _STATE["marcus"] = int(marcus.get("node_id") or 0)
    check("a third person lands", resp.status_code == 201 and _STATE["marcus"] > 0, str(marcus))


# ---------------------------------------------------------------------------
# Facts about a person
# ---------------------------------------------------------------------------

def stage_person_facts():
    print("\n[relationships: person facts]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post(
        f"/api/private-office/relationships/{_STATE['dana']}/facts",
        json={"fact_type": "firm", "value": "Whitfield & Co"})
    body = resp.get_json() or {}
    check("a member-asserted fact lands with a fact id",
          resp.status_code == 201 and body.get("ok") and body.get("fact_id"),
          f"{resp.status_code} {body}")

    resp = client.post("/api/private-office/relationships/999999/facts",
                       json={"fact_type": "firm", "value": "Nowhere"})
    check("a fact about an absent person is 404", resp.status_code == 404,
          str(resp.status_code))

    resp = client.post(
        f"/api/private-office/relationships/{_STATE['dana']}/facts",
        json={"fact_type": "", "value": "x"})
    check("a fact without a type is refused", resp.status_code in (400, 404),
          str(resp.status_code))


# ---------------------------------------------------------------------------
# Commitments and connections — through the existing primitives
# ---------------------------------------------------------------------------

def stage_commitments_and_connections():
    print("\n[relationships: commitments and connections]")
    dana_ref = _STATE["dana_ref"]

    def work(cur):
        first = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Renew umbrella policy", obligation_type="INSURANCE",
            related_entity_ids=[dana_ref], actor_user_id=USER_A)
        second = records_mod.create_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Sign updated will", obligation_type="LEGAL",
            related_entity_ids=[dana_ref], actor_user_id=USER_A)
        req = records_mod.create_record(
            cur, record_type=records_mod.TYPE_REQUEST, owner_user_id=USER_A,
            title="Schedule estate review", category="LEGAL",
            related_entity_ids=[dana_ref], actor_user_id=USER_A)
        pro = graph_mod.upsert_node(
            cur, owner_user_id=USER_A, node_type=model.NODE_PROFESSIONAL,
            external_ref="whitfield-co", actor_user_id=USER_A)
        edge = graph_mod.record_edge(
            cur, owner_user_id=USER_A, source=_STATE["dana"],
            relation_type=model.RELATION_ADVISED_BY, target=int(pro["node_id"]),
            provenance_type=model.PROVENANCE_USER_ASSERTED, actor_user_id=USER_A)
        return first, second, req, edge

    first, second, req, edge = _service(work)
    _STATE["obligation_open"] = int(first["record_id"])
    _STATE["obligation_to_close"] = int(second["record_id"])
    _STATE["request"] = int(req["record_id"])
    check("obligations and a request cite the person's node ref",
          all(x["status"] in ("created", "existing") for x in (first, second, req)),
          f"{first} {second} {req}")
    check("an ADVISED_BY connection lands",
          edge.get("status") in ("written", "refreshed"), str(edge))

    def close(cur):
        return records_mod.update_record(
            cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
            record_id=_STATE["obligation_to_close"], status="RESOLVED",
            actor_user_id=USER_A)

    closed = _service(close)
    check("one obligation resolves", (closed or {}).get("status") not in (None, "absent"),
          str(closed))


# ---------------------------------------------------------------------------
# Directory
# ---------------------------------------------------------------------------

def stage_directory():
    print("\n[relationships: directory]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.get("/api/private-office/relationships")
    body = resp.get_json() or {}
    people = body.get("people") or []
    check("the directory lists every person", len(people) == 3, str(len(people)))
    check("newest first", people and people[0]["node_id"] == _STATE["marcus"],
          str([p["node_id"] for p in people]))

    by_id = {p["node_id"]: p for p in people}
    dana = by_id.get(_STATE["dana"]) or {}
    check("identity comes from the fact store",
          dana.get("name") == "Dana Whitfield" and dana.get("role") == "Estate Attorney",
          str(dana))
    check("open commitments counts open rows only — resolved ones dropped out",
          dana.get("open_commitments") == 2, str(dana.get("open_commitments")))
    check("the connection is counted", dana.get("connections") == 1,
          str(dana.get("connections")))
    check("an uninvolved person counts zero",
          (by_id.get(_STATE["marcus"]) or {}).get("open_commitments") == 0
          and (by_id.get(_STATE["marcus"]) or {}).get("connections") == 0,
          str(by_id.get(_STATE["marcus"])))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def stage_profile():
    print("\n[relationships: profile]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.get(f"/api/private-office/relationships/{_STATE['dana']}")
    body = resp.get_json() or {}
    person = body.get("person") or {}
    check("the profile answers 200", resp.status_code == 200 and body.get("ok"),
          str(resp.status_code))

    facts = person.get("facts") or []
    by_type = {f["fact_type"]: f for f in facts}
    check("the name is a fact with USER_ASSERTED provenance",
          by_type.get("name", {}).get("value") == "Dana Whitfield"
          and by_type.get("name", {}).get("provenance_type") == model.PROVENANCE_USER_ASSERTED,
          str(by_type.get("name")))
    check("the member's own fact shows", by_type.get("firm", {}).get("value") == "Whitfield & Co",
          str(by_type.get("firm")))

    check("connections carry relation and far end",
          len(person.get("connections") or []) == 1
          and person["connections"][0]["relation_type"] == model.RELATION_ADVISED_BY
          and person["connections"][0]["other_node_type"] == model.NODE_PROFESSIONAL,
          str(person.get("connections")))

    commitments = person.get("commitments") or []
    check("commitments list the citing obligations and request",
          len(commitments) == 3 and sum(1 for c in commitments if c["open"]) == 2,
          str(commitments))

    timeline = person.get("timeline") or []
    check("the timeline is populated and every line carries a parseable ref",
          timeline and all(evidence.parse_ref(item.get("ref")) for item in timeline),
          str(timeline[:3]))
    ats = [item.get("at") or "" for item in timeline]
    check("the timeline is newest first", ats == sorted(ats, reverse=True), str(ats))

    resp = client.get("/api/private-office/relationships/999999")
    check("an absent person is 404", resp.status_code == 404, str(resp.status_code))


# ---------------------------------------------------------------------------
# Briefing preparation
# ---------------------------------------------------------------------------

def stage_briefing():
    print("\n[relationships: briefing]")
    client = _app().test_client()
    _as(USER_A)

    facts_before = _fact_count(USER_A)
    resp = client.get(f"/api/private-office/relationships/{_STATE['dana']}/briefing")
    body = resp.get_json() or {}
    briefing = body.get("briefing") or {}
    check("the briefing answers 200", resp.status_code == 200 and body.get("ok"),
          str(resp.status_code))
    check("it names the person it is about",
          (briefing.get("person") or {}).get("node_id") == _STATE["dana"],
          str(briefing.get("person")))
    check("open commitments only — the resolved obligation is absent",
          len(briefing.get("open_commitments") or []) == 2
          and all(c["open"] for c in briefing["open_commitments"]),
          str(briefing.get("open_commitments")))
    check("recent activity is capped", len(briefing.get("recent_activity") or []) <= 10,
          str(len(briefing.get("recent_activity") or [])))

    refs = briefing.get("evidence") or []
    fact_refs = [f["ref"] for f in briefing.get("known_facts") or []]
    check("the evidence union covers the person and every quoted fact",
          _STATE["dana_ref"] in refs and all(r in refs for r in fact_refs),
          str(refs))
    check("it declares its source truthfully",
          briefing.get("generated_from") == "private_office_records",
          str(briefing.get("generated_from")))
    check("preparing a briefing writes no facts",
          _fact_count(USER_A) == facts_before,
          f"{facts_before} -> {_fact_count(USER_A)}")

    rows = _query_all(
        f"SELECT action, object_id FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id=? AND action=?",
        (USER_A, "PRIVATE_CONTEXT_RETRIEVED"))
    check("the retrieval is audited as such",
          any(str(r.get("object_id")) == str(_STATE["dana"]) for r in rows), str(rows))


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def stage_isolation():
    print("\n[relationships: owner isolation]")
    client = _app().test_client()
    _as(USER_B)

    resp = client.get("/api/private-office/relationships")
    people = (resp.get_json() or {}).get("people") or []
    check("another member's directory is empty of A's people",
          resp.status_code == 200 and not people, str(people))

    resp = client.get(f"/api/private-office/relationships/{_STATE['dana']}")
    check("A's person is not-found for B", resp.status_code == 404, str(resp.status_code))

    resp = client.get(f"/api/private-office/relationships/{_STATE['dana']}/briefing")
    check("A's briefing is not-found for B", resp.status_code == 404, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/relationships/{_STATE['dana']}/facts",
        json={"fact_type": "firm", "value": "intrusion"})
    check("B cannot write a fact onto A's person", resp.status_code == 404,
          str(resp.status_code))
    check("and nothing landed for A",
          not _query_all(
              f"SELECT id FROM {schema.FACTS_TABLE} "
              f"WHERE owner_user_id=? AND typed_value=?", (USER_A, "intrusion")))


# ---------------------------------------------------------------------------
# Bookkeeping and matrix truth
# ---------------------------------------------------------------------------

def stage_bookkeeping():
    print("\n[relationships: audit]")
    rows = _query_all(
        f"SELECT action, object_type, object_id FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id=?", (USER_A,))
    actions = {row["action"] for row in rows}
    check("directory/profile reads and briefing retrievals are audited",
          {"PRIVATE_GRAPH_READ", "PRIVATE_CONTEXT_RETRIEVED"} <= actions,
          str(sorted(actions)))
    leaked = [r for r in rows if "Dana" in str(r.get("object_id"))
              or "Whitfield" in str(r.get("object_type"))]
    check("audit rows carry identity, never a name", not leaked, str(leaked))


def stage_feature_matrix():
    print("\n[relationships: feature matrix truth]")
    spec = matrix.get(rel_routes.RELATIONSHIPS_FEATURE_ID)
    check("the row is IMPLEMENTED with a kill switch",
          spec is not None and spec.implementation == matrix.IMPL_IMPLEMENTED
          and spec.flag_env == "PRIVATE_RELATIONSHIPS_ENABLED", str(spec))
    state = matrix.availability(rel_routes.RELATIONSHIPS_FEATURE_ID, tiers.TIER_PRIVATE)
    check("a Private member is entitled by default",
          state["availability"] == matrix.AVAIL_ENTITLED, str(state))


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

STAGES = (
    stage_gates,
    stage_add_person,
    stage_person_facts,
    stage_commitments_and_connections,
    stage_directory,
    stage_profile,
    stage_briefing,
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
        print(f"FAILED ({len(_FAILURES)}):")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("ALL RELATIONSHIP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
