"""Who may be banned from a Live, by whom, and what a ban actually denies.

``pulse_live_moderation`` shipped with a complete enforcement side and no way
to write to it. Six authorization sites call ``pulse_live_user_is_blocked``
-- the canonical audience gate, the replay/state read, the co-host request,
the join-status projection, the guest invite and the invite answer -- and all
six consulted a table that nothing could populate. The check was real, the
authority behind it did not exist, and production held zero rows. A reviewer
reading ``bot.py`` would reasonably conclude live bans were enforced.

This module is the missing authority. It is deliberately the *smallest* thing
that makes those six readers true.

Scope: one Live session
-----------------------
A ban is keyed ``(live_id, target_user_id)``. That is read off the existing
predicate, not guessed from the table name: the shipped query filters on
``live_id`` and ``target_user_id`` together, so the unit of moderation is a
single broadcast. Banning someone does not follow them to the host's next
Live, and nothing here creates a platform-wide ban. If a host wants a durable
break with an account, the social block (``blocked_users``) is that control
and it already exists.

Ban is not a social block
-------------------------
``pulse_live_viewer_authorized`` checks them separately and reports distinct
reasons (``live_blocked`` vs ``social_blocked``). Either one denies access;
neither implies the other. A live ban must never write ``blocked_users`` --
"get out of my stream" and "I never want to hear from this person again" are
different user intents, and silently upgrading the first to the second would
be a privacy decision the moderator did not make.

Current state is EXISTS, not a row count
----------------------------------------
The canonical rule: a target is banned from a Live when **at least one** row
exists with a ban-flavoured ``action`` and ``status='active'``. Re-banning an
already-banned viewer writes nothing and reports ``already_active``; unban
clears *every* active row for the pair. So N active rows and 1 active row mean
the same thing and unban collapses both -- repeated bans cannot accumulate
into a state that one unban fails to clear.

Reversal keeps the history. Unban flips ``status`` to ``reversed`` rather than
deleting, and every transition is additionally recorded in
``pulse_live_audit_logs`` by the caller, which is where "who did this and
when" lives.

Reading it cannot quietly answer "not banned"
---------------------------------------------
:func:`is_banned` fails **closed** on a read error: if the authority cannot be
consulted, the protected action is denied and the failure is logged. The
sentinel mission found a profile block check that returned ALLOW on exactly
this path, and this table now guards live access on the same shape of query.

The one exception is a missing table, which returns "not banned" and logs at
ERROR. That asymmetry is argued rather than assumed: ``init_db()`` creates
this table unconditionally on every boot and production has it, so "the table
is gone" is unreachable in a real deployment -- failing closed there would buy
no production safety while denying all live access in any partial harness. The
guarantee that the table keeps existing is enforced statically by
``tests/protection/test_live_moderation_authority.py`` instead, which is the
place that can actually prove it.
"""

from __future__ import annotations

import logging
from datetime import datetime

LOGGER = logging.getLogger(__name__)

#: The two transitions a moderator may request. A closed set on purpose: an
#: open-ended action string reaching a security table is how a vocabulary
#: becomes an attack surface.
ACTION_BAN = "ban"
ACTION_UNBAN = "unban"
MODERATION_ACTIONS = (ACTION_BAN, ACTION_UNBAN)

STATUS_ACTIVE = "active"
STATUS_REVERSED = "reversed"

#: Spellings the shipped reader already treats as a ban. We *write* only
#: ``ban``; we must still *read* all four, because the enforcement predicate
#: has accepted them since before this module existed and narrowing it now
#: would silently un-ban anything written by hand or by a future caller.
BAN_ACTIONS = ("block", "blocked", "ban", "banned")

_BAN_ACTION_SQL = "('block', 'blocked', 'ban', 'banned')"

