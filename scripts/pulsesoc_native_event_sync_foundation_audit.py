#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8")


def expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def expect_all(source: str, tokens: list[str], label: str, failures: list[str]) -> None:
    for token in tokens:
        expect(token in source, f"{label} missing token: {token}", failures)


def main() -> int:
    failures: list[str] = []

    event_sync = read("mobile-native/src/core/eventSync.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    activity = read("mobile-native/src/screens/ActivityInboxScreen.tsx")
    notifications = read("mobile-native/src/screens/NotificationCenterScreen.tsx")
    orders = read("mobile-native/src/screens/BuyerOrdersScreen.tsx")
    marketplace = read("mobile-native/src/screens/MarketplaceScreen.tsx")
    seller_store = read("mobile-native/src/screens/SellerStoreScreen.tsx")
    report = read("reports/pulsesoc_native_event_sync_foundation.md")
    progress = read("reports/pulsesoc_native_progress.md")

    expect_all(
        event_sync,
        [
            "SYNC_CURSOR_KEY",
            "DEFAULT_SYNC_ENDPOINT",
            "/api/pulse/sync/events",
            "registerSyncInvalidation",
            "invalidateNativeSync",
            "startNativeEventSync",
            "pollNativeSync",
            "loadNativeSyncCursor",
            "saveNativeSyncCursor",
            "subsystemsForSyncEvent",
            "latestEventId",
            "lastFullResyncAt"
        ],
        "core event sync service",
        failures
    )
    expect_all(
        event_sync,
        [
            '"orders", "activity", "notifications"',
            '"marketplace", "seller_inventory", "activity"',
            '"messenger", "activity"',
            '"calls", "activity", "notifications"',
            '"safety", "activity", "notifications"',
            '"verification", "activity", "notifications"',
            '"intelligence", "activity", "notifications"'
        ],
        "invalidation mapping",
        failures
    )
    expect("invalidating" in event_sync and "dedupeSubsystems" in event_sync and "uniqueHandlers" in event_sync, "core service prevents loops and duplicate handler invalidations", failures)
    expect("shouldFallbackToFullRefresh" in event_sync and "full_resync_fallback" in event_sync, "core service has safe full-refresh fallback", failures)

    expect_all(app_nav, ["startNativeEventSync", "registerSyncInvalidation", "invalidateNativeSync", "notification_received"], "navigator sync integration", failures)
    expect_all(activity, ['registerSyncInvalidation("activity"', 'registerSyncInvalidation("notifications"'], "activity inbox integration", failures)
    expect_all(notifications, ['registerSyncInvalidation("notifications"'], "notification center integration", failures)
    expect_all(orders, ['registerSyncInvalidation("orders"'], "buyer orders integration", failures)
    expect_all(marketplace, ['registerSyncInvalidation("marketplace"'], "marketplace integration", failures)
    expect_all(
        seller_store,
        [
            'registerSyncInvalidation("seller_inventory"',
            'registerSyncInvalidation("marketplace"',
            'registerSyncInvalidation("orders"'
        ],
        "seller store integration",
        failures
    )

    expect("not a full WebSocket, SSE, APNs, FCM, or LiveKit realtime system" in report, "report states realtime boundary honestly", failures)
    expect("Production WebView routes and payment/provider logic were not modified." in report, "report documents WebView/provider safety", failures)
    expect("Native Event Sync Foundation" in progress, "progress report updated with event sync foundation", failures)

    if failures:
        print("PulseSoc native event sync foundation audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native event sync foundation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
