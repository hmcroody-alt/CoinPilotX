"""Actual Flask cookie/session and shared Commerce CSRF route-boundary tests.

The bot resolver is replaced by a minimal session-backed authority so tests do
not boot the monolith; authorization, CSRF gate, parsers and handlers are real.
"""
import json
import re
from types import SimpleNamespace

from flask import Flask, jsonify, request, session
import pytest

from services import business_os_supplier_routes as routes
from services.business_os.suppliers import connections
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_connections import database, connect  # noqa: F401

BASE = "/api/business-os/suppliers/cj"
CSRF = "fixture-cookie-csrf"


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "synthetic-session-signing-key"
    app.testing = True

    def user():
        actor = session.get("user_id")
        return {"user_id": actor, "account_status": session.get("account_status", "active"), "access_enabled": 1} if actor else None

    def admin(permission):
        assert permission == "users.view"
        if session.get("is_admin"):
            return {"id": session["user_id"]}, None
        return None, (jsonify({"ok": False, "code": "admin_required"}), 403)

    monkeypatch.setattr(routes, "_bot", lambda: SimpleNamespace(api_account_user=user, require_admin_api=admin))
    routes.register(app)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, actor="100", *, admin=False):
    with client.session_transaction() as state:
        state["user_id"] = actor
        state["csrf_token"] = CSRF
        state["is_admin"] = admin


def payload(**changes):
    body = {"business_id": "biz-a", "store_id": "store-a", "api_key": "fixture-route-api-key", "external_shop_id": "cj-shop-a"}
    body.update(changes)
    return body


def post(client, url, body=None, **kwargs):
    return client.post(url, json=body or payload(), headers={"X-CSRF-Token": CSRF}, **kwargs)


def test_dark_by_default_and_login_required_before_connect(client, monkeypatch):
    calls = []
    monkeypatch.setattr(connections, "connect_cj", lambda *a, **k: calls.append(a))
    monkeypatch.delenv("BUSINESS_OS_SUPPLIERS_CJ")
    assert post(client, BASE + "/connect").status_code == 404
    monkeypatch.setenv("BUSINESS_OS_SUPPLIERS_CJ", "true")
    assert post(client, BASE + "/connect").status_code == 401
    assert calls == []


def test_cookie_write_uses_real_csrf_gate_and_forged_bearer_does_not_bypass(client, monkeypatch):
    login(client)
    calls = []
    monkeypatch.setattr(connections, "connect_cj", lambda *a, **k: calls.append(a))
    for headers in ({}, {"X-CSRF-Token": "wrong"}, {"Authorization": "Bearer forged-not-verified"}):
        response = client.post(BASE + "/connect", json=payload(), headers=headers)
        assert response.status_code == 403 and response.get_json()["code"] == "csrf"
    assert calls == []


def test_a_csrf_refusal_says_which_half_of_the_gate_refused(client, monkeypatch):
    """A 403 here is indistinguishable from the outside, and self-sustaining.

    The cookie authenticates the request, so the access log records a signed-in
    user being refused and nothing about why. Three different faults land on this
    one status -- no bearer sent, a bearer that will not resolve, and a bearer
    belonging to a different user than the cookie -- and the client cannot climb
    out of any of them, because `pulseApi` refreshes on 401 and this is a 403.

    So the gate records which of its terms failed. Asserted through the
    persisted row rather than the response alone, because the encoding exists
    specifically to survive `diagnostics.record`'s `name:number` validator; a
    string that reached the operator as `?` would be worse than none.
    """
    from services import db
    from services.business_os.suppliers import diagnostics

    login(client)
    with client.session_transaction() as state:
        # The key the gate itself reads. `login` sets `user_id` for this file's
        # account stub; the cookie leg of the CSRF check is a different name, and
        # conflating them is how the bit under test would read as always-zero.
        state["account_user_id"] = "100"
    monkeypatch.setenv("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN", "on")
    calls = []
    monkeypatch.setattr(connections, "connect_cj", lambda *a, **k: calls.append(a))

    # Cookie present, no bearer at all: the shape a native client has when its
    # access token expired and only the cookie leg is still being sent.
    no_bearer = client.post(BASE + "/connect", json=payload()).get_json()
    # Same, plus an unverifiable bearer -- the header is there, it resolves to
    # nobody. Must not read the same as "no header".
    forged = client.post(BASE + "/connect", json=payload(),
                         headers={"Authorization": "Bearer forged-not-verified"}).get_json()

    assert no_bearer["code"] == forged["code"] == "csrf"
    assert no_bearer["origin"] == "csrf_gate:00100"
    assert forged["origin"] == "csrf_gate:10100"
    assert calls == []

    conn = db.connect()
    try:
        diagnostics.ensure_schema(conn)
        rows = conn.execute("SELECT origin FROM business_os_cj_diagnostic_origin "
                            "ORDER BY observed_at").fetchall()
    finally:
        conn.close()
    stored = [r["origin"] for r in rows]
    assert stored == ["csrf_gate:00100", "csrf_gate:10100"], (
        f"{stored}: the encoding must survive the record validator, not arrive as '?'")


