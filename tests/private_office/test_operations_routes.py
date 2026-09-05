"""The Operations HTTP surface — six record views, status moves, attention.

Same stub-the-monolith pattern as ``test_private_office_routes.py``::

    python -m pytest tests/private_office/test_operations_routes.py
    python tests/private_office/test_operations_routes.py

What these tests are actually defending
---------------------------------------
* Both locks in front of every operations route: the tier gate and the office
  passcode grant. A signed-in, entitled member presenting no grant gets 423
  and no data.
* The body allowlist. ``owner_user_id``, ``source_type``, ``provenance_type``
  and ``relevance_score`` in a POST body change nothing: the owner comes from
  the session, the source is pinned to USER, and the member's own enthusiasm
  is not a relevance score.
* Owner isolation over HTTP, with two real members. A foreign record id on the
  status route is byte-identical to an id that was never issued.
* The projection survives serialisation: no ``owner_user_id``, no
  ``record_key`` anywhere in any response.
* Every list read leaves an audit row — reads of a private store are not
  quieter than writes to it.
"""

import json
import os
import sys
import tempfile
import types
import datetime as _dt

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_ops_"), "test.db")
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
from services.private_office import audit as po_audit  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import records as po_records  # noqa: E402
from services.private_office import retrieval as po_retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9401
USER_B = 9402

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


#: Unlock grants, minted once through the real security endpoints, injected by
#: the client below. The lock itself gets its own direct 423 assertion in
#: ``stage_second_lock`` so "unlocked" stays a thing the suite had to obtain.
_GRANTS: dict[int, str] = {}
PASSCODE = "849271"


class _GrantClient(FlaskClient):
    """Presents the current member's unlock grant. ``setdefault`` on purpose:
    a caller passing the header explicitly — including empty, to exercise a
    locked request — keeps what it passed."""

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


def _audit_count(action: str, object_id: str) -> int:
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} "
            f"WHERE action = ? AND object_id = ?",
            (action, object_id),
        )
        row = cur.fetchone()
        return int(row["n"] if hasattr(row, "keys") else row[0])
    finally:
        conn.close()


#: One valid creation body per view. The required fields come from
#: ``records.SPECS[...]["required"]``; the suite fails loudly if the vocabulary
#: drifts, because every view is created at least once below.
_SEED_BODIES: dict[str, dict] = {
    "obligations": {"title": "Renew the lease", "obligation_type": "RENEWAL",
                    "due_at": "2027-01-15T00:00:00Z"},
    "events": {"event_type": "MEETING", "title": "Met the accountant",
               "occurred_at": "2026-09-01T10:00:00Z"},
    "decisions": {"question": "Refinance the loan?", "summary": "Rates moved."},
    "requests": {"title": "Book the flight", "category": "TRAVEL",
                 "description": "Two seats, morning."},
    "risks": {"title": "Policy lapse", "risk_type": "INSURANCE",
              "summary": "Coverage gap after the move."},
    "opportunities": {"title": "Off-market building", "opportunity_type": "PROPERTY"},
}


# ---------------------------------------------------------------------------
def stage_authentication():
    print("\n[authentication]")
    _stub._test_user = None
    client = _app().test_client()
    for path, method in (
        ("/api/private-office/records/obligations", "get"),
        ("/api/private-office/records/obligations", "post"),
        ("/api/private-office/records/obligations/1/status", "post"),
        ("/api/private-office/attention", "get"),
    ):
        resp = getattr(client, method)(path, json={})
        check(f"{method.upper()} {path} requires login", resp.status_code == 401,
              str(resp.status_code))


def stage_unknown_view():
    print("\n[unknown view]")
    _as(USER_A)
    client = _app().test_client()
    for method, path in (("get", "/api/private-office/records/passwords"),
                         ("post", "/api/private-office/records/passwords"),
                         ("post", "/api/private-office/records/passwords/1/status")):
        resp = getattr(client, method)(path, json={})
        body = resp.get_json() or {}
        check(f"{method.upper()} {path.rsplit('/records/', 1)[-1]} is a 404",
              resp.status_code == 404, str(resp.status_code))
        check("the 404 lists the real views",
              body.get("views") == sorted(po_retrieval.RECORD_VIEWS), str(body))


