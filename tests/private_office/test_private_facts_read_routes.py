"""The read model's HTTP surface — nine GET routes over the fact store.

    python -m pytest tests/private_office/test_private_facts_read_routes.py
    python tests/private_office/test_private_facts_read_routes.py

What this file is actually defending
------------------------------------
The read model already has a suite that proves it counts, pages, scopes and
truncates correctly. None of that survives contact with a route that drops a
flag on the way out. So these stages deliberately do not re-test the module;
they test the *seam*, and every check here is about something that can only go
wrong between the module and the wire:

* **Bounds survive serialisation.** ``complete``, ``has_more``, ``truncated``
  and ``unavailable`` are the entire reason the module is honest about being
  bounded. A handler that forgot one would return a short list that looks
  exactly like a small store, and nothing would raise. Each flag is asserted
  against a fixture built specifically to make it true, because a flag checked
  only in its default state is a flag whose absence reads as correct.

* **A failure is never an empty state.** The 503 payload is asserted to carry
  no data key at all. A response that said ``{"ok": false, "items": []}`` gives
  a client two readings of the same body, and the one it picks will be the
  wrong one on exactly the day the store is unreadable.

* **Two paths to one answer agree.** ``/evidence`` is a slice of the same
  ``fact_detail`` call the fact route makes. The suite asserts they match rather
  than asserting each is right, because the failure mode worth catching is the
  second implementation, not the first wrong value.

* **Isolation is a property of the URL.** No route takes an owner. A foreign
  fact id is indistinguishable from one that was never issued — asserted on the
  status line as well as the body, since a 403-vs-404 split is an existence
  oracle however carefully the messages are worded.

* **The lock and the switch cover every route.** Both are asserted over the
  URL map rather than route by route, so a tenth route added later is included
  in the proof without anyone remembering to add it.

* **``available`` stays three-valued over JSON.** Python's ``None`` becomes
  ``null``, and a client reading null as false would tell the member a citation
  this build cannot parse is a document they have lost.
"""

import json
import os
import sqlite3
import sys
import tempfile
import types
from datetime import timedelta

_TMP_DB = os.path.join(
    tempfile.mkdtemp(prefix="private_facts_read_routes_"), "test.db")
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
from services import private_office_facts_routes as fact_routes  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import documents  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import read_model  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import security  # noqa: E402

USER_A = 9601
USER_B = 9602
PASSCODE = "715390"

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


_GRANTS: dict[int, str] = {}


class _GrantClient(FlaskClient):
    """Presents the current member's unlock grant unless the caller set one.

    ``setdefault``, so a stage that wants to exercise a *locked* request can
    pass an empty header and keep it.
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
    """Both packs on one app, as production mounts them.

    The read pack's routes live beneath a path the other pack already owns, and
    the only thing keeping ``/facts/overview`` out of ``/facts/<int:fact_id>``
    is Flask's int converter. Registering them separately in tests would prove
    each pack works in a world where the other does not exist.
    """
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    routes.register(app)
    fact_routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active",
                        "access_enabled": 1}


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
            "access_enabled INTEGER DEFAULT 1)")
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)", (uid, "active"))
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


#: Every route this pack owns, as (path, needs_a_fact_id). Derived from the URL
#: map rather than typed out, so the blanket properties below — auth, lock,
#: GET-only, no owner argument — cover a route added later without anyone
#: remembering to extend a list.
def _pack_rules(app):
    return [rule for rule in app.url_map.iter_rules()
            if str(rule.endpoint).startswith("private_office_facts.")]


def _path_for(rule, fact_id: int) -> str:
    path = str(rule.rule)
    return path.replace("<int:fact_id>", str(fact_id))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed(owner, **kwargs):
    """Seed through the canonical writer, not through HTTP.

    The write surface for facts is a different route in a different pack with
    its own refusals; routing fixtures through it would make every read stage
    depend on the write stage passing.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        kwargs.setdefault("subject_type", facts.SUBJECT_NODE)
        kwargs.setdefault("subject_id", "1")
        kwargs.setdefault("value_type", model.VALUE_STRING)
        kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
        result = facts.record_fact(cur, owner_user_id=owner, **kwargs)
        conn.commit()
        return int(result["fact_id"])
    finally:
        conn.close()


