#!/usr/bin/env python3
"""Anti-vacuity battery for the Operations Slice 1 suite.

A passing test suite proves nothing on its own: a suite that asserts the wrong
thing, or asserts nothing at all, is also green. This script breaks the
implementation in a series of specific, plausible ways — each one a change a
reasonable engineer might make while "simplifying" — and requires that the
paired suite go red for every one of them.

The count is deliberately not written down here. A number in a docstring is a
fact that stops being true the first time someone adds a mutation and does not
notice the prose, and the summary line at the end reports the real one.

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

#: Each mutation is paired with the suite that is supposed to defend against it,
#: rather than being run against everything. Pairing is the stricter claim: it
#: says *this* suite covers *this* invariant, so a mutation caught only by some
#: unrelated file's incidental breakage is still reported as a hole.
SUITE_OPS = "tests/private_office/test_private_operations.py"
SUITE_LINKS = "tests/private_office/test_private_record_links.py"

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

#: G-3, the dependency graph. Same rule: each of these is a change that leaves
#: the feature working well enough to demo.
LINK_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "cycle detection is skipped",
        RECORDS,
        "    if _reaches(cur, owner, (tgt_kind, tgt_id), (src_kind, src_id)):",
        "    if False:",
    ),
    (
        "the cycle walk stops at one hop",
        RECORDS,
        "        for nxt in _outgoing(cur, owner, node[0], node[1]):",
        "        for nxt in (_outgoing(cur, owner, node[0], node[1]) if depth < 1 else ()):",
    ),
    (
        "exhausting the traversal bound admits the link",
        RECORDS,
        "        if depth >= MAX_DEPENDENCY_DEPTH:\n            return True",
        "        if depth >= MAX_DEPENDENCY_DEPTH:\n            return False",
    ),
    (
        "a record may depend on itself",
        RECORDS,
        "    if (src_kind, src_id) == (tgt_kind, tgt_id):",
        "    if False:",
    ),
    (
        "the dependency ceiling is off by one",
        RECORDS,
        "if existing_count >= MAX_DEPENDENCIES_PER_RECORD:",
        "if existing_count > MAX_DEPENDENCIES_PER_RECORD:",
    ),
    (
        "a cross-owner endpoint says so instead of not-found",
        RECORDS,
        'raise PrivateRecordRejected(f"no such {kind} record")',
        'raise PrivateRecordRejected(f"{kind} {ident} belongs to another owner")',
    ),
    (
        "events become linkable",
        RECORDS,
        '    return tuple(k for k in RECORD_TYPES if SPECS[k]["closing"])',
        "    return RECORD_TYPES",
    ),
    (
        "a refused link is not audited",
        RECORDS,
        "            action=_audit.ACTION_RECORD_LINK_DENIED,",
        "            action=_audit.ACTION_RECORD_READ,",
    ),
    (
        "closed blockers still block",
        RECORDS,
        '    open_blockers = [b for b in blockers if b["open"]]',
        "    open_blockers = list(blockers)",
    ),
    (
        "a revision leaves its dependencies behind",
        RECORDS,
        "    _repoint_links(cur, owner, kind, int(record_id), new_id, now_iso=now_iso)",
        "    pass",
    ),
    (
        "only the blocker side of a revision is re-pointed",
        RECORDS,
        '    for column in ("source", "target"):',
        '    for column in ("target",):',
    ),
    (
        "the bulk reader ignores the blocker's lifecycle",
        RECORDS,
        'f"AND t.lifecycle_state = ? "\n            f"AND t.status NOT IN ({placeholders}) "',
        'f"AND t.lifecycle_state IS NOT ? "\n            f"AND t.status NOT IN ({placeholders}) "',
    ),
    (
        "blocked records are dropped from the attention queue",
        OPS,
        "    if blocked:\n        found.add(REASON_BLOCKED)",
        "    if blocked:\n        return ()",
    ),
    (
        "being blocked outranks being overdue",
        OPS,
        "    if blocked:\n        found.add(REASON_BLOCKED)",
        "    if blocked:\n        return (REASON_BLOCKED,)",
    ),
    (
        "the blocker count collapses to a boolean",
        OPS,
        '        "open_blocker_count": blockers,',
        '        "open_blocker_count": 1 if blockers else 0,',
    ),
    (
        "the blocked total is counted from the page",
        OPS,
        '"blocked": sum(1 for item in collected if item.get("blocked")),',
        '"blocked": sum(1 for item in collected[:bounded] if item.get("blocked")),',
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


def _run(root: str, suite: str) -> int:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, os.path.join(root, suite)],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


#: (suite, mutations). Order is presentation only; each group is independent.
GROUPS: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    ("Operations Slice 1", SUITE_OPS, MUTATIONS),
    ("G-3 dependencies", SUITE_LINKS, LINK_MUTATIONS),
]


def main() -> int:
    print("Baseline: every unmutated suite must pass.")
    base = _overlay(tempfile.mkdtemp(prefix="mut_base_"))
    try:
        for label, suite, _ in GROUPS:
            if _run(base, suite) != 0:
                print(f"  FAIL — {label} is already red; "
                      f"mutation results would be noise.")
                return 1
            print(f"  PASS — {label} baseline green.")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()

    survived: list[str] = []
    unapplied: list[str] = []
    total = 0
    for label, suite, mutations in GROUPS:
        print(f"{label} ({len(mutations)} mutations, judged by {suite}):")
        for name, rel_path, old, new in mutations:
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="mut_"))
            try:
                if not _apply(root, rel_path, old, new):
                    unapplied.append(f"{label}: {name}")
                    print(f"  ERROR  {name} — anchor text not found exactly once")
                    continue
                if _run(root, suite) == 0:
                    survived.append(f"{label}: {name}")
                    print(f"  SURVIVED  {name}")
                else:
                    print(f"  CAUGHT    {name}")
            finally:
                shutil.rmtree(root, ignore_errors=True)
        print()

    print("=" * 60)
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
    print(f"PASS — all {total} mutations were caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
