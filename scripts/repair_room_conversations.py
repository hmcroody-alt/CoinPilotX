#!/usr/bin/env python3
"""Audit or explicitly repair canonical chat bindings for legacy text Rooms."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "0")

import bot
from services import pulse_chat_bridge


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Perform idempotent repairs; default is dry-run.")
    parser.add_argument("--room-id", type=int, default=0, help="Limit repair to one legacy room id.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum active rooms inspected or repaired.")
    args = parser.parse_args()

    conn = bot.db(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    where = ["c.conversation_type='room'", "COALESCE(c.status,'active')='active'", "COALESCE(c.deleted_at,'')='' "]
    params: list[int] = []
    if args.room_id:
        where.append("c.id=?")
        params.append(args.room_id)
    cur.execute(
        f"""
        SELECT c.id, COALESCE(c.comm_v2_conversation_id,0) AS mapped_id,
               CASE WHEN v.id IS NULL THEN 0 ELSE 1 END AS target_exists
        FROM pulse_conversations c
        LEFT JOIN comm_v2_conversations v ON v.id=c.comm_v2_conversation_id
        WHERE {' AND '.join(where)}
        ORDER BY c.id ASC LIMIT ?
        """,
        tuple(params + [max(1, min(args.limit, 5000))]),
    )
    rooms = [dict(row) for row in cur.fetchall()]
    valid = [room for room in rooms if int(room.get("mapped_id") or 0) and int(room.get("target_exists") or 0)]
    missing = [room for room in rooms if not int(room.get("mapped_id") or 0)]
    stale = [room for room in rooms if int(room.get("mapped_id") or 0) and not int(room.get("target_exists") or 0)]
    print(f"MODE={'APPLY' if args.apply else 'DRY_RUN'}")
    print(f"ROOMS_TOTAL={len(rooms)}")
    print(f"ROOMS_WITH_VALID_CONVERSATION={len(valid)}")
    print(f"ROOMS_MISSING_CONVERSATION={len(missing)}")
    print(f"ROOMS_WITH_STALE_MAPPING={len(stale)}")
    print(f"ROOMS_REPAIRABLE={len(missing) + len(stale)}")
    if not args.apply:
        conn.close()
        return 0

    repaired = 0
    failed = 0
    for room in missing + stale:
        if pulse_chat_bridge.sync_thread(cur, conn, int(room["id"])):
            repaired += 1
        else:
            failed += 1
    conn.close()
    print(f"ROOMS_REPAIRED={repaired}")
    print(f"ROOMS_FAILED={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