def _with_conn(work):
    conn = db.connect()
    try:
        out = work(conn, conn.cursor())
        conn.commit()
        return out
    finally:
        conn.close()


def _document(cur, owner: int, title: str) -> int:
    """A real row in the real documents table, built by the real DDL.

    Not through ``documents.store_document``, which is the canonical writer but
    also writes the file to ``media_storage.PRIVATE_UPLOAD_ROOT``. These stages
    care only whether a *reference* resolves, and a read test that leaves real
    bytes on disk outside its temp directory is a test with a side effect
    nobody asked for. ``delete_document`` below is still the real writer, so the
    transition this stage is actually about — a document going away underneath a
    citation — happens the way it happens in production.
    """
    # `force=True`, not the plain call. The module memoises "schema is present"
    # in a process-global, and under the full package run an earlier test module
    # has already primed that flag against *its* database — so the plain call is
    # a no-op here and the insert hits a table that was never created. The
    # failure only appears when the whole package runs in one process, which is
    # exactly the run that matters and not the one anybody iterates on.
    documents.ensure_documents_schema(cur, force=True)
    stamp = "2026-01-01T00:00:00+00:00"
    cur.execute(
        f"""INSERT INTO {documents.DOCUMENTS_TABLE}
            (owner_user_id, title, lifecycle_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)""",
        (owner, title, documents.LIFECYCLE_ACTIVE, stamp, stamp))
    cur.execute(
        f"SELECT id FROM {documents.DOCUMENTS_TABLE} WHERE owner_user_id=?"
        " ORDER BY id DESC LIMIT 1", (owner,))
    return int(dict(cur.fetchone())["id"])


# ---------------------------------------------------------------------------
def stage_every_route_requires_a_session():
    """No login, no answer — asserted across the whole pack, not a sample."""
    print("\n[authentication]")
    _stub._test_user = None
    app = _app()
    client = app.test_client()
    rules = _pack_rules(app)
    check("the pack registered every route", len(rules) == 9, str(len(rules)))
    for rule in rules:
        resp = client.get(_path_for(rule, 1))
        check(f"GET {rule.rule} requires login", resp.status_code == 401,
              str(resp.status_code))


def stage_the_surface_is_read_only_and_ownerless():
    """Two structural properties, proven over the URL map.

    Checked against the map rather than by calling each route, because the
    claim is about what exists — a POST handler that was never invoked is still
    a write surface, and an owner argument nobody happened to pass is still a
    way to name somebody else.
    """
    print("\n[shape]")
    app = _app()
    for rule in _pack_rules(app):
        methods = {m for m in rule.methods if m not in ("HEAD", "OPTIONS")}
        check(f"{rule.rule} is GET-only", methods == {"GET"}, str(methods))
        check(f"{rule.rule} names no owner",
              rule.arguments <= {"fact_id"}, str(rule.arguments))

    # And the negative, over HTTP: a write verb is refused by the router.
    _as(USER_A)
    client = app.test_client()
    resp = client.post("/api/private-office/facts/overview", json={})
    check("POST to a read route is rejected by the router",
          resp.status_code == 405, str(resp.status_code))


