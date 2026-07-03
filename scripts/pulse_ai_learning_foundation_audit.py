#!/usr/bin/env python3
"""Audit Pulse AI privacy-safe learning foundation."""

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
    knowledge = read("services/pulse_ai_knowledge.py")
    routes = read("pulse_communications_v2/routes.py")
    js = read("static/js/pulse_messages_v2.js")
    template = read("templates/admin_pulse_ai_learning_center.html")
    migration = read("migrations/pulse_ai_messenger.sql")
    report = read("reports/pulse_ai_learning_foundation.md") if (ROOT / "reports/pulse_ai_learning_foundation.md").exists() else ""

    for table in (
        "pulse_ai_knowledge_items",
        "pulse_ai_user_memory",
        "pulse_ai_feedback",
        "pulse_ai_learning_events",
        "pulse_ai_safety_reviews",
        "pulse_ai_feature_registry",
        "pulse_ai_conversation_context_permissions",
    ):
        require(table in service and table in migration, f"{table} exists in runtime schema and migration", failures)
    require("private_context_opt_in" in service and "No hidden training on private conversations" not in service, "Explicit private context opt-in modeled", failures)
    require("record_feedback" in service and "queued_review" in service, "Feedback is queued for review", failures)
    require("clear_memory" in service and "deleted_at" in service, "Clear memory soft-deletes user memory", failures)
    require("export_memory" in service, "Export memory endpoint helper exists", failures)
    require("admin_learning_dashboard" in service, "Admin dashboard data helper exists", failures)
    require("/admin/pulse-ai/learning" in routes and "/api/admin/pulse-ai/learning" in routes, "Admin learning routes exist", failures)
    require("data-pulse-ai-setting" in js and "data-pulse-ai-memory-clear" in js and "data-pulse-ai-memory-export" in js, "User learning settings controls exist", failures)
    require("private chats are not used" in template.lower() or "private chats" in template.lower(), "Admin page states privacy boundary", failures)
    require("Private chats are not used" in report or "private conversations" in report, "Learning report documents privacy boundary", failures)
    require("private messages" in knowledge.lower() and "do not secretly learn" in knowledge.lower(), "System prompt documents private-data boundary", failures)

    if failures:
        print("Pulse AI Learning Foundation audit failed:")
        for item in failures:
            print(f" - {item}")
        return 1
    print("Pulse AI Learning Foundation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
