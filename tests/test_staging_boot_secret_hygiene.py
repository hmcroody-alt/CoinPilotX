"""Exercise the boot helper without importing the entire web application."""
import ast
import logging
from datetime import datetime
from pathlib import Path


def test_bootstrap_password_never_enters_log_record(caplog):
    tree = ast.parse((Path(__file__).parents[1] / "bot.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "remember_owner_temp_password")
    state = {}
    namespace = {"OWNER_BOOTSTRAP_TEMP": state, "OWNER_ADMIN_EMAIL": "staging@example.invalid",
                 "datetime": datetime, "logging": logging}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "boot-helper", "exec"), namespace)
    sentinel = "synthetic-password-not-a-real-credential"
    namespace[function.name](sentinel, "owner_admin_created")
    assert state["password"] == sentinel  # Preserve the authenticated one-time retrieval flow.
    assert state["display_available"]
    assert "GENERATED ONCE" in caplog.text
    assert sentinel not in caplog.text
    assert all(sentinel not in str(record.args) for record in caplog.records)


def test_staging_probe_is_truthful_without_redirecting_to_production(monkeypatch):
    from flask import Flask, redirect
    from types import SimpleNamespace
    import cj_staging_runtime as runtime
    app = Flask(__name__)
    app.before_request(lambda: redirect("https://pulsesoc.com", 301))
    bot = SimpleNamespace(app=app, ROUTE_PACK_STATUS={"suppliers": {"registered": True}})
    monkeypatch.setattr(runtime, "health", lambda: ({"infrastructure_ready": True}, 200))
    runtime.configure_http(bot)
    assert bot.CANONICAL_HTTPS_ORIGIN == runtime.ORIGIN
    assert bot.APP_BASE_URL == runtime.ORIGIN
    client = app.test_client()
    response = client.get("/health/ready", base_url="http://healthcheck.railway.app")
    assert response.status_code == 200 and response.json["infrastructure_ready"]
    assert "Location" not in response.headers
    bot.ROUTE_PACK_STATUS["suppliers"]["registered"] = False
    assert client.get("/health/ready").status_code == 503
    # No other route/method is exempted from the existing application hooks.
    assert client.post("/health/ready").status_code == 301
    assert client.get("/api/business-os/suppliers/cj/connections").status_code == 301


def test_postgres_cursor_iteration_preserves_mapping_and_consumption():
    from services.db import CompatCursor
    class RawCursor:
        description = [("id",), ("value",)]
        def __init__(self):
            self.rows = iter([(1, "first"), (2, "second")])
        def fetchone(self):
            return next(self.rows, None)
    cursor = CompatCursor(RawCursor())
    first = next(cursor)
    assert first[0] == first["id"] == 1
    assert [dict(row) for row in cursor] == [{"id": 2, "value": "second"}]
    assert list(cursor) == []
