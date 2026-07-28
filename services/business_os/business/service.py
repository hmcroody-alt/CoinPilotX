"""Canonical Business service — the source of truth for business identity.

Everything a business *is* on PulseSoc lives here: identity (legal/display name),
brand, contact, organization/locations, team + RBAC, versioned policies, and an
append-only timeline. Other Business OS modules reference the ``business_id`` row
this service owns; they never restate business information locally.

Design contract (mirrors the marketplace slice exactly):

  1. Flag-gated. With ``BUSINESS_OS_BUSINESS`` unset the whole surface is inert —
     every entry point raises ``BusinessError(503, "disabled")``.
  2. Server-authoritative. Clients never set ``business_id`` / ``owner_user_id`` /
     ``status`` / ``version`` / timestamps directly; the service assigns them.
  3. Access is checked on every operation against the caller's effective role
     (owner or membership). A caller with no access sees 404 — existence is never
     leaked.
  4. Every mutation is audited into ``business_os_business_audit`` (the timeline).
  5. Account hold beats everything (``_require_not_held``), evaluated from the
     live context bot.py passes in.

Nothing here imports Flask or ``bot.py``; all logic is pure and unit-testable.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.business import schema as _schema


# --- flag -------------------------------------------------------------------
FLAG_ENV = "BUSINESS_OS_BUSINESS"


def is_enabled() -> bool:
    """True only when the rollout flag is explicitly on. Unset => off (inert)."""
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


# --- vocabularies -----------------------------------------------------------
BUSINESS_STATUSES = {"draft", "active", "suspended", "archived"}
# Owner/admin-reachable lifecycle verbs -> fixed target states. Clients never
# send a raw status; they send an action.
BUSINESS_ACTIONS = {
    "activate": "active", "suspend": "suspended",
    "restore": "active", "archive": "archived",
}
BUSINESS_TRANSITIONS = {
    "draft": {"active", "archived"},
    "active": {"suspended", "archived"},
    "suspended": {"active", "archived"},
    "archived": {"draft"},
}

LOCATION_KINDS = {"physical", "virtual", "warehouse", "office", "popup"}
LOCATION_STATUSES = {"active", "closed"}

# RBAC roles, most-privileged first. The index in this tuple is the rank used by
# _role_rank; a caller can only assign/downgrade to a role at or below their own.
ROLES = ("owner", "admin", "manager", "staff", "viewer")
MEMBER_STATUSES = {"active", "invited", "suspended", "removed"}

# Fixed permission matrix. Each permission maps to the minimum role that holds it.
# _require_permission compares the caller's role rank against this.
PERMISSIONS = {
    "business.read": "viewer",
    "business.update": "manager",
    "business.lifecycle": "admin",     # activate/suspend/archive
    "location.read": "viewer",
    "location.write": "manager",
    "policy.read": "viewer",
    "policy.write": "manager",
    "member.read": "staff",
    "member.write": "admin",           # invite / change role / remove
    "timeline.read": "staff",
}

POLICY_TYPES = {"returns", "privacy", "terms", "shipping", "refunds", "community"}

# Field limits keep typos and abuse out of the canonical row.
NAME_MAX = 160
TAGLINE_MAX = 200
DESC_MAX = 8000
POLICY_BODY_MAX = 40000
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

# Client-writable field allowlists (anything else is ignored/rejected).
BUSINESS_CREATE_FIELDS = {
    "legal_name", "display_name", "tagline", "description", "category",
    "logo_media_ref", "primary_color", "contact_email", "contact_phone",
    "website_url",
}
BUSINESS_UPDATE_FIELDS = set(BUSINESS_CREATE_FIELDS)  # same editable identity/brand set
LOCATION_FIELDS = {
    "label", "kind", "address_line1", "address_line2", "city", "region",
    "postal_code", "country",
}


class BusinessError(ValueError):
    """Raised when a business operation is rejected before any state change.

    Carries an ``http_status`` (403 forbidden/hold, 404 missing/not-accessible,
    409 illegal transition/conflict, 400 validation, 503 flag off) and a stable
    ``code``.
    """

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


# --- time / id helpers ------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uid() -> str:
    return uuid.uuid4().hex


def _sid(user_id: Any) -> str:
    return str(user_id)


def _require_enabled() -> None:
    if not is_enabled():
        raise BusinessError(
            "Business OS is not enabled in this environment.",
            http_status=503, code="disabled")


def _require_not_held(context: Optional[dict]) -> None:
    """Account hold beats everything. Uses the passed live context if present.
    Never silently passes a held account."""
    ctx = context or {}
    status = str(ctx.get("account_status") or "").lower()
    access = ctx.get("access_enabled")
    if status in {"suspended", "banned", "disabled", "hold"}:
        raise BusinessError("Account is on hold.", 403, "account_hold")
    if access is not None and not access:
        raise BusinessError("Account access is disabled.", 403, "account_hold")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _rows(rows) -> list:
    out = []
    for r in rows or []:
        d = _row(r)
        if d is not None:
            out.append(d)
    return out


def _audit(conn, *, business_id, subject_type, subject_ref, action, actor,
           reason=None, before=None, after=None) -> None:
    conn.execute(
        "INSERT INTO business_os_business_audit "
        "(business_id, subject_type, subject_ref, action, actor, reason, "
        "before_json, after_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            None if business_id is None else str(business_id),
            subject_type,
            None if subject_ref is None else str(subject_ref),
            action,
            None if actor is None else str(actor),
            reason,
            None if before is None else json.dumps(before, sort_keys=True),
            None if after is None else json.dumps(after, sort_keys=True),
            _now_iso(),
        ),
    )


# --- validation helpers -----------------------------------------------------
def _clean_str(value: Any, *, field: str, max_len: int, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise BusinessError(f"{field} is required.", 400, "invalid")
        return None
    if not isinstance(value, str):
        raise BusinessError(f"{field} must be text.", 400, "invalid")
    v = value.strip()
    if not v:
        if required:
            raise BusinessError(f"{field} is required.", 400, "invalid")
        return None
    if len(v) > max_len:
        raise BusinessError(f"{field} is too long (max {max_len}).", 400, "invalid")
    return v


def _validate_identity(payload: dict, *, require_name: bool) -> dict:
    """Normalize/validate the client-writable identity+brand+contact fields.
    Returns a dict of only the provided, cleaned fields."""
    if not isinstance(payload, dict):
        raise BusinessError("Payload must be an object.", 400, "invalid")
    out: dict = {}
    if require_name or "display_name" in payload:
        out["display_name"] = _clean_str(
            payload.get("display_name"), field="display_name",
            max_len=NAME_MAX, required=require_name)
    if "legal_name" in payload:
        out["legal_name"] = _clean_str(payload.get("legal_name"),
                                       field="legal_name", max_len=NAME_MAX)
    if "tagline" in payload:
        out["tagline"] = _clean_str(payload.get("tagline"),
                                    field="tagline", max_len=TAGLINE_MAX)
    if "description" in payload:
        out["description"] = _clean_str(payload.get("description"),
                                        field="description", max_len=DESC_MAX)
    if "category" in payload:
        out["category"] = _clean_str(payload.get("category"),
                                     field="category", max_len=NAME_MAX)
    if "logo_media_ref" in payload:
        out["logo_media_ref"] = _clean_str(payload.get("logo_media_ref"),
                                           field="logo_media_ref", max_len=512)
    if "primary_color" in payload:
        color = payload.get("primary_color")
        if color is None or (isinstance(color, str) and not color.strip()):
            out["primary_color"] = None
        else:
            if not isinstance(color, str) or not COLOR_RE.match(color.strip()):
                raise BusinessError("primary_color must be a hex color like #1a2b3c.",
                                    400, "invalid")
            out["primary_color"] = color.strip().lower()
    if "contact_email" in payload:
        email = payload.get("contact_email")
        if email is None or (isinstance(email, str) and not email.strip()):
            out["contact_email"] = None
        else:
            if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
                raise BusinessError("contact_email is not a valid email.", 400, "invalid")
            out["contact_email"] = email.strip()
    if "contact_phone" in payload:
        out["contact_phone"] = _clean_str(payload.get("contact_phone"),
                                          field="contact_phone", max_len=40)
    if "website_url" in payload:
        url = payload.get("website_url")
        if url is None or (isinstance(url, str) and not url.strip()):
            out["website_url"] = None
        else:
            if not isinstance(url, str) or not URL_RE.match(url.strip()):
                raise BusinessError("website_url must start with http:// or https://.",
                                    400, "invalid")
            out["website_url"] = url.strip()
    return out


# --- RBAC -------------------------------------------------------------------
def _role_rank(role: Optional[str]) -> int:
    """Lower rank = more privileged. Unknown/None role => below viewer."""
    try:
        return ROLES.index(role)
    except (ValueError, TypeError):
        return len(ROLES)  # no access


def _effective_role(conn, business_id: str, user_id: Any) -> Optional[str]:
    """The caller's effective role on a business: 'owner' if they own it, else the
    active membership role, else None (no access)."""
    uid = _sid(user_id)
    biz = conn.execute(
        "SELECT owner_user_id FROM business_os_business WHERE business_id = ?",
        (business_id,),
    ).fetchone()
    if biz is None:
        return None  # caller learns nothing about non-existent vs not-owned
    biz = _row(biz)
    if biz and _sid(biz.get("owner_user_id")) == uid:
        return "owner"
    m = conn.execute(
        "SELECT role, status FROM business_os_business_members "
        "WHERE business_id = ? AND user_id = ?",
        (business_id, uid),
    ).fetchone()
    m = _row(m)
    if m and str(m.get("status")) == "active":
        role = str(m.get("role"))
        return role if role in ROLES else None
    return None


def _require_access(conn, business_id: str, user_id: Any) -> str:
    """Return the caller's effective role or raise 404 (existence not leaked)."""
    role = _effective_role(conn, business_id, user_id)
    if role is None:
        raise BusinessError("Business not found.", 404, "not_found")
    return role


