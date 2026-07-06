#!/usr/bin/env python3
"""Commerce provider-boundary audit for the PulseSoc native migration."""

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


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def add_user(cur, email: str, username: str, display_name: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (email, username, display_name, "x", now, now),
    )
    return int(cur.lastrowid)


def add_listing(cur, seller_id: int, title: str, price_label: str, status: str, approval_status: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO marketplace_listings
        (seller_user_id, title, short_description, description, category, price_label, currency,
         status, approval_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'Education', ?, 'USD', ?, ?, ?, ?)
        """,
        (seller_id, title, title, f"{title} detail", price_label, status, approval_status, now, now),
    )
    return int(cur.lastrowid)


def run_backend_contract_smoke(failures: list[str]) -> None:
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_commerce_boundary_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ.pop("STRIPE_SECRET_KEY", None)

    bot = importlib.import_module("bot")
    bot.STRIPE_SECRET_KEY = ""
    bot.stripe.api_key = ""
    bot.init_db()

    now = "2026-07-06T14:00:00"
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    buyer_id = add_user(cur, "commerce-buyer-qa@example.com", "commercebuyerqa", "Commerce Buyer QA", now)
    seller_id = add_user(cur, "commerce-seller-qa@example.com", "commercesellerqa", "Commerce Seller QA", now)
    unapproved_seller_id = add_user(cur, "commerce-unapproved-qa@example.com", "commerceunapprovedqa", "Unapproved Seller QA", now)
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, display_name, bio, status, created_at, updated_at) VALUES (?, ?, ?, 'approved', ?, ?)",
        (seller_id, "Approved Commerce QA", "Approved seller fixture", now, now),
    )
    active_listing = add_listing(cur, seller_id, "Provider Boundary Listing", "$25.00", "active", "approved", now)
    zero_price_listing = add_listing(cur, seller_id, "Free Boundary Listing", "Free", "active", "approved", now)
    own_listing = add_listing(cur, buyer_id, "Own Listing", "$12.00", "active", "approved", now)
    unapproved_listing = add_listing(cur, unapproved_seller_id, "Unapproved Listing", "$18.00", "active", "approved", now)
    deleted_listing = add_listing(cur, seller_id, "Deleted Linked Listing", "$31.00", "seller_deleted", "seller_deleted", now)
    cur.execute(
        """
        INSERT INTO seller_transactions
        (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency,
         platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'merchant', 'marketplace_product', ?, 3100, 'USD', 310, 2790, 'refunded', ?, ?, ?)
        """,
        (buyer_id, seller_id, deleted_listing, json.dumps({"title": "Deleted Linked Listing"}, default=str), now, now),
    )
    refunded_tx_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    unauth = client.post("/api/pulse/payments/checkout", json={"item_type": "marketplace_product", "item_id": active_listing})
    require(unauth.status_code == 401, f"unauthenticated checkout expected 401, got {unauth.status_code}", failures)
    with client.session_transaction() as session:
        session["account_user_id"] = buyer_id

    own_response = client.post("/api/pulse/payments/checkout", json={"item_type": "marketplace_product", "item_id": own_listing})
    require(own_response.status_code == 400, f"own-listing checkout expected 400, got {own_response.status_code}", failures)
    require("cannot buy your own" in json.dumps(own_response.json or {}).lower(), "own-listing checkout did not explain self-purchase block", failures)

    free_response = client.post("/api/pulse/payments/checkout", json={"item_type": "marketplace_product", "item_id": zero_price_listing})
    require(free_response.status_code == 400, f"free checkout expected 400, got {free_response.status_code}", failures)
    require("free or not priced" in json.dumps(free_response.json or {}).lower(), "free checkout did not explain pricing block", failures)

    unapproved_response = client.post("/api/pulse/payments/checkout", json={"item_type": "marketplace_product", "item_id": unapproved_listing})
    require(unapproved_response.status_code == 403, f"unapproved checkout expected 403, got {unapproved_response.status_code}", failures)
    require("not approved" in json.dumps(unapproved_response.json or {}).lower(), "unapproved checkout did not explain seller approval block", failures)

    blocked_ids: list[int] = []
    for attempt in range(2):
        blocked = client.post("/api/pulse/payments/checkout", json={"item_type": "marketplace_product", "item_id": active_listing})
        payload = blocked.json or {}
        require(blocked.status_code == 503, f"no-provider checkout attempt {attempt + 1} expected 503, got {blocked.status_code}", failures)
        require(not payload.get("checkout_url"), "no-provider checkout returned a provider checkout URL", failures)
        require("No card was charged" in json.dumps(payload), "no-provider checkout did not state that no card was charged", failures)
        blocked_ids.append(int(payload.get("transaction_id") or 0))
    require(all(blocked_ids), "blocked checkout attempts did not return server transaction ids", failures)
    require(len(set(blocked_ids)) == len(blocked_ids), "retry checkout reused a transaction id unexpectedly", failures)

    orders_response = client.get("/api/pulse/orders?limit=20")
    require(orders_response.status_code == 200, f"buyer order list returned {orders_response.status_code}", failures)
    orders = (orders_response.json or {}).get("orders") or []
    ids = {int(order.get("id") or 0) for order in orders}
    require(set(blocked_ids).issubset(ids), "blocked checkout transactions missing from buyer orders", failures)
    require(refunded_tx_id in ids, "deleted-listing refunded transaction missing from buyer orders", failures)
    for order in orders:
        if int(order.get("id") or 0) in blocked_ids:
            require(order.get("status_group") == "failed", "blocked checkout did not normalize to failed for buyer order view", failures)
            require(order.get("payment_status") == "failed", "blocked checkout did not normalize payment_status to failed", failures)
        if int(order.get("id") or 0) == refunded_tx_id:
            require(order.get("status_group") == "refunded", "refunded order did not normalize to refunded", failures)
            require(order.get("marketplace_listing_id") == deleted_listing, "deleted listing relation was lost from order history", failures)
            require(order.get("receipt_url") and order.get("dispute_url") and order.get("support_url"), "refunded order missing safe fallback URLs", failures)

    detail = client.get(f"/api/pulse/orders/{refunded_tx_id}?source=seller_transactions")
    require(detail.status_code == 200, f"refunded detail returned {detail.status_code}", failures)
    detail_order = (detail.json or {}).get("order") or {}
    require(detail_order.get("tracking", {}).get("message"), "order detail missing provider-controlled tracking copy", failures)

    seller_client = bot.webhook_app.test_client()
    with seller_client.session_transaction() as session:
        session["account_user_id"] = seller_id
    seller_orders = seller_client.get("/api/pulse/payments/seller/orders")
    require(seller_orders.status_code == 200, f"seller order list returned {seller_orders.status_code}", failures)
    seller_order_ids = {int(order.get("id") or 0) for order in ((seller_orders.json or {}).get("orders") or [])}
    require(refunded_tx_id in seller_order_ids, "seller order endpoint missing refunded linked transaction", failures)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "bot.py",
        "mobile-native/src/api/orders.ts",
        "mobile-native/src/api/marketplace.ts",
        "mobile-native/src/screens/BuyerOrdersScreen.tsx",
        "mobile-native/src/screens/MarketplaceScreen.tsx",
        "mobile-native/src/screens/SellerStoreScreen.tsx",
        "mobile-native/src/api/activity.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "reports/pulsesoc_native_commerce_boundary_polish.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot_source = read("bot.py")
    order_api = read("mobile-native/src/api/orders.ts")
    marketplace_api = read("mobile-native/src/api/marketplace.ts")
    buyer_screen = read("mobile-native/src/screens/BuyerOrdersScreen.tsx")
    marketplace_screen = read("mobile-native/src/screens/MarketplaceScreen.tsx")
    seller_screen = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    activity_api = read("mobile-native/src/api/activity.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    report = read("reports/pulsesoc_native_commerce_boundary_polish.md") if (ROOT / "reports/pulsesoc_native_commerce_boundary_polish.md").exists() else ""
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/pulse/payments/checkout",
        "seller_transactions",
        "blocked_stripe_not_configured",
        "seller_transaction_id",
        "checkout.session.completed",
        "payment_intent.payment_failed",
        "charge.refunded",
        "charge.dispute.created",
        "stripe_event_processed",
        "notify_user(cur, tx.get(\"buyer_user_id\"), \"purchase\"",
    ]:
        require(token in bot_source, f"backend missing commerce boundary token {token}", failures)

    for token in [
        "buyerOrderWebUrl",
        "supportOrderWebUrl",
        "receipt_url",
        "dispute_url",
        "tracking",
        "normalizeStatus",
    ]:
        require(token in order_api, f"native order API missing {token}", failures)

    for token in [
        "View Receipt",
        "Support",
        "Receipt, support, dispute, shipping, and provider pages open through existing PulseSoc web/provider flows.",
        "Buyer-side order state is read from PulseSoc payment ledgers.",
    ]:
        require(token in buyer_screen, f"buyer order screen missing provider boundary copy/control {token}", failures)

    for token in [
        "openMarketplaceCheckout",
        "pulseApi<MarketplaceActionResponse>(\"/api/pulse/payments/checkout\"",
        "checkout_url",
    ]:
        require(token in marketplace_api, f"marketplace API missing checkout boundary token {token}", failures)

    for token in [
        "checkout, seller approval, moderation, refunds, disputes, and payout release remain server-authoritative",
        "Checkout is not available for this listing yet.",
    ]:
        require(token in marketplace_screen, f"marketplace screen missing boundary copy {token}", failures)

    for token in [
        "payment, payout, trust, and fulfillment decisions remain server-authoritative",
        "Orders, seller fees, Stripe Connect onboarding, checkout, and payout release remain provider and backend controlled.",
    ]:
        require(token in seller_screen, f"seller store screen missing boundary copy {token}", failures)

    for token in ["order", "checkout", "purchase", "marketplace"]:
        require(token in activity_api, f"activity API missing commerce classifier token {token}", failures)
    for token in ["/pulse/orders", "/pulse/purchases", "/dashboard/orders", "BuyerOrderDetail"]:
        require(token in routing, f"notification routing missing buyer order target {token}", failures)

    for token in [
        "PulseSoc Native Commerce Polish + Provider Boundary QA",
        "server-authoritative",
        "No duplicate charge risk was introduced",
        "Provider-Only Release Blockers",
        "QA Browser Route Checks",
    ]:
        require(token in report, f"commerce boundary report missing {token}", failures)
    require("Native Commerce Polish + Provider Boundary QA" in progress, "progress missing commerce boundary polish update", failures)

    run_backend_contract_smoke(failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native commerce provider boundary audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
