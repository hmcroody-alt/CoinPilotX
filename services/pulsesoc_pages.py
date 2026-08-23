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
# with real backing data render for a visitor; this is the ceiling, not a
# promise.
#
# Every value here must be in RENDERABLE_TABS. A tab this list names and no
# client screen draws is a button that opens onto nothing — which is worse than
# the missing feature it stands in for, because it looks like a bug rather than
# an absence. `services` used to be exactly that for BUSINESS,
# PROFESSIONAL_SERVICE and LOCAL_BUSINESS: no link source, no rows, no renderer.
# Those types get `shop` instead — Marketplace already carries `service` and
# `booking` listing types, so the catalogue exists and a second one would be a
# second commerce backend to keep in sync.
TYPE_TABS = {
    "ARTIST": ["posts", "music", "videos", "merch", "about"],
    "CREATOR": ["posts", "videos", "merch", "about"],
    "PUBLIC_FIGURE": ["posts", "videos", "about"],
    "BUSINESS": ["home", "shop", "about"],
    "BRAND": ["home", "shop", "about"],
    "STORE": ["home", "shop", "about"],
    "RESTAURANT": ["home", "menu", "about"],
    "PROFESSIONAL_SERVICE": ["home", "shop", "about"],
    "LOCAL_BUSINESS": ["home", "shop", "about"],
    "NONPROFIT": ["home", "about"],
    "ORGANIZATION": ["home", "about"],
    "MEDIA": ["posts", "videos", "about"],
    "SPORTS_TEAM": ["posts", "shop", "about"],
    "VENUE": ["home", "about"],
    "EDUCATION": ["home", "about"],
    "OTHER": ["posts", "about"],
}

# Page types that are an organisation rather than a person or an act. They are
# the ones whose operations continue into Business OS, and the ones whose setup
# checklist asks for a category, hours and a location. Both questions have the
# same answer, so there is one list: a second copy is a second thing to forget
# when a page type is added.
BUSINESS_PAGE_TYPES = frozenset({
    "BUSINESS", "BRAND", "STORE", "RESTAURANT", "PROFESSIONAL_SERVICE",
    "LOCAL_BUSINESS", "NONPROFIT", "ORGANIZATION", "MEDIA", "VENUE", "EDUCATION",
})

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

# Every tab PageScreen has a branch for. This is a contract with the client,
# restated here because the server is what decides which tabs a page offers and
# it therefore needs to know which ones mean anything.
#
# The point of naming it is `module_availability`: with this set closed, every
# tab has an availability rule, so "a tab nobody can render" stops being a thing
# that can be typed into TYPE_TABS and quietly shipped. Adding a tab to the
# ceiling and teaching a screen to draw it become the same change.
RENDERABLE_TABS = frozenset(ALWAYS_TABS | {"music", "videos", "shop", "merch", "menu"})

# Optional tab -> the link_type that gives it real content. A tab with no link
# and no rows is hidden from the public and kept (as a setup prompt) for the
# team, because an empty module reads as a dead button.
TAB_LINK_SOURCE = {
    "music": "music_artist",
    "shop": "store",
    "merch": "store",
    "menu": "store",
}
# `events` is deliberately absent, and `event` links are now refused outright
# rather than stored: the canonical events backend
# (services/business_os/events) keys on `business_id` and lists only for a
# caller holding a manager role, so there is no public read to point a tab at
# and no way to say whose event a ref names. A tab that 403s for every visitor
# is worse than no tab. Both come back together, or neither does.

# Post types the videos module counts as a video. Same set pulse_feed_engine
# uses for its video surfaces; the tab reads the page's own posts, so it needs
# no link.
VIDEO_POST_TYPES = ("video", "replay", "roast_clip")

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
    videos = 0
    try:
        cur.execute("SELECT COUNT(*) AS c FROM pulse_posts WHERE page_id=? AND deleted_at IS NULL", (int(page_id),))
        posts = _int(_row(cur.fetchone()).get("c"))
        marks = ",".join("?" for _ in VIDEO_POST_TYPES)
        cur.execute(
            f"SELECT COUNT(*) AS c FROM pulse_posts WHERE page_id=? AND deleted_at IS NULL "
            f"AND post_type IN ({marks})",
            (int(page_id), *VIDEO_POST_TYPES),
        )
        videos = _int(_row(cur.fetchone()).get("c"))
    except Exception:
        pass  # page_id column not present yet on a legacy DB
    return {"followers": followers, "posts": posts, "videos": videos}