def _require_permission(role: str, permission: str) -> None:
    needed = PERMISSIONS.get(permission)
    if needed is None:
        raise BusinessError("Unknown permission.", 403, "forbidden")
    if _role_rank(role) > _role_rank(needed):
        raise BusinessError(
            f"Your role ({role}) cannot perform this action.", 403, "forbidden")


# --- projections ------------------------------------------------------------
def _business_public(row: dict) -> dict:
    """Client-safe projection of a canonical business row."""
    return {
        "business_id": row.get("business_id"),
        "owner_user_id": row.get("owner_user_id"),
        "legal_name": row.get("legal_name"),
        "display_name": row.get("display_name"),
        "tagline": row.get("tagline"),
        "description": row.get("description"),
        "category": row.get("category"),
        "logo_media_ref": row.get("logo_media_ref"),
        "primary_color": row.get("primary_color"),
        "contact_email": row.get("contact_email"),
        "contact_phone": row.get("contact_phone"),
        "website_url": row.get("website_url"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _get_business_row(conn, business_id: str) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_business WHERE business_id = ?",
        (business_id,),
    ).fetchone())


# ============================================================================
# Business identity
# ============================================================================
def create_business(owner_user_id: Any, payload: dict,
                    *, context: Optional[dict] = None) -> dict:
    """Create a canonical business owned by the caller. display_name is required.
    The owner_user_id, business_id, status, and timestamps are server-assigned."""
    _require_enabled()
    _require_not_held(context)
    fields = _validate_identity(payload or {}, require_name=True)
    now = _now_iso()
    bid = _uid()
    conn = db.connect()
    try:
        conn.execute(
            """
            INSERT INTO business_os_business
            (business_id, owner_user_id, legal_name, display_name, tagline,
             description, category, logo_media_ref, primary_color, contact_email,
             contact_phone, website_url, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bid, _sid(owner_user_id), fields.get("legal_name"),
                fields["display_name"], fields.get("tagline"),
                fields.get("description"), fields.get("category"),
                fields.get("logo_media_ref"), fields.get("primary_color"),
                fields.get("contact_email"), fields.get("contact_phone"),
                fields.get("website_url"), "draft", now, now,
            ),
        )
        # The owner is also recorded as an explicit member row so the team surface
        # is complete and role lookups are uniform.
        conn.execute(
            "INSERT INTO business_os_business_members "
            "(member_id, business_id, user_id, role, status, invited_by, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_uid(), bid, _sid(owner_user_id), "owner", "active",
             _sid(owner_user_id), now, now),
        )
        _audit(conn, business_id=bid, subject_type="business", subject_ref=bid,
               action="business.create", actor=_sid(owner_user_id),
               after=_business_public(_get_business_row(conn, bid)))
        conn.commit()
        return _business_public(_get_business_row(conn, bid))
    finally:
        conn.close()


def get_business(business_id: str, actor_user_id: Any) -> dict:
    """Read a business the caller can access. 404 if it doesn't exist or the caller
    has no role on it (existence never leaked)."""
    _require_enabled()
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "business.read")
        row = _get_business_row(conn, business_id)
        out = _business_public(row)
        out["viewer_role"] = role
        return out
    finally:
        conn.close()


def list_businesses(user_id: Any) -> list:
    """Every business the caller owns or is an active member of, most-recent first."""
    _require_enabled()
    uid = _sid(user_id)
    conn = db.connect()
    try:
        rows = _rows(conn.execute(
            """
            SELECT b.* FROM business_os_business b
            WHERE b.owner_user_id = ?
               OR b.business_id IN (
                   SELECT business_id FROM business_os_business_members
                   WHERE user_id = ? AND status = 'active')
            ORDER BY b.created_at DESC
            """,
            (uid, uid),
        ).fetchall())
        return [_business_public(r) for r in rows]
    finally:
        conn.close()


def update_business(business_id: str, actor_user_id: Any, payload: dict,
                   *, context: Optional[dict] = None) -> dict:
    """Update identity/brand/contact fields. Requires business.update (manager+)."""
    _require_enabled()
    _require_not_held(context)
    fields = _validate_identity(payload or {}, require_name=False)
    if not fields:
        raise BusinessError("No editable fields provided.", 400, "invalid")
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "business.update")
        before = _business_public(_get_business_row(conn, business_id))
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [_now_iso(), business_id]
        conn.execute(
            f"UPDATE business_os_business SET {sets}, updated_at = ? "
            "WHERE business_id = ?",
            params,
        )
        after = _business_public(_get_business_row(conn, business_id))
        _audit(conn, business_id=business_id, subject_type="business",
               subject_ref=business_id, action="business.update",
               actor=_sid(actor_user_id), before=before, after=after)
        conn.commit()
        return after
    finally:
        conn.close()


def set_business_status(business_id: str, actor_user_id: Any, action: str,
                       *, reason: Optional[str] = None,
                       context: Optional[dict] = None) -> dict:
    """Move the business lifecycle via a verb (activate/suspend/restore/archive).
    Requires business.lifecycle (admin+). Illegal transitions are 409."""
    _require_enabled()
    _require_not_held(context)
    if action not in BUSINESS_ACTIONS:
        raise BusinessError("Unknown lifecycle action.", 400, "invalid")
    target = BUSINESS_ACTIONS[action]
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "business.lifecycle")
        row = _get_business_row(conn, business_id)
        current = str(row.get("status"))
        allowed = BUSINESS_TRANSITIONS.get(current, set())
        if target == current or target not in allowed:
            raise BusinessError(
                f"Cannot {action} a business in '{current}' state.", 409,
                "illegal_transition")
        before = _business_public(row)
        conn.execute(
            "UPDATE business_os_business SET status = ?, updated_at = ? "
            "WHERE business_id = ?",
            (target, _now_iso(), business_id),
        )
        after = _business_public(_get_business_row(conn, business_id))
        _audit(conn, business_id=business_id, subject_type="business",
               subject_ref=business_id, action=f"business.{action}",
               actor=_sid(actor_user_id), reason=reason, before=before, after=after)
        conn.commit()
        return after
    finally:
        conn.close()


# ============================================================================
# Locations
# ============================================================================
def _validate_location(payload: dict, *, require_label: bool) -> dict:
    if not isinstance(payload, dict):
        raise BusinessError("Payload must be an object.", 400, "invalid")
    out: dict = {}
    if require_label or "label" in payload:
        out["label"] = _clean_str(payload.get("label"), field="label",
                                  max_len=NAME_MAX, required=require_label)
    if "kind" in payload:
        kind = payload.get("kind")
        if kind is not None:
            if kind not in LOCATION_KINDS:
                raise BusinessError("Unknown location kind.", 400, "invalid")
            out["kind"] = kind
    for f in ("address_line1", "address_line2", "city", "region",
              "postal_code", "country"):
        if f in payload:
            out[f] = _clean_str(payload.get(f), field=f, max_len=200)
    return out


def add_location(business_id: str, actor_user_id: Any, payload: dict,
                *, context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    fields = _validate_location(payload or {}, require_label=True)
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "location.write")
        now = _now_iso()
        lid = _uid()
        conn.execute(
            """
            INSERT INTO business_os_business_locations
            (location_id, business_id, label, kind, address_line1, address_line2,
             city, region, postal_code, country, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lid, business_id, fields["label"], fields.get("kind", "physical"),
                fields.get("address_line1"), fields.get("address_line2"),
                fields.get("city"), fields.get("region"),
                fields.get("postal_code"), fields.get("country"),
                "active", now, now,
            ),
        )
        out = _row(conn.execute(
            "SELECT * FROM business_os_business_locations WHERE location_id = ?",
            (lid,),
        ).fetchone())
        _audit(conn, business_id=business_id, subject_type="location",
               subject_ref=lid, action="location.add",
               actor=_sid(actor_user_id), after=out)
        conn.commit()
        return out
    finally:
        conn.close()


