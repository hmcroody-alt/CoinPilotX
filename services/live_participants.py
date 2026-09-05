"""Canonical participant model for PulseSoc multi-guest Live.

A multi-guest Live is a BROADCAST, not a group call. One Live session owns one
Agora channel; a small, bounded set of publishers stand on stage inside it while
an unbounded audience subscribes. Six publishers and a hundred thousand viewers
are still one broadcast with six people on stage — not a 100,006-member call.

This module is the single place that answers four questions the rest of the
system kept answering for itself:

  * What roles exist, and what may each one actually do?
  * How many publishers may stand on stage at once?
  * What Agora RTC uid does a PulseSoc user occupy?
  * Is multi-guest Live switched on for this deployment?

Everything here is pure. No database, no network, no Flask. That keeps it
testable without a running app, and it means the routes in ``bot.py`` and the
token minter in ``services/pulsesoc_communications_engine.py`` can share one
answer instead of drifting apart.

Nothing in this module opens a microphone, a camera, or an audio session. It
describes authority; it does not exercise it.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------
#
# Four roles, and they are not interchangeable. Before this module the server
# collapsed "cohost" and "guest" into one token role, so a co-host was merely a
# guest wearing a different label. They are separated here because moderation
# authority is the whole point of the distinction: a co-host helps run the
# room, a guest is a visitor on stage.

ROLE_HOST = "host"
ROLE_COHOST = "cohost"
ROLE_GUEST = "guest"
ROLE_AUDIENCE = "audience"

#: Every role the Live path recognises, in descending authority order.
LIVE_ROLES = (ROLE_HOST, ROLE_COHOST, ROLE_GUEST, ROLE_AUDIENCE)

#: Roles permitted to publish media. Membership here is what ultimately decides
#: whether Agora mints a publisher token, so this tuple is load-bearing.
PUBLISHING_ROLES = (ROLE_HOST, ROLE_COHOST, ROLE_GUEST)

# Historical spellings that have reached this code from clients, older rows and
# the legacy LiveKit era. Normalising in one place means callers never have to
# remember that "co-host", "publisher" and "creator" were all once in use.
_ROLE_ALIASES = {
    "co-host": ROLE_COHOST,
    "co_host": ROLE_COHOST,
    "cohost": ROLE_COHOST,
    "moderator": ROLE_COHOST,
    "host": ROLE_HOST,
    "creator": ROLE_HOST,
    "publisher": ROLE_HOST,
    "owner": ROLE_HOST,
    "guest": ROLE_GUEST,
    "speaker": ROLE_GUEST,
    "panelist": ROLE_GUEST,
    "audience": ROLE_AUDIENCE,
    "viewer": ROLE_AUDIENCE,
    "watcher": ROLE_AUDIENCE,
    "subscriber": ROLE_AUDIENCE,
}

_ROLE_LABELS = {
    ROLE_HOST: "Host",
    ROLE_COHOST: "Co-host",
    ROLE_GUEST: "Guest",
    ROLE_AUDIENCE: "Viewer",
}

# Permissions are declared per role rather than derived from an ordering, so
# that adding a capability to co-hosts later is a one-line edit that cannot
# accidentally leak downward to guests.
_ROLE_PERMISSIONS: Dict[str, Dict[str, bool]] = {
    ROLE_HOST: {
        "publish": True,
        "publish_camera": True,
        "publish_microphone": True,
        "invite_guests": True,
        "approve_requests": True,
        "remove_guests": True,
        "mute_others": True,
        "end_live": True,
        "leave_stage": False,
    },
    ROLE_COHOST: {
        # A co-host moderates but cannot end the broadcast. Ending a Live is
        # irreversible and belongs to whoever started it.
        "publish": True,
        "publish_camera": True,
        "publish_microphone": True,
        "invite_guests": True,
        "approve_requests": True,
        "remove_guests": True,
        "mute_others": True,
        "end_live": False,
        "leave_stage": True,
    },
    ROLE_GUEST: {
        "publish": True,
        "publish_camera": True,
        "publish_microphone": True,
        "invite_guests": False,
        "approve_requests": False,
        "remove_guests": False,
        "mute_others": False,
        "end_live": False,
        "leave_stage": True,
    },
    ROLE_AUDIENCE: {
        # Audience is subscribe-only. Every flag being False is the point: an
        # audience client has nothing to initialise, no camera to open and no
        # microphone to claim.
        "publish": False,
        "publish_camera": False,
        "publish_microphone": False,
        "invite_guests": False,
        "approve_requests": False,
        "remove_guests": False,
        "mute_others": False,
        "end_live": False,
        "leave_stage": False,
    },
}


def normalize_role(role: Any) -> str:
    """Return one of :data:`LIVE_ROLES`, defaulting to audience.

    Defaulting to audience rather than raising is deliberate. An unrecognised
    role should cost a caller their publishing rights, not crash the request —
    the failure mode we want is "you are a viewer", never "the Live broke".
    """

    key = str(role or "").strip().lower()
    if key in _ROLE_ALIASES:
        return _ROLE_ALIASES[key]
    if key in LIVE_ROLES:
        return key
    return ROLE_AUDIENCE


#: Roles are normalised internally to :data:`LIVE_ROLES`, but the wire has
#: carried "viewer" for the audience role since before this module existed and
#: shipped clients compare against it. The canonical name travels alongside as
#: ``canonical_role`` so newer clients can move across without a flag day.
_WIRE_ROLES = {ROLE_AUDIENCE: "viewer"}


def wire_role(role: Any) -> str:
    """Backwards-compatible role string for API responses."""

    normalized = normalize_role(role)
    return _WIRE_ROLES.get(normalized, normalized)


def role_label(role: Any) -> str:
    """Human-facing label for a role."""

    return _ROLE_LABELS[normalize_role(role)]


def role_permissions(role: Any) -> Dict[str, bool]:
    """Return a copy of the permission set for ``role``.

    A copy, so a caller mutating the result cannot quietly grant every future
    co-host the ability to end a Live.
    """

    return dict(_ROLE_PERMISSIONS[normalize_role(role)])


def can_publish(role: Any) -> bool:
    """Whether ``role`` may publish media into the Live channel."""

    return normalize_role(role) in PUBLISHING_ROLES


def can_moderate(role: Any) -> bool:
    """Whether ``role`` may remove or mute another participant."""

    return bool(role_permissions(role)["remove_guests"])


def can_end_live(role: Any) -> bool:
    """Whether ``role`` may end the broadcast for everyone.

    Only the host. A co-host leaving, a guest leaving, or a co-host removing the
    last guest must never end a Live.
    """

    return bool(role_permissions(role)["end_live"])


def publish_sources(role: Any) -> List[str]:
    """Media sources ``role`` is allowed to publish, for the token contract."""

    perms = role_permissions(role)
    sources: List[str] = []
    if perms["publish_microphone"]:
        sources.append("microphone")
    if perms["publish_camera"]:
        sources.append("camera")
    return sources


# --------------------------------------------------------------------------
# Stage capacity
# --------------------------------------------------------------------------
#
# The ceiling used to be the integer 12 embedded in a SQL string, which meant it
# could not be configured, could not be tested at another value, and could not
# be reported to a client. It is now one server-owned number.

#: Absolute ceiling. Above this a portrait stage cannot render a face large
#: enough to be worth showing, and an audience device is subscribing to more
#: video than it can decode. Configuration may lower this; nothing may raise it.
LIVE_MAX_GUESTS_HARD_CEILING = 12

#: Used when the environment says nothing.
LIVE_MAX_GUESTS_DEFAULT = 12

#: Pending request queue depth. Requests are cheap; publishers are not.
LIVE_MAX_PENDING_REQUESTS = 30

#: Minimum seconds between two request-to-join attempts by the same viewer on
#: the same Live. Without this a declined viewer can re-ask in a tight loop and
#: bury the host's backstage panel during a broadcast. Enforced server-side; the
#: client is told the remaining cooldown so it can disable the button honestly.
LIVE_REQUEST_COOLDOWN_SECONDS = 30

#: How long a host-issued invite stays actionable. An invite is a live social
#: gesture, not a standing offer — if the target has not answered within this
#: window the host has usually moved on, and a stale accept would drop someone
#: onto the stage unannounced.
LIVE_INVITE_TTL_SECONDS = 120


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        # A malformed limit must not take the Live path down, and must not
        # silently become unbounded. Fall back to the configured default.
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def max_guests() -> int:
    """Server-authoritative number of guests permitted on stage.

    Excludes the host. A value of 0 means guests are effectively disabled while
    the single-host Live path continues to work untouched.
    """

    value = _env_int("LIVE_MAX_GUESTS", LIVE_MAX_GUESTS_DEFAULT)
    if value < 0:
        return 0
    return min(value, LIVE_MAX_GUESTS_HARD_CEILING)


def max_publishers() -> int:
    """Total publishers on stage, host included."""

    return max_guests() + 1


def stage_is_full(active_guest_count: int) -> bool:
    """Whether the stage can accept another guest."""

    return int(active_guest_count or 0) >= max_guests()


def stage_capacity(active_guest_count: int) -> Dict[str, Any]:
    """Capacity snapshot for clients, so STAGE FULL is truthful rather than guessed.

    Clients previously had no way to know the limit, so a "stage full" message
    could only ever be a guess made after a failed request. This lets the UI
    state the real number before anyone asks.
    """

    used = max(0, int(active_guest_count or 0))
    limit = max_guests()
    return {
        "max_guests": limit,
        "max_publishers": limit + 1,
        "guests_active": used,
        "slots_available": max(0, limit - used),
        "stage_full": used >= limit,
    }


# --------------------------------------------------------------------------
# Feature flags
# --------------------------------------------------------------------------


def multi_guest_enabled() -> bool:
    """Master switch for multi-guest Live.

    With this off the Live path must behave exactly as it did before this work:
    a single host publishing to an audience. Nothing about single-host Live is
    conditional on this flag being on.

    **Defaults off.** It defaulted on until this commit, which meant multi-guest
    Live went live on deploy unless somebody remembered to set the variable
    false in the Railway environment — an opt-out for a feature whose physical
    audio validation has not been performed. The sibling flag
    ``PULSE_GROUP_CALLS_ENABLED`` defaults off for the same reason, and having
    the two disagree was an accident of authorship, not a decision.

    Defaulting off is also the honest posture for a flag whose failure mode is
    audible. A multi-guest stage cannot be proven working by tests: 369 green
    audio tests say nothing about whether three people heard each other. Until
    that validation is recorded in
    ``reports/realtime_audio_verified_baseline.md``, the deployment that gets
    the feature should be the one that asked for it.

    Turning it on is a single environment variable and needs no deploy, so the
    cost of the conservative default is one operator action; the cost of the
    permissive default is a silent rollout to production.
    """

    return _env_flag("MULTI_GUEST_LIVE_ENABLED", False)


def guest_requests_enabled() -> bool:
    """Whether audience members may request to join the stage.

    Separate from the master switch so a host can run an invite-only panel
    without disabling multi-guest entirely.
    """

    return _env_flag("LIVE_GUEST_REQUESTS_ENABLED", True) and multi_guest_enabled()


def live_feature_flags() -> Dict[str, Any]:
    """Flag snapshot for the client, so the app never hardcodes these."""

    return {
        "multi_guest_live_enabled": multi_guest_enabled(),
        "live_guest_requests_enabled": guest_requests_enabled(),
        "live_max_guests": max_guests(),
        "live_max_publishers": max_publishers(),
    }


# --------------------------------------------------------------------------
# RTC identity
# --------------------------------------------------------------------------

#: Agora numeric uids are unsigned 32-bit.
AGORA_UID_MAX = 0xFFFFFFFF


def rtc_uid(user_id: Any) -> int:
    """Agora RTC uid occupied by a PulseSoc user.

    The uid is the user id. That is a pre-existing protocol decision, not one
    made here, and it is mirrored rather than changed because the cloud
    recording service, the token minter and stored participant records all
    already depend on it.

    Three consequences are worth stating plainly, because they only become
    material once more than one person publishes:

    1. Every client in the channel learns each publisher's numeric uid, which is
       a PulseSoc user id. This is a small information leak that grows with the
       number of publishers.
    2. The uid is not scoped per Live, so uid history correlates across
       sessions.
    3. Agora treats a duplicate uid joining the same channel as a takeover and
       disconnects the first connection. PulseSoc therefore already has a
       one-device-per-Live policy, enforced by an abrupt kick rather than a
       graceful handoff.

    Changing the derivation is a protocol-level decision that would touch
    recording and token issuance together, so it is documented here and left
    alone.
    """

    uid = int(user_id or 0)
    if uid <= 0 or uid > AGORA_UID_MAX:
        raise ValueError("PulseSoc user id cannot be represented as an Agora numeric UID")
    return uid


def safe_rtc_uid(user_id: Any) -> int:
    """:func:`rtc_uid` that returns 0 instead of raising.

    For payload construction, where one unrepresentable id must not fail the
    whole roster response.
    """

    try:
        return rtc_uid(user_id)
    except (TypeError, ValueError):
        return 0


def resolve_uid_owner(uid: Any, participants: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the participant record that owns an Agora ``uid``.

    This is the server-side counterpart of the client registry. A component must
    never infer who a remote uid belongs to from arrival order or from any other
    positional accident; it looks the uid up in the roster the server sent.
    """

    try:
        needle = int(uid or 0)
    except (TypeError, ValueError):
        return None
    if needle <= 0:
        return None
    for participant in participants or []:
        if int((participant or {}).get("rtc_uid") or 0) == needle:
            return participant
    return None


