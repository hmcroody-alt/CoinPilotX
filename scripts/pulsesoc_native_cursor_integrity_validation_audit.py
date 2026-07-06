#!/usr/bin/env python3
"""Seeded validation for PulseSoc native cursor sync integrity."""

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
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def import_bot_with_temp_db():
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_cursor_integrity_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
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


def insert_event(cur, user_id: int, event_type: str, target_url: str, entity_type: str, entity_id: str, created_at: str, metadata: dict) -> int:
    cur.execute(
        """
        INSERT INTO pulse_notifications
        (user_id, actor_user_id, type, title, body, entity_type, entity_id, deep_link, target_url,
         is_read, delivery_status, metadata_json, created_at)
        VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, 0, 'created', ?, ?)
        """,
        (
            user_id,
            event_type,
            f"Cursor {event_type}",
            f"Seeded cursor event for {event_type}",
            entity_type,
            entity_id,
            target_url,
            target_url,
            json.dumps(metadata, default=str),
            created_at,
        ),
    )
    return int(cur.lastrowid)


def assert_event_shape(event: dict, failures: list[str]) -> None:
    for key in [
        "id",
        "event_id",
        "event_type",
        "type",
        "domain",
        "category",
        "entity_type",
        "entity_id",
        "target_url",
        "deep_link",
        "created_at",
        "updated_at",
        "invalidate",
        "metadata",
    ]:
        require(key in event, f"event missing schema key {key}", failures)
    require(isinstance(event.get("invalidate"), list), "event invalidate must be a list", failures)
    require(isinstance(event.get("metadata"), dict), "event metadata must be a dict", failures)


def run_seeded_endpoint_validation(failures: list[str]) -> None:
    bot = import_bot_with_temp_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T17:00:00"
    user_id = add_user(cur, "cursor-sync-qa@example.com", "cursorsyncqa", "Cursor Sync QA", now)
    other_user_id = add_user(cur, "cursor-other-qa@example.com", "cursorotherqa", "Cursor Other QA", now)

    seed_plan = [
        ("purchase_created", "/pulse/orders/101", "commerce_order", "101", "2026-07-06T17:05:00", {"domain": "commerce", "order_id": 101, "api_token": "must-not-leak"}),
        ("payment_failed", "/pulse/orders/102", "commerce_order", "102", "2026-07-06T17:04:00", {"domain": "commerce", "order_id": 102}),
        ("refund_issued", "/pulse/orders/103", "commerce_order", "103", "2026-07-06T17:06:00", {"domain": "commerce", "order_id": 103}),
        ("listing_updated", "/pulse/marketplace/201", "marketplace_listing", "201", "2026-07-06T17:07:00", {"domain": "marketplace", "listing_id": 201}),
        ("message_received", "/pulse/messages/301", "conversation", "301", "2026-07-06T17:08:00", {"domain": "messenger", "password_hint": "must-not-leak"}),
        ("call_started", "/pulse/calls/401", "call", "401", "2026-07-06T17:09:00", {"domain": "calls"}),
        ("report_submitted", "/pulse/safety/reports/501", "report", "501", "2026-07-06T17:10:00", {"domain": "safety"}),
        ("verification_approved", "/pulse/verification", "verification", "601", "2026-07-06T17:11:00", {"domain": "verification"}),
        ("premium_subscription_updated", "/pulse/premium", "premium", "701", "2026-07-06T17:12:00", {"domain": "premium"}),
        ("intelligence_alert", "/pulse/alerts/801", "alert", "801", "2026-07-06T17:13:00", {"domain": "intelligence", "secret_note": "must-not-leak"}),
    ]
    ids = [insert_event(cur, user_id, *event) for event in seed_plan]
    other_id = insert_event(cur, other_user_id, "purchase_created", "/pulse/orders/999", "commerce_order", "999", "2026-07-06T17:14:00", {"domain": "commerce"})
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    unauth = client.get("/api/pulse/sync/events")
    require(unauth.status_code == 401, f"unauthenticated sync returned {unauth.status_code}, expected 401", failures)
    require((unauth.json or {}).get("events") == [], "unauthenticated sync should not return events", failures)

    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    initial = client.get("/api/pulse/sync/events?limit=5")
    require(initial.status_code == 200, f"initial sync returned {initial.status_code}", failures)
    initial_payload = initial.json or {}
    initial_events = initial_payload.get("events") or []
    require(len(initial_events) == 5, f"initial bounded sync expected 5 events, got {len(initial_events)}", failures)
    require(str(initial_payload.get("latest_event_id")) == str(max(ids)), "initial sync latest_event_id did not advance to user max id", failures)
    require(str(initial_payload.get("latestEventId")) == str(max(ids)), "initial sync latestEventId did not advance to user max id", failures)
    require(all(int(event["event_id"]) in ids for event in initial_events), "initial sync leaked another user's event", failures)
    require(str(other_id) not in {str(event.get("event_id")) for event in initial_events}, "initial sync leaked other-user event id", failures)
    for event in initial_events:
        assert_event_shape(event, failures)
    initial_ids = [int(event["event_id"]) for event in initial_events]
    require(initial_ids == sorted(initial_ids), "initial sync did not return events in deterministic cursor order", failures)
    require(len(initial_ids) == len(set(initial_ids)), "initial sync returned duplicate event ids", failures)

    delta = client.get(f"/api/pulse/sync/events?after_id={ids[3]}&limit=100")
    require(delta.status_code == 200, f"delta sync returned {delta.status_code}", failures)
    delta_events = (delta.json or {}).get("events") or []
    delta_ids = [int(event["event_id"]) for event in delta_events]
    require(delta_ids == [event_id for event_id in ids if event_id > ids[3]], f"delta sync returned wrong ids: {delta_ids}", failures)
    require(delta_ids == sorted(delta_ids), "delta sync did not preserve cursor ordering", failures)

    replay = client.get("/api/pulse/sync/events?after=2026-07-06T17:08:00&limit=100")
    require(replay.status_code == 200, f"timestamp replay returned {replay.status_code}", failures)
    replay_events = (replay.json or {}).get("events") or []
    replay_types = {event.get("type") for event in replay_events}
    require("call_started" in replay_types and "intelligence_alert" in replay_types, "timestamp replay missed accumulated online events", failures)
    require("payment_failed" not in replay_types, "timestamp replay included pre-cursor stale event", failures)

    invalid = client.get("/api/pulse/sync/events?after_id=not-a-number&limit=2")
    require(invalid.status_code == 200, f"invalid cursor fallback returned {invalid.status_code}", failures)
    require(len((invalid.json or {}).get("events") or []) == 2, "invalid cursor fallback did not honor safe bounded sync", failures)

    full = client.get("/api/pulse/sync/events?limit=100")
    require(full.status_code == 200, f"full sync returned {full.status_code}", failures)
    full_events = (full.json or {}).get("events") or []
    full_types = {event.get("type"): event for event in full_events}
    expectations = {
        "purchase_created": {"orders", "activity", "notifications"},
        "listing_updated": {"marketplace", "seller_inventory", "activity"},
        "message_received": {"messenger", "activity"},
        "call_started": {"calls", "activity", "notifications"},
        "report_submitted": {"safety", "activity", "notifications"},
        "verification_approved": {"verification", "activity", "notifications"},
        "premium_subscription_updated": {"premium", "activity", "notifications"},
        "intelligence_alert": {"intelligence", "activity", "notifications"},
    }
    for event_type, expected_invalidates in expectations.items():
        event = full_types.get(event_type)
        require(bool(event), f"full sync missing event type {event_type}", failures)
        if event:
            require(expected_invalidates.issubset(set(event.get("invalidate") or [])), f"{event_type} invalidates {event.get('invalidate')} missing {expected_invalidates}", failures)

    metadata_blob = json.dumps([event.get("metadata") or {} for event in full_events], default=str).lower()
    require("must-not-leak" not in metadata_blob, "sensitive metadata value leaked through cursor endpoint", failures)
    require("api_token" not in metadata_blob, "sensitive metadata key api_token leaked through cursor endpoint", failures)
    require("password_hint" not in metadata_blob, "sensitive metadata key password_hint leaked through cursor endpoint", failures)
    require("secret_note" not in metadata_blob, "sensitive metadata key secret_note leaked through cursor endpoint", failures)