def list_locations(business_id: str, actor_user_id: Any) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "location.read")
        return _rows(conn.execute(
            "SELECT * FROM business_os_business_locations "
            "WHERE business_id = ? AND status != 'closed' ORDER BY created_at ASC",
            (business_id,),
        ).fetchall())
    finally:
        conn.close()


def _get_location(conn, business_id: str, location_id: str) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_business_locations "
        "WHERE location_id = ? AND business_id = ?",
        (location_id, business_id),
    ).fetchone())


def update_location(business_id: str, actor_user_id: Any, location_id: str,
                   payload: dict, *, context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    fields = _validate_location(payload or {}, require_label=False)
    if not fields:
        raise BusinessError("No editable fields provided.", 400, "invalid")
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "location.write")
        before = _get_location(conn, business_id, location_id)
        if before is None:
            raise BusinessError("Location not found.", 404, "not_found")
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [_now_iso(), location_id, business_id]
        conn.execute(
            f"UPDATE business_os_business_locations SET {sets}, updated_at = ? "
            "WHERE location_id = ? AND business_id = ?",
            params,
        )
        after = _get_location(conn, business_id, location_id)
        _audit(conn, business_id=business_id, subject_type="location",
               subject_ref=location_id, action="location.update",
               actor=_sid(actor_user_id), before=before, after=after)
        conn.commit()
        return after
    finally:
        conn.close()


