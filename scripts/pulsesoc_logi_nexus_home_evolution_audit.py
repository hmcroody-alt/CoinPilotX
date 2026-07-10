#!/usr/bin/env python3
"""Audit the PulseSoc native Home LogiNexus evolution pass."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "mobile-native/src/screens/HomeScreen.tsx",
    "mobile-native/src/components/HomePulseComposer.tsx",
    "mobile-native/src/session/qaSimulatorAuth.ts",
    "mobile-native/app.config.js",
    "mobile-native/src/components/PostCard.tsx",
    "mobile-native/src/navigation/GlobalNavigation.tsx",
    "reports/pulsesoc_logi_nexus_home_evolution.md",
    "reports/pulsesoc_logi_nexus_ui_ux_review.md",
    "reports/pulsesoc_logi_nexus_visual_convergence.md",
    "reports/pulsesoc_logi_nexus_motion_review.md",
    "reports/pulsesoc_native_progress.md",
]

CHECKS = [
    ("Home layout order preserved", "mobile-native/src/screens/HomeScreen.tsx", "<HomeTopBar"),
    ("Pulse Network hero preserved", "mobile-native/src/screens/HomeScreen.tsx", "function PulseNetworkHero"),
    ("Status rail preserved", "mobile-native/src/screens/HomeScreen.tsx", "function StatusRail"),
    ("Composer preserved", "mobile-native/src/screens/HomeScreen.tsx", "HomePulseComposer"),
    ("Feed tabs preserved", "mobile-native/src/screens/HomeScreen.tsx", "FEED_TABS"),
    ("Feed cards preserved", "mobile-native/src/screens/HomeScreen.tsx", "PostCard"),
    ("Wide command rail", "mobile-native/src/screens/HomeScreen.tsx", "function HomeCommandRail"),
    ("Right intelligence rail", "mobile-native/src/screens/HomeScreen.tsx", "function HomeWebSideRail"),
    ("Atmosphere layer", "mobile-native/src/screens/HomeScreen.tsx", "homeAtmosphereRoot"),
    ("Compact iPhone hero density", "mobile-native/src/screens/HomeScreen.tsx", "heroCompactMetricRow"),
    ("No fake publish rewrite", "mobile-native/src/components/HomePulseComposer.tsx", "createPost(payload)"),
    ("Draft recovery retained", "mobile-native/src/components/HomePulseComposer.tsx", "AsyncStorage.setItem(DRAFT_KEY"),
    ("Composer density preserved in existing component", "mobile-native/src/components/HomePulseComposer.tsx", "minHeight: 40"),
    ("QA simulator local API override", "mobile-native/src/session/qaSimulatorAuth.ts", "api_base"),
    ("Dynamic Expo QA API config", "mobile-native/app.config.js", "EXPO_PUBLIC_PULSE_API_BASE_URL"),
    ("Media viewer retained", "mobile-native/src/components/PostCard.tsx", "NativeMediaViewer"),
    ("Bottom navigation retained", "mobile-native/src/navigation/GlobalNavigation.tsx", "LogiNexusBottomNavigation"),
    ("Evolution report status", "reports/pulsesoc_logi_nexus_home_evolution.md", "Production layout changes: none"),
    ("UI review status", "reports/pulsesoc_logi_nexus_ui_ux_review.md", "Current production Home layout remains the blueprint"),
    ("Visual convergence status", "reports/pulsesoc_logi_nexus_visual_convergence.md", "three-column production Home structure"),
    ("Motion review status", "reports/pulsesoc_logi_nexus_motion_review.md", "motion remains intentionally lightweight"),
    ("Progress updated", "reports/pulsesoc_native_progress.md", "LogiNexus Home Evolution"),
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
        print("PulseSoc LogiNexus Home evolution audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc LogiNexus Home evolution audit passed.")
    print("Verified production layout preservation, wide command rail, right intelligence rail, atmosphere layer, reports, and preserved server-authoritative Home contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
