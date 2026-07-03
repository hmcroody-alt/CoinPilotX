#!/usr/bin/env python3
"""Run PulseSoc Intelligence collectors outside user request paths."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.intelligence_collectors import collector_keys, run_collectors  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PulseSoc Intelligence source collectors.")
    parser.add_argument("--stream", choices=collector_keys(), help="Run one collector stream.")
    parser.add_argument("--all", action="store_true", help="Run every registered collector.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and normalize only; do not persist events.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum candidates per collector.")
    parser.add_argument("--deliver", action="store_true", help="Create notification delivery jobs after live ingest.")
    parser.add_argument("--target-user-id", type=int, default=0, help="Optional target user for delivery QA.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.all and not args.stream:
        args.all = True
    result = run_collectors(
        stream_key=args.stream or "",
        all_streams=bool(args.all),
        dry_run=bool(args.dry_run),
        limit=max(1, int(args.limit or 20)),
        deliver=bool(args.deliver),
        target_user_id=int(args.target_user_id or 0),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"ok={result.get('ok')} dry_run={result.get('dry_run')} duration_ms={result.get('duration_ms')} candidates={result.get('candidate_count')} accepted={result.get('accepted_count')}")
        for run in result.get("runs") or []:
            print(f"- {run.get('stream')}: {run.get('status')} candidates={run.get('candidate_count')} run_id={run.get('collector_run_id')} {run.get('message') or ''}".rstrip())
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