def close_location(business_id: str, actor_user_id: Any, location_id: str,
                  *, reason: Optional[str] = None,
                  context: Optional[dict] = None) -> dict:
    """Soft-close a location (status='closed'). Never hard-deletes."""
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "location.write")
        before = _get_location(conn, business_id, location_id)
        if before is None:
            raise BusinessError("Location not found.", 404, "not_found")
        if str(before.get("status")) == "closed":
            return before
        conn.execute(
            "UPDATE business_os_business_locations SET status = 'closed', "
            "updated_at = ? WHERE location_id = ? AND business_id = ?",
            (_now_iso(), location_id, business_id),
        )
        after = _get_location(conn, business_id, location_id)
        _audit(conn, business_id=business_id, subject_type="location",
               subject_ref=location_id, action="location.close",
               actor=_sid(actor_user_id), reason=reason, before=before, after=after)
        conn.commit()
        return after
    finally:
        conn.close()


# ============================================================================
# Team + RBAC
# ============================================================================
def list_members(business_id: str, actor_user_id: Any) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "member.read")
        return _rows(conn.execute(
            "SELECT * FROM business_os_business_members "
            "WHERE business_id = ? AND status != 'removed' ORDER BY created_at ASC",
            (business_id,),
        ).fetchall())
    finally:
        conn.close()


