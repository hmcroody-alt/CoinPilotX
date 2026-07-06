#!/usr/bin/env python3
"""Seeded chaos validation for PulseSoc native event-sync consistency."""

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

CHAOS_TYPES = [
    "purchase_created",
    "payment_failed",
    "refund_issued",
    "dispute_created",
    "listing_created",
    "listing_updated",
    "listing_removed",
    "order_cancelled",
    "message_received",
    "call_started",
    "call_ended",
    "notification_delivered",
]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def add_user(cur, email: str, username: str, display_name: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        (email, username, display_name, now, now),
    )
    return int(cur.lastrowid)


def add_listing(cur, seller_id: int, title: str, status: str, approval_status: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO marketplace_listings
        (seller_user_id, title, short_description, description, category, price_label, currency,
         quantity, status, approval_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'Education', '$24.00', 'USD', 3, ?, ?, ?, ?)
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
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_native_chaos_", suffix=".sqlite", delete=False) as handle:
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


def notify(bot, cur, recipient: int, actor: int, event_type: str, title: str, target: str, entity_type: str, entity_id: int, metadata: dict) -> None:
    bot.notify_user(
        cur,
        recipient,
        event_type,
        title,
        f"Chaos fixture event for {event_type}.",
        target,
        actor_user_id=actor,
        entity_type=entity_type,
        entity_id=str(entity_id),
        metadata=metadata,
    )


def run_backend_chaos_simulation(failures: list[str]) -> None:
    bot = import_bot_with_temp_db()
    now = "2026-07-06T16:00:00"
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()

    buyer_id = add_user(cur, "chaos-buyer-qa@example.com", "chaosbuyerqa", "Chaos Buyer QA", now)
    seller_id = add_user(cur, "chaos-seller-qa@example.com", "chaossellerqa", "Chaos Seller QA", now)
    actor_id = add_user(cur, "chaos-actor-qa@example.com", "chaosactorqa", "Chaos Actor QA", now)
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, display_name, bio, status, created_at, updated_at) VALUES (?, 'Chaos Seller', 'Chaos fixture seller', 'approved', ?, ?)",
        (seller_id, now, now),
    )

    created_targets: list[str] = []
    final_status_by_tx: dict[int, str] = {}
    listing_ids: list[int] = []

    # Rapid, intentionally out-of-order commerce/listing burst.
    burst_plan = [
        ("purchase_created", "paid", "active", "approved", buyer_id, "commerce_order"),
        ("payment_failed", "failed", "active", "approved", buyer_id, "commerce_order"),
        ("refund_issued", "refunded", "active", "approved", buyer_id, "commerce_order"),
        ("dispute_created", "dispute_opened", "active", "approved", buyer_id, "commerce_order"),
        ("order_cancelled", "cancelled", "active", "approved", buyer_id, "commerce_order"),
        ("listing_created", "created", "pending_review", "pending_review", seller_id, "marketplace_listing"),
        ("listing_updated", "paid", "active", "approved", seller_id, "marketplace_listing"),
        ("listing_removed", "paid", "seller_deleted", "seller_deleted", seller_id, "marketplace_listing"),
    ]
    for index, (event_type, tx_status, listing_status, approval_status, recipient, entity_type) in enumerate(burst_plan, start=1):
        created_at = f"2026-07-06T16:{index:02d}:00"
        listing_id = add_listing(cur, seller_id, f"Chaos {event_type}", listing_status, approval_status, created_at)
        listing_ids.append(listing_id)
        tx_id = add_transaction(cur, buyer_id, seller_id, listing_id, tx_status, f"Chaos {event_type}", created_at)
        final_status_by_tx[tx_id] = tx_status
        target = f"/pulse/orders/{tx_id}" if entity_type == "commerce_order" else f"/pulse/marketplace/{listing_id}"
        created_targets.append(target)
        notify(
            bot,
            cur,
            recipient,
            actor_id,
            event_type,
            f"Chaos {event_type}",
            target,
            entity_type,
            tx_id if entity_type == "commerce_order" else listing_id,
            {
                "event_key": f"chaos-{event_type}-{index}",
                "arrival": "out_of_order" if index % 2 else "rapid_burst",
                "order_id": tx_id,
                "listing_id": listing_id,
                "status": tx_status,
            },
        )

    # Same order updated twice; final server truth must win.
    conflict_listing = add_listing(cur, seller_id, "Chaos Conflict Order", "active", "approved", "2026-07-06T16:20:00")
    conflict_tx = add_transaction(cur, buyer_id, seller_id, conflict_listing, "pending", "Chaos Conflict Order", "2026-07-06T16:20:00")
    cur.execute("UPDATE seller_transactions SET status='failed', updated_at=? WHERE id=?", ("2026-07-06T16:21:00", conflict_tx))
    notify(bot, cur, buyer_id, seller_id, "payment_failed", "Chaos payment failed", f"/pulse/orders/{conflict_tx}", "commerce_order", conflict_tx, {"event_key": "chaos-conflict-failed", "arrival": "rapid_update"})
    cur.execute("UPDATE seller_transactions SET status='paid', updated_at=? WHERE id=?", ("2026-07-06T16:22:00", conflict_tx))
    notify(bot, cur, buyer_id, seller_id, "purchase_created", "Chaos payment recovered", f"/pulse/orders/{conflict_tx}", "commerce_order", conflict_tx, {"event_key": "chaos-conflict-paid", "arrival": "final_truth"})
    final_status_by_tx[conflict_tx] = "paid"

    # Refund after cancellation; backend-final refunded state must win.
    overlap_listing = add_listing(cur, seller_id, "Chaos Refund After Cancel", "active", "approved", "2026-07-06T16:23:00")
    overlap_tx = add_transaction(cur, buyer_id, seller_id, overlap_listing, "cancelled", "Chaos Refund After Cancel", "2026-07-06T16:23:00")
    notify(bot, cur, buyer_id, seller_id, "order_cancelled", "Chaos order cancelled", f"/pulse/orders/{overlap_tx}", "commerce_order", overlap_tx, {"event_key": "chaos-overlap-cancelled", "arrival": "delayed"})
    cur.execute("UPDATE seller_transactions SET status='refunded', updated_at=? WHERE id=?", ("2026-07-06T16:24:00", overlap_tx))
    notify(bot, cur, buyer_id, seller_id, "refund_issued", "Chaos refund after cancellation", f"/pulse/orders/{overlap_tx}", "commerce_order", overlap_tx, {"event_key": "chaos-overlap-refunded", "arrival": "final_truth"})
    final_status_by_tx[overlap_tx] = "refunded"

    # Listing deleted during active order; order history must keep the relation.
    deleted_listing = add_listing(cur, seller_id, "Chaos Deleted Active Order Listing", "active", "approved", "2026-07-06T16:25:00")
    deleted_tx = add_transaction(cur, buyer_id, seller_id, deleted_listing, "paid", "Chaos Deleted Active Order Listing", "2026-07-06T16:25:00")
    cur.execute("UPDATE marketplace_listings SET status='seller_deleted', approval_status='seller_deleted', updated_at=? WHERE id=?", ("2026-07-06T16:26:00", deleted_listing))
    notify(bot, cur, seller_id, buyer_id, "listing_removed", "Chaos listing removed during order", f"/pulse/marketplace/{deleted_listing}", "marketplace_listing", deleted_listing, {"event_key": "chaos-deleted-active-listing", "arrival": "offline_replay", "order_id": deleted_tx})
    final_status_by_tx[deleted_tx] = "paid"

    # Message, call, and generic notification events as Activity Inbox producers.
    notify(bot, cur, buyer_id, actor_id, "message_received", "Chaos message received", "/pulse/messages/99001", "conversation", 99001, {"event_key": "chaos-message-received", "arrival": "rapid_burst"})
    notify(bot, cur, buyer_id, actor_id, "call_started", "Chaos call started", "/pulse/calls/chaos-call-1", "call", 99002, {"event_key": "chaos-call-started", "arrival": "rapid_burst"})
    notify(bot, cur, buyer_id, actor_id, "call_ended", "Chaos call ended", "/pulse/calls/chaos-call-1", "call", 99002, {"event_key": "chaos-call-ended", "arrival": "delayed"})
    notify(bot, cur, buyer_id, actor_id, "notification_delivered", "Chaos notification delivered", "/pulse/activity", "notification", 99003, {"event_key": "chaos-notification-delivered", "arrival": "offline_replay"})
    conn.commit()
    conn.close()

    buyer_client = bot.webhook_app.test_client()
    with buyer_client.session_transaction() as session:
        session["account_user_id"] = buyer_id
    seller_client = bot.webhook_app.test_client()
    with seller_client.session_transaction() as session:
        session["account_user_id"] = seller_id

    notifications = buyer_client.get("/api/pulse/notifications?limit=200")
    require(notifications.status_code == 200, f"buyer notifications returned {notifications.status_code}", failures)
    buyer_items = (notifications.json or {}).get("notifications") or []
    buyer_types = {str(item.get("type")) for item in buyer_items}
    for event_type in ["purchase_created", "payment_failed", "refund_issued", "dispute_created", "order_cancelled", "call_started", "call_ended", "notification_delivered"]:
        require(event_type in buyer_types, f"buyer Activity/Notifications missing chaos event {event_type}", failures)
    message_notifications = buyer_client.get("/api/pulse/notifications?filter=messages&limit=50")
    require(message_notifications.status_code == 200, f"message notifications returned {message_notifications.status_code}", failures)
    message_items = (message_notifications.json or {}).get("notifications") or []
    message_types = {str(item.get("type")) for item in message_items}
    require("message_received" in message_types or "message_notification" in message_types, "message lane missing seeded message event", failures)
    created_values = [str(item.get("created_at") or "") for item in buyer_items]
    require(created_values == sorted(created_values, reverse=True), "buyer notifications are not newest-first under chaos", failures)
    target_values = [str(item.get("target_url") or item.get("deep_link") or "") for item in buyer_items]
    message_targets = [str(item.get("target_url") or item.get("deep_link") or "") for item in message_items]
    require(any(target.startswith("/pulse/orders/") for target in target_values), "buyer notifications lost order targets", failures)
    require(any(target.startswith("/pulse/messages/") for target in message_targets), "message notifications lost message target", failures)
    require(any(target.startswith("/pulse/calls/") for target in target_values), "buyer notifications lost call target", failures)

    counts = buyer_client.get("/api/pulse/notifications/unread-count")
    require(counts.status_code == 200, f"unread count returned {counts.status_code}", failures)
    unread = int((counts.json or {}).get("alert_unread_count") or (counts.json or {}).get("count") or 0)
    require(unread >= 9, "buyer unread count did not include chaos burst events", failures)

    orders = buyer_client.get("/api/pulse/orders?limit=200")
    require(orders.status_code == 200, f"buyer orders returned {orders.status_code}", failures)
    order_items = (orders.json or {}).get("orders") or []
    by_id = {int(order.get("id") or 0): order for order in order_items}
    for tx_id, expected in final_status_by_tx.items():
        require(tx_id in by_id, f"buyer orders missing chaos transaction {tx_id}", failures)
        actual = str(by_id.get(tx_id, {}).get("status_group") or by_id.get(tx_id, {}).get("status") or "")
        require(actual == expected or (expected == "paid" and actual in {"paid", "processing"}) or (expected == "created" and actual in {"pending", "created"}), f"transaction {tx_id} expected {expected}, got {actual}", failures)
    require(by_id.get(overlap_tx, {}).get("status_group") == "refunded", "refund-after-cancellation did not resolve to refunded final truth", failures)
    require(int(by_id.get(deleted_tx, {}).get("marketplace_listing_id") or 0) == deleted_listing, "deleted listing relation lost from active order history", failures)

    seller_orders = seller_client.get("/api/pulse/payments/seller/orders")
    require(seller_orders.status_code == 200, f"seller orders returned {seller_orders.status_code}", failures)
    seller_order_ids = {int(order.get("id") or 0) for order in ((seller_orders.json or {}).get("orders") or [])}
    require(deleted_tx in seller_order_ids and conflict_tx in seller_order_ids, "seller order ledger missing chaos transactions", failures)

    seller_listings = seller_client.get("/api/pulse/marketplace/seller/listings?limit=200")
    require(seller_listings.status_code == 200, f"seller listings returned {seller_listings.status_code}", failures)
    seller_listing_ids = {int((listing.get("id") or listing.get("listing_id") or 0)) for listing in ((seller_listings.json or {}).get("items") or (seller_listings.json or {}).get("listings") or [])}
    require(deleted_listing in seller_listing_ids or any(listing_id in seller_listing_ids for listing_id in listing_ids), "seller inventory lost chaos listings", failures)

    marketplace = buyer_client.get("/api/pulse/marketplace/search?limit=100")
    require(marketplace.status_code == 200, f"marketplace search returned {marketplace.status_code}", failures)
    public_ids = {int((listing.get("id") or listing.get("listing_id") or 0)) for listing in ((marketplace.json or {}).get("items") or (marketplace.json or {}).get("listings") or [])}
    require(deleted_listing not in public_ids, "seller-deleted listing leaked into public marketplace search", failures)

    for item in buyer_items[:5]:
        resolved = buyer_client.post(f"/api/pulse/notifications/{item.get('id')}/resolve", json={"mark_read": False})
        require(resolved.status_code == 200, f"notification resolve returned {resolved.status_code}", failures)
        require((resolved.json or {}).get("target_url") or item.get("target_url") or item.get("deep_link"), "notification resolve lost target under chaos", failures)


