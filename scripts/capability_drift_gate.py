#!/usr/bin/env python3
"""Capability drift gate — read-only, and deliberately half-toothless.

Prints the drift report and exits non-zero **only** for static contract drift.
Production-observed drift is printed with full severity and does not affect the
exit status, for two reasons spelled out in
:mod:`services.pulse_control_plane.drift`:

* the person whose pull request is being tested did not cause it, and a gate
  that goes red for other people's launches gets routed around; and
* CI cannot reach production anyway, so the observed half is read from a dated
  recording in the tree. Failing on it would be failing on the age of a file.

Usage::

    python3 scripts/capability_drift_gate.py            # report + gate
    python3 scripts/capability_drift_gate.py --json     # machine-readable
    python3 scripts/capability_drift_gate.py --strict   # observed drift fails too

``--strict`` exists for a deliberate pre-activation check, not for CI. Stage 23
requires zero drift before ``feature_flags`` may be wired into production access;
that is the one moment when an observed finding *should* stop the process.

Exit status
-----------

0   no static drift (observed findings may still be present and printed)
1   static contract drift, or --strict with any finding
3   the gate could not run — never treat this as a pass
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on production-observed drift too (pre-activation check, not CI)",
    )
    args = parser.parse_args()

    try:
        from services.pulse_control_plane import drift
    except Exception as exc:  # pragma: no cover - import failure path
        print(f"capability drift gate could not import the control plane: {exc}", file=sys.stderr)
        return 3

    try:
        result = drift.detect()
    except Exception as exc:  # pragma: no cover - detector failure path
        # Exit 3, not 1 and certainly not 0. A detector that crashed has not
        # told us the tree is clean; it has told us nothing, and the two must
        # not look alike from outside.
        print(f"capability drift gate failed to run: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(
            json.dumps(
                {
                    "passed": result.passed,
                    "blocking": [f.__dict__ for f in result.blocking],
                    "reported": [f.__dict__ for f in result.reported],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(drift.report(result))

    for finding in result.blocking:
        print(f"::error::{finding.kind} {finding.capability_key}: {finding.detail}")

    if args.strict and result.reported:
        print(
            "::error::--strict: production-observed drift is present, so the control "
            "plane does not yet describe production and must not be wired into it."
        )
        return 1

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