def main() -> int:
    failures: list[str] = []

    bot_source = read("bot.py")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    report = read("reports/pulsesoc_native_cursor_integrity_validation.md")
    autonomous = read("reports/pulsesoc_native_autonomous_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        '@webhook_app.route("/api/pulse/sync/events", methods=["GET"])',
        "after_id",
        "latest_event_id",
        "latestEventId",
        "_pulse_native_sync_safe_metadata",
        "_pulse_native_sync_invalidates",
    ]:
        require(token in bot_source, f"backend cursor endpoint missing token: {token}", failures)
    for token in [
        'const DEFAULT_SYNC_ENDPOINT = "/api/pulse/sync/events"',
        "latestEventId",
        "lastEventAt",
        "shouldFallbackToFullRefresh",
        "normalizeEvents",
        "subsystemsForSyncEvent",
    ]:
        require(token in event_sync, f"native sync client missing token: {token}", failures)
    require("WebSocket" not in event_sync and "EventSource" not in event_sync, "native sync must remain polling-first", failures)
    for token in [
        "Cursor Integrity Validation",
        "Cross-System Sync Verification",
        "Event Ordering Chaos Simulation",
        "Offline To Online Recovery",
        "Backend Contract Validation",
        "SYSTEM STATE AUDIT",
        "ONE highest-impact fix ONLY",
    ]:
        require(token in report, f"cursor validation report missing token: {token}", failures)
    require("Seeded Event Cursor QA Hardening" in autonomous, "autonomous report did not select seeded cursor QA next", failures)
    require("Event Producer Coverage Audit" in progress, "progress report missing next highest-impact fix", failures)

    run_seeded_endpoint_validation(failures)

    if failures:
        print("PulseSoc native cursor integrity validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native cursor integrity validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