#: Longest stored moderator note. Private -- see :func:`active_ban`.
REASON_MAX = 300


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _is_missing_table(exc):
    """SQLite and Postgres phrase an absent relation differently."""
    text = str(exc).lower()
    return "no such table" in text or ("does not exist" in text and "relation" in text)


def is_banned(cur, live_id, target_user_id):
    """Is ``target_user_id`` banned from Live ``live_id``?

    Fails closed. A read failure returns ``True`` (treat as banned) and logs
    ``LIVE_MODERATION_READ_FAILED``, because the alternative -- answering "not
    banned" when the authority could not be read -- is the precise defect the
    database contract sentinel was built to find.
    """
    live_id = _int(live_id)
    target_user_id = _int(target_user_id)
    if not live_id or not target_user_id:
        return False
    try:
        cur.execute(
            "SELECT 1 FROM pulse_live_moderation "
            "WHERE live_id=? AND target_user_id=? AND status='active' "
            f"  AND LOWER(COALESCE(action,'')) IN {_BAN_ACTION_SQL} "
            "LIMIT 1",
            (live_id, target_user_id),
        )
        return bool(cur.fetchone())
    except Exception as exc:
        if _is_missing_table(exc):
            LOGGER.error(
                "LIVE_MODERATION_TABLE_MISSING live_id=%s -- pulse_live_moderation "
                "is absent, so no live ban can be enforced in this deployment",
                live_id,
            )
            return False
        LOGGER.warning(
            "LIVE_MODERATION_READ_FAILED live_id=%s target=%s error=%s detail=%s "
            "-- denying the protected action",
            live_id, target_user_id, exc.__class__.__name__, exc,
        )
        return True


def active_ban(cur, live_id, target_user_id):
    """The active ban row for a pair, or ``{}``.

    Includes ``reason``, which is private moderator metadata. Callers must not
    return it to the banned account -- see :func:`public_denial_message`.
    """
    live_id = _int(live_id)
    target_user_id = _int(target_user_id)
    if not live_id or not target_user_id:
        return {}
    cur.execute(
        "SELECT id, live_id, moderator_user_id, target_user_id, action, reason, "
        "       status, created_at, updated_at "
        "FROM pulse_live_moderation "
        "WHERE live_id=? AND target_user_id=? AND status='active' "
        f"  AND LOWER(COALESCE(action,'')) IN {_BAN_ACTION_SQL} "
        "ORDER BY id DESC LIMIT 1",
        (live_id, target_user_id),
    )
    return _row_to_dict(cur.fetchone())


def list_active_bans(cur, live_id):
    """Every active ban on a Live, newest first. Moderator-only data."""
    live_id = _int(live_id)
    if not live_id:
        return []
    cur.execute(
        "SELECT id, live_id, moderator_user_id, target_user_id, action, reason, "
        "       status, created_at, updated_at "
        "FROM pulse_live_moderation "
        "WHERE live_id=? AND status='active' "
        f"  AND LOWER(COALESCE(action,'')) IN {_BAN_ACTION_SQL} "
        "ORDER BY id DESC LIMIT 200",
        (live_id,),
    )
    return [_row_to_dict(row) for row in (cur.fetchall() or [])]


def ban(cur, live_id, moderator_user_id, target_user_id, reason=""):
    """Record a ban. Idempotent.

    Returns ``{"status": "banned"|"already_active", "ban": {...}}``. Writing a
    second active row for a pair is not an error and not a new row -- the
    caller gets ``already_active`` and the existing row.

    This performs **no authorization**. :func:`authorize` owns that, and the
    route must call it first; keeping them separate means the authorization
    rules are testable without a database write and the writer cannot grow its
    own second opinion about who may use it.
    """
    live_id = _int(live_id)
    target_user_id = _int(target_user_id)
    moderator_user_id = _int(moderator_user_id)
    existing = active_ban(cur, live_id, target_user_id)
    if existing:
        return {"status": "already_active", "ban": existing}
    now = _now()
    cur.execute(
        "INSERT INTO pulse_live_moderation "
        "(live_id, moderator_user_id, target_user_id, action, reason, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (live_id, moderator_user_id, target_user_id, ACTION_BAN,
         normalize_reason(reason), STATUS_ACTIVE, now, now),
    )
    return {"status": "banned", "ban": active_ban(cur, live_id, target_user_id)}


