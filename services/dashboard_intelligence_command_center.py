"""Backend-managed PulseSoc Intelligence Center state.

This module powers the user-facing Intelligence Center and the protected
admin Intelligence Command Center. It intentionally returns aggregate,
owner-scoped, and redacted data only: no raw tokens, private messages,
provider secrets, or internal-only prompts are exposed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services import db as db_service


STRICT_STATES = {"READY", "ACTION", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "COMING SOON", "ADMIN"}


INTELLIGENCE_SECTIONS: tuple[dict[str, Any], ...] = (
    {"key": "scam-intelligence", "label": "Scam Intelligence", "route": "/admin/intelligence-command-center/scam-intelligence", "description": "Scam patterns, fake links, fake accounts, fake giveaways, fake jobs, marketplace risk, community reports, and review state."},
    {"key": "alert-management", "label": "Alert Management", "route": "/admin/intelligence-command-center/alert-management", "description": "Active alerts, local/global warnings, trending scams, alert priority, delivery routing, dismissals, and audit state."},
    {"key": "pulse-brain", "label": "Pulse Brain", "route": "/admin/intelligence-command-center/pulse-brain", "description": "Community mood, platform health, topic intelligence, creator signals, safety signals, summaries, and daily briefing."},
    {"key": "ai-advisor", "label": "AI Advisor", "route": "/admin/intelligence-command-center/ai-advisor", "description": "Personalized recommendations, explanations, missed opportunities, creator/network/safety advice, and reasoning audit."},
    {"key": "safety-scanner", "label": "Safety Scanner", "route": "/admin/intelligence-command-center/safety-scanner", "description": "Message, link, file, device, session, and suspicious activity scans with risk score and recovery actions."},
    {"key": "recommendation-engine", "label": "Recommendation Engine", "route": "/admin/intelligence-command-center/recommendation-engine", "description": "Privacy-safe people, groups, posts, videos, music, marketplace, creator, and community recommendations."},
    {"key": "security-operations", "label": "Security Operations", "route": "/admin/intelligence-command-center/security-operations", "description": "Safety score, device security, login security, privacy health, scam protection, recovery state, and security timeline."},
    {"key": "threat-intelligence", "label": "Threat Intelligence", "route": "/admin/intelligence-command-center/threat-intelligence", "description": "Current threats, blocked threats, suspicious accounts, emerging risks, severity, AI summaries, and resolution history."},
    {"key": "risk-assessment", "label": "Risk Assessment", "route": "/admin/intelligence-command-center/risk-assessment", "description": "Account, device, network, financial, reputation, marketplace, confidence, and timeline risk signals."},
    {"key": "trust-intelligence", "label": "Trust Intelligence", "route": "/admin/intelligence-command-center/trust-intelligence", "description": "Reputation, trust score, reports, copyright, violations, appeals, improvement plans, and trust timeline."},
    {"key": "signal-intelligence", "label": "Signal Intelligence", "route": "/admin/intelligence-command-center/signal-intelligence", "description": "Feed, community, trend, creator, engagement, safety, and recommendation signals."},
    {"key": "research-engine", "label": "Research Engine", "route": "/admin/intelligence-command-center/research-engine", "description": "AI research workspace, topic research, source summaries, saved research, citations, usage limits, and exports."},
    {"key": "feed-intelligence", "label": "Feed Intelligence", "route": "/admin/intelligence-command-center/feed-intelligence", "description": "Feed summaries, hidden trends, recommended reading, creator opportunities, highlights, and personalized briefings."},
    {"key": "prediction-engine", "label": "Prediction Engine", "route": "/admin/intelligence-command-center/prediction-engine", "description": "Future risks, opportunities, creator/community/growth forecasts, confidence levels, and prediction history."},
    {"key": "heatmap-engine", "label": "Heatmap Engine", "route": "/admin/intelligence-command-center/heatmap-engine", "description": "Privacy-safe global activity, community growth, topic heat, engagement heat, safety heat, and discovery heat."},
    {"key": "audit", "label": "Intelligence Audit Logs", "route": "/admin/intelligence-command-center/audit", "description": "Scam, alert, recommendation, risk, trust, AI, prediction, and admin intelligence audit coverage."},
)


INTELLIGENCE_SUBSYSTEM_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "key": "scam-shield",
        "card_key": "scam_shield",
        "label": "Scam Shield",
        "route": "/dashboard/intelligence/scam-shield",
        "admin_route": "/admin/intelligence-command-center/scam-intelligence",
        "action": "Protection Center",
        "metric": "scam_events",
        "state_signal": "scam_alerts",
        "description": "Scam database, fake accounts, fake giveaways, fake crypto, fake jobs, marketplace fraud, link risk, reports, and safety recommendations.",
        "intelligence": "Scores scam, phishing, fake account, and marketplace-risk signals from safe aggregate events.",
        "prediction": "Raises confidence when suspicious link patterns, repeated reports, or known scam keywords cluster.",
        "automation": "Routes risky findings to alert management, threat intelligence, network security, notifications, and admin review.",
        "safety": "Flags risk and recommends review; it does not auto-ban or delete without authorized moderation.",
        "explainability": "Explains why something is suspicious using safe evidence summaries and confidence labels.",
        "recommendations": ("Review new scam alerts.", "Avoid suspicious links until verified.", "Report fake accounts instead of engaging."),
    },
    {
        "key": "scam-alerts",
        "card_key": "scam_alerts",
        "label": "Scam Alerts",
        "route": "/dashboard/intelligence/scam-alerts",
        "admin_route": "/admin/intelligence-command-center/alert-management",
        "action": "Alert Center",
        "metric": "current_scam_alerts",
        "state_signal": "scam_alerts",
        "description": "Active, local, global, friend/community, crypto, marketplace, and trending scam alerts with priority and history.",
        "intelligence": "Prioritizes alerts based on severity, recency, report volume, and user relevance.",
        "prediction": "Surfaces likely next scam waves when related events rise quickly.",
        "automation": "Feeds notifications, dismissals, quiet-hour policy, and admin review queues.",
        "safety": "Uses privacy-safe alert summaries and never exposes reporter identity.",
        "explainability": "Shows alert priority, source class, and recommended user action.",
        "recommendations": ("Check priority alerts first.", "Dismiss alerts only after reading the safety note.", "Escalate repeated scam patterns."),
    },
    {
        "key": "pulse-brain",
        "card_key": "pulse_intelligence",
        "label": "Pulse Brain",
        "route": "/dashboard/intelligence/pulse-brain",
        "admin_route": "/admin/intelligence-command-center/pulse-brain",
        "action": "Open Pulse Brain",
        "metric": "platform_health",
        "description": "Community mood, platform health, trending communities, topic/creator intelligence, safety signals, summaries, and daily briefing.",
        "intelligence": "Combines safe platform, feed, community, safety, and creator signals into one readable briefing.",
        "prediction": "Estimates community momentum and next useful actions from aggregate signals.",
        "automation": "Updates feed intelligence, recommendations, creator studio, community heatmaps, and daily briefing.",
        "safety": "Uses aggregate public/platform signals only.",
        "explainability": "Explains each briefing with confidence and source categories.",
        "recommendations": ("Review the daily briefing.", "Watch trending communities.", "Use safety signals before engaging with risky topics."),
    },
    {
        "key": "ai-advisor",
        "card_key": "ai_insights",
        "label": "AI Advisor",
        "route": "/dashboard/intelligence/ai-advisor",
        "admin_route": "/admin/intelligence-command-center/ai-advisor",
        "action": "Ask AI Advisor",
        "metric": "ai_recommendations",
        "state": "BETA",
        "description": "Daily summaries, personalized recommendations, missed opportunities, creator/network/safety advice, explanations, and usage limits.",
        "intelligence": "Turns safe user and platform signals into explainable suggestions.",
        "prediction": "Suggests likely next-best actions when enough safe signals exist.",
        "automation": "Stays hidden or disabled when AI is off; does not block core PulseSoc behavior.",
        "safety": "Never sends private messages to external AI unless explicitly configured and permission-safe.",
        "explainability": "Shows why each recommendation exists and whether confidence is low, medium, or high.",
        "recommendations": ("Use suggestions as guidance, not automatic actions.", "Review privacy-sensitive recommendations carefully.", "Keep AI disabled states honest."),
    },
    {
        "key": "safety-scan",
        "card_key": "safety_scan",
        "label": "Safety Scan",
        "route": "/dashboard/intelligence/safety-scan",
        "admin_route": "/admin/intelligence-command-center/safety-scanner",
        "action": "Scan My Account",
        "metric": "risk_score",
        "description": "Message, link, file, device, session, suspicious activity, recovery, and threat detection scan state.",
        "intelligence": "Combines user-visible risk signals from login, device, link, report, and safety event sources.",
        "prediction": "Highlights likely weak spots before they become account restrictions.",
        "automation": "Updates threat intelligence, risk assessment, trust intelligence, account security, and recommendations.",
        "safety": "Uses owner-scoped signals and redacts private content.",
        "explainability": "Each scan result shows risk level, confidence, and recommended recovery step.",
        "recommendations": ("Run scan after new-device alerts.", "Review suspicious links before opening.", "Follow recovery steps when risk is elevated."),
    },
    {
        "key": "smart-recommendations",
        "card_key": "recommendations",
        "label": "Smart Recommendations",
        "route": "/dashboard/intelligence/smart-recommendations",
        "admin_route": "/admin/intelligence-command-center/recommendation-engine",
        "action": "Explore Recommendations",
        "metric": "new_opportunities",
        "state": "BETA",
        "description": "People, groups, communities, posts, videos, reels, learning, marketplace, music, and creator suggestions.",
        "intelligence": "Ranks recommendations with privacy-safe signals and no private account leakage.",
        "prediction": "Learns which suggestions are useful without exposing sensitive targeting details.",
        "automation": "Feeds Network, Creator, Music, Marketplace, Feed, and Community recommendation surfaces.",
        "safety": "Respects blocks, privacy, moderation, and hidden/private content rules.",
        "explainability": "Shows broad reason categories instead of internal targeting data.",
        "recommendations": ("Review suggestions periodically.", "Hide irrelevant suggestions to improve future ranking.", "Use community recommendations to grow safely."),
    },
    {
        "key": "security-intelligence",
        "card_key": "safety_center",
        "label": "Security Intelligence",
        "route": "/dashboard/intelligence/security-intelligence",
        "admin_route": "/admin/intelligence-command-center/security-operations",
        "action": "Review Security",
        "metric": "safety_score",
        "description": "Safety score, checklist, device security, login security, privacy health, scam protection, recovery, and security timeline.",
        "intelligence": "Turns account and platform security signals into a user-readable safety score.",
        "prediction": "Warns when device, login, or privacy risk trends upward.",
        "automation": "Updates Account Security, Threat Detection, Device Intelligence, and Notifications.",
        "safety": "Sensitive security details are redacted and owner-scoped.",
        "explainability": "Shows which security category changed and what to do next.",
        "recommendations": ("Review device trust.", "Keep 2FA enabled where available.", "Act on high-risk alerts quickly."),
    },
    {
        "key": "threat-intelligence",
        "card_key": "threat_intelligence",
        "label": "Threat Intelligence",
        "route": "/dashboard/intelligence/threat-intelligence",
        "admin_route": "/admin/intelligence-command-center/threat-intelligence",
        "action": "Analyze Threats",
        "metric": "active_threats",
        "description": "Current threats, suspicious accounts, blocked threats, emerging risks, AI threat summary, severity, and resolution history.",
        "intelligence": "Classifies threats by severity, confidence, affected surface, and resolution state.",
        "prediction": "Detects emerging clusters when failed logins, reports, or scam patterns spike.",
        "automation": "Connects to Scam Shield, Alert Center, Safety Center, Network Security, and admin security review.",
        "safety": "Flags and escalates; does not expose private messages or reporter identity.",
        "explainability": "Threat cards include severity and safe evidence summaries.",
        "recommendations": ("Review critical threats first.", "Mark safe only after evidence review.", "Escalate repeated suspicious accounts."),
    },
    {
        "key": "risk-assessment",
        "card_key": "risk_scanner",
        "label": "Risk Assessment",
        "route": "/dashboard/intelligence/risk-assessment",
        "admin_route": "/admin/intelligence-command-center/risk-assessment",
        "action": "Assess Risk",
        "metric": "risk_score",
        "state": "PARTIAL",
        "description": "Account, device, network, financial, reputation, marketplace, confidence, and risk timeline.",
        "intelligence": "Aggregates risk without exposing private data or billing internals.",
        "prediction": "Projects whether risk is improving, stable, or worsening.",
        "automation": "Updates Trust Intelligence, Security Operations, Account Health, and admin review queues.",
        "safety": "Financial and marketplace risks remain summaries; provider secrets and private records stay hidden.",
        "explainability": "Risk score includes category-level reasons and confidence.",
        "recommendations": ("Review high-risk categories.", "Fix account security before marketplace activity.", "Appeal incorrect trust signals."),
    },
    {
        "key": "trust-intelligence",
        "card_key": "reputation_monitoring",
        "label": "Trust Intelligence",
        "route": "/dashboard/intelligence/trust-intelligence",
        "admin_route": "/admin/intelligence-command-center/trust-intelligence",
        "action": "Review Trust",
        "metric": "trust_score",
        "state": "PARTIAL",
        "description": "Reputation, trust score, reports, copyright, violations, appeals, improvement plan, and trust timeline.",
        "intelligence": "Connects account health, moderation, creator reputation, copyright, and appeals.",
        "prediction": "Shows whether trust is likely to improve after recommended actions.",
        "automation": "Updates Account Health, Creator Reputation, Monetization, and Intelligence Hub.",
        "safety": "Reporter identity and moderator-only notes are never shown to users.",
        "explainability": "Explains trust score with safe categories and improvement steps.",
        "recommendations": ("Resolve warnings and appeals.", "Avoid reposting disputed content.", "Keep profile and content policy-safe."),
    },
    {
        "key": "signal-intelligence",
        "card_key": "deep_signal_analysis",
        "label": "Signal Intelligence",
        "route": "/dashboard/intelligence/signal-intelligence",
        "admin_route": "/admin/intelligence-command-center/signal-intelligence",
        "action": "Analyze Signals",
        "metric": "trending_topics",
        "state": "PARTIAL",
        "description": "Feed, community, trend, creator, engagement, safety, and recommendation signals.",
        "intelligence": "Finds safe aggregate signal movement across PulseSoc.",
        "prediction": "Identifies emerging topics and safety signals before they dominate the feed.",
        "automation": "Feeds Pulse Brain, Feed Intelligence, Recommendations, Creator Studio, and Heatmaps.",
        "safety": "Uses aggregate/public signals only.",
        "explainability": "Each signal includes source category and confidence.",
        "recommendations": ("Watch signal changes before posting.", "Use signals to find safer communities.", "Avoid trend-chasing unsafe topics."),
    },
    {
        "key": "research-workspace",
        "card_key": "ai_research_assistant",
        "label": "Research Workspace",
        "route": "/dashboard/intelligence/research-workspace",
        "admin_route": "/admin/intelligence-command-center/research-engine",
        "action": "Start Research",
        "metric": "research_items",
        "state": "BETA",
        "description": "Ask AI, topic research, source summaries, trend/market/creator research, saved research, citations, exports, and usage limits.",
        "intelligence": "Organizes research tasks and keeps source-aware output separate from private social content.",
        "prediction": "Suggests follow-up research when topic momentum changes.",
        "automation": "Connects to Feed Intelligence, Creator Studio, and saved research surfaces when enabled.",
        "safety": "External-source summaries require citations; private user data is not exported.",
        "explainability": "Research output includes source/citation readiness where available.",
        "recommendations": ("Save useful research.", "Require sources for factual claims.", "Keep private conversations out of research prompts."),
    },
    {
        "key": "feed-intelligence",
        "card_key": "ai_feed_intelligence",
        "label": "Feed Intelligence",
        "route": "/dashboard/intelligence/feed-intelligence",
        "admin_route": "/admin/intelligence-command-center/feed-intelligence",
        "action": "View Feed Intelligence",
        "metric": "feed_signals",
        "state": "BETA",
        "description": "Feed summary, hidden trends, recommended reading, creator opportunities, community highlights, and personalized briefing.",
        "intelligence": "Summarizes feed movement without exposing hidden/private content.",
        "prediction": "Highlights likely useful posts and communities.",
        "automation": "Updates Pulse Brain, Recommendations, Creator Studio, and Community Activity.",
        "safety": "Respects blocks, private content, and moderation state.",
        "explainability": "Recommendations include broad reason categories.",
        "recommendations": ("Review feed summary before deep scrolling.", "Follow safe communities.", "Use creator opportunities carefully."),
    },
    {
        "key": "prediction-center",
        "card_key": "predictive_alerts",
        "label": "Prediction Center",
        "route": "/dashboard/intelligence/prediction-center",
        "admin_route": "/admin/intelligence-command-center/prediction-engine",
        "action": "View Predictions",
        "metric": "prediction_confidence",
        "state": "PARTIAL",
        "description": "Future risks, opportunities, creator predictions, growth predictions, community predictions, forecasts, confidence, and history.",
        "intelligence": "Turns safe historical and live signals into cautious forecasts.",
        "prediction": "Uses confidence bands and avoids pretending low-data forecasts are certain.",
        "automation": "Feeds Creator, Network, Pulse Brain, and alert recommendation surfaces.",
        "safety": "Prediction output is advisory only; no unsafe automatic action.",
        "explainability": "Every prediction has confidence and reason category.",
        "recommendations": ("Treat predictions as guidance.", "Prioritize high-confidence safety alerts.", "Ignore low-confidence noise."),
    },
    {
        "key": "pulse-heatmap",
        "card_key": "community_heatmaps",
        "label": "Pulse Heatmap",
        "route": "/dashboard/intelligence/pulse-heatmap",
        "admin_route": "/admin/intelligence-command-center/heatmap-engine",
        "action": "Explore Heatmaps",
        "metric": "community_heat",
        "state": "PARTIAL",
        "description": "Global activity, privacy-safe regional activity, community growth, topic heat, engagement heat, safety heat, and discovery heat.",
        "intelligence": "Maps aggregate movement without exact geolocation or private identities.",
        "prediction": "Shows where safe discovery momentum is increasing.",
        "automation": "Feeds Pulse Brain, Recommendations, Creator Studio, and moderation signals.",
        "safety": "No exact user location or private status leakage.",
        "explainability": "Heat levels are shown as aggregate confidence labels.",
        "recommendations": ("Explore high-trust communities.", "Avoid low-safety heat spikes.", "Use heatmaps for discovery, not surveillance."),
    },
)


SUBSYSTEMS_BY_CARD = {item["card_key"]: item for item in INTELLIGENCE_SUBSYSTEM_BLUEPRINTS}
SUBSYSTEMS_BY_KEY = {item["key"]: item for item in INTELLIGENCE_SUBSYSTEM_BLUEPRINTS}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _param_sql(sql: str) -> str:
    return sql.replace("?", "%s") if db_service.IS_POSTGRES else sql


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(_param_sql(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}"), params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _latest_created(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> str:
    if not _table_exists(cur, table):
        return ""
    try:
        cur.execute(_param_sql(f"SELECT created_at FROM {table} WHERE {where} ORDER BY created_at DESC LIMIT 1"), params)
        return str(_row_value(cur.fetchone(), "created_at", 0, "") or "")
    except Exception:
        return ""


def _metric_map(cur: Any, user_id: int) -> dict[str, Any]:
    # Security/scam source compatibility: older and newer tables may both exist.
    user_security_events = _count(cur, "security_events", "user_id=?", (user_id,)) + _count(cur, "command_center_security_events", "user_id=?", (user_id,))
    high_security_events = (
        _count(cur, "security_events", "user_id=? AND lower(coalesce(severity,'')) IN ('high','critical')", (user_id,))
        + _count(cur, "command_center_security_events", "user_id=? AND lower(coalesce(severity,'')) IN ('high','critical')", (user_id,))
    )
    scam_events = (
        _count(cur, "security_events", "lower(coalesce(event_type,'')) LIKE '%scam%' OR lower(coalesce(event_type,'')) LIKE '%phishing%'", ())
        + _count(cur, "command_center_security_events", "lower(coalesce(event_type,'')) LIKE '%scam%' OR lower(coalesce(event_type,'')) LIKE '%phishing%'", ())
    )
    user_reports = _count(cur, "reports", "reported_user_id=? OR reporter_user_id=?", (user_id, user_id))
    moderation_cases = _count(cur, "moderation_cases", "user_id=? OR target_user_id=?", (user_id, user_id))
    posts = _count(cur, "posts", "user_id=?", (user_id,))
    reels = _count(cur, "pulse_reels", "user_id=?", (user_id,))
    videos = _count(cur, "pulse_videos", "user_id=?", (user_id,))
    groups = _count(cur, "groups", "owner_user_id=?", (user_id,))
    notifications = _count(cur, "notifications", "user_id=? OR recipient_id=?", (user_id, user_id))
    unread_notifications = _count(cur, "notifications", "(user_id=? OR recipient_id=?) AND lower(coalesce(status,'')) IN ('unread','created','queued')", (user_id, user_id))
    ai_events = _count(cur, "command_center_ai_events", "user_id=?", (user_id,))
    ai_usage = _count(cur, "ai_usage_logs", "user_id=?", (user_id,))
    recommendations = _count(cur, "dashboard_recommendations", "user_id=?", (user_id,)) + _count(cur, "recommendations", "user_id=?", (user_id,))
    public_posts = _count(cur, "posts", "coalesce(visibility,'public')='public'", ())
    active_threats = high_security_events
    safety_score = max(0, 100 - min(70, high_security_events * 12 + user_reports * 4 + moderation_cases * 6))
    risk_score = min(100, high_security_events * 18 + user_reports * 5 + moderation_cases * 8)
    trust_score = max(0, 100 - min(65, risk_score // 2 + moderation_cases * 4))
    platform_health = max(55, 94 - min(28, scam_events // 4))
    prediction_confidence = 72 if (posts + reels + videos + notifications + user_security_events) else 48
    community_heat = min(100, 35 + min(50, public_posts // 10))
    return {
        "overall_intelligence_score": int((safety_score + trust_score + platform_health + max(0, 100 - risk_score)) / 4),
        "platform_health": platform_health,
        "safety_score": safety_score,
        "active_threats": active_threats,
        "current_scam_alerts": scam_events,
        "scam_alerts": scam_events,
        "ai_recommendations": recommendations + ai_events + ai_usage,
        "trending_topics": max(0, min(12, public_posts // 25)),
        "community_mood": "Curious" if public_posts else "Quiet",
        "risk_score": risk_score,
        "trust_score": trust_score,
        "prediction_confidence": prediction_confidence,
        "security_events": user_security_events,
        "new_opportunities": max(recommendations, min(9, posts + reels + videos + groups)),
        "creator_insights": posts + reels + videos,
        "scam_events": scam_events,
        "research_items": ai_events,
        "feed_signals": public_posts,
        "community_heat": community_heat,
        "unread_notifications": unread_notifications,
        "notifications_total": notifications,
        "last_security_event": _latest_created(cur, "security_events", "user_id=?", (user_id,)) or _latest_created(cur, "command_center_security_events", "user_id=?", (user_id,)),
    }


def _state_for_blueprint(blueprint: dict[str, Any], metrics: dict[str, Any]) -> str:
    explicit = blueprint.get("state")
    if explicit:
        return explicit
    signal = blueprint.get("state_signal")
    if signal and _safe_int(metrics.get(signal), 0) > 0:
        return "WARNING"
    if blueprint["card_key"] in {"ai_insights", "ai_research_assistant", "ai_feed_intelligence"}:
        return "BETA"
    return "READY"


def _confidence_for_state(state: str, metrics: dict[str, Any]) -> int:
    if state == "WARNING":
        return 86
    if state == "READY":
        return 82
    if state == "BETA":
        return 68
    if state == "PARTIAL":
        return 58
    return max(35, min(92, _safe_int(metrics.get("prediction_confidence"), 52)))


def _build_subsystem(blueprint: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    state = _state_for_blueprint(blueprint, metrics)
    metric_key = str(blueprint.get("metric") or "")
    count = _safe_int(metrics.get(metric_key), 0)
    confidence = _confidence_for_state(state, metrics)
    if state == "WARNING":
        detail = "Risk signal detected. Review before continuing."
    elif state == "PARTIAL":
        detail = "Functional diagnostics exist; deeper automation is staged."
    elif state == "BETA":
        detail = "Functional beta with safe fallback and no core dependency."
    else:
        detail = "Ready and backend-managed."
    return {
        **blueprint,
        "state": state,
        "count": count,
        "confidence": confidence,
        "detail": detail,
        "cta_label": blueprint.get("action"),
        "audit": "Owner-visible intelligence actions are audit-ready; admin actions are role-gated.",
        "backend": blueprint.get("admin_route"),
    }


def build_intelligence_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    cur = conn.cursor()
    user_id = _safe_int(user.get("user_id") or user.get("id"), 0)
    metrics = _metric_map(cur, user_id)
    hub_recommendations = [
        "Review active alerts before opening suspicious links." if metrics["current_scam_alerts"] else "Scam protection is quiet. Keep reporting suspicious activity.",
        "Run a safety scan after any new-device or unusual-login alert." if metrics["security_events"] else "Security signals are calm. Keep device trust current.",
        "Use Pulse Brain to review community mood and opportunity signals.",
    ]
    hub = {
        "overall_intelligence_score": metrics["overall_intelligence_score"],
        "platform_health": metrics["platform_health"],
        "safety_score": metrics["safety_score"],
        "active_threats": metrics["active_threats"],
        "current_scam_alerts": metrics["current_scam_alerts"],
        "ai_recommendations": metrics["ai_recommendations"],
        "trending_topics": metrics["trending_topics"],
        "community_mood": metrics["community_mood"],
        "risk_score": metrics["risk_score"],
        "trust_score": metrics["trust_score"],
        "prediction_confidence": metrics["prediction_confidence"],
        "security_events": metrics["security_events"],
        "new_opportunities": metrics["new_opportunities"],
        "creator_insights": metrics["creator_insights"],
        "personalized_daily_brief": "Your intelligence layer is monitoring safety, feed momentum, recommendations, and trust signals without exposing private data.",
        "recommended_next_actions": hub_recommendations,
    }
    subsystems = {
        blueprint["key"].replace("-", "_"): _build_subsystem(blueprint, metrics)
        for blueprint in INTELLIGENCE_SUBSYSTEM_BLUEPRINTS
    }
    cards = [_build_subsystem(blueprint, metrics) for blueprint in INTELLIGENCE_SUBSYSTEM_BLUEPRINTS]
    event_mesh = [
        "scam_detected -> Scam Shield, Alert Center, Threat Intelligence, Security Intelligence, Notifications",
        "high_risk_login -> Risk Assessment, Security Intelligence, Trust Intelligence, Account Security",
        "trending_topic -> Pulse Brain, Feed Intelligence, Smart Recommendations, Creator Studio, Pulse Heatmap",
        "copyright_or_report -> Trust Intelligence, Creator Reputation, Account Health, Admin Review",
        "marketplace_risk -> Risk Assessment, Scam Shield, Notifications, Admin Review",
    ]
    return {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "hub": hub,
        "metrics": metrics,
        "subsystems": subsystems,
        "cards": cards,
        "event_mesh": event_mesh,
        "privacy_boundary": "Private messages, raw tokens, secrets, exact geolocation, reporter identities, and hidden/private content are redacted.",
    }


def state_for_widget(intelligence_state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    blueprint = SUBSYSTEMS_BY_CARD.get(widget_key)
    if not blueprint:
        return None
    subsystem = (intelligence_state.get("subsystems") or {}).get(blueprint["key"].replace("-", "_"))
    if not subsystem:
        subsystem = _build_subsystem(blueprint, intelligence_state.get("metrics") or {})
    return {
        "state": subsystem.get("state") or "READY",
        "status": subsystem.get("state") or "READY",
        "status_label": subsystem.get("state") or "READY",
        "route": subsystem.get("route"),
        "cta_label": subsystem.get("action"),
        "detail": subsystem.get("detail"),
        "metric_value": subsystem.get("count"),
        "confidence": subsystem.get("confidence"),
    }


def build_admin_intelligence_state(conn: Any) -> dict[str, Any]:
    cur = conn.cursor()
    aggregate = _metric_map(cur, 0)
    aggregate["all_security_events"] = _count(cur, "security_events") + _count(cur, "command_center_security_events")
    aggregate["all_notifications"] = _count(cur, "notifications") + _count(cur, "notification_delivery_logs")
    aggregate["ai_events"] = _count(cur, "command_center_ai_events") + _count(cur, "ai_usage_logs")
    aggregate["reports"] = _count(cur, "reports") + _count(cur, "moderation_cases")
    aggregate["platform_health"] = max(50, 94 - min(40, aggregate["all_security_events"] // 10))
    sections = []
    for section in INTELLIGENCE_SECTIONS:
        key = section["key"]
        count = 0
        state = "READY"
        if key in {"scam-intelligence", "alert-management", "threat-intelligence", "risk-assessment"}:
            count = _safe_int(aggregate.get("all_security_events"), 0)
            state = "WARNING" if count else "READY"
        elif key in {"ai-advisor", "research-engine"}:
            count = _safe_int(aggregate.get("ai_events"), 0)
            state = "BETA"
        elif key in {"prediction-engine", "heatmap-engine", "trust-intelligence", "signal-intelligence"}:
            count = _safe_int(aggregate.get("reports"), 0)
            state = "PARTIAL"
        elif key == "audit":
            count = _safe_int(aggregate.get("all_security_events"), 0) + _safe_int(aggregate.get("ai_events"), 0)
        sections.append({**section, "count": count, "state": state, "confidence": _confidence_for_state(state, aggregate)})
    return {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metrics": aggregate,
        "sections": sections,
        "privacy_boundary": "Admin surfaces are aggregate-first. Sensitive evidence requires role-gated audited workflows.",
    }
