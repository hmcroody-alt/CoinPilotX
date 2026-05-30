#!/usr/bin/env python3
"""Pulse chat system audit: private, group, room, and self-healing checks."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import chat_health_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db(); bot.init_db()
    bot_source = (ROOT / "bot.py").read_text()
    recovery_js = (ROOT / "static/js/pulse_chat_recovery.js").read_text()
    function_start = bot_source.find("def pulse_dashboard_messenger_page")
    active_return = bot_source.find("return pulse_social_shell(\"Pulse Messenger\", \"Stable Dashboard", function_start)
    active_pulse_chat = bot_source[function_start:active_return]
    expect("pulse-dashboard-chat" in active_pulse_chat, "Pulse Messenger uses restored Dashboard chat shell")
    expect("/api/chat/threads" in active_pulse_chat, "Pulse Messenger loads Dashboard conversation threads")
    expect("/api/chat/start" in active_pulse_chat, "Pulse Messenger starts private chats through Dashboard chat API")
    expect("messageLoadUrl" in active_pulse_chat and "messageSendUrl" in active_pulse_chat, "Pulse Messenger uses unified message load/send bridge")
    expect("/api/pulse/messages/${Number(conversationId)}/messages" in active_pulse_chat, "direct and group messages use canonical Pulse endpoint")
    expect("/api/pulse/messages/rooms/${room}/messages" in active_pulse_chat, "room messages use canonical Pulse endpoint")
    expect('/api/messages/${Number(conversationId)}' in active_pulse_chat and 'source_kind:"legacy"' in active_pulse_chat, "legacy Dashboard direct rows keep legacy message bridge")
    expect("/api/pulse/chatrooms" in active_pulse_chat and "/api/pulse/messages/group-conversations" in active_pulse_chat, "rooms and groups remain wired")
    expect("messageErrorCopy" in active_pulse_chat and "You do not have access to this chat." in active_pulse_chat and "Conversation not found." in active_pulse_chat, "message panel has status-specific error copy")
    expect("pulseMessengerPendingV2" not in active_pulse_chat, "broken Pulse recovery queue is not active in Pulse Messenger shell")
    for primitive in [
        "loadConversations",
        "loadRooms",
        "loadGroups",
        "openConversation",
        "openRoom",
        "pollActiveConversation",
    ]:
        expect(primitive in active_pulse_chat, f"restored Dashboard chat primitive exists: {primitive}")
    expect("Messages syncing" not in bot_source and "Messages syncing" not in recovery_js, "fake syncing loop copy removed")
    expect("Loading latest messages" in recovery_js and "Reconnecting..." in recovery_js, "specific chat recovery states exist")
    expect("Something needs attention. Please try again." not in active_pulse_chat, "messenger does not use generic failure copy")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/messenger_core_audit.py")], cwd=str(ROOT), text=True, capture_output=True)
    expect(result.returncode == 0, "canonical messenger core audit", result.stdout + result.stderr)
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    summary = chat_health_service.health_summary(cur)
    recovery = chat_health_service.chat_recovery_payload(mode="reconnecting")
    chat_health_service.record_recovery_event(cur, 930000, 0, "audit_reconnect", {"source": "chat_system_audit"})
    repair = chat_health_service.repair_stale_sessions(cur)
    conn.commit(); conn.close()
    expect("tables" in summary and summary["tables"].get("conversations", 0) >= 0, "chat health summary available", str(summary))
    expect(recovery.get("fallback_polling") is True and recovery.get("retryable") is True, "chat recovery payload supports polling fallback", str(recovery))
    expect(repair.get("ok") is True, "stale session repair runs", str(repair))
    print("chat system audit ok")


if __name__ == "__main__":
    main()
