"""Canonical block / unblock authority for PulseSoc.

Why this module exists
----------------------

Blocking was implemented four times, and the four did not agree:

* ``services/pulse_settings_routes._mutate_relationship`` — a bare
  insert/delete on ``blocked_users``. No notification, no audit.
* ``bot.api_pulse_block_user`` (``POST /api/pulse/block``) — wrote
  ``blocked_users``, *also* filed an open row in ``pulse_reports``, and emitted
  two safety notifications.
* ``bot.api_messages_block`` (``POST /api/messages/block``) — wrote
  ``blocked_users`` and emitted one safety notification, filed no report.
* ``pulse_communications_v2.service.block_user`` — wrote a different table
  entirely, ``comm_v2_blocks``, with a soft ``status`` column.

That last one matters more than it looks. ``services/presence_service.py``
reads **both** ``comm_v2_blocks`` and ``blocked_users`` when deciding whether to
leak presence, so a block placed through Settings and a block placed through
Messenger produced different enforcement. Whether you disappeared from someone's
presence depended on which screen you had blocked them from. That is not a
preference; it is a safety control that worked or did not work by accident of
navigation.

The contract this module settles on
-----------------------------------

One block writes **both** tables, always emits the safety notification, and
**never** files a moderation report.

Writing both tables is the whole point: a block should mean the same thing to
every reader, and there are two readers. Always notifying makes the act visible
in the user's own safety history no matter where they performed it. Not filing a
report is the one behaviour deliberately *removed* rather than unioned —
``/api/pulse/block`` used to open a moderation case on every block, which
conflates "I do not want to see this person" with "I am accusing this person",
files unreviewed cases against people whose only offence was being muted out of
a feed, and makes the moderation queue's open count meaningless. Reporting
remains available and explicit: ``pulse_feed_engine.report_content``.

Blocking is symmetric in enforcement and asymmetric in record. ``is_blocked``
below is bidirectional — either party having blocked the other suppresses the
interaction — but the stored row names a blocker and a blocked, because unblock
must only undo the block its owner placed.

Deliberate non-goals
--------------------

No cascade. Blocking does not delete follows, friendships, mutes or
conversations, because none of the four prior implementations did and silently
destroying a follow graph on a reversible action is not something to introduce
inside a refactor. Enforcement is at read time, via ``is_blocked``.
"""

from __future__ import annotations

import logging

from services import pulse_mutation_audit, user_context

LOGGER = logging.getLogger(__name__)

REASON_MAX = 500


class SocialGraphError(ValueError):
    """Rejected before any state change.

    ``http_status`` mirrors what the route used to return so migrating a caller
    does not change its status codes: 400 validation, 404 unknown account.
    """

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


def _clean(value, limit: int = REASON_MAX) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_exists(cur, name: str) -> bool:
    try:
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (name,),
        )
        return bool(cur.fetchone())
    except Exception:
        # Postgres has no sqlite_master. Probing the table directly is the
        # portable fallback; a missing table raises and we treat it as absent.
        try:
            cur.execute(f"SELECT 1 FROM {name} LIMIT 1")
            return True
        except Exception:
            return False


