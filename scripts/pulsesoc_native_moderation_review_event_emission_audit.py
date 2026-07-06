#!/usr/bin/env python3
"""Validate unified moderation review event emission for native cursor sync."""

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
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_moderation_review_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ["LIVEKIT_URL"] = "wss://livekit.audit.invalid"
    os.environ["LIVEKIT_API_KEY"] = "audit_key"
    os.environ["LIVEKIT_API_SECRET"] = "audit_secret"
    bot = importlib.import_module("bot")
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


def set_session(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["account_user_id"] = int(user_id)


def sync_events(client, user_id: int, failures: list[str]) -> list[dict]:
    set_session(client, user_id)
    response = client.get("/api/pulse/sync/events?limit=200")
    require(response.status_code == 200, f"sync cursor returned HTTP {response.status_code}", failures)
    return (response.get_json(silent=True) or {}).get("events") or []


def require_event(events: list[dict], event_type: str, failures: list[str], entity_type: str = "") -> dict:
    matches = [
        event
        for event in events
        if event.get("event_type") == event_type
        and (not entity_type or event.get("entity_type") == entity_type)
    ]
    require(bool(matches), f"sync cursor missing {event_type}{' for ' + entity_type if entity_type else ''}", failures)
    event = matches[-1] if matches else {}
    metadata = event.get("metadata") or {}
    for key in ["event_type", "entity_type", "entity_id", "actor_id", "timestamp", "sync_cursor_key"]:
        require(key in metadata, f"{event_type} metadata missing {key}", failures)
    require(metadata.get("moderation_review") is True, f"{event_type} missing moderation_review marker", failures)
    return event


def seed_user(cur, email: str, username: str, display_name: str) -> int:
    now = "2026-07-06T23:30:00"
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        (email, username, display_name, now, now),
    )
    return int(cur.lastrowid)


def run_seeded_checks(bot, failures: list[str]) -> None:
    client = bot.webhook_app.test_client()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    reporter_id = seed_user(cur, "moderation-reporter@example.com", "modreporter", "Moderation Reporter")
    target_id = seed_user(cur, "moderation-target@example.com", "modtarget", "Moderation Target")
    cur.execute(
        """
        INSERT INTO moderation_cases
        (target_type, target_id, reporter_user_id, status, priority, reason, notes, created_at)
        VALUES ('post', '77', ?, 'open', 'high', 'Audit moderation case', '', '2026-07-06T23:30:00')
        """,
        (reporter_id,),
    )
    case_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_posts
        (user_id, post_type, body, visibility, moderation_status, created_at, updated_at)
        VALUES (?, 'text', 'Audit moderation post', 'public', 'needs_review', '2026-07-06T23:30:00', '2026-07-06T23:30:00')
        """,
        (target_id,),
    )
    post_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_reports
        (reporter_user_id, target_type, target_id, reason, details, status, created_at, updated_at)
        VALUES (?, 'marketplace_listing', 501, 'Audit marketplace report', 'Audit details', 'open', '2026-07-06T23:30:00', '2026-07-06T23:30:00')
        """,
        (reporter_id,),
    )
    report_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    admin = {"id": 9101, "user_id": 9101, "email": "moderation-admin@example.com", "role": "owner", "status": "active"}
    with bot.webhook_app.test_request_context("/admin/departments/moderation", method="POST"):
        bot.apply_department_action("moderation", "resolve_case", case_id, "Resolved by audit.", admin)
    require_event(sync_events(client, reporter_id, failures), "moderation_case_resolved", failures, "moderation_case")

    with bot.webhook_app.test_request_context("/admin/departments/moderation", method="POST"):
        bot.apply_department_action("moderation", "dismiss_case", case_id, "Dismissed by audit.", admin)
    require_event(sync_events(client, reporter_id, failures), "moderation_case_dismissed", failures, "moderation_case")

    with bot.webhook_app.test_request_context("/admin/departments/trust-safety", method="POST"):
        bot.apply_department_action("trust-safety", "dismiss_report", report_id, "Dismiss report by audit.", admin)
    require_event(sync_events(client, reporter_id, failures), "marketplace_report_resolved", failures, "report")

    with bot.webhook_app.test_request_context("/admin/departments/moderation", method="POST"):
        bot.apply_department_action("moderation", "restore_content", post_id, "Restore content by audit.", admin)
    require_event(sync_events(client, target_id, failures), "content_restored", failures, "post")

    with bot.webhook_app.test_request_context("/admin/departments/moderation", method="POST"):
        bot.apply_department_action("moderation", "restrict_user", target_id, "Restrict user by audit.", admin)
    require_event(sync_events(client, target_id, failures), "user_restriction_updated", failures, "user")


def main() -> int:
    failures: list[str] = []
    bot_source = read("bot.py")
    report = read("reports/pulsesoc_native_moderation_review_event_emission.md")
    progress = read("reports/pulsesoc_native_progress.md")
    producer_audit = read("scripts/pulsesoc_native_event_producer_coverage_audit.py")

    for token in [
        "def pulse_emit_moderation_review_event",
        "moderation_case_updated",
        "moderation_case_resolved",
        "moderation_case_dismissed",
        "moderation_action_applied",
        "content_restored",
        "content_removed",
        "user_warning_issued",
        "user_restriction_updated",
        "marketplace_report_resolved",
        "content_report_resolved",
    ]:
        require(token in bot_source, f"bot.py missing moderation review token: {token}", failures)

    for token in [
        "Moderation review event coverage %",
        "Full native walkthrough coverage %",
        "Event visibility through sync cursor",
        "ONE highest-impact fix ONLY",
        "Do not use Chrome Incognito",
    ]:
        require(token in report, f"moderation review report missing token: {token}", failures)
    require("Unified Moderation Review Event Emission" in progress, "progress report missing moderation review section", failures)
    require("pulse_emit_moderation_review_event" in producer_audit, "producer audit missing moderation review helper recognition", failures)

    bot = import_bot_with_temp_db()
    run_seeded_checks(bot, failures)

    if failures:
        print("PulseSoc moderation review event emission audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc moderation review event emission audit passed.")
    print("- Moderation case resolve/dismiss events are cursor-visible.")
    print("- Marketplace/content report resolution events are cursor-visible.")
    print("- Content restore and user restriction review events are cursor-visible.")
    print("- Chrome Incognito and Android-specific scope remain intentionally out of scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