# --------------------------------------------------------------------------
# Guest lifecycle
# --------------------------------------------------------------------------
#
# The statuses below already existed across various SQL string literals. Naming
# them means the roster query, the token check and the client state machine can
# no longer disagree about which ones count as "on stage".

#: Invited or approved, but not yet publishing. These occupy a stage slot —
#: otherwise a host could over-invite and overflow the stage on arrival.
GUEST_PENDING_STATUSES = ("accepted", "joining")

#: Actually on stage.
GUEST_LIVE_STATUSES = ("active", "joined", "publishing", "live")

#: Everything that holds a stage slot.
GUEST_ACTIVE_STATUSES = GUEST_PENDING_STATUSES + GUEST_LIVE_STATUSES

#: Terminal. A guest in one of these states must not be able to publish, and a
#: token refresh for them must fail.
GUEST_TERMINAL_STATUSES = ("left", "removed", "declined", "expired", "rejected")


def guest_is_on_stage(status: Any) -> bool:
    """Whether a guest row currently holds a stage slot."""

    return str(status or "").strip().lower() in GUEST_ACTIVE_STATUSES


def guest_is_publishing(status: Any) -> bool:
    """Whether a guest is expected to have live media."""

    return str(status or "").strip().lower() in GUEST_LIVE_STATUSES