def stage_create_and_list_every_view():
    print("\n[create + list, all six views]")
    _as(USER_A)
    client = _app().test_client()
    for view, body in _SEED_BODIES.items():
        created = client.post(f"/api/private-office/records/{view}", json=body)
        payload = created.get_json() or {}
        check(f"POST {view} answers 201", created.status_code == 201,
              f"{created.status_code} {payload}")
        check(f"{view}: the write reports created",
              payload.get("status") == po_records.STATUS_CREATED, str(payload.get("status")))
        check(f"{view}: a record id was issued", int(payload.get("record_id") or 0) > 0,
              str(payload.get("record_id")))

        listed = client.get(f"/api/private-office/records/{view}")
        listing = listed.get_json() or {}
        check(f"GET {view} answers 200", listed.status_code == 200,
              f"{listed.status_code} {listing}")
        check(f"{view}: the created record is listed",
              any(int(r.get("id") or 0) == int(payload["record_id"])
                  for r in listing.get("records") or []),
              str(listing.get("count")))
        check(f"{view}: count matches the list",
              listing.get("count") == len(listing.get("records") or []),
              str(listing.get("count")))
        record_type = po_retrieval.RECORD_VIEWS[view]
        check(f"{view}: the response states the real status vocabulary",
              listing.get("statuses") == list(po_records.SPECS[record_type]["statuses"]),
              str(listing.get("statuses")))
        check(f"{view}: responses are never cached",
              "no-store" in listed.headers.get("Cache-Control", ""))


def stage_body_allowlist():
    """A body that names an owner, a source or a score changes nothing."""
    print("\n[body allowlist]")
    _as(USER_A)
    client = _app().test_client()
    resp = client.post(
        "/api/private-office/records/obligations",
        json={
            "title": "Smuggled write", "obligation_type": "TAX",
            "owner_user_id": USER_B,          # ignored: owner from session
            "source_type": "PROVIDER",         # pinned to USER below the route
            "provenance_type": "VERIFIED",     # absent from the allowlist
            "relevance_score": 1.0,            # absent from the allowlist
        },
    )
    payload = resp.get_json() or {}
    check("the smuggling body still writes for the caller",
          resp.status_code == 201, f"{resp.status_code} {payload}")
    record = payload.get("record") or {}
    check("the source is pinned to USER",
          record.get("source_type") == po_records.SOURCE_USER,
          str(record.get("source_type")))

    _as(USER_B)
    b_list = client.get("/api/private-office/records/obligations").get_json() or {}
    blob = json.dumps(b_list, default=str)
    check("the smuggled owner did not reach B's store", "Smuggled write" not in blob)


def stage_status_transitions():
    print("\n[status transitions]")
    _as(USER_A)
    client = _app().test_client()

    listing = client.get("/api/private-office/records/obligations").get_json() or {}
    target = next(r for r in listing["records"] if r["title"] == "Renew the lease")
    before_open = listing.get("open_count")

    moved = client.post(
        f"/api/private-office/records/obligations/{target['id']}/status",
        json={"status": "RESOLVED"})
    moved_body = moved.get_json() or {}
    check("an obligation can be resolved", moved.status_code == 200,
          f"{moved.status_code} {moved_body}")
    check("the moved record reports the new status",
          (moved_body.get("record") or {}).get("status") == "RESOLVED",
          str(moved_body.get("record")))

    after = client.get("/api/private-office/records/obligations").get_json() or {}
    check("resolving lowers the open count",
          after.get("open_count") == before_open - 1,
          f"{before_open} -> {after.get('open_count')}")

    decisions = client.get("/api/private-office/records/decisions").get_json() or {}
    decision = next(r for r in decisions["records"]
                    if r["question"] == "Refinance the loan?")
    decided = client.post(
        f"/api/private-office/records/decisions/{decision['id']}/status",
        json={"status": "DECIDED", "outcome": "Yes — locked the lower rate."})
    decided_record = (decided.get_json() or {}).get("record") or {}
    check("a decision carries its outcome",
          decided_record.get("outcome") == "Yes — locked the lower rate.",
          str(decided_record.get("outcome")))

    missing = client.post(
        f"/api/private-office/records/obligations/{target['id']}/status", json={})
    missing_body = missing.get_json() or {}
    check("a missing status is a 400", missing.status_code == 400,
          str(missing.status_code))
    check("the 400 states the real vocabulary",
          missing_body.get("statuses") ==
          list(po_records.SPECS[po_records.TYPE_OBLIGATION]["statuses"]),
          str(missing_body))

    invalid = client.post(
        f"/api/private-office/records/obligations/{target['id']}/status",
        json={"status": "SHREDDED"})
    check("an unknown status is a 400, not a silent default",
          invalid.status_code == 400, str(invalid.status_code))
    check("the writer's own reason reaches the member",
          bool((invalid.get_json() or {}).get("message")),
          str(invalid.get_json()))


def stage_owner_isolation():
    print("\n[owner isolation]")
    _as(USER_A)
    client = _app().test_client()
    a_listing = client.get("/api/private-office/records/risks").get_json() or {}
    a_risk = next(r for r in a_listing["records"] if r["title"] == "Policy lapse")

    _as(USER_B)
    b_listing = client.get("/api/private-office/records/risks").get_json() or {}
    blob = json.dumps(b_listing, default=str)
    check("B's risk list contains nothing of A's", "Policy lapse" not in blob)

    foreign = client.post(
        f"/api/private-office/records/risks/{a_risk['id']}/status",
        json={"status": "DISMISSED"})
    absent = client.post(
        "/api/private-office/records/risks/99999999/status",
        json={"status": "DISMISSED"})
    check("a foreign record answers exactly like a nonexistent one",
          foreign.status_code == absent.status_code == 404
          and foreign.get_json() == absent.get_json(),
          f"{foreign.status_code}/{absent.status_code}")

    _as(USER_A)
    still = client.get("/api/private-office/records/risks").get_json() or {}
    a_after = next(r for r in still["records"] if r["id"] == a_risk["id"])
    check("A's risk was not moved by B's attempt",
          a_after.get("status") == a_risk.get("status"), str(a_after.get("status")))


