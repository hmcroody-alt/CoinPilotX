"""PulseSoc Page OS — canonical pages for artists, businesses and organizations.

One shared backend for every page type. PERSON ≠ PAGE ≠ STORE: a user owns
pages, a page links to (never duplicates) the user's store, ad accounts,
communities and events. Migrations are additive only; nothing here touches
audio, live, calls, feed ranking or payment math.

Design rules enforced in this module:
- Bounded roles: OWNER > ADMIN > MANAGER / CONTENT_MANAGER /
  ADVERTISING_MANAGER / MARKETPLACE_MANAGER / ANALYST. Permissions come from
  the matrix, never from client input.
- Ownership transfer is explicit (confirmation phrase), audited, owner-only.
- Invites are role-specific, expiring and auditable; acceptance never grants
  OWNER.
- Handles share the platform grammar, respect a reserved list and are checked
  against BOTH existing pages and user handles (impersonation-aware).
- No hard delete: pages move between ACTIVE / PAUSED / UNPUBLISHED /
  DEACTIVATED and history stays auditable.
- Sentinel emission is observational and best-effort: a Sentinel failure must
  never block a page operation, and Sentinel never auto-seizes or deletes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from services.schema_guard import run_once_per_process

PAGE_TYPES = (
    "ARTIST", "CREATOR", "PUBLIC_FIGURE", "BUSINESS", "BRAND", "STORE",
    "RESTAURANT", "PROFESSIONAL_SERVICE", "LOCAL_BUSINESS", "NONPROFIT",
    "ORGANIZATION", "MEDIA", "SPORTS_TEAM", "VENUE", "EDUCATION", "OTHER",
)

PAGE_STATUSES = ("ACTIVE", "PAUSED", "UNPUBLISHED", "DEACTIVATED")

# Which page types get which optional tab modules on the client. Only tabs
# with real backing data render; this is the ceiling, not a promise.
TYPE_TABS = {
    "ARTIST": ["posts", "music", "videos", "events", "merch", "about"],
    "CREATOR": ["posts", "videos", "events", "merch", "about"],
    "PUBLIC_FIGURE": ["posts", "videos", "events", "about"],
    "BUSINESS": ["home", "services", "shop", "about"],
    "BRAND": ["home", "shop", "about"],
    "STORE": ["home", "shop", "about"],
    "RESTAURANT": ["home", "menu", "about"],
    "PROFESSIONAL_SERVICE": ["home", "services", "about"],
    "LOCAL_BUSINESS": ["home", "services", "shop", "about"],
    "NONPROFIT": ["home", "events", "about"],
    "ORGANIZATION": ["home", "events", "about"],
    "MEDIA": ["posts", "videos", "about"],
    "SPORTS_TEAM": ["posts", "events", "shop", "about"],
    "VENUE": ["home", "events", "about"],
    "EDUCATION": ["home", "events", "about"],
    "OTHER": ["posts", "about"],
}

ROLES = (
    "OWNER", "ADMIN", "MANAGER", "CONTENT_MANAGER",
    "ADVERTISING_MANAGER", "MARKETPLACE_MANAGER", "ANALYST",
)
# Roles an invite or role-change may assign. OWNER is deliberately absent:
# ownership moves only through transfer_ownership.
ASSIGNABLE_ROLES = tuple(r for r in ROLES if r != "OWNER")

_ALL = set(ROLES)
PERMISSIONS = {
    "view_analytics": _ALL,
    "create_content": {"OWNER", "ADMIN", "MANAGER", "CONTENT_MANAGER"},
    "edit_page": {"OWNER", "ADMIN", "MANAGER"},
    "manage_ads": {"OWNER", "ADMIN", "ADVERTISING_MANAGER"},
    "manage_marketplace": {"OWNER", "ADMIN", "MARKETPLACE_MANAGER"},
    "manage_members": {"OWNER", "ADMIN"},
    "manage_links": {"OWNER", "ADMIN", "MANAGER"},
    "manage_status": {"OWNER"},
    "transfer_ownership": {"OWNER"},
}

LINK_TYPES = ("store", "ad_account", "community", "event", "music_artist", "business_os")

# Tabs that always render: they are backed by the page row itself, so they can
# never be empty in a way the viewer would read as broken.
ALWAYS_TABS = {"home", "posts", "about"}

# Optional tab -> the link_type that gives it real content. A tab with no link
# and no rows is hidden from the public and kept (as a setup prompt) for the
# team, because an empty module reads as a dead button.
TAB_LINK_SOURCE = {
    "music": "music_artist",
    "shop": "store",
    "merch": "store",
    "menu": "store",
    "events": "event",
}

HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
RESERVED_HANDLES = {
    "admin", "administrator", "pulsesoc", "pulse", "official", "support",
    "help", "api", "root", "system", "moderator", "mod", "staff", "team",
    "verified", "security", "billing", "payments", "wallet", "settings",
    "login", "signup", "account", "pages", "page", "store", "marketplace",
    "undx", "sentinel", "everyone", "here", "null", "undefined", "anonymous",
}

INVITE_TTL_DAYS = 7
TRANSFER_CONFIRM_PHRASE = "TRANSFER"

VERIFICATION_STATUSES = ("unverified", "pending", "verified", "rejected")


class PageError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(row: Any) -> dict:
    return dict(row) if row is not None else {}


def _text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"<[^>]*>", "", str(value if value is not None else "")).strip()
    return text[:limit]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Schema (additive only)
# ---------------------------------------------------------------------------

@run_once_per_process
def ensure_tables(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            page_type TEXT NOT NULL,
            category TEXT DEFAULT '',
            subcategory TEXT DEFAULT '',
            name TEXT NOT NULL,
            handle TEXT NOT NULL,
            avatar_url TEXT DEFAULT '',
            cover_url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            genre TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            website TEXT DEFAULT '',
            location TEXT DEFAULT '',
            hours_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'ACTIVE',
            verification_status TEXT DEFAULT 'unverified',
            created_at TEXT,
            updated_at TEXT,
            deactivated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_page_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            invited_by INTEGER,
            invite_token TEXT,
            invite_expires_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(page_id, user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_page_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER,
            actor_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT DEFAULT '{}',
            after_json TEXT DEFAULT '{}',
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_page_follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT,
            UNIQUE(page_id, user_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_page_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT,
            UNIQUE(page_id, link_type, ref_id)
        )
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_pages_handle ON pulse_pages(lower(handle))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_pages_owner ON pulse_pages(owner_user_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_page_members_user ON pulse_page_members(user_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_page_audit_page ON pulse_page_audit(page_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pulse_page_follows_page ON pulse_page_follows(page_id)")
    conn.commit()


# ---------------------------------------------------------------------------
# Audit + Sentinel (best-effort, never blocking)
# ---------------------------------------------------------------------------

def _audit(conn: Any, page_id: int, actor_user_id: int, action: str,
           before: dict | None = None, after: dict | None = None) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_page_audit (page_id, actor_user_id, action, before_json, after_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(page_id), int(actor_user_id), action,
         json.dumps(before or {}, default=str), json.dumps(after or {}, default=str), _now()),
    )


def _sentinel_event(event_type: str, actor_user_id: int, page_id: int,
                    severity: str = "info", category: str = "ADMIN",
                    payload: dict | None = None) -> None:
    """Observe. Sentinel may correlate and open incidents downstream; it never
    acts on pages from here, and its failure never blocks the page write."""
    try:
        from services.sentinel import events as sentinel_events
        sentinel_events.ingest(sentinel_events.Event(
            category=category,
            event_type=event_type,
            severity=severity,
            actor_id=f"user:{int(actor_user_id)}",
            actor_type="USER",
            source="pulsesoc.pages",
            source_system="pulsesoc",
            source_component="pulsesoc_pages",
            source_trust="INTERNAL_DIRECT",
            subject_type="page",
            subject_id=str(int(page_id)),
            payload=payload or {},
        ))
    except Exception as exc:  # pragma: no cover - observational only
        logging.debug("PAGE_SENTINEL_EVENT_SKIPPED type=%s error=%s", event_type, exc)


def _sentinel_edge(src_type: str, src_id: Any, edge_type: str,
                   dst_type: str, dst_id: Any) -> None:
    try:
        from services.sentinel import graph as sentinel_graph
        sentinel_graph.upsert_edge(src_type, str(src_id), edge_type, dst_type, str(dst_id))
    except Exception as exc:  # pragma: no cover - observational only
        logging.debug("PAGE_SENTINEL_EDGE_SKIPPED type=%s error=%s", edge_type, exc)


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------

def check_handle(conn: Any, candidate: Any, exclude_page_id: int | None = None) -> dict:
    text = re.sub(r"^@+", "", _text(candidate, 60))
    result = {"candidate": text, "handle": text.lower(), "available": False, "reason": ""}
    if not text:
        result["reason"] = "Enter a handle."
        return result
    if not HANDLE_RE.match(text):
        result["reason"] = "Handles use 3-40 letters, numbers, dots, dashes or underscores."
        return result
    if text.lower() in RESERVED_HANDLES:
        result["reason"] = "That handle is reserved."
        return result
    cur = conn.cursor()
    if exclude_page_id:
        cur.execute("SELECT id FROM pulse_pages WHERE lower(handle)=lower(?) AND id!=? LIMIT 1",
                    (text, int(exclude_page_id)))
    else:
        cur.execute("SELECT id FROM pulse_pages WHERE lower(handle)=lower(?) LIMIT 1", (text,))
    if cur.fetchone():
        result["reason"] = "That handle is taken by another page."
        return result
    # Impersonation-aware: a page may not take an existing personal handle.
    try:
        cur.execute("SELECT user_id FROM users WHERE lower(username)=lower(?) LIMIT 1", (text,))
        if cur.fetchone():
            result["reason"] = "That handle belongs to a member account."
            return result
    except Exception:
        # Fail closed: never say "available" from a failed lookup.
        result["reason"] = "Couldn't check that handle right now. Try again."
        return result
    result["available"] = True
    result["reason"] = "Available."
    return result


# ---------------------------------------------------------------------------
# Membership + permissions
# ---------------------------------------------------------------------------

def role_for(conn: Any, user_id: int, page_id: int) -> str | None:
    cur = conn.cursor()
    cur.execute(
        "SELECT role FROM pulse_page_members WHERE page_id=? AND user_id=? AND status='active' LIMIT 1",
        (int(page_id), int(user_id)),
    )
    row = _row(cur.fetchone())
    return row.get("role") if row else None


def has_permission(role: str | None, permission: str) -> bool:
    if not role or permission not in PERMISSIONS:
        return False
    return role in PERMISSIONS[permission]


def require_permission(conn: Any, user_id: int, page_id: int, permission: str) -> str:
    role = role_for(conn, user_id, page_id)
    if not has_permission(role, permission):
        raise PageError("You don't have permission to do that on this page.", 403)
    return role


def undx_page_context(conn: Any, user_id: int, page_id: int) -> dict:
    """Role-bounded capability set for UNDX missions acting in a page context.
    An ANALYST (or any non-owner) can never obtain owner authority through
    UNDX: capabilities come from the same matrix as the API."""
    role = role_for(conn, user_id, page_id)
    if not role:
        raise PageError("Not a member of this page.", 403)
    return {
        "page_id": int(page_id),
        "user_id": int(user_id),
        "role": role,
        "capabilities": sorted(p for p, roles in PERMISSIONS.items() if role in roles),
        "can_transfer_ownership": role == "OWNER",
    }


# ---------------------------------------------------------------------------
# Page CRUD
# ---------------------------------------------------------------------------

def _load_page(conn: Any, ident: Any) -> dict:
    cur = conn.cursor()
    page = None
    page_id = _int(ident, 0)
    if page_id > 0:
        cur.execute("SELECT * FROM pulse_pages WHERE id=? LIMIT 1", (page_id,))
        page = _row(cur.fetchone())
    if not page:
        handle = re.sub(r"^@+", "", _text(ident, 60))
        if handle:
            cur.execute("SELECT * FROM pulse_pages WHERE lower(handle)=lower(?) LIMIT 1", (handle,))
            page = _row(cur.fetchone())
    if not page:
        raise PageError("Page not found.", 404)
    return page


def _counts(conn: Any, page_id: int) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM pulse_page_follows WHERE page_id=?", (int(page_id),))
    followers = _int(_row(cur.fetchone()).get("c"))
    posts = 0
    try:
        cur.execute("SELECT COUNT(*) AS c FROM pulse_posts WHERE page_id=? AND deleted_at IS NULL", (int(page_id),))
        posts = _int(_row(cur.fetchone()).get("c"))
    except Exception:
        pass  # page_id column not present yet on a legacy DB
    return {"followers": followers, "posts": posts}


def module_availability(conn: Any, page: dict, posts_count: int = 0) -> dict:
    """Which optional modules actually have something behind them.

    One cheap query over the links table; the modules themselves stay lazy.
    """
    linked = {row.get("link_type") for row in list_links(conn, page["id"])}
    available = {}
    for tab in TYPE_TABS.get(page.get("page_type") or "OTHER", TYPE_TABS["OTHER"]):
        if tab in ALWAYS_TABS:
            available[tab] = True
        elif tab in TAB_LINK_SOURCE:
            available[tab] = TAB_LINK_SOURCE[tab] in linked
        else:
            available[tab] = False
    if "posts" in available:
        available["posts"] = True
    return available


def _visible_tabs(page: dict, availability: dict, is_team: bool) -> list[str]:
    ceiling = TYPE_TABS.get(page.get("page_type") or "OTHER", TYPE_TABS["OTHER"])
    if is_team:
        return list(ceiling)
    return [tab for tab in ceiling if availability.get(tab)]


def public_view(conn: Any, page: dict, viewer_user_id: int | None = None) -> dict:
    """Public shape. Management, billing, member emails, audit and Sentinel
    context are never present here."""
    counts = _counts(conn, page["id"])
    viewer_role = role_for(conn, viewer_user_id, page["id"]) if viewer_user_id else None
    following = False
    if viewer_user_id:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pulse_page_follows WHERE page_id=? AND user_id=? LIMIT 1",
                    (int(page["id"]), int(viewer_user_id)))
        following = bool(cur.fetchone())
    hours = {}
    try:
        hours = json.loads(page.get("hours_json") or "{}")
    except Exception:
        hours = {}
    availability = module_availability(conn, page, counts["posts"])
    return {
        "id": int(page["id"]),
        "page_type": page.get("page_type"),
        "category": page.get("category") or "",
        "subcategory": page.get("subcategory") or "",
        "name": page.get("name"),
        "handle": page.get("handle"),
        "avatar_url": page.get("avatar_url") or "",
        "cover_url": page.get("cover_url") or "",
        "description": page.get("description") or "",
        "genre": page.get("genre") or "",
        "website": page.get("website") or "",
        "email": page.get("email") or "",
        "location": page.get("location") or "",
        "hours": hours,
        "status": page.get("status") or "ACTIVE",
        "verification_status": page.get("verification_status") or "unverified",
        "verified": (page.get("verification_status") or "") == "verified",
        "followers_count": counts["followers"],
        "posts_count": counts["posts"],
        "tabs": _visible_tabs(page, availability, bool(viewer_role)),
        "modules": availability,
        "created_at": page.get("created_at"),
        "viewer": {"role": viewer_role, "following": following},
    }


def create_page(conn: Any, user_id: int, payload: dict) -> dict:
    ensure_tables(conn)
    payload = payload or {}
    page_type = _text(payload.get("page_type"), 40).upper()
    if page_type not in PAGE_TYPES:
        raise PageError("Choose a valid page type.")
    name = _text(payload.get("name"), 120)
    if len(name) < 2:
        raise PageError("Enter a page name (at least 2 characters).")
    handle_check = check_handle(conn, payload.get("handle"))
    if not handle_check["available"]:
        raise PageError(handle_check["reason"] or "That handle is not available.", 409)
    if not payload.get("confirm_owner"):
        raise PageError("Confirm that you will be the owner of this page.")
    now = _now()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_pages
        (owner_user_id, page_type, category, subcategory, name, handle, avatar_url, cover_url,
         description, genre, email, phone, website, location, hours_json, status,
         verification_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'unverified', ?, ?)
        """,
        (
            int(user_id), page_type,
            _text(payload.get("category"), 80), _text(payload.get("subcategory"), 80),
            name, handle_check["candidate"],
            _text(payload.get("avatar_url"), 500), _text(payload.get("cover_url"), 500),
            _text(payload.get("description"), 1000), _text(payload.get("genre"), 80),
            _text(payload.get("email"), 200), _text(payload.get("phone"), 40),
            _text(payload.get("website"), 300), _text(payload.get("location"), 240),
            json.dumps(payload.get("hours") or {}, default=str)[:2000],
            now, now,
        ),
    )
    page_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_page_members (page_id, user_id, role, status, created_at, updated_at) "
        "VALUES (?, ?, 'OWNER', 'active', ?, ?)",
        (page_id, int(user_id), now, now),
    )
    _audit(conn, page_id, user_id, "page_created", after={"page_type": page_type, "name": name, "handle": handle_check["candidate"]})
    conn.commit()
    _sentinel_event("page.created", user_id, page_id, payload={"page_type": page_type})
    _sentinel_edge("user", int(user_id), "owns_page", "page", page_id)
    return public_view(conn, _load_page(conn, page_id), viewer_user_id=user_id)


