#!/usr/bin/env python3
"""Backfill PulseSoc Growth Engine records for existing users."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import pulsesoc_growth_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill universal PulseSoc Growth Engine provisioning.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum users to process in this batch.")
    parser.add_argument("--after-user-id", type=int, default=0, help="Resume after this user_id.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    result = pulsesoc_growth_engine.backfill_missing_growth_engines(limit=args.limit, after_user_id=args.after_user_id)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "PulseSoc Growth Engine backfill "
            f"processed={result.get('processed')} created={result.get('created')} "
            f"next_cursor={result.get('next_cursor')} has_more={result.get('has_more')}"
        )
        for error in result.get("errors") or []:
            print(f" - user_id={error.get('user_id')} error={error.get('error')}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