def module_availability(conn: Any, page: dict, counts: dict | None = None,
                        links: list[dict] | None = None) -> dict:
    """Which optional modules actually have something behind them.

    Reads nothing the caller already holds: `public_view` has both the counts
    and the links by the time it asks. The modules themselves stay lazy.
    """
    counts = _counts(conn, page["id"]) if counts is None else counts
    rows = list_links(conn, page["id"]) if links is None else links
    linked = {row.get("link_type") for row in rows}
    available = {}
    for tab in TYPE_TABS.get(page.get("page_type") or "OTHER", TYPE_TABS["OTHER"]):
        if tab in ALWAYS_TABS:
            available[tab] = True
        elif tab in TAB_LINK_SOURCE:
            available[tab] = TAB_LINK_SOURCE[tab] in linked
        elif tab == "videos":
            available[tab] = counts.get("videos", 0) > 0
        else:
            # Unreachable while RENDERABLE_TABS and the branches above agree,
            # and that is the whole design: the previous version of this line
            # was `available[tab] = False`, which turned "nobody taught the
            # server what backs this tab" into a tab that is merely hidden from
            # visitors — and still shown to the team, who then tapped it and got
            # a blank screen. `services` lived there for months. Failing here
            # means a new tab cannot reach production without a rule.
            raise PageError(
                f"tab '{tab}' has no availability rule; add one before offering it",
                500)
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
    links = list_links(conn, page["id"])
    availability = module_availability(conn, page, counts, links)
    shop_seller_id = next(
        (_int(row.get("ref_id")) for row in links if row.get("link_type") == "store"), 0)
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
        "videos_count": counts["videos"],
        "tabs": _visible_tabs(page, availability, bool(viewer_role)),
        "modules": availability,
        "shop_seller_id": shop_seller_id,
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
    try:
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
    except Exception as exc:
        # A double-tap or a concurrent create can pass check_handle and still
        # lose the race at the unique handle index. That is a normal conflict,
        # not a server failure — answer it like one.
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise PageError("That handle is already in use.", 409)
        raise
    # Never trust `lastrowid` alone: on Postgres it is only populated when the
    # table is registered in services.db.AUTO_PK_TABLES (which pulse_pages now
    # is). If it is ever None/0 again, recover the id through the unique handle
    # index instead of crashing the whole creation with `int(None)`.
    page_id = int(cur.lastrowid or 0)
    if not page_id:
        cur.execute("SELECT id FROM pulse_pages WHERE lower(handle) = ?", (handle_check["candidate"].lower(),))
        row = cur.fetchone()
        if not row:
            raise PageError("Your presence could not be created. Please try again.", 500)
        page_id = int(row["id"])
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


def team_view(conn: Any, user_id: int, page_id: int) -> dict:
    """The roster plus what *this* caller may do to it.

    `list_members` answers "who is on the team". It does not answer "may I
    change any of this", and a client that infers the answer from the role
    name re-implements the permission matrix in a second place, where it
    drifts. Every flag below is derived from the same `PERMISSIONS` table the
    mutating calls check, so a control the client renders is a call the server
    will accept — and one it withholds is one that would have 403'd.

    The per-member flags mirror `change_role` and `remove_member` exactly: the
    owner's seat is untouchable here and moves only through
    `transfer_ownership`. `assignable_roles` and `transfer_confirm_phrase` are
    sent rather than hardcoded client-side for the same reason: they are
    server facts, and a stale copy of either is a control that always fails.
    """
    page = _load_page(conn, page_id)
    role = require_permission(conn, user_id, page["id"], "view_analytics")
    can_manage = has_permission(role, "manage_members")
    can_transfer = has_permission(role, "transfer_ownership")
    members = []
    for member in list_members(conn, user_id, page["id"]):
        is_owner = member.get("role") == "OWNER"
        is_you = _int(member.get("user_id"), 0) == _int(user_id, 0)
        is_active = member.get("status") == "active"
        members.append({
            **member,
            "is_owner": is_owner,
            "is_you": is_you,
            "can_change_role": bool(can_manage and not is_owner),
            "can_remove": bool(can_manage and not is_owner),
            # transfer_ownership refuses a target who is not already an active
            # member, so an invited member is not yet a candidate.
            "can_receive_ownership": bool(can_transfer and not is_owner and is_active),
        })
    return {
        "page_id": int(page["id"]),
        "role": role,
        "owner_user_id": _int(page.get("owner_user_id"), 0),
        "can_manage_members": can_manage,
        "can_transfer_ownership": can_transfer,
        "assignable_roles": list(ASSIGNABLE_ROLES),
        "transfer_confirm_phrase": TRANSFER_CONFIRM_PHRASE,
        "members": members,
    }


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