def update_page(conn: Any, user_id: int, page_id: int, payload: dict) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, user_id, page["id"], "edit_page")
    payload = payload or {}
    fields: dict[str, Any] = {}
    for key, limit in (
        ("name", 120), ("category", 80), ("subcategory", 80), ("description", 1000),
        ("genre", 80), ("email", 200), ("phone", 40), ("website", 300),
        ("location", 240), ("avatar_url", 500), ("cover_url", 500),
    ):
        if key in payload:
            fields[key] = _text(payload.get(key), limit)
    if "hours" in payload:
        fields["hours_json"] = json.dumps(payload.get("hours") or {}, default=str)[:2000]
    if "handle" in payload:
        handle_check = check_handle(conn, payload.get("handle"), exclude_page_id=page["id"])
        current = str(page.get("handle") or "").lower()
        if handle_check["handle"] != current:
            if not handle_check["available"]:
                raise PageError(handle_check["reason"] or "That handle is not available.", 409)
            fields["handle"] = handle_check["candidate"]
    if fields.get("name") is not None and len(fields.get("name", "")) < 2:
        raise PageError("Page name must be at least 2 characters.")
    if not fields:
        return public_view(conn, page, viewer_user_id=user_id)
    before = {k: page.get(k) for k in fields}
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    cur = conn.cursor()
    cur.execute(f"UPDATE pulse_pages SET {sets} WHERE id=?", (*fields.values(), int(page["id"])))
    _audit(conn, page["id"], user_id, "page_updated", before=before, after={k: fields[k] for k in fields if k != "updated_at"})
    conn.commit()
    return public_view(conn, _load_page(conn, page["id"]), viewer_user_id=user_id)


