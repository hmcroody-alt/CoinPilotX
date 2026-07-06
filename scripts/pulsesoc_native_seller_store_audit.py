#!/usr/bin/env python3
"""Audit the PulseSoc Native Seller/Store Management foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "mobile-native/src/api/marketplace.ts",
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "mobile-native/src/screens/MarketplaceScreen.tsx",
        "mobile-native/src/screens/ProfileScreen.tsx",
        "mobile-native/src/screens/SettingsScreen.tsx",
        "mobile-native/src/navigation/AppNavigator.tsx",
        "mobile-native/src/navigation/linking.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "mobile-native/src/navigation/types.ts",
        "reports/pulsesoc_native_seller_store_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    api = read("mobile-native/src/api/marketplace.ts")
    screen = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    marketplace = read("mobile-native/src/screens/MarketplaceScreen.tsx")
    profile = read("mobile-native/src/screens/ProfileScreen.tsx")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    types = read("mobile-native/src/navigation/types.ts")
    report = read("reports/pulsesoc_native_seller_store_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/pulse/marketplace/seller/apply",
        "/api/pulse/payouts/connect",
        "/api/pulse/payments/seller/orders",
        "loadSellerStoreSnapshot",
        "sellerStoreWebUrl",
        "cacheSellerStore",
    ]:
        require(token in api, f"marketplace API missing {token}", failures)

    for token in [
        "Seller / Store",
        "Merchant application",
        "Listing management",
        "Product media gallery",
        "Orders and payouts",
        "Connect Payouts",
        "server-authoritative",
    ]:
        require(token in screen, f"SellerStoreScreen missing {token}", failures)

    for token in ["SellerStore", "MerchantApply", "MerchantDashboard", "MerchantProfile", "MarketplaceCreateGateway"]:
        require(token in types, f"route types missing {token}", failures)
        require(token in nav, f"navigator missing {token}", failures)

    for token in [
        'path: "pulse/seller-store"',
        'MerchantApply: "pulse/merchant/apply"',
        'MerchantDashboard: "pulse/merchant/dashboard"',
        'path: "pulse/merchant/:sellerId"',
        'MarketplaceCreateGateway: "pulse/marketplace/create"',
    ]:
        require(token in linking, f"linking missing {token}", failures)

    require("sellerStoreTarget" in routing, "notification routing missing seller store target helper", failures)
    require('navigation?.navigate("SellerStore"' in marketplace, "Marketplace missing SellerStore entry", failures)
    require('navigation.navigate("SellerStore"' in settings, "Settings missing SellerStore entry", failures)
    require("Open Seller / Store Management" in profile, "Profile missing SellerStore entry", failures)

    for token in [
        "PulseSoc Native Seller/Store Management Foundation",
        "GET /api/pulse/payments/seller/orders",
        "POST /api/pulse/marketplace/seller/apply",
        "POST /api/pulse/payouts/connect",
        "Native Seller/Store Practical QA Hardening",
    ]:
        require(token in report, f"feature report missing {token}", failures)

    require("Native Seller/Store Management Foundation" in progress, "master progress missing Seller/Store section", failures)
    require("LogiNexus" not in api + "\n" + screen + "\n" + marketplace + "\n" + profile + "\n" + settings, "internal LogiNexus name leaked into native source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native seller store audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
