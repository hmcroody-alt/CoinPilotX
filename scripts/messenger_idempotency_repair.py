#!/usr/bin/env python3
"""Collapse duplicate Messenger sends so the idempotency index can install.

`scripts/messenger_idempotency_audit.py` names the offending rows and stops
there, deliberately. This is the other half: it proposes a survivor per group
and, only when told to, retires the rest.

WHAT IT DOES, AND WHY THAT IS SAFE TO DO AUTOMATICALLY

The audit's caution -- "the right copy to keep is not always the oldest one" --
is about a group whose members differ. This script therefore refuses to touch
any group where they do. A group is repairable only when every member carries
the same body and the same message type, which makes them copies of one send
rather than distinct messages that happen to share a key, and when no loser
carries anything a reader could miss: a reply pointing at it, a reaction, a
conversation's last_message_id, or a participant's last_read_message_id.

Anything else is reported and left alone. That is not a fallback for a case
that cannot happen -- it is the whole reason a human was required before.

The survivor is the lowest id: the row the sender's first request created, the
one every other copy is a retry of.

RETIRED, NOT DELETED

Losers are soft-deleted -- `deleted_at` is stamped, the row stays on disk. Two
reasons. It is reversible, which matters when the subject is the only copy of
real people's messages. And it is sufficient: the index predicate excludes
deleted rows, so stamping a loser frees the key without destroying it.

USAGE

    python3 scripts/messenger_idempotency_repair.py                 # plan only
    python3 scripts/messenger_idempotency_repair.py --json
    python3 scripts/messenger_idempotency_repair.py --apply --i-have-a-backup

Against production, via the app's own database URL:

    railway run --service Postgres python3 scripts/messenger_idempotency_repair.py

Planning is read-only and enforced as such by the server, not by intention.
`--apply` requires `--i-have-a-backup` as well; neither alone writes anything.

EXIT CODES

    0  nothing to repair, or --apply completed
    1  repairable groups found (planning mode) -- rerun with --apply
    2  the run could not proceed, or some group needs a human
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

DUPLICATE_GROUPS_SQL = f"""
    SELECT conversation_id, sender_user_id, client_message_id, COUNT(*) AS row_count
    FROM {TABLE}
    WHERE client_message_id IS NOT NULL AND client_message_id <> ''
      AND COALESCE(deleted_at,'') = ''
    GROUP BY conversation_id, sender_user_id, client_message_id
    HAVING COUNT(*) > 1
    ORDER BY conversation_id ASC, sender_user_id ASC, client_message_id ASC
"""

# Everything a loser could be carrying that its group's survivor would not
# inherit. A non-zero count anywhere here disqualifies the whole group.
MEMBERS_SQL = f"""
    SELECT m.id, m.public_id, m.created_at, m.deleted_at, m.message_type,
           COALESCE(m.body,'') AS body,
           (SELECT COUNT(*) FROM comm_v2_message_reactions r
             WHERE r.message_id = m.id) AS reactions,
           (SELECT COUNT(*) FROM {TABLE} c
             WHERE c.reply_to_message_id = m.id
               AND COALESCE(c.deleted_at,'') = '') AS replies,
           (SELECT COUNT(*) FROM comm_v2_conversations v
             WHERE v.last_message_id = m.id) AS conversation_pointers,
           (SELECT COUNT(*) FROM comm_v2_participants p
             WHERE p.last_read_message_id = m.id) AS read_pointers,
           (SELECT COUNT(*) FROM comm_v2_attachments a
             WHERE a.message_id = m.id) AS attachments
      FROM {TABLE} m
     WHERE m.conversation_id=? AND m.sender_user_id=? AND m.client_message_id=?
       AND COALESCE(m.deleted_at,'') = ''
     ORDER BY m.id ASC
