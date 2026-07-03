#!/usr/bin/env python3
"""Run PulseSoc Intelligence collectors from cron/Railway worker.

This intentionally does not run during user requests. Production deployments can
schedule this script per stream/cadence.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import pulsesoc_intelligence_engine as engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="PulseSoc Intelligence collector worker")
    parser.add_argument("--stream", default="pulsesoc_discoveries", help="stream key to collect")
    parser.add_argument("--target-user-id", type=int, default=0, help="optional QA target user id")
    parser.add_argument("--deliver", action="store_true", help="deliver accepted signals to the target/subscribers")
    parser.add_argument("--cadence", action="store_true", help="run the one-alert-per-three-hours delivery cadence")
    parser.add_argument("--force", action="store_true", help="force the cadence job even if it is not due")
    parser.add_argument("--limit", type=int, default=500, help="maximum eligible users to match for cadence delivery")
    args = parser.parse_args()
    if args.cadence:
        result = engine.run_alert_cadence(force=bool(args.force), target_user_id=args.target_user_id, limit=args.limit)
    else:
        result = engine.run_internal_collector(args.stream, target_user_id=args.target_user_id, deliver=args.deliver)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