def set_status(conn: Any, user_id: int, page_id: int, status: Any) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, user_id, page["id"], "manage_status")
    status = _text(status, 20).upper()
    if status not in PAGE_STATUSES:
        raise PageError("Choose a valid page status.")
    now = _now()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_pages SET status=?, updated_at=?, deactivated_at=? WHERE id=?",
        (status, now, now if status == "DEACTIVATED" else None, int(page["id"])),
    )
    _audit(conn, page["id"], user_id, "page_status_changed",
           before={"status": page.get("status")}, after={"status": status})
    conn.commit()
    _sentinel_event("page.status_changed", user_id, page["id"], payload={"from": page.get("status"), "to": status})
    return public_view(conn, _load_page(conn, page["id"]), viewer_user_id=user_id)


def request_verification(conn: Any, user_id: int, page_id: int, payload: dict | None = None) -> dict:
    """Distinct from personal verification and never auto-granted: this only
    moves unverified → pending. Granting stays an admin/trust operation."""
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, user_id, page["id"], "edit_page")
    current = page.get("verification_status") or "unverified"
    if current == "verified":
        raise PageError("This page is already verified.")
    if current == "pending":
        raise PageError("Verification is already under review.")
    cur = conn.cursor()
    cur.execute("UPDATE pulse_pages SET verification_status='pending', updated_at=? WHERE id=?",
                (_now(), int(page["id"])))
    _audit(conn, page["id"], user_id, "verification_requested",
           before={"verification_status": current},
           after={"verification_status": "pending", "note": _text((payload or {}).get("note"), 500)})
    conn.commit()
    _sentinel_event("page.verification_requested", user_id, page["id"])
    return {"verification_status": "pending"}


