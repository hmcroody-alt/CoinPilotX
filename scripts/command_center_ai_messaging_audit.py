#!/usr/bin/env python3
"""Audit the PulseSoc Command Center AI messaging foundation."""

from __future__ import annotations

import json
import os
import py_compile
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-ai-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "ai_messaging.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def configure_env(db_path: Path, *, ai_enabled: bool = False) -> None:
    os.environ["PULSESOC_SERVICE_NAME"] = "command-center-worker"
    os.environ["PULSESOC_SERVICE_ROLE"] = "worker"
    os.environ["COMMAND_CENTER_WORKER_ENABLED"] = "true"
    os.environ["COMMAND_CENTER_INTERNAL_TOKEN"] = AUDIT_TOKEN
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["PULSE_AI_ENABLED"] = "true" if ai_enabled else "false"
    os.environ["PULSE_AI_INTERNAL_ONLY"] = "true"
    os.environ["PULSE_AI_MAX_CONTEXT_MESSAGES"] = "30"
    os.environ.pop("PULSE_AI_PROVIDER", None)
    os.environ.pop("PULSE_AI_MODEL", None)
    os.environ.pop("REDIS_URL", None)


def audit_ai_module_disabled() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "ai_disabled.db"
        configure_env(db_path, ai_enabled=False)
        sys.path.insert(0, str(ROOT))
        from services.command_center_worker import ai_messaging

        ai_messaging.ensure_ai_schema()
        response = ai_messaging.summarize_conversation({
            "user_id": 9,
            "conversation_id": 11,
            "messages": [{"role": "member", "body": "hello secret_token_123 should not print"}],
        })
        expect(response.get("available") is False, "disabled AI should be unavailable")
        expect(response.get("reason") == "ai_disabled", "disabled AI reason mismatch")
        return {"response": response}


def audit_worker_endpoints() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "ai_endpoints.db"
        configure_env(db_path, ai_enabled=True)
        sys.path.insert(0, str(ROOT))
        from services.command_center_worker.app import app
        from services.command_center_worker import ai_messaging

        ai_messaging.ensure_ai_schema()
        client = app.test_client()
        payload = {
            "event_id": "audit-ai-1",
            "user_id": 44,
            "conversation_id": 55,
            "messages": [
                {"role": "member", "body": "Can you verify my wallet at https://walletconnect-verify.top?"},
                {"role": "me", "body": "No thanks"},
            ],
            "api_key": "must-not-leak",
            "token": "must-not-leak",
        }
        missing = client.post("/internal/command-center/ai/summary", json=payload)
        expect(missing.status_code == 401, "AI endpoint should reject missing token")
        accepted = client.post(
            "/internal/command-center/ai/summary",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}", "X-Idempotency-Key": "audit-ai-1"},
            json=payload,
        )
        expect(accepted.status_code == 200, f"AI endpoint returned {accepted.status_code}")
        accepted_json = accepted.get_json() or {}
        expect(accepted_json.get("available") is False, "AI endpoint should return safe disabled/unavailable response without provider")
        expect(accepted_json.get("reason") in {"provider_not_configured", "internal_only_adapter_pending"}, "AI endpoint unavailable reason mismatch")
        replies = client.post(
            "/internal/command-center/ai/smart-replies",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json=payload,
        )
        scam = client.post(
            "/internal/command-center/ai/scam-explanation",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"event_id": "audit-scam-ai", "user_id": 44, "security_event": {"event_type": "phishing_link", "score": 90}},
        )
        moderation = client.post(
            "/internal/command-center/ai/moderation-insight",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"event_id": "audit-mod-ai", "user_id": 44, "reason": "review needed"},
        )
        expect(replies.status_code == 200, "smart replies endpoint failed")
        expect(scam.status_code == 200, "scam explanation endpoint failed")
        expect(moderation.status_code == 200, "moderation insight endpoint failed")
        serialized = json.dumps({
            "summary": accepted_json,
            "replies": replies.get_json() or {},
            "scam": scam.get_json() or {},
            "moderation": moderation.get_json() or {},
        }, sort_keys=True)
        expect(AUDIT_TOKEN not in serialized, "internal token leaked in AI response")
        expect("must-not-leak" not in serialized, "secret marker leaked in AI response")
        expect(str(db_path) not in serialized, "database path leaked in AI response")
        return {
            "missing": missing.status_code,
            "summary": accepted_json,
            "smart_replies": replies.get_json() or {},
            "scam": scam.get_json() or {},
            "moderation": moderation.get_json() or {},
        }


def audit_disabled_main_app_client() -> dict:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    os.environ["PULSE_AI_ENABLED"] = "false"
    from services import command_center_client

    summary = command_center_client.request_chat_summary(1, 2, [{"body": "hello"}])
    replies = command_center_client.request_smart_replies(1, 2, [{"body": "hello"}])
    scam = command_center_client.request_scam_explanation({"event_type": "phishing_link"}, user_id=2)
    insight = command_center_client.request_moderation_insight({"reason": "audit"}, user_id=2)
    expect(summary.get("available") is False, "disabled chat summary should be safe")
    expect(replies.get("available") is False, "disabled smart replies should be safe")
    expect(scam.get("available") is False, "disabled scam explanation should be safe")
    expect(insight.get("available") is False, "disabled moderation insight should be safe")
    return {"summary": summary, "replies": replies, "scam": scam, "insight": insight}


def audit_source_hooks() -> dict:
    client_source = (ROOT / "services" / "command_center_client.py").read_text(encoding="utf-8")
    worker_source = (ROOT / "services" / "command_center_worker" / "ai_messaging.py").read_text(encoding="utf-8")
    routes_source = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
    template_source = (ROOT / "templates" / "pulse_messages_v2.html").read_text(encoding="utf-8")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("request_chat_summary" in client_source and "request_smart_replies" in client_source, "main app AI client methods missing")
    expect("PULSE_AI_ENABLED" in worker_source and "PULSE_AI_INTERNAL_ONLY" in worker_source, "AI env support missing")
    expect("ai_context_for_conversation" in routes_source, "conversation permission helper is not used")
    expect("data-ai-summary" in template_source and "data-ai-replies" in template_source, "Messages AI hooks missing")
    expect("AI analysis not enabled" in bot_source and "request_scam_explanation" in bot_source, "admin security AI hook missing")
    expect("auto-send" not in worker_source.lower(), "AI module should not auto-send messages")
    return {"permission_helpers_exist": True, "ui_hooks_present": True, "admin_hook_present": True}


def main() -> int:
    compiled = compile_targets()
    module_disabled = audit_ai_module_disabled()
    endpoints = audit_worker_endpoints()
    disabled_client = audit_disabled_main_app_client()
    source_hooks = audit_source_hooks()
    report = {
        "ok": True,
        "compiled": compiled,
        "ai_module_imports": True,
        "env_disabled_mode_works": module_disabled["response"].get("reason") == "ai_disabled",
        "protected_endpoints_reject_missing_token": endpoints["missing"] == 401,
        "valid_token_receives_safe_disabled_response": endpoints["summary"].get("available") is False,
        "no_ai_required_for_normal_messaging": disabled_client["summary"].get("available") is False,
        "no_secrets_printed": True,
        "permission_helpers_exist": source_hooks["permission_helpers_exist"],
        "worker_disabled_main_app_path_safe": disabled_client["summary"].get("available") is False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
