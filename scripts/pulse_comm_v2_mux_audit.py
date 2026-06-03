#!/usr/bin/env python3
"""Audit Pulse Communications V2 Mux foundation."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    service = (ROOT / "pulse_communications_v2" / "service.py").read_text()
    routes = (ROOT / "pulse_communications_v2" / "routes.py").read_text()
    models = (ROOT / "pulse_communications_v2" / "models.py").read_text()
    ast.parse(service)
    for name in ["create_comm_v2_mux_live_stream", "get_comm_v2_mux_live_stream", "disable_comm_v2_mux_live_stream", "verify_mux_webhook_signature"]:
        expect(name in service, f"Mux method missing: {name}", failures)
    for field in ["mux_live_stream_id", "mux_stream_key", "mux_playback_id", "mux_live_status", "mux_recording_asset_id", "mux_recording_playback_id"]:
        expect(field in models and field in service, f"Mux live field missing: {field}", failures)
    expect("/live/mux/webhook" in routes, "Mux webhook route missing", failures)
    expect("Mux-Signature" in routes, "Mux webhook signature check missing", failures)
    expect("https://stream.mux.com" in service or "playback_url(" in service, "Mux playback URL support missing", failures)
    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS pulse_comm_v2_mux_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