# ---------------------------------------------------------------------------
# Identity switching
# ---------------------------------------------------------------------------

def list_my_pages(conn: Any, user_id: int) -> list[dict]:
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*, m.role FROM pulse_pages p
        JOIN pulse_page_members m ON m.page_id=p.id
        WHERE m.user_id=? AND m.status='active' AND p.status!='DEACTIVATED'
        ORDER BY p.created_at ASC
        """,
        (int(user_id),),
    )
    out = []
    for raw in cur.fetchall():
        page = _row(raw)
        view = public_view(conn, page, viewer_user_id=user_id)
        view["role"] = page.get("role")
        view["can_post"] = has_permission(page.get("role"), "create_content")
        out.append(view)
    return out


def list_identities(conn: Any, user_id: int) -> dict:
    """Everything the user can act as: their personal identity plus every page
    where their role allows content creation. Drives 'Posting as <name>'."""
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, display_name, full_name, avatar_url FROM users WHERE user_id=? LIMIT 1",
                (int(user_id),))
    user = _row(cur.fetchone())
    personal = {
        "kind": "personal",
        "id": int(user_id),
        "name": user.get("display_name") or user.get("full_name") or user.get("username") or "You",
        "handle": user.get("username") or "",
        "avatar_url": user.get("avatar_url") or "",
    }
    pages = []
    for page in list_my_pages(conn, user_id):
        if not page.get("can_post"):
            continue
        pages.append({
            "kind": "page",
            "id": page["id"],
            "name": page["name"],
            "handle": page["handle"],
            "avatar_url": page.get("avatar_url") or "",
            "page_type": page.get("page_type"),
            "role": page.get("role"),
            "verified": bool(page.get("verified")),
        })
    return {"personal": personal, "pages": pages}


# ---------------------------------------------------------------------------
# Members: invites, roles, removal, ownership transfer
# ---------------------------------------------------------------------------

def list_members(conn: Any, user_id: int, page_id: int) -> list[dict]:
    page = _load_page(conn, page_id)
    require_permission(conn, user_id, page["id"], "view_analytics")
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.user_id, m.role, m.status, m.invited_by, m.invite_expires_at, m.created_at,
               u.username, u.display_name, u.full_name, u.avatar_url
        FROM pulse_page_members m LEFT JOIN users u ON u.user_id=m.user_id
        WHERE m.page_id=? AND m.status IN ('active','invited')
        ORDER BY m.created_at ASC
        """,
        (int(page["id"]),),
    )
    out = []
    for raw in cur.fetchall():
        member = _row(raw)
        out.append({
            "user_id": int(member.get("user_id") or 0),
            "name": member.get("display_name") or member.get("full_name") or member.get("username") or "Member",
            "handle": member.get("username") or "",
            "avatar_url": member.get("avatar_url") or "",
            "role": member.get("role"),
            "status": member.get("status"),
            "invited_by": member.get("invited_by"),
            "invite_expires_at": member.get("invite_expires_at"),
            "since": member.get("created_at"),
        })
    return out


