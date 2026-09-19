"""Let the videos that only Mux still holds try for a cover again.

`process_cover_backlog` checks `cover_attempts < MEDIA_WORKER_MAX_ATTEMPTS`
before it does anything, so a row that exhausted the cap is parked for good.
Every one of the 56 production video rows without a usable thumbnail was at the
cap, because the reason they failed was systemic rather than per-row: their
source was never resolvable at all. The fix that makes them resolvable cannot
reach them until the counter is cleared.

Deliberately narrow. Only rows this change actually repaired are reset -- a
video with no stored object whose Mux asset is ready. Rows whose pixels are
simply gone (a deleted R2 object, an ingest that lost the original) are left
parked, because retrying those would burn the worker on work that cannot
succeed. Images are left alone too: they fail for a reason nobody has diagnosed
yet, and resetting them would hide that rather than fix it.

Usage:
    python3 scripts/reset_mux_cover_attempts.py            # report only
    python3 scripts/reset_mux_cover_attempts.py --apply    # clear the counters

Run it against production the way the other media scripts are run:
    railway run --service coinpilotx-media-engine python3 scripts/reset_mux_cover_attempts.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402

# A video with no stored source of any kind, whose Mux asset is ready to be
# read. That is exactly the population `_covers_from_mux_playback` can now
# serve, and nothing else.
ELIGIBLE = """
    deleted_at IS NULL
    AND media_type='video'
    AND COALESCE(is_available, 1)=1
    AND COALESCE(mime_type, '') NOT LIKE 'audio/%'
    AND COALESCE(storage_key, '')=''
    AND COALESCE(object_key, '')=''
    AND COALESCE(mux_status, '')='ready'
    AND COALESCE(mux_playback_id, '')<>''
    AND COALESCE(cover_generated_at, '')=''
    AND COALESCE(cover_attempts, 0) > 0
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="clear the counters (default: report only)")
    args = parser.parse_args()

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"SELECT id, cover_attempts, mux_playback_id FROM chat_media_uploads WHERE {ELIGIBLE} ORDER BY id DESC")
    rows = [dict(row) for row in cur.fetchall()]
    print(f"eligible rows: {len(rows)}", flush=True)
    for row in rows[:10]:
        print(f"  id={row.get('id')} attempts={row.get('cover_attempts')} playback={row.get('mux_playback_id')}", flush=True)
    if len(rows) > 10:
        print(f"  ... and {len(rows) - 10} more", flush=True)

    if not args.apply:
        print("dry run -- re-run with --apply to clear these counters", flush=True)
        conn.close()
        return 0

    cur.execute(f"UPDATE chat_media_uploads SET cover_attempts=0 WHERE {ELIGIBLE}")
    conn.commit()
    conn.close()
    print(f"reset {len(rows)} row(s); the media worker will pick them up on its next cycles", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
