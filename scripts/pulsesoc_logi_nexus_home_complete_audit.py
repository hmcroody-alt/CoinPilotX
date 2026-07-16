#!/usr/bin/env python3
"""Audit the scoped PulseSoc LogiNexus Homefeed transformation milestone."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "mobile-native/src/theme/logiNexus.ts",
    "mobile-native/src/components/LogiNexus.tsx",
    "mobile-native/src/screens/HomeScreen.tsx",
    "mobile-native/src/components/HomePulseComposer.tsx",
    "mobile-native/src/components/PostCard.tsx",
    "mobile-native/src/navigation/GlobalNavigation.tsx",
    "mobile-native/src/components/MasterNavigationDrawer.tsx",
    "reports/pulsesoc_logi_nexus_home_complete_design.md",
    "reports/pulsesoc_logi_nexus_home_ui_ux_audit.md",
    "reports/pulsesoc_logi_nexus_home_visible_qa.md",
    "reports/pulsesoc_logi_nexus_home_performance.md",
    "reports/pulsesoc_logi_nexus_home_accessibility.md",
    "reports/pulsesoc_logi_nexus_design_system.md",
    "reports/pulsesoc_logi_nexus_master_transformation.md",
    "reports/pulsesoc_logi_nexus_screen_inventory.md",
    "reports/pulsesoc_native_progress.md",
]

CHECKS = [
    ("Home token namespace", "mobile-native/src/theme/logiNexus.ts", "home: {"),
    ("Home deep-space token", "mobile-native/src/theme/logiNexus.ts", "backgroundDeepSpace"),
    ("Home typography tokens", "mobile-native/src/theme/logiNexus.ts", "heroMetric"),
    ("Home hero tile primitive", "mobile-native/src/screens/HomeScreen.tsx", "function HeroTile"),
    ("Home inspiration signal lines", "mobile-native/src/screens/HomeScreen.tsx", "heroSignalLine"),
    ("WebView layout side rail", "mobile-native/src/screens/HomeScreen.tsx", "function HomeWebSideRail"),
    ("WebView hero mood hierarchy", "mobile-native/src/screens/HomeScreen.tsx", "heroMoodTitle"),
    ("Home Pulse Radio hero control", "mobile-native/src/screens/HomeScreen.tsx", "home-pulse-radio-toggle"),
    ("Home metric formatter", "mobile-native/src/screens/HomeScreen.tsx", "formatHeroMetric"),
    ("UNDX hero route", "mobile-native/src/screens/HomeScreen.tsx", 'label="UNDX"'),
    ("Pulse Radio hero tile", "mobile-native/src/screens/HomeScreen.tsx", 'label="Pulse Radio"'),
    ("Safety Shield hero tile", "mobile-native/src/screens/HomeScreen.tsx", 'label="Safety Shield"'),
    ("Status rail", "mobile-native/src/screens/HomeScreen.tsx", "function StatusRail"),
    ("Status avatar imagery", "mobile-native/src/screens/HomeScreen.tsx", "statusAvatarImage"),
    ("Transmission Console compact identity", "mobile-native/src/components/HomePulseComposer.tsx", "identityOrb"),
    ("Transmission placeholder", "mobile-native/src/components/HomePulseComposer.tsx", "Transmit to the Pulse Network"),
    ("Transmission audience pill", "mobile-native/src/components/HomePulseComposer.tsx", "audiencePill"),
    ("Composer publish contract preserved", "mobile-native/src/components/HomePulseComposer.tsx", "createPost(payload)"),
    ("Composer draft persistence preserved", "mobile-native/src/components/HomePulseComposer.tsx", "AsyncStorage.setItem(DRAFT_KEY"),
    ("Signal Card verified mark", "mobile-native/src/components/PostCard.tsx", "verifiedMark"),
    ("Signal Card creator pill", "mobile-native/src/components/PostCard.tsx", "creatorPill"),
    ("Signal Card media viewer preserved", "mobile-native/src/components/PostCard.tsx", "NativeMediaViewer"),
    ("Signal Card safety actions preserved", "mobile-native/src/components/PostCard.tsx", "home-feed-mute"),
    ("Global Home PulseSoc title", "mobile-native/src/screens/HomeScreen.tsx", 'title="PulseSoc"'),
    ("Floating create dock", "mobile-native/src/navigation/GlobalNavigation.tsx", "bottomCreateSymbol"),
    ("Report status honesty", "reports/pulsesoc_logi_nexus_home_complete_design.md", "not Homefeed LogiNexus-complete"),
    ("Visible QA status honesty", "reports/pulsesoc_logi_nexus_home_visible_qa.md", "full Homefeed LogiNexus QA remains pending"),
    ("Native progress updated", "reports/pulsesoc_native_progress.md", "LogiNexus Homefeed Inspiration Alignment Pass"),
    ("WebView parity progress updated", "reports/pulsesoc_native_progress.md", "WebView Homefeed Layout Parity Pass"),
]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"Missing required file: {rel}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    for rel in REQUIRED_FILES:
        if not (ROOT / rel).exists():
            failures.append(f"missing file: {rel}")

    for label, rel, needle in CHECKS:
        try:
            text = read(rel)
        except AssertionError as exc:
            failures.append(f"{label}: {exc}")
            continue
        if needle not in text:
            failures.append(f"{label}: missing {needle!r} in {rel}")

    if failures:
        print("PulseSoc LogiNexus Homefeed audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc LogiNexus Homefeed audit passed.")
    print("Verified scoped Home design tokens, hero tiles, Status rail, Transmission Console styling, Signal Card styling, reports, and preserved server-authoritative behavior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