def unban(cur, live_id, moderator_user_id, target_user_id):
    """Reverse every active ban for a pair. Idempotent.

    Returns ``{"status": "unbanned"|"not_banned", "cleared": n}``. Clearing
    *all* active rows is what makes the EXISTS state rule safe under a race:
    if two moderators banned concurrently and both rows landed, one unban
    still restores access.
    """
    live_id = _int(live_id)
    target_user_id = _int(target_user_id)
    if not live_id or not target_user_id:
        return {"status": "not_banned", "cleared": 0}
    if not active_ban(cur, live_id, target_user_id):
        return {"status": "not_banned", "cleared": 0}
    cur.execute(
        "UPDATE pulse_live_moderation SET status=?, updated_at=? "
        "WHERE live_id=? AND target_user_id=? AND status='active' "
        f"  AND LOWER(COALESCE(action,'')) IN {_BAN_ACTION_SQL}",
        (STATUS_REVERSED, _now(), live_id, target_user_id),
    )
    cleared = int(getattr(cur, "rowcount", 0) or 0)
    return {"status": "unbanned", "cleared": max(cleared, 1),
            "moderator_user_id": _int(moderator_user_id)}


def authorize(actor_role, *, actor_user_id, target_user_id, host_user_id,
              can_moderate):
    """May this actor ban/unban this target on this Live?

    Returns ``(True, "")`` or ``(False, reason_code)``. Pure: every input is
    already resolved from canonical server state by the caller, so nothing
    here can be influenced by a client-supplied role. There is deliberately no
    separate ``is_host`` flag -- host-ness reaches this function only as
    ``actor_role``, so there is exactly one place a caller can get the actor's
    authority wrong instead of two that could disagree.

    The rules, and why each exists:

    * ``can_moderate(actor_role)`` -- reuses the single live permission table
      in ``services/live_participants.py`` rather than a second inline notion
      of authority. Host, co-host and platform admin qualify; guests and
      audience do not. Resolving the role from the actor's *active guest row
      on this Live* is what prevents "I moderate Live A, therefore Live B".
    * A moderator may not ban the host. A co-host runs the room; they do not
      get to evict its owner.
    * A moderator may not ban themselves, which would otherwise let a host
      lock themselves out of their own broadcast through an ordinary button.
    """
    actor_user_id = _int(actor_user_id)
    target_user_id = _int(target_user_id)
    host_user_id = _int(host_user_id)
    if not actor_user_id or not target_user_id:
        return False, "invalid_identity"
    if not can_moderate(actor_role):
        return False, "not_a_moderator"
    if target_user_id == host_user_id:
        return False, "cannot_moderate_host"
    if target_user_id == actor_user_id:
        return False, "cannot_moderate_self"
    return True, ""


def public_denial_message():
    """What a banned account is told.

    Deliberately says nothing about who acted, why, when, or that a moderation
    record exists. ``reason`` is internal trust & safety metadata; leaking it
    would turn a moderation note into a message delivered to its subject.
    """
    return "You're no longer able to join this live."


def normalize_reason(reason):
    """Sanitise and bound a moderator note before it is stored."""
    text = str(reason or "").strip()
    text = text.replace("\r", " ").replace("\n", " ")
    return text[:REASON_MAX]


def _row_to_dict(row):
    if not row:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        pass
    keys = ("id", "live_id", "moderator_user_id", "target_user_id", "action",
            "reason", "status", "created_at", "updated_at")
    try:
        return {key: row[index] for index, key in enumerate(keys)}
    except Exception:
        return {}


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
