"""Who may remove a song, and what "remove" is allowed to mean.

Two separate problems live here.

**Reaching the check at all.** PulseSoc has two identity tables. ``users`` rows
authenticate by cookie or bearer token through ``bot.account_user_id``; that is
what the native app and the website use. ``admin_users`` rows authenticate
through ``session["admin_user_id"]``, which is written in exactly one place --
the web admin login form. Every ``/api/admin/...`` route resolves the caller
with ``admin_current_user()``, so a phone-authenticated owner is rejected with
401 before any permission is consulted. ``resolve_actor`` accepts either leg:
an existing admin session, or an account id matched against the explicit
``admin_users.account_user_id`` link.

The link is a stored column, never anything the request supplies. Nothing in a
body, header, or query string steers the lookup, so a user cannot claim to be
the owner, and an account with no linked ``admin_users`` row simply has no
admin identity to check permissions against.

**Deciding.** Once an admin identity exists the answer comes from the caller's
``permission_check`` -- in production ``bot.admin_has_permission``, which
resolves ``ROLE_FALLBACK_PERMISSIONS`` plus the ``role_permissions`` /
``admin_role_permissions`` / ``admin_user_roles`` tables. The ``music.*``
permissions below are ordinary named permissions: ``owner`` and ``super_admin``
hold the ``"*"`` wildcard and therefore hold them all, and every other role
holds one only if someone granted it. In particular ``pulse.moderate`` -- which
guards the pre-existing ``/api/admin/pulse/music/<id>/remove`` -- grants none of
them.

No email address, username, user id or device id appears in any decision.

**Takedown is not deletion.** The states below are ordered by how much they
destroy, and the transitions are deliberately narrow: reaching ``PURGED``
requires three separate authenticated calls (schedule, then a step-up, then the
purge itself). ``PURGED`` is terminal. The dependency here is physical: the
audio lives in a public R2 bucket behind a CDN that was told the object is
immutable for a year, so taking a track down stops the server handing the URL
out but does not reach a client that already has it. Only deleting the object
does, which is what ``QUARANTINED`` exists for -- a copyright or malware
takedown should quarantine, not merely take down.

This module deliberately imports nothing from ``bot``: it is given the session
admin, the account id, and the permission check. That keeps it free of the
import cycle and lets the tests drive it without a request context.
"""

MUSIC_PERMISSIONS = frozenset({
    "music.view_all",
    "music.moderate",
    "music.takedown",
    "music.restore",
    "music.purge",
    "music.manage_rights",
})

STATE_ACTIVE = "ACTIVE"
STATE_TAKEN_DOWN = "TAKEN_DOWN"
STATE_QUARANTINED = "QUARANTINED"
STATE_PURGE_PENDING = "PURGE_PENDING"
STATE_PURGED = "PURGED"

LIFECYCLE_STATES = (
    STATE_ACTIVE,
    STATE_TAKEN_DOWN,
    STATE_QUARANTINED,
    STATE_PURGE_PENDING,
    STATE_PURGED,
)

# States in which a track may still be served, attached, or discovered. Written
# as an allowlist rather than a denylist so a state added later is unavailable
# until someone decides otherwise.
SERVABLE_STATES = frozenset({STATE_ACTIVE})

REASON_CODES = (
    "COPYRIGHT",
    "LICENSING_EXPIRED",
    "POLICY_VIOLATION",
    "UNAUTHORIZED_UPLOAD",
    "DUPLICATE",
    "MALWARE_OR_UNSAFE_FILE",
    "PRIVACY_REQUEST",
    "UPLOADER_REQUEST",
    "OWNER_DECISION",
    "OTHER",
)

# Reason codes whose meaning is not carried by the code itself. A free-text note
# is required for these, and for every purge regardless of code.
REASON_CODES_REQUIRING_NOTE = frozenset({"OTHER"})

ACTION_TAKEDOWN = "takedown"
ACTION_QUARANTINE = "quarantine"
ACTION_RESTORE = "restore"
ACTION_SCHEDULE_PURGE = "schedule_purge"
ACTION_CANCEL_PURGE = "cancel_purge"
ACTION_PURGE = "purge"

ACTION_PERMISSIONS = {
    ACTION_TAKEDOWN: "music.takedown",
    ACTION_QUARANTINE: "music.takedown",
    ACTION_RESTORE: "music.restore",
    ACTION_SCHEDULE_PURGE: "music.purge",
    ACTION_CANCEL_PURGE: "music.purge",
    ACTION_PURGE: "music.purge",
}

