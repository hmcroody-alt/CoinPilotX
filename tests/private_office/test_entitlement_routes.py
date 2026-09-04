"""The HTTP surface for canonical tier truth — auth, shape, and read-only-ness.

Runs against the blueprint mounted on a bare Flask app with a stub ``bot``
module in ``sys.modules``, so none of this imports the 118k-line monolith. What
is under test is the pack's own contract, and that is exactly what a stub keeps
in frame.

    python -m pytest tests/private_office/test_entitlement_routes.py
    python tests/private_office/test_entitlement_routes.py
"""

import os
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_routes_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route pack can import it ------------------
_stub = types.ModuleType("bot")
_stub._test_user = None
_stub._test_admin_denied = ("DENIED", 403)


def _api_account_user():
    return _stub._test_user


def _require_admin_api(permission):
    if _stub._test_admin_denied is None:
        return ({"id": 1, "permission": permission}, None)
    return (None, _stub._test_admin_denied)


_stub.api_account_user = _api_account_user
_stub.require_admin_api = _require_admin_api
sys.modules["bot"] = _stub

from flask import Flask  # noqa: E402

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services.private_office import tiers  # noqa: E402

UID = 700
UID_HELD = 701


def _app():
    app = Flask(__name__)
    routes.register(app)
    return app


def setup_module(module=None):
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid, st in ((UID, "active"), (UID_HELD, "suspended")):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)",
                (uid, st),
            )
        conn.commit()
    finally:
        conn.close()


def _reset_grants():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_ent_grants")
        conn.commit()
    finally:
        conn.close()


# --- authentication ---------------------------------------------------------
def test_entitlement_requires_login():
    _stub._test_user = None
    with _app().test_client() as client:
        resp = client.get("/api/private-office/entitlement")
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_entitlement_answers_only_for_the_caller():
    """There is no user parameter to pass, so this endpoint cannot be pointed
    at somebody else's account. Proved over the URL rule, not by reading the
    handler and hoping."""
    app = _app()
    rules = [r for r in app.url_map.iter_rules()
             if str(r.rule) == "/api/private-office/entitlement"]
    assert rules, "route not registered"
    assert rules[0].arguments == set()


def test_entitlement_returns_the_full_contract():
    _reset_grants()
    _stub._test_user = {"user_id": UID, "account_status": "active",
                        "access_enabled": 1}
    svc.grant_entitlement(UID, "private.access", source="admin")
    with _app().test_client() as client:
        resp = client.get("/api/private-office/entitlement")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["effective_tier"] == tiers.TIER_PRIVATE
    for field in ("effective_tier", "source", "status", "expires_at",
                  "features", "verified_at"):
        assert field in body, field
    assert body["features"]["advanced_undx"]["availability"] == "ENTITLED"


def test_entitlement_is_never_http_cached():
    """A cached tier is a stale tier — a revocation a proxy keeps serving."""
    _reset_grants()
    _stub._test_user = {"user_id": UID, "account_status": "active"}
    with _app().test_client() as client:
        resp = client.get("/api/private-office/entitlement")
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_account_hold_is_reflected_over_http():
    _reset_grants()
    svc.grant_entitlement(UID_HELD, "private_office.access", source="admin")
    _stub._test_user = {"user_id": UID_HELD, "account_status": "suspended",
                        "access_enabled": 1}
    with _app().test_client() as client:
        body = client.get("/api/private-office/entitlement").get_json()
    assert body["effective_tier"] == tiers.TIER_FREE
    assert body["status"] == tiers.STATUS_ACCOUNT_HOLD


def test_degraded_resolve_returns_200_with_ok_false():
    """The load-bearing distinction: during an outage the client must be able
    to say "temporarily unavailable". A 500 gives it nothing to distinguish,
    and a confident ok=True FREE would tell a paying member they are not one."""
    _reset_grants()
    _stub._test_user = {"user_id": UID, "account_status": "active"}
    original = svc.get_entitlements
    svc.get_entitlements = lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    try:
        with _app().test_client() as client:
            resp = client.get("/api/private-office/entitlement")
    finally:
        svc.get_entitlements = original
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["resolver_state"] == tiers.RESOLVER_DEGRADED
    assert body["status"] == tiers.STATUS_UNAVAILABLE
    assert body["effective_tier"] == tiers.TIER_FREE  # fails closed


