#!/usr/bin/env python3
"""Audit PulseSoc video call camera truth and local preview synchronization."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
REPORT = ROOT / "reports" / "pulsesoc_call_camera_sync_fix.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    call_js = read(CALL_JS)
    css = read(CSS)
    routes = read(ROUTES)
    report = read(REPORT) if REPORT.exists() else ""

    require(checks, "LiveKit publication source helper exists", "function publicationSource" in call_js)
    require(checks, "camera publications are separated from generic video", "function isCameraPublication" in call_js)
    require(checks, "participant camera flag is respected", "participantCameraEnabled" in call_js and "isCameraEnabled" in call_js)
    require(checks, "local camera truth is derived from publications", "videoPublicationsFor(participant).filter(isCameraPublication)" in call_js)
    require(checks, "local camera truth does not trust hidden stale video", 'video.closest?.(".is-camera-off")' in call_js)
    require(checks, "camera toggle reads actual surface truth", "const turningOff = syncLocalCameraSurface();" in call_js)
    require(checks, "camera off asks LiveKit participant to disable camera", "setCameraEnabled?.(false)" in call_js)
    require(checks, "camera off unpublishes/stops video tracks", 'stopLocalTracks("video")' in call_js and "unpublishLocalTrack(track, true)" in call_js)
    require(checks, "local preview detach helper exists", "function detachLocalPreview" in call_js)
    require(checks, "local video element is cleared", "clearVideoElement(local)" in call_js and "video.srcObject = null" in call_js)
    require(checks, "local preview tracks live/off state", 'wrap.dataset.cameraState = isLive ? "live" : "off"' in call_js)
    require(checks, "camera button label is reality-based", "renderCameraButtonState" in call_js and "localCameraIsLive()" in call_js)
    require(checks, "camera on republish path remains", 'publishSingleLocalTrack("video")' in call_js and '"enable-video"' in call_js)
    require(checks, "backend video disable route exists", "/disable-video" in routes and "api_call_disable_video" in routes)
    require(checks, "backend video enable route exists", "/enable-video" in routes and "api_call_enable_video" in routes)
    require(checks, "camera-off CSS hides video node", ".pulsesoc-call-local-wrap.is-camera-off .pulsesoc-call-local" in css and "display: none !important" in css)
    require(checks, "camera-off CSS is opaque", ".pulsesoc-call-local-wrap.is-camera-off" in css and "background: #03101d" in css)
    require(checks, "local off placeholder does not use blur-through glass", "backdrop-filter: none" in css)
    require(checks, "report exists", "PulseSoc Video Call Camera Sync Fix" in report)
    require(checks, "no internal architecture name exposed", "LogiNexus" not in call_js and "LogiNexus" not in css)

    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": len(checks) - len(failed), "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
