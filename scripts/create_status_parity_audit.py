#!/usr/bin/env python3
"""Audit Create Status desktop/mobile parity.

Create Status must be one shared Pulse component with responsive adaptation,
not separate desktop and mobile creation systems.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, label: str, details: str = ""):
    if not condition:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")

    require(source.count("id='pulseStatusForm'") == 1, "one unified status creation form exists")
    require("data-status-unified-create='desktop-mobile'" in source, "status editor declares unified desktop/mobile contract")
    require("data-status-create-form='unified'" in source, "status form is shared across breakpoints")
    require(source.count("id='pulseStatusMedia'") == 1, "one shared media picker powers desktop and mobile")
    require(source.count("id='pulseStatusPreview'") == 1, "one shared media preview powers desktop and mobile")
    require("PulseUploadManager.upload" in source and "/api/pulse/media/upload" in source, "desktop/mobile status upload uses same media pipeline")
    require("renderStatusPreview()" in source and "statusDraft.files" in source, "status preview state is shared")
    require("statusForm?.addEventListener('submit'" in source, "status publishing uses one submit path")

    for token in [
        "--pulse-status-stage-width",
        "--pulse-status-stage-height",
        "--pulse-status-stage-radius",
        "@media (min-width: 900px)",
        "@media (max-width: 520px)",
    ]:
        require(token in css, f"responsive status CSS contains {token}")

    require(css.count(".pulse-status-editor .pulse-status-stage") <= 2, "status stage styling is centralized")
    require("100dvh" in css and "env(safe-area-inset-bottom)" in css, "mobile status editor is safe-area aware")
    require("object-fit: contain" in css, "media preview preserves image/video content on all screens")
    require("capture" not in source[source.find("pulseStatusMedia") : source.find("pulseStatusMedia") + 320], "mobile gallery picker does not force a separate camera-only flow")
    print("create status parity audit ok")


if __name__ == "__main__":
    main()
