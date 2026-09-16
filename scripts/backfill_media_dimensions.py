#!/usr/bin/env python3
"""Recover the width/height that `_image_dimensions` never recorded.

Pillow was missing from requirements.txt, so the measurement step in
`media_service.save_upload` raised ImportError inside a bare `except` on every
production deploy. 318 of 318 uploaded images stored width/height NULL. This
reads each image's header back out of durable storage and writes the real size.

Why it matters beyond tidiness: the shipped mobile client's image gate treated a
zero dimension as evidence of a dead upload and dropped the media, so a healthy
photo rendered as a post with no picture. The client-side gate is fixed, but
that fix only reaches users who install a new build. Restoring the dimensions
repairs the posts for the app people already have.

Only width and height are ever written. No row is deleted, no URL is rewritten,
no availability flag is touched.

    python3 scripts/backfill_media_dimensions.py              # dry run
    python3 scripts/backfill_media_dimensions.py --commit     # write

Needs R2 credentials and DATABASE_URL, so in practice:

    railway run --service coinpilotx-media-engine python3 scripts/backfill_media_dimensions.py
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Enough for the header of every format the uploader accepts. JPEG is the only
# one that can push a SOF marker deep, and only past large EXIF thumbnails; a
# miss here costs one extra full fetch, not a wrong answer.
HEADER_BYTES = 128 * 1024


def _measure(blob: bytes):
    from PIL import Image

    with Image.open(io.BytesIO(blob)) as img:
        width, height = img.size
        return int(width or 0), int(height or 0)


def _fetch(media_storage, key: str, byte_range: str = "") -> bytes:
    response = media_storage.get_object(key, byte_range=byte_range)
    return response["Body"].read()


def _dimensions_for(media_storage, row: dict):
    """Header first, whole object only if the header was not enough."""
    key = (row.get("storage_key") or row.get("object_key") or "").strip()
    if not key:
        return None, None, "no_storage_key"
    try:
        blob = _fetch(media_storage, key, f"bytes=0-{HEADER_BYTES - 1}")
    except Exception as exc:
        return None, None, f"fetch_failed:{type(exc).__name__}"
    try:
        width, height = _measure(blob)
        return width, height, "header"
    except Exception:
        pass
    try:
        blob = _fetch(media_storage, key)
        width, height = _measure(blob)
        return width, height, "full"
    except Exception as exc:
        return None, None, f"unreadable:{type(exc).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="write the measured dimensions")
    parser.add_argument("--limit", type=int, default=0, help="stop after N rows")
    parser.add_argument("--context-type", default="", help="restrict to one context_type")
    args = parser.parse_args()

    try:
        import PIL  # noqa: F401
    except Exception:
        print("Pillow is not installed; this script cannot measure anything.", file=sys.stderr)
        return 2

    from services import db as db_module
    from services import media_storage

    conn = db_module.connect()
    cur = conn.cursor()

    # is_available=0 rows are excluded on purpose: the server already knows
    # those bytes are gone, and a failed fetch there is the expected answer
    # rather than a problem to report.
    where = [
        "media_type IN ('image','gif')",
        "(width IS NULL OR width = 0 OR height IS NULL OR height = 0)",
        "COALESCE(is_available, 1) = 1",
        "COALESCE(storage_key, object_key, '') <> ''",
    ]
    params: list = []
    if args.context_type:
        where.append("context_type = ?")
        params.append(args.context_type)
    sql = f"SELECT id, context_type, storage_key, object_key, original_filename FROM chat_media_uploads WHERE {' AND '.join(where)} ORDER BY id"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cur.execute(sql, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]

    mode = "COMMIT" if args.commit else "DRY RUN"
    print(f"[{mode}] {len(rows)} candidate image rows")

    measured = skipped = written = 0
    failures: dict[str, int] = {}
    for row in rows:
        width, height, how = _dimensions_for(media_storage, row)
        if not (width and height):
            skipped += 1
            failures[how] = failures.get(how, 0) + 1
            print(f"  SKIP  id={row['id']} context={row['context_type']} reason={how}")
            continue
        measured += 1
        print(f"  OK    id={row['id']} context={row['context_type']} {width}x{height} via={how}")
        if args.commit:
            # Width and height only, and only where still unset, so a row that
            # a concurrent upload path has since measured is left alone.
            cur.execute(
                """
                UPDATE chat_media_uploads
                SET width = ?, height = ?
                WHERE id = ? AND (width IS NULL OR width = 0 OR height IS NULL OR height = 0)
                """,
                (width, height, row["id"]),
            )
            written += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 1

    if args.commit:
        conn.commit()
    conn.close()

    print()
    print(f"measured={measured} skipped={skipped} written={written if args.commit else 0}")
    for reason, count in sorted(failures.items(), key=lambda kv: -kv[1]):
        print(f"  {reason}: {count}")
    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
