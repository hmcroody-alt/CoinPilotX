#!/usr/bin/env python3
"""Mutation battery for the evidence availability axis.

A test suite that passes proves the code runs. It does not prove the code is
load-bearing. This script breaks one guarantee at a time in
``services/private_office/evidence.py`` and requires the suite to notice. A
mutation that survives is a guarantee nothing is actually defending.

    python3 scripts/private_office/mutate_evidence_availability.py

Exit code 0 means every mutation was killed.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = ROOT / "services" / "private_office" / "evidence.py"
SUITE = ROOT / "tests" / "private_office" / "test_evidence_availability.py"

#: (name, why it matters, old, new). Each `old` must appear exactly once.
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "owner-filter-removed",
        "the owner predicate is the whole isolation guarantee",
        "f\"WHERE owner_user_id=? AND id IN ({placeholders})\",\n                tuple([owner] + ids),",
        "f\"WHERE id IN ({placeholders})\",\n                tuple(ids),",
    ),
    (
        "not-found-becomes-available",
        "a row that is not there must never read as a usable source",
        "    if not found:\n        return AVAILABILITY_NOT_FOUND",
        "    if not found:\n        return AVAILABILITY_AVAILABLE",
    ),
    (
        "deleted-becomes-available",
        "deleting a document must stop it supporting new verification",
        '    "DELETED": AVAILABILITY_SOURCE_UNAVAILABLE,',
        '    "DELETED": AVAILABILITY_AVAILABLE,',
    ),
    (
        "revoked-becomes-available",
        "a revoked source is withdrawn, not merely retired",
        '    "REVOKED": AVAILABILITY_SOURCE_UNAVAILABLE,',
        '    "REVOKED": AVAILABILITY_ARCHIVED,',
    ),
    (
        "unknown-lifecycle-fails-open",
        "an unrecognised lifecycle must fail closed, not be assumed fine",
        "    return _AVAILABILITY_BY_LIFECYCLE.get(state, AVAILABILITY_UNKNOWN)",
        "    return _AVAILABILITY_BY_LIFECYCLE.get(state, AVAILABILITY_AVAILABLE)",
    ),
    (
        "empty-lifecycle-fails-open",
        "a blank lifecycle column is missing information, not an ACTIVE row",
        "    if not state:\n        return AVAILABILITY_UNKNOWN",
        "    if not state:\n        return AVAILABILITY_AVAILABLE",
    ),
    (
        "verification-gate-widened",
        "only a currently readable source may establish a fresh verification",
        "VERIFYING_AVAILABILITY: frozenset[str] = frozenset({AVAILABILITY_AVAILABLE})",
        "VERIFYING_AVAILABILITY: frozenset[str] = frozenset(RESOLVABLE_AVAILABILITY)",
    ),
    (
        "unprobed-becomes-not-found",
        '"I could not look" must not be reported as "there is nothing there"',
        "            availability = AVAILABILITY_UNKNOWN\n        resolved.append(",
        "            availability = AVAILABILITY_NOT_FOUND\n        resolved.append(",
    ),
    (
        "failed-probe-counts-as-probed",
        "a query that raised proves nothing about whether the row exists",
        "        except Exception:\n            continue\n        probed.add(kind)",
        "        except Exception:\n            rows = []\n        probed.add(kind)",
    ),
    (
        "owner-zero-probes-anyway",
        "owner 0 is not a member; it must not be able to read the table",
        "        if owner <= 0:",
        "        if owner < 0:",
    ),
    (
        "resolvable-swallows-unavailable",
        "a deleted source must not render as an openable citation",
        "RESOLVABLE_AVAILABILITY: frozenset[str] = frozenset(\n    {\n        AVAILABILITY_AVAILABLE,",
        "RESOLVABLE_AVAILABILITY: frozenset[str] = frozenset(\n    {\n        AVAILABILITY_SOURCE_UNAVAILABLE,\n        AVAILABILITY_AVAILABLE,",
    ),
    (
        "historical-attribution-downgraded",
        "a verification that happened must stay attributable after deletion",
        '        "attributable": bool(entry.get("ref")),',
        '        "attributable": is_resolvable(availability),',
    ),
    (
        "summary-merges-deleted-and-missing",
        '"you deleted this" and "this points at nothing" are different messages',
        '        "missing": by_state[AVAILABILITY_NOT_FOUND],',
        '        "missing": by_state[AVAILABILITY_NOT_FOUND] + by_state[AVAILABILITY_SOURCE_UNAVAILABLE],',
    ),
    (
        "grouping-reverted-to-n-plus-1",
        "the bound on query count is a real property, not an implementation note",
        "        wanted.setdefault(kind, []).append(row_id)",
        "        wanted.setdefault(f'{kind}:{row_id}', []).append(row_id)",
    ),
]


def run_suite() -> bool:
    """True when the suite passes."""
    proc = subprocess.run(
        [sys.executable, str(SUITE)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return proc.returncode == 0


def main() -> int:
    original = TARGET.read_text()

    if not run_suite():
        print("BASELINE FAILED — fix the suite before mutating.", file=sys.stderr)
        return 2
    print("baseline: PASS\n")

    survivors: list[str] = []
    try:
        for name, why, old, new in MUTATIONS:
            count = original.count(old)
            if count != 1:
                print(f"  SKIP  {name}: anchor matched {count} times, not 1")
                survivors.append(f"{name} (anchor drift)")
                continue
            TARGET.write_text(original.replace(old, new, 1))
            killed = not run_suite()
            print(f"  {'KILL' if killed else 'LIVE'}  {name}  — {why}")
            if not killed:
                survivors.append(name)
    finally:
        TARGET.write_text(original)

    print()
    if survivors:
        print(f"SURVIVORS ({len(survivors)}): " + ", ".join(survivors))
        return 1
    print(f"all {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
