"""Finish the Mux migration for the video rows that still have no asset.

`resolve_media` serves the Mux rendition for any video row that has a playback
id, so the rows with no asset are the only ones left on a CDN URL. There are
two distinct populations and they need opposite treatment:

  * the object is still in the bucket -> create the Mux asset from a presigned
    R2 URL. Presigned rather than the CDN URL because the CDN answers 403 for
    some clients, and `create_mux_asset_from_url` correctly refuses a source it
    cannot read. The presigned URL is signed for GET, so this calls the Mux API
    directly rather than going through create_mux_asset_from_url, whose HEAD
    precheck a GET-signed URL would fail.

  * the object is gone -> there is nothing to migrate. Mark the row unavailable
    so it stops promising media that cannot be fetched by any path.

`public` playback policy matches the 99 existing `pulse` rows in this table.
Messenger DM attachments use `signed` and are created elsewhere; none of the
rows here are DMs.

Idempotent: a row that already has a playback id is skipped, so a re-run after
a partial failure costs nothing and creates no duplicate assets.

    railway run --service CoinPilotX .venv/bin/python scripts/mux_backfill_apply.py
    railway run --service CoinPilotX .venv/bin/python scripts/mux_backfill_apply.py --apply
"""

import os
import sys
import urllib.error
import urllib.request

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import media_service, media_storage  # noqa: E402


APPLY = "--apply" in sys.argv


def object_exists(signed_url):
    """True when the presigned URL yields bytes. Range-limited to one byte."""
    request = urllib.request.Request(signed_url, method="GET", headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return 200 <= int(response.status) < 300, int(response.status)
    except urllib.error.HTTPError as exc:
        return False, int(exc.code)
    except Exception:
        return False, 0


def main():
    # This runs from a workstation via `railway run`, so the service's own
    # DATABASE_URL (postgres.railway.internal) does not resolve here. Prefer the
    # public URL and fall back only to a DATABASE_URL that is not internal.
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or ""
    if not dsn:
        candidate = os.environ.get("DATABASE_URL") or ""
        dsn = candidate if ".railway.internal" not in candidate else ""
    if not dsn:
        raise SystemExit("no externally reachable Postgres URL on this service")
    client = media_storage.object_client()
    if client is None:
        raise SystemExit("R2 not configured on this service")
    bucket = os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")
    if not media_service._mux_auth_header():
        raise SystemExit("Mux credentials missing on this service")
    print(f"mode={'APPLY' if APPLY else 'DRY-RUN'} bucket={bucket}")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(
        """
        SELECT id, storage_key, public_url, context_type
        FROM chat_media_uploads
        WHERE (mux_playback_id IS NULL OR mux_playback_id = '')
          AND storage_key IS NOT NULL AND storage_key <> ''
          AND (lower(public_url) LIKE '%%.mp4' OR lower(public_url) LIKE '%%.mov'
               OR lower(public_url) LIKE '%%.webm' OR lower(public_url) LIKE '%%.m4v')
        ORDER BY id
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    print(f"candidates={len(rows)}\n")

    ingested = dead = failed = 0
    for row in rows:
        media_id, key = row["id"], row["storage_key"]
        signed = client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
        )
        alive, code = object_exists(signed)

        if not alive:
            print(f"id={media_id:<5} GONE   http={code:<4} {key}")
            dead += 1
            if APPLY:
                cur.execute(
                    """
                    UPDATE chat_media_uploads
                    SET is_available = 0,
                        availability_error = %s,
                        availability_checked_at = NOW()
                    WHERE id = %s
                    """,
                    ("object missing from durable storage", media_id),
                )
            continue

        if not APPLY:
            # Creating the asset is the billable, non-transactional half of this
            # script -- a DB rollback would not undo it. So a dry run stops here
            # rather than "previewing" by doing the irreversible part.
            print(f"id={media_id:<5} WOULD  ingest http={code} {key}")
            ingested += 1
            continue

        result = media_service._mux_json_request(
            "/assets",
            method="POST",
            payload={
                "input": signed,
                "playback_policy": ["public"],
                "mp4_support": "standard",
            },
            timeout=30,
        )
        if not result.get("ok"):
            print(
                f"id={media_id:<5} FAIL   {result.get('status_code')} "
                f"{str(result.get('error') or result.get('message'))[:140]}"
            )
            failed += 1
            continue

        data = result.get("data") or {}
        asset_id = str(data.get("id") or "")
        playbacks = data.get("playback_ids") or []
        playback_id = str((playbacks[0] or {}).get("id") or "") if playbacks else ""
        status = str(data.get("status") or "preparing")
        if not (asset_id and playback_id):
            print(f"id={media_id:<5} FAIL   asset created but no playback id: {data}")
            failed += 1
            continue

        print(f"id={media_id:<5} MUX    asset={asset_id} playback={playback_id} status={status}")
        ingested += 1
        if APPLY:
            cur.execute(
                """
                UPDATE chat_media_uploads
                SET mux_asset_id = %s,
                    mux_playback_id = %s,
                    mux_status = %s,
                    mux_asset_created_at = NOW()
                WHERE id = %s
                """,
                (asset_id, playback_id, status, media_id),
            )

    if APPLY:
        conn.commit()
        print("\ncommitted")
    else:
        conn.rollback()
        print("\nDRY-RUN -- nothing written (assets above were NOT created)")
    print(f"ingested={ingested} dead={dead} failed={failed}")
    conn.close()


if __name__ == "__main__":
    main()
