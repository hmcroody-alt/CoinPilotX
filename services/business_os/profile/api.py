"""Business OS — seller business profile: framework-agnostic HTTP controller.

``bot.py`` owns authentication, CSRF and session identity; it calls these functions
with an *already-authenticated* ``actor_user_id`` and the parsed request body, and
turns the returned ``(status_code, body)`` tuple into a Flask JSON response. Keeping
the decisions here rather than inline in ``bot.py`` is what makes every branch
testable — ``bot.py`` cannot be imported in the hermetic sandbox.

Contract for every handler (matching the other Business OS controllers):

* returns ``(int status_code, dict body)``; ``body`` always carries an ``ok`` bool;
* only the curated ``ProfileError`` message reaches the client, never an internal
  exception string;
* the client may never set ``verification``, ``locks``, ``sync``, ``published_at``
  or ``updated_at`` — those are server-authoritative, and the request allowlist
  ``WRITABLE_FIELDS`` silently drops them rather than 400-ing, so a client that
  round-trips the whole owner object back at us still saves correctly.

Two deliberate departures from ``business/api.py``:

**No feature flag.** ``business/api.py`` opens every handler with a ``_dark()`` guard
because ``BUSINESS_OS_BUSINESS`` gates it, and that variable is set in no deployment
file in this repository — so those routes 404 in production. This surface backs a
screen that ships, so gating it the same way would ship a screen that 404s. If a flag
is wanted later it belongs in ``bot.py``'s route registration, where turning it off
removes the route rather than leaving a live route that lies.

**Partial success is a 200, not a 400.** ``update_profile`` returns saved *and*
rejected fields together. Failing the whole request because one URL had a typo is the
behaviour the brief explicitly rules out; the client renders ``rejected`` as per-field
errors beside the fields that did save.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from services.business_os.profile import service as svc
from services.business_os.profile.service import ProfileError

# Re-exported so a client can render pickers without hard-coding the vocabularies,
# and so a drift between client and server shows up as a failing test here.
BUSINESS_CATEGORIES = tuple(svc.BUSINESS_CATEGORIES)
CONTACT_VISIBILITY = tuple(svc.CONTACT_VISIBILITY)
PREFERRED_CONTACT = tuple(svc.PREFERRED_CONTACT)
HOURS_MODES = tuple(svc.HOURS_MODES)
LINK_KINDS = tuple(svc.LINK_KINDS)
ADDRESS_KINDS = tuple(svc.ADDRESS_KINDS)
WEEKDAYS = tuple(svc.WEEKDAYS)
VERIFICATION_STATES = tuple(svc.VERIFICATION_STATES)
WRITABLE_FIELDS = frozenset(svc.WRITABLE_FIELDS)


def _ok(body: Optional[dict] = None, status: int = 200) -> Tuple[int, dict]:
    out: Dict[str, Any] = {"ok": True}
    out.update(body or {})
    return (status, out)


def _err(exc: ProfileError) -> Tuple[int, dict]:
    return (int(getattr(exc, "http_status", 400)),
            {"ok": False, "error": str(exc), "code": getattr(exc, "code", "invalid")})


def _bad(message: str, code: str = "invalid", status: int = 400) -> Tuple[int, dict]:
    return (status, {"ok": False, "error": message, "code": code})


def _actor(actor_user_id: Any) -> int:
    """Reject a missing or non-numeric identity loudly rather than writing row 0."""
    try:
        value = int(actor_user_id)
    except (TypeError, ValueError):
        raise ProfileError("Sign in to manage your business profile.", 401, "unauthenticated")
    if value <= 0:
        raise ProfileError("Sign in to manage your business profile.", 401, "unauthenticated")
    return value


def _pick(payload: Any, allowed) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if key in allowed}


# ============================================================================
# Owner surface
# ============================================================================

def get_profile(actor_user_id: Any) -> Tuple[int, dict]:
    """Everything the owner screen needs in one round trip.

    One call rather than six because the screen's states are cross-cutting: the
    completeness card, the lock explainer and the Live Sync badge are each derived
    from the same verification state, and fetching them separately is how the current
    screen ended up rendering "in review" and "Approved" at the same time.
    """
    try:
        return _ok({"profile": svc.owner_profile(_actor(actor_user_id)),
                    "vocabularies": vocabularies()[1]["vocabularies"]})
    except ProfileError as exc:
        return _err(exc)


def update_profile(actor_user_id: Any, payload: Any) -> Tuple[int, dict]:
    """Partial save. 200 even when some fields were rejected — see the module note."""
    try:
        actor = _actor(actor_user_id)
    except ProfileError as exc:
        return _err(exc)
    fields = _pick(payload, WRITABLE_FIELDS)
    unknown = sorted(set(payload.keys()) - WRITABLE_FIELDS) if isinstance(payload, dict) else []
    if not fields:
        return _bad("No editable fields in that request.", "empty")
    try:
        result = svc.update_profile(actor, fields)
    except ProfileError as exc:
        return _err(exc)
    return _ok({
        "saved": result["saved"],
        "rejected": result["rejected"],
        "queued_for_review": result["queued_for_review"],
        "ignored": unknown,
        "profile": result["profile"],
    })


def set_hours(actor_user_id: Any, payload: Any) -> Tuple[int, dict]:
    payload = payload if isinstance(payload, dict) else {}
    try:
        actor = _actor(actor_user_id)
        profile = svc.set_hours(actor, payload.get("mode"), payload.get("days"))
    except ProfileError as exc:
        return _err(exc)
    return _ok({"profile": profile})


def set_hours_override(actor_user_id: Any, payload: Any) -> Tuple[int, dict]:
    payload = payload if isinstance(payload, dict) else {}
    try:
        actor = _actor(actor_user_id)
        profile = svc.set_hours_override(
            actor,
            payload.get("date") or payload.get("on_date"),
            closed=payload.get("closed", True),
            opens=payload.get("opens"),
            closes=payload.get("closes"),
            label=payload.get("label"),
        )
    except ProfileError as exc:
        return _err(exc)
    return _ok({"profile": profile})


def set_link(actor_user_id: Any, payload: Any) -> Tuple[int, dict]:
    """An empty ``url`` deletes the link; the client's "remove" is the same call."""
    payload = payload if isinstance(payload, dict) else {}
    try:
        actor = _actor(actor_user_id)
        profile = svc.set_link(actor, payload.get("kind"), payload.get("url"),
                               label=payload.get("label"),
                               position=payload.get("position") or 0)
    except ProfileError as exc:
        return _err(exc)
    return _ok({"profile": profile})


