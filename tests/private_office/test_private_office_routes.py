"""Stages 4-6, 8 — the member-facing Private Office HTTP surface.

Same stub-the-monolith pattern as ``test_entitlement_routes.py``::

    python -m pytest tests/private_office/test_private_office_routes.py
    python tests/private_office/test_private_office_routes.py

What these tests are actually defending
---------------------------------------
* The gate is the *server's* answer. While ``private_facts`` is
  NOT_IMPLEMENTED these endpoints refuse everyone, including a
  PRIVATE_OFFICE member — an unbuilt capability is not unlocked by rank, and
  a client that hid the button would still be talking to an endpoint that
  says no. When the matrix flips, the same tests exercise the open path.
* The three refusals stay three. Degraded resolve is 503 ``unavailable``,
  unbuilt is 404, out-of-reach is 403 with a minimum tier. Collapsing them
  would tell a paying member during an outage that they lack access, or offer
  an upgrade for something that does not exist.
* Owner isolation on both verbs, over HTTP, with two real members (Stage 8).
  A body that names another owner writes to the caller's own store; a read
  never returns the other member's row; and a foreign fact id is
  indistinguishable from one that was never issued.
* The response carries the projection, not the row: no ``owner_user_id``, no
  ``fact_key``, no ``subject_id``, no provenance locator, anywhere in the JSON.
"""

import json
import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_member_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route pack can import it ------------------
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
from services.private_office import capital_graph  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import office  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

USER_A = 9301
USER_B = 9302

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


#: Unlock grants, per member, minted once in ``setup_environment``.
#:
#: The Office carries a second lock in front of every data route: a passcode,
#: and a bounded grant proving it was entered on this session. These tests are
#: not about that lock — they are about what the routes do once it is open — so
#: the grant is minted once and injected by the client below. The lock itself
#: gets one direct assertion in ``stage_capital_graph_over_http`` so that
#: "unlocked" stays a thing the tests had to obtain rather than a thing they
#: assumed.
_GRANTS: dict[int, str] = {}
PASSCODE = "849271"


class _GrantClient(FlaskClient):
    """A test client that presents the current member's unlock grant.

    ``setdefault`` rather than assignment on purpose: a caller that passes the
    header explicitly — including an empty one, to exercise a locked request —
    keeps what it passed.
    """

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
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active", "access_enabled": 1}


def _unlock(user_id):
    """Set a passcode and take a grant, both over HTTP.

    Through the real endpoints rather than by writing a grant row directly: a
    grant is bound to the session that earned it, and minting one out of band
    would produce a binding no later request could match.
    """
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
        for uid in (USER_A, USER_B):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None


def _facts_are_live() -> bool:
    return routes.FACTS_FEATURE_ID in feature_matrix.implemented_feature_ids()


