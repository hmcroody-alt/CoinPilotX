"""Feature readiness and public exposure controls for CoinPilotXAI.

The goal of this engine is conservative: public surfaces should only promise
features that are actually ready, while beta/internal systems remain visible to
admins for follow-up work.
"""

from __future__ import annotations

from datetime import UTC, datetime


VALID_STATES = {"enabled", "disabled", "beta", "internal-only", "premium-only", "owner-only"}


FEATURE_DEFINITIONS = [
    {
        "feature_key": "pulse_posts",
        "feature": "PulseSoc posts",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "realtime-ready",
        "mobile_status": "mobile-ready",
        "ai_integration": "supported",
        "monetization_readiness": "not monetized",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "low",
    },
    {
        "feature_key": "pulse_comments_reactions",
        "feature": "Comments and reactions",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "realtime-ready",
        "mobile_status": "mobile-ready",
        "ai_integration": "supported",
        "monetization_readiness": "not monetized",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "low",
    },
    {
        "feature_key": "pulse_messenger",
        "feature": "PulseSoc Messenger",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "fallback-ready",
        "mobile_status": "mobile-ready",
        "ai_integration": "limited",
        "monetization_readiness": "not monetized",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "pulse_spaces",
        "feature": "PulseSoc Spaces",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "partial",
        "mobile_status": "mobile-ready",
        "ai_integration": "recommendation-ready",
        "monetization_readiness": "not monetized",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "low",
    },
    {
        "feature_key": "pulse_groups",
        "feature": "PulseSoc Groups",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "partial",
        "mobile_status": "mobile-ready",
        "ai_integration": "limited",
        "monetization_readiness": "not monetized",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "pulse_reels",
        "feature": "PulseSoc Reels",
        "state": "beta",
        "backend_status": "partially complete",
        "frontend_status": "beta",
        "realtime_status": "partial",
        "mobile_status": "mobile-ready",
        "ai_integration": "ranking-ready",
        "monetization_readiness": "not monetized",
        "security_review": "needs deeper review",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "pulse_livestream",
        "feature": "PulseSoc Live",
        "state": "beta",
        "backend_status": "partially complete",
        "frontend_status": "beta",
        "realtime_status": "partial",
        "mobile_status": "needs device QA",
        "ai_integration": "limited",
        "monetization_readiness": "not ready",
        "security_review": "needs deeper review",
        "observability": "traceable",
        "risk_level": "high",
    },
    {
        "feature_key": "marketplace_browse",
        "feature": "Marketplace browsing",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "not required",
        "mobile_status": "mobile-ready",
        "ai_integration": "safety scan-ready",
        "monetization_readiness": "review-only",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "merchant_applications",
        "feature": "Merchant applications",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "not required",
        "mobile_status": "mobile-ready",
        "ai_integration": "risk scan-ready",
        "monetization_readiness": "approval gated",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "marketplace_checkout",
        "feature": "Marketplace checkout and payouts",
        "state": "internal-only",
        "backend_status": "internal design only",
        "frontend_status": "hidden from public",
        "realtime_status": "not required",
        "mobile_status": "not exposed",
        "ai_integration": "safety-only",
        "monetization_readiness": "not ready",
        "security_review": "not reviewed",
        "observability": "limited",
        "risk_level": "high",
    },
    {
        "feature_key": "premium_identity",
        "feature": "Premium identity",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "not required",
        "mobile_status": "mobile-ready",
        "ai_integration": "not required",
        "monetization_readiness": "PulseSoc Premium prestige",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "premium_advanced_tools",
        "feature": "Premium advanced tools",
        "state": "beta",
        "backend_status": "partially complete",
        "frontend_status": "beta",
        "realtime_status": "not required",
        "mobile_status": "mobile-ready",
        "ai_integration": "partial",
        "monetization_readiness": "PulseSoc Premium enhancement",
        "security_review": "needs deeper review",
        "observability": "limited",
        "risk_level": "medium",
    },
    {
        "feature_key": "ai_assistant",
        "feature": "PulseSoc AI Assistant",
        "state": "beta",
        "backend_status": "implemented",
        "frontend_status": "beta",
        "realtime_status": "not required",
        "mobile_status": "mobile-ready",
        "ai_integration": "AI-ready",
        "monetization_readiness": "premium-ready",
        "security_review": "guardrails required",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "creator_cockpit",
        "feature": "Creator cockpit",
        "state": "beta",
        "backend_status": "partially complete",
        "frontend_status": "beta",
        "realtime_status": "not required",
        "mobile_status": "mobile-ready",
        "ai_integration": "partial",
        "monetization_readiness": "premium-ready",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "medium",
    },
    {
        "feature_key": "admin_command",
        "feature": "Admin command center",
        "state": "enabled",
        "backend_status": "implemented",
        "frontend_status": "production ready",
        "realtime_status": "realtime-ready",
        "mobile_status": "mobile-ready",
        "ai_integration": "supported",
        "monetization_readiness": "internal only",
        "security_review": "reviewed",
        "observability": "traceable",
        "risk_level": "low",
    },
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_state(state: str | None) -> str:
    value = (state or "beta").strip().lower().replace("_", "-")
    return value if value in VALID_STATES else "beta"


def default_flags() -> list[dict]:
    flags = []
    for item in FEATURE_DEFINITIONS:
        state = normalize_state(item.get("state"))
        flags.append(
            {
                "feature_key": item["feature_key"],
                "label": item["feature"],
                "state": state,
                "rollout_percentage": 100 if state in {"enabled", "beta", "premium-only"} else 0,
                "premium_required": 1 if state == "premium-only" else 0,
                "owner_only": 1 if state == "owner-only" else 0,
                "internal_only": 1 if state == "internal-only" else 0,
                "public_label": "Beta" if state == "beta" else "Internal" if state == "internal-only" else "Live",
                "notes": "Seeded capability control.",
            }
        )
    return flags


def evaluate_flag(flag: dict | None, user: dict | None = None) -> dict:
    flag = flag or {}
    state = normalize_state(flag.get("state"))
    user = user or {}
    if state == "disabled":
        return {"visible": False, "usable": False, "state": state, "reason": "Feature disabled."}
    if state == "internal-only" and not user.get("is_admin"):
        return {"visible": False, "usable": False, "state": state, "reason": "Internal review only."}
    if state == "owner-only" and not user.get("is_owner"):
        return {"visible": False, "usable": False, "state": state, "reason": "Owner-only feature."}
    if state == "premium-only" and not (user.get("is_premium") or user.get("is_owner")):
        return {"visible": True, "usable": False, "state": state, "reason": "PulseSoc Premium prestige feature."}
    return {"visible": True, "usable": True, "state": state, "reason": "Available."}


def capability_matrix(runtime_status: dict | None = None, flags: list[dict] | None = None) -> list[dict]:
    runtime_status = runtime_status or {}
    flag_map = {item["feature_key"]: item for item in default_flags()}
    for flag in flags or []:
        key = flag.get("feature_key")
        if key:
            merged = dict(flag_map.get(key, {}))
            merged.update(flag)
            flag_map[key] = merged

    rows = []
    definitions = {item["feature_key"]: item for item in FEATURE_DEFINITIONS}
    for key, definition in definitions.items():
        flag = flag_map.get(key, {})
        state = normalize_state(flag.get("state") or definition.get("state"))
        runtime = runtime_status.get(key, {})
        rows.append(
            {
                "feature_key": key,
                "feature": definition["feature"],
                "backend_status": runtime.get("backend_status") or definition["backend_status"],
                "frontend_status": runtime.get("frontend_status") or definition["frontend_status"],
                "realtime_status": runtime.get("realtime_status") or definition["realtime_status"],
                "mobile_status": runtime.get("mobile_status") or definition["mobile_status"],
                "ai_integration": runtime.get("ai_integration") or definition["ai_integration"],
                "monetization_readiness": runtime.get("monetization_readiness") or definition["monetization_readiness"],
                "security_review": runtime.get("security_review") or definition["security_review"],
                "observability": runtime.get("observability") or definition["observability"],
                "last_tested": runtime.get("last_tested") or now_iso(),
                "risk_level": runtime.get("risk_level") or definition["risk_level"],
                "production_status": "production ready" if state == "enabled" and definition["risk_level"] != "high" else state,
                "flag_state": state,
                "rollout_percentage": int(flag.get("rollout_percentage") or 0),
                "premium_required": int(flag.get("premium_required") or 0),
                "owner_only": int(flag.get("owner_only") or 0),
                "internal_only": int(flag.get("internal_only") or 0),
                "notes": flag.get("notes") or "",
            }
        )
    return rows