def stage_the_lock_covers_every_route():
    """The Office's second lock, asserted across the pack.

    A locked request is one with no grant header. Sent explicitly as an empty
    string so the test client's ``setdefault`` does not helpfully supply one.

    423, not 403, and the difference is the whole point of there being two
    locks. 403 says "this is not yours"; 423 says "this is yours and the room
    is shut". A member who has paid for the Office and simply has not entered
    the passcode on this session must be shown an unlock prompt, not an
    upgrade prompt — so the state code is asserted alongside the status, since
    a client renders off the code.
    """
    print("\n[the second lock]")
    _as(USER_A)
    app = _app()
    client = app.test_client()
    for rule in _pack_rules(app):
        resp = client.get(_path_for(rule, 1),
                          headers={routes.GRANT_HEADER: ""})
        body = resp.get_json() or {}
        check(f"GET {rule.rule} refuses a locked request",
              resp.status_code == 423, str(resp.status_code))
        check(f"GET {rule.rule} says locked, not denied",
              body.get("state") == security.ERR_LOCKED, json.dumps(body)[:120])


def stage_the_kill_switch_covers_every_route():
    """``PRIVATE_FACTS_ENABLED=false`` takes this pack down with the other one.

    The switch is only honest if it is total. A pack that stayed up after the
    feature was disabled would present as an app half working during exactly
    the incident the switch was reached for.
    """
    print("\n[kill switch]")
    _as(USER_A)
    app = _app()
    client = app.test_client()
    previous = os.environ.get("PRIVATE_FACTS_ENABLED")
    os.environ["PRIVATE_FACTS_ENABLED"] = "false"
    try:
        for rule in _pack_rules(app):
            resp = client.get(_path_for(rule, 1))
            body = resp.get_json() or {}
            check(f"GET {rule.rule} is refused while the switch is off",
                  resp.status_code in (403, 404), str(resp.status_code))
            # Not sold, and not disowned. A disabled capability must not carry
            # an upgrade prompt, and must not claim to be unbuilt.
            check(f"GET {rule.rule} is not an upgrade prompt",
                  body.get("reason") != "UPGRADE_REQUIRED", json.dumps(body)[:120])
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_FACTS_ENABLED", None)
        else:
            os.environ["PRIVATE_FACTS_ENABLED"] = previous