def add_member(business_id: str, actor_user_id: Any, member_user_id: Any,
              role_to_grant: str, *, context: Optional[dict] = None) -> dict:
    """Invite a user to the team with a role. Requires member.write (admin+).
    A caller may never grant a role more privileged than their own, and 'owner'
    can only be held by the account owner (never granted here)."""
    _require_enabled()
    _require_not_held(context)
    if role_to_grant not in ROLES:
        raise BusinessError("Unknown role.", 400, "invalid")
    if role_to_grant == "owner":
        raise BusinessError("Ownership cannot be granted here.", 403, "forbidden")
    target_uid = _sid(member_user_id)
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "member.write")
        if _role_rank(role_to_grant) < _role_rank(role):
            raise BusinessError(
                "You cannot grant a role more privileged than your own.",
                403, "forbidden")
        if _sid(actor_user_id) == target_uid:
            raise BusinessError("You cannot change your own membership.",
                                400, "invalid")
        existing = _row(conn.execute(
            "SELECT * FROM business_os_business_members "
            "WHERE business_id = ? AND user_id = ?",
            (business_id, target_uid),
        ).fetchone())
        now = _now_iso()
        if existing and str(existing.get("status")) != "removed":
            raise BusinessError("User is already a member.", 409, "conflict")
        if existing:
            # Re-activate a previously removed member with the new role.
            conn.execute(
                "UPDATE business_os_business_members SET role = ?, status = 'active', "
                "invited_by = ?, updated_at = ? WHERE member_id = ?",
                (role_to_grant, _sid(actor_user_id), now, existing["member_id"]),
            )
            mid = existing["member_id"]
        else:
            mid = _uid()
            conn.execute(
                "INSERT INTO business_os_business_members "
                "(member_id, business_id, user_id, role, status, invited_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, business_id, target_uid, role_to_grant, "active",
                 _sid(actor_user_id), now, now),
            )
        out = _row(conn.execute(
            "SELECT * FROM business_os_business_members WHERE member_id = ?",
            (mid,),
        ).fetchone())
        _audit(conn, business_id=business_id, subject_type="member",
               subject_ref=target_uid, action="member.add",
               actor=_sid(actor_user_id), after=out)
        conn.commit()
        return out
    finally:
        conn.close()


