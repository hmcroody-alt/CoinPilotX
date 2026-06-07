#!/usr/bin/env python3
"""Audit Pulse native App Store and Play Store readiness scaffolding."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile" / "pulse-react-native"


def read(path):
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing {path}")
    return target.read_text(encoding="utf-8")


def main():
    failures = []
    app = json.loads(read("mobile/pulse-react-native/app.json"))["expo"]
    eas = json.loads(read("mobile/pulse-react-native/eas.json"))

    required_files = [
        "mobile/pulse-react-native/assets/icon.png",
        "mobile/pulse-react-native/assets/adaptive-icon.png",
        "mobile/pulse-react-native/assets/splash.png",
        "mobile/pulse-react-native/assets/notification-icon.png",
        "mobile/pulse-react-native/eas.json",
        "mobile/pulse-react-native/credentials/.gitignore",
        "mobile/pulse-react-native/store-metadata/en-US/app-store.md",
        "mobile/pulse-react-native/store-metadata/en-US/play-store.md",
        "mobile/pulse-react-native/store-metadata/data-safety.md",
        "mobile/pulse-react-native/store-metadata/moderation.md",
        "mobile/pulse-react-native/store-metadata/premium-compliance.md",
        "reports/pulse_app_store_play_store_readiness.md",
    ]
    for path in required_files:
        if not (ROOT / path).exists():
            failures.append(f"Missing required file: {path}")

    checks = {
        "app name": app.get("name") == "Pulse",
        "scheme": app.get("scheme") == "pulse",
        "icon configured": app.get("icon") == "./assets/icon.png",
        "splash configured": (app.get("splash") or {}).get("image") == "./assets/splash.png",
        "ios bundle id": (app.get("ios") or {}).get("bundleIdentifier") == "com.pulsesoc.app",
        "android package": (app.get("android") or {}).get("package") == "com.pulsesoc.app",
        "ios permissions": all(key in json.dumps(app.get("ios") or {}) for key in ["NSCameraUsageDescription", "NSMicrophoneUsageDescription", "NSPhotoLibraryUsageDescription"]),
        "ios associated domains": "applinks:pulsesoc.com" in json.dumps(app.get("ios") or {}),
        "android app links": "pulsesoc.com" in json.dumps(app.get("android") or {}),
        "android notification permission": "POST_NOTIFICATIONS" in json.dumps(app.get("android") or {}),
        "expo notifications plugin": "expo-notifications" in json.dumps(app.get("plugins") or []),
        "eas production build": "production" in (eas.get("build") or {}),
        "eas submit placeholders": "REPLACE_WITH_APP_STORE_CONNECT_APP_ID" in json.dumps(eas),
        "play service account ignored": (MOBILE / "credentials" / ".gitignore").exists(),
    }
    for label, ok in checks.items():
        if not ok:
            failures.append(f"Failed check: {label}")

    app_store = read("mobile/pulse-react-native/store-metadata/en-US/app-store.md")
    play_store = read("mobile/pulse-react-native/store-metadata/en-US/play-store.md")
    data_safety = read("mobile/pulse-react-native/store-metadata/data-safety.md")
    moderation = read("mobile/pulse-react-native/store-metadata/moderation.md")
    premium = read("mobile/pulse-react-native/store-metadata/premium-compliance.md")
    for label, text, tokens in [
        ("app store metadata", app_store, ["Privacy Policy URL", "Support URL", "Review Notes"]),
        ("play store metadata", play_store, ["Short Description", "Full Description", "Privacy Policy"]),
        ("data safety", data_safety, ["Data Types Used", "User Controls", "Submission Blockers"]),
        ("moderation", moderation, ["Report content", "Block or restrict users", "Launch Gate"]),
        ("premium compliance", premium, ["Store Risk", "in-app purchase", "Required Before Production Submission"]),
    ]:
        for token in tokens:
            if token not in text:
                failures.append(f"{label} missing token: {token}")

    if failures:
        print("Pulse store submission readiness audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("pulse store submission readiness audit ok")


if __name__ == "__main__":
    main()
