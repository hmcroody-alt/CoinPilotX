"""What a viewer is allowed to see about a profile owner.

Profile OS destinations on the native app are all *about* one account: the
profile that was open when the tile was tapped. The client decides what to draw,
but it cannot be the thing that decides what is allowed — a hidden button is not
access control, and "fetch the private payload and hide it in the UI" is exactly
the failure this module exists to remove.

So the server answers the question once, next to the profile payload, and the
client renders against the answer. Every flag here is about *public or
viewer-authorised* content. Owner-only surfaces (payouts, security history,
verification documents, drafts) are not represented as a permission a visitor
could ever be granted; they live behind owner-scoped endpoints instead.

The relationship inputs are deliberately narrow: block state, follow edges, and
friendship. Anything richer belongs to the subsystem that owns it.
"""

from __future__ import annotations

# Deny-by-default. Every consumer starts here and opens flags explicitly, so a
# new key added to this dict is invisible to visitors until someone decides it
# should not be.
DENY_ALL = {
    "can_view_public_profile": False,
    "can_view_follower_content": False,
    "can_view_friend_content": False,
    "can_view_public_media": False,
    "can_view_public_music": False,
    "can_view_public_activity": False,
    "can_view_public_collections": False,
    "can_view_public_communities": False,
    "can_view_marketplace": False,
    "can_view_business": False,
    "can_view_events": False,
    "can_view_public_memories": False,
    "can_message": False,
    "can_report": False,
    "can_block": False,
}

# The owner of a profile sees everything on it. Message/report/block are false
# because an account cannot perform them against itself.
OWNER_PERMISSIONS = dict(
    DENY_ALL,
    **{
        "can_view_public_profile": True,
        "can_view_follower_content": True,
        "can_view_friend_content": True,
        "can_view_public_media": True,
        "can_view_public_music": True,
        "can_view_public_activity": True,
        "can_view_public_collections": True,
        "can_view_public_communities": True,
        "can_view_marketplace": True,
        "can_view_business": True,
        "can_view_events": True,
        "can_view_public_memories": True,
    },
)

# Statuses where the account is gone or withheld: nothing is viewable, and there
# is nothing left to report or block.
UNAVAILABLE_STATUSES = {"deleted", "deactivated", "disabled", "closed"}
# Statuses where the account still exists but is withheld from other users.
RESTRICTED_STATUSES = {"suspended", "restricted", "banned"}

# Public content flags, opened together once a viewer is allowed to see the
# profile at all. Grouped so a caller cannot accidentally open half of them.
PUBLIC_CONTENT_FLAGS = (
    "can_view_public_media",
    "can_view_public_music",
    "can_view_public_activity",
    "can_view_public_collections",
    "can_view_public_communities",
    "can_view_marketplace",
    "can_view_business",
    "can_view_events",
    "can_view_public_memories",
)


