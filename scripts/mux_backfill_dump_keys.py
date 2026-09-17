"""Dump the storage keys of the video rows that still have no Mux asset.

Read-only. Split from the presign check because the database credentials and
the R2 credentials live on different Railway services, so the two halves have
to run under different `railway run --service` invocations.

    railway run --service Postgres .venv/bin/python scripts/mux_backfill_dump_keys.py
"""

import json
import os

import psycopg2


OUT = "/tmp/mux_backfill_keys.json"


def main():
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_PUBLIC_URL not set -- run under `railway run --service Postgres`")
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, storage_key, public_url, mime_type
            FROM chat_media_uploads
            WHERE (mux_playback_id IS NULL OR mux_playback_id = '')
              AND storage_key IS NOT NULL AND storage_key <> ''
              AND (lower(public_url) LIKE '%%.mp4' OR lower(public_url) LIKE '%%.mov'
                   OR lower(public_url) LIKE '%%.webm' OR lower(public_url) LIKE '%%.m4v')
            ORDER BY id
            """
        )
        rows = [
            {"id": r[0], "storage_key": r[1], "public_url": r[2], "mime_type": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()

    with open(OUT, "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"wrote {len(rows)} rows -> {OUT}")
    for r in rows:
        print(f"  id={r['id']:<6} {r['storage_key']}")


if __name__ == "__main__":
    main()
