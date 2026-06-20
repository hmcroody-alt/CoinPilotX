#!/usr/bin/env python3
"""Audit the PulseSoc Messages command-center UI contract."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    template = read("templates/pulse_messages_v2.html")
    script = read("static/js/pulse_messages_v2.js")
    css = read("static/css/pulse_messages_v2.css")
    bot = read("bot.py")

    for needle in (
        "signal-status-strip",
        "data-thread-signal-state",
        "data-thread-reachability",
        "data-thread-shield-state",
        "data-filter=\"shield\"",
        "data-signal-route",
        "data-initial-conversation-id",
    ):
        require(needle in template, f"template missing {needle}")

    for needle in (
        "initialConversationId",
        "renderSignalIntelligence",
        "messageDeliveryLabel",
        "threadRiskSummary",
        "data-state=\"${escapeAttr(messageDeliveryLabel(item).toLowerCase())}\"",
        "Pulse Shield",
        "connectRealtimeStream",
    ):
        require(needle in script, f"Messages JS missing {needle}")

    for needle in (
        "Mission deck override",
        ".signal-status-strip",
        ".signal-route",
        ".delivery-state",
        ".pulse-shield-warning",
        "prefers-reduced-motion",
    ):
        require(needle in css, f"Messages CSS missing {needle}")

    require("pulse_message_thread_page" in bot and "initial_conversation_id=int(conversation_id or 0)" in bot, "deep-link route must render command-center template")
    require("pulse_messages_v2.html" in bot, "/pulse/messages must render V2 command-center template when enabled")

    print("pulse_messages_command_center_ui_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pulse_messages_command_center_ui_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
