"""Canonical consumer profile mutation.

Why this module exists
----------------------

Editing a consumer profile lived entirely inside ``bot.api_pulse_profile_update``
— sanitation, the reserved-handle check, the uniqueness check, the rate limit,
the UPDATE and the audit write, all inline in a Flask handler. Nothing else
could reach it. A second caller (the UNDX agent, a background job, a future
native endpoint) had exactly two options: call the HTTP route over the network,
or write its own ``UPDATE users`` — and the second option is how a second
authority gets born. This module is the first option made unnecessary.

There is a ``services/business_os/profile/service.py`` with an
``update_profile`` in it. It is not this. That one is the seller-side Business
OS profile: a different table, a different owner concept and a different
lifecycle. The name collision is unfortunate and worth stating out loud, because
"a profile service already exists" is a reasonable thing to conclude from a
grep and a wrong thing to act on.

Partial by construction
-----------------------

Every field defaults to ``_UNSET``, and only fields actually passed are
written. This is the whole difference between a shared service and a lifted
route handler: the route receives a complete form and can afford to write every
column, but a caller that only wants to change the bio must not have to send a
display name — and if it does, it will send a *stale* one, and quietly revert an
edit the user made on another device. Callers that genuinely want to clear a
field pass an empty string, which is distinct from not passing it at all.

Authorization
-------------

Self-service only: the actor is the subject. There is no ``target_user_id``
parameter and there should not be one, because adding it would make this the
place where "can X edit Y's profile" gets decided, and that question belongs to
the admin surface (``/admin`` routes, which have their own role checks and their
own 1000-character bio ceiling). A service that answers both questions ends up
answering the wrong one by default.
"""

from __future__ import annotations

import logging
import re

from services import dashboard_account_command_center as _account, user_context

LOGGER = logging.getLogger(__name__)

# Consumer ceilings, matching what the consumer route has always enforced. The
# admin surface allows a longer bio; that is deliberate and stays there.
DISPLAY_NAME_MAX = 80
USERNAME_MAX = 40
BIO_MAX = 500
SOCIAL_LINKS_MAX = 800
EXPERTISE_TAGS_MAX = 500
VISIBILITIES = {"public", "private"}

# Distinguishes "not supplied" from "supplied as empty". `None` cannot do this
# job because clearing a field is a real, expressible intent.
_UNSET = object()


class ProfileError(ValueError):
    """Rejected before any state change.

    ``http_status`` mirrors the codes ``/api/pulse/profile/update`` already
    returns: 400 validation, 409 handle taken, 429 rate limited.
    """

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


def _clean(value, limit: int) -> str:
    """Same sanitation as ``bot.clean_html``: strip tags, collapse whitespace.

    Reimplemented rather than imported so the sanitation path does not depend on
    importing the 111k-line Flask monolith. The two are two lines long and are
    covered by a test that pins them equal — if ``bot.clean_html`` gains a rule,
    that test fails rather than this silently falling behind.
    """
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat(timespec="seconds")


def profile_state(user_id) -> dict:
    """Read-only canonical snapshot. This is what verification reads back."""
    user_id = int(user_id or 0)
    if not user_id:
        return {}
    conn = user_context.connect()
    try:
        row = conn.cursor().execute(
            "SELECT * FROM users WHERE user_id=? LIMIT 1", (user_id,)
        ).fetchone()
        return _account.profile_snapshot(dict(row)) if row else {}
    finally:
        conn.close()


