"""Human Concierge — a real desk, never a fabricated human.

Run either way::

    python -m pytest tests/private_office/test_private_concierge.py
    python tests/private_office/test_private_concierge.py

What these tests defend
-----------------------
* **The gate order holds over HTTP.** No session is 401, no PRIVATE_OFFICE
  tier is 403, the kill switch is 404, a locked Office is 423 — before any
  request is read.
* **Staffing is never faked.** With an empty roster every payload says the
  desk is UNSTAFFED and that no human has seen the request. A submitted
  request is stored, not answered: the thread contains zero OPERATOR lines
  until a real roster account writes one.
* **Operator lines are real accounts.** Every OPERATOR message was posted by
  an authenticated roster member; the console is invisible (404) to everyone
  else, including fully entitled members.
* **Completion is held to its name.** COMPLETED requires an operator note
  saying what was done, and its evidence refs ride into the thread.
* **The lifecycle splits correctly.** CANCELED belongs to the member alone;
  operators move the working statuses; closed requests refuse everything.
* **Owner isolation everywhere.** Another member's requests are invisible
  and untouchable. The one deliberate crossing — the operator console — is
  audited with the operator as actor and the member as owner.
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_concierge_"), "test.db")
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
from services import private_office_concierge_routes as cg_routes  # noqa: E402
from services.private_office import audit as audit_mod  # noqa: E402
from services.private_office import concierge as concierge_mod  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import records as records_mod  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

USER_A = 9941
USER_B = 9942
USER_C = 9943  # a real session with no Private Office tier — the 403 case
OPERATOR = 9944  # the human on the roster

ROSTER_ENV = concierge_mod.ROSTER_ENV

# Seeded content that must never surface in audit rows.
_SECRETS = ("Book the Kyoto ryokan", "quiet anniversary", "flight AF-276")

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
    cg_routes.register(app)
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


def _set_roster(value):
    if value is None:
        os.environ.pop(ROSTER_ENV, None)
    else:
        os.environ[ROSTER_ENV] = value


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
        for uid in (USER_A, USER_B, USER_C, OPERATOR):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        records_mod.ensure_records_schema(cur, force=True)
        concierge_mod.ensure_concierge_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (USER_A, USER_B):
        _unlock(uid)
    _stub._test_user = None
    _set_roster(None)  # start unstaffed — the truthful baseline


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
    print("\n[concierge: gates]")
    client = _app().test_client()

    _stub._test_user = None
    resp = client.get("/api/private-office/concierge")
    check("no session is 401", resp.status_code == 401, str(resp.status_code))

    _as(USER_C)
    resp = client.get("/api/private-office/concierge")
    body = resp.get_json() or {}
    check("no PRIVATE_OFFICE tier is 403 with a minimum tier",
          resp.status_code == 403
          and body.get("minimum_tier") == tiers.TIER_PRIVATE_OFFICE,
          f"{resp.status_code} {body}")

    previous = os.environ.get("PRIVATE_CONCIERGE_ENABLED")
    os.environ["PRIVATE_CONCIERGE_ENABLED"] = "false"
    try:
        _as(USER_A)
        resp = client.get("/api/private-office/concierge")
        check("kill switch off is 404 for an entitled member",
              resp.status_code == 404, str(resp.status_code))
        state = matrix.availability(
            cg_routes.CONCIERGE_FEATURE_ID, tiers.TIER_PRIVATE_OFFICE)
        check("flag off reads as FEATURE_DISABLED, implementation still IMPLEMENTED",
              state["availability"] == matrix.AVAIL_FEATURE_DISABLED
              and state["implementation"] == matrix.IMPL_IMPLEMENTED, str(state))
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_CONCIERGE_ENABLED", None)
        else:
            os.environ["PRIVATE_CONCIERGE_ENABLED"] = previous

    _as(USER_A)
    resp = client.get("/api/private-office/concierge",
                      headers={routes.GRANT_HEADER: ""})
    check("no unlock grant is 423 Locked", resp.status_code == 423, str(resp.status_code))

    resp = client.get("/api/private-office/concierge")
    body = resp.get_json() or {}
    check("unlocked member reaches the desk",
          resp.status_code == 200 and body.get("ok"), str(resp.status_code))
    check("no-store on the desk",
          "no-store" in (resp.headers.get("Cache-Control") or ""),
          str(resp.headers.get("Cache-Control")))

    resp = client.get("/api/private-office/concierge/desk")
    check("the operator console 404s for a fully entitled member not on the roster",
          resp.status_code == 404, str(resp.status_code))

    _stub._test_user = None
    resp = client.get("/api/private-office/concierge/desk")
    check("the operator console still requires a session",
          resp.status_code == 401, str(resp.status_code))


# ---------------------------------------------------------------------------
# Unstaffed truth
# ---------------------------------------------------------------------------

def stage_unstaffed_truth():
    print("\n[concierge: an empty roster is said out loud]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.get("/api/private-office/concierge")
    body = resp.get_json() or {}
    desk = body.get("desk") or {}
    check("the desk reads UNSTAFFED with zero operators",
          desk.get("staffed") is False and desk.get("operator_count") == 0,
          str(desk))
    check("the unstaffed note says no human has seen anything",
          "no human" in str(desk.get("note", "")).lower()
          or "no operator" in str(desk.get("note", "")).lower(),
          str(desk.get("note")))
    check("provider status declares zero automation",
          (body.get("provider_status") or {}).get("automation") == "none"
          and (body.get("provider_status") or {}).get("inference") == "none",
          str(body.get("provider_status")))

    resp = client.post(
        "/api/private-office/concierge/requests",
        json={"title": _SECRETS[0], "category": "TRAVEL",
              "description": f"A {_SECRETS[1]} trip, {_SECRETS[2]}.",
              "priority": "HIGH"})
    body = resp.get_json() or {}
    check("submitting while unstaffed is 201 — stored, not refused",
          resp.status_code == 201 and body.get("ok"),
          f"{resp.status_code} {body.get('message')}")
    check("the submission response repeats the staffing truth",
          (body.get("desk") or {}).get("staffed") is False, str(body.get("desk")))
    _STATE["req_a"] = int(body.get("request_id") or 0)
    _STATE["ref_a"] = body.get("ref")
    check("the new request has a parseable evidence ref",
          evidence.parse_ref(_STATE["ref_a"] or "") is not None,
          str(_STATE["ref_a"]))

    resp = client.get(f"/api/private-office/concierge/requests/{_STATE['req_a']}")
    body = resp.get_json() or {}
    thread = body.get("thread") or []
    check("an unstaffed desk has answered nothing: zero OPERATOR lines",
          all(m.get("author") != "OPERATOR" for m in thread), str(thread))
    check("the stored request carries its own fields",
          (body.get("request") or {}).get("category") == "TRAVEL"
          and (body.get("request") or {}).get("status") == "OPEN",
          str(body.get("request")))


# ---------------------------------------------------------------------------
# Member submission and thread
# ---------------------------------------------------------------------------

def stage_member_thread():
    print("\n[concierge: the member's side of the thread]")
    client = _app().test_client()
    _as(USER_A)

    resp = client.post(
        f"/api/private-office/concierge/requests/{_STATE['req_a']}/messages",
        json={"body": "Two rooms if possible."})
    body = resp.get_json() or {}
    check("a member message lands as MEMBER",
          resp.status_code == 201
          and (body.get("message_sent") or {}).get("author") == "MEMBER",
          f"{resp.status_code} {body}")

    resp = client.post(
        f"/api/private-office/concierge/requests/{_STATE['req_a']}/messages",
        json={"body": "   "})
    check("an empty message is 400", resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        "/api/private-office/concierge/requests/999999/messages",
        json={"body": "hello?"})
    check("a message to an absent request is 404",
          resp.status_code == 404, str(resp.status_code))

    resp = client.post("/api/private-office/concierge/requests",
                       json={"title": "", "category": "TRAVEL"})
    check("a request without a title is 400", resp.status_code == 400,
          str(resp.status_code))

    resp = client.post("/api/private-office/concierge/requests",
                       json={"title": "x", "category": "TIME_TRAVEL"})
    body = resp.get_json() or {}
    check("an unknown category is 400 naming the allowed set",
          resp.status_code == 400 and "GENERAL" in str(body.get("message")),
          f"{resp.status_code} {body.get('message')}")

    resp = client.post(
        "/api/private-office/concierge/requests",
        json={"title": "Second errand", "category": "ADMIN"})
    _STATE["req_a2"] = int((resp.get_json() or {}).get("request_id") or 0)
    check("a second request files cleanly",
          resp.status_code == 201 and _STATE["req_a2"] > 0, str(resp.status_code))

    resp = client.get("/api/private-office/concierge")
    body = resp.get_json() or {}
    listed = [int(r.get("id") or 0) for r in body.get("requests") or []]
    check("the member's list shows both requests, newest first",
          listed[:2] == [_STATE["req_a2"], _STATE["req_a"]], str(listed))


# ---------------------------------------------------------------------------
# The operator desk
# ---------------------------------------------------------------------------

def stage_operator_desk():
    print("\n[concierge: a real operator on the roster]")
    client = _app().test_client()

    # B files one too, so the queue crosses owners.
    _as(USER_B)
    resp = client.post("/api/private-office/concierge/requests",
                       json={"title": "Renew the parking permit", "category": "ADMIN"})
    _STATE["req_b"] = int((resp.get_json() or {}).get("request_id") or 0)
    check("member B files a request", resp.status_code == 201, str(resp.status_code))

    previous = os.environ.get(ROSTER_ENV)
    _set_roster(str(OPERATOR))
    _STATE["roster_previous"] = previous

    _as(USER_A)
    resp = client.get("/api/private-office/concierge")
    desk = (resp.get_json() or {}).get("desk") or {}
    check("with a roster the desk reads STAFFED",
          desk.get("staffed") is True and desk.get("operator_count") == 1,
          str(desk))

    _as(OPERATOR)
    resp = client.get("/api/private-office/concierge/desk")
    body = resp.get_json() or {}
    queue = [r for r in body.get("queue") or []
             if int(r.get("owner_user_id") or 0) in (USER_A, USER_B)]
    check("the roster account reaches the queue",
          resp.status_code == 200 and body.get("ok"), str(resp.status_code))
    check("the queue crosses owners: A's and B's open requests are all there",
          {int(r["id"]) for r in queue}
          == {_STATE["req_a"], _STATE["req_a2"], _STATE["req_b"]},
          str([r.get("id") for r in queue]))
    ordered = [int(r["id"]) for r in queue]
    check("the queue is oldest first — a work queue, not a feed",
          ordered == sorted(ordered), str(ordered))

    resp = client.get(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}")
    body = resp.get_json() or {}
    check("the console shows one member's request with its thread",
          resp.status_code == 200
          and (body.get("request") or {}).get("id") == _STATE["req_a"],
          str(resp.status_code))
    member_lines = [m for m in body.get("thread") or [] if m.get("author") == "MEMBER"]
    check("the console attributes member lines to their account id",
          member_lines and all(m.get("author_user_id") == USER_A for m in member_lines),
          str(member_lines))

    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={"status": "IN_PROGRESS",
              "note": "On it — checking availability for those dates."})
    body = resp.get_json() or {}
    check("the operator claims the request: IN_PROGRESS",
          resp.status_code == 200
          and (body.get("request") or {}).get("status") == "IN_PROGRESS",
          f"{resp.status_code} {body}")
    check("the claim assigns the operator by id, as a ref",
          (body.get("request") or {}).get("assigned_provider_id")
          == f"operator:{OPERATOR}",
          str((body.get("request") or {}).get("assigned_provider_id")))
    check("the operator's note is an OPERATOR line",
          (body.get("message") or {}).get("author") == "OPERATOR",
          str(body.get("message")))

    _as(USER_A)
    resp = client.get(f"/api/private-office/concierge/requests/{_STATE['req_a']}")
    thread = (resp.get_json() or {}).get("thread") or []
    operator_lines = [m for m in thread if m.get("author") == "OPERATOR"]
    check("the member sees the human's reply",
          len(operator_lines) == 1 and "availability" in operator_lines[0]["body"],
          str(operator_lines))
    check("the member view never exposes the staff account id",
          all("author_user_id" not in m for m in thread), str(thread))


# ---------------------------------------------------------------------------
# Lifecycle — completion held to its name, cancellation held to its owner
# ---------------------------------------------------------------------------

def stage_lifecycle():
    print("\n[concierge: lifecycle]")
    client = _app().test_client()

    # Something real to cite as completion evidence: the obligation the
    # operator's work produced, written through the canonical writer.
    outcome = _service(lambda cur: records_mod.create_record(
        cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=USER_A,
        actor_user_id=USER_A, title="Settle the ryokan balance on arrival",
        obligation_type="PAYMENT"))
    _STATE["obligation_ref"] = evidence.format_ref(
        "obligation", int(outcome["record_id"]))

    _as(OPERATOR)
    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={"status": "COMPLETED"})
    check("completing without a note is refused — completion must say what was done",
          resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={"status": "CANCELED", "note": "closing this"})
    check("an operator cannot cancel — CANCELED belongs to the member",
          resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={"status": "MADE_UP"})
    check("an unknown status is 400", resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={})
    check("an empty update is 400", resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={"status": "COMPLETED",
              "note": "Booked: two rooms, three nights. Balance due on "
                      "arrival — filed as an obligation.",
              "evidence_refs": [_STATE["obligation_ref"]]})
    body = resp.get_json() or {}
    check("completion with a note and evidence lands",
          resp.status_code == 200
          and (body.get("request") or {}).get("status") == "COMPLETED",
          f"{resp.status_code} {body}")
    check("the completion note carries its evidence refs",
          (body.get("message") or {}).get("evidence") == [_STATE["obligation_ref"]],
          str(body.get("message")))
    check("completion stamps completed_at — the REQUEST primitive's name for closure",
          bool((body.get("request") or {}).get("completed_at")),
          str((body.get("request") or {}).get("completed_at")))

    resp = client.post(
        f"/api/private-office/concierge/desk/{USER_A}/{_STATE['req_a']}",
        json={"note": "one more thing"})
    check("a closed request refuses further operator action",
          resp.status_code == 400, str(resp.status_code))

    _as(USER_A)
    resp = client.post(
        f"/api/private-office/concierge/requests/{_STATE['req_a']}/messages",
        json={"body": "thanks!"})
    check("a closed request refuses further member messages",
          resp.status_code == 400, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/requests/{_STATE['req_a2']}/cancel")
    body = resp.get_json() or {}
    check("the member cancels their own open request",
          resp.status_code == 200
          and (body.get("request") or {}).get("status") == "CANCELED",
          f"{resp.status_code} {body}")

    _as(OPERATOR)
    resp = client.get("/api/private-office/concierge/desk")
    queue = [r for r in (resp.get_json() or {}).get("queue") or []
             if int(r.get("owner_user_id") or 0) in (USER_A, USER_B)]
    check("closed and canceled requests leave the queue",
          {int(r["id"]) for r in queue} == {_STATE["req_b"]},
          str([r.get("id") for r in queue]))


# ---------------------------------------------------------------------------
# The roster empties again — history stays, nothing new is fabricated
# ---------------------------------------------------------------------------

def stage_roster_empties():
    print("\n[concierge: the roster empties]")
    client = _app().test_client()
    _set_roster(None)

    _as(USER_A)
    resp = client.get(f"/api/private-office/concierge/requests/{_STATE['req_a']}")
    body = resp.get_json() or {}
    operator_lines = [m for m in body.get("thread") or []
                      if m.get("author") == "OPERATOR"]
    check("history written by a real human survives the roster change",
          len(operator_lines) == 2, str(len(operator_lines)))
    check("but the desk now reads UNSTAFFED again",
          (body.get("desk") or {}).get("staffed") is False, str(body.get("desk")))

    _as(OPERATOR)
    resp = client.get("/api/private-office/concierge/desk")
    check("an account taken off the roster loses the console",
          resp.status_code == 404, str(resp.status_code))

    roster = _STATE.pop("roster_previous", None)
    _set_roster(str(OPERATOR))  # back on duty for the remaining stages
    _STATE["roster_restore"] = roster


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def stage_isolation():
    print("\n[concierge: isolation]")
    client = _app().test_client()
    _as(USER_B)

    resp = client.get("/api/private-office/concierge")
    listed = [int(r.get("id") or 0) for r in (resp.get_json() or {}).get("requests") or []]
    check("B's list is B's alone", listed == [_STATE["req_b"]], str(listed))

    resp = client.get(f"/api/private-office/concierge/requests/{_STATE['req_a']}")
    check("A's request is 404 to B", resp.status_code == 404, str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/requests/{_STATE['req_a']}/messages",
        json={"body": "let me in"})
    check("B cannot speak in A's thread", resp.status_code == 404,
          str(resp.status_code))

    resp = client.post(
        f"/api/private-office/concierge/requests/{_STATE['req_a2']}/cancel")
    check("B cannot cancel A's request", resp.status_code == 404,
          str(resp.status_code))

    got = _service(lambda cur: concierge_mod.get_request(
        cur, owner_user_id=USER_B, request_id=_STATE["req_a"]))
    check("the service layer answers None across owners, same as absent",
          got is None, str(got))

    _as(OPERATOR)
    resp = client.get(
        f"/api/private-office/concierge/desk/{USER_B}/{_STATE['req_b']}")
    check("the operator console crosses owners by design — and only there",
          resp.status_code == 200, str(resp.status_code))


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------

def stage_bookkeeping():
    print("\n[concierge: bookkeeping]")

    rows = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE action = ? AND owner_user_id = ?",
        (audit_mod.ACTION_CONCIERGE_READ, USER_A))
    check("member reads are audited", any(
        r["actor_user_id"] == USER_A and r["object_type"] in ("REQUEST_LIST", "REQUEST")
        for r in rows), str(len(rows)))
    crossings = [r for r in rows if r["actor_user_id"] == OPERATOR]
    check("every operator crossing is audited: operator as actor, member as owner",
          crossings and all(r["object_type"] in ("DESK_QUEUE", "REQUEST")
                            for r in crossings), str(len(crossings)))

    rows = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE action = ? AND owner_user_id = ?",
        (audit_mod.ACTION_CONCIERGE_MESSAGE, USER_A))
    actors = {int(r["actor_user_id"]) for r in rows}
    check("messages are audited for both kinds of author",
          USER_A in actors and OPERATOR in actors, str(actors))

    rows = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE owner_user_id IN (?, ?)",
        (USER_A, USER_B))
    leaked = [
        f"{r['action']}:{secret}" for r in rows for secret in _SECRETS
        if secret.lower() in " ".join(str(v) for v in r.values()).lower()
    ]
    check("no request content leaks into audit rows", not leaked, str(leaked))

    created = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE action = ? AND owner_user_id = ? "
        "AND object_type = 'REQUEST'",
        (audit_mod.ACTION_RECORD_CREATE, USER_A))
    check("the REQUEST rows themselves audited through the canonical writer",
          len(created) >= 2, str(len(created)))


# ---------------------------------------------------------------------------
# Feature matrix
# ---------------------------------------------------------------------------

def stage_feature_matrix():
    print("\n[concierge: the matrix tells the truth]")

    got = matrix.availability(
        cg_routes.CONCIERGE_FEATURE_ID, tiers.TIER_PRIVATE_OFFICE)
    check("human_concierge is IMPLEMENTED and ENTITLED at the top tier",
          got["implementation"] == matrix.IMPL_IMPLEMENTED
          and got["availability"] == matrix.AVAIL_ENTITLED, str(got))

    below = matrix.availability(cg_routes.CONCIERGE_FEATURE_ID, tiers.TIER_PRIVATE)
    check("below the top tier it is NOT_ENTITLED with the honest price",
          below["availability"] == matrix.AVAIL_NOT_ENTITLED
          and below["minimum_tier"] == tiers.TIER_PRIVATE_OFFICE, str(below))

    spec = matrix.get(cg_routes.CONCIERGE_FEATURE_ID)
    check("the kill switch is declared",
          spec.flag_env == "PRIVATE_CONCIERGE_ENABLED", str(spec.flag_env))

    # Staffing must not leak into the matrix: an unstaffed desk is an
    # operational state the payload reports, not a different implementation.
    _set_roster(None)
    try:
        again = matrix.availability(
            cg_routes.CONCIERGE_FEATURE_ID, tiers.TIER_PRIVATE_OFFICE)
        check("an empty roster changes desk_status, never the matrix",
              again["availability"] == matrix.AVAIL_ENTITLED
              and concierge_mod.desk_status()["staffed"] is False, str(again))
    finally:
        roster = _STATE.pop("roster_restore", None)
        _set_roster(roster)


STAGES = (
    stage_gates,
    stage_unstaffed_truth,
    stage_member_thread,
    stage_operator_desk,
    stage_lifecycle,
    stage_roster_empties,
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
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("All private concierge checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
