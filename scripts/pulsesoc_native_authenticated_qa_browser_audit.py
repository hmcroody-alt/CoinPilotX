#!/usr/bin/env python3
"""Audit PulseSoc native authenticated QA browser report and scoped fixes."""

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

    report = read("reports/pulsesoc_native_authenticated_qa_browser_report.md")
    session_store = read("mobile-native/src/session/sessionStore.ts")
    pulse_api = read("mobile-native/src/api/pulseApi.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    intelligence = read("mobile-native/src/api/intelligence.ts")

    require_all(
        report,
        [
            "PulseSoc Native Authenticated QA Browser Report",
            "built-in QA browser",
            "Did not use Chrome Incognito",
            "HTTP/1.1 200 OK",
            "Login | Passed",
            "Session restore | Passed",
            "Logout | Passed",
            "Web Session Storage Was Native-Only",
            "Browser Cookie Header Was Forbidden",
            "Settings Deep Link Fell Back To Home",
            "Intelligence Cards Could Crash On Object Payloads",
            "/pulse/settings",
            "/dashboard/intelligence",
            "Native-Only Behavior Not Verified",
            "Recommended Next Action",
        ],
        "authenticated QA report",
        failures,
    )

    forbidden_report_tokens = ["NativeAuthQA!2026", "password `", "password:"]
    for token in forbidden_report_tokens:
        require(token not in report, f"report must not commit temporary QA password token {token!r}", failures)

    screenshots = [
        "reports/screenshots/pulsesoc_native_authenticated_qa_home_20260704.png",
        "reports/screenshots/pulsesoc_native_authenticated_qa_profile_20260704.png",
        "reports/screenshots/pulsesoc_native_authenticated_qa_intelligence_20260704.png",
        "reports/screenshots/pulsesoc_native_authenticated_qa_settings_20260704.png",
    ]
    for screenshot in screenshots:
        path = ROOT / screenshot
        require(path.exists() and path.stat().st_size > 1000, f"{screenshot} missing or empty", failures)

    require("@react-native-async-storage/async-storage" in session_store, "session store should use AsyncStorage for web QA", failures)
    require('Platform.OS === "web"' in session_store, "session store should branch for web", failures)
    require('Platform.OS !== "web"' in pulse_api, "pulseApi should avoid manual Cookie header on web", failures)
    require('credentials: "include"' in pulse_api, "pulseApi should keep browser-managed cookies enabled", failures)

    require('PulseAI: "pulse/ai"' in linking, "Pulse AI tab should have a stable native deep link", failures)
    require('Settings: "pulse/settings"' in linking, "Settings tab should have a stable native deep link", failures)

    require("Record<string, IntelligenceCard>" in intelligence, "intelligence cards type should allow backend object maps", failures)
    require("Object.entries(cards as Record<string, IntelligenceCard> || {})" in intelligence, "intelligence normalizer should convert object maps to arrays", failures)

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "mobile/pulse-react-native/App.tsx",
    ]
    for path in forbidden_paths:
        source = read(path)
        require(
            "pulsesoc_native_authenticated_qa_browser_report" not in source,
            f"{path} unexpectedly references authenticated native QA report",
            failures,
        )

    if failures:
        print("PulseSoc native authenticated QA browser audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native authenticated QA browser audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