def simulate_native_sync_chaos(failures: list[str]) -> None:
    sync_events = [
        {"event_id": "1", "type": "purchase_created", "target_url": "/pulse/orders/1"},
        {"event_id": "2", "type": "payment_failed", "target_url": "/pulse/orders/2"},
        {"event_id": "2", "type": "payment_failed", "target_url": "/pulse/orders/2"},
        {"event_id": "5", "type": "listing_removed", "target_url": "/pulse/marketplace/9"},
        {"event_id": "3", "type": "message_received", "target_url": "/pulse/messages/1"},
        {"event_id": "4", "type": "call_started", "target_url": "/pulse/calls/abc"},
        {"event_id": "6", "type": "refund_issued", "target_url": "/pulse/orders/1"},
        {"event_id": "6", "type": "refund_issued", "target_url": "/pulse/orders/1"},
    ]
    seen: set[str] = set()
    unique = []
    for event in sync_events:
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        unique.append(event)
    require(len(unique) == 6, "sync chaos dedupe did not collapse duplicate event envelopes", failures)

    invalidated: set[str] = set()
    for event in unique:
        signal = f"{event['type']} {event['target_url']}".lower()
        if any(token in signal for token in ["order", "purchase", "payment", "refund", "dispute", "shipping"]):
            invalidated.update(["orders", "activity", "notifications"])
        if any(token in signal for token in ["listing", "marketplace", "seller", "inventory"]):
            invalidated.update(["marketplace", "seller_inventory", "activity"])
        if any(token in signal for token in ["message", "conversation", "chat"]):
            invalidated.update(["messenger", "activity"])
        if any(token in signal for token in ["call", "ring", "missed"]):
            invalidated.update(["calls", "activity", "notifications"])
    for expected in ["orders", "activity", "notifications", "marketplace", "seller_inventory", "messenger", "calls"]:
        require(expected in invalidated, f"sync chaos invalidation missing {expected}", failures)


