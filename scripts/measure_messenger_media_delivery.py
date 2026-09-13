#!/usr/bin/env python3
"""Measure what a media thread costs to paint, before and after.

§48-49: "faster" is not a claim anyone can check. This produces real media with
ffmpeg, runs the real processing handlers against it, and reports bytes.

"Before" is not a guess or a git checkout -- it is the same attachment rows read
the way the old client read them. The old thread had no derived preview to read
(nothing ever consumed a messenger processing job, so `thumbnail_key` was always
NULL) and resolved its thumbnail slot to the same `/download` URL as the original.
So the before number is the sum of the original assets, measured from the same
files the after number derives from.

Run: .venv/bin/python scripts/measure_messenger_media_delivery.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import messenger_media_foundation as foundation  # noqa: E402

# A thread nobody would call unusual: a short clip, a long one, and four photos
# straight off a phone camera.
THREAD = [
    ("video", "video/mp4", "clip-15s.mp4",
     ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=15",
      "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]),
    ("video", "video/quicktime", "clip-90s.mov",
     ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=90",
      "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]),
] + [
    ("photo", "image/jpeg", f"photo-{n}.jpg",
     ["-f", "lavfi", "-i", f"testsrc2=size=4032x3024:duration=1:rate=1", "-frames:v", "1", "-q:v", "2"])
    for n in range(1, 5)
]


def size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def main() -> int:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("ffmpeg/ffprobe required", file=sys.stderr)
        return 2

    storage = Path(tempfile.mkdtemp(prefix="media-measure-"))
    os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(storage)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    foundation.ensure_schema(cur)
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS comm_v2_conversations (id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
        CREATE TABLE IF NOT EXISTS comm_v2_participants (conversation_id INTEGER, user_id INTEGER, membership_state TEXT, left_at TEXT);
        CREATE TABLE IF NOT EXISTS pulse_jobs (
            id INTEGER PRIMARY KEY, job_type TEXT, target_type TEXT, target_id INTEGER,
            status TEXT, attempts INTEGER, max_attempts INTEGER, created_at TEXT, updated_at TEXT
        );
        INSERT INTO comm_v2_conversations (id, status, deleted_at) VALUES (44, 'active', NULL);
        INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at) VALUES (44, 7, 'active', NULL);
        """
    )
    conn.commit()

    rows = []
    try:
        for media_type, mime_type, name, args in THREAD:
            storage_key = f"messenger/44/{name}"
            target = foundation._local_path(storage_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, str(target)], check=True, timeout=300)
            cur.execute(
                """
                INSERT INTO message_attachments
                (conversation_id, conversation_model, sender_id, media_type, mime_type,
                 original_filename, storage_key, signed_url_strategy, upload_status,
                 processing_status, size_bytes, created_at, updated_at)
                VALUES (?, 'pulse', 7, ?, ?, ?, ?, 'private', 'uploaded', 'queued', ?, ?, ?)
                """,
                (44, media_type, mime_type, name, storage_key, target.stat().st_size,
                 foundation.now_iso(), foundation.now_iso()),
            )
            attachment_id = int(cur.lastrowid)
            conn.commit()

            # The job name is read back from the queue rather than typed here. A
            # name production does not enqueue is retired as `unknown_job_type`
            # without raising, so hardcoding one yields a silent zero: the first
            # run of this script measured a 684x win with both videos skipped.
            foundation._enqueue_processing_jobs(cur, attachment_id, 44, media_type, "queued")
            job = cur.execute(
                "SELECT job_type FROM pulse_jobs WHERE target_type='message_attachment' AND target_id=?",
                (attachment_id,),
            ).fetchone()
            if not job:
                print(f"{name}: production enqueues no processing job", file=sys.stderr)
                return 1
            outcome = foundation.process_attachment(cur, attachment_id, str(job["job_type"]))
            if outcome.get("status") != "processed":
                print(f"{name}: {job['job_type']} -> {outcome}", file=sys.stderr)
                return 1
            conn.commit()

            row = cur.execute("SELECT * FROM message_attachments WHERE id=?", (attachment_id,)).fetchone()
            thumbnail_key = str(row["thumbnail_key"] or "")
            derived = foundation._local_path(thumbnail_key).stat().st_size if thumbnail_key else 0
            rows.append((name, media_type, target.stat().st_size, derived, row["processing_status"]))
    finally:
        conn.close()
        shutil.rmtree(storage, ignore_errors=True)

    before = sum(r[2] for r in rows)
    after = sum(r[3] for r in rows)

    print(f"{'attachment':<16} {'class':<6} {'original':>12} {'preview':>10} {'ratio':>8}  status")
    for name, media_type, original, derived, status in rows:
        ratio = f"{original / derived:.0f}x" if derived else "-"
        print(f"{name:<16} {media_type:<6} {size(original):>12} {size(derived):>10} {ratio:>8}  {status}")

    print()
    print(f"bytes to paint {len(rows)} bubbles")
    print(f"  before (thumbnail slot resolved to the original) : {size(before)}")
    print(f"  after  (derived preview, original on tap only)   : {size(after)}")
    if not after:
        print("  reduction                                        : NOT MEASURED -- no preview was derived")
        return 1
    print(f"  reduction                                        : {(1 - after / before) * 100:.1f}%  ({before / after:.0f}x less)")
    print()
    print("access grants per bubble: 2 before (two hooks, same identity, same URL) -> 1 after")
    return 0


if __name__ == "__main__":
    sys.exit(main())
