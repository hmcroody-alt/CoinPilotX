#!/usr/bin/env python3
"""Report Messenger messages that violate send idempotency.

The send path treats `(conversation_id, sender_user_id, client_message_id)` as the
identity of one logical outbound message, and
`pulse_communications_v2.service` installs a unique index to enforce it. That
index cannot be created on a database that already contains a violation -- and
historical data can, because the constraint did not exist when those rows were
written.

This script finds those rows. It is deliberately READ-ONLY. Deleting a duplicate
means destroying a message a real person sent, and the right copy to keep is not
always the oldest one: the survivor may be the one that carries reactions,
replies pointing at it, or a read receipt. That is a judgement call for a human
with the conversation in front of them, not for a boot path.

Usage:
    python3 scripts/messenger_idempotency_audit.py
    python3 scripts/messenger_idempotency_audit.py --database-url postgres://...
    python3 scripts/messenger_idempotency_audit.py --json

Exit codes:
    0  no violations -- the unique index can be installed
    1  violations found -- resolve them before expecting the index
    2  the audit could not run (no database, missing table)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

TABLE = "comm_v2_messages"

# Grouping ignores blank and NULL client ids on purpose: legacy rows and
# server-authored messages have none, they are not claims of identity, and the
# unique index excludes them by the same predicate.
DUPLICATE_GROUPS_SQL = f"""
    SELECT conversation_id, sender_user_id, client_message_id, COUNT(*) AS row_count
    FROM {TABLE}
    WHERE client_message_id IS NOT NULL AND client_message_id <> ''
    GROUP BY conversation_id, sender_user_id, client_message_id
    HAVING COUNT(*) > 1
    ORDER BY COUNT(*) DESC, conversation_id ASC
"""


def _sqlite_path(database_url: str) -> str:
    """The file a sqlite DATABASE_URL points at, or "" for anything else."""
    for prefix in ("sqlite:///", "sqlite://", "sqlite:"):
        if database_url.startswith(prefix):
            return database_url[len(prefix) :] or ""
    if database_url.endswith(".db") and "://" not in database_url:
        return database_url
    return ""


def _connect(database_url: str):
    """Open a connection using the app's own accessor so the audit sees exactly
    the database the running service sees, rather than a second guess at it.

    A plain sqlite target is opened directly instead. Importing `bot` pulls in
    the whole monolith -- a slow, side-effect-heavy operation that also pins the
    audit to the app's Python version -- and none of that buys anything when the
    target is a file on disk. The read is identical either way; only the route
    to it differs.
    """
    if database_url:
        os.environ["DATABASE_URL"] = database_url
    resolved = database_url or os.environ.get("DATABASE_URL", "")
    direct = _sqlite_path(resolved)
    if direct:
        conn = sqlite3.connect(direct)
        conn.row_factory = sqlite3.Row
        return conn

    import bot  # noqa: E402  -- import after DATABASE_URL is settled

    conn = bot.db()
    try:
        conn.row_factory = bot.sqlite3.Row
    except Exception:
        pass
    return conn


def _rows(cur) -> list[dict]:
    fetched = cur.fetchall() or []
    out = []
    for row in fetched:
        try:
            out.append(dict(row))
        except Exception:
            out.append({"row": list(row)})
    return out


def _members(cur, conversation_id, sender_user_id, client_message_id) -> list[dict]:
    cur.execute(
        f"SELECT id, public_id, created_at, deleted_at, delivery_status, message_type "
        f"FROM {TABLE} "
        "WHERE conversation_id=? AND sender_user_id=? AND client_message_id=? "
        "ORDER BY id ASC",
        (conversation_id, sender_user_id, client_message_id),
    )
    return _rows(cur)


def audit(database_url: str = "") -> dict:
    conn = _connect(database_url)
    cur = conn.cursor()
    try:
        cur.execute(DUPLICATE_GROUPS_SQL)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "groups": []}

    groups = []
    for group in _rows(cur):
        detail = dict(group)
        detail["messages"] = _members(
            cur,
            group.get("conversation_id"),
            group.get("sender_user_id"),
            group.get("client_message_id"),
        )
        groups.append(detail)

    duplicate_rows = sum(int(g.get("row_count") or 0) - 1 for g in groups)
    return {
        "ok": True,
        "index_installable": not groups,
        "duplicate_groups": len(groups),
        "excess_rows": duplicate_rows,
        "groups": groups,
    }


def _print_human(result: dict) -> None:
    if not result.get("ok"):
        print(f"AUDIT FAILED: {result.get('error')}")
        return
    if result["index_installable"]:
        print("No idempotency violations. The unique index can be installed.")
        return
    print(
        f"{result['duplicate_groups']} duplicated client_message_id group(s), "
        f"{result['excess_rows']} row(s) beyond one per logical message.\n"
    )
    for group in result["groups"]:
        print(
            f"conversation={group.get('conversation_id')} "
            f"sender={group.get('sender_user_id')} "
            f"client_message_id={group.get('client_message_id')!r} "
            f"rows={group.get('row_count')}"
        )
        for message in group.get("messages") or []:
            print(
                f"    id={message.get('id')} created_at={message.get('created_at')} "
                f"type={message.get('message_type')} "
                f"delivery={message.get('delivery_status')} "
                f"deleted_at={message.get('deleted_at') or '-'}"
            )
        print()
    print(
        "Nothing was modified. Decide per group which row survives -- check for\n"
        "reactions, replies and read receipts pointing at each id before removing\n"
        "any of them."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="", help="Override DATABASE_URL for this run.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args()

    result = audit(args.database_url)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result)

    if not result.get("ok"):
        return 2
    return 0 if result["index_installable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