def stage_second_lock():
    print("\n[second lock]")
    _as(USER_A)
    client = _app().test_client()
    for method, path, kwargs in (
        ("get", "/api/private-office/records/obligations", {}),
        ("post", "/api/private-office/records/obligations",
         {"json": _SEED_BODIES["obligations"]}),
        ("get", "/api/private-office/attention", {}),
    ):
        resp = getattr(client, method)(
            path, headers={routes.GRANT_HEADER: ""}, **kwargs)
        body = resp.get_json() or {}
        check(f"a locked {method.upper()} {path.rsplit('/', 1)[-1]} is 423",
              resp.status_code == 423, str(resp.status_code))
        check("the locked refusal carries no records",
              "records" not in body and "due_soon" not in body, str(body))


def stage_attention():
    print("\n[attention]")
    _as(USER_A)
    client = _app().test_client()

    soon = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=2)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.post("/api/private-office/records/obligations",
                json={"title": "Pay the retainer", "obligation_type": "PAYMENT",
                      "due_at": soon})
    far = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=60)
           ).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.post("/api/private-office/records/obligations",
                json={"title": "File the annual return", "obligation_type": "FILING",
                      "due_at": far})

    resp = client.get("/api/private-office/attention")
    body = resp.get_json() or {}
    check("attention answers 200", resp.status_code == 200,
          f"{resp.status_code} {body}")
    check("counts cover every view",
          set(body.get("counts") or {}) == set(po_retrieval.RECORD_VIEWS),
          str(body.get("counts")))
    titles = [r.get("title") for r in body.get("due_soon") or []]
    check("a near-due obligation appears in due_soon", "Pay the retainer" in titles,
          str(titles))
    check("a far-future obligation does not", "File the annual return" not in titles,
          str(titles))
    check("the horizon is stated, not implied", bool(body.get("due_horizon")),
          str(body.get("due_horizon")))
    check("attention is never cached",
          "no-store" in resp.headers.get("Cache-Control", ""))


def stage_projection():
    print("\n[projection over http]")
    _as(USER_A)
    client = _app().test_client()
    for view in po_retrieval.RECORD_VIEWS:
        blob = json.dumps(client.get(
            f"/api/private-office/records/{view}").get_json(), default=str)
        for leaked in ("owner_user_id", "record_key"):
            check(f"{view}: {leaked} never appears in the response",
                  leaked not in blob)
    attention_blob = json.dumps(
        client.get("/api/private-office/attention").get_json(), default=str)
    check("attention leaks no owner_user_id", "owner_user_id" not in attention_blob)


def stage_reads_are_audited():
    print("\n[read audit]")
    _as(USER_A)
    client = _app().test_client()
    before = _audit_count(po_audit.ACTION_RECORD_READ, "obligations")
    client.get("/api/private-office/records/obligations")
    after = _audit_count(po_audit.ACTION_RECORD_READ, "obligations")
    check("a list read leaves an audit row", after == before + 1,
          f"{before} -> {after}")

    before_attention = _audit_count(po_audit.ACTION_RECORD_READ, "attention")
    client.get("/api/private-office/attention")
    after_attention = _audit_count(po_audit.ACTION_RECORD_READ, "attention")
    check("an attention read leaves an audit row",
          after_attention == before_attention + 1,
          f"{before_attention} -> {after_attention}")


def stage_kill_switch():
    """The flag closes the whole surface without offering anything to buy."""
    print("\n[kill switch]")
    _as(USER_A)
    client = _app().test_client()
    spec = feature_matrix.FEATURES[routes.OPERATIONS_FEATURE_ID]
    previous = os.environ.get(spec.flag_env)
    os.environ[spec.flag_env] = "false"
    try:
        off = client.get("/api/private-office/records/obligations")
        check("the switch closes the read", off.status_code == 404,
              str(off.status_code))
        check("the switched-off route sells nothing",
              "minimum_tier" not in (off.get_json() or {}), str(off.get_json()))
        off_write = client.post("/api/private-office/records/obligations",
                                json=_SEED_BODIES["obligations"])
        check("the switch closes the write too", off_write.status_code == 404,
              str(off_write.status_code))
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
    stage_unknown_view()
    stage_create_and_list_every_view()
    stage_body_allowlist()
    stage_status_transitions()
    stage_owner_isolation()
    stage_second_lock()
    stage_attention()
    stage_projection()
    stage_reads_are_audited()
    stage_kill_switch()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_office_operations_routes():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
