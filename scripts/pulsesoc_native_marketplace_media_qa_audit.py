#!/usr/bin/env python3
"""Audit PulseSoc native marketplace media QA hardening."""

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
        "mobile-native/src/screens/MarketplaceScreen.tsx",
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "mobile-native/src/components/NativeMediaViewer.tsx",
        "reports/pulsesoc_native_marketplace_media_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot = read("bot.py")
    api = read("mobile-native/src/api/marketplace.ts")
    marketplace = read("mobile-native/src/screens/MarketplaceScreen.tsx")
    seller = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    viewer = read("mobile-native/src/components/NativeMediaViewer.tsx")
    report = read("reports/pulsesoc_native_marketplace_media_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "def pulse_marketplace_listing_payload",
        "pulse_marketplace_media_rows_for_listings",
        "marketplace_product_media",
        "NOT IN ('rejected','removed','blocked','blocked_review')",
        '"media": media',
        '"media_assets": media',
    ]:
        require(token in bot, f"backend marketplace media contract missing {token}", failures)

    for token in [
        "cover_image_url?: string",
        "thumbnail_url?: string",
        "video_url?: string",
        "gallery_json?: string | string[]",
        "media?: PulseMedia[]",
        "media_assets?: PulseMedia[]",
        "normalizeMarketplaceMedia",
        "parseGallery",
    ]:
        require(token in api, f"native marketplace API missing {token}", failures)

    for token in [
        "mediaDisplayUrl(listing.media[0])",
        "NativeMediaViewer",
        "mediaViewerItemFromPulseMedia",
        "marketplaceWebUrl(listing.id)",
    ]:
        require(token in marketplace, f"MarketplaceScreen missing media QA behavior {token}", failures)

    for token in [
        "const [viewerIndex, setViewerIndex] = useState(0)",
        "setViewerIndex(index)",
        "initialIndex={viewerIndex}",
        "Open store media",
        "NativeMediaViewer",
    ]:
        require(token in seller, f"SellerStoreScreen missing gallery hardening token {token}", failures)

    for token in [
        "initialIndex",
        "canGoPrevious",
        "canGoNext",
        "UnsupportedState",
        "ProcessingState",
    ]:
        require(token in viewer, f"NativeMediaViewer missing expected media behavior {token}", failures)

    for token in [
        "PulseSoc Native Marketplace/Seller Media QA Hardening",
        "Marketplace feed cards",
        "Listing Detail screen",
        "Seller/Store gallery",
        "NativeMediaViewer",
        "listing with 0 media",
        "mixed images/videos",
        "moderated media",
        "Payout/checkout boundaries",
        "Authenticated QA browser evidence",
        "No critical blocker",
    ]:
        require(token in report, f"QA report missing {token}", failures)

    for token in [
        "Native Marketplace/Seller Media QA Hardening",
        "completion percentages by subsystem",
        "overall native migration percentage",
        "Native Seller Listing Composer + Listing Edit",
    ]:
        require(token in progress, f"master progress missing {token}", failures)

    require("LogiNexus" not in api + marketplace + seller + viewer, "internal LogiNexus name leaked into native UI source", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native marketplace media qa audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
