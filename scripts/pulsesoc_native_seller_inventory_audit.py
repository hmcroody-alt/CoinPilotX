#!/usr/bin/env python3
"""Audit PulseSoc native seller inventory foundation."""

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
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "reports/pulsesoc_native_seller_inventory_progress.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot = read("bot.py")
    api = read("mobile-native/src/api/marketplace.ts")
    seller = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    report = read("reports/pulsesoc_native_seller_inventory_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "api_pulse_marketplace_seller_listing_update",
        "api_pulse_marketplace_seller_listing_pause",
        "api_pulse_marketplace_seller_listing_resume",
        "api_pulse_marketplace_seller_listing_delete",
        "pulse_marketplace_owned_listing_response",
        "approved_marketplace_seller_for_user",
        "marketplace_listing_review",
        "seller_deleted",
        "WHERE id=? AND seller_user_id=?",
    ]:
        require(token in bot, f"backend missing seller inventory token {token}", failures)

    for token in [
        "MarketplaceListingUpdatePayload",
        "updateMarketplaceSellerListing",
        "pauseMarketplaceSellerListing",
        "resumeMarketplaceSellerListing",
        "deleteMarketplaceSellerListing",
        "/api/pulse/marketplace/seller/listings/${listingId}",
    ]:
        require(token in api, f"native marketplace API missing {token}", failures)

    for token in [
        "Seller inventory",
        "Save and Review",
        "Pause",
        "Resume Review",
        "Remove",
        "Advanced Edit Web",
        "StatusPill",
        "statusKey",
        "NativeMediaViewer",
    ]:
        require(token in seller, f"SellerStore missing inventory UI token {token}", failures)

    require("Public Marketplace visibility remains approval-gated" in seller, "SellerStore missing public visibility guard copy", failures)
    require("LogiNexus" not in seller + api + bot, "internal LogiNexus name leaked into source", failures)

    for token in [
        "PulseSoc Native Seller Inventory Foundation",
        "server-authoritative",
        "Pause/resume/delete",
        "soft removal",
        "No production WebView routes were modified",
    ]:
        require(token in report, f"seller inventory report missing {token}", failures)

    for token in [
        "Native Seller Inventory Controls Foundation",
        "Marketplace and Seller/Store",
        "overall native migration percentage",
        "Recommended next highest-value native feature/action",
    ]:
        require(token in progress, f"master progress missing {token}", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native seller inventory audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
