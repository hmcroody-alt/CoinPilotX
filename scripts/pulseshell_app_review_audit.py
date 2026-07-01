#!/usr/bin/env python3
"""Audit PulseShell bridge and Apple Review readiness guardrails.

This is a static readiness gate. It verifies that the hybrid shell exposes a
safe bridge contract and that App Review supporting artifacts exist. It does
not replace real-device camera, push, Live, or App Store submission QA.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def require_all(text: str, tokens: list[str], label: str) -> None:
    for token in tokens:
        require(token in text, f"{label} contains {token}")


def main() -> int:
    app_tsx = read("mobile/pulse-react-native/App.tsx")
    app_json_text = read("mobile/pulse-react-native/app.json")
    static_bridge = read("static/js/pulseshell_bridge.js")
    environment_engine = read("static/js/pulse_environment_engine.js")
    media_renderer = read("static/js/pulse_media_renderer.js")
    home_os_css = read("static/css/pulse_home_os.css")
    bot = read("bot.py")
    account = read("templates/account.html")
    terms = read("templates/terms.html")
    privacy = read("templates/privacy.html")
    support = read("templates/support.html")
    comm_routes = read("pulse_communications_v2/routes.py")
    comm_service = read("pulse_communications_v2/service.py")
    report = read("reports/pulseshell_audit.md")
    review_notes = read("mobile/pulse-react-native/store-metadata/en-US/app-review-notes-pulseshell.md")

    app_config = json.loads(app_json_text)["expo"]
    ios = app_config.get("ios") or {}
    android = app_config.get("android") or {}
    info_plist = ios.get("infoPlist") or {}

    require_all(
        app_tsx,
        [
            "PULSESHELL_NATIVE_CALL",
            "PulseShellNativeResult",
            "PULSESHELL_SERVER_VALIDATED_ACTIONS",
            "window.PulseShell",
            "PULSESHELL_PERFORMANCE_MODE",
            "PulseShellReady",
            "getNativePushToken",
            "injectPushTokenRegistration",
            "ImagePicker.launchImageLibraryAsync",
            "callVisionCameraPermission",
            "requestCameraPermission",
            "requestMicrophonePermission",
            "Share.share",
            "Vibration.vibrate",
            "useSafeAreaInsets",
        ],
        "native PulseShell bridge",
    )

    require_all(
        static_bridge,
        [
            "window.PulseShell",
            "available: false",
            "PulseShell native",
            "reduced-motion",
            "low-end",
            "battery-saver",
            "PulseShellPerformanceModeChanged",
            "PulseShellPerformanceChanged",
            "PulseShellPerformance",
            "pulseshellPerformance",
            "mediaRootMargin: function (desktop, mobile, constrained)",
            "navigator.share",
        ],
        "browser PulseShell fallback",
    )

    require_all(
        environment_engine,
        [
            "pulseShellMode",
            "effectsReduced",
            "PulseShellPerformanceChanged",
            "PULSESHELL_PERFORMANCE_MODE",
            "activityInterval",
        ],
        "futuristic city performance governance",
    )
    require_all(
        media_renderer,
        [
            "pulseShellPerformanceMode",
            "pulseShellConstrainedMode",
            "mediaRootMargin",
            "constrained: true",
        ],
        "media renderer PulseShell performance controls",
    )
    require_all(
        home_os_css,
        [
            'html[data-pulseshell-performance="battery-saver"]',
            'html[data-pulseshell-performance="low-end"]',
            'html[data-pulseshell-performance="reduced-motion"]',
        ],
        "PulseShell performance CSS throttles atmospheric effects",
    )

    require("/static/js/pulseshell_bridge.js" in bot, "PulseSoc web shells include PulseShell fallback")
    require("NSCameraUsageDescription" in app_json_text, "iOS camera permission string exists")
    require("NSMicrophoneUsageDescription" in app_json_text, "iOS microphone permission string exists")
    require("NSPhotoLibraryUsageDescription" in app_json_text, "iOS photo library permission string exists")
    require("NSPhotoLibraryAddUsageDescription" in app_json_text, "iOS photo library add permission string exists")
    require("co-host" in info_plist.get("NSCameraUsageDescription", "").lower(), "camera permission explains co-host usage")
    require("co-host" in info_plist.get("NSMicrophoneUsageDescription", "").lower(), "microphone permission explains co-host usage")

    android_permissions = set(android.get("permissions") or [])
    for permission in ["CAMERA", "RECORD_AUDIO", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "POST_NOTIFICATIONS"]:
        require(permission in android_permissions, f"Android permission present: {permission}")
    require("READ_CONTACTS" not in android_permissions and "ACCESS_FINE_LOCATION" not in android_permissions, "Android manifest avoids unrelated sensitive permissions")

    require("https://pulsesoc.com/privacy" in privacy or "/privacy" in bot, "privacy policy is reachable")
    require("https://pulsesoc.com/terms" in terms or "/terms" in bot, "terms are reachable")
    require("support@pulsesoc.com" in privacy and "/support" in support, "support path and contact are documented")
    require("no-tolerance" in terms.lower() or "objectionable" in terms.lower(), "Terms include UGC/no-tolerance policy")
    require("Delete Account" in account and "/account/delete" in account and "Permanently Delete Account" in account, "account deletion path is visible")
    require("Report Profile" in bot and "Block User" in bot, "profile report/block actions remain visible")
    require("report_message" in comm_routes and "block_user" in comm_routes, "message report/block routes remain wired")
    require("comm_v2_reports" in comm_service and "comm_v2_blocks" in comm_service, "message report/block persistence remains wired")

    require_all(
        report,
        [
            "Current WebView limitations found",
            "PulseShell modules added or strengthened",
            "Unavailable modules return structured unavailable results",
            "Apple Review risks found",
            "Test accounts needed",
            "No fake native capability was added",
        ],
        "PulseShell audit report",
    )
    require_all(
        review_notes,
        [
            "PulseShell",
            "Live co-hosting can be tested",
            "Account A starts a Live",
            "Account B opens the Live",
            "Users can report content",
            "Delete my account",
            "Do not add admin credentials",
        ],
        "App Review notes template",
    )

    mobile_sources = "\n".join([app_tsx, static_bridge, app_json_text])
    secret_patterns = [
        r"LIVEKIT_API_SECRET",
        r"MUX_TOKEN_SECRET",
        r"STRIPE_SECRET",
        r"BREVO_API_KEY",
        r"APNS_PRIVATE_KEY",
        r"FCM_PRIVATE_KEY",
        r"DATABASE_URL",
        r"PRIVATE_STREAM_KEY",
    ]
    for pattern in secret_patterns:
        require(not re.search(pattern, mobile_sources), f"mobile/PulseShell source does not expose {pattern}")

    forbidden_fake_success = [
        "fake native success",
        "pretend native",
        "mock native success",
        "TODO: return success",
    ]
    lower_sources = mobile_sources.lower()
    for token in forbidden_fake_success:
        require(token not in lower_sources, f"PulseShell source avoids fake-success marker: {token}")

    print("pulseshell_app_review_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pulseshell_app_review_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
