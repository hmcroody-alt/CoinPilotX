"""User-created AI agent architecture."""

from __future__ import annotations


AGENT_TYPES = {
    "market_analyst": ["summarize_market", "monitor_events", "notify_user"],
    "moderation_ai": ["flag_abuse", "summarize_reports", "suggest_action"],
    "learning_coach": ["explain_topic", "build_study_plan", "quiz_user"],
    "creator_assistant": ["caption", "hashtags", "growth_tips"],
    "scam_hunter": ["scan_links", "detect_patterns", "escalate_risk"],
    "community_manager": ["summarize_space", "welcome_users", "spot_conflict"],
}


def agent_blueprint(agent_type: str, owner_user_id=0, name: str = "") -> dict:
    kind = str(agent_type or "creator_assistant").strip().lower()
    return {
        "owner_user_id": int(owner_user_id or 0),
        "agent_type": kind,
        "name": (name or kind.replace("_", " ").title())[:120],
        "capabilities": AGENT_TYPES.get(kind, AGENT_TYPES["creator_assistant"]),
        "status": "draft",
        "safety_mode": "strict",
    }


def safe_agent_response(agent=None, prompt: str = "") -> dict:
    agent = agent or agent_blueprint("creator_assistant")
    text = str(prompt or "").strip()
    if not text:
        return {"ok": False, "message": "Ask the agent a clear question."}
    return {
        "ok": True,
        "agent_type": agent.get("agent_type"),
        "answer": "Agent foundation is ready. Connect model execution to enable autonomous responses.",
        "suggested_actions": agent.get("capabilities", [])[:3],
    }
