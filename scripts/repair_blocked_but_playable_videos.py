#!/usr/bin/env python3
"""Clear ``processing_blocked`` from videos that Mux had already published.

Companion to the ``process_playback_backlog`` fix. Until that landed, the backlog
query keyed only on ``playback_storage_key`` -- which the Mux path never writes --
so every ``.mov`` Mux had already transcoded stayed queued forever. Each cycle the
worker re-downloaded the source from R2, failed to transcode it (the spatial-audio
``-map 0:a?`` bug, or a source that only ever lived in Mux), and stamped the row
``processing_status='processing_blocked'`` with the ffmpeg error. Measured
2026-09-14: 104 of 108 queued rows were in this state while serving fine.

Nothing reads ``processing_blocked`` today, so this is diagnostic state rather than
an outage -- but it is wrong, and it hides real failures in the same column.

``processing_status`` is written to ``processing_blocked`` only by media_worker, so
a row in that state that *also* carries a playback URL is definitionally this bug.
The repair still proves it: it fetches the playback manifest and only clears rows
that actually serve. It does the least it can -- restores the status the Mux writer
would have set and clears the two error columns it knows the worker wrote. It never
touches ``playback_url``, ``is_available``, or any moderation status.

Ordering matters: run this only after the backlog fix is deployed. Against the old
code the worker re-blocks every repaired row within a cycle or two.

Safety: read-only unless ``--apply`` is passed. It skips any row whose playback URL
does not serve -- those are real failures and must keep their error text.

    ./.venv/bin/python scripts/repair_blocked_but_playable_videos.py          # report
    ./.venv/bin/python scripts/repair_blocked_but_playable_videos.py --apply  # write

Needs only the database URL:

    railway run --service Postgres ./.venv/bin/python scripts/repair_blocked_but_playable_videos.py
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from collections import Counter

# Mirrors the ready-state test in services.media_service, kept literal so the script
# can run against a deploy whose code predates the import.
MUX_READY_STATES = ("ready", "asset_ready", "available")


def _database_url() -> str:
    for name in ("DATABASE_PUBLIC_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value:
            return value
    sys.exit("No DATABASE_PUBLIC_URL or DATABASE_URL in the environment.")


def _serves(url: str, timeout: int) -> tuple[bool, str]:
    """Confirm the playback URL actually returns media, not a 404 or a stub."""
    if not url.lower().startswith(("http://", "https://")):
        # The worker's own R2 path writes a relative "/api/pulse/media/<id>/stream".
        # Verifying that needs an app host and a session, so leave it to a human.
        return False, "relative URL - not externally verifiable"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "pulsesoc-media-repair"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            head = response.read(64).decode("utf-8", "replace")
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__})"
    if url.lower().split("?", 1)[0].endswith(".m3u8") and "#EXTM3U" not in head:
        return False, "not an HLS manifest"
    return True, "serves"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repair (default: report only)")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of rows repaired")
    parser.add_argument("--timeout", type=int, default=15, help="per-URL fetch timeout in seconds")
    args = parser.parse_args()

    import psycopg2

    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, COALESCE(mux_status, ''), COALESCE(playback_url, ''),
               COALESCE(availability_error, ''), COALESCE(error_message, '')
        FROM chat_media_uploads
        WHERE deleted_at IS NULL
          AND media_type = 'video'
          AND processing_status = 'processing_blocked'
          AND COALESCE(playback_url, '') <> ''
        ORDER BY id DESC
        """
    )
    rows = cur.fetchall()

    tally: Counter[str] = Counter()
    errors_cleared: Counter[str] = Counter()
    repairable: list[int] = []
    for media_id, mux_status, playback_url, availability_error, error_message in rows:
        if mux_status.lower() not in MUX_READY_STATES:
            # Mid-flight in Mux. Let the Mux reconciler land the terminal state.
            tally[f"mux_status={mux_status or '(empty)'} - not ready, SKIPPED"] += 1
            continue
        ok, reason = _serves(playback_url, args.timeout)
        if not ok:
            # A genuinely broken row. Its error text is the only record of that.
            tally[f"{reason} - SKIPPED"] += 1
            continue
        tally["blocked but serving - repairable"] += 1
        errors_cleared[(availability_error or error_message or "(no error text)")[:80]] += 1
        repairable.append(int(media_id))

    print(f"candidates={len(rows)}")
    for label, count in sorted(tally.items(), key=lambda item: -item[1]):
        print(f"  {count:4d}  {label}")

    if errors_cleared:
        print("\nerror text that would be cleared:")
        for label, count in sorted(errors_cleared.items(), key=lambda item: -item[1]):
            print(f"  {count:4d}  {label!r}")

    if args.limit and len(repairable) > args.limit:
        repairable = repairable[: args.limit]

    if not repairable:
        print("\nNothing to repair.")
        return 0
    if not args.apply:
        print(f"\nDRY RUN. {len(repairable)} row(s) would be returned to 'ready'. Pass --apply to write.")
        return 0

    cur.execute(
        """
        UPDATE chat_media_uploads
        SET processing_status = 'ready',
            availability_error = '',
            error_message = '',
            updated_at = NOW()::text
        WHERE id = ANY(%s)
        """,
        (repairable,),
    )
    conn.commit()
    print(f"\nRepaired {cur.rowcount} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
