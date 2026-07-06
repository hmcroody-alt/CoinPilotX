#!/usr/bin/env python3
"""Validate PulseSoc native cursor replay and multi-session ordering."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def import_bot_with_temp_db():
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_cursor_multidevice_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ["LIVEKIT_URL"] = "wss://livekit.audit.invalid"
    os.environ["LIVEKIT_API_KEY"] = "audit_key"
    os.environ["LIVEKIT_API_SECRET"] = "audit_secret"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
    if hasattr(bot, "push_service"):
        bot.push_service._async_push_enabled = lambda: False
    if hasattr(bot, "notification_service"):
        bot.notification_service.send_push_alert = lambda *args, **kwargs: {
            "ok": True,
            "status": "skipped",
            "message": "audit stub",
        }
    bot.STRIPE_SECRET_KEY = ""
    bot.stripe.api_key = ""
    bot.init_db()
    return bot


def add_user(cur, email: str, username: str, display_name: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        (email, username, display_name, now, now),
    )
    return int(cur.lastrowid)


def client_for(bot, user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = int(user_id)
    return client


def fetch_events(client, after_id: int | str = 0, limit: int = 100) -> dict:
    suffix = f"?limit={limit}"
    if after_id:
        suffix += f"&after_id={after_id}"
    response = client.get(f"/api/pulse/sync/events{suffix}")
    return {"status": response.status_code, "payload": response.get_json(silent=True) or {}}


def event_ids(payload: dict) -> list[int]:
    return [int(event.get("event_id") or event.get("id") or 0) for event in payload.get("events") or []]


def event_types(payload: dict) -> list[str]:
    return [str(event.get("event_type") or event.get("type") or "") for event in payload.get("events") or []]


def emit(bot, cur, user_id: int, event_type: str, entity_type: str, entity_id: str, target_url: str, actor_id: int, metadata: dict | None = None) -> int:
    before = cur.execute("SELECT COALESCE(MAX(id), 0) AS id FROM pulse_notifications WHERE user_id=?", (user_id,)).fetchone()
    before_id = int((before["id"] if hasattr(before, "keys") else before[0]) or 0)
    bot.notify_user(
        cur,
        user_id,
        event_type,
        f"QA {event_type}",
        f"Seeded multi-device event for {event_type}.",
        target_url,
        actor_user_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata={"domain": metadata.get("domain") if metadata else event_type, **(metadata or {})},
    )
    after = cur.execute("SELECT COALESCE(MAX(id), 0) AS id FROM pulse_notifications WHERE user_id=?", (user_id,)).fetchone()
    after_id = int((after["id"] if hasattr(after, "keys") else after[0]) or 0)
    return after_id if after_id > before_id else before_id


def run_seeded_multidevice_checks(bot, failures: list[str]) -> None:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T19:45:00"
    buyer_id = add_user(cur, "cursor-buyer@example.com", "cursorbuyer", "Cursor Buyer", now)
    seller_id = add_user(cur, "cursor-seller@example.com", "cursorseller", "Cursor Seller", now)
    actor_id = seller_id

    seed_ids = [
        emit(bot, cur, buyer_id, "purchase_created", "commerce_order", "9001", "/pulse/orders/9001", actor_id, {"domain": "commerce"}),
        emit(bot, cur, buyer_id, "payment_failed", "commerce_order", "9002", "/pulse/orders/9002", actor_id, {"domain": "commerce"}),
        emit(bot, cur, buyer_id, "listing_updated", "marketplace_listing", "7001", "/pulse/marketplace/7001", actor_id, {"domain": "marketplace"}),
        emit(bot, cur, buyer_id, "message_received", "conversation", "3001", "/pulse/messages/3001", actor_id, {"domain": "messenger"}),
        emit(bot, cur, buyer_id, "call_started", "call", "4001", "/pulse/calls/4001", actor_id, {"domain": "calls"}),
        emit(bot, cur, buyer_id, "report_submitted", "report", "5001", "/pulse/safety/reports/5001", actor_id, {"domain": "safety"}),
    ]
    seller_event_id = emit(bot, cur, seller_id, "order_paid", "commerce_order", "9001", "/pulse/seller-store", buyer_id, {"domain": "commerce", "role": "seller"})
    conn.commit()

    buyer_a = client_for(bot, buyer_id)
    buyer_b = client_for(bot, buyer_id)
    seller_client = client_for(bot, seller_id)

    first_a = fetch_events(buyer_a)
    first_b = fetch_events(buyer_b)
    require(first_a["status"] == 200, f"buyer session A returned {first_a['status']}", failures)
    require(first_b["status"] == 200, f"buyer session B returned {first_b['status']}", failures)
    ids_a = event_ids(first_a["payload"])
    ids_b = event_ids(first_b["payload"])
    require(ids_a == ids_b, f"same-user sessions diverged: {ids_a} != {ids_b}", failures)
    require(ids_a == sorted(ids_a), "same-user initial replay is not monotonic by cursor id", failures)
    require(len(ids_a) == len(set(ids_a)), "same-user initial replay returned duplicate event ids", failures)
    require(set(seed_ids).issubset(set(ids_a)), "same-user initial replay missed seeded event ids", failures)

    latest_cursor = int(first_a["payload"].get("latest_event_id") or first_a["payload"].get("latestEventId") or 0)
    require(latest_cursor == max(seed_ids), f"buyer latest cursor {latest_cursor} did not equal max event {max(seed_ids)}", failures)

    replay_a = fetch_events(buyer_a, after_id=seed_ids[1])
    replay_b = fetch_events(buyer_b, after_id=seed_ids[1])
    require(event_ids(replay_a["payload"]) == event_ids(replay_b["payload"]), "repeated delta replay is not deterministic across sessions", failures)
    require(all(event_id > seed_ids[1] for event_id in event_ids(replay_a["payload"])), "delta replay included pre-cursor event", failures)

    # Delayed event with an old semantic timestamp must still converge because id ordering is authoritative.
    delayed_id = emit(
        bot,
        cur,
        buyer_id,
        "refund_issued",
        "commerce_order",
        "9001",
        "/pulse/orders/9001",
        seller_id,
        {"domain": "commerce", "created_at_override": "2026-07-01T00:00:00"},
    )
    # Duplicate producer replay should be represented as two durable rows, but cursor replay must not loop or reorder them.
    duplicate_one = emit(bot, cur, buyer_id, "notification_delivered", "notification", "dupe-1", "/pulse/activity", seller_id, {"domain": "notifications", "sync_cursor_key": "duplicate-delivery-demo"})
    duplicate_two = emit(bot, cur, buyer_id, "notification_delivered", "notification", "dupe-1", "/pulse/activity", seller_id, {"domain": "notifications", "sync_cursor_key": "duplicate-delivery-demo"})
    conn.commit()

    recovery = fetch_events(buyer_a, after_id=latest_cursor)
    recovery_ids = event_ids(recovery["payload"])
    require(recovery_ids == sorted(recovery_ids), "offline to online recovery is not monotonic", failures)
    require(delayed_id in recovery_ids, "offline to online recovery missed delayed refund event", failures)
    require(duplicate_one in recovery_ids and duplicate_two in recovery_ids, "duplicate delivery rows were not cursor-visible", failures)
    require(len(recovery_ids) == len(set(recovery_ids)), "recovery replay duplicated event ids within one response", failures)

    second_recovery = fetch_events(buyer_b, after_id=latest_cursor)
    require(event_ids(second_recovery["payload"]) == recovery_ids, "multi-session recovery replay diverged", failures)

    seller_payload = fetch_events(seller_client)
    seller_ids = event_ids(seller_payload["payload"])
    seller_types = event_types(seller_payload["payload"])
    require(seller_payload["status"] == 200, f"seller session returned {seller_payload['status']}", failures)
    require(seller_event_id in seller_ids, "seller session missed seller order event", failures)
    require("order_paid" in seller_types, "seller session missed order_paid type", failures)
    require(not set(seed_ids).intersection(set(seller_ids)), "seller session leaked buyer-only events", failures)

    all_buyer = fetch_events(buyer_a)
    invalidates = {
        event.get("event_type"): set(event.get("invalidate") or [])
        for event in all_buyer["payload"].get("events") or []
    }
    expectations = {
        "purchase_created": {"orders", "activity", "notifications"},
        "payment_failed": {"orders", "activity", "notifications"},
        "refund_issued": {"orders", "activity", "notifications"},
        "listing_updated": {"marketplace", "seller_inventory", "activity"},
        "message_received": {"messenger", "activity"},
        "call_started": {"calls", "activity", "notifications"},
        "report_submitted": {"safety", "activity", "notifications"},
        "notification_delivered": {"activity", "notifications"},
    }
    for event_type, expected in expectations.items():
        require(expected.issubset(invalidates.get(event_type, set())), f"{event_type} missing invalidation {expected}; got {invalidates.get(event_type)}", failures)

    invalid_cursor = fetch_events(buyer_a, after_id="not-a-number", limit=3)
    require(invalid_cursor["status"] == 200, f"invalid cursor fallback returned {invalid_cursor['status']}", failures)
    require(len(event_ids(invalid_cursor["payload"])) == 3, "invalid cursor fallback did not stay bounded", failures)

    conn.close()


def main() -> int:
    failures: list[str] = []
    bot_source = read("bot.py")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    report = read("reports/pulsesoc_native_cursor_multidevice_ordering.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "/api/pulse/sync/events",
        "after_id",
        "latest_event_id",
        "_pulse_native_sync_invalidates",
        "_pulse_native_sync_safe_metadata",
    ]:
        require(token in bot_source, f"backend cursor endpoint missing token: {token}", failures)
    for token in [
        "pollNativeSync",
        "loadNativeSyncCursor",
        "saveNativeSyncCursor",
        "normalizeEvents",
        "subsystemsForSyncEvent",
    ]:
        require(token in event_sync, f"native event sync missing token: {token}", failures)
    require("WebSocket" not in event_sync and "EventSource" not in event_sync, "native sync must remain polling-first", failures)
    for token in [
        "Cursor replay correctness %",
        "Multi-device ordering confidence %",
        "Systems that converge correctly",
        "ONE highest-impact fix ONLY",
    ]:
        require(token in report, f"multi-device cursor report missing token: {token}", failures)
    require("Real-time Cursor Replay + Multi-Device Ordering Validation" in progress, "progress report missing multi-device cursor section", failures)

    bot = import_bot_with_temp_db()
    run_seeded_multidevice_checks(bot, failures)

    if failures:
        print("PulseSoc native cursor multi-device ordering audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native cursor multi-device ordering audit passed.")
    print("- Same-user multi-session cursor replay is deterministic.")
    print("- Buyer/seller sessions remain isolated while converging on shared order state.")
    print("- Delayed, duplicate, and offline-to-online event replay stays monotonic and bounded.")
    print("- Android-specific tooling remains intentionally out of scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
