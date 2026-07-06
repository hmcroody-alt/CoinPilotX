#!/usr/bin/env python3
"""Audit PulseSoc backend event producer coverage for native cursor sync."""

from __future__ import annotations

import importlib
import json
import os
import re
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
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_event_producer_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
    bot.STRIPE_SECRET_KEY = ""
    bot.stripe.api_key = ""
    bot.push_service._async_push_enabled = lambda: False
    bot.notification_service.send_push_alert = lambda *args, **kwargs: {"ok": True, "status": "skipped", "message": "audit stub"}
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


def run_shared_emitter_validation(failures: list[str]) -> None:
    bot = import_bot_with_temp_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T18:00:00"
    user_id = add_user(cur, "producer-coverage-qa@example.com", "producercoverageqa", "Producer Coverage QA", now)
    bot.notify_user(
        cur,
        user_id,
        "listing_updated",
        "Listing updated",
        "A listing changed.",
        "/pulse/marketplace/42",
        actor_user_id=user_id,
        entity_type="marketplace_listing",
        entity_id="42",
        metadata={"domain": "marketplace"},
    )
    conn.commit()
    cur.execute("SELECT * FROM pulse_notifications WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    note = dict(cur.fetchone() or {})
    metadata = json.loads(note.get("metadata_json") or "{}")
    conn.close()

    for key in ["event_type", "entity_type", "entity_id", "actor_id", "timestamp", "sync_cursor_key"]:
        require(key in metadata, f"shared notify_user metadata missing {key}", failures)
    require(metadata.get("event_type") == "listing_updated", "shared notify_user event_type mismatch", failures)
    require(metadata.get("entity_type") == "marketplace_listing", "shared notify_user entity_type mismatch", failures)
    require(str(metadata.get("entity_id")) == "42", "shared notify_user entity_id mismatch", failures)
    require(str(note.get("type")) == "listing_updated", "pulse_notifications type did not use normalized event type", failures)

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    response = client.get("/api/pulse/sync/events?limit=10")
    require(response.status_code == 200, f"cursor endpoint returned {response.status_code}", failures)
    events = (response.json or {}).get("events") or []
    require(bool(events), "cursor endpoint did not expose shared emitter event", failures)
    event = events[-1]
    require("marketplace" in set(event.get("invalidate") or []), "shared emitter event did not invalidate marketplace", failures)
    require("seller_inventory" in set(event.get("invalidate") or []), "shared emitter event did not invalidate seller inventory", failures)


def route_block(source: str, route_token: str) -> str:
    index = source.find(route_token)
    if index < 0:
        return ""
    next_route = source.find("@webhook_app.route", index + len(route_token))
    return source[index: next_route if next_route > index else len(source)]


def mutation_has_event(block: str) -> bool:
    return any(token in block for token in ["notify_user(", "create_pulse_notification", "notify_crypto_alert", "pulse_emit_event(", "notification_service.send_user_alert"])


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    bot_source = read("bot.py")
    notification_service = read("services/notification_service.py")
    pulse_notifications = read("services/pulsesoc_notification_system.py")
    alert_engine = read("services/alert_engine.py")
    feed_engine = read("services/pulse_feed_engine.py")
    event_sync = read("mobile-native/src/core/eventSync.ts")
    report = read("reports/pulsesoc_native_event_producer_coverage_audit.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "metadata.setdefault(\"event_type\"",
        "metadata.setdefault(\"entity_type\"",
        "metadata.setdefault(\"entity_id\"",
        "metadata.setdefault(\"actor_id\"",
        "metadata.setdefault(\"timestamp\"",
        "metadata.setdefault(\"sync_cursor_key\"",
        "INSERT INTO pulse_notifications",
    ]:
        require(token in bot_source, f"shared notify_user normalization missing token: {token}", failures)
    for token in [
        "/api/pulse/sync/events",
        "pulse_notifications",
        "_pulse_native_sync_invalidates",
        "_pulse_native_sync_safe_metadata",
    ]:
        require(token in bot_source, f"cursor integration missing token: {token}", failures)
    for token in [
        'const DEFAULT_SYNC_ENDPOINT = "/api/pulse/sync/events"',
        "subsystemsForSyncEvent",
        "normalizeEvents",
        "latestEventId",
    ]:
        require(token in event_sync, f"native event sync missing token: {token}", failures)

    producer_sources = {
        "notification_service": notification_service,
        "pulsesoc_notification_system": pulse_notifications,
        "alert_engine": alert_engine,
        "pulse_feed_engine": feed_engine,
        "bot": bot_source,
    }
    producer_tokens = {
        "marketplace": ["marketplace_listings", "marketplace_product_media"],
        "orders": ["seller_transactions", "creator_transactions"],
        "payments": ["stripe.checkout", "checkout_completed", "charge.refunded", "charge.dispute.created"],
        "messaging": ["pulse_messages", "pulse_emit_event", "notify_new_message"],
        "calls": ["notify_missed_call", "call_started", "call_ended"],
        "safety": ["pulse_reports", "block", "mute", "appeal"],
        "verification": ["verification", "teacher_review", "identity"],
        "alerts": ["alert_events", "notify_crypto_alert", "dispatch_alert_event"],
        "notifications": ["pulse_notifications", "create_pulse_notification", "notify_user"],
    }
    covered_domains = 0
    for domain, tokens in producer_tokens.items():
        haystack = "\n".join(producer_sources.values())
        found = [token for token in tokens if token in haystack]
        if found:
            covered_domains += 1
        else:
            warnings.append(f"No producer tokens found for {domain}")
    coverage_pct = round((covered_domains / len(producer_tokens)) * 100)
    require(coverage_pct >= 80, f"event producer domain discovery too low: {coverage_pct}%", failures)

    critical_routes = {
        "seller listing update": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>", methods=["PATCH", "POST"])',
        "seller listing pause": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>/pause", methods=["POST"])',
        "seller listing resume": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>/resume", methods=["POST"])',
        "seller listing delete": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>/delete", methods=["POST", "DELETE"])',
        "seller apply": '@webhook_app.route("/api/pulse/marketplace/seller/apply", methods=["POST"])',
        "checkout": '@webhook_app.route("/api/pulse/payments/checkout", methods=["POST"])',
        "listing create": '@webhook_app.route("/api/pulse/marketplace/listings/create", methods=["POST"])',
        "listing report": '@webhook_app.route("/api/pulse/marketplace/listings/report", methods=["POST"])',
        "message report": '@webhook_app.route("/api/pulse/messages/<int:message_id>/report", methods=["POST"])',
    }
    silent_routes = []
    for label, token in critical_routes.items():
        block = route_block(bot_source, token)
        require(bool(block), f"missing critical route block for {label}", failures)
        if block and not mutation_has_event(block):
            silent_routes.append(label)
    require("seller listing update" in silent_routes, "audit expected to identify seller listing update as current silent route", failures)
    require("listing create" in silent_routes, "audit expected to identify listing create as current silent route", failures)

    for token in [
        "Event Producer Mapping Audit",
        "Event Coverage Gaps",
        "Duplicate / Unsafe Producer Findings",
        "Sync Integration Validation",
        "SYSTEM STATE AUDIT",
        "Event producer coverage completeness %",
        "ONE highest-impact fix ONLY",
        "Wire Marketplace Seller Inventory mutations",
    ]:
        require(token in report, f"event producer coverage report missing token: {token}", failures)
    require("Event Producer Coverage Audit" in progress, "progress report missing event producer coverage section", failures)

    run_shared_emitter_validation(failures)

    if failures:
        print("PulseSoc native event producer coverage audit failed:")
        for failure in failures:
            print(f"- {failure}")
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"- {warning}")
        return 1

    print("PulseSoc native event producer coverage audit passed.")
    print(f"Discovered producer domain coverage: {coverage_pct}%")
    print("Current critical silent mutation routes:")
    for route in silent_routes:
        print(f"- {route}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