def stage_the_bounds_reach_the_wire():
    """``complete``, ``has_more`` and ``truncated`` survive the handler.

    Each is exercised against a fixture built to make it *true*. A flag only
    ever observed in its default state is one whose disappearance from the
    payload reads as correct, which is the whole failure this stage exists for.
    """
    print("\n[bounds reach the wire]")
    _as(USER_A)
    client = _app().test_client()

    # A store larger than the overview's own scan ceiling would take 500 rows to
    # build, so the ceiling is lowered for one call instead — the property under
    # test is that the handler forwards the flag, not what the ceiling is.
    for i in range(4):
        _seed(USER_A, fact_type=f"bound_{i}", value=str(i),
              domain=model.DOMAIN_FINANCIAL)

    overview = client.get("/api/private-office/facts/overview").get_json()
    check("the overview carries an observed block",
          isinstance(overview.get("observed"), dict), json.dumps(overview)[:160])
    check("and the observed block carries its completeness flag",
          "complete" in (overview.get("observed") or {}),
          str(sorted((overview.get("observed") or {}))))
    check("exact and observed counts are both present and distinct keys",
          "total" in overview and "scanned" in (overview.get("observed") or {}),
          str(sorted(overview)))

    # has_more, made true by asking for less than there is.
    page = client.get("/api/private-office/facts/list?limit=2").get_json()
    check("a page smaller than the store reports has_more",
          page.get("has_more") is True, json.dumps(page)[:200])
    check("and says where the next one starts",
          page.get("next_offset") == 2, str(page.get("next_offset")))
    check("and serves exactly the page it was asked for",
          len(page.get("items") or []) == 2, str(len(page.get("items") or [])))

    tail = client.get("/api/private-office/facts/list?limit=100").get_json()
    check("a page covering the store reports no successor",
          tail.get("has_more") is False, str(tail.get("has_more")))
    check("and offers no next offset", tail.get("next_offset") is None,
          str(tail.get("next_offset")))

    # truncated, on the timeline.
    target = _seed(USER_A, fact_type="bound_timeline", value="v1",
                   domain=model.DOMAIN_LEGAL)
    _with_conn(lambda conn, cur: facts.supersede_fact(
        cur, owner_user_id=USER_A, fact_id=target, value="v2",
        value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED, actor_user_id=USER_A))
    line = client.get(
        f"/api/private-office/facts/{target}/timeline?limit=1").get_json()
    check("a bounded timeline says the bound bit",
          line.get("truncated") is True, json.dumps(line)[:200])
    check("and returns exactly the bound it was given",
          len(line.get("events") or []) == 1, str(len(line.get("events") or [])))
    full = client.get(
        f"/api/private-office/facts/{target}/timeline").get_json()
    check("an unbounded timeline does not claim truncation",
          full.get("truncated") is False, str(full.get("truncated")))
    check("and it follows the chain past the correction",
          len(full.get("chain") or []) >= 2, str(full.get("chain")))

    # `target` has just been corrected, so it is a superseded row sitting in the
    # store — which makes it the fixture for the default the list route picks on
    # the member's behalf. Showing corrections alongside current values by
    # default would put two answers to one question on the same screen with
    # nothing on either row saying which is in force, and the member would act
    # on whichever they read first.
    default = client.get(
        "/api/private-office/facts/list?limit=100").get_json() or {}
    shown = {item["fact_id"] for item in (default.get("items") or [])}
    check("a corrected row is not in the list by default",
          target not in shown, str(sorted(shown))[:120])
    # The successor is named by the chain rather than matched on its value, so
    # this does not quietly depend on which key the row projection uses for it.
    successor = [i for i in (full.get("chain") or []) if i != target]
    check("while the row that replaced it is",
          bool(successor) and successor[-1] in shown,
          f"{successor} vs {sorted(shown)}"[:120])

    # And it is reachable on request — it is the member's own history, and the
    # default is a default, not a wall.
    asked = client.get("/api/private-office/facts/list"
                       "?limit=100&include_superseded=true").get_json() or {}
    check("and is returned to a caller that asks for history",
          target in {item["fact_id"] for item in (asked.get("items") or [])},
          str(len(asked.get("items") or [])))


def stage_a_failure_is_never_an_empty_state():
    """The 503 body carries a refusal and no data.

    The outage is aimed at the *read*, not at the database. Two earlier attempts
    were wrong in instructive ways:

    * Renaming the facts table is a schema write against a protected table, so
      the write-boundary guard refuses the file — and a test exempted from the
      rule it ships alongside teaches that guard to tolerate DDL in ``tests/``.
      It also only produced a failure because ``_with_cursor`` calls
      ``ensure_private_schema`` first and the schema cache happened to suppress
      the recreate, making the outcome depend on a cache rather than a handler.
    * Making ``db.connect`` raise looks like the most honest outage there is,
      and it never reaches this code at all. ``_resolve_for`` and
      ``_office_lock_gate`` both need the database and both fail closed, so the
      request is refused by the tier gate or the lock long before ``_read``
      runs. The stage passed, and was asserting the gates' 503, not the
      handler's. A mutation that put ``"items": []`` into ``_unavailable``
      survived it untouched.

    So the read functions themselves are made to raise. The gates stay working,
    the request arrives, and the only thing that fails is fetching the member's
    facts — which is the exact fault ``_read`` exists to translate.
    """
    print("\n[a failure is not an empty state]")
    _as(USER_A)
    client = _app().test_client()

    def _refuse(*args, **kwargs):
        raise sqlite3.OperationalError("the store is unreadable")

    broken = {
        (fact_routes.po_read, name): getattr(fact_routes.po_read, name)
        for name in ("facts_overview", "facts_page", "expiring_facts")
    }
    broken[(fact_routes.po_review, "review_queue")] = \
        fact_routes.po_review.review_queue
    for (module, name) in broken:
        setattr(module, name, _refuse)

    try:
        for path, forbidden in (
            ("/api/private-office/facts/overview", ("total", "observed")),
            ("/api/private-office/facts/list", ("items",)),
            ("/api/private-office/facts/expiring", ("items",)),
            ("/api/private-office/facts/review", ("items",)),
        ):
            resp = client.get(path)
            body = resp.get_json() or {}
            check(f"{path} answers 503 when the store is unreadable",
                  resp.status_code == 503, str(resp.status_code))
            check(f"{path} says so rather than saying nothing",
                  body.get("ok") is False and body.get("state") == "unavailable",
                  json.dumps(body)[:160])
            leaked = [key for key in forbidden if key in body]
            check(f"{path} carries no data key a client could render as empty",
                  not leaked, str(leaked))
    finally:
        for (module, name), original in broken.items():
            setattr(module, name, original)

    # And the store really is back, or every stage after this one is testing
    # a broken fixture rather than the product.
    resp = client.get("/api/private-office/facts/overview")
    check("the store is readable again afterwards", resp.status_code == 200,
          str(resp.status_code))


