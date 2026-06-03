#!/usr/bin/env python3
"""Audit Pulse Status upload, preview, and viewer contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = STATUS_CSS.read_text(encoding="utf-8")

    for token in [
        "data-status2-media",
        "data-status2-pick-media",
        "statusMedia.disabled=false",
        "renderStatusMediaPreview",
        "statusSelected=isVideo?'video':'photo'",
        "Add text, a photo, or a video before posting.",
        "fd.append('context_type','pulse_status')",
        "pulseApi('/api/pulse/media/upload'",
        "pulseApi('/api/pulse/status'",
        "status_style:currentStatusStyle()",
    ]:
        expect(token in source, f"Status upload contract includes {token}")

    for token in [
        "data-status-viewer",
        "data-status-viewer-media",
        "data-status-viewer-progress",
        "data-status-viewer-prev",
        "data-status-viewer-next",
        "data-status-viewer-close",
        "PulseMediaRenderer.renderMedia",
        "data-status-preview-seconds=\"10\"",
    ]:
        expect(token in source, f"Status viewer contract includes {token}")

    expect("statusMedia.disabled=!['photo','video'].includes(statusSelected)" not in source, "Status media picker is not locked behind a mode")
    expect(".pulse-status-story-viewer" in css and "position: fixed" in css, "Status viewer is full-screen capable")
    expect(".pulse-status-card-media" in css and "object-fit: cover" in css, "Status cards show readable media previews")


if __name__ == "__main__":
    main()
