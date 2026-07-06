#!/usr/bin/env python3
"""Validate PulseSoc communications and safety event emission for native sync."""

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
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_comms_safety_events_", suffix=".sqlite", delete=False) as handle:
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


def add_user(cur, email: str, username: str, display_name: str, now: str) -> int:
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        (email, username, display_name, now, now),
    )
    return int(cur.lastrowid)


def set_session(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["account_user_id"] = int(user_id)


def sync_events(client, user_id: int, failures: list[str]) -> list[dict]:
    set_session(client, user_id)
    response = client.get("/api/pulse/sync/events?limit=100")
    require(response.status_code == 200, f"sync cursor returned HTTP {response.status_code}", failures)
    return (response.get_json(silent=True) or {}).get("events") or []


def require_event(events: list[dict], event_type: str, failures: list[str]) -> dict:
    matches = [event for event in events if event.get("event_type") == event_type]
    require(bool(matches), f"sync cursor missing {event_type}", failures)
    event = matches[-1] if matches else {}
    metadata = event.get("metadata") or {}
    for key in ["event_type", "entity_type", "entity_id", "actor_id", "timestamp", "sync_cursor_key"]:
        require(key in metadata, f"{event_type} metadata missing {key}", failures)
    return event


def seed_pulse_conversation(bot, sender_id: int, recipient_id: int) -> int:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    result, status = bot.pulse_start_conversation(cur, sender_id, target_user_id=recipient_id)
    if status >= 400 or not result.get("ok"):
        raise AssertionError(f"pulse_start_conversation failed: {result}")
    conn.commit()
    conn.close()
    return int(result["conversation_id"])


def run_message_safety_flow(bot, failures: list[str]) -> tuple[int, int]:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-06T22:00:00"
    sender_id = add_user(cur, "comms-sender-qa@example.com", "commssenderqa", "Comms Sender QA", now)
    recipient_id = add_user(cur, "comms-recipient-qa@example.com", "commsrecipientqa", "Comms Recipient QA", now)
    third_id = add_user(cur, "safety-target-qa@example.com", "safetytargetqa", "Safety Target QA", now)
    conn.commit()
    conn.close()

    conversation_id = seed_pulse_conversation(bot, sender_id, recipient_id)
    client = bot.webhook_app.test_client()

    set_session(client, sender_id)
    send_response = client.post(f"/api/pulse/messages/{conversation_id}/send", json={"message": "Cursor-visible hello"})
    send_data = send_response.get_json(silent=True) or {}
    require(send_response.status_code < 400 and send_data.get("ok") is True, f"message send failed: {send_response.status_code} {send_data}", failures)
    message_id = int(send_data.get("message_id") or 0)
    require(message_id > 0, "message send did not return message_id", failures)
    require_event(sync_events(client, recipient_id, failures), "message_received", failures)

    set_session(client, recipient_id)
    seen_response = client.post(f"/api/pulse/messages/{conversation_id}/seen", json={})
    require(seen_response.status_code < 400, f"message seen failed: {seen_response.status_code} {seen_response.get_json(silent=True)}", failures)
    require_event(sync_events(client, sender_id, failures), "message_seen", failures)

    set_session(client, recipient_id)
    report_response = client.post(f"/api/pulse/messages/{message_id}/report", json={"reason": "spam", "notes": "audit report"})
    require(report_response.status_code < 400, f"message report failed: {report_response.status_code} {report_response.get_json(silent=True)}", failures)
    recipient_events = sync_events(client, recipient_id, failures)
    require_event(recipient_events, "message_reported", failures)
    require_event(recipient_events, "report_submitted", failures)

    set_session(client, sender_id)
    delete_response = client.post(f"/api/pulse/messages/{message_id}/delete", json={})
    require(delete_response.status_code < 400, f"message delete failed: {delete_response.status_code} {delete_response.get_json(silent=True)}", failures)
    require_event(sync_events(client, sender_id, failures), "message_deleted", failures)

    block_response = client.post("/api/pulse/block", json={"blocked_user_id": third_id, "reason": "audit safety block"})
    require(block_response.status_code < 400, f"user block failed: {block_response.status_code} {block_response.get_json(silent=True)}", failures)
    sender_events = sync_events(client, sender_id, failures)
    require_event(sender_events, "user_blocked", failures)
    require_event(sender_events, "report_submitted", failures)

    generic_report = client.post("/api/pulse/report", json={"target_type": "user", "target_id": third_id, "reason": "audit generic report"})
    require(generic_report.status_code < 400, f"generic report failed: {generic_report.status_code} {generic_report.get_json(silent=True)}", failures)
    require_event(sync_events(client, sender_id, failures), "report_submitted", failures)
    return sender_id, recipient_id


def seed_comm_v2_conversation(bot, caller_id: int, callee_id: int) -> int:
    from pulse_communications_v2 import service as comm_service

    result = comm_service.create_conversation(caller_id, {"conversation_type": "direct", "target_user_id": callee_id})
    if not result.get("ok"):
        raise AssertionError(f"comm v2 conversation create failed: {result}")
    return int(result.get("conversation_id") or (result.get("conversation") or {}).get("conversation_id") or 0)


def run_call_flow(bot, caller_id: int, callee_id: int, failures: list[str]) -> None:
    from services import pulsesoc_communications_engine as calls

    client = bot.webhook_app.test_client()

    conversation_id = seed_comm_v2_conversation(bot, caller_id, callee_id)
    started = calls.start_call(caller_id, {"conversation_id": conversation_id, "call_type": "audio"})
    require(started.get("ok") is True, f"call start failed: {started}", failures)
    call_id = (started.get("call") or {}).get("public_id") or started.get("public_id")
    require(bool(call_id), "call start did not return public_id", failures)
    require_event(sync_events(client, caller_id, failures), "call_started", failures)
    require_event(sync_events(client, callee_id, failures), "call_started", failures)

    accepted = calls.accept_call(callee_id, call_id, {})
    require(accepted.get("ok") is True, f"call accept failed: {accepted}", failures)
    require_event(sync_events(client, caller_id, failures), "call_accepted", failures)
    ended = calls.end_call(caller_id, call_id, {"reason": "audit_end"})
    require(ended.get("ok") is True, f"call end failed: {ended}", failures)
    require_event(sync_events(client, callee_id, failures), "call_ended", failures)

    declined_conversation_id = seed_comm_v2_conversation(bot, caller_id, callee_id)
    declined_start = calls.start_call(caller_id, {"conversation_id": declined_conversation_id, "call_type": "audio"})
    declined_id = (declined_start.get("call") or {}).get("public_id") or declined_start.get("public_id")
    require(bool(declined_id), f"decline call start failed: {declined_start}", failures)
    declined = calls.decline_call(callee_id, declined_id, {})
    require(declined.get("ok") is True, f"call decline failed: {declined}", failures)
    require_event(sync_events(client, caller_id, failures), "call_declined", failures)

    missed_conversation_id = seed_comm_v2_conversation(bot, caller_id, callee_id)
    missed_start = calls.start_call(caller_id, {"conversation_id": missed_conversation_id, "call_type": "audio"})
    missed_id = (missed_start.get("call") or {}).get("public_id") or missed_start.get("public_id")
    require(bool(missed_id), f"missed call start failed: {missed_start}", failures)
    conn, cur = calls._open_db()
    try:
        cur.execute("UPDATE communication_calls SET created_at='2026-07-06T00:00:00+00:00' WHERE public_id=?", (missed_id,))
        missed = calls._mark_missed_stale_calls_cur(cur, timeout_seconds=5)
        conn.commit()
        require(missed >= 1, "missed call marker did not update stale ringing call", failures)
    finally:
        conn.close()
    require_event(sync_events(client, callee_id, failures), "call_missed", failures)

    original_token = calls._generate_livekit_token
    calls._generate_livekit_token = lambda *args, **kwargs: {"ok": False, "status": "token_failed", "message": "audit token failure"}
    try:
        failed_conversation_id = seed_comm_v2_conversation(bot, caller_id, callee_id)
        failed = calls.start_call(caller_id, {"conversation_id": failed_conversation_id, "call_type": "audio"})
        require(failed.get("ok") is False, f"call failure fixture should fail token generation: {failed}", failures)
    finally:
        calls._generate_livekit_token = original_token
    require_event(sync_events(client, caller_id, failures), "call_failed", failures)


def main() -> int:
    failures: list[str] = []
    bot_source = read("bot.py")
    calls_source = read("services/pulsesoc_communications_engine.py")
    report = read("reports/pulsesoc_native_comms_safety_event_emission.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "def pulse_emit_comms_safety_event",
        "message_received",
        "message_seen",
        "message_deleted",
        "message_reported",
        "user_blocked",
        "report_submitted",
        "safety_appeal_submitted",
    ]:
        require(token in bot_source, f"bot.py missing comms/safety event token: {token}", failures)
    for token in [
        "def _emit_call_sync_event",
        "call_started",
        "call_accepted",
        "call_declined",
        "call_ended",
        "call_missed",
        "call_failed",
    ]:
        require(token in calls_source, f"call engine missing event token: {token}", failures)
    for token in [
        "Message/call/safety event coverage %",
        "Remaining silent mutation paths",
        "Event visibility through sync cursor",
        "Activity/Messenger/Calls/Safety consistency impact",
        "ONE highest-impact fix ONLY",
        "Do not focus on Android",
    ]:
        require(token in report, f"comms/safety event report missing token: {token}", failures)
    require("Message, Call, and Safety Event Emission Hardening" in progress, "progress report missing comms/safety event section", failures)

    bot = import_bot_with_temp_db()
    sender_id, recipient_id = run_message_safety_flow(bot, failures)
    run_call_flow(bot, sender_id, recipient_id, failures)

    if failures:
        print("PulseSoc communications safety event emission audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc communications safety event emission audit passed.")
    print("- Message received/seen/deleted/reported events are cursor-visible.")
    print("- Call started/accepted/declined/ended/missed/failed events are cursor-visible.")
    print("- Safety block/report/appeal event emitters are wired without Android-specific scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