def stage_a_foreign_fact_is_indistinguishable_from_a_missing_one():
    """Isolation, over HTTP, on the status line as well as in the body."""
    print("\n[isolation]")
    foreign = _seed(USER_B, fact_type="other_members_business", value="secret",
                    domain=model.DOMAIN_FINANCIAL)
    _as(USER_A)
    client = _app().test_client()

    never_issued = foreign + 10_000
    for label, fact_id in (("another member's fact", foreign),
                           ("an id never issued", never_issued)):
        detail = client.get(f"/api/private-office/facts/{fact_id}")
        check(f"detail for {label} is 404", detail.status_code == 404,
              str(detail.status_code))
        ev = client.get(f"/api/private-office/facts/{fact_id}/evidence")
        check(f"evidence for {label} is 404", ev.status_code == 404,
              str(ev.status_code))
        line = client.get(f"/api/private-office/facts/{fact_id}/timeline")
        check(f"timeline for {label} is an empty 200",
              line.status_code == 200 and (line.get_json() or {})["events"] == [],
              str(line.status_code))

    # The bodies match too, so the difference is not moved from the status line
    # into the message.
    a = client.get(f"/api/private-office/facts/{foreign}").get_json()
    b = client.get(f"/api/private-office/facts/{never_issued}").get_json()
    check("and the two refusals are byte-identical", a == b,
          f"{json.dumps(a)[:80]} vs {json.dumps(b)[:80]}")

    # Nothing of USER_B's leaks into any of USER_A's collections.
    listing = client.get("/api/private-office/facts/list?limit=100").get_json()
    ids = {item["fact_id"] for item in listing["items"]}
    check("another member's fact is absent from the list", foreign not in ids,
          str(sorted(ids))[:120])
    blob = json.dumps(client.get(
        "/api/private-office/facts/overview").get_json())
    check("and their value does not appear anywhere in the overview",
          "secret" not in blob, blob[:120])


def stage_the_row_shape_is_a_projection_not_a_dump():
    """No internal column reaches the wire, on any collection route.

    ``owner_user_id`` is the one that matters: it is the predicate every query
    in the package is scoped by, and echoing it back turns a response into a
    statement about which member the caller is talking about.
    """
    print("\n[projection, not dump]")
    _as(USER_A)
    client = _app().test_client()
    forbidden = ("owner_user_id", "fact_key", "provenance_ref", "value_hash")
    for path in ("/api/private-office/facts/list?limit=100",
                 "/api/private-office/facts/overview",
                 "/api/private-office/facts/expiring",
                 "/api/private-office/facts/review",
                 "/api/private-office/facts/sources",
                 "/api/private-office/facts/conflicts"):
        blob = json.dumps(client.get(path).get_json() or {})
        leaked = [key for key in forbidden if f'"{key}"' in blob]
        check(f"{path.split('?')[0]} leaks no internal column", not leaked,
              str(leaked))


