#!/usr/bin/env python3
"""Audit final PulseSoc Native Home production-parity pass."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> int:
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    post_card = read("mobile-native/src/components/PostCard.tsx")
    navigation = read("mobile-native/src/navigation/GlobalNavigation.tsx")

    combined = home + composer + post_card + navigation
    for forbidden in (
        "HomeV2",
        "HeroV2",
        "PostCard2",
        "ComposerNew",
        "NewStatusRail",
        "NewFeed",
        "RightRailNew",
    ):
        if forbidden in combined:
            raise AssertionError(f"Duplicate Home implementation detected: {forbidden}")

    for needle, label in (
        ("function PulseNetworkHero", "existing Pulse Network hero"),
        ("function HomeWebSideRail", "existing right rail"),
        ("function HomeCommandRail", "existing left command rail"),
        ("function StatusRail", "existing Status rail"),
        ("export function HomePulseComposer", "existing composer"),
        ("export function PostCard", "existing feed card"),
        ("LogiNexusBottomNavigation", "shared bottom navigation"),
    ):
        require(combined, needle, label)

    for label in ("Post", "Reel", "Live", "Marketplace", "Music", "Poll", "Question", "More"):
        require(composer, label, f"production composer mode {label}")

    for label in ("Photo", "Video", "Feeling", "Location", "Mention", "Topic", "Public"):
        require(composer, label, f"production composer action {label}")

    for label in ("Like", "Comment", "Repost", "Share", "Save", "Follow", "Report", "Hide", "Block", "Mute"):
        require(post_card, label, f"production feed card action {label}")

    for needle, label in (
        ("width: 226", "left rail final production-width sizing"),
        ("width: 314", "right rail final production-width sizing"),
        ("fontSize: 26", "hero title tightened sizing"),
        ("minHeight: 36", "hero metric tightened sizing"),
        ("height: 48", "feed avatar production-density sizing"),
        ("borderRadius: 22", "feed card production-density radius"),
        ("minHeight: 46", "inline comment production-density path"),
        ("minWidth: 108", "composer mode compact production rail"),
    ):
        require(combined, needle, label)

    for report in (
        "reports/pulsesoc_native_home_final_visual_size_pass.md",
        "reports/pulsesoc_native_home_side_by_side_matrix.md",
        "reports/pulsesoc_native_home_responsive_matrix.md",
        "reports/pulsesoc_native_home_right_rail_parity.md",
        "reports/pulsesoc_native_home_feed_card_parity.md",
        "reports/pulsesoc_native_home_simulator_qa.md",
        "reports/pulsesoc_native_home_code_reuse_audit.md",
        "reports/pulsesoc_native_progress.md",
    ):
        read(report)

    final_report = read("reports/pulsesoc_native_home_final_visual_size_pass.md")
    for needle in (
        "Home frozen for exact parity",
        "127.0.0.1:5108",
        "No duplicate Home implementation",
        "No controls removed",
        "No major sections moved",
    ):
        require(final_report, needle, f"final Home parity report item {needle}")

    matrix = read("reports/pulsesoc_native_home_side_by_side_matrix.md")
    for section in ("Header", "Pulse Network hero", "Status rail", "Pulse Composer", "Feed card", "Inline comment", "Right rail"):
        require(matrix, section, f"side-by-side matrix section {section}")

    print("PulseSoc native Home final parity audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
