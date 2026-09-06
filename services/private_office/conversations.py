"""Private Conversations — a classification and policy layer, not a messaging system.

Why this module writes no messages
----------------------------------
``pulse_communications_v2`` is already the canonical message authority for this
platform: conversations, participants with roles, messages with a real partial
unique index over ``(conversation_id, sender_user_id, client_message_id)``,
reactions, receipts, typing, presence, blocks, reports, moderation, per-user
deletions, edit, forward, pin, archive, mute, unread counts and search. The
Stage 1 foundation map established that in code, file by file.

So a Private Office message ledger would be a *second* ledger. The failure mode
is not hypothetical and it is not cosmetic: two ledgers means two unread counts,
two block checks, two moderation queues, two idempotency identities, and a
member who is blocked in one system and reachable in the other. The first
production symptom would be a Private Office thread that keeps notifying after
the member left, because the leave was written to the other table.

This module therefore owns exactly one thing: **the fact that a canonical
conversation is a Private Office conversation, and what kind.** One row per
conversation, keyed by the canonical ``comm_v2_conversations.id``. Everything
else — send, read, react, attach, search — is delegated to
``pulse_communications_v2.service`` unchanged.

New message ledgers: 0. New participant tables: 0. New attachment stores: 0.
New notification engines: 0. New presence systems: 0. New RTC providers: 0.
No LiveKit is referenced, restored, or fallen back to anywhere in this package.

What "Private Office" adds on top of a canonical thread
-------------------------------------------------------
Three things, and only these three:

1. **Scope** — whether the thread is a direct line, a group, a room bound to an
   organization node, or a room bound to an operations project. This is a
   classification of an existing ``conversation_type``, not a new one. Adding a
   value to ``ALLOWED_CONVERSATION_TYPES`` would change how every non-Office
   surface in the app renders a thread; adding a row here changes nothing for
   anyone who does not ask.

2. **Sensitivity** — the same closed vocabulary the rest of Private Office uses
   (``model.SENSITIVITIES``), so a conversation can participate in the existing
   retrieval gates instead of inventing a parallel notion of "private".

3. **Links** — a bounded set of references to other Private Office objects
   (a document, a record, a fact, a meeting, an organization node, a project).
   References, not copies. A link row carries an id and a type; it never carries
   the linked object's content, so this table can never become a shadow copy of
   the document vault or the facts store.

Visibility, deliberately
------------------------
A classified conversation still appears in ordinary Messenger, still carrying
its classification. This is a decision, not an oversight, and it follows from
two facts that are already true:

* A participant may be invited who has no Private Office entitlement at all.
  If Office classification hid the thread from Messenger, that person would
  have a conversation they are a member of and no surface anywhere that can
  open it — messages arriving into a room with no doors.
* Unread counts are computed once, by the canonical service, from
  ``comm_v2_participants.unread_count``. A hidden mirror would need its own
  count, and the two would drift the first time a message was read on the
  surface that was not counting.

So Messenger shows the thread and labels it. The Private Office surface is a
*filtered, gated view* of the same rows, not a separate inbox.

Honesty
-------
There is no cryptographic end-to-end encryption in this path, and this module
says so in :func:`capability_states` rather than staying quiet. Messages are
protected by transport security, the canonical access checks, the Private
Office second lock, and (for RESTRICTED classification) the same at-rest field
crypto the rest of the package uses — none of which is E2EE, and none of which
may be labelled as such on any surface.

Fail-closed
-----------
``PRIVATE_CONVERSATIONS_ENABLED`` defaults to **off**, matching Private
Meetings. A deploy that has not deliberately turned this on has no Private
Conversations surface at all.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from pulse_communications_v2 import service as comm_service
from services.private_office import audit
from services.private_office import model as _model

LOGGER = logging.getLogger("private_office.conversations")

FEATURE_ID = "private_office.conversations"
FLAG_ENV = "PRIVATE_CONVERSATIONS_ENABLED"

CLASSIFICATION_TABLE = "private_office_conversations"
LINKS_TABLE = "private_office_conversation_links"

# ---------------------------------------------------------------------------
# Vocabulary — closed sets. An unknown value is refused at the door rather than
# stored and interpreted later, because the interpreter is always somewhere
# else by the time the bad row is read.
# ---------------------------------------------------------------------------

SCOPE_DIRECT = "DIRECT"
SCOPE_GROUP = "GROUP"
SCOPE_ORGANIZATION_ROOM = "ORGANIZATION_ROOM"
SCOPE_PROJECT_ROOM = "PROJECT_ROOM"

SCOPES: frozenset[str] = frozenset({
    SCOPE_DIRECT, SCOPE_GROUP, SCOPE_ORGANIZATION_ROOM, SCOPE_PROJECT_ROOM,
})

#: How an Office scope maps onto the canonical ``conversation_type``. The
#: canonical vocabulary is not extended; each Office scope *is* one of the four
#: types the messaging foundation already understands. ``community_channel`` is
#: intentionally absent — a Private Office thread is never a community channel,
#: and allowing it would put Office-classified rows inside community discovery.
SCOPE_TO_CONVERSATION_TYPE: dict[str, str] = {
    SCOPE_DIRECT: "direct",
    SCOPE_GROUP: "group",
    SCOPE_ORGANIZATION_ROOM: "room",
    SCOPE_PROJECT_ROOM: "room",
}

#: Scopes that must carry a binding id, and the column that must hold it. A
#: room named after a project but bound to nothing is the kind of row that
#: looks correct on a screen and cannot be joined to anything.
SCOPE_REQUIRED_BINDING: dict[str, str] = {
    SCOPE_ORGANIZATION_ROOM: "organization_node_id",
    SCOPE_PROJECT_ROOM: "operations_project_id",
}

LINK_DOCUMENT = "DOCUMENT"
LINK_RECORD = "RECORD"
LINK_FACT = "FACT"
LINK_MEETING = "MEETING"
LINK_ORGANIZATION_NODE = "ORGANIZATION_NODE"
LINK_PROJECT = "PROJECT"

LINK_TYPES: frozenset[str] = frozenset({
    LINK_DOCUMENT, LINK_RECORD, LINK_FACT, LINK_MEETING,
    LINK_ORGANIZATION_NODE, LINK_PROJECT,
})

DEFAULT_SENSITIVITY = _model.SENSITIVITY_CONFIDENTIAL

MAX_LINKS_PER_CONVERSATION = 200
MAX_LIST_LIMIT = 120
DEFAULT_LIST_LIMIT = 60


class PrivateConversationRejected(ValueError):
    """A request this module refuses, carrying its HTTP mapping.

    Same shape as ``meetings.PrivateMeetingRejected`` on purpose: the routes
    layer translates one exception type per subsystem, and two subsystems that
    raise differently-shaped errors eventually produce two differently-shaped
    error payloads for the same class of mistake.
    """

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)


# ---------------------------------------------------------------------------
# Schema — SQLite dialect; services.db rewrites for Postgres.
#
# Note what is NOT here: no messages, no participants, no attachments, no
# receipts, no typing, no presence. Those tables exist exactly once in this
# repository and they are not in this package.
# ---------------------------------------------------------------------------

CLASSIFICATION_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {CLASSIFICATION_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL UNIQUE,
    owner_user_id INTEGER NOT NULL,
    created_by_user_id INTEGER NOT NULL DEFAULT 0,
    office_scope TEXT NOT NULL DEFAULT '{SCOPE_DIRECT}',
    sensitivity TEXT NOT NULL DEFAULT '{DEFAULT_SENSITIVITY}',
    organization_node_id INTEGER NOT NULL DEFAULT 0,
    operations_project_id INTEGER NOT NULL DEFAULT 0,
    meeting_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT NOT NULL DEFAULT ''
)
"""