def test_missing_account_status_does_not_silence_the_hold_check():
    """Forwarding a context dict without account_status would make the resolver
    treat 'unknown' as 'supplied' and stop consulting the database. The route
    must forward nothing in that case."""
    assert routes._context_from({"user_id": 5}) == {}
    assert routes._context_from({"user_id": 5, "account_status": "active"}) == {
        "account_status": "active", "access_enabled": None}


# --- admin status surface ---------------------------------------------------
def test_status_requires_admin():
    _stub._test_admin_denied = ("DENIED", 403)
    with _app().test_client() as client:
        resp = client.get("/api/admin/private-office/status")
    assert resp.status_code == 403


def test_status_returns_health_for_an_admin():
    _reset_grants()
    _stub._test_admin_denied = None
    try:
        with _app().test_client() as client:
            resp = client.get("/api/admin/private-office/status")
    finally:
        _stub._test_admin_denied = ("DENIED", 403)
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["resolver"]["healthy"] is True
    assert "private_shield.breach_monitoring" in body["providers"]
    assert "tier_counts" in body


# --- no second granting authority -------------------------------------------
#: The one path in this pack allowed to accept a write. It adds a row the member
#: owns to the member's own fact store through the canonical writer; it cannot
#: change what anybody is entitled to. Every other path stays read-only.
_WRITABLE = frozenset({"/api/private-office/facts"})


def test_no_route_here_can_grant_a_tier():
    """Granting a tier belongs to the existing admin entitlement paths. A
    granting write registered here would be a second authority, so the absence
    of one is asserted over the URL map rather than left to code review.

    This used to read "the pack is GET-only", which was true and is no longer
    the claim: the fact-creation endpoint is a write. The claim that actually
    matters survived the change intact — nothing here writes an entitlement —
    so it is now stated directly instead of being approximated by the method
    set, and the one writable path is named rather than implied.
    """
    app = _app()
    for rule in app.url_map.iter_rules():
        path = str(rule.rule)
        if not path.startswith(("/api/private-office", "/api/admin/private-office")):
            continue
        if path in _WRITABLE:
            continue
        assert rule.methods <= {"GET", "HEAD", "OPTIONS"}, \
            f"{path} exposes {rule.methods}"


def test_the_entitlement_endpoints_stay_read_only():
    """The tier answer and the admin status surface are still GET-only."""
    app = _app()
    for rule in app.url_map.iter_rules():
        if str(rule.rule) in ("/api/private-office/entitlement",
                              "/api/admin/private-office/status"):
            assert rule.methods <= {"GET", "HEAD", "OPTIONS"}, str(rule.methods)


def test_the_fact_write_takes_no_owner_parameter():
    """Owner isolation on the write path is a property of the endpoint's shape.

    The owner comes from the session. There is no URL argument to point the
    write at somebody else, and the handler never reads an owner from the body
    — asserted on the source so that adding one is a test failure rather than a
    review miss."""
    app = _app()
    rules = [r for r in app.url_map.iter_rules()
             if str(r.rule) == "/api/private-office/facts"]
    assert rules, "facts route not registered"
    assert rules[0].arguments == set()

    import inspect
    source = inspect.getsource(routes.api_private_office_create_fact)
    assert 'body.get("owner_user_id")' not in source
    assert 'body.get("provenance' not in source, \
        "a client that names its own provenance can label its typing VERIFIED"
    assert 'owner_user_id=user["user_id"]' in source


if __name__ == "__main__":
    setup_module()
    failures = []
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures.append((name, exc))
                print("FAIL", name, "->", type(exc).__name__, exc)
    print(f"\n{'FAILED' if failures else 'OK'}: {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