def invite_member(conn: Any, actor_user_id: int, page_id: int, payload: dict) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, actor_user_id, page["id"], "manage_members")
    payload = payload or {}
    role = _text(payload.get("role"), 40).upper()
    if role not in ASSIGNABLE_ROLES:
        raise PageError("Choose a valid team role. Ownership is not assignable by invite.")
    cur = conn.cursor()
    target_id = _int(payload.get("user_id"), 0)
    if not target_id:
        handle = re.sub(r"^@+", "", _text(payload.get("handle"), 60))
        if not handle:
            raise PageError("Name the member to invite (user id or handle).")
        cur.execute("SELECT user_id FROM users WHERE lower(username)=lower(?) LIMIT 1", (handle,))
        found = _row(cur.fetchone())
        if not found:
            raise PageError("No member with that handle.", 404)
        target_id = int(found["user_id"])
    if target_id == int(actor_user_id):
        raise PageError("You are already on this page's team.")
    cur.execute("SELECT id, status, role FROM pulse_page_members WHERE page_id=? AND user_id=? LIMIT 1",
                (int(page["id"]), target_id))
    existing = _row(cur.fetchone())
    if existing and existing.get("status") == "active":
        raise PageError("That member is already on the team.", 409)
    now = _now()
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)).replace(microsecond=0).isoformat()
    if existing:
        cur.execute(
            "UPDATE pulse_page_members SET role=?, status='invited', invited_by=?, invite_token=?, "
            "invite_expires_at=?, updated_at=? WHERE id=?",
            (role, int(actor_user_id), token, expires, now, int(existing["id"])),
        )
    else:
        cur.execute(
            "INSERT INTO pulse_page_members (page_id, user_id, role, status, invited_by, invite_token, "
            "invite_expires_at, created_at, updated_at) VALUES (?, ?, ?, 'invited', ?, ?, ?, ?, ?)",
            (int(page["id"]), target_id, role, int(actor_user_id), token, expires, now, now),
        )
    _audit(conn, page["id"], actor_user_id, "member_invited",
           after={"user_id": target_id, "role": role, "expires_at": expires})
    conn.commit()
    _sentinel_event("page.member_invited", actor_user_id, page["id"],
                    payload={"target_user_id": target_id, "role": role})
    return {"user_id": target_id, "role": role, "status": "invited", "invite_token": token, "expires_at": expires}


