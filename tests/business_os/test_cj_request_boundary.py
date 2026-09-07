"""Execute the actual shared bot request hooks without starting bot workers.

These are integration checks for credential/raw-byte handling, not a claim that
the complete production WSGI stack or external telemetry was exercised.
"""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, g, jsonify, request
import pytest

from services.business_os_supplier_routes import _body


@pytest.fixture
def boundary():
    source = Path(__file__).resolve().parents[2] / "bot.py"
    tree = ast.parse(source.read_text())
    wanted = {"pulse_security_core_guard", "interactive_security_guard"}
    hooks = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert len(hooks) == 2
    for node in hooks:
        node.decorator_list = []
    records = []
    namespace = dict(request=request, g=g, jsonify=jsonify, os=os, account_user_id=lambda: "fixture-user",
        client_ip_hash=lambda: "fixture-ip-hash", security_monitor=SimpleNamespace(record=lambda *args: records.append(args)),
        pulse_security_core=SimpleNamespace(kill_switch_for=lambda _: {}, device_fingerprint=lambda *args: "fixture-device",
            rate_limited=lambda **kwargs: {}, validate_json_shape=lambda *args: {"ok": True}),
        security_guard=SimpleNamespace(request_limit_for=lambda *args: None, suspicious_text=lambda value: "<script>" in value,
            file_extension_allowed=lambda _: True))
    exec(compile(ast.Module(body=hooks, type_ignores=[]), str(source), "exec"), namespace)
    app = Flask(__name__)
    for name in ("pulse_security_core_guard", "interactive_security_guard"):
        app.before_request(namespace[name])
    return app, records, namespace


@pytest.mark.parametrize("path", ["/api/business-os/suppliers/cj/connect", "/api/business-os/suppliers/cj/discover-shops",
                                  "/api/provider-webhooks/suppliers/cj/opaque-connection"])
def test_shared_middleware_never_caches_or_samples_cj_body(boundary, path):
    app, records, _ = boundary
    body = {"api_key": "fixture-sensitive-value", "provider_text": "<script>untrusted data</script>"}
    raw = json.dumps(body).encode()
    with app.test_request_context(path, method="POST", data=raw, content_type="application/json"):
        assert app.preprocess_request() is None
        assert not getattr(request, "_cached_data", None)
        assert _body() == body
        assert not getattr(request, "_cached_data", None)
    assert records == []


def test_non_supplier_xss_guard_is_unchanged(boundary):
    app, records, _ = boundary
    with app.test_request_context("/api/ordinary", method="POST", json={"text": "<script>bad</script>"}):
        response, status = app.preprocess_request()
        assert status == 400 and not response.get_json()["ok"]
    assert len(records) == 1 and records[0][0] == "xss_payload_blocked"


def test_supplier_still_obeys_shared_rate_limit(boundary):
    app, records, namespace = boundary
    namespace["pulse_security_core"].rate_limited = lambda **kwargs: {"limited": True, "retry_after": 120}
    namespace["pulse_cohost_guard_error"] = lambda *args, **kwargs: None
    with app.test_request_context("/api/business-os/suppliers/cj/connect", method="POST", json={"api_key": "fixture-sensitive-value"}):
        response, status = app.preprocess_request()
        assert status == 429 and response.headers["Retry-After"] == "120"
        assert not getattr(request, "_cached_data", None)
    assert "fixture-sensitive-value" not in repr(records)


def test_supplier_modules_have_no_telemetry_or_undx_secret_sink():
    root = Path(__file__).resolve().parents[2]
    sources = list((root / "services/business_os/suppliers").glob("*.py"))
    sources.append(root / "services/business_os_supplier_routes.py")
    assert len(sources) >= 10
    forbidden = {"sentry_sdk", "analytics", "undx", "logging"}
    for source in sources:
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(name.name.split(".")[0] in forbidden for name in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not set((node.module or "").split(".")) & forbidden