def _seed_direct(owner, domain, fact_type, value):
    """Seed through the canonical writer, bypassing the HTTP gate.

    Deliberately not through the endpoint: the read tests must have data to
    read even while the capability is still NOT_IMPLEMENTED and the write
    endpoint is correctly refusing everyone.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        result = facts.record_fact(
            cur, owner_user_id=owner, subject_type=facts.SUBJECT_NODE,
            subject_id="1", fact_type=fact_type, value=value,
            value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_USER_ASSERTED, domain=domain,
        )
        conn.commit()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
def stage_authentication():
    print("\n[authentication]")
    _stub._test_user = None
    client = _app().test_client()
    for path, method in (
        ("/api/private-office/overview", "get"),
        ("/api/private-office/facts", "get"),
        ("/api/private-office/facts", "post"),
    ):
        resp = getattr(client, method)(path, json={})
        check(f"{method.upper()} {path} requires login", resp.status_code == 401,
              str(resp.status_code))


def stage_no_owner_parameter_exists():
    """Stage 8 — isolation as a property of the URL, not of a check."""
    print("\n[shape]")
    app = _app()
    member_paths = {
        "/api/private-office/overview",
        "/api/private-office/facts",
    }
    seen = set()
    for rule in app.url_map.iter_rules():
        path = str(rule.rule)
        if path in member_paths:
            seen.add(path)
            check(f"{path} takes no URL argument", rule.arguments == set(),
                  str(rule.arguments))
    check("both member routes are registered", seen == member_paths, str(seen))
    check("no route in the pack names a user",
          not any("user" in str(r.rule) for r in app.url_map.iter_rules()))


def stage_gate_refuses_by_implementation_first():
    """Stage 13 — rank does not conjure a capability."""
    print("\n[gate]")
    _as(USER_A)
    client = _app().test_client()

    resolved = tiers.resolve_tier(USER_A)
    check("the seeded member really is at the top of the ladder",
          resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE,
          str(resolved.get("effective_tier")))

    resp = client.get("/api/private-office/facts")
    body = resp.get_json()

    if _facts_are_live():
        check("an entitled member is allowed through", resp.status_code == 200,
              f"{resp.status_code} {body}")
    else:
        check("an unbuilt capability is refused even at PRIVATE_OFFICE",
              resp.status_code == 404, f"{resp.status_code} {body}")
        check("the refusal does not offer an upgrade",
              "minimum_tier" not in body, str(body))
        check("the refusal names the implementation state",
              body.get("implementation") == feature_matrix.IMPL_NOT_IMPLEMENTED,
              str(body))
        check("404 rather than 402/403 — nothing to sell",
              body.get("state") == feature_matrix.AVAIL_NOT_IMPLEMENTED, str(body))

    check("the refusal is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))


def stage_degraded_resolve_is_its_own_answer():
    """Stage 176B — 'could not look' and 'you may not' must not share a shape."""
    print("\n[degraded resolve]")
    _as(USER_A)
    original = svc.get_entitlements
    svc.get_entitlements = lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    try:
        client = _app().test_client()
        facts_resp = client.get("/api/private-office/facts")
        overview_resp = client.get("/api/private-office/overview")
    finally:
        svc.get_entitlements = original

    body = facts_resp.get_json()
    check("a degraded resolve is 503, not 403", facts_resp.status_code == 503,
          str(facts_resp.status_code))
    check("it says unavailable rather than denied",
          body.get("state") == "unavailable", str(body))
    check("it does not claim the member is on FREE",
          "effective_tier" not in body and "minimum_tier" not in body, str(body))

    overview = overview_resp.get_json()
    check("the overview reports ok=False during a degraded resolve",
          overview.get("ok") is False, str(overview.get("ok")))
    check("the overview entry state reads UNKNOWN",
          overview["private_office"]["state"] == office.ENTRY_UNKNOWN,
          str(overview["private_office"]["state"]))
    check("the overview lists no domains it could not count",
          overview.get("domains") == [], str(overview.get("domains")))


def stage_overview_is_honest_about_the_room():
    print("\n[overview]")
    _as(USER_A)
    resp = _app().test_client().get("/api/private-office/overview")
    body = resp.get_json()
    check("the overview answers 200", resp.status_code == 200, str(resp.status_code))
    check("it carries the product entry state",
          body["private_office"]["feature_id"] == office.OFFICE_FEATURE_ID)

    state = body["private_office"]["state"]
    if _facts_are_live():
        check("with a live capability the entry is AVAILABLE",
              state == office.ENTRY_AVAILABLE, state)
        check("the domain summary names every domain",
              [row["domain"] for row in body["domains"]] == list(model.DOMAINS),
              str(body.get("domains")))
    else:
        check("with nothing built the entry is UNAVAILABLE",
              state == office.ENTRY_UNAVAILABLE, state)
        check("no counts are returned to somebody the gate would refuse",
              body["domains"] == [], str(body["domains"]))
        check("no upgrade is offered for an empty room",
              body["private_office"]["upgrade_tier"] is None,
              str(body["private_office"]["upgrade_tier"]))

    check("every unavailable child carries a reason",
          all(child.get("reason") for child in body["private_office"]["unavailable"]))
    check("no unavailable child is marked as opening",
          all(child["opens"] is False for child in body["private_office"]["unavailable"]))
    check("the overview is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))


def stage_owner_isolation_over_http():
    """Stage 8 — two real members, asserted through the HTTP surface."""
    print("\n[owner isolation]")
    a_fact = _seed_direct(USER_A, model.DOMAIN_FINANCIAL, "bank_name", "Bank of A")
    b_fact = _seed_direct(USER_B, model.DOMAIN_FINANCIAL, "bank_name", "Bank of B")
    check("both members have a seeded fact",
          a_fact["fact_id"] and b_fact["fact_id"] and a_fact["fact_id"] != b_fact["fact_id"])

    if not _facts_are_live():
        # The endpoint is correctly refusing everyone, so the HTTP read cannot
        # be exercised yet. Assert the isolation at the layer that IS reachable
        # rather than skipping it: a P0 boundary is not deferred because the
        # door above it is shut.
        conn = db.connect()
        try:
            cur = conn.cursor()
            a_rows = facts.list_facts(cur, owner_user_id=USER_A, limit=50)
            values = json.dumps(office.project_facts(a_rows), default=str)
        finally:
            conn.close()
        check("A's projected facts contain nothing of B's", "Bank of B" not in values)
        check("A can see A's own fact", "Bank of A" in values)
        print("  NOTE  HTTP read path not exercised: private_facts is not live yet")
        return

    client = _app().test_client()
    _as(USER_A)
    a_body = client.get("/api/private-office/facts?domain=FINANCIAL").get_json()
    blob = json.dumps(a_body, default=str)
    check("A's response contains A's fact", "Bank of A" in blob)
    check("A's response contains nothing of B's", "Bank of B" not in blob)

    _as(USER_B)
    b_body = client.get("/api/private-office/facts?domain=FINANCIAL").get_json()
    b_blob = json.dumps(b_body, default=str)
    check("B's response contains B's fact", "Bank of B" in b_blob)
    check("B's response contains nothing of A's", "Bank of A" not in b_blob)

    _as(USER_A)
    write = client.post(
        "/api/private-office/facts",
        json={
            "domain": "FINANCIAL", "fact_type": "written_by_a",
            "value": "x", "value_type": "STRING",
            "owner_user_id": USER_B,  # ignored: the owner comes from the session
        },
    )
    check("a body naming another owner still writes to the caller's store",
          write.status_code == 201, f"{write.status_code} {write.get_json()}")
    _as(USER_B)
    after = json.dumps(
        client.get("/api/private-office/facts?domain=FINANCIAL").get_json(), default=str
    )
    check("the smuggled owner did not reach B's store", "written_by_a" not in after)


def stage_response_carries_the_projection():
    print("\n[projection over http]")
    if not _facts_are_live():
        print("  NOTE  skipped: private_facts is not live yet")
        return
    _as(USER_A)
    body = _app().test_client().get("/api/private-office/facts").get_json()
    blob = json.dumps(body, default=str)
    for leaked in ("owner_user_id", "fact_key", "subject_id", "provenance_ref"):
        check(f"{leaked} never appears in the response", leaked not in blob)
    if body.get("facts"):
        first = body["facts"][0]
        check("each fact carries a verification state",
              first["provenance"]["verification"] in (
                  office.VERIFICATION_VERIFIED, office.VERIFICATION_SOURCED,
                  office.VERIFICATION_SELF_REPORTED, office.VERIFICATION_ESTIMATED,
                  office.VERIFICATION_NEEDS_REVIEW))


def stage_input_validation():
    print("\n[input validation]")
    _as(USER_A)
    client = _app().test_client()

    resp = client.get("/api/private-office/facts?domain=NOT_A_DOMAIN")
    if _facts_are_live():
        check("an unknown domain is a 400, not a silent full-store read",
              resp.status_code == 400, str(resp.status_code))
        check("the 400 lists the real vocabulary",
              set(resp.get_json().get("domains") or []) == set(model.DOMAINS))

        bad = client.post("/api/private-office/facts",
                          json={"domain": "NOPE", "fact_type": "x", "value": "1",
                                "value_type": "STRING"})
        check("an unknown domain is rejected on write", bad.status_code == 400)
        bad_type = client.post("/api/private-office/facts",
                               json={"domain": "FINANCIAL", "fact_type": "x",
                                     "value": "1", "value_type": "NOPE"})
        check("an unknown value type is rejected on write", bad_type.status_code == 400)
        rejected = client.post("/api/private-office/facts",
                               json={"domain": "FINANCIAL", "fact_type": "Bad Type!",
                                     "value": "1", "value_type": "STRING"})
        check("the writer's own validation surfaces as a 400",
              rejected.status_code == 400, str(rejected.status_code))
    else:
        check("validation is not reachable before the gate",
              resp.status_code == 404, str(resp.status_code))
        print("  NOTE  write validation not exercised: private_facts is not live yet")


def _seed_capital(owner, suffix):
    """A two-node owned chain plus one fact, written through the canonical writer.

    Seeded directly rather than over HTTP for the same reason as ``_seed_direct``:
    there is no write surface for the graph, and the read tests need something
    real to read. ``suffix`` keeps the two members' external refs distinguishable
    so a leak shows up as a literal string in the other member's JSON.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        person = graph.upsert_node(
            cur, owner_user_id=owner, node_type=model.NODE_PERSON,
            external_ref=f"person-{suffix}", domain=model.DOMAIN_GENERAL,
        )
        prop = graph.upsert_node(
            cur, owner_user_id=owner, node_type=model.NODE_PROPERTY,
            external_ref=f"property-{suffix}", domain=model.DOMAIN_FINANCIAL,
        )
        graph.record_edge(
            cur, owner_user_id=owner, source=person["node_id"],
            relation_type=model.RELATION_OWNS, target=prop["node_id"],
            provenance_type=model.PROVENANCE_USER_ASSERTED,
        )
        facts.record_fact(
            cur, owner_user_id=owner, subject_type=facts.SUBJECT_NODE,
            subject_id=str(prop["node_id"]), fact_type="street_address",
            value=f"1 {suffix} Lane", value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_VERIFIED,
            domain=model.DOMAIN_FINANCIAL,
        )
        conn.commit()
        return {"person": person["node_id"], "property": prop["node_id"]}
    finally:
        conn.close()


