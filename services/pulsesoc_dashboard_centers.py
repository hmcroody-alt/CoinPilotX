"""PulseSoc dashboard centers with backend-owned state.

These helpers back the Verification Center, AI Advisor, Seller Tools,
Subscriptions, and Premium Center. They intentionally prefer explicit
unavailable states over fabricated analytics, billing, sales, or AI output.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from services import db as db_service
from services import premium_identity_engine


VERIFICATION_TRACKS = (
    "identity",
    "creator",
    "business",
    "seller",
    "advertiser",
    "music_partner",
    "media_partner",
    "organization",
)
VERIFICATION_STATUSES = {
    "not_started",
    "draft",
    "submitted",
    "in_review",
    "needs_more_info",
    "approved",
    "rejected",
    "appealed",
    "revoked",
    "suspended",
}
BADGE_TYPES = (
    "blue_check",
    "identity_verified",
    "creator_verified",
    "business_verified",
    "seller_verified",
    "advertiser_verified",
    "music_partner_verified",
    "media_partner_verified",
    "organization_verified",
    "trusted_account",
    "official_account",
    "founder_badge",
    "premium_badge",
)
ADMIN_ROLES = {
    "owner",
    "super_admin",
    "admin",
    "verification_reviewer",
    "business_reviewer",
    "seller_reviewer",
    "ads_reviewer",
    "music_reviewer",
}
READONLY_ADMIN_ROLES = {"support_readonly", "support_agent"}
TRACK_ROLE_MAP = {
    "identity": {"owner", "super_admin", "admin", "verification_reviewer"},
    "creator": {"owner", "super_admin", "admin", "verification_reviewer"},
    "business": {"owner", "super_admin", "admin", "business_reviewer"},
    "seller": {"owner", "super_admin", "admin", "seller_reviewer"},
    "advertiser": {"owner", "super_admin", "admin", "ads_reviewer"},
    "music_partner": {"owner", "super_admin", "admin", "music_reviewer"},
    "media_partner": {"owner", "super_admin", "admin", "music_reviewer"},
    "organization": {"owner", "super_admin", "admin", "business_reviewer", "verification_reviewer"},
}


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _columns(cur: Any, table: str) -> set[str]:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
            return {str(_row_dict(row).get("column_name") or row[0]) for row in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {str(_row_dict(row).get("name") or row[1]) for row in cur.fetchall()}
    except Exception:
        return set()


def _add_column(cur: Any, table: str, name: str, definition: str) -> None:
    if name in _columns(cur, table):
        return
    if db_service.IS_POSTGRES:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition}")
    else:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        row = cur.fetchone()
        return _safe_int(_row_dict(row).get("total") if row else row[0], 0)
    except Exception:
        return 0


def _first(cur: Any, table: str, where: str, params: tuple[Any, ...] = (), order: str = "id DESC") -> dict[str, Any]:
    if not _table_exists(cur, table):
        return {}
    try:
        cur.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order} LIMIT 1", params)
        return _row_dict(cur.fetchone())
    except Exception:
        return {}


def _json(value: Any) -> str:
    try:
        return json.dumps(value or {}, sort_keys=True)[:8000]
    except Exception:
        return "{}"


def ensure_tables(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            track TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            progress_percent INTEGER DEFAULT 0,
            risk_score INTEGER DEFAULT 0,
            submitted_at TEXT,
            reviewed_at TEXT,
            reviewer_id INTEGER,
            rejection_reason TEXT,
            needs_more_info_reason TEXT,
            appeal_status TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            user_id INTEGER NOT NULL,
            document_type TEXT,
            storage_asset_id TEXT,
            status TEXT DEFAULT 'submitted',
            reviewed_by INTEGER,
            reviewed_at TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            badge_type TEXT NOT NULL,
            source_track TEXT,
            status TEXT NOT NULL DEFAULT 'approved',
            approved_by INTEGER,
            approved_at TEXT,
            revoked_by INTEGER,
            revoked_at TEXT,
            revoke_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            target_user_id INTEGER,
            request_id INTEGER,
            action TEXT,
            before_json TEXT,
            after_json TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            user_id INTEGER NOT NULL,
            appeal_text TEXT,
            status TEXT DEFAULT 'submitted',
            reviewed_by INTEGER,
            reviewed_at TEXT,
            decision_reason TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_user_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            goal_type TEXT,
            title TEXT,
            status TEXT DEFAULT 'active',
            progress_percent INTEGER DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_seller_stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            store_name TEXT,
            description TEXT,
            contact_email TEXT,
            policies TEXT,
            shipping_policy TEXT,
            return_policy TEXT,
            visibility TEXT DEFAULT 'draft',
            vacation_mode INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_seller_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_type TEXT,
            name TEXT,
            description TEXT,
            category TEXT,
            price_cents INTEGER DEFAULT 0,
            inventory INTEGER DEFAULT 0,
            visibility TEXT DEFAULT 'draft',
            return_eligible INTEGER DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_dashboard_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pref_key TEXT NOT NULL,
            pref_value TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_premium_exploration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            feature_key TEXT NOT NULL,
            status TEXT DEFAULT 'reviewed',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_content_planner_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content_type TEXT,
            stage TEXT DEFAULT 'ideas',
            title TEXT,
            caption TEXT,
            hashtags TEXT,
            audience TEXT,
            scheduled_at TEXT,
            media_attached INTEGER DEFAULT 0,
            thumbnail_selected INTEGER DEFAULT 0,
            alt_text TEXT,
            links_validated INTEGER DEFAULT 0,
            final_preview_reviewed INTEGER DEFAULT 0,
            status TEXT DEFAULT 'draft',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulsesoc_content_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT,
            goal TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT DEFAULT 'Planning',
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    for table in (
        "verification_requests",
        "verification_badges",
        "pulsesoc_user_goals",
        "pulsesoc_seller_stores",
        "pulsesoc_seller_products",
        "pulsesoc_dashboard_preferences",
        "pulsesoc_premium_exploration",
        "pulsesoc_content_planner_items",
        "pulsesoc_content_campaigns",
    ):
        try:
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)")
        except Exception:
            pass
    for name, definition in {
        "user_id": "INTEGER",
        "track": "TEXT",
        "status": "TEXT DEFAULT 'draft'",
        "progress_percent": "INTEGER DEFAULT 0",
        "risk_score": "INTEGER DEFAULT 0",
        "submitted_at": "TEXT",
        "reviewed_at": "TEXT",
        "reviewer_id": "INTEGER",
        "rejection_reason": "TEXT",
        "needs_more_info_reason": "TEXT",
        "appeal_status": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }.items():
        _add_column(cur, "verification_requests", name, definition)
    for name, definition in {
        "request_id": "INTEGER",
        "user_id": "INTEGER",
        "document_type": "TEXT",
        "storage_asset_id": "TEXT",
        "storage_path": "TEXT",
        "original_filename": "TEXT",
        "mime_type": "TEXT",
        "file_size": "INTEGER DEFAULT 0",
        "checksum": "TEXT",
        "status": "TEXT DEFAULT 'submitted'",
        "review_status": "TEXT DEFAULT 'pending'",
        "accessed_by": "INTEGER",
        "accessed_at": "TEXT",
        "reviewed_by": "INTEGER",
        "reviewed_at": "TEXT",
        "created_at": "TEXT",
    }.items():
        _add_column(cur, "verification_documents", name, definition)
    for name, definition in {
        "user_id": "INTEGER",
        "badge_type": "TEXT",
        "source_track": "TEXT",
        "status": "TEXT DEFAULT 'approved'",
        "approved_by": "INTEGER",
        "approved_at": "TEXT",
        "revoked_by": "INTEGER",
        "revoked_at": "TEXT",
        "revoke_reason": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }.items():
        _add_column(cur, "verification_badges", name, definition)
    conn.commit()


def audit(conn: Any, actor_user_id: int, target_user_id: int, request_id: int, action: str, before: Any = None, after: Any = None, ip_hash: str = "", user_agent_hash: str = "") -> None:
    ensure_tables(conn)
    conn.cursor().execute(
        """
        INSERT INTO verification_audit_logs
        (actor_user_id, target_user_id, request_id, action, before_json, after_json, ip_hash, user_agent_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (actor_user_id, target_user_id, request_id, action, _json(before), _json(after), ip_hash[:160], user_agent_hash[:240], _now()),
    )
    conn.commit()


