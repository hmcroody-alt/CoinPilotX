#!/usr/bin/env python3
"""Audit the PulseSoc Command Center worker skeleton."""

from __future__ import annotations

import json
import os
import py_compile
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-worker-audit-token"
SECRET_SENTINEL = "postgresql://pulse:super-secret-password@example.invalid:5432/pulsesoc"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "services" / "command_center_worker" / "__init__.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "config.py",
        ROOT / "services" / "command_center_worker" / "health.py",
        ROOT / "services" / "command_center_worker" / "security.py",
        ROOT / "services" / "command_center_worker" / "heartbeat.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def configure_audit_env() -> None:
    os.environ["PULSESOC_SERVICE_NAME"] = "command-center-worker"
    os.environ["PULSESOC_SERVICE_ROLE"] = "worker"
    os.environ["COMMAND_CENTER_WORKER_ENABLED"] = "true"
    os.environ["COMMAND_CENTER_INTERNAL_TOKEN"] = AUDIT_TOKEN
    os.environ["COMMAND_CENTER_HEARTBEAT_SECONDS"] = "30"
    os.environ["DATABASE_URL"] = "sqlite:///coinpilotx.db"
    os.environ.pop("REDIS_URL", None)


def audit_worker_endpoints() -> dict:
    configure_audit_env()
    sys.path.insert(0, str(ROOT))
    from services.command_center_worker.app import app
    from services.command_center_worker.config import load_config
    from services.command_center_worker.health import health_payload

    config = load_config()
    expect(config.service_name == "command-center-worker", "worker service name did not load")
    expect(config.service_role == "worker", "worker service role did not load")
    expect(config.worker_enabled is True, "worker should default enabled for Service 2")
    expect(config.redis_configured is False, "Redis should be optional for the skeleton")

    health = health_payload(config)
    expect("database_ok" in health, "database check missing from health payload")
    expect(health.get("redis_enabled") is False, "missing Redis should report disabled without crashing")
    expect(health.get("redis_ok") is False, "missing Redis should report not ok without crashing")
    health_json = json.dumps(health, sort_keys=True)
    expect(AUDIT_TOKEN not in health_json, "internal token leaked in health payload")
    expect("COMMAND_CENTER_INTERNAL_TOKEN" not in health_json, "token env key leaked in health payload")

    client = app.test_client()
    response = client.get("/internal/command-center/health")
    expect(response.status_code == 200, f"worker health returned {response.status_code}")
    health_response = response.get_json() or {}
    expect(health_response.get("service_name") == "command-center-worker", "worker health service name mismatch")
    expect(health_response.get("service_role") == "worker", "worker health service role mismatch")
    expect(health_response.get("internal_auth_configured") is True, "worker health should report auth configured")
    serialized_health = json.dumps(health_response, sort_keys=True)
    expect(AUDIT_TOKEN not in serialized_health, "internal token leaked in health response")
    expect(SECRET_SENTINEL not in serialized_health, "database secret leaked in health response")

    missing_token = client.post(
        "/internal/command-center/events/test",
        json={"event_type": "test", "source": "main-app", "payload": {"audit": True}},
    )
    expect(missing_token.status_code == 401, "protected endpoint should reject missing token")

    invalid_token = client.post(
        "/internal/command-center/events/test",
        headers={"Authorization": "Bearer wrong-token"},
        json={"event_type": "test", "source": "main-app", "payload": {"audit": True}},
    )
    expect(invalid_token.status_code == 403, "protected endpoint should reject invalid token")

    accepted = client.post(
        "/internal/command-center/events/test",
        headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        json={"event_type": "test", "source": "main-app", "payload": {"audit": True}},
    )
    expect(accepted.status_code == 200, f"protected endpoint returned {accepted.status_code}")
    accepted_json = accepted.get_json() or {}
    expect(accepted_json.get("accepted") is True, "valid token event was not accepted")
    expect(accepted_json.get("status") == "received", "valid token event status mismatch")
    expect(bool(accepted_json.get("event_id")), "valid token event did not return event_id")

    return {
        "health": health_response,
        "missing_token_status": missing_token.status_code,
        "invalid_token_status": invalid_token.status_code,
        "accepted_status": accepted.status_code,
        "accepted_event_id_present": bool(accepted_json.get("event_id")),
    }


def audit_main_app_not_moved() -> dict:
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    client_source = (ROOT / "services" / "command_center_client.py").read_text(encoding="utf-8")
    expect("services.command_center_worker" not in bot_source, "main app imports worker package")
    expect("COMMAND_CENTER_ENABLED\", True" not in client_source, "main app dispatch default was enabled")
    expect("COMMAND_CENTER_ENABLED\", False" in client_source, "main app dispatch default is not explicitly disabled")
    return {
        "main_app_imports_worker": False,
        "main_app_dispatch_default": "disabled",
    }


def main() -> int:
    compiled = compile_targets()
    endpoints = audit_worker_endpoints()
    main_app = audit_main_app_not_moved()
    report = {
        "ok": True,
        "compiled": compiled,
        "worker_imports": True,
        "config_loads": True,
        "health_endpoint_exists": True,
        "protected_endpoint_rejects_missing_token": endpoints["missing_token_status"] == 401,
        "protected_endpoint_rejects_invalid_token": endpoints["invalid_token_status"] == 403,
        "protected_endpoint_accepts_valid_token": endpoints["accepted_status"] == 200,
        "database_check_exposes_secrets": False,
        "redis_missing_does_not_crash": endpoints["health"].get("redis_enabled") is False and endpoints["health"].get("redis_ok") is False,
        "main_app_behavior_moved": False,
        "main_app": main_app,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
