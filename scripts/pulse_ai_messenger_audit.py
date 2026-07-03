#!/usr/bin/env python3
"""Audit Pulse AI Messenger integration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    service = read("services/pulse_ai_service.py")
    router = read("services/pulse_ai_provider_router.py")
    knowledge = read("services/pulse_ai_knowledge.py")
    routes = read("pulse_communications_v2/routes.py")
    js = read("static/js/pulse_messages_v2.js")
    report = read("reports/pulse_ai_messenger.md") if (ROOT / "reports/pulse_ai_messenger.md").exists() else ""

    require("def send_message" in service and "pulse_ai_messages" in service, "AI service stores and sends messages", failures)
    require("pulse_ai_conversations" in service, "AI conversation table exists", failures)
    require("generate_response" in router and "configured_providers" in router, "Provider router fallback exists", failures)
    for provider in ("openai", "claude", "gemini", "deepseek", "groq"):
        require(provider in router, f"{provider} provider supported", failures)
    require("CORE_SYSTEM_PROMPT" in knowledge and "PulseSoc" in knowledge, "PulseSoc knowledge prompt exists", failures)
    for endpoint in ("/api/pulse-ai/message", "/api/pulse-ai/conversation", "/api/pulse-ai/reset", "/api/pulse-ai/status"):
        require(endpoint in routes, f"{endpoint} route exists", failures)
    require("PULSE_AI_CONVERSATION_ID" in js and "sendPulseAIMessage" in js, "Messenger has Pulse AI chat sender", failures)
    require("data-pulse-ai-card" not in read("templates/pulse_messages_v2.html"), "Pulse AI duplicate hero card removed", failures)
    require("active-ai" not in js and "action-icon ai" not in read("templates/pulse_messages_v2.html"), "Pulse AI duplicate rail/action card removed", failures)
    require(".filter((item) => !isPulseAIConversation(item))" in js, "Pulse AI is excluded from duplicate active rail", failures)
    require("item.pinned && !isPulseAIConversation(item)" in js, "Pulse AI is excluded from duplicate pinned cards", failures)
    require("pulseAITyping" in js and "typing-dots" in js, "Typing indicator exists", failures)
    require("Pulse AI supports text conversation first" in js, "Media/voice safely gated for AI chat", failures)
    require("data-pulse-ai-feedback" in js and "/feedback" in js, "Feedback controls wired", failures)
    require("api_key" not in js.lower() and "secret" not in js.lower(), "Frontend does not expose provider secrets", failures)
    require("pulse_ai_messenger" in report, "Completion report exists", failures)

    if failures:
        print("Pulse AI Messenger audit failed:")
        for item in failures:
            print(f" - {item}")
        return 1
    print("Pulse AI Messenger audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
