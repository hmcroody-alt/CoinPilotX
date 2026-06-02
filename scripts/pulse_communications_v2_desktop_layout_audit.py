#!/usr/bin/env python3
"""Desktop layout and speed audit for Pulse Communications 2.0."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_seed() -> tuple[int, int, int]:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    users = [
        (983101, "desktop_v2_one", "Desktop V2 One", "desktop-v2-one@example.test"),
        (983102, "desktop_v2_two", "Desktop V2 Two", "desktop-v2-two@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET username=?, display_name=?, email=?, account_status='active' WHERE user_id=?", (username, display_name, email, user_id))
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
                (user_id, username, display_name, email, now),
            )
    conn.commit()
    conn.close()

    client = client_for(users[0][0])
    status, data, _ = timed_json(client, "POST", "/api/pulse/communications/v2/direct/open", {"target_user_id": users[1][0]})
    expect(status == 200 and data.get("ok"), "desktop audit DM exists", str(data))
    conversation_id = int(data["conversation_id"])
    for index in range(65):
        status, data, _ = timed_json(client, "POST", f"/api/pulse/communications/v2/conversations/{conversation_id}/messages", {"body": f"desktop speed seed {index}"})
        expect(status == 200 and data.get("ok"), f"desktop seed message {index}", str(data))
    return users[0][0], users[1][0], conversation_id


def client_for(user_id: int):
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def timed_json(client, method: str, path: str, payload: dict | None = None) -> tuple[int, dict, float]:
    start = time.perf_counter()
    if method == "GET":
        response = client.get(path)
    else:
        response = client.open(path, method=method, data=json.dumps(payload or {}), content_type="application/json")
    elapsed_ms = (time.perf_counter() - start) * 1000
    return response.status_code, response.get_json(silent=True) or {}, elapsed_ms


def main() -> None:
    user_id, _, conversation_id = ensure_seed()
    client = client_for(user_id)

    css = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
    js = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
    html = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")

    for token in ["@media (min-width: 941px)", "clamp(280px, 19vw, 340px)", "clamp(280px, 17vw, 320px)", ".comm-shell.details-collapsed", "minmax(760px, 1fr)"]:
        expect(token in css, f"desktop CSS includes {token}")
    expect("@media (max-width: 720px)" in css and ".mobile-only { display: inline-grid; }" in css, "mobile rules remain present")
    expect("data-toggle-intel" in html, "desktop details toggle is present")
    expect("INITIAL_MESSAGE_LIMIT = 40" in js, "initial message limit is 40")
    expect("data-load-older" in js and "before_id=" in js, "load older pagination exists")
    expect("conversationCache" in js, "conversation metadata cache exists")
    expect("setTimeout(sendTypingIndicator, 450)" in js, "typing indicator is debounced")
    expect("  loadConversations();\n})();" in js, "single page-load conversation fetch trigger")
    expect("setInterval(" not in js, "no repeated polling interval")

    start = time.perf_counter()
    page = client.get("/pulse/messages-v2")
    page_ms = (time.perf_counter() - start) * 1000
    expect(page.status_code == 200, "desktop messages page renders", f"status={page.status_code}")
    expect(page_ms < 1500, "desktop initial render under 1.5s", f"{page_ms:.0f}ms")

    status, data, conversations_ms = timed_json(client, "GET", "/api/pulse/communications/v2/conversations")
    expect(status == 200 and data.get("ok"), "conversations list loads", str(data))
    expect(conversations_ms < 1500, "conversations list under 1.5s", f"{conversations_ms:.0f}ms")

    status, data, thread_ms = timed_json(client, "GET", f"/api/pulse/communications/v2/conversations/{conversation_id}/messages")
    expect(status == 200 and data.get("ok"), "selected conversation loads", str(data))
    expect(thread_ms < 500, "selected conversation load under 500ms", f"{thread_ms:.0f}ms")
    expect(len(data.get("messages") or []) <= 40, "default thread load is capped at 40", str(len(data.get("messages") or [])))
    expect(data.get("has_older") is True and int(data.get("oldest_message_id") or 0) > 0, "thread exposes older-message pagination", str(data))

    status, older, older_ms = timed_json(client, "GET", f"/api/pulse/communications/v2/conversations/{conversation_id}/messages?before_id={int(data.get('oldest_message_id') or 0)}")
    expect(status == 200 and older.get("ok"), "older messages page loads", str(older))
    expect(older_ms < 500, "older messages load under 500ms", f"{older_ms:.0f}ms")

    print("pulse communications v2 desktop layout audit ok")


if __name__ == "__main__":
    main()
