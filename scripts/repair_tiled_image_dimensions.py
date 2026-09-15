#!/usr/bin/env python3
"""Re-measure messenger photos whose stored size is a HEIC tile, not the image.

Companion to the ``_probe_dimensions`` fix. Until that landed the probe returned
the first two numbers ffprobe printed, and an iPhone HEIC is routinely a grid of
tiles with one stream per tile. Measured 2026-09-14: attachment 80 has 95
streams -- sixty 512x512 tiles ahead of the 2016x1512 photo -- and its row stored
512x512. Attachment 79 had 54 tiles and the same wrong shape. Any client laying
out a bubble or a placeholder from those numbers reserves a square box for a 4:3
photo.

Those rows read ``ready``, so ``reconcile_processing_backlog`` cannot see them
and ``repair_ghost_thumbnail_keys.py --requeue`` does not either -- it only takes
rows that reached ``failed``. Hence a direct pass.

It repairs the dimensions and nothing else. The thumbnail on these rows is not
affected by the bug: ffmpeg's own stream selection picks the largest video
stream, so the generated JPEG is a scaled copy of the real image and already has
the right aspect ratio. Only the numbers beside it lie.

Photos only. A tiled video is not a thing, and re-deriving a video would mean
handing it back to a worker path that can also re-apply the measured-duration
policy -- a much larger blast radius than the defect being repaired.

Ordering matters: run this only after the fix is deployed. It measures with the
deployed ``_probe_dimensions``, so against the old code it computes the same
wrong pair, finds no disagreement, and reports nothing to do.

Safety: read-only unless ``--apply`` is passed. It downloads each candidate and
measures it rather than inferring anything from the stored values, skips a row
whose object is missing or unreadable, and writes only where a fresh measurement
disagrees with what is stored.

    python3 scripts/repair_tiled_image_dimensions.py                 # report only
    python3 scripts/repair_tiled_image_dimensions.py --ids 79,80     # just those two
    python3 scripts/repair_tiled_image_dimensions.py --ids 79,80 --apply

Run it under a service that has both the database URL and the R2 credentials.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "")

from services.messenger_media_foundation import _probe_dimensions  # noqa: E402


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


def _measure(client, bucket: str, storage_key: str) -> tuple[int, int] | None:
    handle = tempfile.NamedTemporaryFile(delete=False, prefix="tiled-probe-", suffix=".bin")
    handle.close()
    try:
        client.download_file(bucket, storage_key, handle.name)
    except Exception as exc:
        print(f"    fetch failed: {exc}")
        return None
    local = Path(handle.name)
    try:
        return _probe_dimensions(local)
    finally:
        local.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repair (default: report only)")
    parser.add_argument("--ids", default="", help="comma-separated attachment ids to restrict the pass to")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of rows measured")
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="measure every ready photo, not just the square ones (the tiles are square, so "
             "width = height is the signature; this drops that filter)",
    )
    args = parser.parse_args()

    import psycopg2

    client, bucket = _object_client()
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()

    sql = """
        SELECT id, COALESCE(mime_type, ''), COALESCE(storage_key, ''), width, height
        FROM message_attachments
        WHERE deleted_at IS NULL
          AND LOWER(COALESCE(media_type, '')) = 'photo'
          AND COALESCE(storage_key, '') <> ''
          AND COALESCE(width, 0) > 0 AND COALESCE(height, 0) > 0
    """
    params: list[object] = []
    wanted = [int(value) for value in args.ids.replace(",", " ").split()]
    if wanted:
        sql += " AND id = ANY(%s)"
        params.append(wanted)
    elif not args.scan_all:
        sql += " AND width = height"
    sql += " ORDER BY id DESC"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cur.execute(sql, params)
    rows = cur.fetchall()

    print(f"bucket={bucket} candidates={len(rows)}")
    repairs: list[tuple[int, int, int]] = []
    unreadable = 0
    for attachment_id, mime_type, storage_key, width, height in rows:
        measured = _measure(client, bucket, storage_key)
        if measured is None:
            unreadable += 1
            print(f"  {attachment_id:6d}  {mime_type:12s} stored={width}x{height} measured=UNREADABLE - skipped")
            continue
        if measured == (int(width), int(height)):
            continue
        print(f"  {attachment_id:6d}  {mime_type:12s} stored={width}x{height} measured={measured[0]}x{measured[1]}")
        repairs.append((int(attachment_id), measured[0], measured[1]))

    print(f"\ndisagreements={len(repairs)} unreadable={unreadable}")
    if not repairs:
        print("Nothing to repair.")
        return 0
    if not args.apply:
        print(f"DRY RUN. {len(repairs)} row(s) would be re-measured. Pass --apply to write.")
        return 0

    for attachment_id, width, height in repairs:
        cur.execute(
            "UPDATE message_attachments SET width=%s, height=%s, updated_at=NOW()::text WHERE id=%s",
            (width, height, attachment_id),
        )
    conn.commit()
    print(f"Re-measured {len(repairs)} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