def stage_capital_graph_over_http():
    """Batch B item 6 — the three read routes, asserted at the wire.

    The service-level properties are pinned in ``test_capital_graph.py``; what
    can only be asserted here is that the HTTP layer does not undo them — that
    the gate is applied, that the projection survives serialisation, and that a
    foreign node id is byte-identical to one that was never issued.
    """
    print("\n[capital graph over http]")
    a_nodes = _seed_capital(USER_A, "alpha")
    b_nodes = _seed_capital(USER_B, "bravo")

    paths = (
        "/api/private-office/capital-graph",
        f"/api/private-office/entities/{a_nodes['property']}",
        f"/api/private-office/entities/{a_nodes['property']}/relationships",
    )

    _stub._test_user = None
    anon = _app().test_client()
    for path in paths:
        check(f"GET {path} requires login", anon.get(path).status_code == 401)

    _as(USER_A)
    client = _app().test_client()

    # The second lock, asserted once and directly: an entitled, signed-in member
    # presenting no unlock grant is refused with 423 and given no Office data.
    # Everything below rides on a grant the suite actually had to obtain.
    locked = client.get("/api/private-office/capital-graph",
                        headers={routes.GRANT_HEADER: ""})
    check("a locked request is refused 423, not served",
          locked.status_code == 423, str(locked.status_code))
    check("the locked refusal carries no graph",
          "capital_graph" not in (locked.get_json() or {}), str(locked.get_json()))

    live = capital_graph.FEATURE_ID in feature_matrix.implemented_feature_ids()
    if not live:
        for path in paths:
            check(f"GET {path} is refused while the feature is off",
                  client.get(path).status_code == 404)
        print("  NOTE  read path not exercised: capital_graph is not live")
        return

    resp = client.get("/api/private-office/capital-graph")
    body = resp.get_json()
    check("an entitled member is allowed through", resp.status_code == 200,
          f"{resp.status_code} {body}")
    check("the response advertises the real views",
          set(body.get("views") or []) == set(capital_graph.VIEWS),
          str(body.get("views")))
    check("the capital graph is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))

    bad_view = client.get("/api/private-office/capital-graph?view=nope")
    check("an unknown view is a 400, not a silent default read",
          bad_view.status_code == 400, str(bad_view.status_code))
    check("the 400 lists the real vocabulary",
          set(bad_view.get_json().get("views") or []) == set(capital_graph.VIEWS))

    blob = json.dumps(body, default=str)
    # No aggregate may appear on the wire. A number labelled like a net worth is
    # read as one whether or not the server meant it that way.
    for invented in ("net_worth", "networth", "total_value", "total_assets",
                     "portfolio_value", "estate_value"):
        check(f"the payload states no {invented}", invented not in blob.lower())
    for leaked in ("owner_user_id", "node_key", "edge_key", "subject_id",
                   "provenance_ref"):
        check(f"{leaked} never appears in the response", leaked not in blob)
    check("A's payload contains A's own property", "1 alpha Lane" in blob)
    check("A's payload contains nothing of B's",
          "bravo" not in blob and "1 bravo Lane" not in blob)

    entity = client.get(f"/api/private-office/entities/{a_nodes['property']}")
    check("the entity route answers 200", entity.status_code == 200,
          str(entity.status_code))
    check("it returns the entity that was asked for",
          (entity.get_json().get("entity") or {}).get("id") == a_nodes["property"],
          str(entity.get_json().get("entity")))

    rels = client.get(
        f"/api/private-office/entities/{a_nodes['property']}/relationships")
    rels_body = rels.get_json()
    check("the relationships route answers 200", rels.status_code == 200,
          str(rels.status_code))
    check("it carries the owning edge",
          any(row.get("relation_type") == model.RELATION_OWNS
              for row in (rels_body.get("relationships") or [])),
          str(rels_body.get("relationships")))

    # The surface is not an existence oracle: a real foreign node and an id that
    # was never issued must be indistinguishable, or a caller can enumerate the
    # other member's graph by diffing responses.
    foreign = client.get(f"/api/private-office/entities/{b_nodes['property']}")
    absent = client.get("/api/private-office/entities/99999999")
    check("a foreign node answers exactly like a nonexistent one",
          foreign.status_code == absent.status_code
          and foreign.get_json() == absent.get_json(),
          f"{foreign.status_code}/{absent.status_code}")

    # The kill switch, at the wire. A switched-off feature must 404 without
    # offering anything to buy, and must not take the fact routes with it.
    spec = feature_matrix.FEATURES[capital_graph.FEATURE_ID]
    previous = os.environ.get(spec.flag_env)
    os.environ[spec.flag_env] = "false"
    try:
        off = client.get("/api/private-office/capital-graph")
        check("the kill switch closes the route", off.status_code == 404,
              str(off.status_code))
        check("the switched-off route sells nothing",
              "minimum_tier" not in (off.get_json() or {}), str(off.get_json()))
        check("the switch does not take the fact routes down with it",
              client.get("/api/private-office/facts").status_code == 200)
    finally:
        if previous is None:
            os.environ.pop(spec.flag_env, None)
        else:
            os.environ[spec.flag_env] = previous


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_authentication()
    stage_no_owner_parameter_exists()
    stage_gate_refuses_by_implementation_first()
    stage_degraded_resolve_is_its_own_answer()
    stage_overview_is_honest_about_the_room()
    stage_owner_isolation_over_http()
    stage_response_carries_the_projection()
    stage_input_validation()
    stage_capital_graph_over_http()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_office_member_routes():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