"""

BLOCKING_COUNTS = ("reactions", "replies", "conversation_pointers", "read_pointers")


def _sqlite_path(database_url: str) -> str:
    for prefix in ("sqlite:///", "sqlite://", "sqlite:"):
        if database_url.startswith(prefix):
            return database_url[len(prefix) :] or ""
    if database_url.endswith(".db") and "://" not in database_url:
        return database_url
    return ""


def _connect(database_url: str, writable: bool):
    """The audit script's connection, with a switch.

    `services.db` and deliberately not `bot`: importing the monolith runs
    `init_db()` at module scope, which is the last thing a repair script should
    do to the database it is about to edit. See the audit for the full note.
    """
    if database_url:
        os.environ["DATABASE_URL"] = database_url
    resolved = database_url or os.environ.get("DATABASE_URL", "")
    direct = _sqlite_path(resolved)
    if direct:
        conn = sqlite3.connect(direct)
        conn.row_factory = sqlite3.Row
        return conn, False

    import services.db as app_db  # noqa: E402  -- import after DATABASE_URL is settled

    conn = app_db.connect()
    if app_db.IS_POSTGRES and not writable:
        # Enforced by the server rather than by this script's good intentions.
        # The commit is load-bearing, not the SET: `default_transaction_read_only`
        # governs transactions that start after it, and psycopg2 already opened
        # one to run the SET.
        conn.execute("SET default_transaction_read_only = on")
        conn.commit()
    return conn, app_db.IS_POSTGRES


def _rows(cur) -> list[dict]:
    out = []
    for row in cur.fetchall() or []:
        try:
            out.append(dict(row))
        except Exception:
            out.append({"row": list(row)})
    return out


def _classify(members: list[dict]) -> tuple[str, list[str]]:
    """Repairable, or the specific reasons a human is still needed."""
    reasons = []
    if len({m.get("body") or "" for m in members}) > 1:
        reasons.append("members have different bodies -- these may be distinct messages")
    if len({m.get("message_type") or "" for m in members}) > 1:
        reasons.append("members have different message types")
    for loser in members[1:]:
        for field in BLOCKING_COUNTS:
            if int(loser.get(field) or 0):
                reasons.append(f"id={loser.get('id')} has {loser.get(field)} {field}")
    return ("needs_human" if reasons else "repairable"), reasons


def plan(database_url: str = "") -> dict:
    conn, _ = _connect(database_url, writable=False)
    cur = conn.cursor()
    try:
        cur.execute(DUPLICATE_GROUPS_SQL)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "groups": []}

    groups = []
    for group in _rows(cur):
        cur.execute(
            MEMBERS_SQL,
            (
                group.get("conversation_id"),
                group.get("sender_user_id"),
                group.get("client_message_id"),
            ),
        )
        members = _rows(cur)
        # Classify first. `_classify` compares bodies, and the loop below drops
        # them so no message content reaches the terminal or the JSON.
        verdict, reasons = _classify(members)
        for member in members:
            member["body_len"] = len(member.pop("body", ""))
        groups.append({
            **group,
            "verdict": verdict,
            "reasons": reasons,
            "survivor_id": members[0]["id"] if members else None,
            "retire_ids": [m["id"] for m in members[1:]] if verdict == "repairable" else [],
            "members": members,
        })

    repairable = [g for g in groups if g["verdict"] == "repairable"]
    blocked = [g for g in groups if g["verdict"] == "needs_human"]
    return {
        "ok": True,
        "duplicate_groups": len(groups),
        "repairable_groups": len(repairable),
        "blocked_groups": len(blocked),
        "rows_to_retire": sum(len(g["retire_ids"]) for g in repairable),
        "groups": groups,
    }


def apply(database_url: str = "") -> dict:
    """Stamp `deleted_at` on every loser, in one transaction.

    The plan is recomputed against the writable connection before anything is
    written. The planning read ran on its own connection, and a send landing in
    between would make those ids a description of a database that no longer
    exists.
    """
    conn, is_postgres = _connect(database_url, writable=True)
    cur = conn.cursor()
    fresh = plan(database_url)
    if not fresh.get("ok"):
        return fresh
    targets = [gid for group in fresh["groups"] if group["verdict"] == "repairable" for gid in group["retire_ids"]]
    if not targets:
        return {**fresh, "applied": 0}

    stamp = _now()
    try:
        for message_id in targets:
            cur.execute(
                f"UPDATE {TABLE} SET deleted_at=?, updated_at=? "
                "WHERE id=? AND COALESCE(deleted_at,'') = ''",
                (stamp, stamp, message_id),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {**fresh, "ok": False, "error": f"{type(exc).__name__}: {exc}", "applied": 0}
    return {**fresh, "applied": len(targets), "retired_at": stamp}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print_human(result: dict, applied: bool) -> None:
    if not result.get("ok"):
        print(f"FAILED: {result.get('error')}")
        return
    if not result["duplicate_groups"]:
        print("No duplicate client_message_id groups. The unique index can install.")
        return

    for group in result["groups"]:
        head = (
            f"conversation={group.get('conversation_id')} "
            f"sender={group.get('sender_user_id')} "
            f"client_message_id={group.get('client_message_id')!r} "
            f"rows={group.get('row_count')}"
        )
        print(f"[{group['verdict']}] {head}")
        retiring = set(group.get("retire_ids") or ())
        for member in group.get("members") or []:
            # A blocked group has no retire_ids, so none of its rows is labelled
            # "retire" -- the label states what this run would do, not what a
            # repairable group would have done.
            role = "retire" if member["id"] in retiring else "keep  "
            extra = " ".join(
                f"{field}={member[field]}" for field in (*BLOCKING_COUNTS, "attachments")
                if int(member.get(field) or 0)
            )
            print(
                f"    {role} id={member['id']} created_at={member.get('created_at')} "
                f"body_len={member.get('body_len')} {extra}".rstrip()
            )
        for reason in group.get("reasons") or []:
            print(f"    ! {reason}")
        print()

    print(
        f"{result['duplicate_groups']} group(s): "
        f"{result['repairable_groups']} repairable, {result['blocked_groups']} need a human."
    )
    if applied:
        print(f"Retired {result.get('applied', 0)} row(s) at {result.get('retired_at')}.")
        print("Restart the service so the installer retries, then confirm")
        print("PULSE_COMM_V2_IDEMPOTENCY_INDEX state=installed hard_uniqueness_active=true")
    elif result["rows_to_retire"]:
        print(
            f"Nothing was modified. {result['rows_to_retire']} row(s) would be soft-deleted.\n"
            "Rerun with --apply --i-have-a-backup to carry it out."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Collapse duplicate Messenger sends.")
    parser.add_argument("--database-url", default="", help="Override DATABASE_URL for this run.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    parser.add_argument("--apply", action="store_true", help="Actually retire the losers.")
    parser.add_argument(
        "--i-have-a-backup",
        action="store_true",
        help="Required with --apply. This edits the only copy of real messages.",
    )
    args = parser.parse_args()

    if args.apply and not args.i_have_a_backup:
        print("--apply also requires --i-have-a-backup. Nothing was modified.")
        return 2

    result = apply(args.database_url) if args.apply else plan(args.database_url)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result, applied=args.apply)

    if not result.get("ok"):
        return 2
    if result.get("blocked_groups"):
        return 2
    if args.apply:
        return 0
    return 1 if result.get("rows_to_retire") else 0


if __name__ == "__main__":
    raise SystemExit(main())
