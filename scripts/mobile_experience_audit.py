#!/usr/bin/env python3
"""Audit mobile-first Pulse homepage, Status, safe-area, and non-decorative controls."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text()
    status_css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text()
    mobile_css = (ROOT / "static" / "css" / "pulse_mobile_system.css").read_text()
    env_js = (ROOT / "static" / "js" / "pulse_environment_engine.js").read_text()
    combined = source + "\n" + status_css + "\n" + mobile_css
    require("env(safe-area-inset-top)" in combined and "env(safe-area-inset-bottom)" in combined, "mobile safe-area rules are present")
    require("100dvh" in combined, "dynamic viewport height is used for fullscreen mobile surfaces")
    require("pulse-status-page-layout" in status_css and "pulse-status2-composer" in status_css, "Status mobile editor has immersive controls")
    require("pulseStatus2Media" in source and "capture" not in source[source.find("pulseStatus2Media") : source.find("pulseStatus2Media") + 320], "Create Status upload does not force camera capture")
    require("pulse_environment_engine.js" in source and "prefers-reduced-motion" in env_js, "ambient environment engine is loaded and respects reduced motion")
    require(
        "input[type=file],.pulse-native-file-input" in source and "opacity:0!important" in source.replace(" ", ""),
        "homepage raw Choose File controls are globally hidden behind custom triggers",
    )
    require(
        '"photo"' in source
        and '"text"' in source
        and "Camera" in source
        and '"camera"' in source
        and "renderStatusMediaPreview" in source,
        "Status creator and camera actions are real, separated controls",
    )
    print("mobile experience audit ok")


if __name__ == "__main__":
    main()
