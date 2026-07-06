#!/usr/bin/env python3
"""System-wide consistency validation audit for PulseSoc native migration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    target = ROOT / relative
    if not target.exists():
        raise AssertionError(f"missing required file: {relative}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_tokens(source: str, tokens: list[str], label: str, failures: list[str]) -> None:
    for token in tokens:
        require(token in source, f"{label} missing token: {token}", failures)


def run_sub_audit(script: str, failures: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        failures.append(f"{script} failed:\n{result.stdout}\n{result.stderr}".strip())


def main() -> int:
    failures: list[str] = []

    report = read("reports/pulsesoc_native_system_consistency_validation.md")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    activity = read("mobile-native/src/api/activity.ts")
    notifications = read("mobile-native/src/api/notifications.ts")
    orders = read("mobile-native/src/api/orders.ts")
    marketplace = read("mobile-native/src/api/marketplace.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    bot = read("bot.py")

    require_tokens(
        report,
        [
            "PulseSoc Native System Consistency Validation",
            "Commerce Flow Integrity",
            "Payment Flow Consistency",
            "Notification + Activity Sync",
            "Cross-System Consistency",
            "Sync Engine Validation",
            "Edge Case Stress Tests",
            "SYSTEM STATE AUDIT",
            "1. Fully Consistent Systems",
            "2. Partially Inconsistent Systems",
            "3. Broken Or Stale Sync Points",
            "4. Real-Time Readiness Gaps",
            "5. Subsystem Completion %",
            "6. Overall Native Migration %",
            "7. Critical Architectural Gaps",
            "8. ONE Next Highest-Value Action Only",
            "Native Event Sync Seeded QA Hardening",
        ],
        "system consistency report",
        failures,
    )

    require_tokens(
        event_sync,
        [
            "/api/pulse/sync/events",
            "latestEventId",
            "lastFullResyncAt",
            "registerSyncInvalidation",
            "invalidateNativeSync",
            "startNativeEventSync",
            "pollNativeSync",
            "full_resync_fallback",
            "uniqueHandlers",
            '"orders", "activity", "notifications"',
            '"marketplace", "seller_inventory", "activity"',
            '"messenger", "activity"',
            '"calls", "activity", "notifications"',
        ],
        "native polling sync foundation",
        failures,
    )
    require("WebSocket" not in event_sync and "EventSource" not in event_sync, "native sync foundation must remain polling-first, not WebSocket/SSE", failures)

    require_tokens(
        app_nav,
        [
            "startNativeEventSync",
            'subsystems: ["activity", "notifications", "orders", "marketplace", "seller_inventory"]',
            "notification_received",
        ],
        "navigator sync lifecycle",
        failures,
    )
    require_tokens(
        activity,
        [
            "loadActivityInboxState",
            "listNotifications",
            "getNotificationBadgeCounts",
            "listConversations().catch(loadCachedConversations)",
            "getActiveCalls().catch(loadCachedActiveCalls)",
            "serverAuthoritative: true",
            "marketplace|listing|seller|order|checkout|purchase|product",
        ],
        "Activity Inbox server-authoritative aggregation",
        failures,
    )
    require_tokens(
        notifications,
        [
            "listNotifications",
            "getNotificationBadgeCounts",
            "markNotificationRead",
            "markAllNotificationsRead",
            "deleteNotification",
            "resolveNotificationTarget",
        ],
        "notification API controls",
        failures,
    )
    require_tokens(
        orders,
        [
            "/api/pulse/orders",
            "loadCachedBuyerOrders",
            "status_group",
            "receipt_url",
            "dispute_url",
            "tracking",
        ],
        "buyer order consistency API",
        failures,
    )
    require_tokens(
        marketplace,
        [
            "searchMarketplace",
            "listMarketplaceSellerListings",
            "loadSellerStoreSnapshot",
            "listMarketplaceSellerOrders",
            "openMarketplaceCheckout",
            "/api/pulse/payments/checkout",
        ],
        "marketplace and seller consistency API",
        failures,
    )
    require_tokens(
        routing,
        [
            "/pulse/orders",
            "/pulse/purchases",
            "/dashboard/orders",
            "/pulse/marketplace",
            "/pulse/activity",
            "/pulse/inbox",
            "pulse\\/calls",
            "/pulse/messages",
        ],
        "native deep-link consistency routing",
        failures,
    )
    require_tokens(
        bot,
        [
            "seller_transactions",
            "marketplace_listings",
            "pulse_notifications",
            "stripe_event_processed",
            "Payment webhook duplicate skipped",
            "charge.refunded",
            "charge.dispute.created",
            "/api/pulse/notifications",
            "/api/pulse/orders",
            "/api/pulse/payments/seller/orders",
            "/api/pulse/payments/checkout",
        ],
        "backend authoritative commerce/activity contracts",
        failures,
    )

    for script in [
        "scripts/pulsesoc_native_commerce_boundary_polish_audit.py",
        "scripts/pulsesoc_native_activity_fixture_hardening_audit.py",
        "scripts/pulsesoc_native_event_sync_foundation_audit.py",
        "scripts/pulsesoc_native_realtime_sync_readiness_audit.py",
    ]:
        run_sub_audit(script, failures)

    if failures:
        print("PulseSoc native system consistency validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native system consistency validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
