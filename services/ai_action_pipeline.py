"""AI recommendation to action workflow pipeline.

The pipeline converts internal intelligence recommendations into auditable,
owner-controllable work items. It never executes dangerous actions directly.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha1


SUPPORTED_TYPES = {
    "product_issue",
    "security_risk",
    "growth_opportunity",
    "creator_opportunity",
    "trust_safety_risk",
    "infrastructure_warning",
    "monetization_opportunity",
    "moderation_review",
    "livestream_issue",
    "marketplace_risk",
    "notification_failure",
}

DANGEROUS_ACTION_WORDS = {
    "ban",
    "delete",
    "pricing",
    "spend",
    "mass notification",
    "freeze marketplace",
    "shutdown livestream",
    "revoke privilege",
    "disable security",
}


def recommendation_id(source_engine: str, title: str, affected_area: str = "") -> str:
    raw = f"{source_engine}|{title}|{affected_area}|{datetime.utcnow().date().isoformat()}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_recommendation(data=None) -> dict:
    data = data or {}
    recommended_action = str(data.get("recommended_action") or data.get("recommendation") or "Create admin task.").strip()
    risk_level = str(data.get("risk_level") or data.get("priority") or "normal").lower()
    owner_required = bool(data.get("owner_approval_required"))
    if any(word in recommended_action.lower() for word in DANGEROUS_ACTION_WORDS):
        owner_required = True
        risk_level = "critical" if risk_level in {"normal", "low"} else risk_level
    rec_type = str(data.get("recommendation_type") or data.get("type") or "product_issue").lower()
    if rec_type not in SUPPORTED_TYPES:
        rec_type = "product_issue"
    source = str(data.get("source_engine") or "meta_intelligence_engine")[:100]
    title = str(data.get("title") or "AI recommended action")[:180]
    area = str(data.get("affected_area") or "global_command")[:100]
    return {
        "external_id": data.get("id") or recommendation_id(source, title, area),
        "source_engine": source,
        "recommendation_type": rec_type,
        "title": title,
        "description": str(data.get("description") or data.get("reason") or recommended_action)[:2000],
        "priority": str(data.get("priority") or risk_level or "normal")[:40],
        "confidence": max(0.0, min(1.0, float(data.get("confidence") or 0.5))),
        "risk_level": risk_level[:40],
        "recommended_action": recommended_action[:1200],
        "owner_approval_required": owner_required,
        "estimated_impact": str(data.get("estimated_impact") or "Operational risk reduction")[:500],
        "affected_area": area,
        "status": str(data.get("status") or "open")[:40],
        "created_at": data.get("created_at") or datetime.utcnow().isoformat(timespec="seconds"),
    }


def from_meta_coordination(meta=None, fabric=None) -> dict:
    meta = meta or {}
    fabric = fabric or {}
    decision = meta.get("decision") or {}
    priority = decision.get("priority") or fabric.get("priority") or "normal"
    risk = "critical" if priority == "critical" else "high" if priority == "high" else "normal"
    return normalize_recommendation({
        "source_engine": "meta_intelligence_engine",
        "recommendation_type": "infrastructure_warning" if "failsafe" in str(decision.get("decision") or "").lower() else "product_issue",
        "title": "Global command recommended action",
        "description": decision.get("reason") or fabric.get("recommendation") or "Review global command intelligence.",
        "priority": priority,
        "confidence": decision.get("confidence") or fabric.get("confidence") or 0.5,
        "risk_level": risk,
        "recommended_action": decision.get("decision") or fabric.get("recommendation") or "Create admin task for review.",
        "owner_approval_required": bool(decision.get("requires_owner_approval")),
        "estimated_impact": "Improves platform stability, trust, or operational response time.",
        "affected_area": "global_command",
    })


def department_for_recommendation(rec=None) -> str:
    rec = rec or {}
    mapping = {
        "security_risk": "security",
        "trust_safety_risk": "trust-safety",
        "moderation_review": "moderation",
        "creator_opportunity": "creators",
        "growth_opportunity": "growth",
        "monetization_opportunity": "monetization",
        "livestream_issue": "delivery",
        "marketplace_risk": "monetization",
        "notification_failure": "delivery",
        "infrastructure_warning": "engineering",
        "product_issue": "engineering",
    }
    return mapping.get(str(rec.get("recommendation_type") or ""), "engineering")