def test_connect_server_resolves_actor_context_and_does_not_cache_secret_request_body(client, monkeypatch):
    login(client)
    observed = {}

    def connect_spy(*args, **kwargs):
        observed["args"], observed["kwargs"] = args, kwargs
        observed["cached_data"] = getattr(request, "_cached_data", None)
        observed["remaining_stream"] = request.stream.read()
        return {"id": "safe-connection", "status": "CONNECTED", "credential_present": True}

    monkeypatch.setattr(connections, "connect_cj", connect_spy)
    response = post(client, BASE + "/connect", payload(actor_user_id="200", merchant_id="200"))
    assert response.status_code == 201
    assert observed["args"][:3] == ("biz-a", "store-a", "100")
    assert observed["kwargs"]["context"]["account_status"] == "active"
    assert observed["cached_data"] is None and observed["remaining_stream"] == b""
    assert "fixture-route-api-key" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache" and response.headers["Referrer-Policy"] == "no-referrer"


def test_inactivity_forecast_is_reachable_and_tenant_scoped(client):
    """The countdown is worthless if a merchant cannot see it, and dangerous if
    the wrong one can. Both halves are checked here rather than only the first."""
    row = connect()
    login(client, "100")
    url = BASE + "/connections/" + row["id"] + "/inactivity?business_id=biz-a&store_id=store-a"
    response = client.get(url)
    assert response.status_code == 200
    forecast = response.get_json()["inactivity"]
    # Sandbox-only, so structurally zero real orders and the full window ahead.
    assert forecast["real_orders"] == 0 and forecast["state"] == "OK"
    assert forecast["sandbox_orders_count_toward_this"] is False
    # The caveat travels with the number over the wire, not just in Python.
    assert forecast["estimate_may_be_late"] is True

    login(client, "200")
    denied = client.get(url)
    assert denied.status_code == 404 and row["id"] not in denied.get_data(as_text=True)


@pytest.mark.parametrize("method,url", [("get", BASE + "/connections?business_id=biz-a&store_id=store-a"),
    ("get", BASE + "/connections/{id}?business_id=biz-a&store_id=store-a")])
def test_actual_connection_read_denies_other_merchant(client, method, url):
    row = connect()
    login(client, "200")
    response = getattr(client, method)(url.replace("{id}", row["id"]))
    assert response.status_code == 404 and row["id"] not in response.get_data(as_text=True)


def test_real_scope_authorization_denies_cross_merchant_before_cj_auth(client, monkeypatch):
    login(client, "200")
    calls = []
    monkeypatch.setattr(connections, "_new_adapter", lambda *args: calls.append(args))
    response = post(client, BASE + "/connect")
    assert response.status_code == 404 and calls == []


@pytest.mark.parametrize("data,content_type,status", [("bad json", "application/json", 400),
    ("[]", "application/json", 400), ("{}", "text/plain", 415), ("x" * 131073, "application/json", 413)])
def test_strict_bounded_body_parser_rejects_before_secret_handler(client, monkeypatch, data, content_type, status):
    login(client)
    calls = []
    monkeypatch.setattr(connections, "connect_cj", lambda *a, **k: calls.append(a))
    result = client.post(BASE + "/connect", data=data, content_type=content_type, headers={"X-CSRF-Token": CSRF})
    assert result.status_code == status and calls == []


@pytest.mark.parametrize("name", ["api_key", "accessToken", "refresh-token", "openId", "ciphertext", "credential_bundle"])
def test_future_secret_projection_is_blocked_recursively(client, monkeypatch, name):
    login(client)
    monkeypatch.setattr(connections, "discover_shops", lambda *a, **k: {"shops": [{name: "fixture-must-not-leak"}]})
    result = post(client, BASE + "/discover-shops")
    assert result.status_code == 500 and result.get_json()["code"] == "secret_output_blocked"
    assert "fixture-must-not-leak" not in result.get_data(as_text=True)


