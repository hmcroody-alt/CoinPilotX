#!/usr/bin/env python3
"""Audit PulseSoc native device QA setup documentation and repo configuration."""

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

    report = read("reports/pulsesoc_native_device_qa_setup.md")
    package = read("mobile-native/package.json")
    lockfile = read("mobile-native/package-lock.json")
    app_json = read("mobile-native/app.json")
    eas = read("mobile-native/eas.json")
    config = read("mobile-native/src/api/config.ts")
    push = read("mobile-native/src/api/push.ts")

    require_all(
        report,
        [
            "Current State",
            "Remaining Blockers",
            "Required External Software",
            "Required Apple Setup",
            "Required Android Setup",
            "Required Expo Setup",
            "Required Push Configuration",
            "Required Certificates",
            "Required Provisioning",
            "Required Environment Variables",
            "Exact Commands To Start Testing",
            "Exact Commands To Build iOS",
            "Exact Commands To Build Android",
            "Exact Commands To Launch QA",
            "Is The Native App Now Ready For Real Device QA?",
            "Not yet on this machine",
            "react-native-web",
            "react-dom",
            "@expo/metro-runtime",
            "adb",
            "xcrun simctl",
            "Browser QA note",
            "http://localhost:8094",
            "EXPO_PUBLIC_EXPO_PROJECT_ID",
            "EXPO_PUBLIC_PULSE_API_BASE_URL",
        ],
        "device QA report",
        failures,
    )
    require_all(
        package,
        [
            '"start:qa"',
            '"web:qa"',
            '"ios:simulator"',
            '"android:emulator"',
            '"build:ios:development"',
            '"build:ios:simulator"',
            '"build:android:development"',
            '"react-native-web"',
            '"react-dom"',
            '"@expo/metro-runtime"',
        ],
        "package QA scripts/dependencies",
        failures,
    )
    require_all(
        lockfile,
        [
            '"react-native-web"',
            '"react-dom"',
            '"@expo/metro-runtime"',
        ],
        "lockfile web dependencies",
        failures,
    )
    require_all(
        app_json,
        [
            '"scheme": "pulsesoc"',
            '"bundleIdentifier": "com.pulsesoc.nativeapp"',
            '"package": "com.pulsesoc.nativeapp"',
            '"expo-notifications"',
            '"expo-camera"',
            '"expo-image-picker"',
            '"POST_NOTIFICATIONS"',
        ],
        "Expo app config",
        failures,
    )
    require_all(
        eas,
        [
            '"developmentClient": true',
            '"development-simulator"',
            '"simulator": true',
            '"preview"',
            '"production"',
        ],
        "EAS config",
        failures,
    )
    require_all(
        config + push,
        [
            "EXPO_PROJECT_ID",
            "EXPO_PUBLIC_EXPO_PROJECT_ID",
            "Constants.easConfig",
            "getExpoPushTokenAsync({ projectId: EXPO_PROJECT_ID })",
        ],
        "push project id support",
        failures,
    )

    forbidden_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "mobile/pulse-react-native/App.tsx",
    ]
    for path in forbidden_paths:
        source = read(path)
        require("pulsesoc_native_device_qa_setup" not in source, f"{path} unexpectedly references native device QA artifacts", failures)

    if failures:
        print("PulseSoc native device setup audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native device setup audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
