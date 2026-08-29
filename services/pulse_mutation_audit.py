"""One audit trail for the shared consumer mutation services.

Why this exists
---------------

The consumer-side mutations (block, unblock, bio, reel delete, comment
edit/delete, report) were each written inline in a route, and each recorded
itself differently or not at all. Blocking someone through Settings wrote a row
and nothing else; blocking the same person through the feed wrote a row, filed a
moderation report and emitted a notification; blocking them from Messages
emitted the notification but filed no report. Three call sites, three different
histories, and no single place to answer "who blocked whom, when, and from
where".

Business OS already solved this for its own vertical with
``business_os_mkt_audit`` (see ``services/business_os/marketplace/service.py``),
and this table is deliberately the same shape so the two read alike. It is not a
second authority — it records what the authority did. Nothing reads it to make a
decision.

What a row means
----------------

One row is one *completed* mutation: it is written inside the same transaction
as the mutation itself, so a rolled-back write leaves no audit row claiming it
happened, and a committed write cannot be missing one. ``before_json`` and
``after_json`` carry the resource state on either side, which is what makes the
trail answer "what changed" rather than only "something changed".

``correlation_id`` ties together the several rows a single user action can
produce — a block writes two tables, and both rows carry the same id, so the
pair can be recognised as one act rather than two coincidences.

What must never go in here
--------------------------

No secrets, tokens, keys or payment credentials, and no message bodies. The
``before``/``after`` snapshots are built by the calling service and are expected
to hold state, not content: a bio change records that the bio changed and its
length, not the prose; a report records the target and the reason category, and
the reason text only because a moderator has to read it to act on it and it is
already stored in ``pulse_reports`` regardless.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

LOGGER = logging.getLogger(__name__)

# Snapshots are small by construction. The cap is a backstop against a caller
# accidentally passing a whole row with a serialized blob in it.
_SNAPSHOT_MAX = 4000


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(cur) -> None:
    """Create the table if it is missing.

    Called on the write path rather than only from ``bot.init_db`` because these
    services are also reached by the UNDX worker and by tests against a database
    that has never booted the Flask app.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_mutation_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            correlation_id TEXT,
            actor_user_id INTEGER NOT NULL,
            actor_surface TEXT,
            operation TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            outcome TEXT,
            before_json TEXT,
            after_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # Reads are "everything this actor did" and "everything that happened to
    # this target". Both are moderation questions, and both scan without these.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_pulse_mutation_audit_actor "
        "ON pulse_mutation_audit (actor_user_id, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_pulse_mutation_audit_target "
        "ON pulse_mutation_audit (target_type, target_id, created_at)"
    )


def _snapshot(value) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, sort_keys=True, default=str)[:_SNAPSHOT_MAX]
    except Exception:
        return json.dumps({"unserializable": True})


def record(
    cur,
    *,
    actor_user_id: int,
    operation: str,
    target_type: str,
    target_id,
    before=None,
    after=None,
    outcome: str = "applied",
    actor_surface: str = "",
    correlation_id: str = "",
) -> str:
    """Write one audit row on the caller's cursor. Returns the correlation id.

    Takes a cursor rather than opening its own connection on purpose: an audit
    row that commits independently of the mutation it describes is worse than no
    audit row, because it can assert a change that was rolled back.

    A failure here is logged and swallowed. That is a deliberate ordering
    choice — the mutation is the user's intent and the audit row is our record
    of it, so losing the record must not also lose the intent. The log line is
    the fallback trail.
    """
    correlation_id = correlation_id or new_correlation_id()
    try:
        ensure_schema(cur)
        cur.execute(
            """
            INSERT INTO pulse_mutation_audit
                (correlation_id, actor_user_id, actor_surface, operation,
                 target_type, target_id, outcome, before_json, after_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                correlation_id,
                int(actor_user_id or 0),
                str(actor_surface or "")[:80],
                str(operation or "")[:120],
                str(target_type or "")[:80],
                str(target_id if target_id is not None else "")[:120],
                str(outcome or "applied")[:40],
                _snapshot(before),
                _snapshot(after),
                _now(),
            ),
        )
    except Exception as exc:
        LOGGER.warning(
            "PULSE_MUTATION_AUDIT_WRITE_FAILED operation=%s actor=%s target=%s:%s "
            "correlation_id=%s outcome=%s error=%s",
            operation, actor_user_id, target_type, target_id,
            correlation_id, outcome, exc.__class__.__name__,
        )
    return correlation_id