def update_member_role(business_id: str, actor_user_id: Any, member_user_id: Any,
                      new_role: str, *, context: Optional[dict] = None) -> dict:
    """Change a member's role. Requires member.write (admin+). The account owner's
    role cannot be changed, and a caller cannot set a role above their own."""
    _require_enabled()
    _require_not_held(context)
    if new_role not in ROLES or new_role == "owner":
        raise BusinessError("Invalid target role.", 400, "invalid")
    target_uid = _sid(member_user_id)
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "member.write")
        if _sid(actor_user_id) == target_uid:
            raise BusinessError("You cannot change your own role.", 400, "invalid")
        member = _row(conn.execute(
            "SELECT * FROM business_os_business_members "
            "WHERE business_id = ? AND user_id = ? AND status = 'active'",
            (business_id, target_uid),
        ).fetchone())
        if member is None:
            raise BusinessError("Member not found.", 404, "not_found")
        if str(member.get("role")) == "owner":
            raise BusinessError("The owner's role cannot be changed.", 403, "forbidden")
        if _role_rank(new_role) < _role_rank(role):
            raise BusinessError(
                "You cannot set a role more privileged than your own.",
                403, "forbidden")
        before = dict(member)
        conn.execute(
            "UPDATE business_os_business_members SET role = ?, updated_at = ? "
            "WHERE member_id = ?",
            (new_role, _now_iso(), member["member_id"]),
        )
        after = _row(conn.execute(
            "SELECT * FROM business_os_business_members WHERE member_id = ?",
            (member["member_id"],),
        ).fetchone())
        _audit(conn, business_id=business_id, subject_type="member",
               subject_ref=target_uid, action="member.role_change",
               actor=_sid(actor_user_id), before=before, after=after)
        conn.commit()
        return after
    finally:
        conn.close()


def remove_member(business_id: str, actor_user_id: Any, member_user_id: Any,
                 *, reason: Optional[str] = None,
                 context: Optional[dict] = None) -> dict:
    """Soft-remove a member (status='removed'). The owner can never be removed."""
    _require_enabled()
    _require_not_held(context)
    target_uid = _sid(member_user_id)
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "member.write")
        member = _row(conn.execute(
            "SELECT * FROM business_os_business_members "
            "WHERE business_id = ? AND user_id = ? AND status = 'active'",
            (business_id, target_uid),
        ).fetchone())
        if member is None:
            raise BusinessError("Member not found.", 404, "not_found")
        if str(member.get("role")) == "owner":
            raise BusinessError("The owner cannot be removed.", 403, "forbidden")
        before = dict(member)
        conn.execute(
            "UPDATE business_os_business_members SET status = 'removed', "
            "updated_at = ? WHERE member_id = ?",
            (_now_iso(), member["member_id"]),
        )
        after = _row(conn.execute(
            "SELECT * FROM business_os_business_members WHERE member_id = ?",
            (member["member_id"],),
        ).fetchone())
        _audit(conn, business_id=business_id, subject_type="member",
               subject_ref=target_uid, action="member.remove",
               actor=_sid(actor_user_id), reason=reason, before=before, after=after)
        conn.commit()
        return after
    finally:
        conn.close()