LINKS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {LINKS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    link_type TEXT NOT NULL,
    target_id TEXT NOT NULL DEFAULT '',
    created_by_user_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, link_type, target_id)
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_poc_owner "
    f"ON {CLASSIFICATION_TABLE} (owner_user_id, office_scope, conversation_id)",
    f"CREATE INDEX IF NOT EXISTS idx_poc_org_node "
    f"ON {CLASSIFICATION_TABLE} (organization_node_id, conversation_id)",
    f"CREATE INDEX IF NOT EXISTS idx_poc_project "
    f"ON {CLASSIFICATION_TABLE} (operations_project_id, conversation_id)",
    f"CREATE INDEX IF NOT EXISTS idx_poc_links_conversation "
    f"ON {LINKS_TABLE} (conversation_id, link_type)",
    f"CREATE INDEX IF NOT EXISTS idx_poc_links_target "
    f"ON {LINKS_TABLE} (link_type, target_id)",
)

_SCHEMA_READY = False


def reset_conversations_schema_cache() -> None:
    """Forget the process-local 'schema is ready' flag.

    Only success is ever cached (see :func:`ensure_conversations_schema`), so
    this exists for tests and for the case where a fresh database is swapped in
    underneath a long-lived process.
    """
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_conversations_schema(cur, *, force: bool = False) -> None:
    """Create this package's two tables. Idempotent on SQLite and Postgres.

    The canonical messaging schema is ensured too, because a classification row
    that points at a conversation table which does not exist yet is a foreign
    key to nowhere. Ordering matters here in exactly the way it did not matter
    in development: the worker process that reads classifications never serves
    an HTTP request, so it cannot rely on a route handler having created the
    messaging tables first.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    comm_service.ensure_v2_schema(cur)
    cur.execute(CLASSIFICATION_TABLE_DDL)
    cur.execute(LINKS_TABLE_DDL)
    for ddl in INDEX_DDL:
        cur.execute(ddl)
    _SCHEMA_READY = True


# ---------------------------------------------------------------------------
# Flags — fail closed. An absent env var means OFF, which is the opposite of
# feature_matrix._flag_enabled's historical default, hence a local helper.
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def _flag_on(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if raw == "":
        return bool(default)
    return raw in _TRUTHY


def conversations_enabled() -> bool:
    return _flag_on(FLAG_ENV, False)


def _require_enabled() -> None:
    if not conversations_enabled():
        raise PrivateConversationRejected(
            "Private Conversations is not enabled.",
            status=404,
            code="feature_disabled",
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(value: Any) -> dict:
    """A cursor row as a plain dict, or ``{}``.

    Mirrors ``comm_service._row``; duplicated rather than imported because
    reaching into another package's private helper is how a rename in that
    package becomes a runtime failure in this one.
    """
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _clean_scope(value: object) -> str:
    scope = str(value or "").strip().upper()
    if scope not in SCOPES:
        raise PrivateConversationRejected(
            "Choose a supported conversation scope.", status=400, code="invalid_scope"
        )
    return scope


def _clean_sensitivity(value: object) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return DEFAULT_SENSITIVITY
    if raw not in _model.SENSITIVITIES:
        raise PrivateConversationRejected(
            "Choose a supported sensitivity.", status=400, code="invalid_sensitivity"
        )
    return raw


def _clean_link_type(value: object) -> str:
    raw = str(value or "").strip().upper()
    if raw not in LINK_TYPES:
        raise PrivateConversationRejected(
            "Choose a supported link type.", status=400, code="invalid_link_type"
        )
    return raw


def _clean_target_id(value: object) -> str:
    """A link target is an *identifier*, never content.

    Reuses the audit module's id shape check so the two agree by construction:
    if a value would be dropped from an audit row for looking like a secret, it
    is not allowed to become a link target either.
    """
    target = audit.safe_object_id(value)
    if not target:
        raise PrivateConversationRejected(
            "That link target is not a valid identifier.",
            status=400,
            code="invalid_link_target",
        )
    return target


def _unwrap(result: dict, *, default_code: str = "messaging_error") -> dict:
    """Turn a canonical service envelope into data, or raise.

    ``pulse_communications_v2`` returns ``{"ok": bool, ...}`` envelopes. This
    layer raises instead, so a route cannot accidentally return a success shape
    that happens to contain an error — the specific bug that produces "no
    conversations yet" on a screen whose fetch actually failed.

    Read the canonical failure shape carefully. ``service._err`` builds
    ``{"ok": False, "status": <code string>, "message": ..., "http_status":
    <int>, "trace_id": ...}`` — ``status`` holds the *code*, and the HTTP code
    lives in ``http_status``. Treating ``status`` as a number here raised
    ``ValueError`` inside the failure path, turning every honest 400 from the
    messaging authority into a 503 with a stack trace. The HTTP code is
    therefore taken from ``http_status`` first, and ``status`` is used only when
    it is genuinely numeric.
    """
    if not isinstance(result, dict):
        raise PrivateConversationRejected(
            "Messaging is unavailable.", status=503, code=default_code
        )
    if result.get("ok"):
        return result

    status = 400
    for candidate in (result.get("http_status"), result.get("status")):
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            status = value
            break

    code = result.get("code") or result.get("status") or default_code
    if not isinstance(code, str) or not code.strip():
        code = default_code
    message = str(result.get("message") or "Messaging is unavailable.")
    raise PrivateConversationRejected(message, status=status, code=str(code))


# ---------------------------------------------------------------------------
# Classification reads
# ---------------------------------------------------------------------------

def classification(cur, conversation_id: int) -> dict:
    """The Office classification for a canonical conversation, or ``{}``."""
    ensure_conversations_schema(cur)
    cur.execute(
        f"SELECT * FROM {CLASSIFICATION_TABLE} WHERE conversation_id=? LIMIT 1",
        (int(conversation_id or 0),),
    )
    return _row(cur.fetchone())


def is_private_conversation(cur, conversation_id: int) -> bool:
    return bool(classification(cur, conversation_id))


def classifications_for(cur, conversation_ids: list[int]) -> dict[int, dict]:
    """Classifications for many conversations at once, keyed by conversation id.

    The list surface needs this: resolving one row per conversation inside a
    render loop is the shape that turns a 40-thread inbox into 40 queries, and
    it is the shape that gets written first every time.
    """
    ids = [int(x) for x in (conversation_ids or []) if int(x or 0) > 0]
    if not ids:
        return {}
    ensure_conversations_schema(cur)
    placeholders = ",".join("?" for _ in ids)
    cur.execute(
        f"SELECT * FROM {CLASSIFICATION_TABLE} "
        f"WHERE conversation_id IN ({placeholders})",
        tuple(ids),
    )
    out: dict[int, dict] = {}
    for raw in cur.fetchall() or []:
        row = _row(raw)
        if row:
            out[int(row.get("conversation_id") or 0)] = row
    return out


def classification_payload(row: dict) -> dict:
    """The client-facing shape of a classification row.

    Deliberately omits ``owner_user_id`` and the internal primary key: a
    participant who is not the owner has no need for either, and shipping them
    to every client is how an id becomes a de facto public identifier.
    """
    if not row:
        return {}
    return {
        "office_scope": str(row.get("office_scope") or ""),
        "sensitivity": str(row.get("sensitivity") or ""),
        "organization_node_id": int(row.get("organization_node_id") or 0),
        "operations_project_id": int(row.get("operations_project_id") or 0),
        "meeting_id": int(row.get("meeting_id") or 0),
        "archived": bool(str(row.get("archived_at") or "")),
        "created_at": str(row.get("created_at") or ""),
        "is_private_office": True,
        # Repeated on every payload rather than documented once, because the
        # place a false "encrypted" badge gets rendered is a screen whose author
        # never read this module.
        "end_to_end_encrypted": False,
    }


# ---------------------------------------------------------------------------
# Classification writes
# ---------------------------------------------------------------------------

def _validate_binding(scope: str, *, organization_node_id: int,
                      operations_project_id: int) -> None:
    required = SCOPE_REQUIRED_BINDING.get(scope)
    if not required:
        return
    value = (
        organization_node_id if required == "organization_node_id"
        else operations_project_id
    )
    if int(value or 0) <= 0:
        raise PrivateConversationRejected(
            "This room must be bound to its organization or project.",
            status=400,
            code="missing_binding",
        )


def classify(cur, *, conversation_id: int, owner_user_id: int, actor_user_id: int,
             office_scope: str, sensitivity: object = "",
             organization_node_id: int = 0, operations_project_id: int = 0,
             meeting_id: int = 0) -> dict:
    """Mark an existing canonical conversation as a Private Office conversation.

    Upsert by hand rather than ``INSERT OR IGNORE``: that statement means two
    different things on SQLite and Postgres, and ``services.db`` rewrites it to
    a bare ``INSERT`` on Postgres. Dedupe is a decision made here, visibly,
    where the reader of this function can see it.
    """
    _require_enabled()
    ensure_conversations_schema(cur)

    scope = _clean_scope(office_scope)
    level = _clean_sensitivity(sensitivity)
    org_id = max(0, int(organization_node_id or 0))
    project_id = max(0, int(operations_project_id or 0))
    _validate_binding(scope, organization_node_id=org_id,
                      operations_project_id=project_id)

    now = _now_iso()
    existing = classification(cur, conversation_id)
    if existing:
        cur.execute(
            f"""UPDATE {CLASSIFICATION_TABLE}
                SET office_scope=?, sensitivity=?, organization_node_id=?,
                    operations_project_id=?, meeting_id=?, updated_at=?
                WHERE conversation_id=?""",
            (scope, level, org_id, project_id, max(0, int(meeting_id or 0)), now,
             int(conversation_id)),
        )
    else:
        cur.execute(
            f"""INSERT INTO {CLASSIFICATION_TABLE}
                (conversation_id, owner_user_id, created_by_user_id, office_scope,
                 sensitivity, organization_node_id, operations_project_id,
                 meeting_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(conversation_id), int(owner_user_id or 0), int(actor_user_id or 0),
             scope, level, org_id, project_id, max(0, int(meeting_id or 0)), now, now),
        )

    audit.record(
        cur,
        actor_user_id=int(actor_user_id or 0),
        owner_user_id=int(owner_user_id or 0),
        action=audit.ACTION_CONVERSATION_CLASSIFY,
        object_type="CONVERSATION",
        object_id=f"CONVERSATION:{int(conversation_id)}",
        purpose="user_request",
    )
    return classification(cur, conversation_id)