def guest_status_sql_list() -> str:
    """Active statuses as a SQL literal list, for the roster queries."""

    return ",".join(f"'{status}'" for status in GUEST_ACTIVE_STATUSES)


# --------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------
#
# Two paths can put someone on stage: the audience asks (pull) or the host
# offers (push). They share one row and one lifecycle so a viewer can never end
# up holding both a pending request and a pending invite for the same Live.

#: Host has offered; the target has not answered.
INVITE_STATUS_PENDING = "invited"

#: Target accepted. From here the row behaves exactly like a host-approved
#: request, which is what lets the rest of the guest pipeline stay unchanged.
INVITE_STATUS_ACCEPTED = "accepted"

INVITE_STATUS_DECLINED = "declined"
INVITE_STATUS_EXPIRED = "expired"
INVITE_STATUS_CANCELLED = "cancelled"

#: Where a stage row came from. Kept explicit because the two origins carry
#: different consent: a request means the guest already opted in, an invite does
#: not, and the guest must confirm before any camera starts.
ORIGIN_REQUEST = "request"
ORIGIN_INVITE = "invite"

INVITE_ID_PREFIX = "inv"


def build_invite_id(live_id: Any, request_id: Any) -> str:
    """Stable, deterministic invite identifier.

    Derived from the row rather than randomly generated, so the same invite
    always presents the same id no matter how many times it is delivered. Push
    notification, realtime event, polled state and the accept call therefore all
    agree, and a client that receives the invite twice can discard the duplicate
    instead of showing two prompts for one offer.
    """

    live = max(0, _coerce_int(live_id))
    request = max(0, _coerce_int(request_id))
    if not live or not request:
        return ""
    return f"{INVITE_ID_PREFIX}-{live}-{request}"


def parse_invite_id(invite_id: Any) -> Optional[Dict[str, int]]:
    """Inverse of :func:`build_invite_id`, or ``None`` if malformed.

    Parsing rather than trusting means a forged or truncated id fails here
    instead of reaching a query.
    """

    parts = str(invite_id or "").strip().lower().split("-")
    if len(parts) != 3 or parts[0] != INVITE_ID_PREFIX:
        return None
    live = _coerce_int(parts[1])
    request = _coerce_int(parts[2])
    if live <= 0 or request <= 0:
        return None
    return {"live_id": live, "request_id": request}


def _coerce_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0
