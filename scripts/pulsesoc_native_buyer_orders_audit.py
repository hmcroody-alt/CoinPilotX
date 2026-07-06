#!/usr/bin/env python3
"""Audit PulseSoc native buyer orders foundation."""

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
        "bot.py",
        "mobile-native/src/api/orders.ts",
        "mobile-native/src/screens/BuyerOrdersScreen.tsx",
        "mobile-native/src/navigation/AppNavigator.tsx",
        "mobile-native/src/navigation/linking.ts",
        "mobile-native/src/navigation/types.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "reports/pulsesoc_native_buyer_orders_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot = read("bot.py")
    api = read("mobile-native/src/api/orders.ts")
    screen = read("mobile-native/src/screens/BuyerOrdersScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    types = read("mobile-native/src/navigation/types.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    marketplace = read("mobile-native/src/screens/MarketplaceScreen.tsx")
    report = read("reports/pulsesoc_native_buyer_orders_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "api_pulse_buyer_orders",
        "api_pulse_buyer_order_detail",
        "pulse_buyer_order_response",
        "/api/pulse/orders",
        "/api/pulse/purchases",
        "seller_transactions",
        "creator_transactions",
        "receipt_url",
        "support_url",
        "tracking",
    ]:
        require(token in bot, f"backend missing buyer order token {token}", failures)

    for token in [
        "listBuyerOrders",
        "getBuyerOrder",
        "loadCachedBuyerOrders",
        "buyerOrderWebUrl",
        "supportOrderWebUrl",
        "normalizeBuyerOrder",
        "/api/pulse/orders",
        "provider controlled",
    ]:
        require(token in api, f"native order API missing {token}", failures)

    for token in [
        "Purchase History",
        "Order Detail",
        "Transaction Timeline",
        "View Receipt",
        "Support",
        "Open Listing",
        "View Seller",
        "server and provider controlled",
        "StatusPill",
        "TimelineStep",
    ]:
        require(token in screen, f"BuyerOrdersScreen missing {token}", failures)

    for token in ["BuyerOrders", "BuyerOrderDetail", "BuyerPurchases", "BuyerOrdersDashboard"]:
        require(token in types, f"types missing route {token}", failures)
        require(token in app_nav, f"navigator missing route {token}", failures)

    for token in ["pulse/orders", "pulse/purchases", "dashboard/orders"]:
        require(token in linking, f"linking missing {token}", failures)

    require("buyerOrderRouteTarget" in routing, "notification routing missing buyer order target", failures)
    require("Purchase History" in settings, "Settings missing Purchase History entry", failures)
    require("Purchase History" in marketplace, "Marketplace missing Purchase History entry", failures)
    require("LogiNexus" not in bot + api + screen + app_nav + linking + types + routing, "internal LogiNexus name leaked into product source", failures)

    for token in [
        "PulseSoc Native Buyer Orders Foundation",
        "read-only",
        "backend/provider",
        "No production WebView marketplace route was modified",
        "Run a short authenticated buyer order QA hardening pass",
    ]:
        require(token in report, f"buyer orders report missing {token}", failures)

    for token in [
        "Native Purchase/Order History + Buyer Commerce Controls Foundation",
        "Purchase History",
        "overall native migration percentage",
        "Recommended next highest-value native feature/action",
    ]:
        require(token in progress, f"progress report missing {token}", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native buyer orders audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
