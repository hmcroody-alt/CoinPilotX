"""Track outcomes from AI recommendations and admin actions."""

from __future__ import annotations

from datetime import datetime


def result_payload(recommendation_id=0, action_request_id=0, status="success", useful=None, owner_feedback="", metrics=None) -> dict:
    return {
        "recommendation_id": int(recommendation_id or 0),
        "action_request_id": int(action_request_id or 0),
        "status": str(status or "success")[:40],
        "useful": useful,
        "owner_feedback": str(owner_feedback or "")[:1000],
        "metrics": metrics or {},
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def score_outcome(results=None) -> dict:
    results = results or []
    total = len(results)
    success = sum(1 for item in results if item.get("status") == "success")
    useful = sum(1 for item in results if item.get("useful") is True)
    failed = sum(1 for item in results if item.get("status") == "failed")
    return {
        "total": total,
        "success_rate": round(100 * success / max(1, total), 2),
        "usefulness_rate": round(100 * useful / max(1, total), 2),
        "failure_count": failed,
        "repeated_issue_detected": failed >= 3,
    }