def list_my_invites(conn: Any, user_id: int) -> list[dict]:
    """The invites waiting on *you*, with the token needed to act on them.

    `invite_member` returns the token to the inviter and to nobody else, and
    nothing is sent to the invitee — so before this existed the only way onto a
    team was for the inviter to copy the token out of an API response and hand
    it over by hand. That is precisely the shared-credential habit the role
    system replaces, reintroduced one layer up.

    Returning a user their own token grants nothing new: `accept_invite` and
    `decline_invite` both match on `invite_token AND user_id`, so a token is
    only usable by the person it was already issued to.

    Expired invites are listed with `expired: true` rather than filtered out.
    Hiding them makes the screen read as though the invite was never sent, and
    the invitee is then left with nothing to say to the person who sent it;
    shown, they can ask for a fresh one.
    """
    ensure_tables(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.invite_token, m.role, m.invite_expires_at, m.created_at,
               p.id AS page_id, p.name AS page_name, p.handle AS page_handle,
               p.avatar_url AS page_avatar_url, p.page_type, p.status AS page_status,
               u.username AS inviter_username, u.display_name AS inviter_display_name,
               u.full_name AS inviter_full_name
        FROM pulse_page_members m
        JOIN pulse_pages p ON p.id = m.page_id
        LEFT JOIN users u ON u.user_id = m.invited_by
        WHERE m.user_id=? AND m.status='invited' AND m.invite_token IS NOT NULL
        ORDER BY m.created_at DESC
        """,
        (int(user_id),),
    )
    now = _now()
    out = []
    for raw in cur.fetchall():
        invite = _row(raw)
        # A deactivated page has no team to join; accepting would hand someone a
        # role on something they cannot see.
        if str(invite.get("page_status") or "").upper() == "DEACTIVATED":
            continue
        expires = str(invite.get("invite_expires_at") or "")
        out.append({
            "token": invite.get("invite_token") or "",
            "role": invite.get("role"),
            "expires_at": expires,
            "expired": bool(expires and expires < now),
            "invited_at": invite.get("created_at"),
            "page_id": int(invite.get("page_id") or 0),
            "page_name": invite.get("page_name") or "",
            "page_handle": invite.get("page_handle") or "",
            "page_avatar_url": invite.get("page_avatar_url") or "",
            "page_type": invite.get("page_type") or "",
            "invited_by_name": (invite.get("inviter_display_name")
                                or invite.get("inviter_full_name")
                                or invite.get("inviter_username")
                                or ""),
        })
    return out


def decline_invite(conn: Any, user_id: int, token: Any) -> dict:
    """Refuse an invite. Scoped exactly like `accept_invite`.

    Without this the invitee's only exit is to accept and hope someone with
    `manage_members` removes them later — clearing the invite is itself a
    management action they do not have. Declining is the one thing about an
    invite that is unambiguously the invitee's call, so it is gated on holding
    the token, not on a page permission.
    """
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
    cur.execute(
        "UPDATE pulse_page_members SET status='declined', invite_token=NULL, invite_expires_at=NULL, "
        "updated_at=? WHERE id=?",
        (_now(), int(invite["id"])),
    )
    _audit(conn, int(invite["page_id"]), user_id, "invite_declined", after={"role": invite.get("role")})
    conn.commit()
    _sentinel_event("page.invite_declined", user_id, int(invite["page_id"]),
                    payload={"role": invite.get("role")})
    return {"page_id": int(invite["page_id"]), "status": "declined"}


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

# How to find out who a referenced resource belongs to, per link type. A link
# points the page at something owned elsewhere, so permission on the PAGE is
# only half the question — the other half is whether the actor is entitled to
# that resource at all.
#
# Each entry is (table, id column, owner column, extra WHERE). `music_artist`
# is keyed on the artist name rather than a row id, because that is what the
# music catalogue is searched by; the entitlement is then "you publish approved
# tracks under this name", which is the only checkable form the claim has.
_LINK_OWNER_SOURCES = {
    "store": ("marketplace_sellers", "id", "user_id", ""),
    "ad_account": ("advertisers", "id", "owner_user_id", ""),
    "community": ("pulse_groups", "id", "owner_user_id", ""),
    "music_artist": (
        "pulse_audio_tracks", "LOWER(artist)", "uploader_user_id",
        " AND COALESCE(approved_by_admin,0)=1 AND COALESCE(active,1)=1",
    ),
}


def _link_ref_owners(conn: Any, link_type: str, ref: str) -> set[int] | None:
    """User ids entitled to attach `ref` as `link_type`.

    Returns an empty set when the resource exists but belongs to nobody
    reachable, and None when the lookup itself could not be performed. Both are
    treated as "refuse" by the caller — a link that cannot be verified is never
    granted, because the failure mode of guessing here is another member's
    storefront rendering under someone else's page.
    """
    source = _LINK_OWNER_SOURCES.get(link_type)
    if not source:
        return None
    table, id_column, owner_column, extra = source
    key = ref.casefold() if id_column.startswith("LOWER(") else _int(ref, -1)
    if key == -1:
        return set()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT DISTINCT {owner_column} FROM {table} WHERE {id_column}=?{extra}",  # noqa: S608 - fixed table map
            (key,),
        )
        return {_int(row[0], 0) for row in cur.fetchall() if _int(row[0], 0)}
    except Exception as exc:
        # Fail closed: a missing table or a failed query is "cannot verify",
        # never "allowed".
        logging.warning("PAGE_LINK_OWNER_LOOKUP_FAILED type=%s error=%s", link_type, exc)
        return None


# The same tables read the other way round: not "who owns this ref" but "what
# does this person hold that could be connected". The label column is whatever
# a member would recognise the thing by — never an internal id, which is what
# the client would otherwise have to ask them to type.
_LINK_CANDIDATE_SOURCES = {
    "store": ("marketplace_sellers", "id", "user_id", "display_name", ""),
    "ad_account": ("advertisers", "id", "owner_user_id", "advertiser_name", ""),
    "community": ("pulse_groups", "id", "owner_user_id", "name", ""),
    "music_artist": (
        "pulse_audio_tracks", "artist", "uploader_user_id", "artist",
        " AND COALESCE(approved_by_admin,0)=1 AND COALESCE(active,1)=1",
    ),
}

LINK_LABELS = {
    "store": "Shop",
    "ad_account": "Ad account",
    "community": "Community",
    "music_artist": "Music catalogue",
}

# One map, read by both the check and the offer. Kept out of `set_link` so the
# permission a member is told they need cannot drift from the one enforced.
_LINK_PERMISSION = {"store": "manage_marketplace", "ad_account": "manage_ads"}


def _link_permission(link_type: str) -> str:
    return _LINK_PERMISSION.get(link_type, "manage_links")


def _link_candidates(conn: Any, link_type: str, holder_user_ids: set[int]) -> list[dict]:
    """Refs of `link_type` held by any of `holder_user_ids`, with labels.

    A lookup failure yields an empty list, not an error: being unable to offer
    a choice is a smaller problem than a management screen that will not open,
    and nothing here grants anything — `set_link` re-derives entitlement.
    """
    source = _LINK_CANDIDATE_SOURCES.get(link_type)
    holders = sorted({_int(uid, 0) for uid in holder_user_ids if _int(uid, 0)})
    if not source or not holders:
        return []
    table, ref_column, owner_column, label_column, extra = source
    placeholders = ",".join("?" * len(holders))
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT DISTINCT {ref_column} AS ref, {label_column} AS label "  # noqa: S608 - fixed table map
            f"FROM {table} WHERE {owner_column} IN ({placeholders}){extra}",
            holders,
        )
        rows = cur.fetchall()
    except Exception as exc:
        logging.warning("PAGE_LINK_CANDIDATES_FAILED type=%s error=%s", link_type, exc)
        return []
    out = []
    for row in rows:
        record = _row(row)
        ref = _text(record.get("ref"), 80)
        if not ref:
            continue
        out.append({"ref_id": ref, "label": _text(record.get("label"), 120) or ref})
    return sorted(out, key=lambda item: item["label"].casefold())


def link_options(conn: Any, actor_user_id: int, page_id: int) -> dict:
    """What this presence is connected to, and what it could be connected to.

    Without this the only way to attach a shop is to know its internal id and
    type it in, which is both unusable and the shape that made the missing
    ownership check so easy to exploit. Offering only real, held resources
    means the ordinary path never involves a member handling an id at all.

    Convenience, not authority: every ref returned here is re-checked by
    `set_link`, so a stale or hand-crafted option buys nothing.
    """
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    role = require_permission(conn, actor_user_id, page["id"], "view_analytics")
    connected = {}
    for row in list_links(conn, page["id"]):
        connected.setdefault(_text(row.get("link_type"), 40), _text(row.get("ref_id"), 80))
    # Actor-or-owner, matching `set_link` exactly. Offering the owner's
    # resources to a delegated manager is what lets that manager finish the job
    # they were given the seat for.
    holders = {_int(actor_user_id, 0), _int(page.get("owner_user_id"), 0)}
    links = []
    for link_type in LINK_TYPES:
        # A type with no resolver cannot be offered, for the same reason it
        # cannot be set: nothing can say whose it is.
        if link_type not in _LINK_CANDIDATE_SOURCES:
            continue
        permission = _link_permission(link_type)
        can_manage = has_permission(role, permission)
        links.append({
            "link_type": link_type,
            "label": LINK_LABELS.get(link_type, link_type),
            "permission": permission,
            "can_manage": can_manage,
            "connected_ref_id": connected.get(link_type, ""),
            # Withheld rather than filtered client-side: a role that cannot
            # connect anything has no reason to receive a list of what the
            # owner holds.
            "options": _link_candidates(conn, link_type, holders) if can_manage else [],
        })
    return {"page_id": int(page["id"]), "role": role, "links": links}


def set_link(conn: Any, actor_user_id: int, page_id: int, link_type: Any, ref_id: Any) -> dict:
    ensure_tables(conn)
    page = _load_page(conn, page_id)
    link_type = _text(link_type, 40).lower()
    if link_type not in LINK_TYPES:
        raise PageError("Choose a valid link type.")
    perm = _link_permission(link_type)
    require_permission(conn, actor_user_id, page["id"], perm)
    ref = _text(ref_id, 80)
    if not ref:
        raise PageError("A reference id is required.")
    # Permission on the page is not permission over what the page is pointed
    # at. Without this, anyone able to edit any page could hang another
    # member's storefront, ad account or community under their own presence —
    # and for `store` the id lands in the PUBLIC view and raises a shop tab.
    entitled = _link_ref_owners(conn, link_type, ref)
    if entitled is None:
        raise PageError("That kind of link can't be connected here yet.", 400)
    if not entitled & {_int(actor_user_id, 0), _int(page.get("owner_user_id"), 0)}:
        raise PageError("You can only connect something you own.", 403)
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
                    limit: int = 20, offset: int = 0,
                    post_types: tuple[str, ...] | None = None) -> dict:
    page = _load_page(conn, page_id)
    limit = max(1, min(_int(limit, 20), 40))
    offset = max(0, _int(offset, 0))
    type_sql = ""
    type_params: tuple = ()
    if post_types:
        type_sql = " AND post_type IN (%s)" % ",".join("?" for _ in post_types)
        type_params = tuple(post_types)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM pulse_posts WHERE page_id=? AND deleted_at IS NULL "
            "AND COALESCE(visibility,'public')='public' AND COALESCE(moderation_status,'approved')!='blocked' "
            + type_sql +
            " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (int(page["id"]), *type_params, limit + 1, offset),
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


def _count_since(cur: Any, sql: str, page_id: int, days: int) -> int:
    """Count rows created inside the window. Timestamps are stored as ISO-8601
    text, so a lexical >= comparison against a computed cutoff is portable
    across SQLite and Postgres-with-text columns."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        cur.execute(sql, (int(page_id), cutoff))
        return _int(_row(cur.fetchone()).get("c"))
    except Exception:
        return 0


def page_analytics(conn: Any, user_id: int, page_id: int) -> dict:
    """Only numbers with an authoritative source. Anything unmeasured is
    absent, never invented. Growth windows are measured directly from the
    follow/post timestamps — no estimation."""
    page = _load_page(conn, page_id)
    require_permission(conn, user_id, page["id"], "view_analytics")
    counts = _counts(conn, page["id"])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM pulse_page_members WHERE page_id=? AND status='active'",
                (int(page["id"]),))
    team = _int(_row(cur.fetchone()).get("c"))
    followers_7d = _count_since(
        cur, "SELECT COUNT(*) AS c FROM pulse_page_follows WHERE page_id=? AND created_at>=?", page["id"], 7)
    followers_30d = _count_since(
        cur, "SELECT COUNT(*) AS c FROM pulse_page_follows WHERE page_id=? AND created_at>=?", page["id"], 30)
    posts_30d = _count_since(
        cur, "SELECT COUNT(*) AS c FROM pulse_posts WHERE page_id=? AND deleted_at IS NULL AND created_at>=?",
        page["id"], 30)
    return {
        "page_id": int(page["id"]),
        "followers": counts["followers"],
        "followers_7d": followers_7d,
        "followers_30d": followers_30d,
        "posts": counts["posts"],
        "posts_30d": posts_30d,
        "team_members": team,
        "note": "Only measured metrics are reported. Reach and engagement appear once their sources are wired.",
    }


