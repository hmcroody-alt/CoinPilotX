#!/usr/bin/env python3
"""Static contract audit for the PulseSoc Messenger V3 experience."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_all(text: str, needles: tuple[str, ...], label: str) -> None:
    missing = [needle for needle in needles if needle not in text]
    require(not missing, f"{label} missing: {', '.join(missing)}")


def run_checks() -> None:
    template = read("templates/pulse_messages_v2.html")
    script = read("static/js/pulse_messages_v2.js")
    css = read("static/css/pulse_messages_v2.css")
    bot = read("bot.py")
    routes = read("pulse_communications_v2/routes.py")

    require_all(
        template,
        (
            "PulseSoc Messenger V3",
            "Search people, rooms, messages...",
            'data-filter="all"',
            'data-filter="direct"',
            'data-filter="groups"',
            'data-filter="rooms"',
            'data-filter="unread"',
            "data-active-rail",
            "data-open-new-chat",
            "data-open-new-group",
            "data-open-new-room",
            "data-conversations",
            "thread-head",
            "data-thread-trust",
            "data-messages",
            "data-composer",
            "data-toggle-attachments",
            "data-message-input",
            "data-toggle-emoji",
            "data-voice-start",
            "data-send-button",
            "conversation-skeleton-list",
        ),
        "Messenger V3 template",
    )
    require(template.index("thread-head") < template.index("data-messages"), "thread header must render before the timeline")
    require(template.index("data-messages") < template.index("data-composer"), "composer must be part of the initial thread shell")
    require("href=\"#\"" not in template, "template must not contain href=#")
    require("javascript:void" not in template, "template must not contain javascript:void")
    require("LogiNexus" not in template, "internal design language must not be exposed to users")
    require(template.count("call-action is-disabled") == 2, "voice and video call controls must both be safely disabled")
    require("data-start-call" not in template, "placeholder call endpoints must not be presented as active controls")
    require('disabled title="Voice calls coming soon"' in template, "voice call disabled reason is required")
    require('disabled title="Video calls coming soon"' in template, "video call disabled reason is required")
    require("{% if ai_enabled %}" in template and "data-ai-summary" in template, "AI controls must be feature-gated")

    require_all(
        script,
        (
            "messageCache: new Map()",
            "threadHydrating",
            "sessionStorage.setItem",
            "restoreDraft",
            "renderTrustBadges",
            "toggleThreadSearch",
            "applyThreadSearch",
            "toggleEmojiPanel",
            "insertEmoji",
            "retryFailedMessage",
            'target.closest("[data-conversation-id]")',
            'addEventListener("submit", sendMessage)',
            "client_message_id",
            "delivery_status: \"sending\"",
            "appendRealtimeMessage",
            "connectRealtimeStream",
            "scheduleRealtimePoll",
            "data-ai-summary",
            "No messages here yet. Send the first one.",
        ),
        "Messenger V3 JavaScript",
    )
    require("data-start-call" not in script, "JavaScript must not wire placeholder call routes")
    require("Communications V2 action failed" not in script, "legacy V2 action copy must be removed")
    require("v2 test UI" not in script, "test-only Messenger copy must be removed")
    require("LogiNexus" not in script, "internal design language must not be exposed by JavaScript")

    require_all(
        css,
        (
            "overflow-x: hidden",
            "env(safe-area-inset-bottom",
            ".conversation-skeleton-list",
            ".message-skeletons",
            ".message.is-mine",
            ".message-retry",
            ".pulse-messenger-dock",
            "@media (max-width: 840px)",
            "@media (max-width: 430px)",
            "@media (max-width: 374px)",
            "@media (prefers-reduced-motion: reduce)",
            ":focus-visible",
        ),
        "Messenger V3 CSS",
    )
    require("LogiNexus" not in css, "internal design language must not be exposed by CSS")

    require_all(
        bot,
        (
            '@webhook_app.route("/pulse/messages", methods=["GET"])',
            '@webhook_app.route("/pulse/messages/<int:conversation_id>", methods=["GET"])',
            '"pulse_messages_v2.html"',
            "initial_conversation_id=int(conversation_id or 0)",
        ),
        "Messenger publish routes",
    )
    require_all(
        routes,
        (
            'API_PREFIX = "/api/pulse/communications/v2"',
            "/conversations",
            "/direct/open",
            "/groups",
            "/rooms",
            "/messages",
            "/typing",
            "/presence",
            "/attachments/upload",
        ),
        "Messenger backend routes",
    )


def main() -> int:
    run_checks()
    print("messenger_v3_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"messenger_v3_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