def viewer_permissions(cur, target_user_id, viewer_user_id, account=None):
    """Resolve what ``viewer_user_id`` may see about ``target_user_id``.

    ``account`` is the target's ``users`` row when the caller already has it,
    purely to avoid a second read; it is re-fetched when absent.

    Returns a plain dict of snake_case booleans, safe to embed in a JSON
    payload. Never raises for a missing account — an unknown profile is simply
    one nobody may see.
    """
    target_user_id = _int(target_user_id)
    viewer_user_id = _int(viewer_user_id)
    if not target_user_id:
        return dict(DENY_ALL)

    if target_user_id == viewer_user_id:
        return dict(OWNER_PERMISSIONS)

    if account is None:
        account = _fetch_account(cur, target_user_id)
    if not account:
        return dict(DENY_ALL)

    status = str(account.get("account_status") or account.get("status") or "active").strip().lower()
    if status in UNAVAILABLE_STATUSES:
        return dict(DENY_ALL)

    # A block in *either* direction closes the profile. Checking only "did the
    # owner block me" would let a viewer keep reading someone they themselves
    # blocked, which is not what a block means to either party.
    if _blocked_either_way(cur, target_user_id, viewer_user_id):
        # Still blockable/reportable: the viewer needs a route to manage or
        # escalate the relationship even when the content is closed.
        return dict(DENY_ALL, can_report=True, can_block=True)

    if status in RESTRICTED_STATUSES:
        return dict(DENY_ALL, can_report=True, can_block=True)

    permissions = dict(DENY_ALL, can_report=True, can_block=True)

    visibility = str(account.get("profile_visibility") or "public").strip().lower()
    follows = _follows(cur, viewer_user_id, target_user_id)
    friends = _friends(cur, viewer_user_id, target_user_id)

    if visibility == "private":
        # A private profile is readable only by accounts it has accepted. The
        # shell (name, avatar) is still shown so the viewer knows whose profile
        # they landed on, but no content flags open.
        permissions["can_view_public_profile"] = bool(friends)
        if friends:
            permissions["can_view_friend_content"] = True
            permissions["can_view_follower_content"] = True
            for flag in PUBLIC_CONTENT_FLAGS:
                permissions[flag] = True
        permissions["can_message"] = bool(friends)
        return permissions

    permissions["can_view_public_profile"] = True
    permissions["can_view_follower_content"] = bool(follows or friends)
    permissions["can_view_friend_content"] = bool(friends)
    for flag in PUBLIC_CONTENT_FLAGS:
        permissions[flag] = True

    permissions["can_message"] = _can_message(account, follows, friends)
    return permissions


def _can_message(account, follows, friends):
    """Honour the owner's inbox preference when the column exists.

    ``everyone`` is the default because that is the historical behaviour; the
    stricter values only bite for accounts that opted into them.
    """
    preference = str(account.get("message_privacy") or account.get("dm_privacy") or "everyone").strip().lower()
    if preference in {"nobody", "none", "off"}:
        return False
    if preference in {"friends", "friends_only"}:
        return bool(friends)
    if preference in {"followers", "following"}:
        return bool(follows or friends)
    return True


def _fetch_account(cur, target_user_id):
    try:
        cur.execute("SELECT * FROM users WHERE user_id=? LIMIT 1", (target_user_id,))
        return dict(cur.fetchone() or {})
    except Exception:
        return {}


def _blocked_either_way(cur, target_user_id, viewer_user_id):
    if not viewer_user_id:
        return False
    return _exists(
        cur,
        "SELECT 1 FROM blocked_users WHERE (blocker_user_id=? AND blocked_user_id=?) "
        "OR (blocker_user_id=? AND blocked_user_id=?) LIMIT 1",
        (target_user_id, viewer_user_id, viewer_user_id, target_user_id),
    )


def _follows(cur, viewer_user_id, target_user_id):
    if not viewer_user_id:
        return False
    return _exists(
        cur,
        "SELECT 1 FROM pulse_follows WHERE follower_user_id=? AND followed_user_id=? LIMIT 1",
        (viewer_user_id, target_user_id),
    )


def _friends(cur, viewer_user_id, target_user_id):
    """Accepted friendship, checked against both tables the app writes to.

    ``pulse_friendships`` and ``pulse_friends`` coexist in this schema (see
    ``pulse_friend_graph``), so a single-table check would silently misreport
    friendship for accounts written through the other one.
    """
    if not viewer_user_id:
        return False
    if _exists(
        cur,
        "SELECT 1 FROM pulse_friendships WHERE user_id=? AND friend_user_id=? LIMIT 1",
        (viewer_user_id, target_user_id),
    ):
        return True
    return _exists(
        cur,
        "SELECT 1 FROM pulse_friends WHERE user_id=? AND friend_user_id=? "
        "AND COALESCE(status,'active')='active' LIMIT 1",
        (viewer_user_id, target_user_id),
    )


def _exists(cur, sql, params):
    """Run an existence check, treating a missing table as "no".

    Optional subsystems create their tables lazily here, so a relationship table
    that has not been provisioned yet must read as absent-relationship rather
    than take down the profile payload.
    """
    try:
        cur.execute(sql, params)
        return bool(cur.fetchone())
    except Exception:
        return False


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
