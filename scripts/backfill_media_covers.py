"""One-off cover backfill for existing media.

The media worker already heals legacy rows a few at a time
(process_cover_backlog each cycle). This script is the fast path: run it once
on Railway (or locally against prod DATABASE_URL) to pre-generate covers for
the whole backlog instead of waiting for the sweep.

Usage:
    python3 scripts/backfill_media_covers.py [--batch 10] [--max-rows 0]

Requires ffmpeg and the media storage env (R2 credentials in prod).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import media_worker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=10, help="rows per pass (max 10)")
    parser.add_argument("--max-rows", type=int, default=0, help="stop after N rows (0 = all)")
    args = parser.parse_args()

    media_worker.ensure_media_worker_schema()
    total_processed = 0
    total_failed = 0
    while True:
        result = media_worker.process_cover_backlog(args.batch)
        if result.get("status") == "ffmpeg_missing":
            print("ffmpeg is not installed — set RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg", flush=True)
            return 1
        total_processed += int(result.get("processed") or 0)
        total_failed += int(result.get("failed") or 0)
        print(
            f"pass checked={result.get('checked')} processed={result.get('processed')} "
            f"failed={result.get('failed')} total_processed={total_processed}",
            flush=True,
        )
        if int(result.get("checked") or 0) == 0:
            break
        if args.max_rows and total_processed + total_failed >= args.max_rows:
            break
        time.sleep(0.2)
    print(f"done processed={total_processed} failed={total_failed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
