#!/usr/bin/env python3
"""Audit the PulseSoc native short authenticated QA browser sweep artifacts."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pulsesoc_native_short_qa_browser_sweep.md"
PROGRESS = ROOT / "reports" / "pulsesoc_native_progress.md"
LOGIN_SCREEN = ROOT / "mobile-native" / "src" / "screens" / "LoginScreen.tsx"
SETTINGS_SCREEN = ROOT / "mobile-native" / "src" / "screens" / "SettingsScreen.tsx"
SCREENSHOTS = [
    ROOT / "reports" / "screenshots" / "pulsesoc_native_short_qa_home_20260704.png",
    ROOT / "reports" / "screenshots" / "pulsesoc_native_short_qa_intelligence_20260704.png",
    ROOT / "reports" / "screenshots" / "pulsesoc_native_short_qa_settings_20260704.png",
]


REQUIRED_REPORT_TERMS = [
    "# PulseSoc Native Short Authenticated QA Browser Sweep",
    "built-in QA browser only",
    "HTTP/1.1 200 OK",
    "Login | Verified",
    "Session restore | Verified",
    "Logout | Verified after fix",
    "Re-login after logout | Verified after fix",
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
    "Live Viewer",
    "Premium",
    "Creator Studio",
    "Growth Center",
    "Intelligence/Alerts",
    "Settings",
    "Pulse AI",
    "Crypto Alert Deep Link",
    "Device-Only Items Not Verified",
    "Native Alert Management + Crypto/Market Alert CRUD remains the correct next feature",
]


FORBIDDEN_REPORT_TERMS = [
    "NativeShortQA!2026",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def require_file(path: Path) -> str:
    if not path.exists():
        fail(f"missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    report = require_file(REPORT)
    progress = require_file(PROGRESS)
    login = require_file(LOGIN_SCREEN)
    settings = require_file(SETTINGS_SCREEN)

    for term in REQUIRED_REPORT_TERMS:
        if term not in report:
            fail(f"report missing required term: {term}")

    for term in FORBIDDEN_REPORT_TERMS:
        if term in report or term in progress:
            fail("QA credentials must not be recorded in committed reports")

    for screenshot in SCREENSHOTS:
        if not screenshot.exists():
            fail(f"missing screenshot evidence: {screenshot.relative_to(ROOT)}")
        if screenshot.stat().st_size < 1000:
            fail(f"screenshot evidence is unexpectedly small: {screenshot.relative_to(ROOT)}")

    if 'accessibilityLabel="Email or username"' not in login:
        fail("LoginScreen email input must expose a QA-accessible label")
    if 'accessibilityLabel="Password"' not in login:
        fail("LoginScreen password input must expose a QA-accessible label")
    if 'accessibilityRole="button"' not in login:
        fail("LoginScreen Pressable controls must expose button roles")
    if settings.count('accessibilityRole="button"') < 7:
        fail("SettingsScreen controls must expose button roles for QA browser access")

    if "Short Authenticated QA Browser Sweep" not in progress:
        fail("progress report must include the completed short QA sweep")
    if "Native Alert Management + Crypto/Market Alert CRUD" not in progress:
        fail("progress report must preserve the recommended next feature")

    production_webview_files = [
        ROOT / "templates" / "index.html",
        ROOT / "templates" / "account.html",
        ROOT / "static" / "js" / "pulse_home_core.js",
        ROOT / "mobile" / "pulse-react-native" / "App.tsx",
    ]
    for path in production_webview_files:
        if path.exists() and "pulsesoc_native_short_qa_browser_sweep" in path.read_text(encoding="utf-8", errors="ignore"):
            fail(f"short QA report leaked into production WebView file: {path.relative_to(ROOT)}")

    print("PulseSoc native short QA browser sweep audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
