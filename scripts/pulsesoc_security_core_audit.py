#!/usr/bin/env python3
"""Static gate for the PulseSoc security core directive."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing file: {path}")
    return target.read_text(encoding="utf-8")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def expect_all(text: str, tokens: list[str], label: str) -> None:
    for token in tokens:
        expect(token in text, f"{label}: {token}")


def main() -> None:
    core = read("services/pulse_security_core.py")
    bot = read("bot.py")
    mobile = read("mobile/pulse-react-native/App.tsx")
    native_live_audit = read("mobile/pulse-react-native/scripts/native-live-audit.js")
    app_review_audit = read("scripts/pulseshell_app_review_audit.py")

    ast.parse(core)
    ast.parse(read("services/security_guard.py"))

    expect_all(
        core,
        [
            "HIGH_RISK_RATE_RULES",
            "STRICT_JSON_FIELDS",
            "PULSESHELL_ALLOWED_ACTIONS",
            "KILL_SWITCH_ENV",
            "validate_pulseshell_call",
            "consume_nonce",
            "server_authoritative",
            "X-PulseSoc-Client-Trust",
            "cache_engine.cache_get",
            "cache_engine.cache_set",
            "PULSESOC_DISABLE_LIVE",
            "PULSESOC_DISABLE_COHOST",
            "PULSESOC_FREEZE_PAYMENTS",
            "PULSESOC_THROTTLE_MESSAGING",
        ],
        "security core contract",
    )
    expect_all(
        bot,
        [
            "pulse_security_core_guard",
            "pulse_security_core.kill_switch_for",
            "pulse_security_core.rate_limited",
            "pulse_security_core.validate_json_shape",
            "api_pulseshell_validate",
            "/api/pulseshell/validate",
            "pulse_security_core.validate_pulseshell_call",
            "pulse_security_core.security_headers",
            "pulse_security_rate_limited",
            "pulse_security_unknown_json_fields",
            "pulseshell_server_validation",
            "mobile_security_sessions",
            "issue_mobile_security_tokens",
            "rotate_mobile_refresh_token",
            "revoke_mobile_refresh_token",
            "join_request_race",
            "WHERE id=? AND live_id=? AND status='pending'",
        ],
        "Flask security boundary",
    )
    expect_all(
        mobile,
        [
            "validatePulseShellCall",
            "/api/pulseshell/validate",
            "timestamp",
            "nonce",
            "serverAuthoritative",
            "PulseShell request was not approved by the server.",
        ],
        "native PulseShell server validation",
    )
    expect("PULSESOC_OPEN_NATIVE_LIVE" in native_live_audit, "native live audit still checks bridge entry")
    expect("/api/pulseshell/validate" in mobile, "PulseShell validation endpoint is used by native shell")
    expect("PRIVATE_STREAM_KEY" in app_review_audit, "App Review audit still checks secret leakage")

    forbidden_mobile_tokens = ["LIVEKIT_API_SECRET", "MUX_TOKEN_SECRET", "STRIPE_SECRET", "DATABASE_URL"]
    for token in forbidden_mobile_tokens:
        expect(token not in mobile, f"mobile source does not expose {token}")

    print("PulseSoc security core audit passed.")


if __name__ == "__main__":
    main()
