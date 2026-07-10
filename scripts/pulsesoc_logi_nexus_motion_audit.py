#!/usr/bin/env python3
"""Audit the shared PulseSoc LogiNexus motion foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    ("motion utility", "mobile-native/src/theme/logiNexusMotion.ts", "export const logiNexusMotion"),
    ("ambient pulse helper", "mobile-native/src/theme/logiNexusMotion.ts", "createLogiNexusAmbientPulse"),
    ("reduced motion hook", "mobile-native/src/theme/logiNexusMotion.ts", "useLogiNexusReducedMotion"),
    ("accessibility reduce motion", "mobile-native/src/theme/logiNexusMotion.ts", "AccessibilityInfo.isReduceMotionEnabled"),
    ("shared token reuse", "mobile-native/src/theme/logiNexusMotion.ts", "logiNexus.motion"),
    ("dashboard uses shared motion", "mobile-native/src/screens/UserDashboardScreen.tsx", "createLogiNexusAmbientPulse"),
    ("dashboard respects reduced motion", "mobile-native/src/screens/UserDashboardScreen.tsx", "useLogiNexusReducedMotion"),
    ("calls use shared motion", "mobile-native/src/calls/IncomingCallLayer.tsx", "createLogiNexusAmbientPulse"),
    ("calls respect reduced motion", "mobile-native/src/calls/IncomingCallLayer.tsx", "useLogiNexusReducedMotion"),
    ("motion report", "reports/pulsesoc_logi_nexus_motion_system.md", "Shared Motion System"),
]


def main() -> int:
    failures: list[str] = []
    for label, rel_path, needle in CHECKS:
        path = ROOT / rel_path
        if not path.exists():
            failures.append(f"Missing {label}: {rel_path}")
            continue
        if needle not in path.read_text(encoding="utf-8"):
            failures.append(f"{label} missing {needle!r}")

    if failures:
        print("PulseSoc LogiNexus motion audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc LogiNexus motion audit passed")
    print("- shared motion utility, ambient pulse helper, reduced-motion hook, and migrated surfaces are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
