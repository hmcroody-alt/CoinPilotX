#!/usr/bin/env python3
"""Audit the parallel PulseSoc native app foundation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
BOT = ROOT / "bot.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_file(relative: str) -> str:
    path = ROOT / relative
    require(path.exists(), f"missing {relative}")
    return read(path)


def main() -> int:
    package = require_file("mobile-native/package.json")
    app = require_file("mobile-native/App.tsx")
    app_json = require_file("mobile-native/app.json")
    api = require_file("mobile-native/src/api/pulse.ts")
    auth = require_file("mobile-native/src/api/auth.ts")
    push = require_file("mobile-native/src/api/push.ts")
    nav = require_file("mobile-native/src/navigation/AppNavigator.tsx")
    report = require_file("reports/pulsesoc_native_app_api_contract.md")
    plan = require_file("reports/pulsesoc_native_app_migration_plan.md")
    bot = read(BOT)

    require(NATIVE.is_dir(), "native app must live in mobile-native/")
    require('"expo"' in package and '"react-native"' in package, "Expo React Native dependencies are declared")
    require("@react-navigation/native" in package and "@react-navigation/bottom-tabs" in package, "native navigation dependencies are declared")
    require("expo-notifications" in package and "expo-camera" in package and "expo-image-picker" in package, "native permission/media dependencies are declared")
    require("@livekit/react-native" in package and "livekit-client" in package, "native LiveKit SDK dependencies are declared")

    code_paths = [
        path
        for path in NATIVE.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx", ".js", ".json"}
        and path.name not in {"README.md"}
    ]
    combined_native = "\n".join(path.read_text(encoding="utf-8") for path in code_paths)
    require("react-native-webview" not in combined_native.lower(), "native foundation must not use WebView")
    require("WebView" not in combined_native, "native foundation must not render WebView")

    require("AuthNavigator" in app and "AppNavigator" in app, "app switches between auth and signed-in native navigation")
    require("createBottomTabNavigator" in nav and "createNativeStackNavigator" in nav, "native tabs and stack navigation are wired")
    require("expo-secure-store" in package and "SecureStore" in require_file("mobile-native/src/session/sessionStore.ts"), "session cookie uses secure native storage")
    require("Notifications.getExpoPushTokenAsync" in push and '"/api/push/subscribe"' in push, "native push registration posts to backend")

    expected_routes = [
        "/api/mobile/auth/session",
        "/api/mobile/auth/login",
        "/api/mobile/auth/register",
        "/api/mobile/auth/logout",
        "/api/push/subscribe",
        "/api/dashboard/mission-control",
        "/api/pulse/messages/conversations",
        "/api/pulse/messages/<int:conversation_id>",
        "/api/pulse/messages/<int:conversation_id>/send",
        "/api/pulse/assistant/chat",
        "/api/pulse/profile/me",
    ]
    for route in expected_routes:
        require(route in bot, f"backend route exists: {route}")

    client_routes = [
        "/api/mobile/auth/session",
        "/api/mobile/auth/login",
        "/api/mobile/auth/register",
        "/api/mobile/auth/logout",
    ]
    for route in client_routes:
        require(route in auth, f"auth client wires {route}")

    for route in [
        "/api/dashboard/mission-control",
        "/api/pulse/messages/conversations",
        "/api/pulse/messages/",
        "/api/pulse/assistant/chat",
        "/api/pulse/profile/me",
    ]:
        require(route in api, f"Pulse API client wires {route}")

    require("mobile-native/" in report and "mobile-native/" in plan, "reports document separate native track")
    require("Do not submit" in plan and "No-Submit Gates" in plan, "migration plan includes App Store no-submit gates")
    require("Current production shell remains" in report, "API contract documents current WebView app remains live")
    require("Phase 1" in report and "Phase 4" in plan, "reports cover requested phased roadmap")
    require("NSCameraUsageDescription" in app_json and "NSMicrophoneUsageDescription" in app_json, "native camera/mic permissions are declared")

    print("PulseSoc native app foundation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
