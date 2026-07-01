"""Backend-managed PulseSoc Creator Command Center state.

This module returns owner-scoped creator data for the user dashboard and
aggregate-only creator diagnostics for admin command surfaces. It avoids raw
storage URLs, private moderation notes, and other sensitive fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services import db as db_service


CREATOR_SECTIONS = (
    {
        "key": "posts",
        "label": "Posts Manager",
        "route": "/admin/creator-command-center/posts",
        "description": "Published posts, edits, archives, privacy state, reports, comments, reactions, and moderation state.",
    },
    {
        "key": "reels",
        "label": "Reels Manager",
        "route": "/admin/creator-command-center/reels",
        "description": "Short-form reels, playback health, audio rules, moderation status, and performance diagnostics.",
    },
    {
        "key": "videos",
        "label": "Videos Manager",
        "route": "/admin/creator-command-center/videos",
        "description": "Long-form videos, thumbnails, processing state, transcoding health, moderation status, and playback metrics.",
    },
    {
        "key": "statuses",
        "label": "Statuses Manager",
        "route": "/admin/creator-command-center/statuses",
        "description": "Story/status creation, viewer analytics, completion metrics, expiration rules, and report status.",
    },
    {
        "key": "live-studio",
        "label": "Live Studio Manager",
        "route": "/admin/creator-command-center/live-studio",
        "description": "Live readiness, schedule/go-live state, stream health, reports, viewer count, and replay readiness.",
    },
    {
        "key": "audience-intelligence",
        "label": "Audience Intelligence",
        "route": "/admin/creator-command-center/audience-intelligence",
        "description": "Audience growth, retention, regions where privacy-safe, follower conversion, and audience recommendations.",
    },
    {
        "key": "content-performance",
        "label": "Content Performance",
        "route": "/admin/creator-command-center/content-performance",
        "description": "Performance across posts, reels, videos, statuses, saves, comments, shares, and watch signals.",
    },
    {
        "key": "best-posting-time",
        "label": "Timing Intelligence",
        "route": "/admin/creator-command-center/best-posting-time",
        "description": "Best posting windows, schedule readiness, audience timing, and safe publish recommendations.",
    },
    {
        "key": "creator-score",
        "label": "Creator Score",
        "route": "/admin/creator-command-center/creator-score",
        "description": "Creator readiness, trust, consistency, media health, moderation health, and audience quality.",
    },
    {
        "key": "creator-tools",
        "label": "Creator Tools",
        "route": "/admin/creator-command-center/creator-tools",
        "description": "Publishing workspace, media tools, caption assistance hooks, and creator workflow controls.",
    },
    {
        "key": "trend-intelligence",
        "label": "Trend Intelligence",
        "route": "/admin/creator-command-center/trend-intelligence",
        "description": "Trend alignment, music match, hashtag opportunity, and creator-safe trend recommendations.",
    },
    {
        "key": "content-planner",
        "label": "Planner",
        "route": "/admin/creator-command-center/content-planner",
        "description": "Content plans, cross-format sequencing, planned campaigns, and recommendation-driven creator calendar.",
    },
    {
        "key": "post-scheduler",
        "label": "Scheduler",
        "route": "/admin/creator-command-center/post-scheduler",
        "description": "Scheduled content state, timing conflicts, queue readiness, and publish automation hooks.",
    },
    {
        "key": "draft-studio",
        "label": "Draft Manager",
        "route": "/admin/creator-command-center/draft-studio",
        "description": "Draft inventory, recovery, privacy protection, moderation readiness, and safe delete/archive state.",
    },
    {
        "key": "ai-creator-assistant",
        "label": "Creator AI",
        "route": "/admin/creator-command-center/ai-creator-assistant",
        "description": "Caption, script, SEO, thumbnail, audience, monetization, copyright, and safety assistance controls.",
    },
    {
        "key": "engagement-prediction",
        "label": "Engagement Prediction",
        "route": "/admin/creator-command-center/engagement-prediction",
        "description": "Predicted reach, likes, comments, shares, saves, watch time, retention, and viral chance.",
    },
    {
        "key": "creator-reputation",
        "label": "Creator Reputation",
        "route": "/admin/creator-command-center/creator-reputation",
        "description": "Creator trust, copyright status, warnings, reports, eligibility, verification, and appeal history.",
    },
    {
        "key": "viral-opportunity-scanner",
        "label": "Viral Opportunity Scanner",
        "route": "/admin/creator-command-center/viral-opportunity-scanner",
        "description": "Viral windows, trend alignment, audience readiness, music match, hashtag opportunity, and publish guidance.",
    },
    {
        "key": "media-health",
        "label": "Media Processing Health",
        "route": "/admin/creator-command-center/media-health",
        "description": "Upload, thumbnail, processing, playback, and media review health.",
    },
    {
        "key": "moderation",
        "label": "Content Moderation",
        "route": "/admin/creator-command-center/moderation",
        "description": "Reports, review queues, removals, appeals, unsafe media, and policy state.",
    },
    {
        "key": "audit",
        "label": "Creator Audit Logs",
        "route": "/admin/creator-command-center/audit",
        "description": "Edit, delete, archive, privacy, moderation, media, and live administrative audit visibility.",
    },
)


STRICT_STATES = {"READY", "ACTION", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "COMING SOON", "ADMIN"}


CREATOR_SUBSYSTEM_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "key": "posts",
        "card_key": "my_posts",
        "label": "My Posts",
        "route": "/dashboard/creator/posts",
        "admin_route": "/admin/creator-command-center/posts",
        "action": "Manage Posts",
        "metric": "posts_total",
        "premium": False,
        "description": "Published, private, archived, reported, and moderated posts with edit/delete/archive controls.",
        "intelligence": "Reads post volume, privacy state, moderation state, and recent content to show what needs attention.",
        "automation": "Updates creator hub, moderation queue, content performance, and reputation when post state changes.",
        "analytics": "Tracks owner-safe comments, reactions, share readiness, views where available, and moderation status.",
        "protection": "Owner-scoped actions, sanitized text, server-side privacy controls, and moderation gating.",
        "recovery": "Archives stay recoverable where data exists; unsafe changes remain traceable for support review.",
        "ai_guidance": "Suggests stronger captions, safer wording, hashtag cleanup, and publish readiness when AI is enabled.",
        "recommendations": ("Review private or archived posts.", "Resolve moderation review items first.", "Use strong captions before publishing."),
    },
    {
        "key": "reels",
        "label": "Reels",
        "route": "/dashboard/creator/reels",
        "admin_route": "/admin/creator-command-center/reels",
        "action": "Manage Reels",
        "metric": "reels_total",
        "description": "Short-form video library, audio state, processing health, moderation state, and completion signals.",
        "intelligence": "Combines processing, review, completion, and creator-safe music signals.",
        "automation": "Refreshes media health, creator hub, and performance surfaces after uploads or failures.",
        "analytics": "Uses completion rate, review state, and processing state without exposing private viewer identities.",
        "protection": "Media validation, moderation status, copyright-aware audio handling, and owner-only management.",
        "recovery": "Processing failures surface as warnings with retry/replace workflow entry points.",
        "ai_guidance": "Can recommend captions, music fit, safe hashtags, and retention improvements when enabled.",
        "recommendations": ("Fix processing warnings before sharing.", "Use approved music only.", "Review completion trends for future reels."),
    },
    {
        "key": "videos",
        "label": "Videos",
        "route": "/dashboard/creator/videos",
        "admin_route": "/admin/creator-command-center/videos",
        "action": "Manage Videos",
        "metric": "videos_total",
        "description": "Long-form videos, thumbnail state, processing/transcoding health, privacy, and moderation state.",
        "intelligence": "Surfaces video processing, playback, moderation, and aggregate view readiness.",
        "automation": "Video processing events update media health, creator hub, and content performance.",
        "analytics": "Tracks owner-safe video views, processing state, and moderation state.",
        "protection": "Raw storage URLs stay hidden; upload and thumbnail handling remain server-managed.",
        "recovery": "Replace/remove flows keep failed videos inspectable without leaking storage paths.",
        "ai_guidance": "Can advise on thumbnails, titles, chapters, and retention when enabled.",
        "recommendations": ("Resolve processing items.", "Keep thumbnails clear and safe.", "Check moderation state before promotion."),
    },
    {
        "key": "statuses",
        "label": "Statuses",
        "route": "/dashboard/creator/statuses",
        "admin_route": "/admin/creator-command-center/statuses",
        "action": "Manage Stories",
        "metric": "statuses_total",
        "description": "Status stories, viewer analytics, reactions, completion, privacy, expiration, and report state.",
        "intelligence": "Reads active status count, views, completion, and moderation signals.",
        "automation": "Status expiration, reactions, and reports update status activity and creator hub.",
        "analytics": "Shows aggregate status views and completion without leaking private viewer data.",
        "protection": "Privacy rules, report handling, and media moderation are enforced server-side.",
        "recovery": "Expired or deleted story behavior remains traceable and recoverable where policy allows.",
        "ai_guidance": "Can recommend tighter story captions and better timing when enabled.",
        "recommendations": ("Check completion rate.", "Keep story overlays readable.", "Use privacy intentionally."),
    },
    {
        "key": "live-studio",
        "card_key": "live_studio",
        "label": "Live Studio",
        "route": "/dashboard/creator/live-studio",
        "admin_route": "/admin/creator-command-center/live-studio",
        "action": "Manage Live Broadcasts",
        "metric": "live_total",
        "description": "Live readiness, schedule/go-live entry, stream readiness, safety reviews, and replay readiness.",
        "intelligence": "Checks live records, active/scheduled state, reports, and readiness signals.",
        "automation": "Live starts update audience, notification, creator hub, and moderation surfaces.",
        "analytics": "Tracks safe aggregate live status and safety reviews without exposing restricted stream data.",
        "protection": "Stream keys are never exposed; safety reviews stay protected.",
        "recovery": "Readiness checks guide failed live setup without blocking other creator tools.",
        "ai_guidance": "Can help plan titles, show structure, and safety notes when enabled.",
        "recommendations": ("Run readiness checks before going live.", "Resolve open live reports.", "Schedule live sessions ahead of peak audience time."),
    },
    {
        "key": "audience-intelligence",
        "label": "Audience Intelligence",
        "route": "/dashboard/creator/audience-intelligence",
        "admin_route": "/admin/creator-command-center/audience-intelligence",
        "action": "Understand Audience",
        "metric": "audience_score",
        "premium": True,
        "description": "Audience growth, retention, viewer quality, follower conversion, and privacy-safe audience signals.",
    },
    {
        "key": "content-performance",
        "label": "Content Performance",
        "route": "/dashboard/creator/content-performance",
        "admin_route": "/admin/creator-command-center/content-performance",
        "action": "Optimize Content",
        "metric": "content_score",
        "premium": True,
        "description": "Cross-format performance, comments, saves, shares, completion, and content improvement guidance.",
    },
    {
        "key": "best-posting-time",
        "label": "Best Posting Time",
        "route": "/dashboard/creator/best-posting-time",
        "admin_route": "/admin/creator-command-center/best-posting-time",
        "action": "Optimize Timing",
        "metric": "timing_score",
        "premium": True,
        "description": "Timing recommendations, audience availability, schedule conflicts, and next-best publish window.",
    },
    {
        "key": "creator-score",
        "label": "Creator Score",
        "route": "/dashboard/creator/creator-score",
        "admin_route": "/admin/creator-command-center/creator-score",
        "action": "View Creator Score",
        "metric": "creator_score",
        "description": "Creator readiness, consistency, media health, trust state, and recommended next actions.",
    },
    {
        "key": "creator-tools",
        "label": "Creator Tools",
        "route": "/dashboard/creator/creator-tools",
        "admin_route": "/admin/creator-command-center/creator-tools",
        "action": "Open Creator Workspace",
        "metric": "tool_count",
        "description": "Publishing, media, audio, safety, and workflow tools routed into one creator workspace.",
    },
    {
        "key": "trend-intelligence",
        "label": "Trend Intelligence",
        "route": "/dashboard/creator/trend-intelligence",
        "admin_route": "/admin/creator-command-center/trend-intelligence",
        "action": "Explore Trends",
        "metric": "trend_signals",
        "premium": True,
        "state_hint": "BETA",
        "description": "Trend alignment, creator-safe music fit, hashtags, audience readiness, and opportunity signals.",
    },
    {
        "key": "content-planner",
        "label": "Content Planner",
        "route": "/dashboard/creator/content-planner",
        "admin_route": "/admin/creator-command-center/content-planner",
        "action": "Plan Content",
        "metric": "planned_content",
        "premium": True,
        "state_hint": "PARTIAL",
        "description": "Planning console for posts, reels, videos, statuses, campaigns, drafts, and recommendations.",
    },
    {
        "key": "post-scheduler",
        "label": "Post Scheduler",
        "route": "/dashboard/creator/post-scheduler",
        "admin_route": "/admin/creator-command-center/post-scheduler",
        "action": "Schedule Posts",
        "metric": "scheduled_content",
        "premium": True,
        "state_hint": "PARTIAL",
        "description": "Schedule queue, timing conflicts, future publish hooks, and moderation-aware scheduling state.",
    },
    {
        "key": "draft-studio",
        "label": "Draft Studio",
        "route": "/dashboard/creator/draft-studio",
        "admin_route": "/admin/creator-command-center/draft-studio",
        "action": "Manage Drafts",
        "metric": "drafts",
        "premium": True,
        "state_hint": "PARTIAL",
        "description": "Draft inventory, recovery, safe deletion, privacy protection, and review readiness.",
    },
    {
        "key": "ai-creator-assistant",
        "label": "AI Creator Assistant",
        "route": "/dashboard/creator/ai-creator-assistant",
        "admin_route": "/admin/creator-command-center/ai-creator-assistant",
        "action": "Ask Creator AI",
        "metric": "ai_recommendations",
        "premium": True,
        "state_hint": "BETA",
        "description": "Caption writer, script writer, SEO advisor, thumbnail advisor, audience coach, copyright advisor, and safety advisor hooks.",
    },
    {
        "key": "engagement-prediction",
        "label": "Engagement Prediction",
        "route": "/dashboard/creator/engagement-prediction",
        "admin_route": "/admin/creator-command-center/engagement-prediction",
        "action": "Predict Engagement",
        "metric": "engagement_prediction",
        "premium": True,
        "state_hint": "PARTIAL",
        "description": "Predicted reach, likes, shares, saves, comments, watch time, retention, viral chance, and safe estimates.",
    },
    {
        "key": "creator-reputation",
        "label": "Creator Reputation",
        "route": "/dashboard/creator/creator-reputation",
        "admin_route": "/admin/creator-command-center/creator-reputation",
        "action": "Review Reputation",
        "metric": "reputation_score",
        "premium": True,
        "state_hint": "BETA",
        "description": "Creator trust, copyright status, warnings, reports, community guideline state, monetization eligibility, and appeals.",
    },
    {
        "key": "viral-opportunity-scanner",
        "label": "Viral Opportunity Scanner",
        "route": "/dashboard/creator/viral-opportunity-scanner",
        "admin_route": "/admin/creator-command-center/viral-opportunity-scanner",
        "action": "Scan Opportunities",
        "metric": "viral_opportunities",
        "premium": True,
        "state_hint": "PARTIAL",
        "description": "Viral windows, trend alignment, audience readiness, music match, hashtag opportunity, and publish recommendation.",
    },
)


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
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
                (table,),
            )
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _columns(cur: Any, table: str) -> set[str]:
    if not _table_exists(cur, table):
        return set()
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                (table,),
            )
            return {str(_row_value(row, "column_name", 0) or "") for row in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {str(_row_value(row, "name", 1) or "") for row in cur.fetchall()}
    except Exception:
        return set()


def _not_deleted_clause(cols: set[str]) -> list[str]:
    clauses: list[str] = []
    if "deleted_at" in cols:
        clauses.append("COALESCE(deleted_at,'')=''")
    if "status" in cols:
        clauses.append("COALESCE(status,'active')!='deleted'")
    return clauses


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _avg(cur: Any, table: str, column: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> float:
    cols = _columns(cur, table)
    if column not in cols:
        return 0.0
    try:
        cur.execute(f"SELECT AVG({column}) AS average_value FROM {table} WHERE {where}", params)
        return _safe_float(_row_value(cur.fetchone(), "average_value", 0), 0.0)
    except Exception:
        return 0.0


def _owner_column(cur: Any, table: str, candidates: tuple[str, ...]) -> str | None:
    cols = _columns(cur, table)
    for column in candidates:
        if column in cols:
            return column
    return None


def _owner_where(cur: Any, table: str, owner_id: int, candidates: tuple[str, ...]) -> tuple[str, tuple[Any, ...], set[str]]:
    cols = _columns(cur, table)
    owner_col = next((column for column in candidates if column in cols), "")
    if not owner_col:
        return "1=0", (), cols
    clauses = [f"{owner_col}=?"]
    clauses.extend(_not_deleted_clause(cols))
    return " AND ".join(clauses), (owner_id,), cols


def _owner_count(cur: Any, table: str, owner_id: int, candidates: tuple[str, ...], extra: str = "", extra_params: tuple[Any, ...] = ()) -> int:
    where, params, _cols = _owner_where(cur, table, owner_id, candidates)
    if extra:
        where = f"{where} AND {extra}"
    return _count(cur, table, where, params + extra_params)


def _moderation_count(cur: Any, table: str, owner_id: int, candidates: tuple[str, ...]) -> int:
    cols = _columns(cur, table)
    if "moderation_status" not in cols:
        return 0
    return _owner_count(
        cur,
        table,
        owner_id,
        candidates,
        "LOWER(COALESCE(moderation_status,'')) IN ('pending','review','needs_review','flagged','rejected')",
    )


def _processing_count(cur: Any, table: str, owner_id: int, candidates: tuple[str, ...]) -> int:
    cols = _columns(cur, table)
    states: list[str] = []
    for column in ("processing_status", "transcoding_status", "mux_status"):
        if column in cols:
            states.append(
                f"LOWER(COALESCE({column},'')) NOT IN ('','ready','complete','completed','processed','available','active')"
            )
    if not states:
        return 0
    return _owner_count(cur, table, owner_id, candidates, "(" + " OR ".join(states) + ")")


def _recent_content(cur: Any, table: str, owner_id: int, candidates: tuple[str, ...], limit: int = 6) -> list[dict[str, Any]]:
    cols = _columns(cur, table)
    owner_col = next((column for column in candidates if column in cols), "")
    if not owner_col:
        return []
    select_columns = [column for column in ("id", "title", "caption", "body", "status", "moderation_status", "visibility", "processing_status", "created_at", "updated_at") if column in cols]
    if not select_columns:
        return []
    clauses = [f"{owner_col}=?"]
    clauses.extend(_not_deleted_clause(cols))
    order_col = "updated_at" if "updated_at" in cols else "created_at" if "created_at" in cols else "id"
    try:
        cur.execute(
            f"SELECT {', '.join(select_columns)} FROM {table} WHERE {' AND '.join(clauses)} ORDER BY {order_col} DESC LIMIT ?",
            (owner_id, max(1, min(int(limit), 20))),
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            title = item.get("title") or item.get("caption") or item.get("body") or f"{table} #{item.get('id')}"
            rows.append(
                {
                    "id": _safe_int(item.get("id"), 0),
                    "title": str(title or "")[:120],
                    "status": str(item.get("status") or item.get("processing_status") or "active")[:40],
                    "moderation_status": str(item.get("moderation_status") or "clear")[:40],
                    "visibility": str(item.get("visibility") or "default")[:40],
                    "created_at": str(item.get("created_at") or item.get("updated_at") or "")[:40],
                }
            )
        return rows
    except Exception:
        return []


def _status_views(cur: Any, owner_id: int) -> int:
    if not (_table_exists(cur, "pulse_status_views") and _table_exists(cur, "pulse_statuses")):
        return 0
    try:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM pulse_status_views v
            WHERE EXISTS (
              SELECT 1 FROM pulse_statuses s
              WHERE s.id=v.status_id AND s.user_id=? AND COALESCE(s.deleted_at,'')=''
            )
            """,
            (owner_id,),
        )
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _status_completion(cur: Any, owner_id: int) -> float:
    if not (_table_exists(cur, "pulse_status_views") and _table_exists(cur, "pulse_statuses")):
        return 0.0
    if "completion_ratio" not in _columns(cur, "pulse_status_views"):
        return 0.0
    try:
        cur.execute(
            """
            SELECT AVG(v.completion_ratio) AS completion_rate
            FROM pulse_status_views v
            WHERE EXISTS (
              SELECT 1 FROM pulse_statuses s
              WHERE s.id=v.status_id AND s.user_id=? AND COALESCE(s.deleted_at,'')=''
            )
            """,
            (owner_id,),
        )
        return _safe_float(_row_value(cur.fetchone(), "completion_rate", 0), 0.0)
    except Exception:
        return 0.0


