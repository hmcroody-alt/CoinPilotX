#!/usr/bin/env python3
"""Readiness audit for PulseSoc native real-time event synchronization."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_file(relative: str, failures: list[str]) -> str:
    path = ROOT / relative
    require(path.exists(), f"missing {relative}", failures)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_static_contracts(failures: list[str]) -> None:
    bot = require_file("bot.py", failures)
    command_client = require_file("services/command_center_client.py", failures)
    worker = require_file("services/command_center_worker/app.py", failures)
    activity = require_file("mobile-native/src/api/activity.ts", failures)
    notifications = require_file("mobile-native/src/api/notifications.ts", failures)
    orders = require_file("mobile-native/src/api/orders.ts", failures)
    marketplace = require_file("mobile-native/src/api/marketplace.ts", failures)
    messenger = require_file("mobile-native/src/api/messenger.ts", failures)
    calls = require_file("mobile-native/src/api/calls.ts", failures)
    cache = require_file("mobile-native/src/core/cache.ts", failures)
    app_nav = require_file("mobile-native/src/navigation/AppNavigator.tsx", failures)
    routing = require_file("mobile-native/src/navigation/notificationRouting.ts", failures)
    progress = require_file("reports/pulsesoc_native_progress.md", failures)
    report = require_file("reports/pulsesoc_native_realtime_sync_readiness.md", failures)

    for token in [
        "def notify_user",
        "_pulse_notification_combined_list",
        "/api/pulse/notifications",
        "/api/pulse/notifications/unread-count",
        "/api/pulse/orders",
        "/api/pulse/payments/seller/orders",
        "seller_transactions",
        "stripe_event_processed",
        "Payment webhook duplicate skipped",
    ]:
        require(token in bot, f"backend missing server-authoritative token: {token}", failures)

    for token in [
        "def enqueue_realtime_event",
        "def get_realtime_status",
        "def get_realtime_events",
        "/internal/command-center/realtime/event",
        "/internal/command-center/realtime/poll/",
        "polling_fallback",
        "idempotency_key",
        "X-Idempotency-Key",
    ]:
        require(token in command_client, f"command center client missing realtime token: {token}", failures)

    for token in [
        "/internal/command-center/realtime/event",
        "/internal/command-center/realtime/poll/<int:user_id>",
        "/internal/command-center/realtime/stream/<int:user_id>",
        "/internal/command-center/realtime/status",
    ]:
        require(token in worker, f"command center worker missing route token: {token}", failures)

    for token in [
        "loadActivityInboxState",
        "listNotifications",
        "getNotificationBadgeCounts",
        "listConversations().catch(loadCachedConversations)",
        "getActiveCalls().catch(loadCachedActiveCalls)",
        "serverAuthoritative: true",
        "marketplace|listing|seller|order|checkout|purchase|product",
        "call|ring|voice|video",
        "safety|trust|report|appeal|strike|block|mute",
        "verification|verified|badge|identity",
    ]:
        require(token in activity, f"activity API missing sync-readiness token: {token}", failures)

    for token in [
        "readJsonCache",
        "writeJsonCache",
        "listBuyerOrders",
        "loadCachedBuyerOrders",
        "/api/pulse/orders",
        "normalizeStatus",
    ]:
        require(token in orders, f"orders API missing sync-readiness token: {token}", failures)

    for token in [
        "loadSellerStoreSnapshot",
        "loadCachedSellerStore",
        "searchMarketplace",
        "listMarketplaceSellerOrders",
        "listMarketplaceSellerListings",
        "/api/pulse/marketplace/seller/listings",
        "/api/pulse/payments/seller/orders",
    ]:
        require(token in marketplace, f"marketplace API missing sync-readiness token: {token}", failures)

    for token in [
        "syncConversation",
        "/api/pulse/messages/",
        "/sync?after_id=",
        "listConversations",
        "loadCachedConversations",
    ]:
        require(token in messenger, f"messenger API missing sync-readiness token: {token}", failures)

    for token in [
        "getActiveCalls",
        "getCallStatus",
        "loadCachedActiveCalls",
        "loadCachedCallStatus",
        "/api/calls/active",
        "/api/calls/",
    ]:
        require(token in calls, f"calls API missing sync-readiness token: {token}", failures)

    for token in [
        "AsyncStorage.getItem",
        "AsyncStorage.removeItem",
        "AsyncStorage.setItem",
    ]:
        require(token in cache, f"core cache missing safe hydration token: {token}", failures)

    for token in [
        "AppState.addEventListener",
        "Notifications.addNotificationReceivedListener",
        "refreshBadges",
        "ActivityInbox",
        "BuyerOrders",
        "SellerStore",
        "Marketplace",
    ]:
        require(token in app_nav, f"app navigation missing lifecycle/surface token: {token}", failures)

    for token in [
        "/pulse/orders",
        "/dashboard/orders",
        "/pulse/marketplace",
        "/pulse/activity",
        "/pulse/inbox",
        "/pulse/messages",
        "pulse\\/calls",
        "buyerOrderRouteTarget",
        "normalizeNotificationTarget",
    ]:
        require(token in routing, f"notification routing missing realtime target token: {token}", failures)

    for token in [
        "Native Real-time Event Sync Readiness",
        "Native Event Sync Foundation",
        "overall native migration",
        "Activity + Notifications",
        "Buyer Orders",
        "Seller Inventory",
    ]:
        require(token in report + progress, f"readiness/progress missing token: {token}", failures)


def check_command_center_fallback(failures: list[str]) -> None:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    os.environ.pop("COMMAND_CENTER_INTERNAL_URL", None)
    os.environ.pop("COMMAND_CENTER_INTERNAL_TOKEN", None)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    client = importlib.import_module("services.command_center_client")
    status = client.get_realtime_status()
    events = client.get_realtime_events(42, after_id=7, limit=10)
    dispatch = client.enqueue_realtime_event(
        "commerce.order.updated",
        {"order_id": 123, "invalidate": ["activity", "orders", "seller_inventory", "marketplace"]},
        recipient_ids=[42],
        actor_id=7,
        event_id="audit-commerce-order-123",
    )
    require(status.get("available") is False, "disabled realtime status should report unavailable fallback", failures)
    require(status.get("transport") == "polling_fallback", "disabled realtime status should expose polling_fallback", failures)
    require(events.get("events") == [], "disabled realtime events should return empty event list", failures)
    require(int(events.get("latest_event_id") or 0) == 7, "disabled realtime poll should preserve latest_event_id cursor", failures)
    require(dispatch.get("ok") is True and dispatch.get("dispatched") is False and dispatch.get("reason") == "disabled", "disabled realtime dispatch should degrade safely", failures)


def main() -> int:
    failures: list[str] = []
    check_static_contracts(failures)
    check_command_center_fallback(failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PulseSoc native realtime sync readiness audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