def set_address(actor_user_id: Any, payload: Any) -> Tuple[int, dict]:
    """Operational addresses only — ``ADDRESS_KINDS`` has no ``legal`` member.

    A registered/legal address is verification evidence and is written by the
    verification flow with its own review trail, not by the profile editor.
    """
    payload = payload if isinstance(payload, dict) else {}
    kind = payload.get("kind")
    body = payload.get("address") if isinstance(payload.get("address"), dict) else payload
    try:
        actor = _actor(actor_user_id)
        profile = svc.set_address(actor, kind, body)
    except ProfileError as exc:
        return _err(exc)
    return _ok({"profile": profile})


def check_handle(actor_user_id: Any, candidate: Any) -> Tuple[int, dict]:
    """Pre-flight, so the handle editor can warn *before* the seller commits.

    Always 200, including when the handle is taken: "unavailable" is a legitimate
    answer to this question, not a failed request. The client reads ``available``.
    """
    try:
        actor = _actor(actor_user_id)
        return _ok({"handle": svc.check_handle(actor, candidate)})
    except ProfileError as exc:
        return _err(exc)


def publish(actor_user_id: Any) -> Tuple[int, dict]:
    """Make the saved state the published state — what Live Sync reports against."""
    try:
        return _ok({"profile": svc.publish(_actor(actor_user_id))})
    except ProfileError as exc:
        return _err(exc)


