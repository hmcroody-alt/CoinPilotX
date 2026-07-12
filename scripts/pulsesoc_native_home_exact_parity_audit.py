#!/usr/bin/env python3
"""Audit native Home exact-production-parity guardrails."""

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
    tokens = read("mobile-native/src/theme/logiNexus.ts")
    production_css = read("static/css/pulse_home_os.css") + read("static/css/pulse_desktop_feed.css")

    for forbidden in ("HomeV2", "NewHome", "LogiHome", "HomeExperimental", "Composer2", "FeedCardNew"):
        if forbidden in home + composer + post_card:
            raise AssertionError(f"Duplicate Home implementation detected: {forbidden}")

    for needle, label in [
        ("PulseNetworkHero", "native Pulse Network hero"),
        ("StatusRail", "native Status rail"),
        ("HomePulseComposer", "existing native composer reuse"),
        ("HomeCommandRail", "wide left rail"),
        ("HomeWebSideRail", "wide right rail"),
        ("FEED_TABS", "feed tab registry"),
        ("MasterNavigationDrawer", "drawer integration"),
    ]:
        require(home, needle, label)

    for label in ("Pulse Composer", "Post", "Reel", "Live", "Marketplace", "Music", "Poll", "Question", "More"):
        require(composer, label, f"composer production control {label}")

    for label in ("Photo", "Video", "Feeling", "Location", "Mention", "Topic", "Public"):
        require(composer, label, f"composer production tool {label}")

    for label in ("Comment", "Save", "Repost", "Share", "Report", "Hide", "Block", "Mute", "Follow"):
        require(post_card, label, f"feed card action {label}")

    for needle in ("--home-bg", "--home-panel", "--home-emerald", "pulse-home-hero", "mobile-bottom-nav"):
        require(production_css, needle, f"production CSS token/source {needle}")

    for needle in ("backgroundDeepSpace", "surfaceGlass", "borderSubtle", "accentPrimary"):
        require(tokens, needle, f"native production token {needle}")

    for report in (
        "reports/pulsesoc_native_home_exact_parity_inventory.md",
        "reports/pulsesoc_native_home_production_layout_parity.md",
        "reports/pulsesoc_native_home_visual_parity.md",
        "reports/pulsesoc_native_home_interaction_parity.md",
        "reports/pulsesoc_native_home_code_reuse_audit.md",
        "reports/pulsesoc_native_production_ui_token_map.md",
        "reports/pulsesoc_native_home_simulator_qa.md",
    ):
        read(report)

    print("PulseSoc native Home exact parity audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
