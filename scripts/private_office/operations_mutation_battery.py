#!/usr/bin/env python3
"""Anti-vacuity battery for the Operations Slice 1 suite.

A passing test suite proves nothing on its own: a suite that asserts the wrong
thing, or asserts nothing at all, is also green. This script breaks the
implementation in twelve specific, plausible ways — each one a change a
reasonable engineer might make while "simplifying" — and requires that the
suite go red for every one of them.

A mutation that survives is reported as a hole in the tests, not as a pass.

Usage::

    python3 scripts/private_office/operations_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay
directory of symlinks, with the one mutated file materialised as a real copy.
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUITE = "tests/private_office/test_private_operations.py"

RECORDS = "services/private_office/records.py"
OPS = "services/private_office/operations.py"

#: (name, file, old, new). ``old`` must appear exactly once — a mutation that
#: cannot be applied is a failure of this script, not a silent skip.
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "terminal to terminal is allowed",
        RECORDS,
        "    if there in closing:\n        raise PrivateRecordRejected(",
        "    if there in closing:\n        return TRANSITION_MOVE\n    if False:\n        raise PrivateRecordRejected(",
    ),
    (
        "the reopen flag is ignored",
        RECORDS,
        "    if not reopen:\n        # The status alone is not consent.",
        "    if False:\n        # The status alone is not consent.",
    ),
    (
        "restating a status counts as an update",
        RECORDS,
        "        if move == TRANSITION_STAY:\n            # A legal restatement",
        "        if False:\n            # A legal restatement",
    ),
    (
        "a refused transition is not audited",
        RECORDS,
        "action=_audit.ACTION_RECORD_TRANSITION_DENIED,",
        "action=_audit.ACTION_RECORD_UPDATE,",
    ),
    (
        "closed records still derive OVERDUE",
        RECORDS,
        'if stored in spec["closing"]:\n        return stored\n    moment = deadline_moment',
        'if False:\n        return stored\n    moment = deadline_moment',
    ),
    (
        "an event's occurred_at is treated as a deadline",
        RECORDS,
        "    TYPE_REQUEST: \"deadline_at\",\n}",
        "    TYPE_REQUEST: \"deadline_at\",\n    TYPE_EVENT: \"occurred_at\",\n}",
    ),
    (
        "every type shares the obligation due-soon window",
        RECORDS,
        "TYPE_REQUEST: timedelta(days=3),",
        "TYPE_REQUEST: timedelta(days=14),",
    ),
    (
        "an unknown status filter widens instead of narrowing",
        RECORDS,
        "        # rather than widening to everything.\n        if not valid:\n            return 0",
        "        # rather than widening to everything.\n        if not valid:\n            valid = list(spec[\"statuses\"])",
    ),
    (
        "the attention priority order is reversed",
        OPS,
        "_REASON_INDEX = {name: i for i, name in enumerate(REASON_RANK)}",
        "_REASON_INDEX = {name: i for i, name in enumerate(reversed(REASON_RANK))}",
    ),
    (
        "the attention total is capped like the page",
        OPS,
        '"total": len(collected),',
        '"total": len(collected[:bounded]),',
    ),
    (
        "high risks are counted from the truncated page",
        OPS,
        "    active_high_risks = _records.count_records(\n        cur, record_type=_records.TYPE_RISK, owner_user_id=owner,\n        statuses=live_risk_statuses, severities=sorted(ATTENTION_SEVERITIES))",
        "    active_high_risks = len([i for i in queue[\"items\"]\n                             if i.get(\"primary_reason\") == REASON_HIGH_RISK])",
    ),
    (
        "an unanswerable count is reported as zero",
        OPS,
        '"expiring_opportunities": UNSUPPORTED,',
        '"expiring_opportunities": 0,',
    ),
    (
        "closed records are classified for attention",
        OPS,
        '    if stored in spec["closing"]:\n        return ()',
        "    if False:\n        return ()",
    ),
]


def _overlay(root: str) -> str:
    """A symlink mirror of the repo, deep only where we need to write."""
    real_dirs = {
        "services", "services/private_office", "tests", "tests/private_office",
    }
    for base, dirs, files in os.walk(REPO):
        rel = os.path.relpath(base, REPO)
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        if rel and rel not in real_dirs:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if d not in {".git", "node_modules", "__pycache__", ".venv"}]
        target = os.path.join(root, rel) if rel else root
        os.makedirs(target, exist_ok=True)
        for name in files:
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                os.symlink(os.path.join(base, name), dst)
        for name in dirs:
            child = f"{rel}/{name}" if rel else name
            if child in real_dirs:
                continue
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                os.symlink(os.path.join(base, name), dst)
    return root


def _apply(root: str, rel_path: str, old: str, new: str) -> bool:
    source = os.path.join(REPO, rel_path)
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    if text.count(old) != 1:
        return False
    dst = os.path.join(root, rel_path)
    if os.path.islink(dst):
        os.unlink(dst)
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(text.replace(old, new, 1))
    return True


def _run(root: str) -> int:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, os.path.join(root, SUITE)],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


def main() -> int:
    mutations = MUTATIONS

    print("Baseline: the unmutated suite must pass.")
    base = _overlay(tempfile.mkdtemp(prefix="mut_base_"))
    try:
        if _run(base) != 0:
            print("  FAIL — the suite is already red; mutation results would be noise.")
            return 1
        print("  PASS — baseline green.\n")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    survived: list[str] = []
    unapplied: list[str] = []
    for name, rel_path, old, new in mutations:
        root = _overlay(tempfile.mkdtemp(prefix="mut_"))
        try:
            if not _apply(root, rel_path, old, new):
                unapplied.append(name)
                print(f"  ERROR  {name} — anchor text not found exactly once")
                continue
            code = _run(root)
            if code == 0:
                survived.append(name)
                print(f"  SURVIVED  {name}")
            else:
                print(f"  CAUGHT    {name}")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 60)
    if unapplied:
        print(f"{len(unapplied)} mutation(s) could not be applied:")
        for name in unapplied:
            print(f"  - {name}")
    if survived:
        print(f"{len(survived)} mutation(s) SURVIVED — the suite has a hole there:")
        for name in survived:
            print(f"  - {name}")
    if survived or unapplied:
        return 1
    print(f"PASS — all {len(mutations)} mutations were caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
