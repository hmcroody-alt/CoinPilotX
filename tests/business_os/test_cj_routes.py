"""Actual Flask cookie/session and shared Commerce CSRF route-boundary tests.

The bot resolver is replaced by a minimal session-backed authority so tests do
not boot the monolith; authorization, CSRF gate, parsers and handlers are real.
"""
import json
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
