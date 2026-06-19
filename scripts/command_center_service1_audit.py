#!/usr/bin/env python3
"""Audit PulseSoc main-app readiness for future Command Center dispatch."""

from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_SENTINEL = "super-secret-command-center-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def run_optional_env_import_check() -> str:
    env = os.environ.copy()
    for key in (
        "PULSESOC_SERVICE_NAME",
        "PULSESOC_SERVICE_ROLE",
        "COMMAND_CENTER_ENABLED",
        "COMMAND_CENTER_INTERNAL_URL",
        "COMMAND_CENTER_INTERNAL_TOKEN",
    ):
        env.pop(key, None)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services import command_center_client as c; import json; print(json.dumps(c.status(), sort_keys=True))",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    expect("main-app" in output, "default service name is not used when env vars are absent")
    expect("web" in output, "default service role is not used when env vars are absent")
    return output


def run_secret_redaction_check() -> str:
    env = os.environ.copy()
    env["COMMAND_CENTER_ENABLED"] = "false"
    env["COMMAND_CENTER_INTERNAL_TOKEN"] = SECRET_SENTINEL
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services import command_center_client as c; import json; print(json.dumps(c.status(), sort_keys=True)); print(json.dumps(c.enqueue_security_event({'audit': True}), sort_keys=True))",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    expect(SECRET_SENTINEL not in output, "command center token value leaked in client output")
    expect("COMMAND_CENTER_INTERNAL_TOKEN" not in output, "token env key leaked in client output")
    return output


def audit_flask_health_endpoint() -> dict:
    os.environ.setdefault("COMMAND_CENTER_ENABLED", "false")
    sys.path.insert(0, str(ROOT))
    import bot  # noqa: WPS433 - audit imports the app to inspect route behavior.

    client = bot.webhook_app.test_client()
    response = client.get("/api/service/health")
    expect(response.status_code == 200, f"health endpoint returned {response.status_code}")
    data = response.get_json() or {}
    expect(data.get("service_name") == "main-app", "health endpoint service_name is incorrect")
    expect(data.get("service_role") == "web", "health endpoint service_role is incorrect")
    expect(data.get("command_center_enabled") is False, "disabled worker mode should be reported as false")
    expect("database_ok" in data, "health endpoint does not report database_ok")
    serialized = json.dumps(data, sort_keys=True)
    expect("token" not in serialized.lower(), "health endpoint exposes token-shaped fields")
    expect("secret" not in serialized.lower(), "health endpoint exposes secret-shaped fields")
    expect(SECRET_SENTINEL not in serialized, "health endpoint leaked a secret value")
    return data


def audit_disabled_dispatch() -> dict:
    sys.path.insert(0, str(ROOT))
    from services import command_center_client

    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    result = command_center_client.enqueue_notification_event({"audit": True}, idempotency_key="service1-audit")
    expect(result.get("ok") is True, "disabled dispatch should be safe")
    expect(result.get("dispatched") is False, "disabled dispatch should not call a worker")
    expect(result.get("reason") == "disabled", "disabled dispatch reason should be explicit")
    return result


def main() -> int:
    compiled = compile_targets()
    optional_env_output = run_optional_env_import_check()
    redaction_output = run_secret_redaction_check()
    health = audit_flask_health_endpoint()
    disabled_dispatch = audit_disabled_dispatch()
    report = {
        "ok": True,
        "compiled": compiled,
        "client_imports": True,
        "health_endpoint": {
            "service_name": health.get("service_name"),
            "service_role": health.get("service_role"),
            "command_center_enabled": health.get("command_center_enabled"),
            "database_ok": health.get("database_ok"),
        },
        "disabled_worker_mode": disabled_dispatch,
        "env_vars_optional": "main-app" in optional_env_output and "web" in optional_env_output,
        "secrets_printed": SECRET_SENTINEL in redaction_output,
    }
    expect(report["secrets_printed"] is False, "secret sentinel was printed")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