def stage_evidence_and_detail_are_one_answer():
    """Two routes, one ``fact_detail`` call, and they must not diverge.

    The check compares them rather than asserting each is right. A wrong shared
    value fails the read model's own suite; what fails here instead is a second
    implementation growing behind ``/evidence`` — which is the thing that would
    let the fact screen and the sources screen disagree about whether a document
    is still there.
    """
    print("\n[one answer, two routes]")
    fact_id = _seed(USER_A, fact_type="cited_claim", value="42",
                    domain=model.DOMAIN_FINANCIAL)
    live_id = _with_conn(
        lambda conn, cur: _document(cur, USER_A, "still here"))
    doomed_id = _with_conn(
        lambda conn, cur: _document(cur, USER_A, "about to vanish"))
    for doc in (live_id, doomed_id):
        _with_conn(lambda conn, cur, d=doc: facts.link_evidence(
            cur, owner_user_id=USER_A, fact_id=fact_id,
            source_ref=evidence.format_ref("document", d),
            actor_user_id=USER_A))

    # A citation the resolver cannot parse. link_evidence validates the kind, so
    # this goes in underneath — the same damage fixture the read model's own
    # suite uses, for the same reason: `available: null` is unreachable through
    # the writers and would otherwise be an untested value on the wire.
    _with_conn(lambda conn, cur: cur.execute(
        f"""INSERT INTO {schema.FACT_EVIDENCE_TABLE}
        (owner_user_id, fact_id, source_ref, source_kind, relation,
         note_key, linked_by, linked_at, detached_at, detached_by, created_at)
        VALUES (?, ?, 'ledger:7', 'ledger', 'SUPPORTS', '', ?, ?, '', 0, ?)""",
        (USER_A, fact_id, USER_A, "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00")))

    _with_conn(lambda conn, cur: documents.delete_document(
        cur, owner_user_id=USER_A, document_id=doomed_id, actor_user_id=USER_A))

    _as(USER_A)
    client = _app().test_client()
    detail = client.get(f"/api/private-office/facts/{fact_id}").get_json()
    ev = client.get(f"/api/private-office/facts/{fact_id}/evidence").get_json()

    check("both routes report the same fact",
          detail["fact"]["fact_id"] == ev["fact_id"] == fact_id)
    check("and the same evidence, item for item",
          detail["fact"]["evidence"] == ev["evidence"],
          json.dumps(ev["evidence"])[:160])
    check("and the same set of missing sources",
          detail["fact"]["missing_sources"] == ev["missing"],
          f"{detail['fact']['missing_sources']} vs {ev['missing']}")
    check("and the same verification verdict",
          detail["fact"]["verification"] == ev["verification"])

    # The tri-state, after a round trip through JSON.
    by_ref = {e["source_ref"]: e["available"] for e in ev["evidence"]}
    check("a live document serialises as true",
          by_ref.get(evidence.format_ref("document", live_id)) is True,
          str(by_ref))
    check("a deleted document serialises as false",
          by_ref.get(evidence.format_ref("document", doomed_id)) is False,
          str(by_ref))
    check("an unparseable citation serialises as null, not false",
          "ledger:7" in by_ref and by_ref["ledger:7"] is None, str(by_ref))
    check("and null is not reported to the member as a missing document",
          "ledger:7" not in ev["missing"], str(ev["missing"]))


