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
        "key": "analytics",
        "label": "Creator Analytics",
        "route": "/admin/creator-command-center/analytics",
        "description": "Owner-safe performance metrics across posts, reels, videos, statuses, and live activity.",
    },
    {
        "key": "media-health",
        "label": "Media Processing Health",
        "route": "/admin/creator-command-center/media-health",
        "description": "Upload, thumbnail, transcoding, Mux/processing, playback, and media moderation health.",
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
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
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
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
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

    cards = [
        {
            "key": "my_posts",
            "label": "My Posts",
            "route": "/dashboard/creator/posts",
            "state": "REVIEW" if posts_review else "ON",
            "count": posts_total,
            "detail": f"{posts_review} in review · {posts_private} private · {posts_archived} archived",
            "action": "Manage Posts",
        },
        {
            "key": "reels",
            "label": "Reels",
            "route": "/dashboard/creator/reels",
            "state": "WARNING" if reels_processing else "REVIEW" if reels_review else "ON",
            "count": reels_total,
            "detail": f"{reels_processing} processing · {reels_review} in review",
            "action": "Manage Reels",
        },
        {
            "key": "videos",
            "label": "Videos",
            "route": "/dashboard/creator/videos",
            "state": "WARNING" if videos_processing else "REVIEW" if videos_review else "ON",
            "count": videos_total,
            "detail": f"{videos_processing} processing · {video_views} views",
            "action": "Manage Videos",
        },
        {
            "key": "statuses",
            "label": "Statuses",
            "route": "/dashboard/creator/statuses",
            "state": "REVIEW" if statuses_review else "ON",
            "count": statuses_total,
            "detail": f"{statuses_active} active · {status_views} views",
            "action": "Manage Statuses",
        },
        {
            "key": "live_studio",
            "label": "Live Studio",
            "route": "/dashboard/creator/live-studio",
            "state": "ON" if live_ready else "ACTION",
            "count": live_total,
            "detail": f"{live_active} live/scheduled · readiness checks available",
            "action": "Open Studio",
        },
    ]

    return {
        "user_id": user_id,
        "metrics": {
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
        },
        "cards": cards,
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

    section_counts = {
        "posts": posts_total,
        "reels": reels_total,
        "videos": videos_total,
        "statuses": statuses_total,
        "live-studio": live_total,
        "analytics": posts_total + reels_total + videos_total + statuses_total,
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
                "state": "WARNING" if warning_sections.get(key, 0) else "ON",
            }
        )

    return {
        "metrics": {
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
        },
        "sections": sections,
        "privacy": {
            "aggregate_only": True,
            "raw_media_urls_hidden": True,
            "private_drafts_hidden": True,
            "moderation_notes_hidden": True,
        },
    }
