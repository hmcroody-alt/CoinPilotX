#!/usr/bin/env python3
"""Audit the PulseSoc LogiNexus shared layout foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    ("existing screen module evolved", "mobile-native/src/components/Screen.tsx", "export function Screen"),
    ("screen shell primitive", "mobile-native/src/components/Screen.tsx", "export function LogiNexusScreenShell"),
    ("scroll container primitive", "mobile-native/src/components/Screen.tsx", "export function LogiNexusScrollContainer"),
    ("section primitive", "mobile-native/src/components/Screen.tsx", "export function LogiNexusSection"),
    ("state panel primitive", "mobile-native/src/components/Screen.tsx", "export function LogiNexusStatePanel"),
    ("responsive columns primitive", "mobile-native/src/components/Screen.tsx", "export function LogiNexusResponsiveColumns"),
    ("safe area support", "mobile-native/src/components/Screen.tsx", "useSafeAreaInsets"),
    ("responsive width support", "mobile-native/src/components/Screen.tsx", "useWindowDimensions"),
    ("keyboard persistence", "mobile-native/src/components/Screen.tsx", 'keyboardShouldPersistTaps="handled"'),
    ("dashboard state panel", "mobile-native/src/screens/UserDashboardScreen.tsx", "LogiNexusStatePanel"),
    ("messenger state panel", "mobile-native/src/screens/MessengerScreen.tsx", "LogiNexusStatePanel"),
    ("profile screen shell", "mobile-native/src/screens/ProfileScreen.tsx", "LogiNexusScreenShell"),
    ("post detail screen shell", "mobile-native/src/screens/PostDetailScreen.tsx", "LogiNexusScreenShell"),
    ("layout system report", "reports/pulsesoc_logi_nexus_layout_system.md", "Shared Screen Layout System"),
    ("screen shells report", "reports/pulsesoc_logi_nexus_screen_shells.md", "Screen Shells"),
    ("responsive report", "reports/pulsesoc_logi_nexus_responsive_layout.md", "Responsive Layout"),
    ("safe area report", "reports/pulsesoc_logi_nexus_safe_area_review.md", "Safe Area"),
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

    forbidden_pairs = [
        ("mobile-native/src/screens/PostDetailScreen.tsx", "ActivityIndicator"),
        ("mobile-native/src/screens/ProfileScreen.tsx", "ActivityIndicator"),
        ("mobile-native/src/screens/MessengerScreen.tsx", "ActivityIndicator"),
        ("mobile-native/src/screens/UserDashboardScreen.tsx", "ActivityIndicator"),
    ]
    for rel_path, needle in forbidden_pairs:
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        if needle in text:
            failures.append(f"{rel_path} still owns full-screen {needle} state")

    if failures:
        print("PulseSoc LogiNexus layout system audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc LogiNexus layout system audit passed")
    print("- shared shell, scroll, section, state, and responsive primitives are present")
    print("- representative Dashboard, Messenger, Profile, and Post Detail states use shared layout primitives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