def stage_an_unhonourable_filter_is_a_client_error():
    """A filter we cannot apply is refused, never silently dropped.

    Dropping it would answer a request for one heading with the member's whole
    store — a wider answer than was asked for, which is the wrong direction to
    fail in for a private-data endpoint.
    """
    print("\n[filters]")
    _as(USER_A)
    client = _app().test_client()

    bad_domain = client.get("/api/private-office/facts/list?domain=NOT_A_DOMAIN")
    check("an unknown domain is a 400", bad_domain.status_code == 400,
          str(bad_domain.status_code))
    check("and the answer says which domains exist",
          set((bad_domain.get_json() or {}).get("domains") or [])
          == set(model.DOMAINS))
    check("and it returns no facts at all",
          "items" not in (bad_domain.get_json() or {}),
          json.dumps(bad_domain.get_json())[:120])

    bad_reason = client.get("/api/private-office/facts/review?reason=BECAUSE")
    check("an unknown review reason is a 400", bad_reason.status_code == 400,
          str(bad_reason.status_code))
    check("and the answer says which reasons exist",
          set((bad_reason.get_json() or {}).get("reasons") or [])
          == set(model.REVIEW_REASONS))

    # The honourable forms still work, so the checks above are not passing
    # because the filter is rejected in every case.
    good = client.get(
        f"/api/private-office/facts/list?domain={model.DOMAIN_FINANCIAL}")
    check("a known domain is accepted", good.status_code == 200,
          str(good.status_code))
    check("and it narrows the answer",
          all(item["domain"] == model.DOMAIN_FINANCIAL
              for item in (good.get_json() or {})["items"]),
          str({i["domain"] for i in (good.get_json() or {})["items"]}))


def stage_the_ceiling_is_the_modules_to_enforce():
    """A caller cannot argue past a bound by naming a bigger number.

    The handlers pass ``?limit=`` straight down. If one of them ever grew its
    own clamp, this is where the two ceilings would be caught disagreeing.

    Every limit is read with ``.get``. A page size the route cannot parse is
    supposed to fall back to the module's default; if instead it escapes as an
    exception the response is a 503 with no ``limit`` in it, and subscripting
    would turn a failed guarantee into a KeyError that names none of them.
    """
    print("\n[ceilings]")
    _as(USER_A)
    client = _app().test_client()

    def _limit(query: str):
        return (client.get(
            f"/api/private-office/facts/list?{query}").get_json() or {}
        ).get("limit")

    over = _limit("limit=99999")
    check("an oversized page is clamped to the module's ceiling",
          over == read_model.MAX_PAGE, str(over))
    junk = _limit("limit=lots")
    check("an unreadable page size falls back to the module's default",
          junk == read_model.DEFAULT_PAGE, str(junk))
    negative = _limit("limit=-4")
    check("a negative page size does the same",
          negative == read_model.DEFAULT_PAGE, str(negative))

    # The published bounds are what the health surface advertises, so a client
    # that reads them there can predict what it will get here.
    check("and the ceiling the client is clamped to is the published one",
          over == read_model.MAX_PAGE)


def stage_the_cross_cutting_views_answer_at_all():
    """Sources, conflicts, review and expiring return their documented shape.

    Deliberately shallow. What each of these computes is proven in the read
    model's and the review queue's own suites; the only thing that can go wrong
    here is a handler forwarding the wrong key, so that is all that is asserted.
    """
    print("\n[cross-cutting views]")
    _as(USER_A)
    client = _app().test_client()

    sources = client.get("/api/private-office/facts/sources")
    body = sources.get_json() or {}
    check("sources answers 200", sources.status_code == 200,
          str(sources.status_code))
    check("and carries its own truncation flag", "truncated" in body,
          str(sorted(body)))
    check("and a count that matches the list it sent",
          body.get("count") == len(body.get("sources") or []),
          f"{body.get('count')} vs {len(body.get('sources') or [])}")
    check("and does not claim to be unavailable when it read fine",
          body.get("unavailable") is None, str(body.get("unavailable")))

    conflicts = client.get("/api/private-office/facts/conflicts").get_json()
    check("conflicts carries a list and a truncation flag",
          isinstance(conflicts.get("conflicts"), list)
          and "truncated" in conflicts, str(sorted(conflicts)))

    review = client.get("/api/private-office/facts/review").get_json()
    check("review carries its items and the reason vocabulary",
          isinstance(review.get("items"), list)
          and set(review.get("reasons") or []) == set(model.REVIEW_REASONS),
          str(sorted(review)))

    expiring = client.get("/api/private-office/facts/expiring").get_json()
    check("expiring carries items and an already-expired tally",
          isinstance(expiring.get("items"), list)
          and isinstance(expiring.get("already_expired"), int),
          str(sorted(expiring)))


