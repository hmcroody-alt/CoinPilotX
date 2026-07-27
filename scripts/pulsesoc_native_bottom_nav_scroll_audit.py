#!/usr/bin/env python3
"""Audit native PulseSoc bottom navigation scroll visibility behavior."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile-native" / "src"
FILES = {
    "provider": MOBILE / "navigation" / "BottomNavVisibility.tsx",
    "global_nav": MOBILE / "navigation" / "GlobalNavigation.tsx",
    "app_nav": MOBILE / "navigation" / "AppNavigator.tsx",
    "screen": MOBILE / "components" / "Screen.tsx",
    "home": MOBILE / "screens" / "HomeScreen.tsx",
    "messenger": MOBILE / "screens" / "MessengerScreen.tsx",
    "reels": MOBILE / "screens" / "ReelsScreen.tsx",
}
REPORT = ROOT / "reports" / "pulsesoc_native_bottom_nav_scroll_behavior.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def read(name: str) -> str:
    path = FILES[name]
    require(path.exists(), f"{path.relative_to(ROOT)} missing")
    return path.read_text()


def main() -> None:
    provider = read("provider")
    global_nav = read("global_nav")
    app_nav = read("app_nav")
    screen = read("screen")
    home = read("home")
    messenger = read("messenger")
    reels = read("reels")
    report = REPORT.read_text()

    require("BottomNavVisibilityProvider" in provider, "visibility provider missing")
    require("useBottomNavScrollVisibility" in provider, "scroll visibility hook missing")
    require("keyboardDidShow" in provider and "keyboardDidHide" in provider, "keyboard handling missing")
    require("topRevealY" in provider and "minimumScrollableDistance" in provider, "scroll guard thresholds missing")
    require("NativeSyntheticEvent<NativeScrollEvent>" in provider, "native scroll event typing missing")
    require("useBottomNavVisibility" in global_nav, "bottom nav does not consume visibility context")
    require("Animated.timing" in global_nav and "useNativeDriver: true" in global_nav, "tab bar animation is not native-driver backed")
    require("LayoutChangeEvent" in global_nav and "shellHeight" in global_nav, "bottom nav rendered height is not measured")
    require("offscreenDistance" in global_nav and "Math.max(shellHeight, 180)" in global_nav, "bottom nav hide distance is still fixed")
    require("onLayout={(event: LayoutChangeEvent)" in global_nav, "bottom nav shell layout measurement missing")
    require("position: \"absolute\"" in global_nav and "bottom: 0" in global_nav, "hidden tab bar still reserves a bottom layout slot")
    require("left: 0" in global_nav and "right: 0" in global_nav, "absolute bottom tab bar is not edge anchored")
    require("zIndex: 40" in global_nav and "elevation: 40" in global_nav, "bottom tab bar overlay depth missing")
    require("pointerEvents={hidden ? \"none\" : \"auto\"}" in global_nav, "hidden tab bar still captures touches")
    require("accessibilityElementsHidden={hidden}" in global_nav, "hidden tab bar accessibility hiding missing")
    require("BottomNavVisibilityProvider" in app_nav and "<Tabs.Navigator" in app_nav, "tab navigator not wrapped in provider")
    require("onScroll={bottomNavScroll.onScroll}" in screen, "shared scroll container not wired")
    require("onScroll={bottomNavScroll.onScroll}" in home, "Home feed list not wired")
    require("onScroll={bottomNavScroll.onScroll}" in messenger, "Messenger conversation list not wired")
    require("useBottomNavScrollVisibility" in reels and "onScroll={bottomNavScroll.onScroll}" in reels, "Reels scroll-responsive navigation is not wired")
    require("Physical iPhone" in report and "Reels" in report and "Verification" in report, "report incomplete")
    require("Black Cover Fix" in report and "absolute overlay" in report, "black cover regression notes missing")

    print("PASS: Native PulseSoc bottom nav scroll visibility is wired and audited.")


if __name__ == "__main__":
    main()