def test_dataclass_auth_bundle_cannot_bypass_secret_response_guard(client, monkeypatch):
    from services.business_os.suppliers.cj import AuthBundle
    login(client)
    bundle = AuthBundle("fixture-dataclass-access", "fixture-dataclass-refresh", "fixture-dataclass-openid",
                        "2030-01-01T00:00:00Z", "2031-01-01T00:00:00Z")
    monkeypatch.setattr(connections, "connect_cj", lambda *args, **kwargs: bundle)
    response = post(client, BASE + "/connect")
    assert response.status_code == 500
    assert "fixture-dataclass" not in response.get_data(as_text=True)


def test_provider_exception_text_and_invalid_code_never_escape(client, monkeypatch, caplog):
    login(client)

    def fail(*args, **kwargs):
        failure = RuntimeError("fixture-secret-provider-message")
        failure.code = "https://secret.invalid/fixture-key"
        raise failure

    monkeypatch.setattr(connections, "connect_cj", fail)
    result = post(client, BASE + "/connect")
    assert result.status_code == 503 and result.get_json()["code"] == "supplier_unavailable"
    assert "fixture-secret" not in result.get_data(as_text=True) and "fixture-key" not in caplog.text


def test_retry_after_preserves_valid_provider_delay_without_shortening(client, monkeypatch):
    login(client)

    def fail(*args, **kwargs):
        raise SupplierError("RATE_LIMITED", http_status=429, retry_after=172800.5)

    monkeypatch.setattr(connections, "connect_cj", fail)
    result = post(client, BASE + "/connect")
    assert result.status_code == 429
    assert int(result.headers["Retry-After"]) >= 172800.5


def test_subscribe_and_read_routes_require_csrf_and_real_tenant_scope(client, monkeypatch):
    row = connect()
    login(client, "200")
    calls = []
    monkeypatch.setattr(connections, "_new_adapter", lambda *args: calls.append(args))
    for suffix in ("subscribe", "read/product"):
        result = post(client, BASE + "/connections/" + row["id"] + "/" + suffix,
                      {"business_id": "biz-a", "store_id": "store-a", "params": {"pid": "1001"}})
        assert result.status_code == 404
    assert calls == []


def test_shop_binding_routes_require_csrf_and_real_tenant_scope(client, monkeypatch):
    """Reachable, and only by the merchant who owns the connection.

    Listed beside `subscribe` and `read/product` rather than trusted to the
    service tests, because the service function being right is not the same
    claim as the route existing -- the admin moderation defect earlier in this
    chain was a route nobody had ever posted to.
    """
    row = connect()
    calls = []
    # The real handlers run; only the credential hydration is stubbed, so a
    # refusal reaching CJ at all would show up as a non-empty `calls`.
    monkeypatch.setattr(connections, "_hydrate", lambda *a, **k: calls.append(a))
    body = {"business_id": "biz-a", "store_id": "store-a", "external_shop_id": "cj-shop-a"}
    for suffix in ("bind-shop", "shops"):
        url = BASE + "/connections/" + row["id"] + "/" + suffix
        login(client, "100")
        assert client.post(url, json=body).status_code == 403  # no CSRF header
        login(client, "200")
        assert post(client, url, body).status_code == 404  # foreign merchant
    assert calls == []


