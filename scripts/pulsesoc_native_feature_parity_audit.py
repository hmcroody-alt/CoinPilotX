#!/usr/bin/env python3
"""Audit the PulseSoc native feature parity and QA readiness report."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_all(source: str, tokens: list[str], label: str, failures: list[str]) -> None:
    for token in tokens:
        require(token in source, f"{label} missing {token!r}", failures)


def main() -> int:
    failures: list[str] = []

    report = read("reports/pulsesoc_native_feature_parity_qa_readiness.md")
    progress = read("reports/pulsesoc_native_progress.md")
    package = read("mobile-native/package.json")
    app_json = read("mobile-native/app.json")
    nav_types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")

    required_areas = [
        "Auth/session",
        "Messenger",
        "Notifications",
        "Home Feed",
        "Post Detail",
        "Feed Composer",
        "Profile",
        "Reels",
        "Status Viewer",
        "Status Creator",
        "Media Upload",
        "Media Viewer",
        "Marketplace",
        "Search/Discovery",
        "Saved/Collections",
        "Groups/Communities/Rooms",
        "Live Viewer",
        "Premium/Entitlements",
        "Creator Studio",
        "Growth Center",
        "Intelligence/Alerts",
        "Settings",
        "Deep links",
        "Push notifications",
        "Offline/cache behavior",
        "Real-device readiness",
    ]
    require_all(report, required_areas, "parity matrix", failures)
    require_all(
        report,
        [
            "native status",
            "Web parity level",
            "Reusable backend/API coverage",
            "Remaining gaps",
            "Device-only QA needed",
            "Risk",
            "Fix order",
            "Expo web browser QA is blocked",
            "react-native-web@~0.19.10",
            "react-dom@18.2.0",
            "@expo/metro-runtime@~3.2.3",
            "adb is not available",
            "xcrun simctl",
            "Physical device QA flow is not established",
            "Recommended next action: device QA setup",
            "Do not add another major native feature"
        ],
        "QA readiness report",
        failures,
    )
    require_all(
        progress,
        [
            "Feature Parity + QA Readiness Report",
            "reports/pulsesoc_native_feature_parity_qa_readiness.md",
            "scripts/pulsesoc_native_feature_parity_audit.py",
            "Recommended next action: device QA setup"
        ],
        "master progress",
        failures,
    )
    require_all(
        nav_types + linking + routing,
        [
            "Chat",
            "PostDetail",
            "ReelDetail",
            "StatusDetail",
            "MarketplaceDetail",
            "GroupDetail",
            "LiveDetail",
            "Premium",
            "CreatorStudio",
            "GrowthCenter",
            "IntelligenceCenter",
            "NotificationPreferences",
            'normalized.startsWith("/dashboard/crypto/alerts")'
        ],
        "routing coverage evidence",
        failures,
    )
    require('"expo"' in package and '"react-native"' in package, "mobile-native package is Expo/React Native", failures)
    require('"scheme": "pulsesoc"' in app_json, "Expo app declares pulsesoc scheme", failures)
    require("react-native-web" not in package, "Report correctly treats Expo web as unavailable unless dependencies are added", failures)

    production_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "mobile/pulse-react-native/App.tsx",
    ]
    for path in production_paths:
        source = read(path)
        require("pulsesoc_native_feature_parity" not in source, f"{path} unexpectedly references parity audit artifacts", failures)

    if failures:
        print("PulseSoc native feature parity audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native feature parity audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
