"""Pulse operating-system UI state helpers."""

from __future__ import annotations


def default_workspace(user_id=0) -> dict:
    return {
        "user_id": int(user_id or 0),
        "panels": ["feed", "ai_assistant", "notifications"],
        "command_palette": True,
        "layout": "mobile_first",
    }
