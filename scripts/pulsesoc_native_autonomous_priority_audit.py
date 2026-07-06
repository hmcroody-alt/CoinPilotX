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

    bot = read("bot.py")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    report = read("reports/pulsesoc_native_autonomous_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    expect_all(
        bot,
        [
            '@webhook_app.route("/api/pulse/sync/events", methods=["GET"])',
            "def api_pulse_native_sync_events",
            "api_account_user()",
            "pulse_notifications",
            "after_id",
            "latest_event_id",
            "latestEventId",
            "_pulse_native_sync_invalidates",
            "_pulse_native_sync_safe_metadata",
            "Cache-Control",
            "no-store, max-age=0",
        ],
        "server event cursor endpoint",
        failures,
    )
    expect("password" in bot and "credential" in bot and "_pulse_native_sync_safe_metadata" in bot, "server endpoint filters sensitive metadata keys", failures)
    expect("notify_user(" in bot and "INSERT INTO pulse_notifications" in bot, "endpoint reuses existing notification truth source", failures)

    expect_all(
        event_sync,
        [
            'const DEFAULT_SYNC_ENDPOINT = "/api/pulse/sync/events"',
            "latestEventId",
            "lastEventAt",
            "registerSyncInvalidation",
            "invalidateNativeSync",
            "shouldFallbackToFullRefresh",
            "subsystemsForSyncEvent",
        ],
        "native event sync client",
        failures,
    )
    expect("WebSocket" not in event_sync and "EventSource" not in event_sync, "native sync remains polling-first", failures)

    expect_all(
        report,
        [
            "=== PULSESOC SYSTEM DASHBOARD ===",
            "OVERALL PROGRESS %",
            "SUBSYSTEM HEALTH TABLE",
            "CURRENTLY WEAKEST SYSTEM",
            "WHY IT IS WEAK",
            "WHAT WAS FIXED THIS RUN",
            "NEXT AUTO-SELECTED ACTION",
            "SYSTEM HEALTH SCORE",
            "Event Sync / Real-time consistency",
            "Why This Was The Highest-value Next Action For System Completion",
        ],
        "autonomous progress report",
        failures,
    )
    for subsystem in [
        "Marketplace",
        "Seller System",
        "Buyer Orders",
        "Activity Inbox",
        "Messaging",
        "Calls",
        "Notifications",
        "Event Sync",
        "Trust/Safety",
        "Verification",
        "Media/Capture",
        "Creator Tools",
    ]:
        expect(subsystem in report, f"dashboard missing subsystem: {subsystem}", failures)

    expect("Autonomous Priority System" in progress, "master progress report includes autonomous priority update", failures)
    expect("/api/pulse/sync/events" in progress, "master progress report records event cursor endpoint", failures)

    if failures:
        print("PulseSoc native autonomous priority audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native autonomous priority audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