# Completion guidance is management-only: the checklist derives strictly from
# fields that actually exist on the page row plus the real post count. It is
# never included in public_view — visitors never see setup warnings.
_BUSINESS_COMPLETENESS_TYPES = BUSINESS_PAGE_TYPES
_ARTIST_COMPLETENESS_TYPES = {"ARTIST", "CREATOR", "PUBLIC_FIGURE", "SPORTS_TEAM"}


def page_completeness(conn: Any, page: dict) -> dict:
    counts = _counts(conn, page["id"])
    page_type = page.get("page_type") or ""
    hours = {}
    try:
        hours = json.loads(page.get("hours_json") or "{}")
    except Exception:
        hours = {}
    items = [
        {"key": "avatar", "label": "Add a profile image", "done": bool(page.get("avatar_url"))},
        {"key": "cover", "label": "Add a cover image", "done": bool(page.get("cover_url"))},
        {"key": "description", "label": "Write a description", "done": bool((page.get("description") or "").strip())},
        {"key": "contact", "label": "Add a website or contact",
         "done": bool(page.get("website") or page.get("email") or page.get("phone"))},
        {"key": "first_post", "label": "Publish your first post", "done": counts["posts"] > 0},
    ]
    if page_type in _ARTIST_COMPLETENESS_TYPES:
        items.insert(3, {"key": "genre", "label": "Add your genres", "done": bool((page.get("genre") or "").strip())})
    if page_type in _BUSINESS_COMPLETENESS_TYPES:
        items.insert(3, {"key": "category", "label": "Set your category", "done": bool((page.get("category") or "").strip())})
        items.append({"key": "hours", "label": "Add business hours", "done": bool(hours)})
        items.append({"key": "location", "label": "Add your location", "done": bool((page.get("location") or "").strip())})
    done = sum(1 for i in items if i["done"])
    return {"percent": int(round(100 * done / len(items))) if items else 0, "items": items}


