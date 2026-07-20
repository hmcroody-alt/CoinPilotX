#!/usr/bin/env python3
"""Deterministic architecture and contract audit for UNDX V4 NEXUS CORE."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import undx_architecture, undx_policy  # noqa: E402


def main() -> int:
    path = ROOT / "backend/undx/config/undx_training_v4_nexus_core.yaml"
    raw = path.read_bytes()
    spec = yaml.safe_load(raw)
    cases = [case for suite in spec["evaluation"]["suites"].values() for case in suite]
    checks: dict[str, bool] = {}
    original = os.environ.get("UNDX_CONFIG_VERSION")
    os.environ["UNDX_CONFIG_VERSION"] = "4.0"
    try:
        policy = undx_policy.load_policy()
        context = undx_policy.compile_context("Turn on post notifications.")
    finally:
        if original is None:
            os.environ.pop("UNDX_CONFIG_VERSION", None)
        else:
            os.environ["UNDX_CONFIG_VERSION"] = original

    checks["v4_active"] = policy["schema_version"] == "4.0" and policy["codename"] == "NEXUS CORE"
    checks["v1_v2_v3_retained"] = all((ROOT / f"backend/undx/config/{name}").exists() for name in (
        "undx_intelligence_bootstrap.yaml", "undx_intelligence_bootstrap_v2.yaml", "undx_intelligence_bootstrap_v3.yaml"
    ))
    checks["dynamic_compilation"] = len(context["system_context"]) < len(raw) and context["compiled_chars"] <= undx_policy.MAX_POLICY_CHARS
    checks["identity_invariant"] = "canonical name is UNDX" in context["system_context"]
    checks["notification_route_reused"] = undx_policy.PRODUCTION_TOOL_REGISTRY["pulsesoc.notification_preferences.update"]["route"] == "/api/pulse/notifications/preferences"
    checks["confirmation_required"] = context["requires_confirmation"] and "pulsesoc.notification_preferences.update" in context["tool_names"]
    checks["writes_fail_closed_by_default"] = context["writes_enabled"] is False
    checks["eval_ids_unique"] = len(cases) == len({case["id"] for case in cases})

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    undx_architecture.ensure_schema(cur)
    action = undx_architecture.notification_action_from_text("Turn on post notifications.")
    checks["typed_action_resolution"] = bool(action) and action["target_id"] == "posts" and action["arguments"]["push"] is True
    confirmation = undx_architecture.create_confirmation(cur, 7, action)
    consumed = undx_architecture.consume_confirmation(cur, 7, confirmation["confirmation_token"])
    checks["confirmation_one_time"] = bool(consumed) and undx_architecture.consume_confirmation(cur, 7, confirmation["confirmation_token"]) is None
    checks["confirmation_account_bound"] = undx_architecture.consume_confirmation(cur, 8, confirmation["confirmation_token"]) is None
    safe_context = undx_architecture.sanitize_ui_context({"current_route": "Chat", "selected_conversation_id": -9001001, "admin": True, "token": "secret"})
    checks["ui_context_allowlisted"] = safe_context == {"current_route": "Chat", "selected_conversation_id": "-9001001"}
    checks["v4_schema_extensions"] = all(cur.execute("SELECT name FROM sqlite_master WHERE name=?", (name,)).fetchone() for name in (
        "pulse_ai_confirmations", "pulse_ai_client_contexts", "pulse_ai_missions", "pulse_ai_tool_operations"
    ))
    conn.close()

    native = (ROOT / "mobile-native/src/screens/ChatScreen.tsx").read_text()
    messenger = (ROOT / "mobile-native/src/api/messenger.ts").read_text()
    routes = (ROOT / "pulse_communications_v2/routes.py").read_text()
    checks["native_structured_cards"] = all(term in native + messenger for term in ("confirmation_card", "verified_success_card", "Confirm UNDX action"))
    checks["native_server_confirmation"] = "confirmPulseAiAction" in messenger and "/api/pulse-ai/actions/confirm" in messenger
    checks["authenticated_orchestration_route"] = "/api/pulse-ai/actions/confirm" in routes and "_require_user()" in routes
    checks["spec_hash_stable"] = hashlib.sha256(raw).hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()

    failures = sorted(name for name, passed in checks.items() if not passed)
    result = {
        "ok": not failures,
        "schema_version": spec["schema_version"],
        "codename": spec["codename"],
        "spec_sha256": hashlib.sha256(raw).hexdigest(),
        "evaluation_case_count": len(cases),
        "checks": checks,
        "failures": failures,
        "release_ready": False,
        "manual_blockers": [
            "full simulator V4 matrix",
            "personally observed physical iPhone 16 Pro V4 matrix",
            "production feature-flag rollout and live action verification",
        ],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