def accept_invite(conn: Any, user_id: int, token: Any) -> dict:
    ensure_tables(conn)
    token = _text(token, 120)
    if not token:
        raise PageError("Invite token required.")
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_page_members WHERE invite_token=? AND user_id=? AND status='invited' LIMIT 1",
        (token, int(user_id)),
    )
    invite = _row(cur.fetchone())
    if not invite:
        raise PageError("Invite not found or already handled.", 404)
    expires = str(invite.get("invite_expires_at") or "")
    if expires and expires < _now():
        raise PageError("This invite has expired. Ask for a new one.", 410)
    # Acceptance NEVER grants ownership, whatever the stored role says.
    role = invite.get("role") if invite.get("role") in ASSIGNABLE_ROLES else "ANALYST"
    now = _now()
    cur.execute(
        "UPDATE pulse_page_members SET status='active', role=?, invite_token=NULL, invite_expires_at=NULL, "
        "updated_at=? WHERE id=?",
        (role, now, int(invite["id"])),
    )
    _audit(conn, int(invite["page_id"]), user_id, "invite_accepted", after={"role": role})
    conn.commit()
    _sentinel_event("page.invite_accepted", user_id, int(invite["page_id"]), payload={"role": role})
    _sentinel_edge("user", int(user_id), "admin_of", "page", int(invite["page_id"]))
    return {"page_id": int(invite["page_id"]), "role": role, "status": "active"}


def change_role(conn: Any, actor_user_id: int, page_id: int, member_user_id: int, new_role: Any) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, actor_user_id, page["id"], "manage_members")
    new_role = _text(new_role, 40).upper()
    if new_role not in ASSIGNABLE_ROLES:
        raise PageError("Ownership moves only through an explicit transfer.")
    current = role_for(conn, member_user_id, page["id"])
    if not current:
        raise PageError("That member is not on this page.", 404)
    if current == "OWNER":
        raise PageError("The owner's role can't be changed here. Use ownership transfer.", 403)
    cur = conn.cursor()
    cur.execute("UPDATE pulse_page_members SET role=?, updated_at=? WHERE page_id=? AND user_id=?",
                (new_role, _now(), int(page["id"]), int(member_user_id)))
    _audit(conn, page["id"], actor_user_id, "member_role_changed",
           before={"user_id": int(member_user_id), "role": current},
           after={"user_id": int(member_user_id), "role": new_role})
    conn.commit()
    _sentinel_event("page.member_role_changed", actor_user_id, page["id"],
                    payload={"target_user_id": int(member_user_id), "from": current, "to": new_role})
    return {"user_id": int(member_user_id), "role": new_role}


def remove_member(conn: Any, actor_user_id: int, page_id: int, member_user_id: int) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, actor_user_id, page["id"], "manage_members")
    current = role_for(conn, member_user_id, page["id"])
    if not current:
        raise PageError("That member is not on this page.", 404)
    if current == "OWNER":
        raise PageError("The owner can't be removed. Transfer ownership first.", 403)
    cur = conn.cursor()
    cur.execute("UPDATE pulse_page_members SET status='removed', updated_at=? WHERE page_id=? AND user_id=?",
                (_now(), int(page["id"]), int(member_user_id)))
    _audit(conn, page["id"], actor_user_id, "member_removed",
           before={"user_id": int(member_user_id), "role": current})
    conn.commit()
    _sentinel_event("page.member_removed", actor_user_id, page["id"],
                    payload={"target_user_id": int(member_user_id)})
    return {"user_id": int(member_user_id), "status": "removed"}


def transfer_ownership(conn: Any, actor_user_id: int, page_id: int,
                       new_owner_user_id: int, confirm: Any) -> dict:
    """Owner-only, explicitly confirmed, fully audited. There is deliberately
    no client-side path to owner: this is the only door."""
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    require_permission(conn, actor_user_id, page["id"], "transfer_ownership")
    if _text(confirm, 40).upper() != TRANSFER_CONFIRM_PHRASE:
        raise PageError(f'Type "{TRANSFER_CONFIRM_PHRASE}" to confirm the ownership transfer.')
    new_owner_user_id = _int(new_owner_user_id, 0)
    if new_owner_user_id == int(actor_user_id):
        raise PageError("You already own this page.")
    target_role = role_for(conn, new_owner_user_id, page["id"])
    if not target_role:
        raise PageError("The new owner must already be an active team member.", 400)
    now = _now()
    cur = conn.cursor()
    cur.execute("UPDATE pulse_pages SET owner_user_id=?, updated_at=? WHERE id=?",
                (new_owner_user_id, now, int(page["id"])))
    cur.execute("UPDATE pulse_page_members SET role='OWNER', updated_at=? WHERE page_id=? AND user_id=?",
                (now, int(page["id"]), new_owner_user_id))
    cur.execute("UPDATE pulse_page_members SET role='ADMIN', updated_at=? WHERE page_id=? AND user_id=?",
                (now, int(page["id"]), int(actor_user_id)))
    _audit(conn, page["id"], actor_user_id, "ownership_transferred",
           before={"owner_user_id": int(page.get("owner_user_id") or 0)},
           after={"owner_user_id": new_owner_user_id})
    conn.commit()
    _sentinel_event("page.ownership_transferred", actor_user_id, page["id"], severity="medium",
                    category="SECURITY",
                    payload={"from_user_id": int(actor_user_id), "to_user_id": new_owner_user_id})
    _sentinel_edge("user", new_owner_user_id, "owns_page", "page", int(page["id"]))
    return {"page_id": int(page["id"]), "owner_user_id": new_owner_user_id, "previous_owner_role": "ADMIN"}


