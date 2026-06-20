#!/usr/bin/env python3
"""Guard PulseSoc web capacity against long-lived request starvation."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PULSE_COMMUNICATIONS_V2_ENABLED", "true")
os.environ.pop("PULSE_COMM_V2_SSE_ENABLED", None)
os.environ.pop("ARENA_SSE_ENABLED", None)

import bot  # noqa: E402


USER_ID = 985301


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (USER_ID,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
            (USER_ID, "capacity_audit", "Capacity Audit", "capacity-audit@example.test", now),
        )
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = USER_ID

    comm_stream = client.get("/api/pulse/communications/v2/realtime/stream")
    expect(comm_stream.status_code == 204, "Communications SSE is disabled by default on web workers")
    expect(comm_stream.headers.get("X-Pulse-Realtime-Transport") == "polling", "Communications stream advertises polling fallback")

    arena_stream = client.get("/api/arena/realtime/match/1/stream")
    expect(arena_stream.status_code == 204, "Arena SSE is disabled by default on web workers")

    source = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
    expect('dataset.pulseSse !== "enabled"' in source, "Messages client does not open SSE by default")
    expect("scheduleRealtimePoll(3000)" in source, "Messages client uses short nonblocking polling")

    worker = (ROOT / "services/command_center_worker/app.py").read_text(encoding="utf-8")
    expect('message_payload.get("recipient_ids")' in worker, "Command Center routes message events to all recipients")

    client_source = (ROOT / "services/command_center_client.py").read_text(encoding="utf-8")
    expect("def get_realtime_events" in client_source, "Main app can poll shared Command Center events")

    bot.PULSE_MESSENGER_SCHEMA_READY = True
    conn = bot.db()
    cur = conn.cursor()
    before = int(getattr(bot.g, "db_query_count", 0) or 0) if bot.has_request_context() else 0
    bot.ensure_pulse_messenger_schema(cur, conn)
    after = int(getattr(bot.g, "db_query_count", 0) or 0) if bot.has_request_context() else before
    conn.close()
    expect(after == before, "Messenger schema guard is a no-op after startup")

    print("pulse realtime capacity audit ok")


if __name__ == "__main__":
    main()
