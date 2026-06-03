#!/usr/bin/env python3
"""Audit Pulse Communications V2 attachment foundation."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    service = ROOT / "pulse_communications_v2" / "service.py"
    models = ROOT / "pulse_communications_v2" / "models.py"
    routes = ROOT / "pulse_communications_v2" / "routes.py"
    js = ROOT / "static" / "js" / "pulse_messages_v2.js"
    service_text = service.read_text()
    model_text = models.read_text()
    ast.parse(service_text)
    expect("comm_v2_attachments" in model_text, "Attachment table missing", failures)
    for field in ["cdn_url", "playback_url", "thumbnail_url", "file_size", "mux_asset_id", "mux_playback_id", "mux_status"]:
        expect(field in model_text and field in service_text, f"Attachment field missing: {field}", failures)
    expect("stage_attachment_upload" in service_text, "Attachment upload service missing", failures)
    expect("/attachments/upload" in routes.read_text(), "Attachment upload route missing", failures)
    expect("/attachments/upload" in js.read_text(), "V2 frontend should use V2 attachment upload endpoint", failures)
    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS pulse_comm_v2_attachments_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