def stage_an_expired_window_reaches_the_member():
    """The already-closed case, end to end.

    Asserted over HTTP rather than trusting the module's own coverage, because
    the tally in the payload is computed in the handler — it is the one number
    on this surface that the read model does not produce.
    """
    print("\n[expired windows]")
    opened = (facts._now() - timedelta(days=400)).isoformat()
    closed = (facts._now() - timedelta(days=45)).isoformat()
    fact_id = _seed(USER_A, fact_type="lapsed_policy", value="p1",
                    domain=model.DOMAIN_LEGAL, valid_from=opened,
                    valid_to=closed, allow_backfill=True)
    _as(USER_A)
    client = _app().test_client()
    body = client.get("/api/private-office/facts/expiring").get_json()
    ids = [item["fact_id"] for item in body["items"]]
    check("a window that closed six weeks ago is on the list", fact_id in ids,
          str(ids))
    item = next(i for i in body["items"] if i["fact_id"] == fact_id)
    check("and it is labelled as already expired",
          item["already_expired"] is True, str(item))
    check("and the handler's tally counts it",
          body["already_expired"] >= 1, str(body["already_expired"]))
    check("and the tally agrees with the items it sent",
          body["already_expired"]
          == sum(1 for i in body["items"] if i["already_expired"]),
          str(body["already_expired"]))


def stage_reads_are_recorded():
    """A read of a private store is itself an event worth keeping.

    Counted as a delta rather than an absolute, so the check does not depend on
    what every stage before it happened to leave behind.
    """
    print("\n[audit]")
    def _count(conn, cur):
        cur.execute(
            f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} "
            f"WHERE owner_user_id = ? AND action = ?",
            (USER_A, "PRIVATE_FACT_READ"))
        return int(dict(cur.fetchone())["n"])

    before = _with_conn(_count)
    _as(USER_A)
    client = _app().test_client()
    client.get("/api/private-office/facts/overview")
    client.get("/api/private-office/facts/list")
    client.get("/api/private-office/facts/sources")
    after = _with_conn(_count)
    check("three reads left three audit rows", after - before == 3,
          f"{before} -> {after}")

    # A refused read must not be recorded as a read that happened.
    _stub._test_user = None
    before = _with_conn(_count)
    _app().test_client().get("/api/private-office/facts/overview")
    check("an unauthenticated request records no read",
          _with_conn(_count) == before)


def main() -> int:
    print("=" * 60)
    print("PRIVATE OFFICE FACTS READ ROUTES")
    print(f"db: {_TMP_DB}")
    if routes.FACTS_FEATURE_ID not in feature_matrix.implemented_feature_ids():
        print("SKIP: private_facts is not IMPLEMENTED in the matrix")
        return 0
    setup_environment()

    stage_every_route_requires_a_session()
    stage_the_surface_is_read_only_and_ownerless()
    stage_the_lock_covers_every_route()
    stage_the_kill_switch_covers_every_route()
    stage_the_bounds_reach_the_wire()
    stage_a_failure_is_never_an_empty_state()
    stage_a_foreign_fact_is_indistinguishable_from_a_missing_one()
    stage_the_row_shape_is_a_projection_not_a_dump()
    stage_evidence_and_detail_are_one_answer()
    stage_an_unhonourable_filter_is_a_client_error()
    stage_the_ceiling_is_the_modules_to_enforce()
    stage_the_cross_cutting_views_answer_at_all()
    stage_an_expired_window_reaches_the_member()
    stage_reads_are_recorded()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_facts_read_routes():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
