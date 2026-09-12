#!/usr/bin/env python3
"""Ask every provider whether the model this deployment sends still exists.

    python3 scripts/undx_model_audit.py              # probe everything
    python3 scripts/undx_model_audit.py openai meta  # probe two
    python3 scripts/undx_model_audit.py --json       # machine-readable
    python3 scripts/undx_model_audit.py --dry-run    # probe, record nothing

`--dry-run` still calls the providers and still spends the money — there is no
version of this check that does not. What it skips is the bookkeeping: the
ledger and the shared health surface are left alone, so a run from a laptop
against production credentials cannot close a circuit or move a budget total.

This spends real money — one small completion per provider — because the
alternative, diffing a model-listing endpoint, reports green through the exact
outage it exists to catch. See the `services.undx_model_audit` docstring for
the evidence.

Run it from one place. Nine workers each running an audit is a nine-way herd
against providers that may already be failing.

Exit status is 1 when any provider is in an actionable state (dead key, unpaid
invoice, retired model) so a scheduler can act on it without parsing anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import undx_health, undx_model_audit  # noqa: E402

MARK = {
    undx_health.HEALTHY: "ok  ",
    undx_health.DEGRADED: "??  ",
    undx_health.RATE_LIMITED: "429 ",
    undx_health.UNAVAILABLE: "down",
    undx_health.AUTH_FAILED: "KEY ",
    undx_health.BILLING_FAILED: "BILL",
    undx_health.MODEL_RETIRED: "GONE",
    undx_health.CIRCUIT_OPEN: "rest",
    undx_health.UNKNOWN: "----",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("providers", nargs="*", help="default: all configured")
    parser.add_argument("--json", action="store_true", help="emit the raw result")
    parser.add_argument("--timeout", type=int, default=undx_model_audit.PROBE_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true",
                        help="probe, but record nothing to the ledger or the "
                             "health surface")
    args = parser.parse_args()

    result = undx_model_audit.audit(args.providers or None,
                                    timeout=args.timeout,
                                    record=not args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1

    print(f"UNDX model audit — {result['probes']} probes, "
          f"{result['elapsed_ms']}ms, "
          f"${result['cost_usd']:.6f}"
          f"{'' if result['cost_complete'] else ' (floor: an unpriced model was probed)'}")
    if args.dry_run:
        print("dry run: nothing was recorded")
    print()
    for name in sorted(result["providers"]):
        row = result["providers"][name]
        note = row["skipped"] or row["error"] or ""
        if row["model_mismatch"]:
            note = f"served {row['served_model']!r}; {note}".strip("; ")
        print(f"  {MARK.get(row['state'], '    ')}  {name:<12} {row['model']:<28} "
              f"{row['state']:<15} {note[:60]}")
    print()
    if result["retired"]:
        print("RETIRED — the configured model no longer exists: "
              + ", ".join(result["retired"]))
    if result["actionable"]:
        print("Needs a person, not a retry: " + ", ".join(result["actionable"]))
    else:
        print("Nothing actionable.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
