#!/usr/bin/env python3
"""Audit the PulseSoc native Verification Center foundation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "mobile-native/src/api/verification.ts",
    "mobile-native/src/screens/VerificationCenterScreen.tsx",
    "mobile-native/src/navigation/AppNavigator.tsx",
    "mobile-native/src/navigation/linking.ts",
    "mobile-native/src/navigation/notificationRouting.ts",
    "mobile-native/src/navigation/types.ts",
    "mobile-native/src/screens/SettingsScreen.tsx",
    "mobile-native/src/screens/ProfileScreen.tsx",
    "mobile-native/src/screens/PremiumScreen.tsx",
    "mobile-native/src/screens/TrustSafetyScreen.tsx",
    "reports/pulsesoc_native_verification_progress.md",
    "reports/pulsesoc_native_progress.md",
]

REQUIRED_SNIPPETS = {
    "mobile-native/src/api/verification.ts": [
        "/api/dashboard/account/state",
        "/api/dashboard/account/verification/request",
        "/api/dashboard/account/verification/appeal",
        "/api/dashboard/account/verification/document",
        "/api/pulse/profile/me",
        "/api/premium/status",
        "DocumentPicker.getDocumentAsync",
        "readJsonCache",
        "writeJsonCache",
    ],
    "mobile-native/src/screens/VerificationCenterScreen.tsx": [
        "Verification Center",
        "Badge preview",
        "Verification checklist",
        "Choose verification path",
        "Private document handoff",
        "Submit appeal",
        "Open protected web verification",
    ],
    "mobile-native/src/navigation/AppNavigator.tsx": [
        "VerificationCenterScreen",
        "VerificationCenter",
        "VerificationWebCenter",
    ],
    "mobile-native/src/navigation/linking.ts": [
        'path: "pulse/verification/:track?"',
        'path: "dashboard/account/verification"',
    ],
    "mobile-native/src/navigation/notificationRouting.ts": [
        "verificationRouteTarget",
        'target.startsWith("/dashboard/account/verification")',
        'target.startsWith("/pulse/verification")',
    ],
    "mobile-native/src/screens/SettingsScreen.tsx": [
        "Verification Center",
        'navigation.navigate("VerificationCenter"',
    ],
    "mobile-native/src/screens/ProfileScreen.tsx": [
        "Open Verification Center",
        "Verification:",
    ],
    "mobile-native/src/screens/PremiumScreen.tsx": [
        "Open Verification Center",
    ],
    "mobile-native/src/screens/TrustSafetyScreen.tsx": [
        "Verification",
        'navigation.navigate("VerificationCenter"',
    ],
    "reports/pulsesoc_native_verification_progress.md": [
        "Reuse-First Inventory",
        "Not Rebuilt Natively",
        "Device/Provider Limitations",
    ],
    "reports/pulsesoc_native_progress.md": [
        "Native Verification Center + Badge/Identity Verification foundation",
    ],
}

FORBIDDEN_USER_FACING = [
    "LogiNexus",
]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def main() -> int:
    for path in REQUIRED_FILES:
        read(path)

    for path, snippets in REQUIRED_SNIPPETS.items():
        content = read(path)
        missing = [snippet for snippet in snippets if snippet not in content]
        if missing:
            raise AssertionError(f"{path} missing snippets: {missing}")

    for path in ["mobile-native/src/screens/VerificationCenterScreen.tsx", "mobile-native/src/api/verification.ts"]:
        content = read(path)
        for token in FORBIDDEN_USER_FACING:
            if token in content:
                raise AssertionError(f"{path} exposes internal-only term: {token}")

    print("PulseSoc native Verification Center audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
