#!/usr/bin/env python3
"""Audit App Store CTA behavior for PulseSoc Intelligence Pulses."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


APP_STORE_URL = "https://apps.apple.com/us/app/pulsesoc/id6777591572"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> int:
    service = (ROOT / "services" / "pulsesoc_intelligence_engine.py").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "pulsesoc_intelligence_center.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pulsesoc_intelligence_center.css").read_text(encoding="utf-8")
    knowledge = (ROOT / "data" / "pulse_ai" / "pulsesoc_knowledge.json").read_text(encoding="utf-8")

    for token in ["PULSESOC_APP_STORE_URL", APP_STORE_URL, "ALLOWED_ACTION_DOMAINS", "validate_actions"]:
        if token not in service:
            fail(f"central CTA service missing {token}")
    for token in ["apps.apple.com", "isSafeExternalUrl", "navigator.share", "clipboard.writeText", "signal-cta"]:
        if token not in js:
            fail(f"CTA renderer/handler missing {token}")
    if "signal-cta" not in css:
        fail("CTA styling missing")
    if "raw URL" not in knowledge or "Download PulseSoc" not in knowledge:
        fail("Pulse AI knowledge does not describe safe download/share behavior")

    if "javascript:" not in service:
        fail("server-side action validation does not reject javascript-style URLs")
    if "javascript:" not in js:
        fail("client-side action validation does not reject javascript-style URLs")

    report = ROOT / "reports" / "pulsesoc_intelligence_app_store_cta.md"
    if not report.exists():
        fail("App Store CTA report missing")

    print("PASS: PulseSoc Intelligence App Store CTA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
