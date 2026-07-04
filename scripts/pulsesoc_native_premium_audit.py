#!/usr/bin/env python3
"""Static audit for PulseSoc native Premium + Entitlements foundation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    api = read("mobile-native/src/api/premium.ts")
    screen = read("mobile-native/src/screens/PremiumScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    profile = read("mobile-native/src/screens/ProfileScreen.tsx")
    header = read("mobile-native/src/components/ProfileHeader.tsx")
    report = read("reports/pulsesoc_native_premium_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in (
        "/api/premium/status",
        "/api/premium/checkout",
        "/api/premium/billing-portal",
        "/api/dashboard/economy/state",
        "readJsonCache",
        "writeJsonCache",
        "startPremiumCheckout",
        "openPremiumBillingPortal",
        "mobile_native_premium",
    ):
        require(token in api, f"Premium API wrapper missing required reuse token: {token}")

    for forbidden in (
        "stripe.",
        "Stripe(",
        "grant_entitlement",
        "grantFounder",
        "revoke_entitlement",
        "setPremium",
        "founder_number =",
        "subscription_status =",
    ):
        require(forbidden not in api, f"Premium API must not duplicate payment or entitlement logic: {forbidden}")
        require(forbidden not in screen, f"Premium screen must not duplicate payment or entitlement logic: {forbidden}")

    for token in (
        "getPremiumStatus",
        "loadCachedPremiumStatus",
        "AppState.addEventListener",
        "Premium status",
        "Founder",
        "Entitlements",
        "Upgrade with PulseSoc Checkout",
        "Manage Billing",
        "Open Premium Web Hub",
        "Native never grants Premium access",
    ):
        require(token in screen, f"Premium screen missing required behavior/copy: {token}")

    for token in (
        "PremiumScreen",
        '<Stack.Screen name="Premium"',
    ):
        require(token in app_nav, f"Premium navigation missing: {token}")
    require("Premium: undefined" in types, "RootStackParamList missing Premium route")
    require('path: "pulse/premium"' in linking, "Deep-link config missing /pulse/premium")
    require('normalized.startsWith("/pulse/premium")' in routing and 'navigationRef.navigate("Premium")' in routing, "Notification routing missing Premium target")
    require('navigation.navigate("Premium")' in settings, "Settings missing Premium entry point")
    require('navigation?.navigate("Premium")' in profile and "onPremium" in header, "Profile missing Premium entry point")

    for phrase in (
        "The backend remains authoritative",
        "Checkout handoff through existing",
        "Native did not add",
        "Not device-verified",
        "Native Creator Studio Foundation",
        "GET /api/dashboard/creator/state",
        "Risk: Medium",
    ):
        require(phrase in report, f"Premium progress report missing required detail: {phrase}")

    for phrase in (
        "Premium + Entitlements Foundation",
        "reports/pulsesoc_native_premium_progress.md",
        "scripts/pulsesoc_native_premium_audit.py",
        "Native Creator Studio Foundation",
        "GET /api/dashboard/creator/state",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"Master progress missing Premium checkpoint or next recommendation: {phrase}")

    for path in ("templates", "static/js", "static/css", "mobile/pulse-react-native"):
        require(
            not any((ROOT / path).glob("**/*pulsesoc_native_premium*")),
            f"Premium native mission must not create production WebView artifacts under {path}",
        )

    print("PulseSoc native Premium foundation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