# ---------------------------------------------------------------------------
# Follows
# ---------------------------------------------------------------------------

def toggle_follow(conn: Any, user_id: int, page_id: int) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    if page.get("status") not in ("ACTIVE", "PAUSED"):
        raise PageError("This page isn't accepting followers right now.", 403)
    cur = conn.cursor()
    cur.execute("SELECT id FROM pulse_page_follows WHERE page_id=? AND user_id=? LIMIT 1",
                (int(page["id"]), int(user_id)))
    existing = _row(cur.fetchone())
    if existing:
        cur.execute("DELETE FROM pulse_page_follows WHERE id=?", (int(existing["id"]),))
        following = False
    else:
        cur.execute("INSERT INTO pulse_page_follows (page_id, user_id, created_at) VALUES (?, ?, ?)",
                    (int(page["id"]), int(user_id), _now()))
        following = True
    conn.commit()
    return {"page_id": int(page["id"]), "following": following,
            "followers_count": _counts(conn, page["id"])["followers"]}


# ---------------------------------------------------------------------------
# Links to canonical systems (store / ads / community / event)
# ---------------------------------------------------------------------------

def set_link(conn: Any, actor_user_id: int, page_id: int, link_type: Any, ref_id: Any) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    link_type = _text(link_type, 40).lower()
    if link_type not in LINK_TYPES:
        raise PageError("Choose a valid link type.")
    perm = {"store": "manage_marketplace", "ad_account": "manage_ads"}.get(link_type, "manage_links")
    require_permission(conn, actor_user_id, page["id"], perm)
    ref = _text(ref_id, 80)
    if not ref:
        raise PageError("A reference id is required.")
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO pulse_page_links (page_id, link_type, ref_id, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (int(page["id"]), link_type, ref, int(actor_user_id), _now()),
    )
    _audit(conn, page["id"], actor_user_id, "link_set", after={"link_type": link_type, "ref_id": ref})
    conn.commit()
    if link_type == "store":
        _sentinel_edge("page", int(page["id"]), "owns_account", "seller", ref)
    return {"page_id": int(page["id"]), "link_type": link_type, "ref_id": ref}