def _owner_live_report_count(cur: Any, owner_id: int) -> int:
    """Count live reports tied to the current creator without exposing platform-wide report volume."""
    if not _table_exists(cur, "pulse_live_reports"):
        return 0
    report_cols = _columns(cur, "pulse_live_reports")
    report_owner_col = next((column for column in ("user_id", "owner_user_id", "creator_user_id", "host_user_id") if column in report_cols), "")
    open_clause = "COALESCE(status,'open') IN ('open','pending','review')" if "status" in report_cols else "1=1"
    if report_owner_col:
        return _count(cur, "pulse_live_reports", f"{report_owner_col}=? AND {open_clause}", (owner_id,))
    if "stream_id" not in report_cols:
        return 0
    for stream_table in ("pulse_live_streams", "pulse_live_sessions", "pulse_live_events"):
        if not _table_exists(cur, stream_table):
            continue
        stream_cols = _columns(cur, stream_table)
        stream_owner_col = next((column for column in ("user_id", "owner_user_id", "creator_user_id", "host_user_id") if column in stream_cols), "")
        if not stream_owner_col or "id" not in stream_cols:
            continue
        try:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM pulse_live_reports r
                WHERE {open_clause}
                  AND EXISTS (
                    SELECT 1 FROM {stream_table} s
                    WHERE s.id=r.stream_id AND s.{stream_owner_col}=?
                  )
                """,
                (owner_id,),
            )
            return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
        except Exception:
            continue
    return 0


def _safe_state(value: Any, default: str = "READY") -> str:
    state = str(value or default).strip().upper().replace("_", " ")
    return state if state in STRICT_STATES else default


def _creator_subsystem_payload(
    blueprint: dict[str, Any],
    metrics: dict[str, Any],
    *,
    state: str | None = None,
    count: int | None = None,
) -> dict[str, Any]:
    key = str(blueprint.get("key") or "")
    base_state = state or blueprint.get("state_hint") or ("PREMIUM" if blueprint.get("premium") else "READY")
    metric_key = str(blueprint.get("metric") or "")
    metric_value = _safe_int(metrics.get(metric_key), 0)
    if count is None:
        count = metric_value
    description = str(blueprint.get("description") or "Creator subsystem.")
    recommendations = list(blueprint.get("recommendations") or ())
    if not recommendations:
        recommendations = [
            "Review the current state before publishing.",
            "Use owner-safe analytics to decide the next action.",
            "Resolve warnings before promoting content.",
        ]
    return {
        "key": key,
        "card_key": str(blueprint.get("card_key") or key.replace("-", "_")),
        "label": str(blueprint.get("label") or key.replace("-", " ").title()),
        "route": str(blueprint.get("route") or f"/dashboard/creator/{key}"),
        "action": str(blueprint.get("action") or "Review Creator"),
        "state": _safe_state(base_state),
        "count": _safe_int(count, 0),
        "detail": str(blueprint.get("detail") or description),
        "description": description,
        "intelligence": str(blueprint.get("intelligence") or "Reads owner-scoped creator signals and explains what needs attention."),
        "command": str(blueprint.get("command") or "Routes the creator to the correct workflow."),
        "automation": str(blueprint.get("automation") or "Synchronizes related creator surfaces when content, media, moderation, or live state changes."),
        "analytics": str(blueprint.get("analytics") or "Uses aggregate, owner-safe metrics without exposing private viewer or draft data."),
        "protection": str(blueprint.get("protection") or "Applies owner scoping, server-side permissions, media validation, and safety boundaries."),
        "recovery": str(blueprint.get("recovery") or "Keeps failed, draft, processing, and review states visible enough to recover safely."),
        "ai_guidance": str(blueprint.get("ai_guidance") or "Returns safe guidance when AI is enabled and stays quiet when disabled."),
        "recommendations": recommendations,
    }


def _creator_event_bus(metrics: dict[str, Any]) -> list[dict[str, str]]:
    events = [
        ("content_uploaded", "New post/reel/video/status signals refresh creator hub, media health, and content performance."),
        ("media_processing_changed", "Processing warnings update media health and creator hub."),
        ("moderation_state_changed", "Review or warning states update reputation, content performance, and creator hub."),
        ("live_state_changed", "Live readiness and active streams update audience, delivery, and creator hub."),
        ("schedule_changed", "Planner, scheduler, drafts, and timing intelligence stay synchronized."),
    ]
    if _safe_int(metrics.get("moderation_reviews"), 0):
        events.append(("creator_attention_required", "Moderation queue has active items and should be reviewed before promotion."))
    if _safe_int(metrics.get("media_processing"), 0):
        events.append(("media_processing_warning", "Processing queue has active work; publishing surfaces should show a warning."))
    return [{"event": name, "effect": effect} for name, effect in events]


def build_creator_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Return sanitized owner-scoped dashboard state for one creator account."""
    cur = conn.cursor()
    user_id = _safe_int(user.get("user_id"), 0)
    now = datetime.utcnow().isoformat(timespec="seconds")

    posts_total = _owner_count(cur, "pulse_posts", user_id, ("user_id", "owner_user_id"))
    posts_review = _moderation_count(cur, "pulse_posts", user_id, ("user_id", "owner_user_id"))
    posts_archived = _owner_count(cur, "pulse_posts", user_id, ("user_id", "owner_user_id"), "LOWER(COALESCE(status,'')) IN ('archived','hidden')")
    posts_private = _owner_count(cur, "pulse_posts", user_id, ("user_id", "owner_user_id"), "LOWER(COALESCE(visibility,'public'))!='public")

    reels_total = _owner_count(cur, "pulse_reels", user_id, ("user_id", "owner_user_id"))
    reels_review = _moderation_count(cur, "pulse_reels", user_id, ("user_id", "owner_user_id"))
    reels_processing = _processing_count(cur, "pulse_reels", user_id, ("user_id", "owner_user_id"))
    reels_completion = _avg(cur, "pulse_reels", "completion_rate", *_owner_where(cur, "pulse_reels", user_id, ("user_id", "owner_user_id"))[:2])

    videos_total = _owner_count(cur, "pulse_videos", user_id, ("owner_user_id", "user_id"))
    videos_review = _moderation_count(cur, "pulse_videos", user_id, ("owner_user_id", "user_id"))
    videos_processing = _processing_count(cur, "pulse_videos", user_id, ("owner_user_id", "user_id"))
    video_views = 0
    if "view_count" in _columns(cur, "pulse_videos"):
        where, params, _cols = _owner_where(cur, "pulse_videos", user_id, ("owner_user_id", "user_id"))
        try:
            cur.execute(f"SELECT COALESCE(SUM(view_count),0) AS total FROM pulse_videos WHERE {where}", params)
            video_views = _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
        except Exception:
            video_views = 0

    statuses_total = _owner_count(cur, "pulse_statuses", user_id, ("user_id", "owner_user_id"))
    statuses_total += _owner_count(cur, "pulse_status", user_id, ("user_id", "owner_user_id"))
    statuses_active = _owner_count(
        cur,
        "pulse_statuses",
        user_id,
        ("user_id", "owner_user_id"),
        "(COALESCE(expires_at,'')='' OR expires_at>?)",
        (now,),
    )
    statuses_review = _moderation_count(cur, "pulse_statuses", user_id, ("user_id", "owner_user_id"))
    status_views = _status_views(cur, user_id)
    status_completion = _status_completion(cur, user_id)

    live_total = 0
    live_active = 0
    live_tables = ("pulse_live_streams", "pulse_live_sessions", "pulse_live_events")
    for table in live_tables:
        live_total += _owner_count(cur, table, user_id, ("user_id", "owner_user_id", "creator_user_id", "host_user_id"))
        cols = _columns(cur, table)
        if "status" in cols:
            live_active += _owner_count(
                cur,
                table,
                user_id,
                ("user_id", "owner_user_id", "creator_user_id", "host_user_id"),
                "LOWER(COALESCE(status,'')) IN ('live','active','streaming','scheduled')",
            )
    live_reports = _owner_live_report_count(cur, user_id)

    media_processing = reels_processing + videos_processing
    moderation_reviews = posts_review + reels_review + videos_review + statuses_review
    creator_health = max(45, 100 - min(45, moderation_reviews * 7) - min(25, media_processing * 5))
    media_health = max(40, 100 - min(60, media_processing * 12))
    live_ready = True
    total_content = posts_total + reels_total + videos_total + statuses_total
    audience_score = max(45, min(100, 58 + min(24, total_content * 2) + min(18, status_views // 10)))
    content_score = max(45, min(100, 62 + min(24, video_views // 8) + min(14, posts_total + reels_total)))
    timing_score = 72 if total_content else 48
    creator_score = max(35, round((creator_health + media_health + audience_score + content_score + timing_score) / 5))
    reputation_score = max(40, 100 - min(45, moderation_reviews * 8) - min(20, live_reports * 6))
    metrics = {
        "posts_total": posts_total,
        "posts_in_review": posts_review,
        "reels_total": reels_total,
        "reels_processing": reels_processing,
        "reels_completion_rate": round(reels_completion, 3),
        "videos_total": videos_total,
        "videos_processing": videos_processing,
        "video_views": video_views,
        "statuses_total": statuses_total,
        "statuses_active": statuses_active,
        "status_views": status_views,
        "status_completion_rate": round(status_completion, 3),
        "live_total": live_total,
        "live_active": live_active,
        "live_reports": live_reports,
        "moderation_reviews": moderation_reviews,
        "media_processing": media_processing,
        "creator_health_score": creator_health,
        "media_health_score": media_health,
        "audience_score": audience_score,
        "content_score": content_score,
        "timing_score": timing_score,
        "creator_score": creator_score,
        "tool_count": 7,
        "trend_signals": 3 if total_content else 0,
        "planned_content": 0,
        "scheduled_content": 0,
        "drafts": _owner_count(cur, "pulse_drafts", user_id, ("user_id", "owner_user_id")),
        "ai_recommendations": 0,
        "engagement_prediction": content_score,
        "reputation_score": reputation_score,
        "viral_opportunities": 1 if total_content and moderation_reviews == 0 else 0,
        "upload_queue": media_processing,
        "encoding_jobs": media_processing,
        "content_queue": media_processing + moderation_reviews,
        "today_reach": status_views + video_views,
        "audience_growth": max(0, min(100, status_views // 5 + posts_total)),
    }

    state_overrides = {
        "posts": "REVIEW" if posts_review else "READY",
        "reels": "WARNING" if reels_processing else "REVIEW" if reels_review else "READY",
        "videos": "WARNING" if videos_processing else "REVIEW" if videos_review else "READY",
        "statuses": "REVIEW" if statuses_review else "READY",
        "live-studio": "READY" if live_ready else "ACTION",
        "creator-score": "WARNING" if creator_score < 55 else "READY",
        "creator-reputation": "WARNING" if moderation_reviews or live_reports else "BETA",
        "viral-opportunity-scanner": "ACTION" if metrics["viral_opportunities"] else "PARTIAL",
    }
    detail_overrides = {
        "posts": f"{posts_review} in review · {posts_private} private · {posts_archived} archived",
        "reels": f"{reels_processing} processing · {reels_review} in review",
        "videos": f"{videos_processing} processing · {video_views} views",
        "statuses": f"{statuses_active} active · {status_views} views",
        "live-studio": f"{live_active} live/scheduled · readiness checks available",
        "creator-score": f"{creator_score}% creator readiness",
        "creator-reputation": f"{reputation_score}% reputation · {moderation_reviews} review signals",
    }
    subsystems: dict[str, dict[str, Any]] = {}
    cards: list[dict[str, Any]] = []
    for blueprint in CREATOR_SUBSYSTEM_BLUEPRINTS:
        key = str(blueprint.get("key") or "")
        payload = _creator_subsystem_payload(
            blueprint,
            metrics,
            state=state_overrides.get(key),
        )
        if key in detail_overrides:
            payload["detail"] = detail_overrides[key]
        subsystems[key.replace("-", "_")] = payload
        cards.append(
            {
                "key": payload["card_key"],
                "subsystem_key": key,
                "label": payload["label"],
                "route": payload["route"],
                "state": payload["state"],
                "count": payload["count"],
                "detail": payload["detail"],
                "action": payload["action"],
            }
        )

    recommendations = [
        "Resolve moderation reviews before promoting content." if moderation_reviews else "Keep publishing consistently across posts, reels, videos, and stories.",
        "Wait for processing jobs before boosting media." if media_processing else "Media pipeline is ready for the next upload.",
        "Use timing and trend tools before your next major post." if total_content else "Create your first post, reel, video, or story to activate deeper creator intelligence.",
    ]
    intelligence = {
        "creator_health": creator_health,
        "creator_score": creator_score,
        "today_reach": metrics["today_reach"],
        "content_queue": metrics["content_queue"],
        "drafts": metrics["drafts"],
        "scheduled_content": metrics["scheduled_content"],
        "upload_queue": metrics["upload_queue"],
        "encoding_jobs": metrics["encoding_jobs"],
        "live_stream_status": "READY" if live_ready else "ACTION",
        "audience_growth": metrics["audience_growth"],
        "viral_opportunities": metrics["viral_opportunities"],
        "best_time_to_post": "Learning" if total_content else "Needs content",
        "ai_recommendations": metrics["ai_recommendations"],
        "copyright_alerts": moderation_reviews,
        "monetization_status": "Core access",
        "trust_score": reputation_score,
        "reputation_score": reputation_score,
        "community_guideline_status": "Review needed" if moderation_reviews else "Clear",
        "recommended_next_actions": recommendations,
    }

    return {
        "user_id": user_id,
        "metrics": metrics,
        "intelligence": intelligence,
        "cards": cards,
        "subsystems": subsystems,
        "event_bus": _creator_event_bus(metrics),
        "posts": {
            "total": posts_total,
            "in_review": posts_review,
            "archived": posts_archived,
            "private": posts_private,
            "recent": _recent_content(cur, "pulse_posts", user_id, ("user_id", "owner_user_id")),
        },
        "reels": {
            "total": reels_total,
            "in_review": reels_review,
            "processing": reels_processing,
            "completion_rate": round(reels_completion, 3),
            "recent": _recent_content(cur, "pulse_reels", user_id, ("user_id", "owner_user_id")),
        },
        "videos": {
            "total": videos_total,
            "in_review": videos_review,
            "processing": videos_processing,
            "views": video_views,
            "recent": _recent_content(cur, "pulse_videos", user_id, ("owner_user_id", "user_id")),
        },
        "statuses": {
            "total": statuses_total,
            "active": statuses_active,
            "in_review": statuses_review,
            "views": status_views,
            "completion_rate": round(status_completion, 3),
            "recent": _recent_content(cur, "pulse_statuses", user_id, ("user_id", "owner_user_id")),
        },
        "live": {
            "total": live_total,
            "active": live_active,
            "reports_open": live_reports,
            "ready": live_ready,
        },
        "privacy": {
            "owner_scoped": True,
            "raw_media_urls_hidden": True,
            "moderation_notes_hidden": True,
            "viewer_identity_protected": True,
        },
    }


def build_admin_creator_state(conn: Any) -> dict[str, Any]:
    """Return aggregate admin diagnostics for creator operations."""
    cur = conn.cursor()
    posts_total = _count(cur, "pulse_posts")
    posts_review = _count(cur, "pulse_posts", "LOWER(COALESCE(moderation_status,'')) IN ('pending','review','needs_review','flagged','rejected')")
    posts_archived = _count(cur, "pulse_posts", "LOWER(COALESCE(status,'')) IN ('archived','hidden')")
    reels_total = _count(cur, "pulse_reels")
    reels_review = _count(cur, "pulse_reels", "LOWER(COALESCE(moderation_status,'')) IN ('pending','review','needs_review','flagged','rejected')")
    reels_processing = _processing_count(cur, "pulse_reels", 0, ("__missing__",))
    if reels_processing == 0 and _table_exists(cur, "pulse_reels"):
        cols = _columns(cur, "pulse_reels")
        states = [f"LOWER(COALESCE({column},'')) NOT IN ('','ready','complete','completed','processed','available','active')" for column in ("processing_status", "transcoding_status") if column in cols]
        reels_processing = _count(cur, "pulse_reels", "(" + " OR ".join(states) + ")") if states else 0
    videos_total = _count(cur, "pulse_videos")
    videos_review = _count(cur, "pulse_videos", "LOWER(COALESCE(moderation_status,'')) IN ('pending','review','needs_review','flagged','rejected')")
    video_cols = _columns(cur, "pulse_videos")
    video_states = [f"LOWER(COALESCE({column},'')) NOT IN ('','ready','complete','completed','processed','available','active')" for column in ("processing_status", "mux_status") if column in video_cols]
    videos_processing = _count(cur, "pulse_videos", "(" + " OR ".join(video_states) + ")") if video_states else 0
    statuses_total = _count(cur, "pulse_statuses") + _count(cur, "pulse_status")
    statuses_active = _count(cur, "pulse_statuses", "COALESCE(deleted_at,'')='' AND (COALESCE(expires_at,'')='' OR expires_at>?)", (datetime.utcnow().isoformat(timespec="seconds"),))
    statuses_review = _count(cur, "pulse_statuses", "LOWER(COALESCE(moderation_status,'')) IN ('pending','review','needs_review','flagged','rejected')")
    live_total = sum(_count(cur, table) for table in ("pulse_live_streams", "pulse_live_sessions", "pulse_live_events"))
    live_active = 0
    for table in ("pulse_live_streams", "pulse_live_sessions", "pulse_live_events"):
        if "status" in _columns(cur, table):
            live_active += _count(cur, table, "LOWER(COALESCE(status,'')) IN ('live','active','streaming','scheduled')")
    live_reports = _count(cur, "pulse_live_reports", "COALESCE(status,'open') IN ('open','pending','review')")
    moderation_queue = posts_review + reels_review + videos_review + statuses_review
    media_processing = reels_processing + videos_processing
    creator_health = max(40, 100 - min(50, moderation_queue * 3) - min(30, media_processing * 4))
    media_health = max(35, 100 - min(65, media_processing * 8))
    live_health = 100 if live_reports == 0 else max(45, 100 - min(55, live_reports * 8))
    total_content = posts_total + reels_total + videos_total + statuses_total
    audience_score = max(45, min(100, 58 + min(24, total_content) + min(18, statuses_active * 2)))
    content_score = max(45, min(100, 62 + min(28, total_content) - min(20, moderation_queue * 2)))
    timing_score = 72 if total_content else 48
    creator_score = max(35, round((creator_health + media_health + live_health + audience_score + content_score + timing_score) / 6))
    reputation_score = max(40, 100 - min(50, moderation_queue * 3) - min(20, live_reports * 5))
    metrics = {
        "posts_total": posts_total,
        "posts_in_review": posts_review,
        "posts_archived": posts_archived,
        "reels_total": reels_total,
        "reels_processing": reels_processing,
        "videos_total": videos_total,
        "videos_processing": videos_processing,
        "statuses_total": statuses_total,
        "statuses_active": statuses_active,
        "live_total": live_total,
        "live_active": live_active,
        "live_reports": live_reports,
        "media_processing": media_processing,
        "moderation_queue": moderation_queue,
        "creator_health_score": creator_health,
        "media_health_score": media_health,
        "live_health_score": live_health,
        "audience_score": audience_score,
        "content_score": content_score,
        "timing_score": timing_score,
        "creator_score": creator_score,
        "tool_count": 7,
        "trend_signals": 3 if total_content else 0,
        "planned_content": 0,
        "scheduled_content": 0,
        "drafts": _count(cur, "pulse_drafts"),
        "ai_recommendations": 0,
        "engagement_prediction": content_score,
        "reputation_score": reputation_score,
        "viral_opportunities": 1 if total_content and moderation_queue == 0 else 0,
        "upload_queue": media_processing,
        "encoding_jobs": media_processing,
        "content_queue": media_processing + moderation_queue,
        "today_reach": statuses_active,
        "audience_growth": max(0, min(100, total_content)),
    }

    section_counts = {
        "posts": posts_total,
        "reels": reels_total,
        "videos": videos_total,
        "statuses": statuses_total,
        "live-studio": live_total,
        "audience-intelligence": audience_score,
        "content-performance": content_score,
        "best-posting-time": timing_score,
        "creator-score": creator_score,
        "creator-tools": metrics["tool_count"],
        "trend-intelligence": metrics["trend_signals"],
        "content-planner": metrics["planned_content"],
        "post-scheduler": metrics["scheduled_content"],
        "draft-studio": metrics["drafts"],
        "ai-creator-assistant": metrics["ai_recommendations"],
        "engagement-prediction": metrics["engagement_prediction"],
        "creator-reputation": reputation_score,
        "viral-opportunity-scanner": metrics["viral_opportunities"],
        "media-health": media_processing,
        "moderation": moderation_queue,
        "audit": _count(cur, "admin_audit_logs") + _count(cur, "account_audit_logs"),
    }
    warning_sections = {"media-health": media_processing, "moderation": moderation_queue, "live-studio": live_reports}
    sections = []
    for section in CREATOR_SECTIONS:
        key = section["key"]
        sections.append(
            {
                **section,
                "count": section_counts.get(key, 0),
                "state": "WARNING" if warning_sections.get(key, 0) else "PARTIAL" if key in {"content-planner", "post-scheduler", "draft-studio", "engagement-prediction", "viral-opportunity-scanner"} else "BETA" if key in {"trend-intelligence", "ai-creator-assistant", "creator-reputation"} else "READY",
            }
        )

    return {
        "metrics": metrics,
        "sections": sections,
        "event_bus": _creator_event_bus(metrics),
        "privacy": {
            "aggregate_only": True,
            "raw_media_urls_hidden": True,
            "private_drafts_hidden": True,
            "moderation_notes_hidden": True,
        },
    }
