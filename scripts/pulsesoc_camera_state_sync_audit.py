#!/usr/bin/env python3
"""Audit PulseSoc call camera state synchronization safeguards."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
REPORT = ROOT / "reports" / "pulsesoc_camera_state_sync_fix.md"


def require(name: str, passed: bool) -> bool:
    print(("PASS" if passed else "FAIL") + f": {name}")
    return bool(passed)


def main() -> int:
    call_js = CALL_JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    report = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""

    checks = [
        require("LiveKit video publication helpers exist", "videoPublicationsFor" in call_js and "publicationVideoIsLive" in call_js),
        require("local camera truth derives from tracks/publications", "function localCameraIsLive" in call_js and "state.mutedVideo = !isLive" in call_js),
        require("remote camera truth derives from tracks/publications", "function remoteCameraIsLive" in call_js and "requireSubscribed" in call_js),
        require("camera-off fallback refuses live remote video", "remoteCameraIsLive()) return" in call_js),
        require("track mute/unmute events resync camera surfaces", "TrackMuted" in call_js and "TrackUnmuted" in call_js and "syncCameraSurfaces" in call_js),
        require("local fallback clears stale video when camera is off", "clearVideoElement(local)" in call_js),
        require("camera button state is rendered from actual camera truth", "renderCameraButtonState" in call_js and "localCameraIsLive()" in call_js),
        require("camera-off placeholder uses avatar/orb markup", "renderCameraOffFallback" in call_js and "pulsesoc-call-camera-orb" in call_js),
        require("camera-off placeholder CSS avoids black box", ".pulsesoc-call-camera-off" in css and "pulseCameraOffRing" in css),
        require("PiP fallback selector is direct-child scoped", ".pulsesoc-call-local-wrap > span" in css),
        require("cache bust references camera-state-sync", "camera-state-sync-v4-20260703" in template),
        require("report exists", "Camera State Synchronization" in report),
    ]
    passed = sum(1 for item in checks if item)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
