#!/usr/bin/env python3
"""CLI for the one-shot CJ stock repair. All logic lives in `suppliers/repair.py`.

Thin on purpose: a repair that can only be exercised by running it against a
production database is a repair nobody can test. Everything here is argument
parsing and printing; every decision is in the module, where the unit tests
reach it.

    # safe anywhere, no network, no write
    python scripts/repair_cj_inventory.py --mode audit

    # reads CJ, writes nothing, prints the plan `apply` would execute
    python scripts/repair_cj_inventory.py --mode dry-run

    # the same, bounded to two products, and committed
    python scripts/repair_cj_inventory.py --mode canary --limit 2 \
        --checkpoint /tmp/cj-canary.json

    python scripts/repair_cj_inventory.py --mode apply \
        --checkpoint /tmp/cj-apply.json

    # put it back
    python scripts/repair_cj_inventory.py --rollback /tmp/cj-apply.json

Needs `BUSINESS_OS_SUPPLIERS_CJ` and `CJ_NETWORK_ENABLED` for every mode except
`audit`, and a DATABASE_URL reaching the database being repaired.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.business_os.suppliers import repair  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=repair.MODES, default=repair.AUDIT)
    parser.add_argument("--connection", default=None,
                        help="limit to one supplier connection id")
    parser.add_argument("--limit", type=int, default=None,
                        help="products to touch; required for canary")
    parser.add_argument("--checkpoint", default=None,
                        help="where to write the rollback checkpoint")
    parser.add_argument("--rollback", default=None,
                        help="restore a checkpoint and exit")
    parser.add_argument("--json", action="store_true",
                        help="emit the full machine-readable report")
    args = parser.parse_args(argv)

    if args.rollback:
        with open(args.rollback, "r", encoding="utf-8") as handle:
            counts = repair.restore(json.load(handle))
        print(f"restored {counts['variants']} variants, {counts['sources']} sources, "
              f"{counts['listings']} listings")
        return 0

    if args.mode == repair.CANARY and not args.limit:
        parser.error("--limit is required for canary; that is what makes it a canary")
    if args.mode in repair.WRITING_MODES and not args.checkpoint:
        parser.error("--checkpoint is required for a mode that writes")

    # Handed to `run`, not saved from its result: it writes the checkpoint
    # before the first mutation, so a run that dies half way is still reversible.
    report = repair.run(mode=args.mode, connection_id=args.connection,
                        limit=args.limit, checkpoint_path=args.checkpoint)
    if args.checkpoint and report.get("checkpoint"):
        print(f"checkpoint written to {args.checkpoint}")

    print(repair.dumps(report) if args.json else repair.summarize(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
