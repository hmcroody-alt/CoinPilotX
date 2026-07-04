#!/usr/bin/env python3
"""Audit the PulseSoc native Alert Management foundation."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "api": ROOT / "mobile-native" / "src" / "api" / "alerts.ts",
    "screen": ROOT / "mobile-native" / "src" / "screens" / "AlertManagementScreen.tsx",
    "types": ROOT / "mobile-native" / "src" / "navigation" / "types.ts",
    "navigator": ROOT / "mobile-native" / "src" / "navigation" / "AppNavigator.tsx",
    "linking": ROOT / "mobile-native" / "src" / "navigation" / "linking.ts",
    "notification_routing": ROOT / "mobile-native" / "src" / "navigation" / "notificationRouting.ts",
    "intelligence": ROOT / "mobile-native" / "src" / "screens" / "IntelligenceCenterScreen.tsx",
    "settings": ROOT / "mobile-native" / "src" / "screens" / "SettingsScreen.tsx",
    "report": ROOT / "reports" / "pulsesoc_native_alert_management_progress.md",
    "progress": ROOT / "reports" / "pulsesoc_native_progress.md",
    "screenshot": ROOT / "reports" / "screenshots" / "pulsesoc_native_alert_management_qa_20260704.png",
}


API_TERMS = [
    "/api/crypto/alerts",
    "/api/crypto/alerts/${encodeURIComponent(String(alertId))}",
    "/duplicate",
    "/history",
    "/api/alerts/${encodeURIComponent(String(alertId))}/pause",
    "/api/alerts/${encodeURIComponent(String(alertId))}/resume",
    "/api/alerts/${encodeURIComponent(String(alertId))}/delete",
    "/api/alerts/${encodeURIComponent(String(alertId))}/test",
    "/api/alerts/events",
    "/api/alerts/channel-readiness",
    "/api/alerts/test/${encodeURIComponent(channel)}",
]


SCREEN_TERMS = [
    "Alert Management",
    "Crypto and market alerts",
    "Alert detail",
    "Alert history",
    "Create alert",
    "Edit alert",
    "Delivery readiness",
    "ChannelToggle",
    "pauseAlert",
    "resumeAlert",
    "deleteAlert",
    "duplicateCryptoAlert",
    "testAlert",
    "testAlertChannel",
    "openIntelligenceWebFallback",
]


REPORT_TERMS = [
    "# PulseSoc Native Alert Management Progress",
    "Production WebView paths were not modified",
    "Server-owned logic reused",
    "Device-Only Items Not Verified",
    "focused QA hardening pass with seeded alert fixtures",
    "Built-in QA browser verification was completed",
    "Pause, resume, duplicate, test, and delete actions executed",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def read(name: str) -> str:
    path = FILES[name]
    if not path.exists():
        fail(f"missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def require_terms(label: str, text: str, terms: list[str]) -> None:
    for term in terms:
        if term not in text:
            fail(f"{label} missing required term: {term}")


def main() -> int:
    api = read("api")
    screen = read("screen")
    types = read("types")
    navigator = read("navigator")
    linking = read("linking")
    notification_routing = read("notification_routing")
    intelligence = read("intelligence")
    settings = read("settings")
    report = read("report")
    progress = read("progress")
    screenshot = FILES["screenshot"]

    require_terms("alerts api", api, API_TERMS)
    require_terms("alert screen", screen, SCREEN_TERMS)
    require_terms("report", report, REPORT_TERMS)

    if not screenshot.exists():
        fail(f"missing QA screenshot: {screenshot.relative_to(ROOT)}")
    if screenshot.stat().st_size < 1000:
        fail(f"QA screenshot is unexpectedly small: {screenshot.relative_to(ROOT)}")

    if "AlertManagement" not in types or "CryptoAlertManagement" not in types:
        fail("root navigation types must expose AlertManagement and CryptoAlertManagement")
    if "AlertManagementScreen" not in navigator:
        fail("AppNavigator must register AlertManagementScreen")
    if "pulse/alerts/:alertId?" not in linking or "dashboard/crypto/alerts" not in linking:
        fail("linking must include native alert and crypto alert paths")
    if 'navigationRef.navigate("AlertManagement"' not in notification_routing:
        fail("notification routing must route alert targets to AlertManagement")
    if 'navigation.navigate("AlertManagement"' not in intelligence:
        fail("Intelligence Center must link to native Alert Management")
    if 'navigation.navigate("AlertManagement"' not in settings:
        fail("Settings must link to native Alert Management")
    if "Native Alert Management + Crypto/Market Alert CRUD" not in progress:
        fail("progress report must include the completed/recommended alert management state")

    forbidden_native_logic = [
        "evaluate_alert_rule(",
        "dispatch_alert_event(",
        "send_user_alert(",
        "buy/sell/hold",
    ]
    combined_native = api + screen
    for term in forbidden_native_logic:
        if term in combined_native:
            fail(f"native code must not duplicate backend alert logic: {term}")

    production_webview_files = [
        ROOT / "templates" / "index.html",
        ROOT / "templates" / "account.html",
        ROOT / "static" / "js" / "pulse_home_core.js",
        ROOT / "mobile" / "pulse-react-native" / "App.tsx",
    ]
    for path in production_webview_files:
        if path.exists() and "AlertManagementScreen" in path.read_text(encoding="utf-8", errors="ignore"):
            fail(f"Alert Management leaked into production WebView file: {path.relative_to(ROOT)}")

    print("PulseSoc native alert management audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
