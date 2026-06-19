#!/usr/bin/env python3
"""Audit the PulseSoc Command Center security and scam-shield engine."""

from __future__ import annotations

import json
import os
import py_compile
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-security-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "security_engine.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def configure_env(db_path: Path) -> None:
    os.environ["PULSESOC_SERVICE_NAME"] = "command-center-worker"
    os.environ["PULSESOC_SERVICE_ROLE"] = "worker"
    os.environ["COMMAND_CENTER_WORKER_ENABLED"] = "true"
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    os.environ["COMMAND_CENTER_INTERNAL_TOKEN"] = AUDIT_TOKEN
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("REDIS_URL", None)


def audit_engine() -> dict:
    sys.path.insert(0, str(ROOT))
    from services.command_center_worker import security_engine

    low = security_engine.risk_level(24)
    medium = security_engine.risk_level(25)
    high = security_engine.risk_level(50)
    critical = security_engine.risk_level(75)
    expect((low, medium, high, critical) == ("Low", "Medium", "High", "Critical"), "risk levels are incorrect")

    phishing = security_engine.score_event(
        "phishing_link",
        {"text": "Connect wallet to claim airdrop https://walletconnect-verify.top now", "repeat_count": 2},
    )
    expect(phishing["score"] >= 75 and phishing["severity"] == "Critical", "phishing scoring should be critical")
    spam = security_engine.score_event("mass_dm", {"recipient_count": 35, "repeat_count": 4})
    expect(spam["severity"] in {"High", "Critical"}, "mass DM scoring should be high or critical")
    invalid = False
    try:
        security_engine.score_event("delete_user", {})
    except security_engine.SecurityValidationError:
        invalid = True
    expect(invalid, "invalid security event types should be rejected")
    return {"phishing": phishing, "spam": spam}


def audit_worker_endpoints() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "security_audit.db"
        configure_env(db_path)
        sys.path.insert(0, str(ROOT))

        from services.command_center_worker.app import app
        from services.command_center_worker import security_engine

        security_engine.ensure_security_schema()
        client = app.test_client()
        payload = {
            "event_id": "audit-security-1",
            "user_id": 77,
            "actor_id": 0,
            "event_type": "phishing_link",
            "payload": {
                "text": "Verify your wallet at https://walletconnect-verify.top",
                "token": "must-not-leak",
                "private_key": "must-not-leak",
            },
        }
        missing = client.post("/internal/command-center/security/event", json=payload)
        expect(missing.status_code == 401, "security event endpoint should reject missing token")

        accepted = client.post(
            "/internal/command-center/security/event",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}", "X-Idempotency-Key": "audit-security-1"},
            json=payload,
        )
        expect(accepted.status_code == 200, f"security event endpoint returned {accepted.status_code}")
        accepted_json = accepted.get_json() or {}
        expect(accepted_json.get("accepted") is True, "security event was not accepted")
        expect(accepted_json.get("severity") == "Critical", "security event did not score critical")

        risk = client.get(
            "/internal/command-center/security/user/77/risk",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(risk.status_code == 200, f"risk endpoint returned {risk.status_code}")
        risk_json = risk.get_json() or {}
        expect(risk_json.get("risk_score", 0) >= 75, "user risk score did not include event")

        recent = client.get(
            "/internal/command-center/security/recent?limit=10",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(recent.status_code == 200, f"recent security endpoint returned {recent.status_code}")
        recent_json = recent.get_json() or {}
        expect(isinstance(recent_json.get("events"), list), "recent security events shape is unsafe")

        serialized = json.dumps({"accepted": accepted_json, "risk": risk_json, "recent": recent_json}, sort_keys=True)
        expect(AUDIT_TOKEN not in serialized, "internal token leaked in endpoint response")
        expect("must-not-leak" not in serialized, "sensitive payload leaked in endpoint response")
        expect(str(db_path) not in serialized, "database path leaked in endpoint response")
        return {"missing": missing.status_code, "accepted": accepted_json, "risk": risk_json, "recent_count": len(recent_json.get("events") or [])}


def audit_dashboard_source() -> dict:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("Command Center Risk Signals" in source, "admin security dashboard does not render risk signals")
    expect("Block IP" in source and "Block Domain" in source and "Mark Safe" in source and "Investigate" in source, "admin moderation actions missing")
    expect("mask_email(email)" in source, "failed-login dispatch should keep email masked")
    return {"dashboard_loads_source_check": True, "email_masking_present": True}


def audit_disabled_client() -> dict:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    from services import command_center_client

    event = command_center_client.enqueue_security_event({"event_type": "login_failed", "user_id": 1, "payload": {"count": 1}})
    risk = command_center_client.get_user_risk_score(1)
    recent = command_center_client.get_recent_security_events(limit=5)
    expect(event.get("reason") == "disabled", "disabled security dispatch should be safe")
    expect(risk.get("available") is False and risk.get("risk_level") == "Low", "disabled risk lookup should be safe")
    expect(recent.get("available") is False and isinstance(recent.get("events"), list), "disabled recent lookup should be safe")
    return {"event": event, "risk": risk, "recent": recent}


def main() -> int:
    compiled = compile_targets()
    engine = audit_engine()
    endpoints = audit_worker_endpoints()
    dashboard = audit_dashboard_source()
    disabled = audit_disabled_client()
    report = {
        "ok": True,
        "compiled": compiled,
        "scoring_works": engine,
        "events_persist": endpoints["recent_count"] >= 1,
        "risk_levels_correct": True,
        "protected_endpoints_reject_missing_token": endpoints["missing"] == 401,
        "valid_token_accepts_security_event": endpoints["accepted"].get("accepted") is True,
        "dashboard_loads": dashboard["dashboard_loads_source_check"],
        "no_secrets_exposed": True,
        "worker_disabled_main_app_path_safe": disabled["event"].get("reason") == "disabled",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