# (action, from_state) -> to_state. Absence means the transition is refused.
# `PURGED` appears only as a destination: the bytes are gone, so no action can
# lead out of it.
TRANSITIONS = {
    (ACTION_TAKEDOWN, STATE_ACTIVE): STATE_TAKEN_DOWN,
    (ACTION_TAKEDOWN, STATE_TAKEN_DOWN): STATE_TAKEN_DOWN,
    (ACTION_QUARANTINE, STATE_ACTIVE): STATE_QUARANTINED,
    (ACTION_QUARANTINE, STATE_TAKEN_DOWN): STATE_QUARANTINED,
    (ACTION_QUARANTINE, STATE_QUARANTINED): STATE_QUARANTINED,
    (ACTION_RESTORE, STATE_TAKEN_DOWN): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_QUARANTINED): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_PURGE_PENDING): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_ACTIVE): STATE_ACTIVE,
    (ACTION_SCHEDULE_PURGE, STATE_TAKEN_DOWN): STATE_PURGE_PENDING,
    (ACTION_SCHEDULE_PURGE, STATE_QUARANTINED): STATE_PURGE_PENDING,
    (ACTION_SCHEDULE_PURGE, STATE_PURGE_PENDING): STATE_PURGE_PENDING,
    (ACTION_CANCEL_PURGE, STATE_PURGE_PENDING): STATE_TAKEN_DOWN,
    (ACTION_CANCEL_PURGE, STATE_TAKEN_DOWN): STATE_TAKEN_DOWN,
    (ACTION_PURGE, STATE_PURGE_PENDING): STATE_PURGED,
}

# Actions that destroy bytes. These require a step-up regardless of permission.
DESTRUCTIVE_ACTIONS = frozenset({ACTION_PURGE})

STEP_UP_TTL_SECONDS = 300


class AuthorityError(Exception):
    """A refusal with a stable machine-readable code.

    ``error_code`` is what the clients read -- ``pulseApi`` in the native app
    ignores ``code`` and reads ``error_code``, so anything that only set ``code``
    would collapse every distinct refusal into a generic failure.
    """

    def __init__(self, error_code, message, status=400):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


def normalize_state(value):
    """Map a stored ``lifecycle_state`` onto a known state.

    Unrecognised and empty values become ``ACTIVE`` only because that is what a
    row written before this column existed means; rows that were removed through
    the legacy admin route are backfilled to ``TAKEN_DOWN`` in ``init_db`` rather
    than being inferred here, so this default never resurrects a removed track.
    """
    state = str(value or "").strip().upper()
    return state if state in LIFECYCLE_STATES else STATE_ACTIVE


def is_servable(track):
    """Whether a track row may be handed to a player, search result or attach.

    Reads *both* writers. The lifecycle column is the new one, but the older
    admin review route (``/api/admin/pulse/music/<id>/remove``) still stamps only
    ``removed_at`` / ``safety_status``, so a track removed through that route
    after this column existed would have no lifecycle state to speak for it.
    Honouring the legacy trio here keeps one definition of "may this play"
    instead of letting the two writers disagree.
    """
    row = track or {}
    if normalize_state(row.get("lifecycle_state")) not in SERVABLE_STATES:
        return False
    if str(row.get("removed_at") or "").strip():
        return False
    if str(row.get("safety_status") or "").strip().lower() in {"removed", "blocked", "rejected"}:
        return False
    return True


def normalize_reason_code(value):
    code = str(value or "").strip().upper()
    if code not in REASON_CODES:
        raise AuthorityError(
            "music_reason_code_invalid",
            "Choose a removal reason: " + ", ".join(REASON_CODES) + ".",
            400,
        )
    return code


def validate_reason(action, reason_code, reason_note):
    """Return ``(code, note)`` or raise.

    A purge always needs a note: the code alone records *why a category* applied,
    and the one irreversible action in this module should carry a sentence a
    human wrote.
    """
    code = normalize_reason_code(reason_code)
    note = str(reason_note or "").strip()[:2000]
    if not note and (code in REASON_CODES_REQUIRING_NOTE or action in DESTRUCTIVE_ACTIONS):
        raise AuthorityError(
            "music_reason_note_required",
            "Add a short note explaining this decision.",
            400,
        )
    return code, note


