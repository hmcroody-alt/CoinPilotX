"""AI memory system foundation."""

from __future__ import annotations


def memory_card(subject_type: str, subject_id, summary: str, importance: int = 50) -> dict:
    return {
        "subject_type": subject_type,
        "subject_id": str(subject_id),
        "summary": str(summary or "")[:1000],
        "importance": max(0, min(100, int(importance or 0))),
    }
