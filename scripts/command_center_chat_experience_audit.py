#!/usr/bin/env python3
"""Audit the user-facing Command Center chat experience wiring."""

from __future__ import annotations

import json
import py_compile
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from pulse_communications_v2 import service  # noqa: E402


def expect(condition: bool, message: str, details: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")


def ensure_users() -> tuple[int, int]:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    users = [
        (970301, "command_center_a", "Command Center A", "command-center-a@example.test"),
        (970302, "command_center_b", "Command Center B", "command-center-b@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
        if cur.fetchone():
            cur.execute(
                "UPDATE users SET username=?, display_name=?, email=?, avatar_url=?, account_status='active' WHERE user_id=?",
                (username, display_name, email, f"/static/img/audit-{user_id}.png", user_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (user_id, username, display_name, email, avatar_url, account_status, signup_time, onboarding_complete)
                VALUES (?, ?, ?, ?, ?, 'active', ?, 1)
                """,
                (user_id, username, display_name, email, f"/static/img/audit-{user_id}.png", now),
            )
    conn.commit()
    conn.close()
    return users[0][0], users[1][0]


def compile_targets() -> list[str]:
    targets = [
        ROOT / "pulse_communications_v2" / "routes.py",
        ROOT / "pulse_communications_v2" / "service.py",
        ROOT / "scripts" / "command_center_chat_experience_audit.py",
    ]
    for target in targets:
        py_compile.compile(str(target), doraise=True)
    node_result = subprocess.run(
        ["node", "--check", str(ROOT / "static" / "js" / "pulse_messages_v2.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    expect(node_result.returncode == 0, "messages JS syntax check", node_result.stdout + node_result.stderr)
    return [str(target.relative_to(ROOT)) for target in targets] + ["static/js/pulse_messages_v2.js"]


def static_ui_checks() -> dict:
    template = (ROOT / "templates" / "pulse_messages_v2.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "pulse_messages_v2.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pulse_messages_v2.css").read_text(encoding="utf-8")
    for token in ['data-filter="all"', 'data-filter="direct"', 'data-filter="groups"', 'data-filter="rooms"', 'data-filter="unread"']:
        expect(token in template, f"filter wired: {token}")
    for token in ["data-realtime-status", "data-pulse-ai-card", "data-conversation-action=\"mute\"", "data-conversation-action=\"archive\""]:
        expect(token in template, f"template includes {token}")
    for token in ["const root = el(\".comm-shell\")", "data-shield-link", "riskScan", "copyMessage", "pinMessage", "swipeMessage", "Realtime live"]:
        expect(token in js, f"js includes {token}")
    expect("aiEnabled: root?.dataset.aiEnabled" in js, "AI state reads root after declaration")
    expect("disabled title=\"Backend support pending\"" not in template, "no disabled backend-pending action placeholders")
    for token in [".realtime-status", ".pulse-shield-warning", ".message.is-pinned", "@media (prefers-reduced-motion: reduce)"]:
        expect(token in css, f"css includes {token}")
    return {"template": True, "js": True, "css": True}


def service_checks() -> dict:
    user_a, user_b = ensure_users()
    opened = service.create_conversation(user_a, {"conversation_type": "direct", "target_user_id": user_b})
    expect(opened.get("ok") is True, "direct conversation opens", json.dumps(opened, default=str))
    conversation_id = int((opened.get("conversation") or {}).get("conversation_id") or opened.get("conversation_id") or 0)
    expect(conversation_id > 0, "conversation id returned", json.dumps(opened, default=str))

    body = f"Audit hello {secrets.token_hex(4)}"
    sent = service.send_message(user_a, conversation_id, {"body": body, "client_message_id": f"audit-{secrets.token_hex(8)}"})
    expect(sent.get("ok") is True and int(sent.get("message_id") or 0) > 0, "message sends", json.dumps(sent, default=str))
    message_id = int(sent["message_id"])

    loaded = service.list_messages(user_b, conversation_id, {"limit": 20})
    expect(any(int(item.get("id") or 0) == message_id for item in loaded.get("messages") or []), "recipient reloads message", json.dumps(loaded, default=str))

    read = service.mark_read(user_b, conversation_id)
    expect(read.get("ok") is True, "read receipt clears unread", json.dumps(read, default=str))

    muted = service.toggle_mute(user_a, conversation_id)
    expect(muted.get("ok") is True and muted.get("muted") is True, "conversation mutes", json.dumps(muted, default=str))
    unmuted = service.toggle_mute(user_a, conversation_id)
    expect(unmuted.get("ok") is True and unmuted.get("muted") is False, "conversation unmutes", json.dumps(unmuted, default=str))

    pinned = service.toggle_message_pin(user_a, message_id)
    expect(pinned.get("ok") is True and pinned.get("pinned") is True and (pinned.get("message") or {}).get("pinned") is True, "message pin persists", json.dumps(pinned, default=str))

    risky = service.send_message(
        user_a,
        conversation_id,
        {
            "body": "Claim airdrop now https://walletconnect-verify.top/connect wallet",
            "client_message_id": f"audit-risk-{secrets.token_hex(8)}",
        },
    )
    expect(risky.get("ok") is True, "risky message still sends for review", json.dumps(risky, default=str))
    expect((risky.get("message") or {}).get("pulse_shield", {}).get("flagged") is True, "Pulse Shield flag returned", json.dumps(risky, default=str))

    archived = service.archive_conversation(user_a, conversation_id)
    expect(archived.get("ok") is True and archived.get("archived") is True, "conversation archives", json.dumps(archived, default=str))
    conversations = service.list_conversations(user_a, {"type": "all"}).get("conversations") or []
    expect(not any(int(item.get("conversation_id") or 0) == conversation_id for item in conversations), "archived conversation hidden from list")

    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "read_receipt": True,
        "mute": True,
        "pin": True,
        "archive": True,
        "shield": True,
    }


def main() -> int:
    bot.init_db()
    report = {
        "ok": True,
        "compiled": compile_targets(),
        "static_ui": static_ui_checks(),
        "service": service_checks(),
        "notes": {
            "realtime_qa": "Audit verifies realtime hooks and fallback wiring; physical two-browser proof still requires live authenticated sessions.",
            "mobile_qa": "Static mobile safety checks are covered; physical device keyboard QA still requires browser/device access.",
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
