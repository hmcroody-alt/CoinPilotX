"""Human Concierge — a real request desk that never fabricates a human.

What this module is
-------------------
The member-facing half of a human operations desk, built on the REQUEST
primitive: a member submits a request (a ``private_requests`` row written
through ``records.create_record``, the canonical writer), talks to the desk
in a message thread this module owns, and watches the request move through
the REQUEST lifecycle. The operator-facing half is a queue over the same
rows, gated to a roster of real, named operator accounts.

What this module is *not*
-------------------------
* Not an AI pretending to be staff. There is no code path that generates an
  operator message. Every OPERATOR-authored row in the thread was written by
  an authenticated account on the operator roster, and carries that
  account's id in ``author_user_id``. If the roster is empty, the desk is
  UNSTAFFED and every payload says so — a submitted request is stored and
  disclosed as unseen, never answered by a machine wearing a name tag.
* Not autonomous. Requests move when a member or an operator moves them.
  Completion requires an operator and a completion note; the note may cite
  evidence refs into the member's own Office (the "here is what was done"
  trail), resolvable through the same Ask-Why endpoint as everything else.

Staffing truth
--------------
The roster is ``PRIVATE_CONCIERGE_OPERATOR_IDS`` — a comma-separated list of
platform user ids, set by operations when real humans are on duty. It is
runtime configuration on purpose: whether the desk is staffed is an
operational fact that changes without a deploy, so it must not be baked into
the feature matrix. ``desk_status()`` derives from it and is embedded in
every response.

Writes: this module owns ``private_concierge_messages``. REQUEST rows are
written only through ``records.create_record`` / ``records.update_record``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from services.private_office import audit
from services.private_office import evidence
from services.private_office import records as records_mod

MESSAGES_TABLE = "private_concierge_messages"

ROSTER_ENV = "PRIVATE_CONCIERGE_OPERATOR_IDS"

AUTHOR_MEMBER = "MEMBER"
AUTHOR_OPERATOR = "OPERATOR"

#: Product vocabulary for what the desk handles. Closed on purpose: a category
#: is a routing label operators filter by, not a free-text field.
CATEGORIES: tuple[str, ...] = (
    "GENERAL", "TRAVEL", "LEGAL", "FINANCIAL", "PERSONAL", "RESEARCH", "ADMIN",
)

#: Statuses an *operator* may set. OPEN is where submission puts things and
#: CANCELED belongs to the member alone — an operator closing a member's
#: request as "canceled" would be putting words in their mouth.
OPERATOR_STATUSES: tuple[str, ...] = (
    "IN_PROGRESS", "WAITING_ON_USER", "WAITING_ON_PROVIDER", "COMPLETED",
)

STATUS_COMPLETED = "COMPLETED"
STATUS_CANCELED = "CANCELED"

MAX_TITLE_CHARS = 160
MAX_BODY_CHARS = 4000
MAX_THREAD = 200
MAX_QUEUE = 50

MESSAGES_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    request_id INTEGER NOT NULL,
    author_role TEXT NOT NULL,
    author_user_id INTEGER NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    evidence_refs TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_po_concierge_owner_request "
    f"ON {MESSAGES_TABLE} (owner_user_id, request_id, id)",
)

_SCHEMA_READY = False


class PrivateConciergeRejected(ValueError):
    """A concierge request this module refuses."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def reset_concierge_schema_cache() -> None:
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_concierge_schema(cur, *, force: bool = False) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    cur.execute(MESSAGES_TABLE_DDL)
    for ddl in INDEX_DDL:
        cur.execute(ddl)
    records_mod.require_records_schema(cur)
    _SCHEMA_READY = True


# ---------------------------------------------------------------------------
# Roster — the humans, or the honest absence of them
# ---------------------------------------------------------------------------

def operator_ids() -> tuple[int, ...]:
    """The operator roster, from the environment. Empty means unstaffed."""
    raw = os.environ.get(ROSTER_ENV, "")
    out: list[int] = []
    for piece in str(raw).split(","):
        piece = piece.strip()
        if piece.isdigit() and int(piece) > 0:
            out.append(int(piece))
    return tuple(dict.fromkeys(out))


