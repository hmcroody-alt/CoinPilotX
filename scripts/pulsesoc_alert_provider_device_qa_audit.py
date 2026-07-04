#!/usr/bin/env python3
"""Audit the PulseSoc Alert Management provider/device QA setup report."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    file_path = ROOT / path
    if not file_path.exists():
        fail(f"missing required file: {path}")
    return file_path.read_text(encoding="utf-8")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def require_terms(label: str, text: str, terms: list[str]) -> None:
    for term in terms:
        require(term in text, f"{label} missing required term: {term}")


def main() -> int:
    report = read("reports/pulsesoc_alert_provider_device_qa_setup.md")
    progress = read("reports/pulsesoc_native_progress.md")
    app_json = json.loads(read("mobile-native/app.json"))
    eas_json = read("mobile-native/eas.json")
    push_ts = read("mobile-native/src/api/push.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    alert_engine = read("services/alert_engine.py")
    push_service = read("services/push_service.py")
    native_push_readiness = read("services/native_push_readiness.py")
    env_example = read(".env.example")

    require_terms(
        "provider/device QA report",
        report,
        [
            "# PulseSoc Alert Provider + Device QA Setup",
            "No new major user-facing feature was built",
            "Production WebView paths were not modified",
            "APNs Readiness",
            "FCM Readiness",
            "Expo Push Readiness",
            "SMS Readiness",
            "Email Readiness",
            "Telegram Readiness",
            "Notification Tap Deep Links",
            "Lock-Screen Behavior Plan",
            "Provider Failure States",
            "Provider Success States",
            "Logs Needed For Debugging",
            "Physical Device Alert Test Plan",
            "What Cannot Be Verified Yet",
            "com.pulsesoc.nativeapp",
            "com.pulsesoc.app",
            "channelId: \"alerts\"",
            "push_trace_id",
            "notification_delivery_logs",
            "alert_delivery_jobs",
            "push_delivery_jobs",
            "expo_push_tickets",
        ],
    )

    expo = app_json.get("expo") or {}
    require(expo.get("scheme") == "pulsesoc", "mobile-native app keeps pulsesoc custom scheme")
    require((expo.get("ios") or {}).get("bundleIdentifier") == "com.pulsesoc.nativeapp", "parallel native iOS bundle id is documented")
    require((expo.get("android") or {}).get("package") == "com.pulsesoc.nativeapp", "parallel native Android package is documented")
    require("development" in eas_json and "development-simulator" in eas_json, "EAS development profiles remain available")

    require("Notifications.getExpoPushTokenAsync" in push_ts, "native push registration requests Expo token")
    require("/api/push/subscribe" in push_ts, "native push registration reuses existing backend subscribe route")
    require("setNotificationChannelAsync(\"alerts\"" in push_ts, "native creates Android alerts notification channel")
    require("EXPO_PROJECT_ID" in push_ts, "native push registration supports Expo project id")

    require("/pulse/alerts" in notification_routing, "notification routing handles native alert links")
    require("/dashboard/crypto/alerts" in notification_routing, "notification routing handles crypto alert links")
    require("pulsesoc://" in linking, "linking config supports pulsesoc custom scheme")
    require("pulse/alerts/:alertId?" in linking, "linking config supports alert detail path")

    require("def channel_readiness" in alert_engine, "alert engine exposes channel readiness")
    require("def test_delivery_channel" in alert_engine, "alert engine exposes channel test endpoint behavior")
    require("def send_test_alert" in alert_engine, "alert engine exposes alert test behavior")
    require("dispatch_alert_event" in alert_engine and "notification_delivery_logs" in alert_engine, "alert dispatch logs delivery state")
    require("/dashboard/crypto/alerts?alert_id=" in alert_engine, "alert dispatch uses existing crypto alert deep link")

    require("https://exp.host/--/api/v2/push/send" in push_service, "push service supports Expo provider path")
    require("expo_push_tickets" in push_service, "push service stores Expo tickets")
    require("DeviceNotRegistered" in push_service, "push service handles invalid Expo tokens")
    require("push_trace_id" in push_service, "push service emits trace ids")

    require("EXPECTED_APNS_BUNDLE_ID = \"com.pulsesoc.app\"" in native_push_readiness, "current backend APNs readiness still protects production app id")
    require("APNS_BUNDLE_ID" in env_example and "FCM_PROJECT_ID" in env_example, "runtime APNs/FCM env vars are documented")
    require("BREVO_SMS_API_KEY" in env_example and "BREVO_SMS_SENDER" in env_example, "runtime SMS env vars are documented")

    require("Alert Provider + Device QA Setup" in progress, "progress report includes provider/device QA setup")
    require("provider/device QA setup" in progress, "progress report recommends provider/device QA next")

    forbidden = [
        ROOT / "templates" / "index.html",
        ROOT / "templates" / "account.html",
        ROOT / "static" / "js" / "pulse_home_core.js",
        ROOT / "mobile" / "pulse-react-native" / "App.tsx",
    ]
    for path in forbidden:
        if path.exists() and "PulseSoc Alert Provider + Device QA Setup" in path.read_text(encoding="utf-8", errors="ignore"):
            fail(f"provider/device QA report leaked into production WebView file: {path.relative_to(ROOT)}")

    print("PulseSoc alert provider/device QA setup audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