def active_badges(conn: Any, user_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM verification_badges
        WHERE user_id=? AND status='approved' AND revoked_at IS NULL
        ORDER BY approved_at DESC, id DESC
        """,
        (int(user_id),),
    )
    badges = [_row_dict(row) for row in cur.fetchall()]
    result = []
    for badge in badges:
        badge_type = str(badge.get("badge_type") or "")
        result.append(
            {
                **badge,
                "label": badge_type.replace("_", " ").title(),
                "icon": "OK" if badge_type != "blue_check" else "BLUE",
                "color": "cyan" if badge_type != "founder_badge" else "gold",
                "tooltip": f"{badge_type.replace('_', ' ').title()} approved by PulseSoc review.",
                "backend_source": "verification_badges",
            }
        )
    return result


def badge_renderer_html(conn: Any, user_id: int, context: str = "profile") -> str:
    badges = active_badges(conn, user_id)
    if not badges:
        return ""
    spans = []
    for badge in badges[:4]:
        label = str(badge.get("label") or "Verified")
        spans.append(
            f"<span class='verification-badge verification-badge-{badge.get('color')}' title='{label}' aria-label='{label} badge in {context}' data-backend-source='verification_badges'>{badge.get('icon')} {label}</span>"
        )
    return "".join(spans)


def _latest_track_request(cur: Any, user_id: int, track: str) -> dict[str, Any]:
    return _first(cur, "verification_requests", "user_id=? AND track=?", (int(user_id), track))


def _track_steps(user: dict[str, Any], track: str, request_row: dict[str, Any], doc_count: int) -> list[dict[str, Any]]:
    email_ok = bool(user.get("email_verified") or user.get("confirmed_email") or user.get("email_confirmed_at"))
    profile_ok = bool(user.get("username") and (user.get("display_name") or user.get("full_name")))
    submitted = str(request_row.get("status") or "not_started") not in {"", "not_started", "draft"}
    reviewed = str(request_row.get("status") or "") in {"approved", "rejected", "needs_more_info", "revoked", "suspended"}
    common = [
        ("email", "Email verified", email_ok, "/dashboard/account/settings"),
        ("profile", "Profile identity completed", profile_ok, "/dashboard/account/profile"),
        ("submission", "Verification request submitted", submitted, "/dashboard/account/verification"),
        ("review", "Admin review completed", reviewed, "/dashboard/account/verification"),
    ]
    if track in {"identity", "business", "organization", "music_partner", "media_partner"}:
        common.insert(2, ("document", "Private evidence uploaded", doc_count > 0, "/dashboard/account/verification"))
    if track == "seller":
        common.insert(1, ("store", "Store setup started", False, "/dashboard/economy/seller-tools"))
    return [{"key": key, "label": label, "complete": bool(done), "route": route} for key, label, done, route in common]


def build_verification_center(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or user.get("id") or 0)
    tracks = []
    approved = 0
    pending = 0
    locked = []
    for track in VERIFICATION_TRACKS:
        row = _latest_track_request(cur, user_id, track)
        status = str(row.get("status") or "not_started")
        if status == "approved":
            approved += 1
        if status in {"submitted", "in_review", "needs_more_info", "appealed"}:
            pending += 1
        doc_count = _count(cur, "verification_documents", "user_id=? AND request_id=?", (user_id, _safe_int(row.get("id"), 0))) if row else 0
        steps = _track_steps(user, track, row, doc_count)
        progress = int(sum(1 for step in steps if step["complete"]) * 100 / max(1, len(steps)))
        if row and _safe_int(row.get("progress_percent"), 0) > progress:
            progress = _safe_int(row.get("progress_percent"), progress)
        if track == "seller" and not _track_approved(cur, user_id, "business"):
            locked.append("Seller Tools")
        tracks.append(
            {
                "track": track,
                "label": track.replace("_", " ").title(),
                "status": status,
                "progress_percent": progress,
                "request_id": _safe_int(row.get("id"), 0),
                "risk_score": _safe_int(row.get("risk_score"), 0),
                "submitted_documents": doc_count,
                "review_notes": row.get("needs_more_info_reason") or row.get("rejection_reason") or "",
                "next_action": _verification_button(status),
                "badge_result": _badge_for_track(track),
                "steps": steps,
                "unlocks": _verification_unlocks(track),
                "locked_reason": "Business verification required." if track == "seller" and not _track_approved(cur, user_id, "business") else "",
            }
        )
    badges = active_badges(conn, user_id)
    score = int(sum(track["progress_percent"] for track in tracks) / max(1, len(tracks)))
    return {
        "kind": "verification",
        "title": "Verification Center",
        "summary": "Backend-managed trust passport for identity, creator, business, seller, advertiser, media, music, and organization review.",
        "readiness": score,
        "risk_level": "Review" if pending else "Low",
        "pending_reviews": pending,
        "approved_tracks": approved,
        "locked_features": locked,
        "unlocked_features": _unlocked_features(cur, user_id),
        "next_best_action": _next_verification_action(tracks),
        "tracks": tracks,
        "badges": badges,
        "audit_count": _count(cur, "verification_audit_logs", "target_user_id=?", (user_id,)),
    }


def _track_approved(cur: Any, user_id: int, track: str) -> bool:
    row = _latest_track_request(cur, user_id, track)
    return str(row.get("status") or "") == "approved"


def _verification_button(status: str) -> str:
    return {
        "not_started": "Start Verification",
        "draft": "Continue Verification",
        "submitted": "View Submission",
        "in_review": "View Review Status",
        "needs_more_info": "Submit More Info",
        "approved": "View Badge",
        "rejected": "Submit Appeal",
        "appealed": "View Appeal",
        "revoked": "Request Review",
        "suspended": "Contact Support",
    }.get(status, "Start Verification")


def _badge_for_track(track: str) -> str:
    return {
        "identity": "identity_verified",
        "creator": "creator_verified",
        "business": "business_verified",
        "seller": "seller_verified",
        "advertiser": "advertiser_verified",
        "music_partner": "music_partner_verified",
        "media_partner": "media_partner_verified",
        "organization": "organization_verified",
    }.get(track, "trusted_account")


def _verification_unlocks(track: str) -> list[str]:
    return {
        "identity": ["Stronger account trust", "Eligibility for advanced verification"],
        "creator": ["Creator verified badge", "Creator monetization eligibility", "Brand deal eligibility"],
        "business": ["Business verified badge", "Advertiser tools", "Seller tools", "Business profile"],
        "seller": ["Marketplace seller tools", "Payout readiness", "Store analytics", "Product listings"],
        "advertiser": ["Ads Manager", "Campaign Builder", "Ad Wallet", "Ad Analytics"],
        "music_partner": ["Upload music", "Pulse Radio artist tools", "Music Library management"],
        "media_partner": ["Media partner badge", "Distribution readiness tools"],
        "organization": ["Organization badge", "Official account eligibility", "Team controls"],
    }.get(track, [])


def _unlocked_features(cur: Any, user_id: int) -> list[str]:
    unlocked = []
    for track in VERIFICATION_TRACKS:
        if _track_approved(cur, user_id, track):
            unlocked.extend(_verification_unlocks(track))
    return sorted(set(unlocked))


def _next_verification_action(tracks: list[dict[str, Any]]) -> str:
    for status in ("needs_more_info", "rejected", "draft", "not_started", "submitted", "in_review"):
        match = next((track for track in tracks if track["status"] == status), None)
        if match:
            if status == "needs_more_info":
                return f"Submit more information for {match['label']}."
            if status == "rejected":
                return f"Review the decision and submit an appeal for {match['label']} if appropriate."
            if status in {"draft", "not_started"}:
                return f"Complete {match['label']} to unlock connected PulseSoc capabilities."
            return f"Monitor {match['label']} review status."
    return "All available verification tracks are either approved or do not need action."


def create_verification_request(conn: Any, user: dict[str, Any], track: str, ip_hash: str = "", user_agent_hash: str = "") -> dict[str, Any]:
    ensure_tables(conn)
    track = str(track or "").strip().lower()
    if track == "music":
        track = "music_partner"
    if track not in VERIFICATION_TRACKS:
        return {"ok": False, "message": "Unsupported verification track."}
    user_id = int(user.get("user_id") or 0)
    cur = conn.cursor()
    existing = _latest_track_request(cur, user_id, track)
    if existing and str(existing.get("status") or "") in {"submitted", "in_review", "needs_more_info", "approved", "appealed"}:
        return {"ok": True, "message": "Existing verification request is already active.", "request": existing}
    now = _now()
    cur.execute(
        """
        INSERT INTO verification_requests
        (user_id, track, status, progress_percent, risk_score, submitted_at, created_at, updated_at)
        VALUES (?, ?, 'submitted', ?, 0, ?, ?, ?)
        """,
        (user_id, track, 35, now, now, now),
    )
    request_id = getattr(cur, "lastrowid", 0) or 0
    conn.commit()
    row = _latest_track_request(cur, user_id, track)
    audit(conn, user_id, user_id, _safe_int(row.get("id"), request_id), "verification_submitted", existing, row, ip_hash, user_agent_hash)
    return {"ok": True, "message": f"{track.replace('_', ' ').title()} submitted for review.", "request": row}


def submit_appeal(conn: Any, user: dict[str, Any], request_id: int, appeal_text: str, ip_hash: str = "", user_agent_hash: str = "") -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    request_id = int(request_id or 0)
    appeal_text = str(appeal_text or "").strip()[:3000]
    if not request_id or len(appeal_text) < 8:
        return {"ok": False, "message": "Add an appeal note before submitting."}
    cur = conn.cursor()
    cur.execute("SELECT * FROM verification_requests WHERE id=? AND user_id=? LIMIT 1", (request_id, user_id))
    before = _row_dict(cur.fetchone())
    if not before:
        return {"ok": False, "message": "Verification request not found."}
    cur.execute(
        "INSERT INTO verification_appeals (request_id, user_id, appeal_text, status, created_at) VALUES (?, ?, ?, 'submitted', ?)",
        (request_id, user_id, appeal_text, _now()),
    )
    cur.execute("UPDATE verification_requests SET status='appealed', appeal_status='submitted', updated_at=? WHERE id=? AND user_id=?", (_now(), request_id, user_id))
    conn.commit()
    after = _first(cur, "verification_requests", "id=? AND user_id=?", (request_id, user_id))
    audit(conn, user_id, user_id, request_id, "verification_appeal_submitted", before, after, ip_hash, user_agent_hash)
    return {"ok": True, "message": "Appeal submitted for review."}


def record_document(conn: Any, user: dict[str, Any], request_id: int, document_type: str, storage_asset_id: str, ip_hash: str = "", user_agent_hash: str = "") -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    document_type = str(document_type or "supporting_document").strip()[:80]
    storage_asset_id = str(storage_asset_id or "").strip()[:500]
    if not storage_asset_id:
        return {"ok": False, "message": "Secure storage asset id is required."}
    cur = conn.cursor()
    req = _first(cur, "verification_requests", "id=? AND user_id=?", (int(request_id or 0), user_id))
    if not req:
        return {"ok": False, "message": "Start a verification request before uploading evidence."}
    cur.execute(
        "INSERT INTO verification_documents (request_id, user_id, document_type, storage_asset_id, status, created_at) VALUES (?, ?, ?, ?, 'submitted', ?)",
        (int(req.get("id")), user_id, document_type, storage_asset_id, _now()),
    )
    cur.execute("UPDATE verification_requests SET progress_percent=MAX(progress_percent, 55), updated_at=? WHERE id=? AND user_id=?", (_now(), int(req.get("id")), user_id))
    conn.commit()
    audit(conn, user_id, user_id, int(req.get("id")), "verification_document_uploaded", {}, {"document_type": document_type, "storage_asset_id": "redacted"}, ip_hash, user_agent_hash)
    return {"ok": True, "message": "Document recorded for private reviewer access."}


def record_private_document(
    conn: Any,
    user: dict[str, Any],
    request_id: int,
    document_type: str,
    *,
    storage_path: str,
    original_filename: str,
    mime_type: str,
    file_size: int,
    checksum: str,
    ip_hash: str = "",
    user_agent_hash: str = "",
) -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    cur = conn.cursor()
    req = _first(cur, "verification_requests", "id=? AND user_id=?", (int(request_id or 0), user_id))
    if not req:
        return {"ok": False, "message": "Start a verification request before uploading evidence."}
    now = _now()
    cur.execute(
        """
        INSERT INTO verification_documents
        (request_id, user_id, document_type, storage_asset_id, storage_path, original_filename, mime_type,
         file_size, checksum, status, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', 'pending', ?)
        """,
        (
            int(req.get("id")),
            user_id,
            str(document_type or "")[:80],
            f"private-verification-document:{checksum[:16]}",
            str(storage_path or "")[:1000],
            str(original_filename or "")[:180],
            str(mime_type or "")[:120],
            int(file_size or 0),
            str(checksum or "")[:128],
            now,
        ),
    )
    cur.execute("UPDATE verification_requests SET progress_percent=MAX(progress_percent, 55), updated_at=? WHERE id=? AND user_id=?", (now, int(req.get("id")), user_id))
    conn.commit()
    audit(conn, user_id, user_id, int(req.get("id")), "verification_document_uploaded", {}, {"document_type": document_type, "storage": "private"}, ip_hash, user_agent_hash)
    return {"ok": True, "message": "Verification document uploaded for private review."}


def verification_document_for_admin(conn: Any, doc_id: int, admin: dict[str, Any], ip_hash: str = "", user_agent_hash: str = "") -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT vd.*, vr.track FROM verification_documents vd LEFT JOIN verification_requests vr ON vr.id=vd.request_id WHERE vd.id=? LIMIT 1", (int(doc_id or 0),))
    doc = _row_dict(cur.fetchone())
    if not doc:
        return {"ok": False, "message": "Document not found."}
    if not can_review(admin, str(doc.get("track") or "identity")):
        return {"ok": False, "message": "This admin role cannot access that verification document."}
    now = _now()
    cur.execute("UPDATE verification_documents SET accessed_by=?, accessed_at=? WHERE id=?", (int((admin or {}).get("id") or 0), now, int(doc_id or 0)))
    conn.commit()
    audit(conn, int((admin or {}).get("id") or 0), int(doc.get("user_id") or 0), int(doc.get("request_id") or 0), "verification_document_accessed", {}, {"document_id": int(doc_id or 0), "document_type": doc.get("document_type")}, ip_hash, user_agent_hash)
    doc["ok"] = True
    return doc


def can_review(admin: dict[str, Any], track: str, action: str = "review") -> bool:
    role = str((admin or {}).get("role") or "").strip().lower()
    if role in READONLY_ADMIN_ROLES:
        return False
    if action in {"revoke", "restore"}:
        return role in {"owner", "super_admin", "admin"}
    return role in TRACK_ROLE_MAP.get(track, ADMIN_ROLES)


def admin_queue(conn: Any, admin: dict[str, Any], status: str = "", track: str = "") -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    query = "SELECT * FROM verification_requests WHERE 1=1"
    params: list[Any] = []
    if status:
        query += " AND status=?"
        params.append(status)
    if track:
        query += " AND track=?"
        params.append(track)
    query += " ORDER BY updated_at DESC, id DESC LIMIT 100"
    cur.execute(query, tuple(params))
    requests = [_row_dict(row) for row in cur.fetchall()]
    for item in requests:
        item["review_allowed"] = can_review(admin, str(item.get("track") or ""))
        item["documents"] = _count(cur, "verification_documents", "request_id=?", (_safe_int(item.get("id")),))
        cur.execute("SELECT id, document_type, review_status, created_at FROM verification_documents WHERE request_id=? ORDER BY id DESC LIMIT 10", (_safe_int(item.get("id")),))
        item["document_refs"] = [_row_dict(row) for row in cur.fetchall()]
    return {
        "ok": True,
        "requests": requests,
        "badges": _count(cur, "verification_badges"),
        "appeals": _count(cur, "verification_appeals", "status='submitted'"),
        "audit_logs": _count(cur, "verification_audit_logs"),
    }


def admin_decision(conn: Any, admin: dict[str, Any], request_id: int, action: str, reason: str = "", ip_hash: str = "", user_agent_hash: str = "") -> dict[str, Any]:
    ensure_tables(conn)
    action = str(action or "").strip().lower()
    allowed_actions = {"approve", "reject", "needs_more_info", "suspend", "revoke", "restore"}
    if action not in allowed_actions:
        return {"ok": False, "message": "Unsupported verification action."}
    admin_id = int((admin or {}).get("id") or 0)
    cur = conn.cursor()
    cur.execute("SELECT * FROM verification_requests WHERE id=? LIMIT 1", (int(request_id or 0),))
    before = _row_dict(cur.fetchone())
    if not before:
        return {"ok": False, "message": "Verification request not found."}
    track = str(before.get("track") or "")
    target_user_id = int(before.get("user_id") or 0)
    admin_email = str((admin or {}).get("email") or "").strip().lower()
    target_email = ""
    if _table_exists(cur, "users"):
        try:
            cur.execute("SELECT email FROM users WHERE user_id=? OR id=? LIMIT 1", (target_user_id, target_user_id))
            target_email = str(_row_dict(cur.fetchone()).get("email") or "").strip().lower()
        except Exception:
            target_email = ""
    if target_user_id == admin_id or (admin_email and target_email and admin_email == target_email):
        return {"ok": False, "message": "Admins cannot approve or review themselves."}
    if not can_review(admin, track, "revoke" if action == "revoke" else "review"):
        return {"ok": False, "message": "This admin role cannot perform that verification action."}
    now = _now()
    if action == "approve":
        status = "approved"
        cur.execute("UPDATE verification_requests SET status=?, progress_percent=100, reviewed_at=?, reviewer_id=?, rejection_reason=NULL, needs_more_info_reason=NULL, updated_at=? WHERE id=?", (status, now, admin_id, now, int(request_id)))
        badge_type = _badge_for_track(track)
        cur.execute(
            """
            INSERT INTO verification_badges
            (user_id, badge_type, source_track, status, approved_by, approved_at, created_at, updated_at)
            VALUES (?, ?, ?, 'approved', ?, ?, ?, ?)
            """,
            (target_user_id, badge_type, track, admin_id, now, now, now),
        )
    elif action == "reject":
        cur.execute("UPDATE verification_requests SET status='rejected', reviewed_at=?, reviewer_id=?, rejection_reason=?, updated_at=? WHERE id=?", (now, admin_id, reason[:1000], now, int(request_id)))
    elif action == "needs_more_info":
        cur.execute("UPDATE verification_requests SET status='needs_more_info', reviewed_at=?, reviewer_id=?, needs_more_info_reason=?, updated_at=? WHERE id=?", (now, admin_id, reason[:1000], now, int(request_id)))
    elif action == "suspend":
        cur.execute("UPDATE verification_requests SET status='suspended', reviewed_at=?, reviewer_id=?, rejection_reason=?, updated_at=? WHERE id=?", (now, admin_id, reason[:1000], now, int(request_id)))
        cur.execute("UPDATE verification_badges SET status='suspended', revoked_by=?, revoked_at=?, revoke_reason=?, updated_at=? WHERE user_id=? AND source_track=? AND status='approved'", (admin_id, now, reason[:1000], now, target_user_id, track))
    elif action == "revoke":
        cur.execute("UPDATE verification_requests SET status='revoked', reviewed_at=?, reviewer_id=?, rejection_reason=?, updated_at=? WHERE id=?", (now, admin_id, reason[:1000], now, int(request_id)))
        cur.execute("UPDATE verification_badges SET status='revoked', revoked_by=?, revoked_at=?, revoke_reason=?, updated_at=? WHERE user_id=? AND source_track=? AND status='approved'", (admin_id, now, reason[:1000], now, target_user_id, track))
    elif action == "restore":
        cur.execute("UPDATE verification_requests SET status='approved', progress_percent=100, reviewed_at=?, reviewer_id=?, updated_at=? WHERE id=?", (now, admin_id, now, int(request_id)))
    conn.commit()
    after = _first(cur, "verification_requests", "id=?", (int(request_id),))
    audit(conn, admin_id, target_user_id, int(request_id), f"admin_{action}", before, after, ip_hash, user_agent_hash)
    return {"ok": True, "message": f"Verification {action.replace('_', ' ')} recorded.", "request": after}


def _profile_complete(user: dict[str, Any]) -> bool:
    return bool(user.get("username") and (user.get("display_name") or user.get("full_name")) and user.get("email"))


def _has_avatar(user: dict[str, Any]) -> bool:
    return bool(user.get("avatar_url") or user.get("profile_photo_url") or user.get("photo_url"))


def _bio_complete(user: dict[str, Any]) -> bool:
    return bool(str(user.get("bio") or user.get("profile_bio") or "").strip())


def _notification_configured(cur: Any, user_id: int) -> bool:
    return _count(cur, "notification_preferences", "user_id=?", (user_id,)) > 0 or _count(cur, "pulsesoc_dashboard_preferences", "user_id=? AND pref_key LIKE 'notification_%'", (user_id,)) > 0


def _security_reviewed(cur: Any, user_id: int) -> bool:
    return _count(cur, "account_audit_logs", "user_id=? AND action LIKE '%security%'", (user_id,)) > 0 or _count(cur, "security_events", "user_id=?", (user_id,)) > 0


def _checklist(items: list[tuple[str, str, bool, str]]) -> dict[str, Any]:
    entries = [{"key": key, "label": label, "complete": bool(done), "route": route} for key, label, done, route in items]
    percent = int(sum(1 for item in entries if item["complete"]) * 100 / max(1, len(entries)))
    return {"items": entries, "completion_percent": percent}


def _goal_count(cur: Any, user_id: int) -> int:
    return _count(cur, "pulsesoc_user_goals", "user_id=? AND status!='deleted'", (user_id,))


def build_ai_advisor(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    posts = _count(cur, "pulse_posts", "user_id=?", (user_id,)) + _count(cur, "posts", "user_id=?", (user_id,))
    drafts = _count(cur, "pulse_post_drafts", "user_id=?", (user_id,)) + _count(cur, "creator_drafts", "user_id=?", (user_id,))
    scheduled = _count(cur, "scheduled_posts", "user_id=?", (user_id,))
    messages = _count(cur, "comm_v2_messages", "sender_id!=? AND id IN (SELECT message_id FROM comm_v2_read_receipts WHERE user_id=? AND read_at IS NULL)", (user_id, user_id))
    goals = _goal_count(cur, user_id)
    checklist = _checklist(
        [
            ("profile_completed", "Profile completed", _profile_complete(user), "/dashboard/account/profile"),
            ("profile_photo_added", "Profile photo added", _has_avatar(user), "/dashboard/account/profile"),
            ("bio_completed", "Bio completed", _bio_complete(user), "/dashboard/account/profile"),
            ("notifications_configured", "Notifications configured", _notification_configured(cur, user_id), "/dashboard/network/notifications"),
            ("security_reviewed", "Security reviewed", _security_reviewed(cur, user_id), "/dashboard/account/security"),
            ("first_goal_created", "First goal created", goals > 0, "/dashboard/intelligence/ai-advisor"),
            ("drafts_reviewed", "Drafts reviewed", drafts == 0, "/dashboard/creator/draft-studio"),
            ("scheduled_posts_checked", "Scheduled posts checked", scheduled == 0, "/dashboard/creator/post-scheduler"),
            ("marketplace_reviewed", "Marketplace activity reviewed", True, "/dashboard/economy/marketplace"),
            ("crypto_alerts_reviewed", "Crypto alerts reviewed", True, "/dashboard/crypto/alerts"),
            ("messages_reviewed", "Messages reviewed", messages == 0, "/dashboard/network/messages"),
            ("privacy_reviewed", "Privacy settings reviewed", _count(cur, "pulsesoc_dashboard_preferences", "user_id=? AND pref_key='privacy_reviewed'", (user_id,)) > 0, "/dashboard/account/settings"),
            ("top_priority_selected", "Top priority selected", _count(cur, "pulsesoc_dashboard_preferences", "user_id=? AND pref_key='top_priority'", (user_id,)) > 0, "/dashboard/intelligence/ai-advisor"),
            ("action_plan_reviewed", "Today's action plan reviewed", _count(cur, "pulsesoc_dashboard_preferences", "user_id=? AND pref_key='daily_plan_reviewed'", (user_id,)) > 0, "/dashboard/intelligence/ai-advisor"),
        ]
    )
    health_score = int((checklist["completion_percent"] * 0.65) + (min(posts, 10) * 2) + (10 if premium_identity_engine.has_active_premium(user) else 0))
    recommendations = [
        _recommendation("Improve profile", "Add missing profile basics.", "A complete profile improves trust and unlocks stronger recommendations.", "Profile fields, avatar, and bio", "/dashboard/account/profile", "high" if not _profile_complete(user) else "medium", limited=not _profile_complete(user)),
        _recommendation("Create a first goal", "Set one measurable PulseSoc goal.", "Goals let the advisor prioritize actions without inventing analytics.", "pulsesoc_user_goals", "/dashboard/intelligence/ai-advisor", "high" if goals == 0 else "medium", limited=goals == 0),
        _recommendation("Review messages", "Open Messenger and answer pending conversations.", "Unanswered messages can block creator, seller, and community momentum.", "message counts only", "/dashboard/network/messages", "medium", limited=messages == 0),
        _recommendation("Review crypto alerts", "Check alert setup and risk reminders.", "Crypto guidance is educational only; alerts keep watchlists explicit.", "crypto alert tables if available", "/dashboard/crypto/alerts", "medium", limited=True),
    ]
    return {
        "kind": "ai_advisor",
        "title": "AI Advisor",
        "summary": "Personal decision dashboard using only available PulseSoc state and clearly labeled data limits.",
        "metrics": {
            "daily_briefing": "No fabricated changes. Review real messages, drafts, scheduled posts, security reminders, and alerts below.",
            "account_health": health_score,
            "profile_strength": checklist["completion_percent"],
            "creator_progress": min(100, posts * 10),
            "business_progress": 0,
            "wallet_crypto_alerts": _count(cur, "crypto_alerts", "user_id=?", (user_id,)),
            "marketplace_activity": _count(cur, "pulsesoc_seller_products", "user_id=?", (user_id,)),
            "messages_needing_response": messages,
            "important_notifications": _count(cur, "notifications", "user_id=? AND COALESCE(read_at,'')=''", (user_id,)),
        },
        "checklist": checklist,
        "goals": _list_goals(cur, user_id),
        "recommendations": recommendations,
        "crypto_disclaimer": "Crypto information is for education and alerts only. It is not financial advice.",
        "score_explanation": "Account health is calculated from visible checklist completion, owned content count, and real premium status when available.",
    }


def _recommendation(title: str, what: str, why: str, source: str, route: str, confidence: str = "medium", limited: bool = False) -> dict[str, Any]:
    return {
        "title": title,
        "what_to_do": what,
        "why_it_matters": why,
        "expected_benefit": "Clearer next action and safer prioritization.",
        "confidence": confidence,
        "source_data_used": source,
        "route": route,
        "limited_data": bool(limited),
    }


def _list_goals(cur: Any, user_id: int) -> list[dict[str, Any]]:
    if not _table_exists(cur, "pulsesoc_user_goals"):
        return []
    cur.execute("SELECT * FROM pulsesoc_user_goals WHERE user_id=? AND status!='deleted' ORDER BY id DESC LIMIT 20", (user_id,))
    return [_row_dict(row) for row in cur.fetchall()]


def save_goal(conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    title = str(payload.get("title") or payload.get("goal") or "").strip()[:160]
    goal_type = str(payload.get("goal_type") or "custom").strip()[:80]
    if not title:
        return {"ok": False, "message": "Goal title is required."}
    now = _now()
    conn.cursor().execute(
        "INSERT INTO pulsesoc_user_goals (user_id, goal_type, title, status, progress_percent, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'active', 0, ?, ?, ?)",
        (user_id, goal_type, title, _json({"source": "ai_advisor"}), now, now),
    )
    conn.commit()
    return {"ok": True, "message": "Goal saved.", "goals": _goal_count(conn.cursor(), user_id)}


def build_seller_tools(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    store = _first(cur, "pulsesoc_seller_stores", "user_id=?", (user_id,))
    product = _first(cur, "pulsesoc_seller_products", "user_id=?", (user_id,))
    orders = _count(cur, "marketplace_orders", "seller_user_id=?", (user_id,))
    products = _count(cur, "pulsesoc_seller_products", "user_id=?", (user_id,))
    store_checklist = _checklist(
        [
            ("store_name", "Store name completed", bool(store.get("store_name")), "/dashboard/economy/seller-tools"),
            ("store_logo", "Store logo uploaded", False, "/dashboard/economy/seller-tools"),
            ("store_banner", "Store banner uploaded", False, "/dashboard/economy/seller-tools"),
            ("description", "Store description completed", bool(store.get("description")), "/dashboard/economy/seller-tools"),
            ("contact", "Contact information added", bool(store.get("contact_email")), "/dashboard/economy/seller-tools"),
            ("policies", "Store policies added", bool(store.get("policies")), "/dashboard/economy/seller-tools"),
            ("shipping", "Shipping policy added if physical products", bool(store.get("shipping_policy")) or products == 0, "/dashboard/economy/seller-tools"),
            ("return_policy", "Return policy added", bool(store.get("return_policy")), "/dashboard/economy/seller-tools"),
            ("visibility", "Store visibility selected", bool(store.get("visibility")), "/dashboard/economy/seller-tools"),
            ("verification", "Verification status reviewed", _track_approved(cur, user_id, "seller"), "/dashboard/account/verification"),
            ("payout", "Payment/payout setup reviewed if supported", False, "/dashboard/economy/payouts"),
            ("notifications", "Notification settings reviewed", _notification_configured(cur, user_id), "/dashboard/network/notifications"),
        ]
    )
    product_checklist = _checklist(
        [
            ("product_name", "Product name completed", bool(product.get("name")), "/dashboard/economy/seller-tools"),
            ("description", "Product description completed", bool(product.get("description")), "/dashboard/economy/seller-tools"),
            ("category", "Product category selected", bool(product.get("category")), "/dashboard/economy/seller-tools"),
            ("images", "Product images uploaded", False, "/dashboard/economy/seller-tools"),
            ("price", "Price added", _safe_int(product.get("price_cents"), 0) > 0, "/dashboard/economy/seller-tools"),
            ("inventory", "Inventory added if physical product", str(product.get("product_type") or "") != "physical" or _safe_int(product.get("inventory"), 0) >= 0, "/dashboard/economy/seller-tools"),
            ("shipping", "Shipping option selected if physical product", str(product.get("product_type") or "") != "physical", "/dashboard/economy/seller-tools"),
            ("digital", "Digital delivery configured if digital product", str(product.get("product_type") or "") != "digital", "/dashboard/economy/seller-tools"),
            ("tax", "Tax settings reviewed", False, "/dashboard/economy/seller-tools"),
            ("visibility", "Visibility selected", bool(product.get("visibility")), "/dashboard/economy/seller-tools"),
            ("return", "Return eligibility reviewed", product.get("return_eligible") is not None, "/dashboard/economy/seller-tools"),
            ("preview", "Listing preview reviewed", False, "/dashboard/economy/seller-tools"),
        ]
    )
    return {
        "kind": "seller_tools",
        "title": "Seller Tools",
        "summary": "Commerce operating system with safe zero states. No sales, orders, payouts, or analytics are fabricated.",
        "metrics": {
            "today_sales": "$0.00",
            "revenue": "$0.00",
            "orders_awaiting_fulfillment": orders,
            "orders_in_transit": 0,
            "returns": 0,
            "buyer_messages": 0,
            "inventory_alerts": 0,
            "low_stock_warnings": 0,
            "store_health_score": store_checklist["completion_percent"],
            "customer_satisfaction_score": "Unavailable",
        },
        "store": store,
        "products_count": products,
        "store_checklist": store_checklist,
        "product_checklist": product_checklist,
        "recommendations": [
            _recommendation("Complete store setup", "Add store profile, policies, contact, and visibility.", "Seller activation depends on real readiness fields.", "pulsesoc_seller_stores", "/dashboard/economy/seller-tools", "high", limited=not bool(store)),
            _recommendation("Submit seller verification", "Start seller or business verification.", "Seller tools, payouts, and marketplace trust must be backend-approved.", "verification_requests", "/dashboard/account/verification", "high", limited=not _track_approved(cur, user_id, "seller")),
        ],
    }


def save_store(conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    now = _now()
    values = {
        "store_name": str(payload.get("store_name") or "").strip()[:160],
        "description": str(payload.get("description") or "").strip()[:1000],
        "contact_email": str(payload.get("contact_email") or "").strip()[:180],
        "policies": str(payload.get("policies") or "").strip()[:2000],
        "shipping_policy": str(payload.get("shipping_policy") or "").strip()[:2000],
        "return_policy": str(payload.get("return_policy") or "").strip()[:2000],
        "visibility": str(payload.get("visibility") or "draft").strip()[:40],
        "vacation_mode": 1 if payload.get("vacation_mode") else 0,
    }
    if not values["store_name"]:
        return {"ok": False, "message": "Store name is required."}
    cur = conn.cursor()
    existing = _first(cur, "pulsesoc_seller_stores", "user_id=?", (user_id,))
    if existing:
        cur.execute(
            """
            UPDATE pulsesoc_seller_stores SET store_name=?, description=?, contact_email=?, policies=?, shipping_policy=?,
            return_policy=?, visibility=?, vacation_mode=?, updated_at=? WHERE id=? AND user_id=?
            """,
            (*values.values(), now, int(existing.get("id")), user_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO pulsesoc_seller_stores
            (user_id, store_name, description, contact_email, policies, shipping_policy, return_policy, visibility, vacation_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, *values.values(), now, now),
        )
    conn.commit()
    return {"ok": True, "message": "Store saved."}


def save_product(conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    name = str(payload.get("name") or "").strip()[:180]
    if not name:
        return {"ok": False, "message": "Product name is required."}
    price_cents = max(0, int(float(payload.get("price") or 0) * 100))
    inventory = int(payload.get("inventory") or 0)
    if inventory < 0:
        return {"ok": False, "message": "Inventory cannot be negative."}
    now = _now()
    conn.cursor().execute(
        """
        INSERT INTO pulsesoc_seller_products
        (user_id, product_type, name, description, category, price_cents, inventory, visibility, return_eligible, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            str(payload.get("product_type") or "physical")[:60],
            name,
            str(payload.get("description") or "")[:2000],
            str(payload.get("category") or "")[:120],
            price_cents,
            inventory,
            str(payload.get("visibility") or "draft")[:40],
            1 if payload.get("return_eligible") else 0,
            _json({"source": "seller_tools"}),
            now,
            now,
        ),
    )
    conn.commit()
    return {"ok": True, "message": "Product draft saved. Publish remains blocked until readiness checks pass."}


def build_subscriptions(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    premium = premium_identity_engine.has_active_premium(user)
    plan = str(user.get("plan") or user.get("subscription_plan") or ("premium" if premium else "free"))
    subscription_rows = _count(cur, "subscriptions", "user_id=?", (user_id,))
    failed = _count(cur, "payment_records", "user_id=? AND status IN ('failed','requires_payment_method')", (user_id,))
    checklist = _checklist(
        [
            ("plan_selected", "Plan selected", bool(plan), "/dashboard/economy/subscriptions"),
            ("payment_method", "Payment method added", False, "/dashboard/economy/subscriptions"),
            ("billing_email", "Billing email confirmed", bool(user.get("email_verified") or user.get("confirmed_email")), "/dashboard/account/settings"),
            ("renewal", "Renewal date reviewed", False, "/dashboard/economy/subscriptions"),
            ("benefits", "Benefits reviewed", _pref(cur, user_id, "subscription_benefits_reviewed"), "/dashboard/economy/premium"),
            ("premium_access", "Premium access confirmed", premium, "/dashboard/economy/premium"),
            ("notifications", "Notification preferences set", _notification_configured(cur, user_id), "/dashboard/network/notifications"),
            ("cancellation", "Cancellation policy reviewed", _pref(cur, user_id, "cancellation_policy_reviewed"), "/dashboard/economy/subscriptions"),
            ("history", "Billing history reviewed", _pref(cur, user_id, "billing_history_reviewed"), "/dashboard/economy/subscriptions"),
        ]
    )
    health = int((checklist["completion_percent"] * 0.7) + (20 if failed == 0 else 0) + (10 if premium else 0))
    return {
        "kind": "subscriptions",
        "title": "Manage Subscriptions",
        "summary": "Billing and entitlement control center. Values are sourced from account/subscription rows or shown as unavailable.",
        "metrics": {
            "current_plan": plan.title(),
            "founder_premium_status": "Active" if premium else "Not active",
            "renewal_date": "Unavailable",
            "cost": "Unavailable",
            "payment_method_summary": "Use billing portal where configured",
            "active_subscriptions": subscription_rows,
            "canceled_subscriptions": 0,
            "trial_status": str(user.get("trial_status") or "Unavailable"),
            "failed_payments": failed,
            "upcoming_charges": "Unavailable",
            "premium_access_status": "Active" if premium else "Locked",
            "subscription_health_score": health,
        },
        "checklist": checklist,
        "recommendations": [
            _recommendation("Switch billing safely", "Use the secure billing controls for payment method or plan changes when a provider subscription exists.", "This avoids exposing payment method details in PulseSoc and disables the action when Stripe is not connected.", "Stripe portal availability and backend subscription status", "/dashboard/economy/subscriptions", "medium", limited=True),
            _recommendation("Review premium access", "Confirm which entitlements are active.", "Access badges must match backend permissions.", "account plan and entitlement fields", "/dashboard/economy/premium", "high", limited=not premium),
        ],
        "score_explanation": "Subscription health is calculated from checklist completion, failed payment count, and real premium entitlement state.",
    }


def _pref(cur: Any, user_id: int, key: str) -> bool:
    return _count(cur, "pulsesoc_dashboard_preferences", "user_id=? AND pref_key=? AND pref_value='true'", (user_id, key)) > 0


def save_pref(conn: Any, user: dict[str, Any], key: str, value: str = "true") -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    key = str(key or "").strip()[:120]
    if not key:
        return {"ok": False, "message": "Preference key required."}
    cur = conn.cursor()
    existing = _first(cur, "pulsesoc_dashboard_preferences", "user_id=? AND pref_key=?", (user_id, key))
    now = _now()
    if existing:
        cur.execute("UPDATE pulsesoc_dashboard_preferences SET pref_value=?, updated_at=? WHERE id=? AND user_id=?", (str(value)[:500], now, int(existing.get("id")), user_id))
    else:
        cur.execute("INSERT INTO pulsesoc_dashboard_preferences (user_id, pref_key, pref_value, created_at, updated_at) VALUES (?, ?, ?, ?, ?)", (user_id, key, str(value)[:500], now, now))
    conn.commit()
    return {"ok": True, "message": "Preference saved."}


def build_premium_center(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    premium = premium_identity_engine.has_active_premium(user)
    founder = bool(str(user.get("plan") or user.get("subscription_plan") or "").lower() == "founder" or user.get("founder_status") == "active")
    plan = str(user.get("plan") or user.get("subscription_plan") or ("premium" if premium else "free"))
    checklist = _checklist(
        [
            ("plan", "Plan selected", bool(plan), "/dashboard/economy/subscriptions"),
            ("payment", "Payment method added", False, "/dashboard/economy/subscriptions"),
            ("email", "Billing email confirmed", bool(user.get("email_verified") or user.get("confirmed_email")), "/dashboard/account/settings"),
            ("access", "Premium access verified", premium, "/dashboard/economy/premium"),
            ("founder", "Founder badge verified if applicable", not founder or _has_badge(cur, user_id, "founder_badge"), "/dashboard/economy/premium"),
            ("benefits", "Benefits reviewed", _pref(cur, user_id, "premium_benefits_reviewed"), "/dashboard/economy/premium"),
            ("ai_usage", "AI usage reviewed", _pref(cur, user_id, "premium_ai_usage_reviewed"), "/dashboard/economy/premium"),
            ("storage", "Storage usage reviewed", _pref(cur, user_id, "premium_storage_reviewed"), "/dashboard/economy/premium"),
            ("notifications", "Notification preferences reviewed", _notification_configured(cur, user_id), "/dashboard/network/notifications"),
            ("renewal", "Renewal date reviewed", _pref(cur, user_id, "premium_renewal_reviewed"), "/dashboard/economy/subscriptions"),
            ("portal", "Billing portal available if supported", False, "/dashboard/economy/subscriptions"),
            ("security", "Premium security reviewed", _security_reviewed(cur, user_id), "/dashboard/account/security"),
        ]
    )
    benefits = []
    for key, label, required in (
        ("ai_creator_assistant", "AI Creator Assistant", premium),
        ("ai_advisor", "AI Advisor", premium),
        ("seller_tools", "Seller Tools", premium and _track_approved(cur, user_id, "seller")),
        ("creator_studio", "Creator Studio", True),
        ("premium_analytics", "Premium Analytics", premium),
        ("premium_communities", "Premium Communities", premium),
        ("premium_marketplace", "Premium Marketplace", premium),
        ("advanced_search", "Advanced Search", premium),
        ("premium_labs", "Premium Labs", premium),
        ("undx", "UNDX Premium Access", premium),
    ):
        benefits.append({"key": key, "label": label, "status": "Active" if required else "Locked", "route": _benefit_route(key), "unlock_plan": "Premium"})
    return {
        "kind": "premium",
        "title": "Premium Center",
        "summary": "Premium command center with entitlement-aware access, billing handoff, usage limits, and safe unavailable states.",
        "metrics": {
            "current_plan": plan.title(),
            "founder_status": "Active" if founder else "Not active",
            "membership_level": plan.title(),
            "premium_health_score": checklist["completion_percent"],
            "active_benefits": sum(1 for item in benefits if item["status"] == "Active"),
            "next_renewal": "Unavailable",
            "premium_credits": "Unavailable",
            "ai_usage": "Unavailable",
            "storage_usage": "Unavailable",
            "exclusive_content_available": 0,
            "billing_alerts": 0,
        },
        "checklist": checklist,
        "benefits": benefits,
        "recommendations": [
            _recommendation("Explore AI Advisor", "Open AI Advisor if your plan includes it.", "Advisor activity is tracked from real exploration events only.", "premium entitlement state", "/dashboard/intelligence/ai-advisor", "medium", limited=not premium),
            _recommendation("Manage subscription", "Review plan and billing access.", "Billing changes must route through secure backend/provider flows.", "plan and subscription rows", "/dashboard/economy/subscriptions", "high", limited=True),
        ],
    }


def _benefit_route(key: str) -> str:
    return {
        "ai_creator_assistant": "/dashboard/ai/creative-studio",
        "ai_advisor": "/dashboard/intelligence/ai-advisor",
        "seller_tools": "/dashboard/economy/seller-tools",
        "creator_studio": "/dashboard/creator",
        "premium_analytics": "/dashboard/creator/content-performance",
        "premium_communities": "/dashboard/network/groups",
        "premium_marketplace": "/dashboard/economy/marketplace",
        "advanced_search": "/search",
        "premium_labs": "/pulse/labs",
        "undx": "/pulse/premium/undx",
    }.get(key, "/dashboard/economy/premium")


def _has_badge(cur: Any, user_id: int, badge_type: str) -> bool:
    return _count(cur, "verification_badges", "user_id=? AND badge_type=? AND status='approved' AND revoked_at IS NULL", (user_id, badge_type)) > 0


def mark_premium_explored(conn: Any, user: dict[str, Any], feature_key: str) -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    feature_key = str(feature_key or "").strip()[:120]
    if not feature_key:
        return {"ok": False, "message": "Feature key required."}
    cur = conn.cursor()
    existing = _first(cur, "pulsesoc_premium_exploration", "user_id=? AND feature_key=?", (user_id, feature_key))
    now = _now()
    if existing:
        cur.execute("UPDATE pulsesoc_premium_exploration SET status='reviewed', updated_at=? WHERE id=? AND user_id=?", (now, int(existing.get("id")), user_id))
    else:
        cur.execute("INSERT INTO pulsesoc_premium_exploration (user_id, feature_key, status, created_at, updated_at) VALUES (?, ?, 'reviewed', ?, ?)", (user_id, feature_key, now, now))
    conn.commit()
    return {"ok": True, "message": "Premium exploration saved."}


def _content_item_checklist(item: dict[str, Any]) -> dict[str, Any]:
    content_type = str(item.get("content_type") or "")
    return _checklist(
        [
            ("caption", "Caption completed", bool(item.get("caption")), "/dashboard/creator/content-planner"),
            ("media", "Media attached", bool(item.get("media_attached")) or content_type == "text", "/dashboard/creator/content-planner"),
            ("thumbnail", "Thumbnail selected", bool(item.get("thumbnail_selected")) or content_type in {"text", "photo", "poll"}, "/dashboard/creator/content-planner"),
            ("alt_text", "Alt text added", bool(item.get("alt_text")) or content_type == "text", "/dashboard/creator/content-planner"),
            ("hashtags", "Hashtags included", bool(item.get("hashtags")), "/dashboard/creator/content-planner"),
            ("audience", "Audience selected", bool(item.get("audience")), "/dashboard/creator/content-planner"),
            ("monetization", "Monetization selected", True, "/dashboard/creator/content-planner"),
            ("links", "Links validated", bool(item.get("links_validated")) or "http" not in str(item.get("caption") or ""), "/dashboard/creator/content-planner"),
            ("time", "Scheduled time selected", bool(item.get("scheduled_at")) or str(item.get("status") or "") != "scheduled", "/dashboard/creator/content-planner"),
            ("preview", "Final preview reviewed", bool(item.get("final_preview_reviewed")), "/dashboard/creator/content-planner"),
        ]
    )


def build_content_planner(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    cur.execute("SELECT * FROM pulsesoc_content_planner_items WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,))
    items = [_row_dict(row) for row in cur.fetchall()]
    cur.execute("SELECT * FROM pulsesoc_content_campaigns WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,))
    campaigns = [_row_dict(row) for row in cur.fetchall()]
    scheduled_today = sum(1 for item in items if str(item.get("status") or "") == "scheduled")
    drafts = sum(1 for item in items if str(item.get("status") or "") == "draft")
    first = items[0] if items else {}
    checklist = _content_item_checklist(first) if first else _checklist(
        [
            ("caption", "Caption completed", False, "/dashboard/creator/content-planner"),
            ("media", "Media attached", False, "/dashboard/creator/content-planner"),
            ("thumbnail", "Thumbnail selected", False, "/dashboard/creator/content-planner"),
            ("alt_text", "Alt text added", False, "/dashboard/creator/content-planner"),
            ("hashtags", "Hashtags included", False, "/dashboard/creator/content-planner"),
            ("audience", "Audience selected", False, "/dashboard/creator/content-planner"),
            ("monetization", "Monetization selected", True, "/dashboard/creator/content-planner"),
            ("links", "Links validated", True, "/dashboard/creator/content-planner"),
            ("time", "Scheduled time selected", False, "/dashboard/creator/content-planner"),
            ("preview", "Final preview reviewed", False, "/dashboard/creator/content-planner"),
        ]
    )
    return {
        "kind": "content_planner",
        "title": "Content Planner",
        "summary": "Creator planning workspace backed by owned draft records. Scheduling and AI actions validate capability before claiming success.",
        "metrics": {
            "scheduled_today": scheduled_today,
            "drafts": drafts,
            "upcoming_campaigns": len([c for c in campaigns if str(c.get("status") or "").lower() in {"planning", "active"}]),
            "weekly_publishing_goal": "Unavailable",
            "publishing_streak": "Unavailable",
            "content_pieces_this_week": len(items),
            "estimated_reach": "Unavailable",
            "best_posting_time": "Unavailable",
            "recent_performance": "Unavailable",
        },
        "items": items,
        "campaigns": campaigns,
        "checklist": checklist,
        "recommendations": [
            _recommendation("Create a draft", "Save a content idea with caption, audience, and type.", "Draft records unlock checklist and scheduling validation.", "pulsesoc_content_planner_items", "/dashboard/creator/content-planner", "high", limited=not bool(items)),
            _recommendation("Review final preview", "Mark preview reviewed before scheduling.", "Scheduling incomplete content should warn or block.", "content checklist", "/dashboard/creator/content-planner", "medium", limited=checklist["completion_percent"] < 100),
        ],
    }


def build_post_scheduler(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    cur.execute("SELECT * FROM pulsesoc_content_planner_items WHERE user_id=? AND status IN ('scheduled','draft') ORDER BY COALESCE(scheduled_at,'9999') ASC, id DESC LIMIT 40", (user_id,))
    items = [_row_dict(row) for row in cur.fetchall()]
    scheduled = [item for item in items if str(item.get("status") or "") == "scheduled"]
    next_item = scheduled[0] if scheduled else {}
    checklist = _checklist(
        [
            ("content_type", "Content type selected", bool(next_item.get("content_type")), "/dashboard/creator/post-scheduler"),
            ("caption", "Caption/body ready", bool(next_item.get("caption")), "/dashboard/creator/post-scheduler"),
            ("audience", "Audience selected", bool(next_item.get("audience")), "/dashboard/creator/post-scheduler"),
            ("scheduled_time", "Scheduled time selected", bool(next_item.get("scheduled_at")), "/dashboard/creator/post-scheduler"),
            ("links", "Links validated", bool(next_item.get("links_validated")) or "http" not in str(next_item.get("caption") or ""), "/dashboard/creator/post-scheduler"),
            ("preview", "Final preview reviewed", bool(next_item.get("final_preview_reviewed")), "/dashboard/creator/post-scheduler"),
            ("queue", "Queue reviewed", bool(items), "/dashboard/creator/post-scheduler"),
            ("timezone", "Timezone confirmed", bool(user.get("timezone")), "/dashboard/account/settings"),
        ]
    )
    return {
        "kind": "post_scheduler",
        "title": "Post Scheduler",
        "summary": "Owner-scoped scheduling queue backed by planner draft records. Publishing is blocked unless required fields exist.",
        "metrics": {
            "scheduled_posts": len(scheduled),
            "drafts_available": len([item for item in items if str(item.get("status") or "") == "draft"]),
            "next_scheduled_time": next_item.get("scheduled_at") or "Unavailable",
            "timezone": user.get("timezone") or "Unavailable",
            "failed_publishes": 0,
            "queue_health": checklist["completion_percent"],
        },
        "items": items,
        "checklist": checklist,
        "recommendations": [
            _recommendation("Schedule a ready draft", "Choose a content item, audience, and scheduled time.", "The scheduler can only act on owned drafts with complete required fields.", "pulsesoc_content_planner_items", "/dashboard/creator/post-scheduler", "high", limited=not bool(scheduled)),
            _recommendation("Review preview before scheduling", "Mark final preview reviewed.", "This prevents incomplete posts from entering the queue.", "scheduler checklist", "/dashboard/creator/content-planner", "medium", limited=checklist["completion_percent"] < 100),
        ],
    }


def build_draft_studio(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    cur.execute("SELECT * FROM pulsesoc_content_planner_items WHERE user_id=? AND status='draft' ORDER BY updated_at DESC, id DESC LIMIT 40", (user_id,))
    drafts = [_row_dict(row) for row in cur.fetchall()]
    current = drafts[0] if drafts else {}
    checklist = _checklist(
        [
            ("title", "Title added", bool(current.get("title")), "/dashboard/creator/draft-studio"),
            ("caption", "Caption/body added", bool(current.get("caption")), "/dashboard/creator/draft-studio"),
            ("content_type", "Content type selected", bool(current.get("content_type")), "/dashboard/creator/draft-studio"),
            ("hashtags", "Hashtags reviewed", bool(current.get("hashtags")), "/dashboard/creator/draft-studio"),
            ("audience", "Audience selected", bool(current.get("audience")), "/dashboard/creator/draft-studio"),
            ("media", "Media requirement reviewed", bool(current.get("media_attached")) or str(current.get("content_type") or "") == "text", "/dashboard/creator/draft-studio"),
            ("alt_text", "Alt text reviewed", bool(current.get("alt_text")) or str(current.get("content_type") or "") == "text", "/dashboard/creator/draft-studio"),
            ("preview", "Preview reviewed", bool(current.get("final_preview_reviewed")), "/dashboard/creator/draft-studio"),
        ]
    )
    return {
        "kind": "draft_studio",
        "title": "Draft Studio",
        "summary": "Owned draft workspace using persisted planner records. Autosave is represented by explicit Save Draft writes.",
        "metrics": {
            "drafts": len(drafts),
            "ready_drafts": len([draft for draft in drafts if _content_item_checklist(draft)["completion_percent"] >= 80]),
            "needs_media": len([draft for draft in drafts if not draft.get("media_attached") and str(draft.get("content_type") or "") not in {"text", ""}]),
            "needs_preview": len([draft for draft in drafts if not draft.get("final_preview_reviewed")]),
            "version_history": "Unavailable",
            "autosave": "Manual save enabled",
        },
        "items": drafts,
        "checklist": checklist,
        "recommendations": [
            _recommendation("Finish a draft", "Add missing caption, audience, media review, and preview state.", "Complete drafts can move safely into scheduling.", "pulsesoc_content_planner_items", "/dashboard/creator/draft-studio", "high", limited=not bool(drafts)),
        ],
    }


def build_ai_creator_assistant(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or 0)
    drafts = _count(cur, "pulsesoc_content_planner_items", "user_id=? AND status='draft'", (user_id,))
    ai_rows = _count(cur, "ai_messages", "user_id=?", (user_id,)) + _count(cur, "ai_conversations", "user_id=?", (user_id,))
    premium = premium_identity_engine.has_active_premium(user)
    checklist = _checklist(
        [
            ("draft_context", "Draft context available", drafts > 0, "/dashboard/creator/draft-studio"),
            ("profile_context", "Profile context available", _profile_complete(user), "/dashboard/account/profile"),
            ("privacy_review", "Private prompt boundary reviewed", _pref(cur, user_id, "ai_creator_privacy_reviewed"), "/dashboard/creator/ai-creator-assistant"),
            ("ai_endpoint", "AI endpoint available", ai_rows > 0, "/dashboard/ai/creative-studio"),
            ("premium_access", "Premium access verified if required", premium, "/dashboard/economy/premium"),
            ("human_review", "Human confirmation required before applying AI", True, "/dashboard/creator/ai-creator-assistant"),
        ]
    )
    return {
        "kind": "ai_creator_assistant",
        "title": "AI Creator Assistant",
        "summary": "Creator-focused AI surface. It does not generate fake successful AI output when a live endpoint is unavailable.",
        "metrics": {
            "drafts_available": drafts,
            "ai_history_rows": ai_rows,
            "premium_access": "Active" if premium else "Locked or unavailable",
            "image_generation_credits": "Unavailable",
            "video_assistance": "Unavailable",
            "research_tools": "Unavailable",
            "assistant_health": checklist["completion_percent"],
        },
        "checklist": checklist,
        "recommendations": [
            _recommendation("Open a draft before requesting AI help", "Create or select a draft.", "AI suggestions need real user context and must never overwrite content without confirmation.", "draft count", "/dashboard/creator/draft-studio", "high", limited=drafts == 0),
            _recommendation("Review AI privacy boundary", "Confirm private notes and drafts are intentionally included before AI use.", "This prevents accidental private data exposure.", "dashboard preference", "/dashboard/creator/ai-creator-assistant", "high", limited=not _pref(cur, user_id, "ai_creator_privacy_reviewed")),
        ],
    }


def save_content_item(conn: Any, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_tables(conn)
    user_id = int(user.get("user_id") or 0)
    title = str(payload.get("title") or "").strip()[:180]
    caption = str(payload.get("caption") or "").strip()[:5000]
    content_type = str(payload.get("content_type") or "text").strip()[:60]
    status = str(payload.get("status") or "draft").strip()[:60]
    if not title and not caption:
        return {"ok": False, "message": "Add a title or caption before saving a draft."}
    if status == "scheduled" and not payload.get("scheduled_at"):
        return {"ok": False, "message": "Scheduled content needs a scheduled time."}
    now = _now()
    conn.cursor().execute(
        """
        INSERT INTO pulsesoc_content_planner_items
        (user_id, content_type, stage, title, caption, hashtags, audience, scheduled_at, media_attached,
         thumbnail_selected, alt_text, links_validated, final_preview_reviewed, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            content_type,
            str(payload.get("stage") or "ideas")[:60],
            title,
            caption,
            str(payload.get("hashtags") or "")[:500],
            str(payload.get("audience") or "")[:120],
            str(payload.get("scheduled_at") or "")[:120],
            1 if payload.get("media_attached") else 0,
            1 if payload.get("thumbnail_selected") else 0,
            str(payload.get("alt_text") or "")[:1000],
            1 if payload.get("links_validated") else 0,
            1 if payload.get("final_preview_reviewed") else 0,
            status,
            now,
            now,
        ),
    )
    conn.commit()
    return {"ok": True, "message": "Content draft saved."}
