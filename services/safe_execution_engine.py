"""Safe execution engine for approved AI/admin actions."""

from __future__ import annotations

from datetime import datetime


SAFE_ACTIONS = {
    "create_admin_task",
    "send_admin_alert",
    "retry_failed_queue",
    "pause_broken_provider",
    "mark_content_for_review",
    "reduce_content_visibility",
    "request_user_verification",
    "feature_safe_creator",
    "recommend_creator_promotion",
    "create_support_ticket",
    "create_product_bug_ticket",
}

DANGEROUS_ACTIONS = {
    "permanent_ban",
    "delete_account",
    "change_pricing",
    "spend_money",
    "mass_marketing",
    "grant_owner_privileges",
    "disable_security",
}


def classify_action(action_type: str) -> dict:
    action_type = str(action_type or "create_admin_task").strip().lower()
    return {
        "action_type": action_type,
        "safe": action_type in SAFE_ACTIONS,
        "dangerous": action_type in DANGEROUS_ACTIONS,
        "owner_approval_required": action_type in DANGEROUS_ACTIONS or action_type in {"pause_broken_provider", "reduce_content_visibility"},
    }


def execute(action_type: str, payload=None, approved: bool = False) -> dict:
    payload = payload or {}
    info = classify_action(action_type)
    if info["dangerous"]:
        return {"status": "needs_manual_review", "message": "Dangerous actions require owner-controlled manual execution.", "executed_at": datetime.utcnow().isoformat(timespec="seconds")}
    if info["owner_approval_required"] and not approved:
        return {"status": "skipped", "message": "Owner approval is required before this action can execute.", "executed_at": datetime.utcnow().isoformat(timespec="seconds")}
    if not info["safe"]:
        return {"status": "needs_manual_review", "message": "Action type is not registered as safely executable.", "executed_at": datetime.utcnow().isoformat(timespec="seconds")}
    messages = {
        "create_admin_task": "Admin task created for human follow-up.",
        "send_admin_alert": "Admin alert queued.",
        "retry_failed_queue": "Retry request queued for failed jobs.",
        "pause_broken_provider": "Provider pause requested for approved operator review.",
        "mark_content_for_review": "Content review flag queued.",
        "reduce_content_visibility": "Visibility reduction queued for approved safety review.",
        "request_user_verification": "Verification request queued.",
        "feature_safe_creator": "Creator feature recommendation queued.",
        "recommend_creator_promotion": "Creator promotion recommendation queued.",
        "create_support_ticket": "Support ticket task created.",
        "create_product_bug_ticket": "Product bug task created.",
    }
    return {
        "status": "success",
        "message": messages.get(info["action_type"], "Safe action executed."),
        "result": {"payload_summary": {k: payload.get(k) for k in list(payload.keys())[:8]}},
        "executed_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def rollback_plan(action_type: str) -> list[str]:
    plans = {
        "pause_broken_provider": ["Resume provider after health check passes."],
        "reduce_content_visibility": ["Restore content visibility."],
        "feature_safe_creator": ["Remove creator feature boost."],
        "create_admin_task": ["Close or reopen generated task."],
    }
    return plans.get(str(action_type or "").lower(), ["Manual review required; no automatic rollback available."])
