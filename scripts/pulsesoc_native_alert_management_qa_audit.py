#!/usr/bin/env python3
"""Audit the PulseSoc native Alert Management QA hardening pass."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "screen": ROOT / "mobile-native" / "src" / "screens" / "AlertManagementScreen.tsx",
    "report": ROOT / "reports" / "pulsesoc_native_alert_management_qa_hardening.md",
    "progress": ROOT / "reports" / "pulsesoc_native_progress.md",
    "screenshot": ROOT / "reports" / "screenshots" / "pulsesoc_native_alert_management_qa_hardening_20260704.png",
}


SCREEN_TERMS = [
    "validateAlertForm",
    "Add an asset symbol before saving the alert.",
    "Use a numeric target value.",
    "Target value must be greater than zero.",
    "Target value is too large for a safe alert threshold.",
    "Choose at least one delivery channel.",
    "pendingDeleteId",
    "Confirm delete",
    "Delete canceled.",
    "Showing the newest 12 of",
    "selectedIdRef",
    "setNotice(result.message || (editingId ? \"Alert updated.\" : \"Alert created.\"))",
    "setNotice(result.message || `${action} complete.`)",
]


REPORT_TERMS = [
    "# PulseSoc Native Alert Management QA Hardening",
    "No new major feature was built",
    "Production WebView paths were not modified",
    "Built-in QA browser only",
    "Browser-Verified Results",
    "Success Notices Were Cleared After Refresh",
    "Selected Alert Triggered An Unnecessary Initial Reload",
    "Device/Provider Items Not Verified",
    "Next highest-value action: Native alert fixture hardening plus provider/device QA setup",
]


PROGRESS_TERMS = [
    "Native Alert Management QA Hardening",
    "success notices",
    "inline delete confirmation",
    "provider/device QA setup",
]


FORBIDDEN_NATIVE_LOGIC = [
    "evaluate_alert_rule(",
    "dispatch_alert_event(",
    "send_user_alert(",
    "notification_delivery_logs.insert",
    "buy/sell/hold",
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
    screen = read("screen")
    report = read("report")
    progress = read("progress")
    screenshot = FILES["screenshot"]

    require_terms("alert management screen", screen, SCREEN_TERMS)
    require_terms("hardening report", report, REPORT_TERMS)
    require_terms("progress report", progress, PROGRESS_TERMS)

    if not screenshot.exists():
        fail(f"missing QA hardening screenshot: {screenshot.relative_to(ROOT)}")
    if screenshot.stat().st_size < 1000:
        fail(f"QA hardening screenshot is unexpectedly small: {screenshot.relative_to(ROOT)}")

    for term in FORBIDDEN_NATIVE_LOGIC:
        if term in screen:
            fail(f"native screen must not duplicate backend alert logic: {term}")

    production_webview_files = [
        ROOT / "templates" / "index.html",
        ROOT / "templates" / "account.html",
        ROOT / "templates" / "pulsesoc_intelligence_center.html",
        ROOT / "static" / "js" / "pulse_home_core.js",
        ROOT / "mobile" / "pulse-react-native" / "App.tsx",
    ]
    for path in production_webview_files:
        if path.exists() and "validateAlertForm" in path.read_text(encoding="utf-8", errors="ignore"):
            fail(f"alert QA hardening leaked into production WebView file: {path.relative_to(ROOT)}")

    print("PulseSoc native alert management QA hardening audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
