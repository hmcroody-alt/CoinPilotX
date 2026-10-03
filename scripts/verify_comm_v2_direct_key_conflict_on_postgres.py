#!/usr/bin/env python3
"""Prove `service.create_conversation`'s direct branch on real PostgreSQL.

## Why this exists

`comm_v2_conversations.direct_key` is `TEXT UNIQUE` (prod carries it as
`comm_v2_conversations_direct_key_key`). The direct branch of
`create_conversation` looks for an existing thread with

    WHERE direct_key=? AND COALESCE(deleted_at,'')=''

and, finding none, does a **plain INSERT** of that same `direct_key`. A
soft-deleted row satisfies the unique index but not the SELECT, so the two
disagree and the INSERT hits the constraint. The pair can then never reopen the
DM they deleted: every attempt raises.

The suite runs on SQLite, which enforces the same `UNIQUE` -- so the raise
reproduces there too -- but it cannot show what the statement does to a
PostgreSQL *transaction*, nor exercise `services/db`'s `INSERT OR IGNORE` ->
`ON CONFLICT DO NOTHING` rewrite, which is the shape of the fix. Both of those
only exist on the engine production runs on.

The known-good counterpart is `services/pulse_chat_bridge.direct_thread`, which
resolves the same key: conflict-tolerant insert, unconditional re-SELECT, never
`lastrowid`, and it revives a soft-deleted row instead of colliding with it.

## Why a script and not a test

It needs a PostgreSQL server. In `tests/` it would either skip silently in CI (a
green tick that proved nothing) or make the whole suite require a database it
does not otherwise need. The SQLite-expressible half of this -- that the raise
happens at all, and that it stops after the fix -- is covered by
`tests/test_comm_v2_direct_conversation_reopen.py`.

## Safety

DSN-guarded. It creates and drops `comm_v2_*`, `users` and `user_settings`, so
it refuses anything that is not plainly a local throwaway: the host must be
loopback. There is no flag to override that.

    docker run -d --rm --name cv2pg -e POSTGRES_PASSWORD=devcheck \
        -e POSTGRES_DB=ddlcheck -p 55433:5432 postgres:18
    DATABASE_URL=postgresql://postgres:devcheck@127.0.0.1:55433/ddlcheck \
        /Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python \
        scripts/verify_comm_v2_direct_key_conflict_on_postgres.py
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _guard(url: str) -> None:
    """Refuse anything that is not plainly a local throwaway.

    Checked before `services.db` is imported, because importing it opens the
    connection pool against whatever `DATABASE_URL` says.
    """
    if not url:
        sys.exit("DATABASE_URL is unset. This script needs a throwaway PostgreSQL.")
    if not url.startswith("postgres"):
        sys.exit(f"DATABASE_URL is not PostgreSQL ({url.split(':')[0]}:...). "
                 "Running this on SQLite would prove the opposite of the point.")
    host = (urlparse(url).hostname or "").lower()
    if host not in LOOPBACK:
        sys.exit(f"REFUSED: host {host!r} is not loopback. This script drops and "
                 "recreates comm_v2_* -- point it at a container.")


_guard(os.environ.get("DATABASE_URL", ""))

from services import db as db_service  # noqa: E402

# Without this the module silently falls back to SQLite and every check below
# passes vacuously against the very engine the proof exists to escape.
if db_service.ENGINE_NAME != "postgresql":
    sys.exit(f"REFUSED: services.db resolved engine {db_service.ENGINE_NAME!r}, not postgresql.")

# `service._open_db` reaches for `bot.db()` and `bot.sqlite3.Row`. Importing the
# real `bot` would run `init_db()` -- ~170 tables -- against this container at
# module scope, for two attributes. A stand-in keeps the proof about the one
# function under test. `_ensure_columns` is a no-op when `bot` has no
# `add_columns_if_missing`, which is exactly the surface omitted here.
import sqlite3  # noqa: E402

_bot_stub = types.ModuleType("bot")
_bot_stub.sqlite3 = sqlite3
_bot_stub.db = db_service.connect
sys.modules.setdefault("bot", _bot_stub)

from pulse_communications_v2 import service  # noqa: E402

USER_A = 9001
USER_B = 9002

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))


def reset_schema() -> None:
    """A fresh database for each run, built the way production builds it.

    `models.ensure_schema` is the real DDL, translated by `services/db`, so the
    `direct_key` unique index under test is the one production carries rather
    than one this script invented.
    """
    conn = db_service.connect()
    cur = conn.cursor()
    for table in (
        "comm_v2_participants", "comm_v2_messages", "comm_v2_blocks",
        "comm_v2_conversations", "user_settings", "users",
    ):
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.commit()
    # `users` and `user_settings` are bot.init_db's, not comm_v2's; only the
    # columns this path reads. They must exist: on PostgreSQL a SELECT against a
    # missing table aborts the transaction, and `message_privacy._settings_rows`
    # swallows that exception -- which would poison the connection here for a
    # reason that has nothing to do with the bug.
    cur.execute(
        "CREATE TABLE users (user_id SERIAL PRIMARY KEY, username TEXT, "
        "display_name TEXT, avatar_url TEXT, email TEXT)"
    )
    cur.execute(
        "CREATE TABLE user_settings (id SERIAL PRIMARY KEY, user_id INTEGER, "
        "setting_key TEXT, setting_value TEXT)"
    )
    conn.commit()
    service._SCHEMA_READY = False
    schema_conn, schema_cur = service._open_db()
    for uid in (USER_A, USER_B):
        schema_cur.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (uid, f"u{uid}"))
    schema_conn.commit()
    schema_conn.close()
    conn.close()


def direct_key_rows(key: str) -> list[dict]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, COALESCE(deleted_at,'') AS deleted_at, status FROM comm_v2_conversations "
        "WHERE direct_key=? ORDER BY id",
        (key,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def soft_delete(conversation_id: int) -> None:
    """What a user leaving/deleting a thread does: set `deleted_at`, keep the row.

    The row stays, and so does its `direct_key` -- which is the whole collision.
    """
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE comm_v2_conversations SET deleted_at=?, status='deleted' WHERE id=?",
        (service._now(), int(conversation_id)),
    )
    conn.commit()
    conn.close()


def active_participants(conversation_id: int) -> set[int]:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM comm_v2_participants WHERE conversation_id=? "
        "AND membership_state='active' AND COALESCE(left_at,'')=''",
        (int(conversation_id),),
    )
    out = {int(dict(r)["user_id"]) for r in cur.fetchall()}
    conn.close()
    return out


def main() -> int:
    print(f"engine={db_service.ENGINE_NAME}  url={urlparse(os.environ['DATABASE_URL']).hostname}")
    reset_schema()
    direct_key = ":".join(str(x) for x in sorted([USER_A, USER_B]))

    print("\n[1] first open creates the thread")
    first = service.create_conversation(USER_A, {"conversation_type": "direct", "target_user_id": USER_B})
    check("first open succeeds", bool(first.get("ok")), str(first.get("message") or first.get("status")))
    first_id = int(first.get("conversation_id") or 0)
    check("first open returns an id", first_id > 0, f"id={first_id}")
    check("both members active", active_participants(first_id) == {USER_A, USER_B},
          str(sorted(active_participants(first_id))))

    print("\n[2] reopening a live thread is idempotent (the existing-row branch)")
    second = service.create_conversation(USER_A, {"conversation_type": "direct", "target_user_id": USER_B})
    check("second open succeeds", bool(second.get("ok")), str(second.get("message") or second.get("status")))
    check("second open returns the same id", int(second.get("conversation_id") or 0) == first_id,
          f"{second.get('conversation_id')} vs {first_id}")
    check("still exactly one row for the key", len(direct_key_rows(direct_key)) == 1,
          f"rows={len(direct_key_rows(direct_key))}")

    print("\n[3] THE BUG: reopening a soft-deleted thread")
    soft_delete(first_id)
    rows = direct_key_rows(direct_key)
    check("soft-deleted row still holds the key", len(rows) == 1 and bool(rows[0]["deleted_at"]),
          f"deleted_at={rows[0]['deleted_at'] if rows else None!r}")
    raised: Exception | None = None
    third: dict = {}
    try:
        third = service.create_conversation(USER_A, {"conversation_type": "direct", "target_user_id": USER_B})
    except Exception as exc:  # noqa: BLE001 -- the raise *is* the finding
        raised = exc
    if raised is not None:
        detail = f"{raised.__class__.__name__}: {str(raised).strip().splitlines()[0][:140]}"
        check("reopen does NOT raise", False, detail)
        # Name the constraint, so a pass here can never be some other failure
        # wearing this check's label.
        check("the raise is the direct_key unique violation", False,
              "direct_key" in str(raised) or "comm_v2_conversations" in str(raised))
    else:
        check("reopen does NOT raise", True)
        check("reopen succeeds", bool(third.get("ok")), str(third.get("message") or third.get("status")))
        reopened_id = int(third.get("conversation_id") or 0)
        check("reopen revives the SAME row, not a second one", reopened_id == first_id,
              f"{reopened_id} vs {first_id}")
        check("no duplicate row for the key", len(direct_key_rows(direct_key)) == 1,
              f"rows={len(direct_key_rows(direct_key))}")
        revived = direct_key_rows(direct_key)[0]
        check("revived row is undeleted", not revived["deleted_at"], f"deleted_at={revived['deleted_at']!r}")
        check("revived row is active", revived["status"] == "active", f"status={revived['status']!r}")
        check("both members active again", active_participants(first_id) == {USER_A, USER_B},
              str(sorted(active_participants(first_id))))

    print("\n[4] a fresh connection still works afterwards")
    # On PostgreSQL a failed statement poisons its transaction until something
    # unwinds it. This is what distinguishes the two engines, and it is why the
    # proof belongs here rather than only in the SQLite suite.
    try:
        probe = db_service.connect()
        probe_cur = probe.cursor()
        probe_cur.execute("SELECT COUNT(*) AS n FROM comm_v2_conversations")
        count = int(dict(probe_cur.fetchone())["n"])
        probe.close()
        check("pool still usable after the attempt", True, f"conversations={count}")
    except Exception as exc:  # noqa: BLE001
        check("pool still usable after the attempt", False, f"{exc.__class__.__name__}: {exc}")

    failures = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    if failures:
        print("FAILED:")
        for label, _, detail in failures:
            print(f"  - {label}" + (f"  -- {detail}" if detail else ""))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