def set_sensitivity(cur, *, conversation_id: int, actor_user_id: int,
                    sensitivity: object) -> dict:
    """Change a thread's sensitivity. Owner-only is enforced by the caller."""
    _require_enabled()
    row = classification(cur, conversation_id)
    if not row:
        raise PrivateConversationRejected(
            "That conversation is not a Private Office conversation.",
            status=404,
            code="not_private_conversation",
        )
    level = _clean_sensitivity(sensitivity)
    cur.execute(
        f"UPDATE {CLASSIFICATION_TABLE} SET sensitivity=?, updated_at=? "
        f"WHERE conversation_id=?",
        (level, _now_iso(), int(conversation_id)),
    )
    audit.record(
        cur,
        actor_user_id=int(actor_user_id or 0),
        owner_user_id=int(row.get("owner_user_id") or 0),
        action=audit.ACTION_CONVERSATION_SENSITIVITY_CHANGE,
        object_type="CONVERSATION",
        object_id=f"CONVERSATION:{int(conversation_id)}",
        purpose="user_request",
    )
    return classification(cur, conversation_id)


# ---------------------------------------------------------------------------
# Links — references to other Private Office objects. Never copies.
# ---------------------------------------------------------------------------

def link(cur, *, conversation_id: int, actor_user_id: int, link_type: str,
         target_id: object) -> dict:
    _require_enabled()
    row = classification(cur, conversation_id)
    if not row:
        raise PrivateConversationRejected(
            "That conversation is not a Private Office conversation.",
            status=404,
            code="not_private_conversation",
        )
    kind = _clean_link_type(link_type)
    target = _clean_target_id(target_id)

    cur.execute(
        f"SELECT COUNT(1) FROM {LINKS_TABLE} WHERE conversation_id=?",
        (int(conversation_id),),
    )
    count_row = cur.fetchone()
    current = int((count_row or [0])[0] or 0)
    if current >= MAX_LINKS_PER_CONVERSATION:
        raise PrivateConversationRejected(
            "This conversation already has the maximum number of links.",
            status=409,
            code="too_many_links",
        )

    cur.execute(
        f"SELECT * FROM {LINKS_TABLE} "
        f"WHERE conversation_id=? AND link_type=? AND target_id=? LIMIT 1",
        (int(conversation_id), kind, target),
    )
    existing = _row(cur.fetchone())
    if existing:
        # Idempotent by design: a client that retries a link after a timeout
        # must not get a 409 for work it already succeeded at.
        return link_payload(existing)

    cur.execute(
        f"""INSERT INTO {LINKS_TABLE}
            (conversation_id, link_type, target_id, created_by_user_id, created_at)
            VALUES (?, ?, ?, ?, ?)""",
        (int(conversation_id), kind, target, int(actor_user_id or 0), _now_iso()),
    )
    audit.record(
        cur,
        actor_user_id=int(actor_user_id or 0),
        owner_user_id=int(row.get("owner_user_id") or 0),
        action=audit.ACTION_CONVERSATION_LINK,
        object_type="CONVERSATION",
        object_id=f"CONVERSATION:{int(conversation_id)}",
        purpose="user_request",
    )
    cur.execute(
        f"SELECT * FROM {LINKS_TABLE} "
        f"WHERE conversation_id=? AND link_type=? AND target_id=? LIMIT 1",
        (int(conversation_id), kind, target),
    )
    return link_payload(_row(cur.fetchone()))


