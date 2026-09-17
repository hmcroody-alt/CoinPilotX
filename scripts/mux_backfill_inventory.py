"""Read-only inventory of video rows that are still CDN-bound.

`resolve_media` now serves the Mux rendition for any video row that has a
playback id, so the only rows still handed a URL the Cloudflare edge rule
challenges are the ones with no Mux asset at all. This lists exactly those,
plus whether each one carries the storage key a presigned-ingest backfill
would need.

Writes nothing. Run under:
    railway run --service Postgres .venv/bin/python scripts/mux_backfill_inventory.py
"""

import os

import psycopg2
import psycopg2.extras


VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".avi")


def _columns(cur, table):
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


def inventory(cur, table, url_col):
    cols = _columns(cur, table)
    if not cols:
        print(f"\n== {table}: table not present ==")
        return
    if url_col not in cols:
        print(f"\n== {table}: no {url_col} column (has: {sorted(cols)[:12]}) ==")
        return

    # storage_key is what a presigned-URL backfill would hand to Mux; report its
    # presence per row rather than assuming, because a row that has neither a
    # Mux id nor a storage key cannot be backfilled by any automated path.
    key_col = "storage_key" if "storage_key" in cols else None
    has_mux = "mux_playback_id" in cols

    select = [f"{url_col} AS url"]
    if "id" in cols:
        select.insert(0, "id")
    if key_col:
        select.append(f"{key_col} AS storage_key")
    if has_mux:
        select.append("mux_playback_id")
    if "mux_status" in cols:
        select.append("mux_status")

    ext_filter = " OR ".join([f"lower({url_col}) LIKE '%%{e}'" for e in VIDEO_EXTS])
    where = [f"({ext_filter})", f"{url_col} IS NOT NULL", f"{url_col} <> ''"]
    if has_mux:
        where.append("(mux_playback_id IS NULL OR mux_playback_id = '')")

    cur.execute(f"SELECT {', '.join(select)} FROM {table} WHERE {' AND '.join(where)}")
    rows = cur.fetchall()

    print(f"\n== {table}: {len(rows)} video rows with no Mux asset ==")
    if not has_mux:
        print("   (no mux_playback_id column -- every video row here is CDN-only)")
    backfillable = 0
    for row in rows:
        d = dict(row)
        key = d.get("storage_key") or ""
        if key:
            backfillable += 1
        print(
            f"   id={d.get('id')!s:<8} key={'YES' if key else 'NO ':<4} "
            f"status={str(d.get('mux_status') or '-'):<12} {str(d.get('url'))[:88]}"
        )
    if rows:
        print(f"   -> {backfillable}/{len(rows)} have a storage_key (presigned ingest possible)")


def main():
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_PUBLIC_URL not set -- run under `railway run --service Postgres`")
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Column names differ per table and are not guessable -- pulse_videos
        # carries media_url, pulse_media_assets only ever holds playback_url.
        for table, url_col in (
            ("chat_media_uploads", "public_url"),
            ("pulse_media_assets", "playback_url"),
            ("pulse_videos", "media_url"),
            ("pulse_status_media", "media_url"),
        ):
            try:
                inventory(cur, table, url_col)
            except Exception as exc:  # keep going; one bad table should not hide the rest
                conn.rollback()
                print(f"\n== {table}: FAILED {exc} ==")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
