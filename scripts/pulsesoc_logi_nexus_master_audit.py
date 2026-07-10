#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "design tokens": ROOT / "mobile-native/src/theme/logiNexus.ts",
    "components": ROOT / "mobile-native/src/components/LogiNexus.tsx",
    "home": ROOT / "mobile-native/src/screens/HomeScreen.tsx",
    "composer": ROOT / "mobile-native/src/components/HomePulseComposer.tsx",
    "post card": ROOT / "mobile-native/src/components/PostCard.tsx",
    "master drawer": ROOT / "mobile-native/src/components/MasterNavigationDrawer.tsx",
    "global navigation": ROOT / "mobile-native/src/navigation/GlobalNavigation.tsx",
    "navigation inventory": ROOT / "mobile-native/src/navigation/masterNavigation.ts",
    "route dispatcher": ROOT / "mobile-native/src/navigation/nativeRouteActions.ts",
    "master report": ROOT / "reports/pulsesoc_logi_nexus_master_transformation.md",
    "design report": ROOT / "reports/pulsesoc_logi_nexus_design_system.md",
    "home report": ROOT / "reports/pulsesoc_logi_nexus_home_progress.md",
    "drawer report": ROOT / "reports/pulsesoc_logi_nexus_master_navigation_drawer.md",
    "global navigation report": ROOT / "reports/pulsesoc_native_global_navigation_logi_nexus.md",
    "visible qa report": ROOT / "reports/pulsesoc_logi_nexus_visible_qa.md",
    "screen inventory": ROOT / "reports/pulsesoc_logi_nexus_screen_inventory.md",
}

CHECKS = [
    ("LogiNexus token export", "mobile-native/src/theme/logiNexus.ts", "export const logiNexus"),
    ("LogiNexus panel primitive", "mobile-native/src/components/LogiNexus.tsx", "LogiNexusPanel"),
    ("LogiNexus empty state primitive", "mobile-native/src/components/LogiNexus.tsx", "LogiNexusEmptyState"),
    ("Home imports primitives", "mobile-native/src/screens/HomeScreen.tsx", "LogiNexusPanel"),
    ("Home exposes UNDX label", "mobile-native/src/screens/HomeScreen.tsx", "UNDX"),
    ("Home hero uses UNDX alerts", "mobile-native/src/screens/HomeScreen.tsx", "UNDX alerts"),
    ("Composer transmission console", "mobile-native/src/components/HomePulseComposer.tsx", "Transmission Console"),
    ("Composer accessibility publish", "mobile-native/src/components/HomePulseComposer.tsx", "accessibilityLabel={mode === \"live\" ? \"Open Live Studio\" : \"Publish Signal\"}"),
    ("Feed cards use primitive", "mobile-native/src/components/PostCard.tsx", "LogiNexusCard"),
    ("Master drawer component", "mobile-native/src/components/MasterNavigationDrawer.tsx", "export function MasterNavigationDrawer"),
    ("Master drawer search", "mobile-native/src/components/MasterNavigationDrawer.tsx", "Search PulseSoc navigation"),
    ("Master drawer inventory", "mobile-native/src/navigation/masterNavigation.ts", "masterNavigationSections"),
    ("Master drawer UNDX action", "mobile-native/src/navigation/masterNavigation.ts", "Digital Intelligence Companion"),
    ("Shared route dispatcher", "mobile-native/src/navigation/nativeRouteActions.ts", "export function openNativeRoute"),
    ("Home uses master drawer", "mobile-native/src/screens/HomeScreen.tsx", "MasterNavigationDrawer"),
    ("Global header primitive", "mobile-native/src/navigation/GlobalNavigation.tsx", "LogiNexusGlobalHeader"),
    ("Global bottom navigation primitive", "mobile-native/src/navigation/GlobalNavigation.tsx", "LogiNexusBottomNavigation"),
    ("App uses global navigation", "mobile-native/src/navigation/AppNavigator.tsx", "LogiNexusBottomNavigation"),
    ("Master drawer identity header", "mobile-native/src/components/MasterNavigationDrawer.tsx", "master-drawer-identity"),
    ("Global tab title uses UNDX", "mobile-native/src/navigation/AppNavigator.tsx", "title: \"UNDX\""),
]


def read(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    for label, path in REQUIRED.items():
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

    progress = ROOT / "reports/pulsesoc_native_progress.md"
    if progress.exists():
        progress_text = progress.read_text(encoding="utf-8")
        if "LogiNexus Transformation Phase 1" not in progress_text:
            failures.append("progress report missing LogiNexus Transformation Phase 1 section")
        if "LogiNexus Master Navigation Drawer Foundation" not in progress_text:
            failures.append("progress report missing LogiNexus Master Navigation Drawer Foundation section")
        if "LogiNexus Global Navigation Foundation" not in progress_text:
            failures.append("progress report missing LogiNexus Global Navigation Foundation section")

    if failures:
        print("PulseSoc LogiNexus audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc LogiNexus audit passed.")
    print("Checked shared tokens, primitives, Home, composer, feed cards, master drawer, route inventory, route dispatcher, and reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
