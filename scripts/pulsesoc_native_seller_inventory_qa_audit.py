#!/usr/bin/env python3
"""Audit PulseSoc native seller inventory practical QA hardening."""

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
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "reports/pulsesoc_native_seller_inventory_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot = read("bot.py")
    seller = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    qa_report = read("reports/pulsesoc_native_seller_inventory_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "include_removed",
        "LOWER(COALESCE(l.status,'')) NOT IN ('seller_deleted','deleted','removed')",
        "LOWER(COALESCE(l.approval_status,'')) NOT IN ('seller_deleted','deleted','removed')",
        "seller_deleted",
    ]:
        require(token in bot, f"backend missing seller inventory QA token {token}", failures)

    for token in [
        "statusKey(listing) === \"removed\"",
        "setListings((current) => current.filter((item) => item.id !== listing.id))",
        "setEditingListingId(0)",
        "Seller inventory",
        "NativeMediaViewer",
    ]:
        require(token in seller, f"SellerStore missing QA hardening token {token}", failures)

    require("LogiNexus" not in seller + bot, "internal LogiNexus name leaked into runtime source", failures)

    for token in [
        "PulseSoc Native Seller Inventory Practical QA",
        "server-authoritative",
        "seller_deleted",
        "public search returned zero rows",
        "Authenticated React Native Web click-through QA",
        "No production WebView routes were modified",
    ]:
        require(token in qa_report, f"QA report missing {token}", failures)

    for token in [
        "Native Seller Inventory Practical QA Hardening",
        "Native Purchase/Order History + Buyer Commerce Controls Foundation",
        "overall native migration percentage",
    ]:
        require(token in progress, f"progress report missing {token}", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("pulsesoc native seller inventory QA audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