def list_links(conn: Any, page_id: int, link_type: str | None = None) -> list[dict]:
    ensure_tables(conn)
    cur = conn.cursor()
    if link_type:
        cur.execute("SELECT link_type, ref_id, created_at FROM pulse_page_links WHERE page_id=? AND link_type=?",
                    (int(page_id), link_type))
    else:
        cur.execute("SELECT link_type, ref_id, created_at FROM pulse_page_links WHERE page_id=?", (int(page_id),))
    return [_row(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Content: page posts through the canonical content system
# ---------------------------------------------------------------------------

def create_page_post(conn: Any, user_id: int, page_id: int, payload: dict) -> dict:
    """Page posts are pulse_posts rows with page_id set — the ONE content
    system, the same feed, no ranking change. Attribution is resolved at
    serialization time from the page row."""
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    if page.get("status") != "ACTIVE":
        raise PageError("Only active pages can publish.", 403)
    require_permission(conn, user_id, page["id"], "create_content")
    payload = payload or {}
    from services import pulse_feed_engine
    result = pulse_feed_engine.create_post(
        int(user_id),
        body=payload.get("body") or "",
        post_type=payload.get("post_type") or "text",
        title=payload.get("title") or "",
        tags=payload.get("tags") or [],
        visibility=payload.get("visibility") or "public",
        media_ids=payload.get("media_ids") or [],
        page_id=int(page["id"]),
    )
    if result.get("ok") and result.get("post_id"):
        _audit(conn, page["id"], user_id, "page_post_created", after={"post_id": result["post_id"]})
        conn.commit()
    return result


def list_page_posts(conn: Any, page_id: int, viewer_user_id: int | None = None,
                    limit: int = 20, offset: int = 0) -> dict:
    page = _load_page(conn, page_id)
    limit = max(1, min(_int(limit, 20), 40))
    offset = max(0, _int(offset, 0))
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM pulse_posts WHERE page_id=? AND deleted_at IS NULL "
            "AND COALESCE(visibility,'public')='public' AND COALESCE(moderation_status,'approved')!='blocked' "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (int(page["id"]), limit + 1, offset),
        )
        ids = [int(_row(r)["id"]) for r in cur.fetchall()]
    except Exception:
        ids = []
    has_more = len(ids) > limit
    ids = ids[:limit]
    posts = []
    try:
        from services import pulse_feed_engine
        for post_id in ids:
            post = pulse_feed_engine.get_post(post_id, viewer_user_id=viewer_user_id)
            if post:
                posts.append(post)
    except Exception as exc:
        logging.warning("PAGE_POSTS_SERIALIZE_FAILED page_id=%s error=%s", page_id, exc)
    return {"posts": posts, "has_more": has_more, "next_offset": offset + len(ids)}


# ---------------------------------------------------------------------------
# Lazy modules: resolved from the canonical systems, never mirrored here
# ---------------------------------------------------------------------------

def page_music(conn: Any, page_id: int, limit: int = 24) -> dict:
    """Tracks for an artist presence, straight from the canonical catalogue.

    The presence stores only a pointer (the `music_artist` link); the records
    stay in music_service. With no link there is no catalogue identity to read,
    so the module is empty rather than guessing from the page name.
    """
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    links = [row.get("ref_id") for row in list_links(conn, page["id"], "music_artist")]
    artist = _text(links[0], 120) if links else ""
    if not artist:
        return {"page_id": int(page["id"]), "artist": "", "tracks": [], "linked": False}
    tracks = []
    try:
        from services import music_service
        wanted = artist.casefold()
        tracks = [
            t for t in music_service.search_tracks(artist, limit=max(1, min(_int(limit, 24), 50)))
            if str(t.get("artist") or "").casefold() == wanted
        ]
    except Exception as exc:
        logging.warning("PAGE_MUSIC_FAILED page_id=%s error=%s", page_id, exc)
        raise PageError("We couldn't load this section.", 503)
    return {"page_id": int(page["id"]), "artist": artist, "tracks": tracks, "linked": True}


def admin_overview(conn: Any, page_id: int) -> dict:
    """Read-only inspection for platform admins.

    Authorisation belongs to the caller (the existing admin gate); this only
    assembles what an admin is allowed to see, and takes no action.
    """
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, role, status, created_at FROM pulse_page_members "
        "WHERE page_id=? ORDER BY created_at", (int(page["id"]),))
    members = [_row(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT actor_user_id, action, created_at FROM pulse_page_audit "
        "WHERE page_id=? ORDER BY id DESC LIMIT 20", (int(page["id"]),))
    recent_audit = [_row(r) for r in cur.fetchall()]
    counts = _counts(conn, page["id"])
    return {
        "id": int(page["id"]),
        "page_type": page.get("page_type"),
        "name": page.get("name"),
        "handle": page.get("handle"),
        "status": page.get("status"),
        "verification_status": page.get("verification_status"),
        "owner_user_id": int(page.get("owner_user_id") or 0),
        "created_at": page.get("created_at"),
        "followers": counts["followers"],
        "posts": counts["posts"],
        "members": members,
        "links": list_links(conn, page["id"]),
        "recent_audit": recent_audit,
    }


# ---------------------------------------------------------------------------
# Search + analytics (real numbers only)
# ---------------------------------------------------------------------------

def search_pages(conn: Any, query: Any, limit: int = 20, include_inactive: bool = False) -> list[dict]:
    """Public search sees ACTIVE pages only. `include_inactive` exists for the
    admin console, whose whole job is to find a paused or deactivated page."""
    ensure_tables(conn)
    q = _text(query, 80)
    if not q:
        return []
    like = f"%{q}%"
    where = "(name LIKE ? OR handle LIKE ? OR category LIKE ?)"
    if not include_inactive:
        where = "status='ACTIVE' AND " + where
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM pulse_pages WHERE {where} ORDER BY created_at DESC LIMIT ?",
        (like, like, like, max(1, min(_int(limit, 20), 50))),
    )
    return [public_view(conn, _row(r)) for r in cur.fetchall()]


def page_analytics(conn: Any, user_id: int, page_id: int) -> dict:
    """Only numbers with an authoritative source. Anything unmeasured is
    absent, never invented."""
    page = _load_page(conn, page_id)
    require_permission(conn, user_id, page["id"], "view_analytics")
    counts = _counts(conn, page["id"])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM pulse_page_members WHERE page_id=? AND status='active'",
                (int(page["id"]),))
    team = _int(_row(cur.fetchone()).get("c"))
    return {
        "page_id": int(page["id"]),
        "followers": counts["followers"],
        "posts": counts["posts"],
        "team_members": team,
        "note": "Only measured metrics are reported. Reach and engagement appear once their sources are wired.",
    }


def manage_view(conn: Any, user_id: int, page_id: int) -> dict:
    page = _load_page(conn, page_id)
    role = require_permission(conn, user_id, page["id"], "view_analytics")
    view = public_view(conn, page, viewer_user_id=user_id)
    view.update({
        "role": role,
        "capabilities": sorted(p for p, roles in PERMISSIONS.items() if role in roles),
        "owner_user_id": int(page.get("owner_user_id") or 0),
        "phone": page.get("phone") or "",
        "links": list_links(conn, page["id"]),
        "members": list_members(conn, user_id, page["id"]) if has_permission(role, "manage_members") else [],
        "analytics": page_analytics(conn, user_id, page["id"]),
    })
    return view