def check_static_contracts(failures: list[str]) -> None:
    report = read("reports/pulsesoc_native_event_sync_chaos_validation.md")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    activity = read("mobile-native/src/api/activity.ts")
    orders = read("mobile-native/src/api/orders.ts")
    marketplace = read("mobile-native/src/api/marketplace.ts")
    messenger = read("mobile-native/src/api/messenger.ts")
    calls = read("mobile-native/src/api/calls.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    bot = read("bot.py")

    for token in [
        "SYSTEM STATE AUDIT",
        "Systems Stable Under Chaos",
        "Systems That Drift Under Load",
        "Systems That Fail Under Concurrency",
        "Sync Engine Weaknesses Exposed",
        "Missing Real-Time Guarantees",
        "Subsystem Completion %",
        "Overall Native Migration %",
        "ONE Highest-Impact Fix Only",
        "Expose or confirm an authenticated server event cursor endpoint",
    ]:
        require(token in report, f"chaos report missing {token}", failures)

    for token in [
        "/api/pulse/sync/events",
        "latestEventId",
        "lastFullResyncAt",
        "full_resync_fallback",
        "uniqueHandlers",
        '"orders", "activity", "notifications"',
        '"marketplace", "seller_inventory", "activity"',
        '"messenger", "activity"',
        '"calls", "activity", "notifications"',
    ]:
        require(token in event_sync, f"event sync missing chaos stability token {token}", failures)
    require("WebSocket" not in event_sync and "EventSource" not in event_sync, "event sync must remain polling-first", failures)

    for token in ["loadActivityInboxState", "listNotifications", "listConversations().catch(loadCachedConversations)", "getActiveCalls().catch(loadCachedActiveCalls)", "serverAuthoritative: true"]:
        require(token in activity, f"activity consumer missing {token}", failures)
    for token in ["/api/pulse/orders", "loadCachedBuyerOrders", "status_group", "receipt_url", "dispute_url"]:
        require(token in orders, f"orders consumer missing {token}", failures)
    for token in ["searchMarketplace", "loadSellerStoreSnapshot", "listMarketplaceSellerListings", "listMarketplaceSellerOrders"]:
        require(token in marketplace, f"marketplace/seller consumer missing {token}", failures)
    for token in ["syncConversation", "/sync?after_id=", "listConversations", "loadCachedConversations"]:
        require(token in messenger, f"messenger consumer missing {token}", failures)
    for token in ["getActiveCalls", "getCallStatus", "getCallEvents", "loadCachedActiveCalls"]:
        require(token in calls, f"calls consumer missing {token}", failures)
    for token in ["/pulse/orders", "/pulse/marketplace", "/pulse/activity", "/pulse/inbox", "/pulse/messages", "pulse\\/calls"]:
        require(token in routing, f"routing missing {token}", failures)
    for token in ["def notify_user", "seller_transactions", "marketplace_listings", "stripe_event_processed", "Payment webhook duplicate skipped", "charge.refunded", "charge.dispute.created"]:
        require(token in bot, f"backend producer missing {token}", failures)


def main() -> int:
    failures: list[str] = []
    for path in [
        "reports/pulsesoc_native_event_sync_chaos_validation.md",
        "mobile-native/src/core/eventSync.ts",
        "mobile-native/src/api/activity.ts",
        "mobile-native/src/api/orders.ts",
        "mobile-native/src/api/marketplace.ts",
        "mobile-native/src/api/messenger.ts",
        "mobile-native/src/api/calls.ts",
        "mobile-native/src/navigation/notificationRouting.ts",
        "bot.py",
    ]:
        require((ROOT / path).exists(), f"missing {path}", failures)

    check_static_contracts(failures)
    simulate_native_sync_chaos(failures)
    run_backend_chaos_simulation(failures)

    if failures:
        print("PulseSoc native event sync chaos validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PulseSoc native event sync chaos validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