def sync_status(actor_user_id: Any) -> Tuple[int, dict]:
    """The Live Sync sheet: last publish, freshness, review-gated fields, readiness.

    Reports only the three states the server can honestly assert. ``saving``,
    ``offline`` and ``sync_failed`` describe the client's own request and are the
    client's to display; a server claiming "synced" to a phone with no signal would
    be worse than saying nothing.
    """
    try:
        actor = _actor(actor_user_id)
        profile = svc.owner_profile(actor)
    except ProfileError as exc:
        return _err(exc)
    completion = profile.get("completion") or {}
    locks = profile.get("locks") or {}
    return _ok({
        "sync": profile.get("sync"),
        "verification": profile.get("verification"),
        "published_at": profile.get("published_at"),
        "updated_at": profile.get("updated_at"),
        "review_protected_fields": locks.get("requires_review") or [],
        "blocked_fields": locks.get("blocked") or [],
        "completion": {
            "percent": completion.get("percent"),
            "completed": len(completion.get("completed") or []),
            "total": completion.get("total"),
        },
    })


# ============================================================================
# Buyer surface
# ============================================================================

def get_public_profile(user_id: Any, *, viewer_user_id: Any = None,
                       viewer_has_purchased: bool = False) -> Tuple[int, dict]:
    """The read-only buyer view, assembled from an allowlist in the service.

    ``viewer_has_purchased`` is supplied by ``bot.py``, which is the only layer that
    can answer it; this module never infers it. An owner fetching their own profile
    through this route gets the buyer view, not a privileged one — that is the point
    of the route.
    """
    try:
        subject = int(user_id)
    except (TypeError, ValueError):
        return _bad("Unknown business.", "not_found", 404)
    try:
        public = svc.public_profile(subject, viewer_has_purchased=bool(viewer_has_purchased))
    except ProfileError as exc:
        return _err(exc)
    if not public.get("business_name") and not public.get("handle"):
        return _bad("Unknown business.", "not_found", 404)
    return _ok({"profile": public,
                "is_self": _same_user(viewer_user_id, subject)})


def preview_profile(actor_user_id: Any) -> Tuple[int, dict]:
    """"View as buyer" — the owner's own public profile, at its strictest.

    ``viewer_has_purchased`` is pinned to ``False``: a preview that showed the owner
    the post-purchase view would show them contact details most visitors will never
    see, which is the flattering answer rather than the true one.

    ``preview`` is returned so the client can render the banner and disable the
    owner-unsafe actions (message, follow, buy) from server state rather than from a
    navigation param that a deep link could omit.
    """
    try:
        actor = _actor(actor_user_id)
        public = svc.public_profile(actor, viewer_has_purchased=False)
    except ProfileError as exc:
        return _err(exc)
    return _ok({
        "profile": public,
        "preview": {
            "active": True,
            "title": "Buyer preview",
            "subtitle": "This is how your public business profile appears.",
            "exit_label": "Exit preview",
            # Named so the client disables rather than silently no-ops them: a
            # button that looks live and does nothing teaches the owner the wrong
            # thing about their own shop.
            "simulated_actions": ["message", "follow", "buy", "share", "report"],
        },
    })


def _same_user(a: Any, b: Any) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


# ============================================================================
# Vocabularies
# ============================================================================

def vocabularies() -> Tuple[int, dict]:
    """The pickers, served rather than hard-coded in the app.

    ``seller_type`` is absent on purpose. It is a classification a reviewer applied to
    the account and it lives in ``seller_lifecycle``; offering it here is how
    "Individual" came to be printed where a buyer expects a business category.
    """
    return _ok({"vocabularies": {
        "business_categories": [
            {"value": value, "label": svc.BUSINESS_CATEGORY_LABELS.get(value, value)}
            for value in BUSINESS_CATEGORIES
        ],
        "contact_visibility": [
            {"value": "private", "label": "Private"},
            {"value": "after_purchase", "label": "Visible after purchase"},
            {"value": "public", "label": "Visible to all buyers"},
        ],
        "preferred_contact": list(PREFERRED_CONTACT),
        "hours_modes": [
            {"value": "unset", "label": "Not set"},
            {"value": "weekly", "label": "Weekly hours"},
            {"value": "by_appointment", "label": "By appointment"},
            {"value": "temporarily_closed", "label": "Temporarily closed"},
        ],
        "weekdays": [{"value": day, "label": svc.WEEKDAY_LABELS[day]} for day in WEEKDAYS],
        "link_kinds": list(LINK_KINDS),
        "address_kinds": list(ADDRESS_KINDS),
        "verification_states": list(VERIFICATION_STATES),
        "writable_fields": sorted(WRITABLE_FIELDS),
    }})
