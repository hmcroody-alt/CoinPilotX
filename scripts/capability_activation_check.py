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

Why this script reads the database and the drift gate does not
--------------------------------------------------------------

After the Stage 11 migration, the stored claim about a capability lives in
``deployment_state``, not in the legacy ``state`` word. Measuring drift against
``state`` alone would report four permanent findings that no action can ever
clear, because repairing that column is the one edit this mission forbids — so
a gate wired to it could never go green. A gate that cannot pass is the same
failure as one that cannot fail.

So the migrated columns are read here and handed to
:func:`~services.pulse_control_plane.activation.readiness`, which downgrades the
legacy-word findings to notes for rows it can see were migrated, and separately
checks that each migrated column agrees with what this package concluded. The
control-plane package itself still opens nothing: it is importable in CI, on a
fork, with no network. This script is the only place the two meet, and it is
run by hand immediately before a cutover.

Without ``--database-url`` the check runs against no migration evidence at all
and therefore blocks. That is the correct default: "I could not confirm the
migration" and "the migration did not happen" deserve the same answer.

Usage::

    python3 scripts/capability_activation_check.py           # plan + verdict
    python3 scripts/capability_activation_check.py --json    # machine-readable
    python3 scripts/capability_activation_check.py --database-url ...

Exit status
-----------

0   activation may proceed with wave 1
1   activation is blocked; the blockers are listed
3   the check could not run — never treat this as a pass
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def read_migrated(url: str) -> dict[str, str]:
    """Capability key -> stored ``deployment_state``, for migrated rows only.

    A row whose ``deployment_state`` is NULL has not been migrated and is
    omitted, so that a partial migration is visibly partial rather than
    averaging out. ``readiness`` blocks on the legacy word for anything missing
    here, which is the behaviour that existed before the migration ran.
    """
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT feature_key, deployment_state FROM feature_flags "
            "WHERE deployment_state IS NOT NULL"
        )
        return {key: state for key, state in cur.fetchall()}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL"),
        help=(
            "read the migrated columns from this database. Without it the check "
            "sees no migration evidence and blocks."
        ),
    )
    args = parser.parse_args()

    try:
        from services.pulse_control_plane import activation
    except Exception as exc:  # pragma: no cover - import failure path
        print(f"activation check could not import the control plane: {exc}", file=sys.stderr)
        return 3

    migrated: dict[str, str] = {}
    if args.database_url:
        try:
            migrated = read_migrated(args.database_url)
        except Exception as exc:
            # Exit 3, not 1. Being unable to read the migration is not evidence
            # that activation is unsafe; it is the absence of evidence either
            # way, and reporting it as a blocker would invite someone to "fix"
            # it by clearing the blocker list.
            print(f"could not read the migrated columns: {exc}", file=sys.stderr)
            return 3

    try:
        result = activation.readiness(migrated=migrated)
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
                    "migrated_rows": len(migrated),
                },
                indent=2,
            )
        )
    else:
        print(activation.plan(result))

    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
