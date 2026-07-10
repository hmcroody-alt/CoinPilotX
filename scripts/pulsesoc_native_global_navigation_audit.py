#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    "global navigation primitive": ROOT / "mobile-native/src/navigation/GlobalNavigation.tsx",
    "app navigator": ROOT / "mobile-native/src/navigation/AppNavigator.tsx",
    "master drawer": ROOT / "mobile-native/src/components/MasterNavigationDrawer.tsx",
    "route dispatcher": ROOT / "mobile-native/src/navigation/nativeRouteActions.ts",
    "dashboard route dispatcher": ROOT / "mobile-native/src/navigation/dashboardRouting.ts",
    "home screen": ROOT / "mobile-native/src/screens/HomeScreen.tsx",
    "global navigation report": ROOT / "reports/pulsesoc_native_global_navigation_logi_nexus.md",
    "visible qa report": ROOT / "reports/pulsesoc_native_global_navigation_visible_qa.md",
    "screen inventory": ROOT / "reports/pulsesoc_logi_nexus_screen_inventory.md",
    "native progress report": ROOT / "reports/pulsesoc_native_progress.md",
}

CHECKS = [
    ("Global header export", "mobile-native/src/navigation/GlobalNavigation.tsx", "export function LogiNexusGlobalHeader"),
    ("Bottom navigation export", "mobile-native/src/navigation/GlobalNavigation.tsx", "export function LogiNexusBottomNavigation"),
    ("Primary Home tab", "mobile-native/src/navigation/GlobalNavigation.tsx", 'name: "Home"'),
    ("Primary Reels tab", "mobile-native/src/navigation/GlobalNavigation.tsx", 'name: "Reels"'),
    ("Primary Create tab", "mobile-native/src/navigation/GlobalNavigation.tsx", 'name: "Create"'),
    ("Primary Messages tab", "mobile-native/src/navigation/GlobalNavigation.tsx", 'name: "Messenger"'),
    ("Primary Profile tab", "mobile-native/src/navigation/GlobalNavigation.tsx", 'name: "Profile"'),
    ("Create opens Home composer", "mobile-native/src/navigation/GlobalNavigation.tsx", 'navigate("Home", { openComposer: true })'),
    ("Header drawer test id", "mobile-native/src/navigation/GlobalNavigation.tsx", "global-header-drawer"),
    ("Header activity badge", "mobile-native/src/navigation/GlobalNavigation.tsx", "badges?.activity"),
    ("Header message badge", "mobile-native/src/navigation/GlobalNavigation.tsx", "badges?.messages"),
    ("Bottom nav test id", "mobile-native/src/navigation/GlobalNavigation.tsx", "global-bottom-navigation"),
    ("Safe area support", "mobile-native/src/navigation/GlobalNavigation.tsx", "useSafeAreaInsets"),
    ("Header accessibility", "mobile-native/src/navigation/GlobalNavigation.tsx", "accessibilityLabel"),
    ("App uses global header", "mobile-native/src/navigation/AppNavigator.tsx", "LogiNexusGlobalHeader"),
    ("App uses global bottom nav", "mobile-native/src/navigation/AppNavigator.tsx", "LogiNexusBottomNavigation"),
    ("App uses drawer identity", "mobile-native/src/navigation/AppNavigator.tsx", "identity={identity}"),
    ("App starts sync", "mobile-native/src/navigation/AppNavigator.tsx", "startNativeEventSync"),
    ("App refreshes badge counts", "mobile-native/src/navigation/AppNavigator.tsx", "getNotificationBadgeCounts"),
    ("App fetches profile identity", "mobile-native/src/navigation/AppNavigator.tsx", "getMyProfile"),
    ("Global UNDX title", "mobile-native/src/navigation/AppNavigator.tsx", 'title: "UNDX"'),
    ("Master drawer identity", "mobile-native/src/components/MasterNavigationDrawer.tsx", "DrawerIdentity"),
    ("Master drawer identity test id", "mobile-native/src/components/MasterNavigationDrawer.tsx", "master-drawer-identity"),
    ("Home uses global header", "mobile-native/src/screens/HomeScreen.tsx", "LogiNexusGlobalHeader"),
    ("Native dispatcher minimal contract", "mobile-native/src/navigation/nativeRouteActions.ts", "navigate: (...args: any[]) => void"),
    ("Dashboard dispatcher minimal contract", "mobile-native/src/navigation/dashboardRouting.ts", "navigate: (...args: any[]) => void"),
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    for label, path in REQUIRED_FILES.items():
        if not path.exists():
            failures.append(f"missing {label}: {path.relative_to(ROOT)}")

    for label, rel, needle in CHECKS:
        try:
            content = read(rel)
        except FileNotFoundError:
            failures.append(f"{label}: {rel} missing")
            continue
        if needle not in content:
            failures.append(f"{label}: missing {needle!r} in {rel}")

    if failures:
        print("PulseSoc native global navigation audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native global navigation audit passed.")
    print("Checked shared header, bottom nav, badge wiring, drawer identity, route dispatchers, Home integration, and reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
