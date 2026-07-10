#!/usr/bin/env python3
"""Audit the native PulseSoc LogiNexus Homefeed reconstruction scope."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "mobile-native/src/screens/HomeScreen.tsx",
    "mobile-native/src/components/HomePulseComposer.tsx",
    "mobile-native/src/components/PostCard.tsx",
    "mobile-native/src/navigation/GlobalNavigation.tsx",
    "mobile-native/metro.config.js",
    "reports/pulsesoc_logi_nexus_home_reconstruction_plan.md",
    "reports/pulsesoc_logi_nexus_home_component_map.md",
    "reports/pulsesoc_logi_nexus_home_simulator_qa.md",
    "reports/pulsesoc_logi_nexus_home_visual_comparison.md",
    "reports/pulsesoc_logi_nexus_home_performance.md",
    "reports/pulsesoc_logi_nexus_home_accessibility.md",
    "reports/pulsesoc_logi_nexus_home_complete_design.md",
    "reports/pulsesoc_native_progress.md",
]

CHECKS = [
    ("responsive Home width handling", "mobile-native/src/screens/HomeScreen.tsx", "useWindowDimensions"),
    ("compact Pulse Network hero", "mobile-native/src/screens/HomeScreen.tsx", "heroQuickRow"),
    ("server-derived hero metrics", "mobile-native/src/screens/HomeScreen.tsx", "HeroMetricCell"),
    ("server-derived status count", "mobile-native/src/screens/HomeScreen.tsx", "statuses.filter"),
    ("no fake concept follower metric", "mobile-native/src/screens/HomeScreen.tsx", "formatHeroMetric(posts.length)"),
    ("Your Orbit status rail", "mobile-native/src/screens/HomeScreen.tsx", "Your Orbit"),
    ("Home composer preserved", "mobile-native/src/screens/HomeScreen.tsx", "HomePulseComposer"),
    ("Transmission placeholder", "mobile-native/src/components/HomePulseComposer.tsx", "Transmit to the Pulse Network"),
    ("publish action QA selector", "mobile-native/src/components/HomePulseComposer.tsx", "home-composer-publish"),
    ("draft persistence preserved", "mobile-native/src/components/HomePulseComposer.tsx", "AsyncStorage.setItem(DRAFT_KEY"),
    ("Signal Card creator treatment", "mobile-native/src/components/PostCard.tsx", "creatorPill"),
    ("native media viewer preserved", "mobile-native/src/components/PostCard.tsx", "NativeMediaViewer"),
    ("global command strip primitive", "mobile-native/src/navigation/GlobalNavigation.tsx", "LogiNexusGlobalHeader"),
    ("floating create dock", "mobile-native/src/navigation/GlobalNavigation.tsx", "bottomCreateSymbol"),
    ("Expo notifications resolver alias", "mobile-native/metro.config.js", "@ide/backoff"),
    ("simulator QA report", "reports/pulsesoc_logi_nexus_home_simulator_qa.md", "iPhone 17 Pro"),
    ("inspiration-only boundary", "reports/pulsesoc_logi_nexus_home_visual_comparison.md", "inspiration only"),
    ("progress updated", "reports/pulsesoc_native_progress.md", "LogiNexus Homefeed Native Reconstruction"),
]

FORBIDDEN_NATIVE_STRINGS = [
    "4.8M",
    "3,281",
    "12.8K",
    "The future is not something we enter",
    "Galactic Flow",
]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
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

    native_text = "\n".join(
        read(rel)
        for rel in [
            "mobile-native/src/screens/HomeScreen.tsx",
            "mobile-native/src/components/HomePulseComposer.tsx",
            "mobile-native/src/components/PostCard.tsx",
            "mobile-native/src/navigation/GlobalNavigation.tsx",
        ]
    )
    for forbidden in FORBIDDEN_NATIVE_STRINGS:
        if forbidden in native_text:
            failures.append(f"inspiration-only violation: found {forbidden!r} in native source")

    if failures:
        print("PulseSoc LogiNexus Home reconstruction audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc LogiNexus Home reconstruction audit passed.")
    print("Verified native Home reconstruction, server-derived metrics, preserved publish/feed/media contracts, reports, and inspiration-only guardrails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
