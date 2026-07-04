#!/usr/bin/env python3
"""Audit PulseSoc native QA browser report and web boot hardening."""

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

    report = read("reports/pulsesoc_native_qa_browser_report.md")
    linking = read("mobile-native/src/navigation/linking.ts")
    login_screenshot = ROOT / "reports/screenshots/pulsesoc_native_qa_browser_login_20260704.png"
    mobile_screenshot = ROOT / "reports/screenshots/pulsesoc_native_qa_browser_login_mobile_20260704.png"

    require_all(
        report,
        [
            "PulseSoc Native QA Browser Report",
            "built-in QA browser",
            "Did not use Chrome Incognito",
            "HTTP/1.1 200 OK",
            "Duplicate Reels Linking Pattern",
            "Found conflicting screens with the same pattern",
            "App boot: passed after the Reels linking fix",
            "Login screen: passed",
            "Signup navigation: passed",
            "Home Feed",
            "Messenger",
            "Notifications",
            "Profile",
            "Reels",
            "Status",
            "Marketplace",
            "Search",
            "Saved",
            "Groups",
            "Live",
            "Premium",
            "Creator",
            "Growth",
            "Intelligence",
            "No provided QA login/session",
            "Native-Only Features Not Testable In Web QA",
            "Recommended Next Step",
        ],
        "QA browser report",
        failures,
    )
    require("Reels: \"pulse/reels\"" in linking, "Tabs Reels route should keep pulse/reels deep link", failures)
    require('Reels: {\n        path: "pulse/reels"' not in linking, "Root Reels route must not duplicate pulse/reels deep link", failures)
    require('path: "pulse/reels/:reelId"' in linking, "Reel detail route should preserve pulse/reels/:reelId", failures)
    require(login_screenshot.exists() and login_screenshot.stat().st_size > 1000, "desktop login screenshot missing or empty", failures)
    require(mobile_screenshot.exists() and mobile_screenshot.stat().st_size > 1000, "mobile login screenshot missing or empty", failures)

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "mobile/pulse-react-native/App.tsx",
    ]
    for path in forbidden_paths:
        source = read(path)
        require("pulsesoc_native_qa_browser_report" not in source, f"{path} unexpectedly references native QA browser report", failures)

    if failures:
        print("PulseSoc native QA browser audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native QA browser audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