def unlink(cur, *, conversation_id: int, actor_user_id: int, link_type: str,
           target_id: object) -> bool:
    _require_enabled()
    row = classification(cur, conversation_id)
    if not row:
        raise PrivateConversationRejected(
            "That conversation is not a Private Office conversation.",
            status=404,
            code="not_private_conversation",
        )
    kind = _clean_link_type(link_type)
    target = _clean_target_id(target_id)
    cur.execute(
        f"DELETE FROM {LINKS_TABLE} "
        f"WHERE conversation_id=? AND link_type=? AND target_id=?",
        (int(conversation_id), kind, target),
    )
    audit.record(
        cur,
        actor_user_id=int(actor_user_id or 0),
        owner_user_id=int(row.get("owner_user_id") or 0),
        action=audit.ACTION_CONVERSATION_UNLINK,
        object_type="CONVERSATION",
        object_id=f"CONVERSATION:{int(conversation_id)}",
        purpose="user_request",
    )
    return True


def link_payload(row: dict) -> dict:
    if not row:
        return {}
    return {
        "link_type": str(row.get("link_type") or ""),
        "target_id": str(row.get("target_id") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def list_links(cur, conversation_id: int, *, link_type: str = "") -> list[dict]:
    ensure_conversations_schema(cur)
    params: list[Any] = [int(conversation_id or 0)]
    clause = ""
    if link_type:
        clause = "AND link_type=?"
        params.append(_clean_link_type(link_type))
    cur.execute(
        f"SELECT * FROM {LINKS_TABLE} WHERE conversation_id=? {clause} "
        f"ORDER BY id DESC LIMIT {MAX_LINKS_PER_CONVERSATION}",
        tuple(params),
    )
    return [link_payload(_row(r)) for r in (cur.fetchall() or [])]


def conversations_for_target(cur, *, link_type: str, target_id: object) -> list[int]:
    """Which Office conversations reference this object.

    The reverse direction is what makes a document detail screen able to say
    "discussed in 2 conversations" without the documents package needing to
    know anything about messaging.
    """
    ensure_conversations_schema(cur)
    kind = _clean_link_type(link_type)
    target = _clean_target_id(target_id)
    cur.execute(
        f"SELECT conversation_id FROM {LINKS_TABLE} "
        f"WHERE link_type=? AND target_id=? ORDER BY conversation_id DESC LIMIT 200",
        (kind, target),
    )
    return [int(_row(r).get("conversation_id") or 0) for r in (cur.fetchall() or [])]


# ---------------------------------------------------------------------------
# Delegation — creation and listing. Every message operation is the canonical
# service's, called directly by the routes layer; nothing is wrapped for the
# sake of wrapping it.
# ---------------------------------------------------------------------------

def create(cur, *, actor_user_id: int, office_scope: str,
           payload: dict | None = None) -> dict:
    """Create a canonical conversation and classify it in one member-visible act.

    Two writes across two authorities, so ordering is the whole design: the
    canonical conversation is created first and the classification second. If
    the classification write fails, what exists is an ordinary conversation the
    member can still open in Messenger — degraded, but not lost, and not a
    dangling classification pointing at a conversation that was never created.
    """
    _require_enabled()
    ensure_conversations_schema(cur)

    scope = _clean_scope(office_scope)
    payload = dict(payload or {})
    payload["conversation_type"] = SCOPE_TO_CONVERSATION_TYPE[scope]
    if scope in (SCOPE_ORGANIZATION_ROOM, SCOPE_PROJECT_ROOM):
        # An Office room is never discoverable. The canonical room path treats
        # an absent privacy as "public", which would put a private office room
        # into public room discovery.
        payload["privacy"] = "private"

    created = _unwrap(
        comm_service.create_conversation(int(actor_user_id), payload),
        default_code="create_failed",
    )
    conversation_id = int(created.get("conversation_id") or 0)
    if conversation_id <= 0:
        raise PrivateConversationRejected(
            "The conversation could not be created.",
            status=502,
            code="create_failed",
        )

    row = classify(
        cur,
        conversation_id=conversation_id,
        owner_user_id=int(actor_user_id),
        actor_user_id=int(actor_user_id),
        office_scope=scope,
        sensitivity=payload.get("sensitivity") or "",
        organization_node_id=int(payload.get("organization_node_id") or 0),
        operations_project_id=int(payload.get("operations_project_id") or 0),
        meeting_id=int(payload.get("meeting_id") or 0),
    )

    conversation = dict(created.get("conversation") or {})
    conversation["private_office"] = classification_payload(row)
    return {
        "conversation": conversation,
        "conversation_id": conversation_id,
        "private_office": classification_payload(row),
    }


def list_for_member(cur, *, actor_user_id: int, limit: int = DEFAULT_LIST_LIMIT,
                    office_scope: str = "") -> dict:
    """The member's Private Office threads — a filtered view of canonical rows.

    Filtering happens *after* the canonical list, not instead of it, so a thread
    is only ever shown to someone the canonical participant check already
    admitted. Doing the classification join in SQL and skipping the canonical
    query would be faster and would be a second access path.
    """
    _require_enabled()
    ensure_conversations_schema(cur)

    listed = _unwrap(
        comm_service.list_conversations(int(actor_user_id), {}),
        default_code="list_failed",
    )
    items = list(listed.get("items") or [])
    ids = [int(item.get("id") or item.get("conversation_id") or 0) for item in items]
    classified = classifications_for(cur, ids)

    wanted = _clean_scope(office_scope) if office_scope else ""
    out: list[dict] = []
    for item in items:
        conversation_id = int(item.get("id") or item.get("conversation_id") or 0)
        row = classified.get(conversation_id)
        if not row:
            continue
        if wanted and str(row.get("office_scope") or "") != wanted:
            continue
        if str(row.get("archived_at") or ""):
            continue
        enriched = dict(item)
        enriched["private_office"] = classification_payload(row)
        enriched["links"] = list_links(cur, conversation_id)
        out.append(enriched)

    capped = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
    out = out[:capped]

    audit.record(
        cur,
        actor_user_id=int(actor_user_id),
        owner_user_id=int(actor_user_id),
        action=audit.ACTION_CONVERSATION_READ,
        object_type="CONVERSATION_LIST",
        object_id="LIST",
        purpose="user_request",
        result_count=len(out),
    )
    return {"items": out, "count": len(out)}


def require_member_view(cur, *, actor_user_id: int, conversation_ref: object) -> dict:
    """Resolve a conversation the member may actually see, or raise.

    Membership is decided by the canonical service — this function does not
    query ``comm_v2_participants`` itself. A second membership predicate is a
    second answer to "is this person in this thread", and the two disagree the
    first time either one changes.
    """
    _require_enabled()
    ensure_conversations_schema(cur)

    control = _unwrap(
        comm_service.conversation_control_center(int(actor_user_id), conversation_ref),
        default_code="conversation_unavailable",
    )
    conversation = dict(control.get("conversation") or {})
    conversation_id = int(conversation.get("id") or conversation.get("conversation_id") or 0)
    if conversation_id <= 0:
        raise PrivateConversationRejected(
            "That conversation is unavailable.", status=404, code="missing_conversation"
        )
    row = classification(cur, conversation_id)
    if not row:
        raise PrivateConversationRejected(
            "That conversation is not a Private Office conversation.",
            status=404,
            code="not_private_conversation",
        )
    return {
        "conversation_id": conversation_id,
        "conversation": conversation,
        "control": control,
        "classification": row,
        "private_office": classification_payload(row),
        "links": list_links(cur, conversation_id),
    }


# ---------------------------------------------------------------------------
# Capability truth
# ---------------------------------------------------------------------------

def capability_states() -> dict[str, Any]:
    """What this subsystem can and cannot do, as data a screen can render.

    Every entry here is a claim about code that exists in this repository. The
    two ``False`` entries are the ones that matter: a screen that reads this
    dict cannot render an encryption badge or a disappearing-messages toggle,
    because the truth is here and it is negative.
    """
    return {
        "feature_id": FEATURE_ID,
        "enabled": conversations_enabled(),
        "flag_env": FLAG_ENV,
        "message_ledger": "comm_v2_messages",
        "attachment_authority": "message_attachments",
        "rtc_provider": "agora",
        "scopes": sorted(SCOPES),
        "link_types": sorted(LINK_TYPES),
        # No cryptographic E2EE exists on this path. Stage 53 forbids the label
        # and this is the machine-readable reason a client must not render it.
        "end_to_end_encrypted": False,
        "encryption_note": (
            "Protected by transport security, canonical access checks and the "
            "Private Office second lock. Not end-to-end encrypted."
        ),
        "disappearing_messages": False,
    }


__all__ = [
    "FEATURE_ID", "FLAG_ENV",
    "CLASSIFICATION_TABLE", "LINKS_TABLE",
    "SCOPE_DIRECT", "SCOPE_GROUP", "SCOPE_ORGANIZATION_ROOM", "SCOPE_PROJECT_ROOM",
    "SCOPES", "SCOPE_TO_CONVERSATION_TYPE",
    "LINK_DOCUMENT", "LINK_RECORD", "LINK_FACT", "LINK_MEETING",
    "LINK_ORGANIZATION_NODE", "LINK_PROJECT", "LINK_TYPES",
    "PrivateConversationRejected",
    "ensure_conversations_schema", "reset_conversations_schema_cache",
    "conversations_enabled",
    "classification", "classifications_for", "classification_payload",
    "is_private_conversation", "classify", "set_sensitivity",
    "link", "unlink", "list_links", "link_payload", "conversations_for_target",
    "create", "list_for_member", "require_member_view",
    "capability_states",
]
