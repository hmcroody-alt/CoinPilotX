#!/usr/bin/env python3
"""Audit mobile-first Pulse homepage, Pulse Waves, safe-area, and non-decorative controls."""

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
    require("pulse-status-mode-picker" in status_css and "pulse-status-bottom-bar" in status_css, "Pulse Waves mobile launcher has immersive controls")
    require("pulseStatusMedia" in source and "capture" not in source[source.find("pulseStatusMedia") : source.find("pulseStatusMedia") + 320], "Photo Wave upload does not force camera capture")
    require("pulse_environment_engine.js" in source and "prefers-reduced-motion" in env_js, "ambient environment engine is loaded and respects reduced motion")
    require(
        "input[type=file],.pulse-native-file-input" in source and "opacity:0!important" in source.replace(" ", ""),
        "homepage raw Choose File controls are globally hidden behind custom triggers",
    )
    require("data-status-start='text'" in source and "data-status-start='upload'" in source and "routeStatusIntent" in source, "Pulse Wave creation has the strict Text/Photo choices")
    require(source.count("data-status-start=") == 2, "mobile Wave launcher exposes only two primary choices")
    require("Add Music" in source and "Add Voice Note" in source and ".pulse-wave-secondary-controls" in status_css, "secondary Wave tools stay subtle on mobile")
    require("Voice Wave" not in source and "Mood Wave" not in source and "Live Wave" not in source and "AI Wave" not in source, "old complex Wave actions are not visible")
    require("pulse-wave-step" in source and ".pulse-wave-step" in status_css, "mobile Wave flow shows step progress")
    require("pulse-wave-text-canvas" in source and ".pulse-wave-text-canvas" in status_css, "mobile Text Wave uses immersive writing canvas")
    require("PulseWaveComponents" in source, "mobile Wave flow uses native dynamic components")
    require("pulse-wave-preview-live" in source and ".pulse-wave-preview-live" in status_css, "mobile Photo Wave uses real selected media preview")
    require("Just now · Public" not in source and "Preview your Wave" not in source, "mobile Wave preview does not render fake mockup card copy")
    require("pulseWaveAtmosphere" in status_css and "pulseWaveFloat" in status_css, "mobile Wave atmosphere uses lightweight motion")
    print("mobile experience audit ok")


if __name__ == "__main__":
    main()