# ============================================================================
# Policies (versioned, append-only)
# ============================================================================
def set_policy(business_id: str, actor_user_id: Any, policy_type: str, body: Any,
              *, context: Optional[dict] = None) -> dict:
    """Publish a new version of a policy document. Requires policy.write (manager+).
    Never overwrites history: each publish is a new max(version) row."""
    _require_enabled()
    _require_not_held(context)
    if policy_type not in POLICY_TYPES:
        raise BusinessError("Unknown policy type.", 400, "invalid")
    text = _clean_str(body, field="body", max_len=POLICY_BODY_MAX, required=True)
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "policy.write")
        prev = _row(conn.execute(
            "SELECT MAX(version) AS v FROM business_os_business_policies "
            "WHERE business_id = ? AND policy_type = ?",
            (business_id, policy_type),
        ).fetchone())
        next_version = int((prev or {}).get("v") or 0) + 1
        pid = _uid()
        conn.execute(
            "INSERT INTO business_os_business_policies "
            "(policy_id, business_id, policy_type, version, body, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, business_id, policy_type, next_version, text,
             _sid(actor_user_id), _now_iso()),
        )
        out = _row(conn.execute(
            "SELECT * FROM business_os_business_policies WHERE policy_id = ?",
            (pid,),
        ).fetchone())
        _audit(conn, business_id=business_id, subject_type="policy",
               subject_ref=f"{policy_type}:{next_version}", action="policy.set",
               actor=_sid(actor_user_id), after={"policy_type": policy_type,
                                                 "version": next_version})
        conn.commit()
        return out
    finally:
        conn.close()


def get_policy(business_id: str, actor_user_id: Any, policy_type: str) -> Optional[dict]:
    """The live (highest-version) policy of a type, or None if never set."""
    _require_enabled()
    if policy_type not in POLICY_TYPES:
        raise BusinessError("Unknown policy type.", 400, "invalid")
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "policy.read")
        return _row(conn.execute(
            "SELECT * FROM business_os_business_policies "
            "WHERE business_id = ? AND policy_type = ? "
            "ORDER BY version DESC LIMIT 1",
            (business_id, policy_type),
        ).fetchone())
    finally:
        conn.close()


def list_policies(business_id: str, actor_user_id: Any) -> list:
    """The live version of every policy type that has been set."""
    _require_enabled()
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "policy.read")
        rows = _rows(conn.execute(
            "SELECT * FROM business_os_business_policies "
            "WHERE business_id = ? ORDER BY policy_type ASC, version DESC",
            (business_id,),
        ).fetchall())
        live: dict = {}
        for r in rows:
            pt = r.get("policy_type")
            if pt not in live:
                live[pt] = r
        return list(live.values())
    finally:
        conn.close()


# ============================================================================
# Timeline (append-only audit read)
# ============================================================================
def get_timeline(business_id: str, actor_user_id: Any, *, limit: int = 100) -> list:
    """The append-only business timeline, most-recent first. Requires timeline.read
    (staff+). before/after JSON are parsed back into objects for the client."""
    _require_enabled()
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    conn = db.connect()
    try:
        role = _require_access(conn, business_id, actor_user_id)
        _require_permission(role, "timeline.read")
        rows = _rows(conn.execute(
            "SELECT id, business_id, subject_type, subject_ref, action, actor, "
            "reason, before_json, after_json, created_at "
            "FROM business_os_business_audit WHERE business_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (business_id, limit),
        ).fetchall())
        out = []
        for r in rows:
            entry = {
                "id": r.get("id"),
                "subject_type": r.get("subject_type"),
                "subject_ref": r.get("subject_ref"),
                "action": r.get("action"),
                "actor": r.get("actor"),
                "reason": r.get("reason"),
                "created_at": r.get("created_at"),
            }
            for k, src in (("before", "before_json"), ("after", "after_json")):
                raw = r.get(src)
                if raw:
                    try:
                        entry[k] = json.loads(raw)
                    except Exception:
                        entry[k] = None
            out.append(entry)
        return out
    finally:
        conn.close()
