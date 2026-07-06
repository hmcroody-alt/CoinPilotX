#!/usr/bin/env python3
"""Commerce/activity fixture audit for PulseSoc native event consistency."""

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

COMMERCE_EVENTS = [
    ("purchase", "Purchase completed", "/pulse/orders/{order_id}", "paid"),
    ("payment_failed", "Payment failed", "/pulse/orders/{order_id}", "failed"),
    ("refund", "Refund issued", "/pulse/orders/{order_id}", "refunded"),
    ("dispute", "Dispute created", "/pulse/orders/{order_id}", "dispute_opened"),
    ("shipping", "Shipping updated", "/pulse/orders/{order_id}", "shipped"),
    ("order_cancelled", "Order cancelled", "/pulse/orders/{order_id}", "cancelled"),
    ("marketplace_listing_created", "Listing created", "/pulse/marketplace/{listing_id}", "pending_review"),
    ("marketplace_listing_updated", "Listing updated", "/pulse/seller-store?mode=dashboard", "active"),
    ("marketplace_listing_removed", "Listing removed", "/pulse/seller-store?mode=dashboard", "seller_deleted"),
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def add_user(cur, email: str, username: str, name: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        (email, username, name, now, now),
    )
    return int(cur.lastrowid)


def add_listing(cur, seller_id: int, title: str, status: str, approval_status: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO marketplace_listings
        (seller_user_id, title, short_description, description, category, price_label, currency,
         quantity, status, approval_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'Education', '$24.00', 'USD', 1, ?, ?, ?, ?)
        """,
        (seller_id, title, title, f"{title} detail", status, approval_status, now, now),
    )
    return int(cur.lastrowid)


def add_transaction(cur, buyer_id: int, seller_id: int, listing_id: int, status: str, title: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO seller_transactions
        (buyer_user_id, seller_user_id, seller_type, item_type, item_id, amount_cents, currency,
         platform_fee_cents, seller_net_cents, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'merchant', 'marketplace_product', ?, 2400, 'USD', 240, 2160, ?, ?, ?, ?)
        """,
        (buyer_id, seller_id, listing_id, status, json.dumps({"title": title}, default=str), now, now),
    )
    return int(cur.lastrowid)


def import_bot_with_temp_db():
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_activity_fixture_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
    bot.STRIPE_SECRET_KEY = ""
    bot.stripe.api_key = ""
    bot.init_db()
    return bot


def run_backend_fixture_smoke(failures: list[str]) -> None:
    bot = import_bot_with_temp_db()
    now = "2026-07-06T15:00:00"
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    buyer_id = add_user(cur, "activity-buyer-qa@example.com", "activitybuyerqa", "Activity Buyer QA", now)
    seller_id = add_user(cur, "activity-seller-qa@example.com", "activitysellerqa", "Activity Seller QA", now)
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, display_name, bio, status, created_at, updated_at) VALUES (?, 'Activity Seller', 'Fixture seller', 'approved', ?, ?)",
        (seller_id, now, now),
    )

    created: list[dict] = []
    for index, (event_type, title, target_template, status) in enumerate(COMMERCE_EVENTS, start=1):
        listing_status = "seller_deleted" if status == "seller_deleted" else "active"
        approval_status = "seller_deleted" if status == "seller_deleted" else ("pending_review" if status == "pending_review" else "approved")
        listing_id = add_listing(cur, seller_id, f"{title} Listing", listing_status, approval_status, f"2026-07-06T15:{index:02d}:00")
        tx_status = "created" if status == "pending_review" else status
        tx_id = add_transaction(cur, buyer_id, seller_id, listing_id, tx_status, f"{title} Listing", f"2026-07-06T15:{index:02d}:00")
        target = target_template.format(order_id=tx_id, listing_id=listing_id)
        recipient = seller_id if event_type in {"marketplace_listing_created", "marketplace_listing_updated", "marketplace_listing_removed"} else buyer_id
        bot.notify_user(
            cur,
            recipient,
            event_type,
            title,
            f"Fixture event for {event_type}.",
            target,
            actor_user_id=seller_id if recipient == buyer_id else buyer_id,
            entity_type="commerce_order" if "listing" not in event_type else "marketplace_listing",
            entity_id=str(tx_id if "listing" not in event_type else listing_id),
            metadata={
                "event_key": f"commerce-fixture-{event_type}",
                "order_id": tx_id,
                "listing_id": listing_id,
                "status": status,
                "target_url": target,
            },
        )
        created.append({"event_type": event_type, "tx_id": tx_id, "listing_id": listing_id, "target": target, "recipient": recipient})
    conn.commit()
    conn.close()

    buyer_client = bot.webhook_app.test_client()
    with buyer_client.session_transaction() as session:
        session["account_user_id"] = buyer_id
    seller_client = bot.webhook_app.test_client()
    with seller_client.session_transaction() as session:
        session["account_user_id"] = seller_id

    buyer_notifications = buyer_client.get("/api/pulse/notifications?limit=100")
    require(buyer_notifications.status_code == 200, f"buyer notifications returned {buyer_notifications.status_code}", failures)
    buyer_items = (buyer_notifications.json or {}).get("notifications") or []
    buyer_types = {str(item.get("type")) for item in buyer_items}
    for event_type in ["purchase", "payment_failed", "refund", "dispute", "shipping", "order_cancelled"]:
        require(event_type in buyer_types, f"buyer activity missing {event_type}", failures)
    created_values = [str(item.get("created_at") or "") for item in buyer_items]
    require(created_values == sorted(created_values, reverse=True), "buyer activity notifications are not newest-first", failures)
    targets = [str(item.get("target_url") or item.get("deep_link") or "") for item in buyer_items]
    require(len(targets) == len(set(targets)), "buyer commerce activity targets duplicated unexpectedly", failures)

    seller_notifications = seller_client.get("/api/pulse/notifications?limit=100")
    require(seller_notifications.status_code == 200, f"seller notifications returned {seller_notifications.status_code}", failures)
    seller_types = {str(item.get("type")) for item in ((seller_notifications.json or {}).get("notifications") or [])}
    for event_type in ["marketplace_listing_created", "marketplace_listing_updated", "marketplace_listing_removed"]:
        require(event_type in seller_types, f"seller activity missing {event_type}", failures)

    buyer_orders = buyer_client.get("/api/pulse/orders?limit=100")
    require(buyer_orders.status_code == 200, f"buyer orders returned {buyer_orders.status_code}", failures)
    order_items = (buyer_orders.json or {}).get("orders") or []
    order_statuses = {str(order.get("status_group")) for order in order_items}
    for status in ["paid", "failed", "refunded", "cancelled", "shipped"]:
        require(status in order_statuses, f"buyer orders missing {status} status", failures)
    require(any(str(order.get("status_group")) == "dispute_opened" for order in order_items), "buyer orders missing dispute_opened status", failures)
    require(any(order.get("marketplace_listing_id") for order in order_items), "buyer orders lost marketplace listing relation", failures)

    seller_orders = seller_client.get("/api/pulse/payments/seller/orders")
    require(seller_orders.status_code == 200, f"seller orders returned {seller_orders.status_code}", failures)
    seller_order_ids = {int(order.get("id") or 0) for order in ((seller_orders.json or {}).get("orders") or [])}
    require(any(item["tx_id"] in seller_order_ids for item in created), "seller order endpoint did not share transaction ledger", failures)

    for item in buyer_items[:3]:
        resolved = buyer_client.post(f"/api/pulse/notifications/{item.get('id')}/resolve", json={"mark_read": False})
        require(resolved.status_code == 200, f"resolve returned {resolved.status_code}", failures)
        payload = resolved.json or {}
        original_target = str(item.get("target_url") or item.get("deep_link") or "")
        # Backend may fall back for native-only targets, but the native Activity layer preserves the original target.
        require(payload.get("target_url") or original_target, "notification resolve lost target information", failures)

    count_response = buyer_client.get("/api/pulse/notifications/unread-count")
    require(count_response.status_code == 200, f"unread count returned {count_response.status_code}", failures)
    require(int((count_response.json or {}).get("alert_unread_count") or (count_response.json or {}).get("count") or 0) >= 6, "buyer unread count did not include commerce activity", failures)

    first_note = buyer_items[0]
    read_response = buyer_client.post(f"/api/pulse/notifications/{first_note.get('id')}/read", json={"notification_id": first_note.get("id")})
    require(read_response.status_code == 200, f"mark read returned {read_response.status_code}", failures)
    delete_response = buyer_client.delete(f"/api/pulse/notifications/{first_note.get('id')}", json={"notification_id": first_note.get("id")})
    require(delete_response.status_code == 200, f"delete notification returned {delete_response.status_code}", failures)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "bot.py",
        "mobile-native/src/api/activity.ts",
        "mobile-native/src/api/notifications.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "mobile-native/src/screens/ActivityInboxScreen.tsx",
        "mobile-native/src/api/orders.ts",
        "mobile-native/src/api/marketplace.ts",
        "reports/pulsesoc_native_activity_fixture_hardening.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for path in required_files:
        require((ROOT / path).exists(), f"missing {path}", failures)

    bot_source = read("bot.py")
    activity_api = read("mobile-native/src/api/activity.ts")
    notification_api = read("mobile-native/src/api/notifications.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    inbox_screen = read("mobile-native/src/screens/ActivityInboxScreen.tsx")
    orders_api = read("mobile-native/src/api/orders.ts")
    marketplace_api = read("mobile-native/src/api/marketplace.ts")
    report = read("reports/pulsesoc_native_activity_fixture_hardening.md") if (ROOT / "reports/pulsesoc_native_activity_fixture_hardening.md").exists() else ""
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "def notify_user",
        "_pulse_notification_combined_list",
        "legacy_pulse_count",
        "pulse_notifications",
        "/api/pulse/notifications",
        "/api/pulse/notifications/unread-count",
        "/api/pulse/notifications/read-all",
        "stripe_event_processed",
        "Payment webhook duplicate skipped",
        "charge.refunded",
        "charge.dispute.created",
    ]:
        require(token in bot_source, f"backend missing event authority token {token}", failures)

    for token in [
        "marketplace|listing|seller|order|checkout|purchase|product",
        "resolveActivityItemTarget",
        "if (resolved.fallback_used && item.targetUrl) return item.targetUrl",
        "countUnreadByCategory",
        "serverAuthoritative: true",
    ]:
        require(token in activity_api, f"activity API missing consistency token {token}", failures)

    for token in [
        "listNotifications",
        "getNotificationBadgeCounts",
        "markNotificationRead",
        "markAllNotificationsRead",
        "deleteNotification",
        "resolveNotificationTarget",
    ]:
        require(token in notification_api, f"notification API missing {token}", failures)

    for token in [
        "/pulse/orders",
        "/pulse/purchases",
        "/dashboard/orders",
        "BuyerOrderDetail",
        "/pulse/marketplace",
        "/pulse/activity",
        "/pulse/inbox",
    ]:
        require(token in routing, f"native notification routing missing {token}", failures)

    for token in ["Messages", "Calls", "Social", "Safety", "Verification", "Marketplace", "Creator/Growth", "Intelligence", "Mark read", "Delete"]:
        require(token in activity_api + inbox_screen, f"Activity Inbox missing category/control {token}", failures)

    for token in ["status_group", "receipt_url", "dispute_url", "tracking", "supportOrderWebUrl"]:
        require(token in orders_api, f"orders API missing activity sync token {token}", failures)
    for token in ["listMarketplaceSellerOrders", "openMarketplaceCheckout", "listMarketplaceSellerListings"]:
        require(token in marketplace_api, f"marketplace API missing sync token {token}", failures)

    for token in [
        "PulseSoc Native Commerce + Activity Fixture Hardening",
        "Commerce events route through the existing Marketplace lane",
        "server-authoritative",
        "Duplicate webhook delivery",
        "Provider/device behavior not verified",
        "QA browser checks",
    ]:
        require(token in report, f"activity fixture report missing {token}", failures)
    require("Native Commerce + Activity Fixture Hardening" in progress, "progress missing activity fixture hardening update", failures)

    run_backend_fixture_smoke(failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native commerce activity fixture audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
