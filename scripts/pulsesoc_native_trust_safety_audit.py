#!/usr/bin/env python3
"""Audit the PulseSoc native Trust/Safety foundation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "mobile-native/src/api/support.ts",
    "mobile-native/src/screens/TrustSafetyScreen.tsx",
    "mobile-native/src/navigation/AppNavigator.tsx",
    "mobile-native/src/navigation/linking.ts",
    "mobile-native/src/navigation/notificationRouting.ts",
    "mobile-native/src/navigation/types.ts",
    "mobile-native/src/screens/SettingsScreen.tsx",
    "reports/pulsesoc_native_trust_safety_progress.md",
    "reports/pulsesoc_native_progress.md",
]

REQUIRED_SNIPPETS = {
    "mobile-native/src/api/support.ts": [
        "/api/support/ticket",
        "/api/security/report",
        "/api/scam-shield/scan",
        "/api/pulse/report",
        "/api/pulse/block",
        "readJsonCache",
        "writeJsonCache",
    ],
    "mobile-native/src/screens/TrustSafetyScreen.tsx": [
        "Trust & Safety",
        "Support tickets",
        "Open support ticket",
        "Security report",
        "Scam Shield",
        "Scan risk",
        "openSupportWebFallback",
    ],
    "mobile-native/src/navigation/AppNavigator.tsx": [
        "TrustSafetyScreen",
        "TrustSafetySupport",
        "TrustCenter",
        "SecurityReport",
        "ScamShield",
    ],
    "mobile-native/src/navigation/linking.ts": [
        'path: "pulse/help"',
        'TrustSafetySupport: "support"',
        'TrustSafetyHelp: "help"',
        'TrustCenter: "trust-center"',
        'SecurityReport: "security"',
        'ScamShield: "scam-shield/:mode?"',
    ],
    "mobile-native/src/navigation/notificationRouting.ts": [
        "trustSafetyTarget",
        'target.startsWith("/pulse/help")',
        'target.startsWith("/support")',
        'target.startsWith("/security")',
        'target.startsWith("/scam-shield")',
    ],
    "mobile-native/src/screens/SettingsScreen.tsx": [
        "Trust and Safety",
        'navigation.navigate("TrustSafety"',
    ],
    "reports/pulsesoc_native_trust_safety_progress.md": [
        "Reuse-First Inventory",
        "QA Status",
        "Remaining Gaps",
    ],
    "reports/pulsesoc_native_progress.md": [
        "Native Trust, Safety & Support",
    ],
}


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

    print("PulseSoc native Trust/Safety audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
