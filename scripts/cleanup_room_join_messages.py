#!/usr/bin/env python3
"""Hide legacy visible chat-room join notices from public timelines."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def main():
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        """
        UPDATE pulse_messages
        SET deleted_at=COALESCE(deleted_at, ?),
            status='hidden',
            delivery_status='hidden'
        WHERE COALESCE(deleted_at,'')=''
          AND COALESCE(message_type,'') IN ('system','system_join','chat_event')
          AND lower(COALESCE(body,'')) LIKE '% joined'
        """,
        (now,),
    )
    changed = cur.rowcount if cur.rowcount is not None else 0
    conn.commit()
    conn.close()
    print(f"hidden legacy room join notices: {changed}")


if __name__ == "__main__":
    main()
