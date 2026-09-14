#!/usr/bin/env python3
"""Clear messenger ``thumbnail_key`` values that name an object nobody can fetch.

Companion to the ``_store_derived_thumbnail`` fix. Until that landed, a row whose
``signed_url_strategy`` read ``signed`` -- what a successful direct upload writes
-- had its generated thumbnail written to the container's local disk while the
key was recorded as though it lived in object storage. The disk went away with
the next deploy; the key did not. Measured 2026-09-14: 38 of 39 attachments
carrying a thumbnail key had nothing behind it.

Those rows are invisible to ``reconcile_processing_backlog``, which selects on
``processing_status IN ('queued','processing')`` -- a ghost row reads ``ready``.
So this walks them directly, confirms the absence against object storage rather
than assuming it, and hands the row back to the existing sweep by clearing the
key and returning the status to ``queued``. Nothing here generates a thumbnail;
the worker does that, correctly, once the row is visible to it again.

Ordering matters: run this only after the fix is deployed. Against the old code
the worker regenerates the same ghost and the repair is a no-op that costs a
round of processing.

Clearing the key is only half the repair, and the missing half is not obvious.
``reconcile_processing_backlog`` gives an attachment ``MAX_PROCESSING_ROUNDS``
(4) job rows for all time, then marks it ``failed`` rather than re-queuing it
again. Every attachment stranded by the old dispatcher had already accumulated
exactly 4 -- all of them reading ``done``, none of them having produced anything
-- so a row handed back as ``queued`` was marked ``processing_unrecoverable`` on
the very next sweep instead of being reprocessed. Observed the hard way: of 38
rows cleared in the first run, 35 went straight to ``failed``.

Raising the ceiling would loosen a real safety bound for every attachment, and
deleting the stale job rows would destroy the audit trail. Neither is needed.
The sweep skips any attachment that already has a *live* job -- that is its
"someone else is on it" check -- so enqueuing the job directly hands the work to
the ordinary worker path and the ceiling never comes into play. That is what
``--requeue`` does, and it is why the two phases belong in one script.

Safety: read-only unless ``--apply`` is passed. It never touches a row whose
source object is missing (processing would defer forever on absent bytes), never
touches a deleted row, and only ever clears a key it has just proven is dead.

    python3 scripts/repair_ghost_thumbnail_keys.py              # report only
    python3 scripts/repair_ghost_thumbnail_keys.py --apply      # clear ghost keys
    python3 scripts/repair_ghost_thumbnail_keys.py --requeue    # report phase two
    python3 scripts/repair_ghost_thumbnail_keys.py --requeue --apply

Run it under a service that has both the database URL and the R2 credentials, or
export them yourself.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

# Only these can be re-derived; the sweep has no job type for anything else.
REPAIRABLE_MEDIA_TYPES = ("photo", "video", "voice")
# Mirrors services.messenger_media_foundation.REMOTE_OBJECT_STRATEGIES. Kept
# literal so the script can run against a deploy whose code predates the import.
REMOTE_STRATEGIES = ("r2", "s3", "signed")


def _database_url() -> str:
    for name in ("DATABASE_PUBLIC_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value:
            return value
    sys.exit("No DATABASE_PUBLIC_URL or DATABASE_URL in the environment.")


def _object_client():
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("R2_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL")
    key = os.getenv("R2_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID")
    secret = os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_ACCESS_KEY")
    bucket = os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET")
    if not (endpoint and key and secret and bucket):
        sys.exit("Object storage is not configured in this environment.")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    return client, bucket


JOB_TYPE_BY_MEDIA_TYPE = {
    "photo": "messenger_photo_thumbnail",
    "video": "messenger_video_metadata_thumbnail",
    "voice": "messenger_voice_waveform",
}


def _requeue_exhausted(cur, conn, client, bucket, args) -> int:
    """Give back the attachments the sweep retired because their budget was already spent.

    These read ``failed``/``processing_unrecoverable`` with no thumbnail. The
    budget they exhausted was spent entirely by a dispatcher that retired each
    job without running it, so the ceiling is measuring a failure that never
    actually happened.

    Enqueuing a live job is the whole trick: the sweep's ``NOT EXISTS`` guard
    skips any attachment with a pending or processing job, so the row is handed
    to the normal worker path and never re-tested against the ceiling. No job
    history is deleted and no global bound is relaxed.
    """
    cur.execute(
        """
        SELECT id, media_type, COALESCE(storage_key, '')
        FROM message_attachments
        WHERE deleted_at IS NULL
          AND LOWER(COALESCE(processing_status, '')) = 'failed'
          AND COALESCE(thumbnail_key, '') = ''
          AND LOWER(COALESCE(upload_status, '')) IN ('uploaded', 'attached')
          AND LOWER(COALESCE(media_type, '')) IN %s
          AND NOT EXISTS (
            SELECT 1 FROM pulse_jobs j
            WHERE j.target_type = 'message_attachment' AND j.target_id = message_attachments.id
              AND LOWER(COALESCE(j.status, '')) IN ('pending', 'processing')
          )
        ORDER BY id DESC
        """,
        (REPAIRABLE_MEDIA_TYPES,),
    )
    rows = cur.fetchall()

    eligible: list[tuple[int, str]] = []
    skipped = 0
    for attachment_id, media_type, storage_key in rows:
        job_type = JOB_TYPE_BY_MEDIA_TYPE.get(str(media_type or "").lower())
        if not job_type or not storage_key:
            skipped += 1
            continue
        try:
            client.head_object(Bucket=bucket, Key=storage_key)
        except Exception:
            # Bytes genuinely gone. This is the case the ceiling exists for.
            skipped += 1
            continue
        eligible.append((int(attachment_id), job_type))

    if args.limit and len(eligible) > args.limit:
        eligible = eligible[: args.limit]

    print(f"retired attachments with a recoverable source: {len(eligible)} (skipped {skipped})")
    if not eligible:
        return 0
    if not args.apply:
        print(f"DRY RUN. {len(eligible)} row(s) would be re-queued. Pass --apply to write.")
        return 0

    for attachment_id, job_type in eligible:
        cur.execute(
            """
            INSERT INTO pulse_jobs (job_type, target_type, target_id, status, attempts, max_attempts, created_at, updated_at)
            VALUES (%s, 'message_attachment', %s, 'pending', 0, 3, NOW()::text, NOW()::text)
            """,
            (job_type, attachment_id),
        )
        cur.execute(
            """
            UPDATE message_attachments
            SET processing_status = 'queued', error_code = '', error_message = '', updated_at = NOW()::text
            WHERE id = %s
            """,
            (attachment_id,),
        )
    conn.commit()
    print(f"Re-queued {len(eligible)} attachment(s) with a live job each.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repair (default: report only)")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of rows repaired")
    parser.add_argument(
        "--requeue",
        action="store_true",
        help="phase two: hand attachments the sweep has already given up on back to the worker",
    )
    args = parser.parse_args()

    import psycopg2

    client, bucket = _object_client()
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()

    if args.requeue:
        return _requeue_exhausted(cur, conn, client, bucket, args)
    cur.execute(
        """
        SELECT id, media_type, processing_status, upload_status,
               COALESCE(signed_url_strategy, ''), COALESCE(storage_key, ''), COALESCE(thumbnail_key, '')
        FROM message_attachments
        WHERE COALESCE(thumbnail_key, '') <> ''
          AND deleted_at IS NULL
          AND LOWER(COALESCE(signed_url_strategy, '')) IN %s
          AND LOWER(COALESCE(media_type, '')) IN %s
        ORDER BY id DESC
        """,
        (REMOTE_STRATEGIES, REPAIRABLE_MEDIA_TYPES),
    )
    rows = cur.fetchall()

    def present(key: str) -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False

    tally: Counter[str] = Counter()
    repairable: list[int] = []
    for attachment_id, media_type, processing, upload, strategy, storage_key, thumb_key in rows:
        if present(thumb_key):
            tally["thumbnail present - left alone"] += 1
            continue
        if not storage_key or not present(storage_key):
            # Re-queuing this would defer forever on bytes_unavailable and burn
            # the attachment's processing rounds until it is marked failed.
            tally["source object also missing - SKIPPED"] += 1
            continue
        tally[f"ghost key ({media_type}/{strategy}/{processing}/{upload})"] += 1
        repairable.append(int(attachment_id))

    print(f"bucket={bucket} candidates={len(rows)}")
    for label, count in sorted(tally.items(), key=lambda item: -item[1]):
        print(f"  {count:4d}  {label}")

    if args.limit and len(repairable) > args.limit:
        repairable = repairable[: args.limit]

    if not repairable:
        print("\nNothing to repair.")
        return 0
    if not args.apply:
        print(f"\nDRY RUN. {len(repairable)} row(s) would be cleared and re-queued. Pass --apply to write.")
        return 0

    cur.execute(
        """
        UPDATE message_attachments
        SET thumbnail_key = '',
            processing_status = 'queued',
            error_code = '',
            error_message = '',
            updated_at = NOW()::text
        WHERE id = ANY(%s)
        """,
        (repairable,),
    )
    conn.commit()
    print(f"\nRepaired {cur.rowcount} row(s); the media worker's sweep will re-enqueue them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
