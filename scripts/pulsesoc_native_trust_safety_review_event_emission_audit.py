#!/usr/bin/env python3
"""Validate trust/safety review update event emission for native cursor sync."""

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
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_trust_safety_review_", suffix=".sqlite", delete=False) as handle:
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
    response = client.get("/api/pulse/sync/events?limit=100")
    require(response.status_code == 200, f"sync cursor returned HTTP {response.status_code}", failures)
    return (response.get_json(silent=True) or {}).get("events") or []


def require_event(events: list[dict], event_type: str, failures: list[str], entity_type: str = "") -> dict:
    matches = [event for event in events if event.get("event_type") == event_type and (not entity_type or event.get("entity_type") == entity_type)]
    require(bool(matches), f"sync cursor missing {event_type}{' for ' + entity_type if entity_type else ''}", failures)
    event = matches[-1] if matches else {}
    metadata = event.get("metadata") or {}
    for key in ["event_type", "entity_type", "entity_id", "actor_id", "timestamp", "sync_cursor_key"]:
        require(key in metadata, f"{event_type} metadata missing {key}", failures)
    return event


def seed_user(cur, email: str, username: str, display_name: str) -> int:
    now = "2026-07-06T23:00:00"
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
    from services import pulsesoc_dashboard_centers

    pulsesoc_dashboard_centers.ensure_tables(conn)
    reporter_id = seed_user(cur, "trust-review-reporter@example.com", "trustreviewreporter", "Trust Review Reporter")
    target_id = seed_user(cur, "trust-review-target@example.com", "trustreviewtarget", "Trust Review Target")

    cur.execute("INSERT INTO pulse_groups (owner_user_id, slug, name, status, created_at, updated_at) VALUES (?, 'trust-review-group', 'Trust Review Group', 'active', '2026-07-06T23:00:00', '2026-07-06T23:00:00')", (target_id,))
    group_id = int(cur.lastrowid)
    cur.execute("INSERT OR IGNORE INTO pulse_group_members (group_id, user_id, role, created_at) VALUES (?, ?, 'member', '2026-07-06T23:00:00')", (group_id, reporter_id))
    cur.execute("INSERT INTO pulse_audio_tracks (title, artist, uploader_user_id, audio_url, source_type, safety_status, active, created_at, updated_at) VALUES ('Audit Track', 'Audit Artist', ?, '/static/audit.mp3', 'artist_upload', 'approved', 1, '2026-07-06T23:00:00', '2026-07-06T23:00:00')", (target_id,))
    track_id = int(cur.lastrowid)
    cur.execute("INSERT INTO verification_requests (user_id, track, status, progress_percent, submitted_at, created_at, updated_at) VALUES (?, 'identity', 'submitted', 35, '2026-07-06T23:00:00', '2026-07-06T23:00:00', '2026-07-06T23:00:00')", (reporter_id,))
    verification_id = int(cur.lastrowid)
    cur.execute("INSERT INTO pulse_reports (reporter_user_id, target_type, target_id, reason, status, created_at, updated_at) VALUES (?, 'user', ?, 'audit report', 'open', '2026-07-06T23:00:00', '2026-07-06T23:00:00')", (reporter_id, target_id))
    pulse_report_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    set_session(client, reporter_id)
    marketplace_report = client.post("/api/pulse/marketplace/listings/report", json={"listing_id": 101, "reason": "audit listing report"})
    require(marketplace_report.status_code < 400, f"marketplace report failed: {marketplace_report.status_code}", failures)
    require_event(sync_events(client, reporter_id, failures), "report_submitted", failures, "marketplace_report")

    bot.GROUPS_ADVANCED_MODE = True
    group_report = client.post("/api/pulse/groups/report", json={"group_id": group_id, "reason": "audit group report"})
    if group_report.status_code < 400:
        require_event(sync_events(client, reporter_id, failures), "report_submitted", failures, "group_report")
    else:
        failures.append(f"group report failed: {group_report.status_code} {group_report.get_json(silent=True)}")

    music_report = client.post(f"/api/pulse/music/{track_id}/report", json={"reason": "rights concern", "details": "audit details"})
    require(music_report.status_code < 400, f"music report failed: {music_report.status_code} {music_report.get_json(silent=True)}", failures)
    require_event(sync_events(client, reporter_id, failures), "report_submitted", failures, "music_report")

    admin = {"id": 9001, "user_id": 9001, "email": "trust-review-admin@example.com", "role": "owner", "status": "active"}
    bot._verification_admin_or_redirect = lambda: (admin, None)
    verification_review = client.post("/api/admin/verification/action", json={"request_id": verification_id, "action": "reject", "reason": "audit rejection"})
    require(verification_review.status_code < 400, f"verification review failed: {verification_review.status_code} {verification_review.get_json(silent=True)}", failures)
    require_event(sync_events(client, reporter_id, failures), "safety_appeal_rejected", failures, "verification_request")

    bot.admin_current_user = lambda: admin
    music_review = client.post(f"/api/admin/pulse/music/{track_id}/remove", json={"reason": "audit removal"})
    require(music_review.status_code < 400, f"music review failed: {music_review.status_code} {music_review.get_json(silent=True)}", failures)
    require_event(sync_events(client, reporter_id, failures), "report_reviewed", failures, "music_report")

    with bot.webhook_app.test_request_context("/admin/departments/trust-safety", method="POST"):
        bot.apply_department_action("trust-safety", "dismiss_report", pulse_report_id, "audit dismissal", admin)
    require_event(sync_events(client, reporter_id, failures), "report_dismissed", failures, "report")


def main() -> int:
    failures: list[str] = []
    bot_source = read("bot.py")
    report = read("reports/pulsesoc_native_trust_safety_review_event_emission.md")
    progress = read("reports/pulsesoc_native_progress.md")
    producer_audit = read("scripts/pulsesoc_native_event_producer_coverage_audit.py")

    for token in [
        "def pulse_emit_trust_safety_review_event",
        "safety_appeal_approved",
        "safety_appeal_rejected",
        "safety_appeal_updated",
        "report_reviewed",
        "report_dismissed",
        "marketplace_report",
        "group_report",
        "group_comment_report",
        "group_post_report",
        "music_report",
    ]:
        require(token in bot_source, f"bot.py missing trust/safety review event token: {token}", failures)

    for token in [
        "Trust/safety review event coverage %",
        "Remaining silent mutation paths",
        "Event visibility through sync cursor",
        "Activity/Trust/Safety/Account Health consistency impact",
        "ONE highest-impact fix ONLY",
        "Do not focus on Android",
    ]:
        require(token in report, f"trust/safety review report missing token: {token}", failures)
    require("Trust/Safety Review Update Event Emission Hardening" in progress, "progress report missing trust/safety review section", failures)
    require("pulse_emit_trust_safety_review_event" in producer_audit, "producer audit missing trust/safety review helper recognition", failures)

    bot = import_bot_with_temp_db()
    run_seeded_checks(bot, failures)

    if failures:
        print("PulseSoc trust/safety review event emission audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc trust/safety review event emission audit passed.")
    print("- Verification review decisions are cursor-visible.")
    print("- Marketplace/group/music report variants emit safety cursor events.")
    print("- Music report review and trust/safety report dismissal emit review cursor events.")
    print("- Android-specific tooling remains intentionally out of scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