def _ensure_blocked_users(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_user_id INTEGER,
            blocked_user_id INTEGER,
            reason TEXT,
            created_at TEXT,
            UNIQUE(blocker_user_id, blocked_user_id)
        )
        """
    )


def _emit_safety_event(cur, actor_id: int, event_type: str, target_id: int, title: str, body: str, extra: dict) -> None:
    """Best-effort notification. Never the reason a block fails.

    Imported lazily: ``bot`` imports this package, so a module-level import here
    would close the cycle.
    """
    try:
        import bot

        bot.pulse_emit_comms_safety_event(
            cur,
            actor_id,
            event_type,
            "user",
            target_id,
            actor_user_id=actor_id,
            target_url="/pulse/safety",
            title=title,
            body=body,
            category="safety",
            extra=extra,
        )
    except Exception as exc:
        LOGGER.warning(
            "SOCIAL_GRAPH_SAFETY_EVENT_FAILED event=%s actor=%s target=%s error=%s",
            event_type, actor_id, target_id, exc.__class__.__name__,
        )


def _validate(requester_user_id, target_user_id, verb: str) -> tuple[int, int]:
    requester_id = int(requester_user_id or 0)
    target_id = int(target_user_id or 0)
    if not requester_id:
        raise SocialGraphError("Login required.", 401, "unauthenticated")
    if not target_id:
        raise SocialGraphError("A user is required.", 400, "invalid_target")
    if requester_id == target_id:
        raise SocialGraphError(f"You cannot {verb} yourself.", 400, "self_target")
    return requester_id, target_id


def _read_state(cur, requester_id: int, target_id: int) -> dict:
    """Current block state across both tables, for before/after snapshots."""
    _ensure_blocked_users(cur)
    cur.execute(
        "SELECT id, reason, created_at FROM blocked_users "
        "WHERE blocker_user_id=? AND blocked_user_id=? LIMIT 1",
        (requester_id, target_id),
    )
    row = cur.fetchone()
    primary = dict(row) if row else {}
    comm_status = ""
    if _table_exists(cur, "comm_v2_blocks"):
        try:
            cur.execute(
                "SELECT status FROM comm_v2_blocks "
                "WHERE blocker_user_id=? AND blocked_user_id=? LIMIT 1",
                (requester_id, target_id),
            )
            comm_row = cur.fetchone()
            comm_status = str(dict(comm_row).get("status") or "") if comm_row else ""
        except Exception:
            comm_status = ""
    return {
        "blocked": bool(primary),
        "created_at": primary.get("created_at") or "",
        "messaging_block_status": comm_status,
    }


def _write_comm_v2_block(cur, requester_id: int, target_id: int, reason: str, active: bool) -> None:
    """Mirror the block onto the messaging table ``presence_service`` also reads.

    Soft status rather than a delete, because that is how the column is modelled
    and how the messaging reads test it (``status='active'``).
    """
    if not _table_exists(cur, "comm_v2_blocks"):
        return
    now = _now()
    status = "active" if active else "inactive"
    try:
        cur.execute(
            "INSERT OR IGNORE INTO comm_v2_blocks "
            "(blocker_user_id, blocked_user_id, reason, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (requester_id, target_id, reason, status, now, now),
        )
        cur.execute(
            "UPDATE comm_v2_blocks SET status=?, reason=?, updated_at=? "
            "WHERE blocker_user_id=? AND blocked_user_id=?",
            (status, reason, now, requester_id, target_id),
        )
    except Exception as exc:
        LOGGER.warning(
            "SOCIAL_GRAPH_COMM_V2_MIRROR_FAILED actor=%s target=%s active=%s error=%s",
            requester_id, target_id, active, exc.__class__.__name__,
        )


def block_user(requester_user_id, target_user_id, *, reason: str = "", surface: str = "") -> dict:
    """Block ``target_user_id`` on behalf of ``requester_user_id``.

    Idempotent: blocking someone already blocked succeeds with
    ``changed=False``. The client fires this from a toggle that retries, and a
    409 there would surface as an error on an action that already holds.
    """
    requester_id, target_id = _validate(requester_user_id, target_user_id, "block")
    reason = _clean(reason)
    correlation_id = pulse_mutation_audit.new_correlation_id()

    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (target_id,))
        if not cur.fetchone():
            raise SocialGraphError("That account no longer exists.", 404, "not_found")

        before = _read_state(cur, requester_id, target_id)
        now = _now()
        if before["blocked"]:
            # Refresh the reason but do not move created_at: the block dates from
            # when it was first placed, and a moderator reading the trail needs
            # that date to be the real one.
            if reason:
                cur.execute(
                    "UPDATE blocked_users SET reason=? WHERE blocker_user_id=? AND blocked_user_id=?",
                    (reason, requester_id, target_id),
                )
        else:
            cur.execute(
                "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, reason, created_at) "
                "VALUES (?, ?, ?, ?)",
                (requester_id, target_id, reason, now),
            )
        _write_comm_v2_block(cur, requester_id, target_id, reason, active=True)
        after = _read_state(cur, requester_id, target_id)
        changed = not before["blocked"]

        _emit_safety_event(
            cur, requester_id, "user_blocked", target_id,
            "User blocked",
            "This user was blocked and can no longer reach you.",
            {"blocked_user_id": target_id, "surface": surface or "unspecified",
             "correlation_id": correlation_id, "changed": changed},
        )
        pulse_mutation_audit.record(
            cur,
            actor_user_id=requester_id, operation="social_graph.block",
            target_type="user", target_id=target_id,
            before=before, after=after,
            outcome="applied" if changed else "already_blocked",
            actor_surface=surface, correlation_id=correlation_id,
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

    return {
        "ok": True,
        "user_id": target_id,
        "target_user_id": target_id,
        "blocked": True,
        "state": "blocked",
        "changed": changed,
        "correlation_id": correlation_id,
        "message": "User blocked." if changed else "User was already blocked.",
    }


def unblock_user(requester_user_id, target_user_id, *, surface: str = "") -> dict:
    """Remove the block ``requester_user_id`` placed on ``target_user_id``.

    Idempotent and terminal: unblocking someone who is not blocked succeeds with
    ``changed=False`` rather than 404, so a retried toggle converges. A 404 here
    would also be an oracle — it would distinguish "not blocked" from "no such
    row", which are the same fact to this caller.

    Deletes the ``blocked_users`` row (hard, as the settings route always has)
    and flips ``comm_v2_blocks`` to ``inactive`` (soft, as that table models it).
    """
    requester_id, target_id = _validate(requester_user_id, target_user_id, "unblock")
    correlation_id = pulse_mutation_audit.new_correlation_id()

    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (target_id,))
        if not cur.fetchone():
            raise SocialGraphError("That account no longer exists.", 404, "not_found")

        before = _read_state(cur, requester_id, target_id)
        cur.execute(
            "DELETE FROM blocked_users WHERE blocker_user_id=? AND blocked_user_id=?",
            (requester_id, target_id),
        )
        _write_comm_v2_block(cur, requester_id, target_id, "", active=False)
        after = _read_state(cur, requester_id, target_id)
        changed = bool(before["blocked"])

        _emit_safety_event(
            cur, requester_id, "user_unblocked", target_id,
            "User unblocked",
            "This user was unblocked and can reach you again.",
            {"unblocked_user_id": target_id, "surface": surface or "unspecified",
             "correlation_id": correlation_id, "changed": changed},
        )
        pulse_mutation_audit.record(
            cur,
            actor_user_id=requester_id, operation="social_graph.unblock",
            target_type="user", target_id=target_id,
            before=before, after=after,
            outcome="applied" if changed else "not_blocked",
            actor_surface=surface, correlation_id=correlation_id,
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

    return {
        "ok": True,
        "user_id": target_id,
        "target_user_id": target_id,
        "blocked": False,
        "state": "not_blocked",
        "changed": changed,
        "correlation_id": correlation_id,
        "message": "User unblocked." if changed else "User was not blocked.",
    }


def block_state(requester_user_id, target_user_id) -> dict:
    """Read-only canonical state. This is what verification reads back."""
    requester_id = int(requester_user_id or 0)
    target_id = int(target_user_id or 0)
    if not requester_id or not target_id:
        return {"blocked": False, "created_at": "", "messaging_block_status": ""}
    conn = user_context.connect()
    try:
        return _read_state(conn.cursor(), requester_id, target_id)
    finally:
        conn.close()
