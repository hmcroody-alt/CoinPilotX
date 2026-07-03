#!/usr/bin/env python3
"""Audit Pulse AI Intelligence Upgrade."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_json(path: str):
    return json.loads(read(path))


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    knowledge_json = load_json("data/pulse_ai/pulsesoc_knowledge.json")
    cyber_json = load_json("data/pulse_ai/cybersecurity_knowledge.json")
    feature_map = load_json("data/pulse_ai/pulsesoc_feature_map.json")
    knowledge = read("services/pulse_ai_knowledge.py")
    service = read("services/pulse_ai_service.py")
    provider_router = read("services/pulse_ai_provider_router.py")
    task_router = read("services/pulse_ai_router.py")
    web_search = read("services/pulse_ai_web_search.py")
    safety = read("services/pulse_ai_safety.py")
    migration = read("migrations/pulse_ai_messenger.sql")
    routes = read("pulse_communications_v2/routes.py")
    js = read("static/js/pulse_messages_v2.js")
    admin_template = read("templates/admin_pulse_ai_learning_center.html")
    report = read("reports/pulse_ai_intelligence_upgrade.md") if (ROOT / "reports/pulse_ai_intelligence_upgrade.md").exists() else ""

    required_features = {
        "Home feed", "Reels", "Status / Stories", "Messenger", "Audio calls", "Video calls",
        "Pulse AI chat", "PulseSoc Music", "Notifications", "Crypto alerts", "Market alerts",
        "Intelligence Streams", "Manage My Alerts", "Conversation Control Center", "Profile",
        "Privacy settings", "Security settings", "Creator tools", "Premium / Founder access",
        "Verification badges", "Live streaming", "Search", "Trending content",
        "Communities / groups / rooms", "Reporting users", "Blocking users", "Account recovery",
        "Password reset", "App Store availability", "Mobile PWA behavior", "Push notifications",
        "Lock-screen notifications",
    }
    found_features = {item.get("feature") for item in knowledge_json}
    for feature in sorted(required_features):
        require(feature in found_features, f"{feature} knowledge exists", failures)
    for item in knowledge_json:
        for key in ("feature", "summary", "where_to_find_it", "how_to_use_it", "common_questions", "troubleshooting", "related_features", "safety_notes"):
            require(key in item, f"{item.get('feature')} has {key}", failures)

    required_cyber = {
        "password safety", "phishing prevention", "scam detection", "two-factor authentication",
        "device security", "public Wi-Fi risks", "malware prevention", "social engineering",
        "crypto wallet safety", "fake investment scams", "romance scams", "SIM swapping",
        "email security", "browser safety", "app permissions", "incident response basics",
        "small business cybersecurity", "WordPress security basics", "secure backups", "update hygiene",
    }
    found_cyber = {item.get("topic") for item in cyber_json}
    for topic in sorted(required_cyber):
        require(topic in found_cyber, f"{topic} cyber knowledge exists", failures)
    require(len(feature_map) >= 20 and all("entry_points" in item and "user_help" in item for item in feature_map), "feature registry JSON exists", failures)

    require("DATA_DIR" in knowledge and "_derived_knowledge_items" in knowledge, "JSON knowledge ingestion exists", failures)
    require("CORE_SYSTEM_PROMPT" in knowledge and "Do not invent current prices" in knowledge, "system prompt has current-info rule", failures)
    require("generate_response" in provider_router and "configured_providers_for_task" in provider_router, "task-aware provider router exists", failures)
    for provider in ("openai", "claude", "gemini", "deepseek", "groq"):
        require(provider in provider_router, f"{provider} supported", failures)
    require("should_search" in web_search and "DEFAULT_TIMEOUT_SECONDS" in web_search and "duckduckgo" in web_search.lower(), "safe web search service exists", failures)
    require("CISA" in web_search or "cisa.gov" in web_search, "trusted cybersecurity source hints exist", failures)
    require("DISALLOWED_PATTERNS" in safety and "refusal_message" in safety and "CYBER_MODES" in safety, "cyber safety rules exist", failures)
    for phrase in ("phishing kit", "MFA bypass", "malware creation", "unauthorized access"):
        require(phrase.lower() in safety.lower(), f"{phrase} blocked", failures)
    require("pulse_ai_web_search_logs" in service and "pulse_ai_provider_events" in service and "pulse_ai_safety_events" in service, "runtime event tables exist", failures)
    require("pulse_ai_web_search_logs" in migration and "pulse_ai_provider_events" in migration and "pulse_ai_safety_events" in migration, "migration event tables exist", failures)
    require("pulse_ai_router.classify" in service and "pulse_ai_safety.classify_request" in service, "message path classifies safety/task", failures)
    require("pulse_ai_web_search.search" in service and "context_block" in service, "message path adds web context", failures)
    require("redact_sensitive_text" in service, "message path redacts sensitive text", failures)
    require("/api/pulse-ai/message" in routes and "/api/pulse-ai/settings" in routes, "Pulse AI routes exist", failures)
    require("PULSE_AI_CONVERSATION_ID" in js and "sendPulseAIMessage" in js, "Messenger Pulse AI integration exists", failures)
    require("Web Searches" in admin_template and "Provider Events" in admin_template and "Safety Event Trends" in admin_template, "admin visibility exists", failures)
    require("OPENAI_API_KEY" not in js and "ANTHROPIC_API_KEY" not in js and "SERPAPI_API_KEY" not in js, "frontend does not expose secrets", failures)
    require("PulseSoc feature coverage" in report and "Cybersecurity coverage" in report and "Web search behavior" in report, "completion report exists", failures)

    if failures:
        print("Pulse AI Intelligence Upgrade audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("Pulse AI Intelligence Upgrade audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