def test_binding_a_shop_over_http_answers_with_the_binding_and_no_secret(client, monkeypatch):
    """The one route that turns a shopless connection into a fulfilling one."""
    from services import db
    from tests.business_os.test_cj_connections import FakeAdapter, SECRETS

    row = connect()
    conn = db.connect()
    conn.execute("UPDATE business_os_supplier_connections SET external_shop_id='' WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    monkeypatch.setattr(connections, "_hydrate", lambda *a, **k: FakeAdapter())
    login(client, "100")
    response = post(client, BASE + "/connections/" + row["id"] + "/bind-shop",
                    {"business_id": "biz-a", "store_id": "store-a", "external_shop_id": "cj-shop-a"})
    assert response.status_code == 200
    assert response.get_json()["data"]["external_shop_id"] == "cj-shop-a"
    text = response.get_data(as_text=True)
    assert not any(secret in text for secret in SECRETS.values())

    listed = post(client, BASE + "/connections/" + row["id"] + "/shops",
                  {"business_id": "biz-a", "store_id": "store-a"}).get_json()["data"]
    assert listed["external_shop_id"] == "cj-shop-a"
    assert [(s["shop_id"], s["fulfillable"]) for s in listed["shops"]] == [("cj-shop-a", True)]


def test_admin_health_requires_existing_admin_authority(client):
    login(client)
    url = "/api/admin/business-os/suppliers/cj/health"
    assert client.get(url).status_code == 403
    login(client, admin=True)
    response = client.get(url)
    assert response.status_code == 200 and response.get_json()["health"]["connections_count"] == 0


def test_missing_sandbox_stops_at_route_before_fulfillment_handler(client, monkeypatch):
    from services.business_os.suppliers import fulfillment
    row = connect()
    login(client)
    called = []
    monkeypatch.setattr(fulfillment, "create_intent", lambda **kwargs: called.append(kwargs))
    result = post(client, BASE + "/connections/" + row["id"] + "/fulfillment-intents",
        {"business_id": "biz-a", "store_id": "store-a", "order_id": "order-fixture", "idempotency_key": "idem-fixture"})
    assert result.status_code == 400 and result.get_json()["code"] == "sandbox_flag_required"
    assert called == []


# `_error` answers every one of a dozen validators with the same opaque 502, so
# a real provider response we refuse arrives with no way to ask which field was
# refused. `CJ_SUPPLIER_DIAGNOSTIC_ORIGIN` returns that one coordinate. These
# pin what it may and may not carry -- the point of the flag is that it buys a
# raise site and nothing else, so a later "while we're here" widening that let
# provider text through would have to break one of these first.

def _raise_from_adapter(monkeypatch, factory):
    def boom(*args, **kwargs):
        from services.business_os.suppliers import cj
        factory(cj)
    monkeypatch.setattr(connections, "discover_shops", boom)


def test_diagnostic_origin_is_absent_unless_a_deployment_asks_for_it(client, monkeypatch):
    login(client)
    monkeypatch.delenv("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN", raising=False)
    _raise_from_adapter(monkeypatch, lambda cj: cj._dict(None))
    response = post(client, BASE + "/discover-shops")
    body = response.get_json()
    assert response.status_code == 502 and body["error_code"] == "MALFORMED_PROVIDER_RESPONSE"
    assert "origin" not in body


def test_diagnostic_origin_separates_validators_that_share_one_error_code(client, monkeypatch):
    login(client)
    monkeypatch.setenv("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN", "on")
    seen = {}
    for label, factory in (("dict", lambda cj: cj._dict(None)),
                           ("text", lambda cj: cj._text("x" * 201, 200, required=True)),
                           ("id", lambda cj: cj._id("not-an-id", provider=True))):
        _raise_from_adapter(monkeypatch, factory)
        body = post(client, BASE + "/discover-shops").get_json()
        assert body["error_code"] == "MALFORMED_PROVIDER_RESPONSE"
        seen[label] = body["origin"]
    # One code, three raise sites: distinguishing them is the whole purpose.
    assert len(set(seen.values())) == 3
    assert all(re.fullmatch(r"[a-z_]+\.py:\d+", value) for value in seen.values())


def test_diagnostic_origin_cannot_carry_provider_text_or_a_credential(client, monkeypatch):
    login(client)
    monkeypatch.setenv("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN", "on")

    def boom(*args, **kwargs):
        raise SupplierError("MALFORMED_PROVIDER_RESPONSE")

    monkeypatch.setattr(connections, "discover_shops", boom)
    text = post(client, BASE + "/discover-shops").get_data(as_text=True)
    assert "fixture-route-api-key" not in text
    # Raised outside the supplier package, so there is no frame to attribute and
    # the field stays empty rather than reaching for the nearest other frame.
    assert json.loads(text)["origin"] == ""


def test_diagnostic_origin_is_recorded_where_an_operator_can_read_it(client, monkeypatch):
    # The caller that actually hits these failures is a mobile screen that
    # renders a sentence and throws the body away, so returning the field is not
    # by itself enough to make it observable on a deployment.
    from services import db
    from services.business_os.suppliers import diagnostics

    login(client)
    monkeypatch.setenv("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN", "on")
    _raise_from_adapter(monkeypatch, lambda cj: cj._text("x" * 201, 200, required=True))
    returned = post(client, BASE + "/discover-shops").get_json()["origin"]

    conn = db.connect()
    try:
        diagnostics.ensure_schema(conn)
        rows = conn.execute("SELECT code, status, origin FROM business_os_cj_diagnostic_origin").fetchall()
    finally:
        conn.close()
    assert [(r["code"], r["status"], r["origin"]) for r in rows] == [
        ("MALFORMED_PROVIDER_RESPONSE", 502, returned)]


def test_diagnostic_record_stores_a_raise_site_and_refuses_anything_else(monkeypatch):
    from services import db
    from services.business_os.suppliers import diagnostics

    for code, status, origin in (("MALFORMED_PROVIDER_RESPONSE", 502, "cj.py:54"),
                                 ("<script>", 502, "cj.py:54"),
                                 ("MALFORMED_PROVIDER_RESPONSE", 999, "APIkey is wrong"),
                                 ("MALFORMED_PROVIDER_RESPONSE", 502, "")):
        diagnostics.record(code, status, origin)
    conn = db.connect()
    try:
        rows = conn.execute("SELECT code, status, origin FROM business_os_cj_diagnostic_origin "
                            "ORDER BY observed_at").fetchall()
    finally:
        conn.close()
    stored = [(r["code"], r["status"], r["origin"]) for r in rows]
    # Provider text cannot become an origin, and a bad code cannot become one
    # either: everything that is not a raise site collapses to a placeholder.
    assert stored == [("MALFORMED_PROVIDER_RESPONSE", 502, "cj.py:54"),
                      ("?", 502, "cj.py:54"),
                      ("MALFORMED_PROVIDER_RESPONSE", 0, "?"),
                      ("MALFORMED_PROVIDER_RESPONSE", 502, "")]


def test_diagnostic_names_the_endpoint_because_one_line_serves_a_dozen_of_them(client, monkeypatch):
    """`_request` rejects every CJ call from a single line, so a line is not enough.

    A raise site told us which validator refused a response. It could not tell us
    which *call* was refused, because `SUPPLIER_REJECTED` is raised once for all
    of `PATHS`. The endpoint is one of our own approved constants -- checked
    against PATHS before the request is issued -- so naming it costs no provider
    data, and it is the difference between "CJ rejected something" and "CJ
    rejected shop/getShops".
    """
    from services import db
    from services.business_os.suppliers import diagnostics

    login(client)
    monkeypatch.setenv("CJ_SUPPLIER_DIAGNOSTIC_ORIGIN", "on")
    _raise_from_adapter(monkeypatch, lambda cj: (_ for _ in ()).throw(
        SupplierError("SUPPLIER_REJECTED", http_status=422, endpoint="shop/getShops", provider_code=1600300)))
    body = post(client, BASE + "/discover-shops").get_json()
    assert body["endpoint"] == "shop/getShops" and body["provider_code"] == 1600300

    conn = db.connect()
    try:
        diagnostics.ensure_schema(conn)
        row = conn.execute("SELECT endpoint, provider_code FROM business_os_cj_diagnostic_origin").fetchall()[-1]
    finally:
        conn.close()
    assert (row["endpoint"], row["provider_code"]) == ("shop/getShops", 1600300)


@pytest.mark.parametrize("endpoint,provider_code", [
    ("https://evil.example/steal?k=SECRET", 200),        # not one of our paths
    ("shop/getShops", "APIkey is wrong"),                # a message is not a code
    ("shop/getShops", 1.5),                              # nor is a float
    ("shop/getShops", True),                             # nor a bool wearing an int's clothes
])
def test_diagnostic_endpoint_and_provider_code_refuse_anything_but_a_coordinate(endpoint, provider_code):
    """The integer is the whole safety argument -- so nothing else may pass as one."""
    error = SupplierError("SUPPLIER_REJECTED", endpoint=endpoint, provider_code=provider_code)
    assert error.endpoint in (None, "shop/getShops") and error.provider_code in (None, 200)
    if endpoint.startswith("https"):
        assert error.endpoint is None
    if not (type(provider_code) is int and type(provider_code) is not bool):
        assert error.provider_code is None


def test_diagnostic_table_cannot_grow_without_bound(monkeypatch):
    from services import db
    from services.business_os.suppliers import diagnostics

    monkeypatch.setattr(diagnostics, "KEEP_ROWS", 5)
    for line in range(20):
        diagnostics.record("MALFORMED_PROVIDER_RESPONSE", 502, f"cj.py:{line}")
    conn = db.connect()
    try:
        rows = conn.execute("SELECT origin FROM business_os_cj_diagnostic_origin").fetchall()
    finally:
        conn.close()
    assert len(rows) <= 5 and "cj.py:19" in {r["origin"] for r in rows}
