#!/usr/bin/env python3
"""Audit PulseSoc native seller listing composer foundation."""

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
        "mobile-native/package.json",
        "mobile-native/src/navigation/AppNavigator.tsx",
        "mobile-native/src/navigation/linking.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "mobile-native/src/screens/SellerListingComposerScreen.tsx",
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "reports/pulsesoc_native_seller_listing_composer_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    api = read("mobile-native/src/api/marketplace.ts")
    package_json = read("mobile-native/package.json")
    nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    composer = read("mobile-native/src/screens/SellerListingComposerScreen.tsx")
    seller = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    report = read("reports/pulsesoc_native_seller_listing_composer_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "MarketplaceListingCreatePayload",
        "MarketplaceListingCreateResponse",
        "createMarketplaceListing",
        "/api/pulse/marketplace/listings/create",
        "listMarketplaceSellerListings",
        "/api/pulse/marketplace/seller/listings",
        "media_ids",
    ]:
        require(token in api, f"marketplace API missing composer token {token}", failures)

    require('"@egjs/hammerjs"' in package_json, "mobile-native package missing react-native-gesture-handler web dependency @egjs/hammerjs", failures)

    for token in [
        "SellerListingComposerScreen",
        "MarketplaceCreateGateway",
    ]:
        require(token in nav, f"navigation missing composer token {token}", failures)

    require('MarketplaceCreateGateway: "pulse/marketplace/create"' in linking, "linking missing pulse marketplace create route", failures)
    require('navigationRef.navigate("MarketplaceCreateGateway"' in routing, "notification/deep-link routing missing marketplace create composer target", failures)

    for token in [
        "Create Listing",
        "Marketplace Forge",
        "Product media IDs",
        "Capture Media",
        "Web Uploader",
        "Submit for Review",
        "createMarketplaceListing",
        "navigation.navigate(\"SellerStore\"",
        "sellerStoreWebUrl(\"create\")",
    ]:
        require(token in composer, f"composer screen missing {token}", failures)

    require('navigation.navigate("MarketplaceCreateGateway"' in seller, "SellerStore does not route Create Listing to native composer", failures)

    for token in [
        "PulseSoc Native Seller Listing Composer",
        "server-authoritative",
        "/api/pulse/marketplace/listings/create",
        "safe web fallback",
        "No production WebView routes were modified",
    ]:
        require(token in report, f"composer report missing {token}", failures)

    for token in [
        "Native Seller Listing Composer + Listing Edit Foundation",
        "Native Seller Listing Composer Foundation",
        "Marketplace and Seller/Store",
    ]:
        require(token in progress, f"master progress missing {token}", failures)

    require("LogiNexus" not in composer + seller + api, "internal LogiNexus name leaked into native source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native seller listing composer audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