def is_operator(user_id: object) -> bool:
    try:
        return int(user_id or 0) in operator_ids()
    except (TypeError, ValueError):
        return False


def desk_status() -> dict[str, Any]:
    """The staffing truth embedded in every concierge payload."""
    count = len(operator_ids())
    if count:
        note = ("Requests are read and fulfilled by human operators. "
                "Nothing here is automated or generated.")
    else:
        note = ("No operator is currently registered. A submitted request is "
                "stored, but no human has seen it and none will until the "
                "desk is staffed.")
    return {"staffed": count > 0, "operator_count": count, "note": note}


# ---------------------------------------------------------------------------
# Member surface
# ---------------------------------------------------------------------------

def submit_request(
    cur,
    *,
    owner_user_id: int,
    title: str,
    description: str = "",
    category: str = "GENERAL",
    priority: str = "NORMAL",
    deadline_at: object = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """File one request with the desk. The REQUEST row is the request."""
    owner = int(owner_user_id or 0)
    ensure_concierge_schema(cur)
    clean_title = _clip(title, MAX_TITLE_CHARS)
    if not clean_title:
        raise PrivateConciergeRejected("a request needs a title")
    clean_category = str(category or "GENERAL").strip().upper()
    if clean_category not in CATEGORIES:
        allowed = ", ".join(CATEGORIES)
        raise PrivateConciergeRejected(f"category must be one of: {allowed}")

    fields: dict[str, Any] = {
        "title": clean_title,
        "category": clean_category,
        "priority": str(priority or "NORMAL").strip().upper(),
    }
    clean_description = _clip(description, MAX_BODY_CHARS)
    if clean_description:
        fields["description"] = clean_description
    if str(deadline_at or "").strip():
        fields["deadline_at"] = str(deadline_at).strip()

    outcome = records_mod.create_record(
        cur, record_type=records_mod.TYPE_REQUEST, owner_user_id=owner,
        actor_user_id=int(actor_user_id or owner), purpose="concierge_service",
        **fields)
    request_id = int(outcome["record_id"])
    return {
        "status": outcome["status"],
        "request_id": request_id,
        "ref": evidence.format_ref("request", request_id),
        "request": outcome["record"],
        "desk": desk_status(),
    }


def _fetch_request(cur, owner: int, request_id: int) -> dict | None:
    return records_mod.get_record(
        cur, record_type=records_mod.TYPE_REQUEST, owner_user_id=owner,
        record_id=int(request_id or 0), audit=False)


def _thread(cur, owner: int, request_id: int, *, member_view: bool) -> list[dict]:
    cur.execute(
        f"""SELECT id, author_role, author_user_id, body, evidence_refs,
                   created_at
            FROM {MESSAGES_TABLE}
            WHERE owner_user_id = ? AND request_id = ?
            ORDER BY id ASC LIMIT ?""",
        (owner, int(request_id), MAX_THREAD),
    )
    out = []
    for row in cur.fetchall():
        data = dict(row) if not isinstance(row, dict) else row
        message = {
            "id": int(data["id"]),
            "author": str(data["author_role"]),
            "body": str(data["body"]),
            "evidence": evidence.unpack_refs(data.get("evidence_refs")),
            "created_at": str(data["created_at"]),
        }
        # The member sees who *kind* of author wrote a line, not which staff
        # account; the operator console and the audit trail keep the id.
        if not member_view:
            message["author_user_id"] = int(data["author_user_id"])
        out.append(message)
    return out


def _insert_message(
    cur, *, owner: int, request_id: int, author_role: str,
    author_user_id: int, body: str, refs: object = (),
) -> dict[str, Any]:
    clean_body = _clip(body, MAX_BODY_CHARS)
    if not clean_body:
        raise PrivateConciergeRejected("a message needs a body")
    packed = evidence.pack_refs(evidence.normalize_refs(refs))
    now = _now_iso()
    cur.execute(
        f"""INSERT INTO {MESSAGES_TABLE}
            (owner_user_id, request_id, author_role, author_user_id, body,
             evidence_refs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (owner, int(request_id), author_role, int(author_user_id),
         clean_body, packed, now),
    )
    message_id = int(cur.lastrowid)
    audit.record(
        cur, actor_user_id=int(author_user_id), owner_user_id=owner,
        action=audit.ACTION_CONCIERGE_MESSAGE, object_type="REQUEST",
        object_id=int(request_id), purpose="concierge_service",
    )
    return {"id": message_id, "author": author_role, "body": clean_body,
            "evidence": evidence.unpack_refs(packed), "created_at": now}


def post_member_message(
    cur, *, owner_user_id: int, request_id: int, body: str,
    actor_user_id: int | None = None,
) -> dict[str, Any] | None:
    """The member speaks in their own thread. None when the request isn't theirs."""
    owner = int(owner_user_id or 0)
    ensure_concierge_schema(cur)
    request_row = _fetch_request(cur, owner, request_id)
    if request_row is None:
        return None
    if request_row.get("status") in (STATUS_COMPLETED, STATUS_CANCELED):
        raise PrivateConciergeRejected("this request is closed")
    return _insert_message(
        cur, owner=owner, request_id=int(request_id),
        author_role=AUTHOR_MEMBER,
        author_user_id=int(actor_user_id or owner), body=body)


def get_request(
    cur, *, owner_user_id: int, request_id: int, member_view: bool = True,
) -> dict[str, Any] | None:
    """One request with its thread. None when absent or not the caller's."""
    owner = int(owner_user_id or 0)
    ensure_concierge_schema(cur)
    request_row = _fetch_request(cur, owner, request_id)
    if request_row is None:
        return None
    return {
        "request": request_row,
        "ref": evidence.format_ref("request", int(request_row["id"])),
        "thread": _thread(cur, owner, int(request_id), member_view=member_view),
        "desk": desk_status(),
    }


def list_requests(
    cur, *, owner_user_id: int, statuses: object = None, limit: int = 50,
) -> list[dict]:
    """The member's own requests, newest first, via the gated reader."""
    ensure_concierge_schema(cur)
    return records_mod.list_records(
        cur, record_type=records_mod.TYPE_REQUEST,
        owner_user_id=int(owner_user_id or 0),
        statuses=statuses, limit=limit)


def cancel_request(
    cur, *, owner_user_id: int, request_id: int,
    actor_user_id: int | None = None,
) -> dict[str, Any] | None:
    """The member withdraws a request. CANCELED is theirs alone to say."""
    owner = int(owner_user_id or 0)
    ensure_concierge_schema(cur)
    outcome = records_mod.update_record(
        cur, record_type=records_mod.TYPE_REQUEST, owner_user_id=owner,
        record_id=int(request_id or 0),
        actor_user_id=int(actor_user_id or owner),
        purpose="concierge_service", status=STATUS_CANCELED)
    if outcome["status"] == "absent":
        return None
    return {"request": outcome["record"], "desk": desk_status()}


# ---------------------------------------------------------------------------
# Operator surface — real accounts on the roster, audited across the boundary
# ---------------------------------------------------------------------------

def _require_operator(operator_user_id: object) -> int:
    operator = int(operator_user_id or 0)
    if not is_operator(operator):
        raise PrivateConciergeRejected("not an operator")
    return operator


def desk_queue(cur, *, operator_user_id: int, limit: int = MAX_QUEUE) -> list[dict]:
    """Open requests across members, oldest first — a work queue, not a feed.

    The one deliberately cross-owner read in the Office. It is gated to the
    roster and audited per member whose data appears, with the operator as
    actor — the exact rows "who looked at whose requests" must find.
    """
    operator = _require_operator(operator_user_id)
    ensure_concierge_schema(cur)
    spec = records_mod.SPECS[records_mod.TYPE_REQUEST]
    open_statuses = [s for s in spec["statuses"] if s not in spec["closing"]]
    bounded = max(1, min(int(limit or MAX_QUEUE), MAX_QUEUE))
    cur.execute(
        f"""SELECT * FROM {records_mod.private_table_for(records_mod.TYPE_REQUEST)}
            WHERE lifecycle_state = ?
              AND status IN ({', '.join('?' for _ in open_statuses)})
            ORDER BY id ASC LIMIT ?""",
        tuple([records_mod.LIFECYCLE_ACTIVE] + open_statuses + [bounded]),
    )
    rows = []
    for raw in cur.fetchall():
        data = dict(raw)
        row = records_mod._serialize(records_mod.TYPE_REQUEST, data)  # noqa: SLF001
        # The member-facing projection drops owner_user_id because the member
        # already proved ownership. The console is the one caller that needs
        # it — it is the address of every desk action.
        row["owner_user_id"] = int(data["owner_user_id"])
        rows.append(row)
    per_owner: dict[int, int] = {}
    for row in rows:
        per_owner[int(row["owner_user_id"])] = \
            per_owner.get(int(row["owner_user_id"]), 0) + 1
    for owner, count in per_owner.items():
        audit.record(
            cur, actor_user_id=operator, owner_user_id=owner,
            action=audit.ACTION_CONCIERGE_READ, object_type="DESK_QUEUE",
            purpose="concierge_service", result_count=count,
        )
    return rows


def operator_get_request(
    cur, *, operator_user_id: int, owner_user_id: int, request_id: int,
) -> dict[str, Any] | None:
    """One member's request as the operator console sees it, audited."""
    operator = _require_operator(operator_user_id)
    owner = int(owner_user_id or 0)
    data = get_request(
        cur, owner_user_id=owner, request_id=request_id, member_view=False)
    if data is None:
        return None
    audit.record(
        cur, actor_user_id=operator, owner_user_id=owner,
        action=audit.ACTION_CONCIERGE_READ, object_type="REQUEST",
        object_id=int(request_id), purpose="concierge_service",
    )
    return data


def operator_update(
    cur,
    *,
    operator_user_id: int,
    owner_user_id: int,
    request_id: int,
    status: str = "",
    note: str = "",
    evidence_refs: object = (),
) -> dict[str, Any] | None:
    """An operator acts on a request: message, status move, or both.

    Completion is held to its name — COMPLETED requires a note saying what
    was done, and any evidence refs ride on that note so the member can walk
    "why is this complete" back to real rows. A status move alone (no note)
    is allowed for the intermediate states.
    """
    operator = _require_operator(operator_user_id)
    owner = int(owner_user_id or 0)
    ensure_concierge_schema(cur)
    request_row = _fetch_request(cur, owner, request_id)
    if request_row is None:
        return None
    if request_row.get("status") in (STATUS_COMPLETED, STATUS_CANCELED):
        raise PrivateConciergeRejected("this request is closed")

    wanted = str(status or "").strip().upper()
    if wanted and wanted not in OPERATOR_STATUSES:
        allowed = ", ".join(OPERATOR_STATUSES)
        raise PrivateConciergeRejected(f"status must be one of: {allowed}")
    clean_note = _clip(note, MAX_BODY_CHARS)
    if wanted == STATUS_COMPLETED and not clean_note:
        raise PrivateConciergeRejected(
            "completion requires a note describing what was done")
    if not wanted and not clean_note:
        raise PrivateConciergeRejected("nothing to do: pass a status, a note, or both")

    message = None
    if clean_note:
        message = _insert_message(
            cur, owner=owner, request_id=int(request_id),
            author_role=AUTHOR_OPERATOR, author_user_id=operator,
            body=clean_note, refs=evidence_refs)

    updated = request_row
    if wanted:
        fields: dict[str, Any] = {"status": wanted}
        if not str(request_row.get("assigned_provider_id") or "").strip():
            fields["assigned_provider_id"] = records_mod.safe_ref(
                f"operator:{operator}")
        outcome = records_mod.update_record(
            cur, record_type=records_mod.TYPE_REQUEST, owner_user_id=owner,
            record_id=int(request_id), actor_user_id=operator,
            purpose="concierge_service", **fields)
        updated = outcome["record"]

    return {"request": updated, "message": message}
