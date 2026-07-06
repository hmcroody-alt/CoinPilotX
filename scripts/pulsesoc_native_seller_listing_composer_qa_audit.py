#!/usr/bin/env python3
"""Audit PulseSoc native seller listing composer practical QA hardening."""

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
        "mobile-native/src/api/marketplace.ts",
        "mobile-native/src/screens/SellerListingComposerScreen.tsx",
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "reports/pulsesoc_native_seller_listing_composer_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot = read("bot.py")
    api = read("mobile-native/src/api/marketplace.ts")
    composer = read("mobile-native/src/screens/SellerListingComposerScreen.tsx")
    seller_store = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    report = read("reports/pulsesoc_native_seller_listing_composer_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/pulse/marketplace/seller/listings",
        "api_pulse_marketplace_seller_listings",
        "WHERE l.seller_user_id=?",
        "pulse_marketplace_listing_payload",
        "pulse_marketplace_media_rows_for_listings",
    ]:
        require(token in bot, f"backend missing seller-owned listing token {token}", failures)

    for token in [
        "listMarketplaceSellerListings",
        "/api/pulse/marketplace/seller/listings",
        "loadSellerStoreSnapshot",
        "listMarketplaceSellerOrders",
    ]:
        require(token in api, f"native marketplace API missing {token}", failures)

    require("searchMarketplace({ limit: 48 })" not in api, "Seller/Store still depends on public marketplace search for owned listings", failures)
    require('navigation.navigate("SellerStore"' in composer, "composer does not return to Seller/Store after review handoff", failures)
    require('navigation.navigate("MarketplaceCreateGateway"' in seller_store, "Seller/Store create entry does not route to native composer", failures)

    for token in [
        "PulseSoc Native Seller Listing Composer Practical QA",
        "Missing media",
        "Merchant approval",
        "seller-owned listings endpoint",
        "NativeMediaViewer",
        "No production WebView routes were modified",
    ]:
        require(token in report, f"QA report missing {token}", failures)

    for token in [
        "Native Seller Listing Composer Practical QA Hardening",
        "Marketplace and Seller/Store",
        "overall native migration percentage",
        "Recommended next highest-value native feature/action",
    ]:
        require(token in progress, f"progress report missing {token}", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native seller listing composer qa audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
