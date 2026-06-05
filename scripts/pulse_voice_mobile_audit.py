#!/usr/bin/env python3
"""Audit mobile readiness for Communications V2 voice notes."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "pulse_messages_v2.css").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "pulse_messages_v2.html").read_text(encoding="utf-8")

for needle in [
    'grid-template-columns: 44px 44px minmax(0, 1fr) 64px',
    ".voice-recorder",
    ".voice-actions button",
    "env(safe-area-inset-bottom)",
]:
    assert needle in CSS, f"missing mobile voice style {needle}"

for needle in ["aria-label=\"Record voice note\"", "aria-live=\"polite\"", "data-voice-timer"]:
    assert needle in TEMPLATE, f"missing accessible mobile recorder markup {needle}"

print("pulse voice mobile audit ok")
