#!/usr/bin/env python3
"""Audit the PulseSoc marketplace media payload contract hardening."""

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
        "reports/pulsesoc_native_marketplace_media_contract.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot = read("bot.py")
    native_api = read("mobile-native/src/api/marketplace.ts")
    report = read("reports/pulsesoc_native_marketplace_media_contract.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "def pulse_marketplace_gallery_urls",
        "def pulse_marketplace_media_rows_for_listings",
        "def pulse_marketplace_media_payload",
        "def pulse_marketplace_listing_payload",
        "marketplace_product_media",
        "NOT IN ('rejected','removed','blocked','blocked_review')",
        '"cover_image_url": cover.get("media_url")',
        '"media": media',
        '"media_assets": media',
        "pulse_marketplace_listing_payload(row",
    ]:
        require(token in bot, f"backend missing marketplace media contract token {token}", failures)

    for token in [
        "cover_image_url?: string",
        "thumbnail_url?: string",
        "video_url?: string",
        "gallery_json?: string | string[]",
        "media?: PulseMedia[]",
        "media_assets?: PulseMedia[]",
        "normalizeMarketplaceMedia",
    ]:
        require(token in native_api, f"native marketplace API missing media token {token}", failures)

    for token in [
        "PulseSoc Native Marketplace/Seller Media Payload Contract",
        "GET /api/pulse/marketplace/search",
        "`media`",
        "`media_assets`",
        "Product media rows with rejected, removed, blocked",
        "Native Marketplace/Seller Media QA Hardening",
        "Completed QA evidence",
        "media_count=3",
        "Three `Open store media` tiles",
    ]:
        require(token in report, f"media contract report missing {token}", failures)

    for token in [
        "Native Marketplace/Seller Media Payload Contract Hardening",
        "Native Marketplace/Seller Media QA Hardening",
        "Built-in QA browser evidence confirmed",
    ]:
        require(token in progress, f"master progress missing {token}", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc marketplace media contract audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
