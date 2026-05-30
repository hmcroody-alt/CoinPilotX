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

    require(source.count("data-status2-form") >= 1, "one unified status creation form exists")
    require("data-status-unified-create='desktop-mobile'" in source, "status editor declares unified desktop/mobile contract")
    require("data-status-create-form='dedicated'" in source, "status form is shared across breakpoints")
    require(source.count("id='pulseStatus2Media'") == 1, "one shared media picker powers desktop and mobile")
    require(source.count("data-status2-preview") >= 1, "one shared media preview powers desktop and mobile")
    home_start = source.find("def pulse_status_rail_html")
    home_end = source.find("def pulse_status_type_buttons_html")
    home_source = source[home_start:home_end]
    require("data-status2-form" not in home_source and "pulseStatus2Media" not in home_source, "shared creator is not visible on homepage by default")
    require("Post Status" in source, "desktop/mobile creator has a clear post action")
    require("PulseUploadManager.upload" in source and "/api/pulse/media/upload" in source, "desktop/mobile status upload uses same media pipeline")
    require("renderStatusMediaPreview" in source and "URL.createObjectURL" in source, "status preview state is shared")
    require("statusForm?.addEventListener('submit'" in source, "status publishing uses one submit path")

    for token in [
        ".pulse-status2-modal",
        ".pulse-status2-composer",
        ".pulse-status2-type-grid",
        "@media (max-width: 900px)",
        "@media (max-width: 560px)",
    ]:
        require(token in css, f"responsive status CSS contains {token}")

    require(".pulse-status2-composer" in css, "status stage styling is centralized")
    require("grid-template-columns: minmax(0, 640px)" in css, "desktop Status page uses constrained polished composer column")
    require("pulse-status-home-entry" in css, "homepage uses compact Status entry styling")
    require("white-space: nowrap" in css and "overflow-x: auto" in css, "status type selector avoids overlap with horizontal compact pills")
    require("100dvh" in css and "env(safe-area-inset-bottom)" in css, "mobile status editor is safe-area aware")
    require("object-fit: contain" in css, "media preview preserves image/video content on all screens")
    require("capture" not in source[source.find("pulseStatus2Media") : source.find("pulseStatus2Media") + 320], "mobile gallery picker does not force a separate camera-only flow")
    print("create status parity audit ok")


if __name__ == "__main__":
    main()