def resolve_actor(session_admin, account_user_id, admin_by_account_user_id):
    """Resolve the acting admin identity from whichever leg authenticated.

    ``session_admin`` is ``bot.admin_current_user()`` -- already the result of a
    validated web admin session. ``account_user_id`` is ``bot.account_user_id()``
    -- an id proven by a signed bearer token or a session cookie, never a value
    read off the request body. ``admin_by_account_user_id`` looks the linked
    ``admin_users`` row up by that id.

    Returns the admin row, or ``None`` when the caller has no admin identity.
    The web session wins when both are present so that an admin working in the
    browser is never silently re-identified as someone else.
    """
    if session_admin:
        return dict(session_admin)
    if not account_user_id:
        return None
    linked = admin_by_account_user_id(int(account_user_id))
    if not linked:
        return None
    linked = dict(linked)
    if str(linked.get("status") or "").strip().lower() != "active":
        return None
    return linked


def require_permission(actor, permission, permission_check):
    """Raise unless ``actor`` holds ``permission``.

    The two refusals are deliberately different: no admin identity is a 401
    (authenticate as someone who could act), an identity without the permission
    is a 403 (you are known and you may not). Collapsing them would tell an
    ordinary user that the endpoint exists but say nothing useful to a real
    admin who is missing a grant.
    """
    if permission not in MUSIC_PERMISSIONS:
        raise AuthorityError("music_permission_unknown", "Unknown music permission.", 500)
    if not actor:
        raise AuthorityError(
            "music_authority_required",
            "Sign in with an account that has music moderation authority.",
            401,
        )
    if not permission_check(actor, permission):
        raise AuthorityError(
            "music_permission_denied",
            "This account does not hold the %s permission." % permission,
            403,
        )
    return actor


def plan_transition(action, current_state, *, expected_state=None, legal_hold=False):
    """Decide what ``action`` does to a track in ``current_state``.

    Returns ``(new_state, changed)``. ``changed`` is False when the action was
    already applied -- the caller answers 200 with ``changed: false`` rather than
    erroring, so a client that retries a timed-out request does not see a failure
    for work that succeeded.

    ``expected_state`` is the caller's view of the world. When it disagrees with
    the stored state the transition is refused with 409: two moderators acting on
    the same track from stale screens must not silently overwrite each other.
    """
    current = normalize_state(current_state)
    if expected_state:
        expected = normalize_state(expected_state)
        if expected != current:
            raise AuthorityError(
                "music_state_conflict",
                "This track is now %s, not %s. Reload and try again." % (current, expected),
                409,
            )
    if legal_hold and action in DESTRUCTIVE_ACTIONS:
        raise AuthorityError(
            "music_legal_hold",
            "This track is under legal hold and cannot be purged. Release the hold first.",
            409,
        )
    key = (action, current)
    if key not in TRANSITIONS:
        raise AuthorityError(
            "music_transition_refused",
            "Cannot %s a track that is %s." % (action.replace("_", " "), current),
            409,
        )
    new_state = TRANSITIONS[key]
    return new_state, new_state != current


def legacy_columns_for_state(state, *, now, actor_admin_id):
    """The pre-existing ``active`` / ``safety_status`` / ``removed_at`` trio.

    Roughly ten read paths already filter on these -- reel attach, trending
    sounds, search, the artist page, ``music_service`` -- and the admin review
    dashboard counts on them. Writing them in the same statement as
    ``lifecycle_state`` is what makes a takedown take effect everywhere without
    editing every query, and what keeps a future reader from finding two
    disagreeing sources of truth.
    """
    state = normalize_state(state)
    if state == STATE_ACTIVE:
        return {
            "active": 1,
            "approved_by_admin": 1,
            "safety_status": "approved",
            "removed_at": "",
            "removed_by_admin": None,
        }
    return {
        "active": 0,
        "approved_by_admin": 0,
        "safety_status": "removed",
        "removed_at": now,
        "removed_by_admin": actor_admin_id or 0,
    }


def actor_role(actor):
    return str((actor or {}).get("role") or "").strip().lower()


def unavailable_audio_payload(reason_state=None):
    """What a player gets in place of a track it may no longer have.

    Every url is blank and ``audio_unavailable`` is set, so the client shows
    "Audio unavailable" instead of spinning on a dead URL. Title and artist are
    blank too: a removed track's metadata is part of what a copyright or privacy
    takedown is removing. The caller keeps the content's own fields -- video,
    caption, engagement -- and must not flip ``original_audio_muted``, because
    unmuting would publish audio the creator chose to silence.
    """
    return {
        "audio_unavailable": True,
        "audio_unavailable_state": normalize_state(reason_state) if reason_state else STATE_TAKEN_DOWN,
        "id": 0,
        "track_id": 0,
        "title": "",
        "artist": "",
        "attached_audio_url": "",
        "audio_url": "",
        "preview_url": "",
        "waveform": "",
    }
