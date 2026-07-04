#!/usr/bin/env python3
"""Static audit for the PulseSoc native Marketplace foundation."""

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
    report = read("reports/pulsesoc_native_marketplace_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/marketplace.ts")
    screen = read("mobile-native/src/screens/MarketplaceScreen.tsx")
    nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    notifications = read("mobile-native/src/navigation/notificationRouting.ts")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "Native Marketplace does not implement its own",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"marketplace report must document reuse/safety/device truth: {phrase}")

    for token in (
        "searchMarketplace",
        "/api/pulse/marketplace/search",
        "saveMarketplaceListing",
        "/api/pulse/marketplace/listings/save",
        "reportMarketplaceListing",
        "/api/pulse/marketplace/listings/report",
        "startMarketplaceSellerChat",
        "/api/pulse/messages/start",
        "openMarketplaceCheckout",
        "/api/pulse/payments/checkout",
        "marketplaceWebUrl",
        "loadCachedMarketplace",
        "normalizeMarketplaceListing",
    ):
        require(token in api, f"marketplace API wrapper missing: {token}")

    for token in (
        "MarketplaceScreen",
        "MarketplaceCard",
        "MarketplaceDetailModal",
        "NativeMediaViewer",
        "mediaViewerItemFromPulseMedia",
        "searchMarketplace",
        "saveMarketplaceListing",
        "reportMarketplaceListing",
        "startMarketplaceSellerChat",
        "openMarketplaceCheckout",
        "loadCachedMarketplace",
        "marketplaceWebUrl",
        "Checkout",
        "Contact Seller",
        "Open Web",
    ):
        require(token in screen, f"Marketplace screen behavior missing: {token}")

    for token in ("MarketplaceScreen", "Marketplace", "MarketplaceDetail"):
        require(token in nav, f"navigation missing marketplace route: {token}")

    for token in ("Marketplace", "MarketplaceDetail"):
        require(token in types, f"navigation types missing marketplace route: {token}")

    for token in ("pulse/marketplace", "MarketplaceDetail"):
        require(token in linking, f"linking missing marketplace route: {token}")
        require(token in notifications, f"notification routing missing marketplace target: {token}")

    for phrase in (
        "Marketplace Browse + Listing Detail Foundation",
        "Native Search + Discovery Foundation",
        "Why This Comes Next",
        "Risk: Medium",
        "Complexity: Medium",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed Marketplace and next-feature recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("react-native-webview" not in mobile_native.lower(), "native Marketplace must not introduce WebView")

    print("PulseSoc native Marketplace audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
