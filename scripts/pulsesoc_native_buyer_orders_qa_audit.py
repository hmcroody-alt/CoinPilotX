#!/usr/bin/env python3
"""QA audit for PulseSoc native buyer order lifecycle hardening."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REQUIRED_STATUSES = ["pending", "paid", "processing", "shipped", "delivered", "cancelled", "failed", "refunded"]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run_contract_smoke(failures: list[str]) -> None:
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_buyer_orders_qa_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    bot = importlib.import_module("bot")
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T12:00:00"
    cur.execute(
        "INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
        ("buyer-orders-qa@example.com", "buyerordersqa", "Buyer Orders QA", "x", now, now),
    )
    buyer_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
        ("seller-orders-qa@example.com", "sellerordersqa", "Seller Orders QA", "x", now, now),
    )
    seller_id = int(cur.lastrowid)
    listing_ids: list[int] = []
    for index, status in enumerate(REQUIRED_STATUSES, start=1):
        listing_status = "seller_deleted" if status == "refunded" else "active"
        approval_status = "seller_deleted" if status == "refunded" else "approved"
        cur.execute(
            """
            INSERT INTO marketplace_listings
            (seller_user_id, title, short_description, description, category, price_label, currency, status, approval_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'USD', ?, ?, ?, ?)
            """,
            (
                seller_id,
                f"QA {status.title()} Listing",
                f"{status} listing",
                f"{status} listing detail",
                "Education",
                f"${10 + index}.00",
                listing_status,
                approval_status,
                f"2026-07-06T12:{index:02d}:00",
                now,
            ),
        )
        listing_id = int(cur.lastrowid)
        listing_ids.append(listing_id)
        raw_status = "created" if status == "pending" else "blocked_stripe_not_configured" if status == "failed" else status
        cur.execute(
            """
            INSERT INTO seller_transactions
            (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency,
             platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, 'merchant', 'marketplace_product', ?, ?, 'USD', 100, ?, ?, ?, ?, ?)
            """,
            (
                buyer_id,
                seller_id,
                listing_id,
                (10 + index) * 100,
                (10 + index) * 100 - 100,
                raw_status,
                json.dumps({"title": f"QA {status.title()} Listing"}, default=str),
                f"2026-07-06T12:{index:02d}:00",
                now,
            ),
        )
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    unauth = client.get("/api/pulse/orders")
    require(unauth.status_code == 401, f"unauthenticated order list expected 401, got {unauth.status_code}", failures)
    with client.session_transaction() as session:
        session["account_user_id"] = buyer_id

    list_response = client.get("/api/pulse/orders?limit=20")
    require(list_response.status_code == 200, f"buyer order list returned {list_response.status_code}", failures)
    orders = (list_response.json or {}).get("orders") or []
    groups = {str(order.get("status_group")) for order in orders}
    for status in REQUIRED_STATUSES:
        require(status in groups, f"missing status group {status}", failures)
    require(len(orders) == len(REQUIRED_STATUSES), f"expected {len(REQUIRED_STATUSES)} orders, got {len(orders)}", failures)
    created_values = [str(order.get("created_at") or "") for order in orders]
    require(created_values == sorted(created_values, reverse=True), "orders are not sorted newest first", failures)

    by_status = {str(order.get("status_group")): order for order in orders}
    failed = by_status.get("failed") or {}
    require(failed.get("payment_status") == "failed", "failed order payment_status should remain failed", failures)
    delivered = by_status.get("delivered") or {}
    require(delivered.get("payment_status") == "paid", "delivered order payment_status should read paid", failures)
    refunded = by_status.get("refunded") or {}
    require(refunded.get("marketplace_listing_id") in listing_ids, "refunded/deleted listing order lost listing reference", failures)
    require(refunded.get("receipt_url") and refunded.get("support_url"), "refunded order missing safe fallback URLs", failures)

    for status in REQUIRED_STATUSES:
        order = by_status.get(status) or {}
        detail_response = client.get(f"/api/pulse/orders/{order.get('id')}?source={order.get('source_table')}")
        require(detail_response.status_code == 200, f"detail for {status} returned {detail_response.status_code}", failures)
        detail = (detail_response.json or {}).get("order") or {}
        require(detail.get("status_group") == status, f"detail status mismatch for {status}", failures)
        require(detail.get("seller", {}).get("display_name"), f"detail missing seller for {status}", failures)

    purchases_response = client.get("/api/pulse/purchases?limit=20")
    require(purchases_response.status_code == 200, f"purchases alias returned {purchases_response.status_code}", failures)
    require(len((purchases_response.json or {}).get("purchases") or []) == len(REQUIRED_STATUSES), "purchases alias count mismatch", failures)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "bot.py",
        "mobile-native/src/api/orders.ts",
        "mobile-native/src/screens/BuyerOrdersScreen.tsx",
        "reports/pulsesoc_native_buyer_orders_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot_source = read("bot.py")
    api = read("mobile-native/src/api/orders.ts")
    screen = read("mobile-native/src/screens/BuyerOrdersScreen.tsx")
    report = read("reports/pulsesoc_native_buyer_orders_qa.md") if (ROOT / "reports/pulsesoc_native_buyer_orders_qa.md").exists() else ""
    progress = read("reports/pulsesoc_native_progress.md")

    for token in REQUIRED_STATUSES:
        require(token in api or token in screen or token in bot_source, f"missing status token {token}", failures)
    for token in ["payment_status = status_group", "payment_status = \"paid\"", "server/provider controlled", "receipt_url", "support_url"]:
        require(token in bot_source, f"backend missing QA token {token}", failures)
    for token in ["Transaction Timeline", "Open Listing", "View Seller", "View Receipt", "Support", "No purchases yet"]:
        require(token in screen, f"BuyerOrdersScreen missing QA UI token {token}", failures)
    for token in ["normalizeStatus", "loadCachedBuyerOrders", "formatOrderMoney", "supportOrderWebUrl"]:
        require(token in api, f"orders API missing QA token {token}", failures)
    for token in ["PulseSoc Native Buyer Orders Practical QA", "pending", "paid", "processing", "shipped", "delivered", "cancelled", "failed", "refunded"]:
        require(token in report, f"QA report missing {token}", failures)
    require("Native Buyer Orders Practical QA Hardening" in progress, "progress missing buyer orders QA hardening", failures)

    run_contract_smoke(failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native buyer orders QA audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
