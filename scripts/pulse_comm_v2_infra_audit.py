#!/usr/bin/env python3
"""Audit Pulse Communications V2 infrastructure diagnostics."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    infra = ROOT / "pulse_communications_v2" / "infrastructure.py"
    routes = ROOT / "pulse_communications_v2" / "routes.py"
    flags = ROOT / "pulse_communications_v2" / "flags.py"
    bot = ROOT / "bot.py"
    source = infra.read_text()
    bot_source = bot.read_text()
    ast.parse(source)
    expect("TWILIO_ACCOUNT_SID" in source, "Twilio SID diagnostic missing", failures)
    expect("TWILIO_AUTH_TOKEN" in source, "Twilio auth diagnostic missing", failures)
    expect("R2_ACCESS_KEY_ID" in source and "R2_SECRET_ACCESS_KEY" in source, "R2 diagnostics missing", failures)
    expect("R2_ENDPOINT_URL" in source and "MUX_SOURCE_BASE_URL" in source, "R2/Mux source optional diagnostics missing", failures)
    expect("MUX_TOKEN_ID" in source and "MUX_TOKEN_SECRET" in source and "MUX_WEBHOOK_SECRET" in source, "Mux diagnostics missing", failures)
    expect("MUX_DATA_ENV_KEY" in source, "Mux data env optional diagnostic missing", failures)
    expect('"readiness"' in source and '"video_messages"' in source, "Readiness diagnostics missing", failures)
    expect("os.environ.get(name)" in source and '"configured"' in source, "Diagnostics should expose configured yes/no only", failures)
    expect("diagnostics" in routes.read_text(), "Diagnostics route missing", failures)
    expect("/admin/pulse-infrastructure" in bot_source, "Admin Pulse Infrastructure route missing", failures)
    expect("admin_pulse_infrastructure_page" in bot_source, "Admin Pulse Infrastructure handler missing", failures)
    expect("require_admin_page(\"system.view\")" in bot_source, "Admin Pulse Infrastructure should require system.view", failures)
    expect('"true"' in flags.read_text().lower(), "V2 feature flag should default published with env rollback", failures)
    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS pulse_comm_v2_infra_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
