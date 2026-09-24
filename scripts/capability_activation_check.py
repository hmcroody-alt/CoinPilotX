#!/usr/bin/env python3
"""Stage 23 pre-activation check — may feature_flags be wired up yet?

Run this immediately before wiring any capability into production feature
access, and not at any other time. It is not a CI gate: it fails on
production-observed drift by design, and failing unrelated pull requests on the
state of a production row is how a gate gets routed around.

It composes more than ``capability_drift_gate.py --strict`` does. Zero drift is
necessary and not sufficient: activation additionally requires that every
widening in the shadow matrix has been written down with its evidence, that no
capability on the never-wire list is scheduled, that the control plane has not
begun inferring authentication, and that ``evaluate_flag`` still has no call
sites — because the Stage 11 migration deliberately leaves the legacy ``state``
column holding its pre-migration words, and what makes that safe is only that
nothing reads it.

Usage::

    python3 scripts/capability_activation_check.py           # plan + verdict
    python3 scripts/capability_activation_check.py --json    # machine-readable

Exit status
-----------

0   activation may proceed with wave 1
1   activation is blocked; the blockers are listed
3   the check could not run — never treat this as a pass
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = parser.parse_args()

    try:
        from services.pulse_control_plane import activation
    except Exception as exc:  # pragma: no cover - import failure path
        print(f"activation check could not import the control plane: {exc}", file=sys.stderr)
        return 3

    try:
        result = activation.readiness()
    except Exception as exc:  # pragma: no cover - failure path
        # Exit 3. A check that crashed has not said activation is safe; it has
        # said nothing, and the two must not look alike from outside.
        print(f"activation check failed to run: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(
            json.dumps(
                {
                    "ready": result.ready,
                    "blockers": list(result.blockers),
                    "notes": list(result.notes),
                },
                indent=2,
            )
        )
    else:
        print(activation.plan(result))

    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
