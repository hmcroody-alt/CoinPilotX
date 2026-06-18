#!/usr/bin/env python3
"""Audit Pulse AI assistant chat UI and API wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"FAIL: missing {label}: {needle}")


def main() -> None:
    bot = (ROOT / "bot.py").read_text()
    require(bot, '@webhook_app.route("/api/pulse/assistant/chat", methods=["POST"])', "assistant chat API")
    require(bot, "get_or_create_ai_conversation", "assistant conversation persistence")
    require(bot, "save_ai_message", "assistant message persistence")
    require(bot, "ai_router_service.route", "assistant AI router")
    require(bot, 'class="pulse-ai-chat"', "assistant chat UI")
    require(bot, "data-ai-form", "assistant composer form")
    require(bot, "fetch('/api/pulse/assistant/chat'", "assistant frontend API call")
    print("PASS: Pulse AI assistant is a real chat UI backed by the AI chat API.")


if __name__ == "__main__":
    main()
