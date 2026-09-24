#!/usr/bin/env python3
"""Stage 11 — write the reconciled truth into production, additively.

This is the only script in the package that opens a writable connection, and it
spends most of its length refusing to.

What it writes
--------------

New columns beside the legacy ones, never the legacy ones themselves.
``feature_flags.state`` is not in the UPDATE statement at all; it is in the
``WHERE`` clause, as the compare-and-set guard. That asymmetry is the whole
design: the seeded word is what we check the world against, and the thing we
must not touch, because ``normalize_state`` maps every unrecognised value to
``beta`` — the most permissive state the legacy engine has. There is no value
meaning "this no longer decides anything", so the only safe edit to that column
is none.

Why it re-reads the rows instead of trusting the audit
------------------------------------------------------

:mod:`services.pulse_control_plane.observations` is a *recording*, dated
``MEASURED_AT``. The manifest is built from the rows this script reads live,
and the recording is then compared against them. If they disagree, somebody
edited the matrix since the audit and every verdict downstream is suspect — so
the run stops before proposing a single write rather than repairing a row whose
evidence has expired.

That check and the per-row compare-and-set are not redundant. The first catches
a drift that happened before this process started and stops the whole run; the
second catches one that lands between the SELECT and the UPDATE and rolls the
transaction back. Different windows, same answer: stop and investigate.

Usage::

    python3 scripts/capability_migration.py             # dry run, read-only
    python3 scripts/capability_migration.py --apply     # one transaction

The dry run opens the connection ``readonly=True``, so a bug in the manifest
builder cannot write by accident — the database refuses rather than this script
remembering not to. ``--apply`` is the only path that does not set that flag.

Exit status
-----------

0   dry run completed, or --apply committed
1   refused, or rolled back
3   could not run (no DATABASE_URL, import failure, unreachable database)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ROW_COLUMNS = ("feature_key", "state", "rollout_percentage", "public_label")


def _connect(url: str, *, readonly: bool):
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url)
    if readonly:
        conn.set_session(readonly=True)
    return conn


def _read_rows(conn) -> dict[str, dict]:
    import psycopg2.extras

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"SELECT {', '.join(ROW_COLUMNS)} FROM feature_flags")
    return {r["feature_key"]: dict(r) for r in cur.fetchall()}


def _audit_is_still_current(live: dict[str, dict], recorded: dict[str, str]) -> list[str]:
    """Rows whose stored state no longer matches the audit recording."""
    stale = []
    for key, recorded_state in sorted(recorded.items()):
        row = live.get(key)
        if row is None:
            stale.append(f"{key}: audited, but no row in production now")
        elif row["state"] != recorded_state:
            stale.append(
                f"{key}: audit recorded {recorded_state!r}, production holds "
                f"{row['state']!r}"
            )
    for key in sorted(set(live) - set(recorded)):
        stale.append(f"{key}: row in production that the audit never saw")
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the capability migration manifest.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write (default is a read-only dry run)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL"),
        help="defaults to $DATABASE_URL, then $DATABASE_PUBLIC_URL",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("no database URL: set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 3

    try:
        from services.pulse_control_plane import migration, observations, reconciler
    except Exception as exc:
        print(f"could not import the control plane: {exc}", file=sys.stderr)
        return 3

    try:
        conn = _connect(args.database_url, readonly=not args.apply)
        live = _read_rows(conn)
    except Exception as exc:
        print(f"could not read feature_flags: {exc}", file=sys.stderr)
        return 3

    mode = "APPLY" if args.apply else "DRY RUN (connection is read-only)"
    print(f"capability migration — {mode}")
    print(f"audit recorded {observations.MEASURED_AT} against {observations.MEASURED_AGAINST}")
    print(f"{len(live)} row(s) in production feature_flags\n")

    stale = _audit_is_still_current(live, observations.STORED_STATES)
    if stale:
        print("REFUSING: the audit no longer describes production.")
        for line in stale:
            print(f"  {line}")
        print(
            "\nRe-measure before migrating. A verdict is only as good as the row it "
            "was measured against."
        )
        conn.close()
        return 1

    reconciliations = reconciler.reconcile_all(
        observations.PRODUCTION_SIGNALS,
        {key: row["state"] for key, row in live.items()},
    )
    manifest = migration.build_manifest(reconciliations, live)
    print(migration.manifest_summary(manifest))
    print()

    reconciled_at = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    try:
        result = migration.apply_manifest(
            conn,
            manifest,
            reconciled_at=reconciled_at,
            dry_run=not args.apply,
            strict=True,
        )
    except Exception as exc:
        conn.rollback()
        conn.close()
        print(f"migration raised, transaction rolled back: {exc}", file=sys.stderr)
        return 3

    if result.columns_added:
        verb = "would add" if result.dry_run else "added"
        print(f"{verb} column(s): {', '.join(result.columns_added)}")

    for row in result.rows:
        print(f"  [{row.outcome:13}] {row.capability_key:27} {row.detail}")

    print()
    if result.rolled_back_because:
        print(f"ROLLED BACK: {result.rolled_back_because}")
        conn.close()
        return 1

    if result.dry_run:
        print(f"dry run: {result.written} row(s) would be written. Nothing was committed.")
        conn.close()
        return 0

    print(f"committed: {result.written} row(s) written at {reconciled_at}.")
    print("feature_flags.state was not written. evaluate_flag still has no call sites.")
    conn.close()
    return 0 if result.committed else 1


if __name__ == "__main__":
    raise SystemExit(main())
