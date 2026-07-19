#!/usr/bin/env python3
"""Static audit for the PulseSoc Native Home mockup-inspired alignment pass.

The mockup is inspiration only. This gate checks for the intended native Home
structure, preserved wiring, and no duplicate/visual-only Home rebuild.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(text: str, token: str, label: str) -> None:
    if token not in text:
        raise AssertionError(f"Missing {label}: {token}")
    print(f"ok - {label}")


def main() -> int:
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    nav = read("mobile-native/src/navigation/GlobalNavigation.tsx")
    report = read("reports/pulsesoc_native_home_mockup_alignment.md")
    combined = home + composer + nav

    for forbidden in (
        "MockupHomeScreen",
        "HomeScreenV4",
        "FakePulseRadio",
        "FakeStatusRail",
        "visualOnly",
        "todoRoute",
    ):
        if forbidden in combined:
            raise AssertionError(f"Forbidden visual-only or duplicate marker found: {forbidden}")

    for token, label in (
        ("function PulseNetworkHero", "existing Pulse Network hero reused"),
        ("function StatusPlaceholder", "status placeholder circles"),
        ("heroMoodTitle", "large hero mood typography"),
        ("heroRadioPill", "floating Pulse Radio control"),
        ("heroQuickRow", "UNDX Radio Safety quick row"),
        ("HomePulseComposer", "native composer remains embedded"),
        ("togglePulseRadio", "real Pulse Radio toggle wiring"),
        ("onOpenStatus", "real Status item wiring"),
        ("onAddStatus", "real Add Status wiring"),
        ("onCreated", "real composer publish callback"),
    ):
        require(home, token, label)

    for token, label in (
        ("CREATE A SIGNAL", "composer mockup title"),
        ("collapsedComposer", "compact composer shell"),
        ("collapsedQuickTools", "compact media tools"),
        ("media.chooseImages", "real image picker wiring"),
        ("media.chooseVideo", "real video picker wiring"),
        ("createPost", "real post publish wiring"),
        ("createReel", "real reel publish wiring"),
    ):
        require(composer, token, label)

    for token, label in (
        ("iconButtonHome", "home-only large top controls"),
        ("avatarButtonHome", "home-only profile control"),
        ("headerTitleHome", "center PulseSoc brand"),
        ("bottomCreateSymbol", "premium create nav control"),
        ("global-bottom-navigation", "shared bottom navigation remains wired"),
    ):
        require(nav, token, label)

    for token, label in (
        ("Mockup used as inspiration only", "report design boundary"),
        ("No fake native route or fake playback was added", "report wiring boundary"),
        ("Files changed", "report file list"),
        ("QA commands", "report QA section"),
    ):
        require(report, token, label)

    print("PulseSoc native Home mockup alignment audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
