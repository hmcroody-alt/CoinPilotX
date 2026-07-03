"""High-level task routing hints for Pulse AI.

This module does not call providers directly. It classifies the user request so
the Messenger service can choose retrieval, safety, web search, and provider
fallback behavior without creating another assistant system.
"""

from __future__ import annotations

import re
from typing import Any

from services import pulse_ai_safety, pulse_ai_web_search


PULSESOC_TERMS = {
    "pulsesoc",
    "status",
    "reels",
    "messenger",
    "pulse ai",
    "music",
    "notifications",
    "crypto alerts",
    "manage my alerts",
    "conversation control",
    "profile",
    "premium",
    "creator",
    "verification",
    "live",
    "lock screen",
}


def classify(message: str) -> dict[str, Any]:
    text = " ".join(str(message or "").lower().split())
    safety = pulse_ai_safety.classify_request(text)
    needs_web = pulse_ai_web_search.should_search(text)
    platform_help = any(term in text for term in PULSESOC_TERMS) or bool(re.search(r"\b(where|how|why|can)\b", text))
    if safety.get("disallowed"):
        task = "safety_refusal"
    elif safety.get("category") == "cyber":
        task = "cybersecurity"
    elif needs_web:
        task = "web_search"
    elif "code" in text or "api" in text or "technical" in text:
        task = "technical"
    elif platform_help:
        task = "pulsesoc_help"
    else:
        task = "general"
    return {
        "task": task,
        "needs_web_search": needs_web,
        "platform_help": platform_help,
        "safety": safety,
    }
