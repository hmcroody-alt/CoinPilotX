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
from services import private_office_relationships_routes as rel_routes  # noqa: E402
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
#: The paths in this pack allowed to accept a write. The facts and records
#: endpoints add rows the member owns through the canonical writers; the
#: security endpoints manage the member's own second-lock passcode and unlock
#: grants. None of them can change what anybody is entitled to — an unlock
#: grant is not a tier, and `verify_and_unlock` never consults or writes the
#: entitlement tables. Every other path stays read-only.
_WRITABLE = frozenset({
    "/api/private-office/facts",
    "/api/private-office/records/<view>",
    "/api/private-office/records/<view>/<int:record_id>/status",
    "/api/private-office/security/setup",
    "/api/private-office/security/unlock",
    "/api/private-office/security/lock",
    "/api/private-office/security/change",
    "/api/private-office/security/reset",
    "/api/private-office/security/biometric"
})


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


def test_the_office_write_takes_no_owner_parameter():
    """Owner isolation on the write path is a property of the endpoint's shape.

    The owner comes from the session. There is no URL argument to point the
    write at somebody else, and no handler reads an owner from the body —
    asserted on the source so that adding one is a test failure rather than a
    review miss.

    This was written against the Private Facts write. That endpoint retired
    with its feature, and the property did not: it belongs to every Office
    write, not to that one. It is repointed at Relationship Intelligence, which
    now holds the Office's only body-carrying writes.

    The assertion is made over the module's syntax tree rather than over one
    function's text, and that change has a history worth keeping. The earlier
    version read the handler's source with ``inspect.getsource`` and looked for
    the literal ``owner_user_id=user["user_id"]``. When the person write grew a
    ``_save`` helper and the handler became a two-line delegation, the literal
    moved one frame down and the test failed — while the property it names was
    not merely intact but now enforced at *twelve* call sites instead of one.

    A security test that a refactor can turn red is a security test people learn
    to edit rather than believe, and the edit that makes it green again is
    usually the one that deletes it. So the claim is restated in terms the
    refactor cannot move: every ``owner_user_id`` and ``actor_user_id`` keyword
    *anywhere in the route pack* must be the session's own id, spelled
    ``user["user_id"]``. Delegation does not hide it, a new endpoint is covered
    the day it is written, and the way to break this test is to actually take an
    owner from somewhere other than the session.
    """
    app = Flask(__name__)
    routes.register(app)
    rel_routes.register(app)
    rules = [r for r in app.url_map.iter_rules()
             if str(r.rule) == "/api/private-office/relationships"
             and "POST" in r.methods]
    assert rules, "relationships write route not registered"
    assert rules[0].arguments == set()

    import ast
    import inspect
    tree = ast.parse(inspect.getsource(rel_routes))

    def is_session_owner(node) -> bool:
        """True for exactly ``user["user_id"]`` and nothing else."""
        return (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "user"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "user_id")

    owner_bindings = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in ("owner_user_id", "actor_user_id"):
                continue
            owner_bindings += 1
            assert is_session_owner(kw.value), (
                "%s= is bound to %r at line %d, not to the session's own "
                "user[\"user_id\"]. An owner that comes from anywhere but the "
                "session is an owner a client can choose."
                % (kw.arg, ast.dump(kw.value)[:120], getattr(kw.value, "lineno", -1)))

    # Anti-vacuity. If the route pack is ever restructured so that these
    # keywords are passed positionally, or through a dict splat, the loop above
    # finds nothing and passes for the worst possible reason.
    assert owner_bindings >= 8, (
        "expected the route pack to bind owner_user_id/actor_user_id by keyword "
        "at many call sites; found %d. If the calls moved to positional "
        "arguments or **kwargs this test can no longer see them, and it is "
        "passing vacuously rather than truthfully." % owner_bindings)

    # The other half: an owner the client supplies cannot be read even to be
    # ignored. Stated over the whole pack for the same reason as above.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "body"
                and node.args
                and isinstance(node.args[0], ast.Constant)):
            continue
        key = str(node.args[0].value)
        assert key != "owner_user_id", "the body cannot name the owner"
        assert not key.startswith("actor"), \
            "a client that names its own actor can forge the audit trail"
        assert not key.startswith("provenance"), \
            "a client that names its own provenance can label its typing VERIFIED"


def test_every_office_write_route_is_owner_scoped_by_shape():
    """The same property, stated over the surface instead of over one handler.

    Naming a single endpoint is what let the previous version of this test
    retire along with its subject. Derived from the URL map, a write added
    tomorrow is covered the day it is added — and a write that takes an owner
    from the URL fails here rather than in an incident.
    """
    app = Flask(__name__)
    routes.register(app)
    rel_routes.register(app)

    writes = [r for r in app.url_map.iter_rules()
              if str(r.rule).startswith("/api/private-office/")
              and r.methods & {"POST", "PUT", "PATCH", "DELETE"}]
    assert writes, "no Office writes found — this check has gone vacuous"

    for rule in writes:
        # ``node_id`` and friends address a row the owner already owns; the
        # handlers scope every such lookup by owner_user_id. What must never
        # appear is an argument naming *whose* office to write to.
        offenders = {a for a in rule.arguments
                     if "user" in a.lower() or "owner" in a.lower()}
        assert not offenders, (
            f"{rule.rule} takes {sorted(offenders)} from the URL — an Office "
            f"write must read its owner from the session, never from the path"
        )


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