def update_profile(
    actor_user_id,
    *,
    display_name=_UNSET,
    username=_UNSET,
    bio=_UNSET,
    social_links=_UNSET,
    expertise_tags=_UNSET,
    profile_visibility=_UNSET,
    surface: str = "",
    ip_hash: str = "",
    user_agent_hash: str = "",
) -> dict:
    """Update the caller's own profile. Only supplied fields are written.

    Returns ``{"ok": True, "changed": bool, "fields_changed": [...],
    "before": {...}, "after": {...}, "profile": {...}}``.

    ``changed`` is computed from the before/after snapshots rather than from
    "an UPDATE ran", so saving a bio identical to the stored one reports
    ``changed=False``. That matters for the agent surface: a confirmation card
    that says "your bio was updated" when nothing moved is a false receipt.
    """
    actor_id = int(actor_user_id or 0)
    if not actor_id:
        raise ProfileError("Login required.", 401, "unauthenticated")

    updates: dict[str, object] = {}

    if display_name is not _UNSET:
        value = _clean(display_name, DISPLAY_NAME_MAX)
        if not value:
            raise ProfileError("Display name is required.", 400, "display_name_required")
        updates["display_name"] = value

    resolved_username = ""
    if username is not _UNSET:
        candidate = _clean(username, USERNAME_MAX).lstrip("@")
        ok, username_or_error = _account.safe_username(candidate)
        if not ok:
            raise ProfileError(username_or_error, 400, "invalid_username")
        resolved_username = username_or_error
        if resolved_username:
            updates["username"] = resolved_username

    if bio is not _UNSET:
        updates["bio"] = _clean(bio, BIO_MAX)

    if social_links is not _UNSET:
        updates["social_links_json"] = _clean(social_links, SOCIAL_LINKS_MAX)

    if expertise_tags is not _UNSET:
        updates["expertise_tags_json"] = _clean(expertise_tags, EXPERTISE_TAGS_MAX)

    if profile_visibility is not _UNSET:
        value = str(profile_visibility or "").strip().lower()
        if value not in VISIBILITIES:
            raise ProfileError("Profile visibility must be public or private.", 400, "invalid_visibility")
        updates["profile_visibility"] = value

    if not updates:
        raise ProfileError("Nothing to update.", 400, "no_fields")

    conn = user_context.connect()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM users WHERE user_id=? LIMIT 1", (actor_id,)).fetchone()
        if not row:
            raise ProfileError("Account not found.", 404, "not_found")
        current = dict(row)
        before = _account.profile_snapshot(current)

        # Rate limit before the uniqueness probe, not after: the probe is a
        # cheap handle-existence oracle, and a limiter that only runs on the
        # success path does not limit anything an attacker cares about.
        allowed, rate_message = _account.profile_change_allowed(conn, actor_id)
        if not allowed:
            raise ProfileError(rate_message, 429, "rate_limited")

        if resolved_username:
            taken = cur.execute(
                "SELECT user_id FROM users WHERE LOWER(username)=LOWER(?) AND user_id<>? LIMIT 1",
                (resolved_username, actor_id),
            ).fetchone()
            if taken:
                raise ProfileError("That username is already taken.", 409, "username_taken")

        assignments = ", ".join(f"{column}=?" for column in updates)
        cur.execute(
            f"UPDATE users SET {assignments}, updated_at=? WHERE user_id=?",
            (*updates.values(), _now(), actor_id),
        )
        conn.commit()

        fresh = dict(cur.execute(
            "SELECT * FROM users WHERE user_id=? LIMIT 1", (actor_id,)
        ).fetchone() or {})
        after = _account.profile_snapshot(fresh)
        fields_changed = sorted(k for k in after if before.get(k) != after.get(k))

        # The existing profile audit trail, not a new one. `record_profile_audit`
        # writes both `profile_audit_logs` and `account_audit_logs`, and the
        # hourly rate limit above counts its rows — so skipping it on an
        # unchanged save would also silently uncap no-op saves.
        _account.record_profile_audit(
            conn,
            user_id=actor_id,
            actor_user_id=actor_id,
            action="profile_updated",
            before=before,
            after=after,
            ip_hash=str(ip_hash or "")[:120],
            user_agent_hash=str(user_agent_hash or "")[:120],
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    LOGGER.info(
        "PULSE_PROFILE_UPDATED user_id=%s surface=%s fields=%s",
        actor_id, surface or "unspecified", ",".join(fields_changed) or "none",
    )
    return {
        "ok": True,
        "user_id": actor_id,
        "changed": bool(fields_changed),
        "fields_changed": fields_changed,
        "before": before,
        "after": after,
        "profile": fresh,
        "message": "Profile updated." if fields_changed else "Profile already matched.",
    }


def update_profile_bio(requester_user_id, bio, *, surface: str = "") -> dict:
    """Change only the bio. A named wrapper, not a second implementation.

    Exists because "update my bio" is the operation UNDX exposes, and routing it
    through the general updater with five ``_UNSET`` arguments at every call site
    invites someone to eventually pass a sixth by accident.
    """
    result = update_profile(requester_user_id, bio=bio, surface=surface)
    result["bio"] = (result.get("after") or {}).get("bio", "")
    return result