# Tabs that mean "this page type sells something", and what that section is
# called for each. A restaurant manages a menu, an artist manages merch, a
# store manages a shop — one section, three honest names, all of them the same
# `store` link into the existing Marketplace.
_STORE_TAB_LABELS = {"menu": "Menu", "merch": "Merch", "shop": "Shop"}


def manage_sections(page: dict, role: str | None, links: list[dict],
                    counts: dict, analytics: dict) -> list[dict]:
    """The management surface, as sections rather than a wall of buttons.

    Three separate questions, answered separately, because collapsing them is
    how a management screen starts lying:

      * **supported** — does this page *type* have this at all? A media page has
        no merch section, not a disabled one. `TYPE_TABS` already answers what a
        type has, so it answers this too rather than a second list drifting
        beside it. An unsupported section is absent from this list entirely.
      * **permitted** — may *this caller* act here? Straight out of the same
        `PERMISSIONS` table the mutating calls read, so a section that is
        offered is one the server will accept.
      * **ready** — is anything behind it yet? A section with nothing behind it
        stays visible with `setup` naming the one thing missing, because a team
        that cannot see the empty section cannot fill it. That is the difference
        between a section that is intentionally empty and a dead button.

    Pure: every input is already loaded by `manage_view`, so this adds no
    queries. Counts are the measured ones — nothing here is estimated, and a
    section with no number simply has none.

    Two absences are deliberate. **Events** is missing because there is no
    public events read to point it at yet (see the note by `TAB_LINK_SOURCE`);
    a section that 403s for its own team is worse than no section. **Audience**
    is missing because followers are counted but not listable — the count lives
    in Insights, where it is honest, rather than behind a heading that promises
    a list nobody can fetch.
    """
    page_type = page.get("page_type") or "OTHER"
    tabs = set(TYPE_TABS.get(page_type, TYPE_TABS["OTHER"]))
    linked = {row.get("link_type") for row in (links or [])}
    sections: list[dict] = []

    def add(key: str, label: str, hint: str, permission: str,
            ready: bool = True, setup: str = "", count: int | None = None) -> None:
        section = {
            "key": key,
            "label": label,
            "hint": hint,
            "permission": permission,
            "permitted": has_permission(role, permission),
            "ready": bool(ready),
            "setup": "" if ready else setup,
        }
        if count is not None:
            section["count"] = int(count)
        sections.append(section)

    add("overview", "Overview", "How this presence is doing right now.", "view_analytics")
    add("identity", "Identity", "Name, handle, description and contact details.", "edit_page")

    posts = _int(counts.get("posts"), 0)
    add("content", "Posts", "What this presence publishes.", "create_content",
        ready=posts > 0, setup="Nothing published yet. Write the first post as this presence.",
        count=posts)

    if "music" in tabs:
        add("music", "Music", "Tracks released under this name.", "manage_links",
            ready="music_artist" in linked,
            setup="Connect the artist profile these tracks were uploaded under.")

    if "videos" in tabs:
        videos = _int(counts.get("videos"), 0)
        add("videos", "Videos", "Video posts from this presence.", "create_content",
            ready=videos > 0, setup="Publish a video post and it appears here.", count=videos)

    store_tab = next((tab for tab in ("menu", "merch", "shop") if tab in tabs), "")
    if store_tab:
        add("store", _STORE_TAB_LABELS[store_tab], "What this presence sells.", "manage_marketplace",
            ready="store" in linked,
            setup="Connect a shop you already run. Selling stays in Marketplace — this points at it.")

    add("advertising", "Advertising", "Campaigns run for this presence.", "manage_ads",
        ready="ad_account" in linked,
        setup="Connect an ad account you already own to run campaigns as this presence.")

    if page_type in BUSINESS_PAGE_TYPES:
        add("business_os", "Business OS", "Orders, bookings and day-to-day operations.", "edit_page")

    add("insights", "Insights", "Followers, posts and team, as measured.", "view_analytics",
        count=_int(analytics.get("followers"), 0))
    add("team", "Team & access", "Who can act for this presence.", "view_analytics",
        count=_int(analytics.get("team_members"), 0))

    verified = (page.get("verification_status") or "unverified")
    add("verification", "Verification", "Proving this presence is who it says it is.", "manage_status",
        ready=verified == "verified",
        setup={
            "pending": "Your request is with the review team.",
            "rejected": "The last request was declined. You can send a new one.",
        }.get(verified, "Not verified yet. Requests are reviewed, never granted automatically."))

    add("payments", "Payments", "Where money for this presence arrives.", "manage_status")
    add("settings", "Settings", "Whether this presence is live, paused or unpublished.", "manage_status")
    return sections


def manage_view(conn: Any, user_id: int, page_id: int) -> dict:
    page = _load_page(conn, page_id)
    role = require_permission(conn, user_id, page["id"], "view_analytics")
    view = public_view(conn, page, viewer_user_id=user_id)
    links = list_links(conn, page["id"])
    analytics = page_analytics(conn, user_id, page["id"])
    view.update({
        "role": role,
        "capabilities": sorted(p for p, roles in PERMISSIONS.items() if role in roles),
        "owner_user_id": int(page.get("owner_user_id") or 0),
        "phone": page.get("phone") or "",
        "links": links,
        "members": list_members(conn, user_id, page["id"]) if has_permission(role, "manage_members") else [],
        "analytics": analytics,
        "completeness": page_completeness(conn, page),
        # Which management sections this page has, may be acted on, and has
        # anything behind — decided here so the client never re-derives it.
        "sections": manage_sections(page, role, links, _counts(conn, page["id"]), analytics),
    })
    return view
