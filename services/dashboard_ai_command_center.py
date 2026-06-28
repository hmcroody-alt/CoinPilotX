"""Backend-managed PulseSoc AI command-center state.

This module powers the user-facing PulseSoc AI dashboard and the protected
admin AI Command Center. It intentionally returns aggregate state and safe
workflow metadata only. It never exposes prompts, private conversation bodies,
provider credentials, raw storage paths, API keys, tokens, or cross-user data.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from services import db as db_service


STRICT_STATES = {
    "READY",
    "ACTION REQUIRED",
    "REVIEW",
    "WARNING",
    "LOCKED",
    "PREMIUM",
    "ADMIN",
    "PARTIAL",
    "BETA",
    "COMING SOON",
}


AI_SECTIONS: tuple[dict[str, Any], ...] = (
    {"key": "undx-core", "label": "UNDX Core Intelligence", "route": "/admin/ai-command-center/undx-core", "description": "Mission workspace, project signals, agent coordination, memory safety, execution readiness, and system health."},
    {"key": "adaptive-companion", "label": "Adaptive AI Companion", "route": "/admin/ai-command-center/adaptive-companion", "description": "Assistant health, privacy boundaries, user guidance, context availability, and interaction quality."},
    {"key": "research-lab", "label": "Research Intelligence Lab", "route": "/admin/ai-command-center/research-lab", "description": "Research tasks, evidence readiness, confidence scoring, citation posture, and saved knowledge maps."},
    {"key": "creative-studio", "label": "Creative Intelligence Studio", "route": "/admin/ai-command-center/creative-studio", "description": "Post, script, campaign, caption, title, hashtag, presentation, course, and creator-writing workflows."},
    {"key": "visual-engine", "label": "Visual Intelligence Engine", "route": "/admin/ai-command-center/visual-engine", "description": "Image workflow readiness, prompt safety, media validation, visual assets, and creator-safe generation boundaries."},
    {"key": "music-studio", "label": "Music Intelligence Studio", "route": "/admin/ai-command-center/music-studio", "description": "Music workflow readiness, mood, metadata, safety, radio compatibility, and creator sound guidance."},
    {"key": "video-studio", "label": "Video Intelligence Studio", "route": "/admin/ai-command-center/video-studio", "description": "Video workflow readiness, script, cut, caption, thumbnail, reel, long-form, and publishing guidance."},
    {"key": "mission-control", "label": "AI Mission Control", "route": "/admin/ai-command-center/mission-control", "description": "Cross-platform signals, recommendations, automation queue, event propagation, and AI health."},
    {"key": "knowledge-graph", "label": "Knowledge Graph", "route": "/admin/ai-command-center/knowledge-graph", "description": "Privacy-safe relationships between projects, topics, posts, communities, marketplace, media, safety, and goals."},
    {"key": "agent-council", "label": "Agent Council", "route": "/admin/ai-command-center/agent-council", "description": "Specialist role readiness, agent boundaries, execution guardrails, and decision explainability."},
    {"key": "memory-engine", "label": "Memory Engine", "route": "/admin/ai-command-center/memory-engine", "description": "Session, project, research, creator, business, and private memory health with strict user controls."},
    {"key": "automation-queue", "label": "Automation Queue", "route": "/admin/ai-command-center/automation-queue", "description": "Research, planning, writing, publishing, optimization, moderation, security, and recovery tasks."},
    {"key": "scientific-engine", "label": "Scientific Engine", "route": "/admin/ai-command-center/scientific-engine", "description": "Simulation, modeling, prediction, hypothesis testing, and knowledge-discovery workflows."},
    {"key": "world-model", "label": "World Model", "route": "/admin/ai-command-center/world-model", "description": "PulseSoc-wide aggregate understanding across creator, network, economy, safety, media, and community signals."},
    {"key": "audit", "label": "AI Audit Logs", "route": "/admin/ai-command-center/audit", "description": "AI request, recommendation, automation, review, safety, memory, and admin action audit coverage."},
)


AI_SUBSYSTEM_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "key": "undx",
        "widget_key": "undx",
        "label": "UNDX Core Intelligence",
        "route": "/dashboard/ai/undx",
        "admin_route": "/admin/ai-command-center/undx-core",
        "action": "Enter UNDX",
        "metric": "mission_signals",
        "description": "Mission workspace for project memory, agent coordination, knowledge graph, execution readiness, and system health.",
        "intelligence": "Connects project, research, creator, market, safety, and automation signals into a safe mission view.",
        "automation": "Routes approved tasks into reviewable queues while keeping execution disabled unless configured and permitted.",
        "protection": "Private conversations, prompts, provider keys, and raw files stay hidden; execution remains gated by user permission and audit logs.",
        "prediction": "Scores readiness from project signals, safe memory availability, agent readiness, and queue health.",
        "recovery": "Mission history and safe summaries remain inspectable when an automation fails or is unavailable.",
        "recommendations": ("Open UNDX for mission work.", "Review privacy boundaries before enabling memory.", "Keep autonomous tasks review-gated."),
    },
    {
        "key": "assistant",
        "widget_key": "ai_assistant",
        "label": "Adaptive AI Companion",
        "route": "/dashboard/ai/assistant",
        "admin_route": "/admin/ai-command-center/adaptive-companion",
        "action": "Open Companion",
        "metric": "assistant_threads",
        "description": "Context-aware assistant entry for guidance, planning, navigation, writing, learning, and task support.",
        "intelligence": "Uses only permitted, owner-scoped context to guide users without exposing private platform data.",
        "automation": "Can prepare recommendations, drafts, plans, and navigation paths without silently taking sensitive action.",
        "protection": "Provider-disabled mode stays safe, and private messages, tokens, billing data, and secrets are never rendered.",
        "prediction": "Highlights likely next actions from dashboard, creator, network, safety, and project state.",
        "recovery": "Unavailable provider paths fall back to safe guidance instead of breaking the dashboard.",
        "recommendations": ("Ask the companion for a plan.", "Use it to find the right PulseSoc tool.", "Keep sensitive actions manual unless explicitly confirmed."),
    },
    {
        "key": "research",
        "widget_key": "ai_research",
        "label": "Research Intelligence Lab",
        "route": "/dashboard/ai/research",
        "admin_route": "/admin/ai-command-center/research-lab",
        "action": "Start Research",
        "metric": "research_items",
        "description": "Evidence-first research workspace with confidence scoring, saved maps, timeline, citation readiness, and debate mode preparation.",
        "intelligence": "Organizes research requests into source, evidence, confidence, topic, and risk summaries.",
        "automation": "Research tasks can update recommendations, creator plans, and project memory after user-approved review.",
        "protection": "Research output must keep citations and avoid leaking private workspace data.",
        "prediction": "Estimates confidence from available evidence, saved analyses, and review state.",
        "recovery": "Draft research remains recoverable when provider execution is disabled or interrupted.",
        "recommendations": ("Start with a specific question.", "Review evidence confidence.", "Save only research that should remain in memory."),
    },
    {
        "key": "creative-studio",
        "widget_key": "ai_content_generator",
        "label": "Creative Intelligence Studio",
        "route": "/dashboard/ai/creative-studio",
        "admin_route": "/admin/ai-command-center/creative-studio",
        "action": "Create Content",
        "metric": "creative_tasks",
        "description": "Creator-safe copy, posts, threads, scripts, articles, captions, titles, hashtags, campaigns, courses, and strategy drafts.",
        "intelligence": "Connects creator goals, content history, safety signals, and audience context into draft guidance.",
        "automation": "Drafts can flow to composer, creator studio, or review queues only with user intent.",
        "protection": "Generated content remains draft-only until the user publishes; unsafe or private context is redacted.",
        "prediction": "Scores creative readiness from available creator, feed, status, reel, and moderation signals.",
        "recovery": "Draft and failed-generation states are explainable and recoverable where stored.",
        "recommendations": ("Generate draft ideas before publishing.", "Run safety review on sensitive topics.", "Keep creator voice consistent."),
    },
    {
        "key": "visual-engine",
        "widget_key": "ai_image_tools",
        "label": "Visual Intelligence Engine",
        "route": "/dashboard/ai/visual-engine",
        "admin_route": "/admin/ai-command-center/visual-engine",
        "action": "Open Visual Engine",
        "metric": "visual_tasks",
        "state": "BETA",
        "description": "Image concept, asset review, prompt safety, thumbnail guidance, creator visuals, and media validation readiness.",
        "intelligence": "Evaluates visual requests for purpose, safety, placement, asset needs, and creator consistency.",
        "automation": "Prepared visuals can route into creator drafts or media review when provider support is enabled.",
        "protection": "No unsafe media or raw storage paths are exposed; provider calls remain optional.",
        "prediction": "Rates visual readiness from creative tasks, media assets, and safety checks.",
        "recovery": "Failed visual tasks retain safe error state and retry guidance.",
        "recommendations": ("Use creator-safe prompts.", "Preview before publishing.", "Keep generated assets in media review."),
    },
    {
        "key": "music-studio",
        "widget_key": "ai_music_tools",
        "label": "Music Intelligence Studio",
        "route": "/dashboard/ai/music-studio",
        "admin_route": "/admin/ai-command-center/music-studio",
        "action": "Open Music Studio",
        "metric": "music_tasks",
        "state": "BETA",
        "description": "Music guidance, metadata, mood, tempo, radio readiness, playlist fit, and creator sound recommendations.",
        "intelligence": "Connects Pulse Radio, approved music, creator sounds, and content needs into safe audio guidance.",
        "automation": "Music recommendations can update composer, status, reels, radio, and playlist surfaces.",
        "protection": "Copyright-sensitive actions remain review-gated; raw storage paths and unapproved tracks stay hidden.",
        "prediction": "Scores music fit from approved tracks, radio availability, and creator usage signals.",
        "recovery": "Missing audio provider paths fall back to approved PulseSoc music selection.",
        "recommendations": ("Use approved tracks.", "Match mood to content.", "Keep copyrighted audio review-gated."),
    },
    {
        "key": "video-studio",
        "widget_key": "ai_video_tools",
        "label": "Video Intelligence Studio",
        "route": "/dashboard/ai/video-studio",
        "admin_route": "/admin/ai-command-center/video-studio",
        "action": "Open Video Studio",
        "metric": "video_tasks",
        "state": "BETA",
        "description": "Video scripts, reel plans, captions, thumbnails, pacing, completion guidance, and publishing readiness.",
        "intelligence": "Combines creator, media, safety, music, and audience signals for video planning.",
        "automation": "Video plans can update draft studio, reel workflows, and media-processing queues where supported.",
        "protection": "Uploads, thumbnails, and generated metadata remain owner-scoped and moderation-aware.",
        "prediction": "Rates video readiness from media health, creator activity, approved audio, and retention signals.",
        "recovery": "Processing and draft failures remain diagnosable through media and creator surfaces.",
        "recommendations": ("Plan hook, caption, music, and thumbnail together.", "Keep previews muted by default.", "Review media health before publishing."),
    },
    {
        "key": "mission-control",
        "widget_key": "ai_intelligence_center",
        "label": "AI Mission Control",
        "route": "/dashboard/ai/mission-control",
        "admin_route": "/admin/ai-command-center/mission-control",
        "action": "Open AI Mission Control",
        "metric": "recommendations",
        "description": "Top-level AI intelligence hub for platform signals, safety, creator guidance, automation, memory, and recommendations.",
        "intelligence": "Summarizes safe account, network, creator, intelligence, economy, media, and ads signals into one brain-like view.",
        "automation": "Important events update recommendations, action queues, timelines, and admin diagnostics.",
        "protection": "Shows summaries only; private content, admin-only data, provider secrets, and internal tokens stay redacted.",
        "prediction": "Scores daily readiness from signal coverage, provider state, safety posture, and recommendation quality.",
        "recovery": "Provider downtime becomes partial state with clear local-safe fallbacks.",
        "recommendations": ("Review the daily brief.", "Resolve high-risk recommendations first.", "Keep provider-dependent actions optional."),
    },
)

SUBSYSTEMS_BY_KEY = {item["key"]: item for item in AI_SUBSYSTEM_BLUEPRINTS}
SUBSYSTEMS_BY_WIDGET = {item["widget_key"]: item for item in AI_SUBSYSTEM_BLUEPRINTS}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
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
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _column_exists(cur: Any, table: str, column: str) -> bool:
    if not _table_exists(cur, table):
        return False
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?",
                (table, column),
            )
            return bool(cur.fetchone())
        cur.execute(f"PRAGMA table_info({table})")
        return any(_row_value(row, "name", 1) == column for row in cur.fetchall())
    except Exception:
        return False


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _owner_where(cur: Any, table: str, owner_user_id: int) -> tuple[str, tuple[Any, ...]]:
    for column in ("user_id", "owner_user_id", "created_by", "account_user_id", "recipient_id"):
        if _column_exists(cur, table, column):
            return f"{column}=?", (owner_user_id,)
    return "1=0", ()


def _owner_count(cur: Any, table: str, owner_user_id: int, extra_where: str = "", extra_params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    owner_where, owner_params = _owner_where(cur, table, owner_user_id)
    where = owner_where
    params: tuple[Any, ...] = owner_params
    if extra_where:
        where = f"({where}) AND ({extra_where})"
        params = (*params, *extra_params)
    return _count(cur, table, where, params)


def _provider_enabled() -> bool:
    return os.getenv("PULSE_AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _confidence(*values: Any) -> int:
    positives = sum(1 for value in values if _safe_int(value, 0) > 0)
    return min(98, 56 + positives * 7)


def _metric_display(metric: str, value: Any) -> str:
    if metric.endswith("_score") or metric in {"privacy_score", "mission_health", "provider_readiness", "automation_readiness"}:
        return f"{_safe_int(value, 0)}%"
    return f"{_safe_int(value, 0):,}"


def _build_metrics(cur: Any, owner_user_id: int) -> dict[str, Any]:
    ai_conversations = _owner_count(cur, "ai_conversations", owner_user_id)
    ai_messages = _owner_count(cur, "ai_messages", owner_user_id)
    ai_analyses = _owner_count(cur, "ai_analyses", owner_user_id)
    ai_recommendations = _owner_count(cur, "ai_recommendations", owner_user_id)
    ai_action_requests = _owner_count(cur, "ai_action_requests", owner_user_id)
    command_center_events = _owner_count(cur, "command_center_ai_events", owner_user_id)
    pending_events = 0
    if _table_exists(cur, "command_center_ai_events") and _column_exists(cur, "command_center_ai_events", "status"):
        pending_events = _owner_count(cur, "command_center_ai_events", owner_user_id, "lower(COALESCE(status,'')) IN ('pending','queued','created')")
    research_items = ai_analyses + _owner_count(cur, "research_notes", owner_user_id) + _owner_count(cur, "saved_research", owner_user_id)
    creative_tasks = ai_action_requests + _owner_count(cur, "creator_drafts", owner_user_id) + _owner_count(cur, "post_drafts", owner_user_id)
    visual_tasks = _owner_count(cur, "media_assets", owner_user_id, "lower(COALESCE(mime_type,'')) LIKE 'image/%'") if _column_exists(cur, "media_assets", "mime_type") else _owner_count(cur, "media_assets", owner_user_id)
    music_tasks = _owner_count(cur, "pulse_music_tracks", owner_user_id) + _owner_count(cur, "music_uploads", owner_user_id)
    video_tasks = _owner_count(cur, "videos", owner_user_id) + _owner_count(cur, "reels", owner_user_id)
    assistant_threads = ai_conversations + command_center_events
    mission_signals = ai_conversations + ai_messages + ai_analyses + ai_recommendations + command_center_events
    provider_readiness = 92 if _provider_enabled() else 62
    privacy_score = 100
    memory_coverage = min(100, 35 + min(45, ai_conversations * 4) + min(20, ai_analyses * 5))
    automation_readiness = min(96, 54 + min(24, command_center_events * 4) + (8 if _provider_enabled() else 0))
    mission_health = max(58, min(98, (provider_readiness + privacy_score + automation_readiness + memory_coverage) // 4))
    active_alerts = 0
    if _table_exists(cur, "security_events"):
        active_alerts += _owner_count(cur, "security_events", owner_user_id, "lower(COALESCE(status,'')) NOT IN ('resolved','closed','safe')")
    if _table_exists(cur, "command_center_security_events"):
        active_alerts += _owner_count(cur, "command_center_security_events", owner_user_id, "lower(COALESCE(status,'')) NOT IN ('resolved','closed','safe')")
    return {
        "ai_conversations": ai_conversations,
        "ai_messages": ai_messages,
        "ai_analyses": ai_analyses,
        "ai_recommendations": ai_recommendations,
        "ai_action_requests": ai_action_requests,
        "command_center_events": command_center_events,
        "pending_events": pending_events,
        "research_items": research_items,
        "creative_tasks": creative_tasks,
        "visual_tasks": visual_tasks,
        "music_tasks": music_tasks,
        "video_tasks": video_tasks,
        "assistant_threads": assistant_threads,
        "mission_signals": mission_signals,
        "recommendations": ai_recommendations,
        "provider_enabled": 1 if _provider_enabled() else 0,
        "provider_readiness": provider_readiness,
        "privacy_score": privacy_score,
        "memory_coverage": memory_coverage,
        "automation_readiness": automation_readiness,
        "mission_health": mission_health,
        "active_alerts": active_alerts,
    }


def _state_for_blueprint(blueprint: dict[str, Any], metrics: dict[str, Any]) -> str:
    explicit = blueprint.get("state")
    if explicit:
        return str(explicit)
    if not metrics.get("provider_enabled") and blueprint["key"] in {"assistant", "research", "creative-studio", "mission-control"}:
        return "PARTIAL"
    if _safe_int(metrics.get("active_alerts"), 0) > 0 and blueprint["key"] in {"mission-control", "undx"}:
        return "WARNING"
    if _safe_int(metrics.get("pending_events"), 0) > 0 and blueprint["key"] in {"undx", "mission-control"}:
        return "REVIEW"
    return "READY"


def _detail_for_state(blueprint: dict[str, Any], state: str) -> str:
    if state == "PARTIAL":
        return "This system is wired with local-safe fallbacks. Provider execution is optional and currently disabled or unavailable."
    if state == "WARNING":
        return "AI and safety signals found account or platform risk that should be reviewed before automation is expanded."
    if state == "REVIEW":
        return "Queued AI events or automation tasks need review before execution."
    if state == "BETA":
        return "This system is functional with protected beta workflows and clear provider-disabled fallback."
    return str(blueprint.get("description") or "")


def build_ai_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    cur = conn.cursor()
    owner_user_id = _safe_int(user.get("user_id") or user.get("id"), 0)
    metrics = _build_metrics(cur, owner_user_id)
    cards: list[dict[str, Any]] = []
    subsystems: dict[str, dict[str, Any]] = {}
    for blueprint in AI_SUBSYSTEM_BLUEPRINTS:
        metric = str(blueprint.get("metric") or "mission_signals")
        value = metrics.get(metric, 0)
        state = _state_for_blueprint(blueprint, metrics)
        confidence = _confidence(metrics.get("mission_signals"), metrics.get("ai_conversations"), metrics.get("ai_analyses"), metrics.get("ai_recommendations"), metrics.get("command_center_events"))
        if state in {"PARTIAL", "BETA"}:
            confidence = max(50, confidence - 8)
        if state in {"WARNING", "REVIEW"}:
            confidence = max(52, confidence - 12)
        card = {
            "key": blueprint["key"],
            "widget_key": blueprint["widget_key"],
            "label": blueprint["label"],
            "route": blueprint["route"],
            "admin_route": blueprint["admin_route"],
            "action": blueprint["action"],
            "cta_label": blueprint["action"],
            "state": state,
            "count": _safe_int(value, 0),
            "count_display": _metric_display(metric, value),
            "detail": _detail_for_state(blueprint, state),
            "description": blueprint.get("description", ""),
            "intelligence": blueprint.get("intelligence", ""),
            "automation": blueprint.get("automation", ""),
            "protection": blueprint.get("protection", ""),
            "prediction": blueprint.get("prediction", ""),
            "recovery": blueprint.get("recovery", ""),
            "recommendations": list(blueprint.get("recommendations") or ()),
            "confidence": confidence,
        }
        cards.append(card)
        subsystems[blueprint["widget_key"]] = card
        subsystems[blueprint["key"].replace("-", "_")] = card
    hub_recommendations = [
        "Keep provider-dependent actions optional and review-gated." if not metrics["provider_enabled"] else "Review AI automations before expanding execution.",
        "Use UNDX for mission work that needs project memory and agent coordination.",
        "Store only memory that should remain available later.",
        "Treat AI recommendations as guidance until a user or authorized admin confirms sensitive actions.",
    ]
    if metrics["active_alerts"] > 0:
        hub_recommendations.insert(0, "Review active safety alerts before expanding AI automation.")
    if metrics["pending_events"] > 0:
        hub_recommendations.insert(0, "Review queued AI events before execution.")
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metrics": metrics,
        "hub": {
            "overall_intelligence_score": metrics["mission_health"],
            "provider_state": "Ready" if metrics["provider_enabled"] else "Local-safe mode",
            "privacy_score": metrics["privacy_score"],
            "memory_coverage": metrics["memory_coverage"],
            "automation_readiness": metrics["automation_readiness"],
            "mission_signals": metrics["mission_signals"],
            "assistant_threads": metrics["assistant_threads"],
            "research_items": metrics["research_items"],
            "creative_tasks": metrics["creative_tasks"],
            "active_alerts": metrics["active_alerts"],
            "pending_events": metrics["pending_events"],
            "daily_brief": "PulseSoc AI is connected to safe dashboard intelligence, UNDX, recommendations, research, creator, media, and automation surfaces without exposing private content.",
            "recommended_next_actions": hub_recommendations,
        },
        "cards": cards,
        "subsystems": subsystems,
        "knowledge_graph": {
            "people": "privacy-safe",
            "communities": "aggregate",
            "posts": "owner-scoped",
            "media": "owner-scoped",
            "marketplace": "aggregate",
            "safety": "redacted",
            "goals": "owner-controlled",
        },
        "agent_council": {
            "specialists": ("architect", "engineer", "researcher", "designer", "security analyst", "business advisor", "teacher", "writer"),
            "execution_policy": "review-gated",
            "sensitive_actions": "manual-confirmation",
        },
        "memory_engine": {
            "session_memory": "available",
            "project_memory": "owner-controlled",
            "private_memory": "redacted",
            "shared_memory": "permission-gated",
        },
        "automation_mesh": {
            "research_updates_recommendations": True,
            "creative_updates_creator_studio": True,
            "safety_updates_alerts": True,
            "media_updates_music_video_tools": True,
            "execution_review_required": True,
        },
        "privacy": {
            "raw_prompts_visible": False,
            "private_messages_visible": False,
            "provider_credentials_visible": False,
            "internal_tokens_visible": False,
            "cross_user_data_visible": False,
        },
    }


def state_for_widget(state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    if not state:
        return None
    return (state.get("subsystems") or {}).get(widget_key)


def _admin_metrics(cur: Any) -> dict[str, Any]:
    conversations = _count(cur, "ai_conversations")
    messages = _count(cur, "ai_messages")
    analyses = _count(cur, "ai_analyses")
    recommendations = _count(cur, "ai_recommendations")
    action_requests = _count(cur, "ai_action_requests")
    cc_events = _count(cur, "command_center_ai_events")
    pending = _count(cur, "command_center_ai_events", "lower(COALESCE(status,'')) IN ('pending','queued','created')") if _table_exists(cur, "command_center_ai_events") and _column_exists(cur, "command_center_ai_events", "status") else 0
    audit_logs = _count(cur, "account_audit_logs", "lower(COALESCE(action,'')) LIKE '%ai%'") + _count(cur, "admin_audit_logs", "lower(COALESCE(action,'')) LIKE '%ai%'")
    provider_readiness = 92 if _provider_enabled() else 62
    automation_readiness = min(96, 54 + min(28, cc_events * 3) + (8 if _provider_enabled() else 0))
    privacy_score = 100
    mission_health = max(58, min(98, (provider_readiness + automation_readiness + privacy_score) // 3))
    return {
        "conversations": conversations,
        "messages": messages,
        "analyses": analyses,
        "recommendations": recommendations,
        "action_requests": action_requests,
        "command_center_events": cc_events,
        "pending_events": pending,
        "audit_logs": audit_logs,
        "provider_readiness": provider_readiness,
        "automation_readiness": automation_readiness,
        "privacy_score": privacy_score,
        "mission_health": mission_health,
        "research_items": analyses,
        "creative_tasks": action_requests,
        "memory_signals": conversations,
        "knowledge_graph_signals": conversations + analyses + recommendations,
    }


def build_admin_ai_state(conn: Any) -> dict[str, Any]:
    cur = conn.cursor()
    metrics = _admin_metrics(cur)
    sections: list[dict[str, Any]] = []
    for section in AI_SECTIONS:
        key = section["key"]
        count = 0
        if key in {"undx-core", "mission-control", "world-model"}:
            count = metrics["command_center_events"] + metrics["recommendations"]
        elif key == "adaptive-companion":
            count = metrics["conversations"]
        elif key == "research-lab":
            count = metrics["research_items"]
        elif key == "creative-studio":
            count = metrics["creative_tasks"]
        elif key in {"visual-engine", "music-studio", "video-studio"}:
            count = metrics["action_requests"]
        elif key == "knowledge-graph":
            count = metrics["knowledge_graph_signals"]
        elif key == "agent-council":
            count = metrics["command_center_events"]
        elif key == "memory-engine":
            count = metrics["memory_signals"]
        elif key == "automation-queue":
            count = metrics["pending_events"]
        elif key == "audit":
            count = metrics["audit_logs"]
        state = "READY"
        if key in {"visual-engine", "music-studio", "video-studio", "scientific-engine"}:
            state = "BETA"
        if key == "automation-queue" and metrics["pending_events"] > 0:
            state = "REVIEW"
        if not _provider_enabled() and key in {"adaptive-companion", "research-lab", "creative-studio", "mission-control"}:
            state = "PARTIAL"
        sections.append(
            {
                **section,
                "state": state,
                "count": count,
                "confidence": _confidence(metrics["conversations"], metrics["analyses"], metrics["recommendations"], metrics["command_center_events"], metrics["audit_logs"]),
            }
        )
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metrics": metrics,
        "sections": sections,
        "privacy": {
            "raw_prompts_visible": False,
            "private_messages_visible": False,
            "provider_credentials_visible": False,
            "secrets_visible": False,
        },
    }
