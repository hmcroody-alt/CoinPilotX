"""Poll Mux for the backfilled assets and write their real status back.

The backfill records `preparing`, because that is what Mux returns at creation.
`resolve_media` will not substitute the Mux rendition until the row says ready
-- deliberately, since a rendition that does not exist yet answers 404. So if
nothing ever advances the row, the backfill silently achieves nothing.

Rather than assume the webhook or the media worker will do it, this asks Mux
directly and writes the answer. Safe to re-run.

    railway run --service CoinPilotX .venv/bin/python scripts/mux_backfill_finalize.py
    railway run --service CoinPilotX .venv/bin/python scripts/mux_backfill_finalize.py --apply
"""

import os
from datetime import datetime, timezone
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import media_service  # noqa: E402


APPLY = "--apply" in sys.argv
IDS = (82, 92, 93, 94)


def main():
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or ""
    if not dsn:
        raise SystemExit("no externally reachable Postgres URL")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        "SELECT id, mux_asset_id, mux_playback_id, mux_status FROM chat_media_uploads "
        "WHERE id = ANY(%s) ORDER BY id",
        (list(IDS),),
    )
    rows = [dict(r) for r in cur.fetchall()]

    for row in rows:
        asset_id = row["mux_asset_id"]
        result = media_service._mux_json_request(f"/assets/{asset_id}", timeout=20)
        if not result.get("ok"):
            print(f"id={row['id']:<5} query failed {result.get('status_code')}")
            continue
        data = result.get("data") or {}
        live_status = str(data.get("status") or "")
        print(
            f"id={row['id']:<5} db={row['mux_status']:<10} mux={live_status:<10} "
            f"playback={row['mux_playback_id']}"
        )
        if APPLY and live_status and live_status != row["mux_status"]:
            # mux_ready_at is a text column here, so the timestamp is formatted
            # in Python -- a SQL CASE mixing NOW() with it fails to type-check.
            if live_status == "ready":
                cur.execute(
                    "UPDATE chat_media_uploads SET mux_status = %s, mux_ready_at = %s WHERE id = %s",
                    (live_status, datetime.now(timezone.utc).isoformat(timespec="seconds"), row["id"]),
                )
            else:
                cur.execute(
                    "UPDATE chat_media_uploads SET mux_status = %s WHERE id = %s",
                    (live_status, row["id"]),
                )
    if APPLY:
        conn.commit()
        print("committed")
    else:
        conn.rollback()
        print("DRY-RUN")
    conn.close()


if __name__ == "__main__":
    main()
