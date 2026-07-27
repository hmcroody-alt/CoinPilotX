#!/usr/bin/env python3
"""Validate seller inventory event emission for native cursor sync."""

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


def route_block(source: str, route_token: str) -> str:
    index = source.find(route_token)
    if index < 0:
        return ""
    next_route = source.find("@webhook_app.route", index + len(route_token))
    return source[index: next_route if next_route > index else len(source)]


def import_bot_with_temp_db():
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_seller_inventory_events_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
    bot.STRIPE_SECRET_KEY = ""
    bot.stripe.api_key = ""
    if hasattr(bot, "push_service"):
        bot.push_service._async_push_enabled = lambda: False
    if hasattr(bot, "notification_service"):
        bot.notification_service.send_push_alert = lambda *args, **kwargs: {
            "ok": True,
            "status": "skipped",
            "message": "audit stub",
        }
    bot.init_db()
    return bot


def add_user(cur, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        ("seller-inventory-events-qa@example.com", "sellerinventoryeventsqa", "Seller Inventory Events QA", now, now),
    )
    return int(cur.lastrowid)


def complete_seller_application(bot, user_id: int, now: str) -> None:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    application = bot.seller_lifecycle.get_application(cur, user_id)
    if not application:
        application_id = bot.seller_lifecycle.create_draft(cur, user_id, source="native")
        application = bot.seller_lifecycle.get_application_by_id(cur, application_id)
    application_id = int(application.get("id") or 0)
    fields = bot.seller_lifecycle.merge_fields(
        bot.seller_lifecycle.applicant_fields(application),
        {
            "seller_type": "creator",
            "seller_intent": ["Digital Products", "Courses"],
            "full_name": "Seller Inventory Events QA",
            "country": "United States",
            "state_region": "CA",
            "email": "seller-inventory-events-qa@example.com",
            "phone": "+15555550123",
            "display_name": "Seller Inventory Events QA",
            "website": "https://pulsesoc.com",
            "years_experience": "3",
            "business_description": "A QA seller profile used only to verify that native marketplace seller inventory events reach the sync cursor.",
            "sold_online_before": "yes",
            "banned_elsewhere": "no",
            "guaranteed_profits": "no",
            "comply_rules": "yes",
            "understand_claims": "yes",
            "marketplace_rules": "yes",
            "anti_scam_agreement": "yes",
            "no_profit_guarantees": "yes",
        },
    )
    for index, document_type in enumerate(bot.seller_lifecycle.REQUIRED_DOCUMENTS):
        cur.execute(
            """
            INSERT INTO marketplace_merchant_documents
                (application_id, user_id, document_type, original_filename, stored_path, mime_type,
                 file_size, private_access, scan_status, review_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'image/jpeg', 2048, 1, 'passed', 'pending', ?, ?)
            """,
            (
                application_id,
                user_id,
                document_type,
                f"{document_type}.jpg",
                f"/tmp/seller-inventory-events/{application_id}-{index}.jpg",
                now,
                now,
            ),
        )
    documents = bot.seller_lifecycle.documents_for(cur, application_id)
    bot.seller_lifecycle.save_draft(cur, application_id, fields, documents)
    conn.commit()
    conn.close()


def approve_seller_for_inventory(bot, user_id: int) -> None:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    application = bot.seller_lifecycle.get_application(cur, user_id)
    if not application:
        conn.close()
        raise AssertionError("seller application was not created")
    bot.seller_lifecycle.apply_transition(
        cur,
        application,
        bot.seller_lifecycle.APPROVED,
        actor_type=bot.seller_lifecycle.ADMIN,
        actor_id=99,
        reason="QA approval for seller inventory event emission.",
    )
    conn.commit()
    conn.close()


def assert_ok(response, label: str, failures: list[str]) -> dict:
    data = response.get_json(silent=True) or {}
    require(response.status_code < 400, f"{label} returned HTTP {response.status_code}: {data}", failures)
    require(data.get("ok") is True, f"{label} did not return ok=true: {data}", failures)
    return data


