#!/usr/bin/env python3
"""Audit the native Create/Camera/Music/Live split.

This is intentionally source-level because the mission is mostly navigation
ownership and no-fake-flow enforcement. Browser/device QA still has to verify
camera permissions and physical capture behavior.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(checks: list[dict[str, object]], condition: bool, name: str, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(condition), "detail": detail})


def main() -> int:
    checks: list[dict[str, object]] = []

    types = read("mobile-native/src/navigation/types.ts")
    handoff = read("mobile-native/src/create/createComposerHandoff.ts")
    home_composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    home_screen = read("mobile-native/src/screens/HomeScreen.tsx")
    camera = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    reels = read("mobile-native/src/screens/ReelsScreen.tsx")
    music = read("mobile-native/src/screens/MusicScreen.tsx")
    camera_api = read("mobile-native/src/api/camera.ts")
    dashboard_routing = read("mobile-native/src/navigation/dashboardRouting.ts")
    report_path = ROOT / "reports/pulsesoc_native_create_camera_music_live_split_2026-07-19.md"
    report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""

    require(checks, 'composerMode?: "post" | "status" | "reel"' in types, "Home composer route supports Feed/Status/Reel modes")
    require(checks, "composerReturnNonce" in types, "Home route has camera return nonce")
    require(checks, "returnToComposer?: boolean" in types, "Camera route explicitly opts into composer return")
    require(checks, 'captureMode?: "photo" | "video" | "live"' in types, "Camera route supports Photo/Video/Live modes")
    require(checks, 'surface?: "post" | "status" | "reel"' in types, "Music route carries composer surface")

    require(checks, "saveCreateCameraCaptureResult" in handoff and "consumeCreateCameraCaptureResult" in handoff, "Create-camera persisted handoff exists")
    require(checks, "createComposerModeFromCameraTarget" in handoff, "Camera target maps to composer destination")

    for token in [
        '{ key: "post", label: "Feed"',
        '{ key: "status", label: "Status"',
        '{ key: "reel", label: "Reel"',
        "home-composer-gallery",
        "home-composer-camera",
        "Music ✓",
        "createStatus(statusPayload)",
        "consumeCreateCameraCaptureResult",
        "openCameraFromComposer",
    ]:
        require(checks, token in home_composer, f"Composer contains {token}")
    require(checks, "onOpenLive" not in home_composer and 'mode === "live"' not in home_composer, "Composer no longer has fake Live publish mode")
    require(checks, "returnToComposer: true" in home_screen, "Home opens Camera in composer-return mode")
    require(checks, "captureReturnNonce={captureReturnNonce}" in home_screen, "Home passes capture return nonce to composer")

    require(checks, 'screen: "Home", params: { openComposer: true, composerMode: "reel" }' in reels, "Reels top-right plus opens Create composer")
    require(checks, 'navigation.navigate("CameraStudio", { target: "reel"' not in reels, "Reels top-right plus no longer opens Camera directly")

    for token in [
        'type NativeCaptureMode = "photo" | "video" | "live"',
        'captureMode === "live"',
        "saveCreateCameraCaptureResult",
        "Publishing stays in the Create composer",
        "Open Live Studio",
        "Native Camera will not fake a broadcast",
        "{composerReturnMode ? null : (",
    ]:
        require(checks, token in camera, f"Camera contains {token}")
    require(checks, "const composerReturnMode = Boolean(route.params?.returnToComposer)" in camera, "Camera return mode is explicit, not accidental")

    require(checks, "selectPulseMusicForSurface(track, composerSurface)" in music, "Music selection uses existing native music API")
    require(checks, 'screen: "Home", params: { openComposer: true, composerMode: composerSurface }' in music, "Music returns selection to Create composer")
    require(checks, 'CameraMode = "photo" | "video" | "status" | "reel" | "live"' in camera_api, "Camera API type allows live mode")
    require(checks, 'captureMode: mode === "live" ? "live"' in dashboard_routing, "Dashboard camera routes preserve Live capture mode")

    for section in [
        "Navigation flow",
        "Create composer",
        "Camera capture",
        "Music reuse",
        "Live readiness",
        "QA evidence",
        "Known limitations",
    ]:
        require(checks, section in report, f"Report includes {section}")

    ok = all(check["ok"] for check in checks)
    print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