def run_seeded_inventory_flow(failures: list[str]) -> None:
    bot = import_bot_with_temp_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T19:00:00"
    user_id = add_user(cur, now)
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    apply_data = assert_ok(
        client.post(
            "/api/pulse/marketplace/seller/apply",
            json={"display_name": "Seller Inventory Events QA", "bio": "QA seller for native event emission validation."},
        ),
        "seller application",
        failures,
    )
    require(
        "Draft saved" in apply_data.get("message", ""),
        "legacy seller apply response message changed unexpectedly",
        failures,
    )
    complete_seller_application(bot, user_id, now)
    submit_data = assert_ok(
        client.post("/api/pulse/seller/application/submit"),
        "seller application submit",
        failures,
    )
    require("Application sent for review" in submit_data.get("message", ""), "seller submit response message changed unexpectedly", failures)
    approve_seller_for_inventory(bot, user_id)

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO marketplace_product_media
        (product_id, merchant_id, media_type, media_url, thumbnail_url, position, is_cover, mime_type, file_size, moderation_status, created_at)
        VALUES (0, ?, 'image', ?, ?, 0, 1, 'image/jpeg', 2048, 'pending_review', ?)
        """,
        (user_id, "https://cdn.pulsesoc.test/marketplace/cover.jpg", "https://cdn.pulsesoc.test/marketplace/cover-thumb.jpg", now),
    )
    media_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    create_data = assert_ok(
        client.post(
            "/api/pulse/marketplace/listings/create",
            json={
                "title": "Native Seller Event Listing",
                "description": "A seeded listing used to prove native cursor-visible seller inventory events.",
                "short_description": "Seeded event listing",
                "category": "Education",
                "price_label": "$12.00",
                "currency": "USD",
                "quantity": 3,
                "product_type": "digital",
                "media_ids": [media_id],
            },
        ),
        "listing create",
        failures,
    )
    listing_id = int(create_data.get("listing_id") or 0)
    require(listing_id > 0, "listing create did not return listing_id", failures)

    assert_ok(
        client.patch(
            f"/api/pulse/marketplace/seller/listings/{listing_id}",
            json={
                "title": "Native Seller Event Listing Updated",
                "description": "Updated seeded listing proving update event emission.",
                "short_description": "Updated seeded listing",
                "category": "Education",
                "price_label": "$14.00",
                "quantity": 4,
            },
        ),
        "listing update",
        failures,
    )
    assert_ok(client.post(f"/api/pulse/marketplace/seller/listings/{listing_id}/pause"), "listing pause", failures)
    assert_ok(client.post(f"/api/pulse/marketplace/seller/listings/{listing_id}/resume"), "listing resume", failures)
    assert_ok(client.post(f"/api/pulse/marketplace/seller/listings/{listing_id}/delete"), "listing delete", failures)

    sync_response = client.get("/api/pulse/sync/events?limit=100")
    require(sync_response.status_code == 200, f"sync cursor returned HTTP {sync_response.status_code}", failures)
    events = (sync_response.get_json(silent=True) or {}).get("events") or []
    by_type = {event.get("event_type"): event for event in events}
    required_types = {
        "seller_application_submitted",
        "seller_listing_created",
        "seller_listing_updated",
        "seller_listing_paused",
        "seller_listing_resumed",
        "seller_listing_deleted",
    }
    missing = sorted(required_types - set(by_type))
    require(not missing, f"sync cursor missing seller inventory events: {missing}", failures)

    for event_type in required_types:
        event = by_type.get(event_type) or {}
        metadata = event.get("metadata") or {}
        invalidates = set(event.get("invalidate") or metadata.get("invalidates") or [])
        for key in ["event_type", "entity_type", "entity_id", "actor_id", "timestamp", "sync_cursor_key"]:
            require(key in metadata, f"{event_type} metadata missing {key}", failures)
        require(metadata.get("domain") == "marketplace", f"{event_type} missing marketplace domain", failures)
        require("seller_inventory" in invalidates, f"{event_type} does not invalidate seller_inventory", failures)
        require("marketplace" in invalidates, f"{event_type} does not invalidate marketplace", failures)
        require("activity" in invalidates, f"{event_type} does not invalidate activity", failures)
        require("notifications" in invalidates, f"{event_type} does not invalidate notifications", failures)
        if event_type.startswith("seller_listing_"):
            require(str(event.get("entity_id")) == str(listing_id), f"{event_type} entity_id did not match listing", failures)
            require("orders" in invalidates, f"{event_type} should invalidate buyer orders where relevant", failures)


def main() -> int:
    failures: list[str] = []
    bot_source = read("bot.py")
    report = read("reports/pulsesoc_native_seller_inventory_event_emission.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "def pulse_emit_marketplace_inventory_event",
        "seller_application_submitted",
        "seller_application_changed",
        "seller_listing_created",
        "seller_listing_updated",
        "seller_listing_paused",
        "seller_listing_resumed",
        "seller_listing_deleted",
        "seller_listing_review_changed",
        "sync_cursor_key",
    ]:
        require(token in bot_source, f"bot.py missing seller inventory event token: {token}", failures)

    route_tokens = {
        "seller application submit": '@webhook_app.route("/api/pulse/seller/application/submit", methods=["POST"])',
        "listing create": '@webhook_app.route("/api/pulse/marketplace/listings/create", methods=["POST"])',
        "listing update": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>", methods=["PATCH", "POST"])',
        "listing pause": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>/pause", methods=["POST"])',
        "listing resume": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>/resume", methods=["POST"])',
        "listing delete": '@webhook_app.route("/api/pulse/marketplace/seller/listings/<int:listing_id>/delete", methods=["POST", "DELETE"])',
        "merchant admin review": '@webhook_app.route("/admin/merchant-applications", methods=["GET", "POST"])',
        "listing admin review": '@webhook_app.route("/admin/marketplace-command", methods=["GET", "POST"])',
    }
    for label, token in route_tokens.items():
        block = route_block(bot_source, token)
        require(bool(block), f"missing route block for {label}", failures)
        if label == "merchant admin review":
            action_block = route_block(bot_source, "def admin_seller_application_action(admin):")
            require("admin_seller_application_action(admin)" in block, f"{label} route does not delegate to seller action handler", failures)
            require("pulse_emit_marketplace_inventory_event(" in action_block, f"{label} action handler does not emit seller inventory event", failures)
        else:
            require("pulse_emit_marketplace_inventory_event(" in block, f"{label} route does not emit seller inventory event", failures)

    for token in [
        "Seller inventory event coverage %",
        "Remaining silent mutation paths",
        "Event visibility through sync cursor",
        "Activity/Marketplace/Seller Store consistency impact",
        "ONE highest-impact fix ONLY",
    ]:
        require(token in report, f"seller inventory event report missing token: {token}", failures)
    require("Seller Inventory Event Emission Hardening" in progress, "progress report missing seller inventory event hardening section", failures)

    run_seeded_inventory_flow(failures)

    if failures:
        print("PulseSoc seller inventory event emission audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc seller inventory event emission audit passed.")
    print("- Seller application/create/update/pause/resume/delete emit cursor-visible events.")
    print("- Events include normalized metadata and invalidate Activity, Notifications, Marketplace, Seller Inventory, and Orders where relevant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
