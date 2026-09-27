#!/usr/bin/env python3
"""The runner shared by the mutation matrices under ``scripts/protection/``.

WHY THIS IS A MODULE AND NOT A PATTERN
--------------------------------------
A mutation matrix is two things: a list of controls with the edit that removes
each one, and a runner that applies the edits and checks the suites went red.
The list is the interesting half and is different every time. The runner is the
same every time -- and it is the half with the dangerous part in it, because it
writes to real source files in the working tree.

The repo had two transcriptions of that runner when this was extracted
(``account_enumeration_mutation_matrix.py`` and ``cj_mutation_matrix.py``) and
they had already diverged in a way that matters: only one of them verified the
restore. The other applies a mutation, runs, writes the original back, and
trusts that it worked. If that write ever failed, the working tree would be left
holding a mutation -- specifically, a mutation chosen for its ability to keep
the suite green. That is the one class of bug a mutation harness must not have,
and duplication is how the repo got a version that had it.

So: one runner, with the safety in it, imported by name.

WHAT IT GUARANTEES
------------------
1. **A red baseline aborts.** A suite that is already failing makes every
   mutation look killed, so the whole run means nothing. Checked first.
2. **A missing anchor is a survivor, not a pass.** If the source no longer
   contains the string a mutation edits, the control has moved or been rewritten
   and this entry can no longer claim anything. Reported DRIFTED and counted
   against the exit code, because the alternative -- quietly applying no edit
   and observing a green suite -- reads identically to success.
3. **An ambiguous anchor is a survivor too.** Two occurrences means the edit
   would land somewhere unintended.
4. **The restore is verified by hash, twice.** Once per mutation, and once over
   every touched file after the run.

WHAT IT DOES NOT CLAIM
----------------------
Killing a mutant shows the control is *observed*, not that it is *correct*. A
test pinning the wrong behaviour goes red just as readily when the wrong
behaviour is removed.

Nor does a green matrix mean the tests are non-vacuous in general. It means they
are non-vacuous about the specific lines named in the entries. Both matrices
using this runner have a docstring recording a mutation that survived and the
vacuous test it exposed; that is the mechanism working, and it only works for
controls somebody thought to name.

USAGE
-----
    from mutation_harness import run_matrix          # same directory

    MUTATIONS = [dict(name=..., control=..., path=..., old=..., new=..., suites=[...])]

    if __name__ == "__main__":
        sys.exit(run_matrix(ROOT, MUTATIONS))

``name``     short identifier, printed.
``control``  what the deleted line is for, printed only when it survives -- at
             which point it is the whole message, so write it for that reader.
``path``     repo-relative file to edit.
``old``      exact anchor, which must appear exactly once.
``new``      what replaces it. Removing a control usually means weakening it to
             something plausible rather than deleting the line, since a syntax
             error kills every mutation for free.
``suites``   repo-relative test files that claim to watch this control. Named
             narrowly on purpose: pointing at the whole ``tests/`` tree lets an
             unrelated test take credit, which is the same blind spot one layer
             up from the one this script exists to close.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_suites(root: pathlib.Path, suites, verbose: bool):
    """Red if any suite fails. Returns ``(green, first_failing_suite, tail)``.

    One process per file, always. These suites set module-level database and
    rate-limiter state at import time, so batching them produces failures that
    belong to the batching rather than to the mutation -- which in a harness that
    reads "did it go red?" would be indistinguishable from a kill.
    """
    for suite in suites:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "--no-header", "-x",
             "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(root)},
        )
        tail = [line for line in proc.stdout.splitlines()
                if line.startswith("FAILED") or " passed" in line or " failed" in line]
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {tail[-1] if tail else '?'}")
        if proc.returncode != 0:
            return False, suite, tail[-1] if tail else ""
    return True, None, ""


def run_matrix(root: pathlib.Path, mutations, argv=None) -> int:
    """Apply each mutation, run its suites, restore, report. 0 only if all killed."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", default=None,
                        help="substring filter on mutation names. Narrows the run for "
                             "iteration; a filtered run is not a pass.")
    args = parser.parse_args(argv)

    selected = [m for m in mutations
                if args.only is None or args.only.lower() in m["name"].lower()]
    if not selected:
        print(f"No mutation name matches {args.only!r}. Nothing ran, so nothing is proven.")
        return 2

    touched = sorted({m["path"] for m in selected})
    baseline_hash = {rel: sha256(root / rel) for rel in touched}

    print("Baseline: suites must be green before a mutation means anything.\n")
    baseline = sorted({suite for m in selected for suite in m["suites"]})
    green, suite, tail = run_suites(root, baseline, args.verbose)
    if not green:
        print(f"ABORT: {suite} is already failing ({tail}). Fix that first -- a red\n"
              f"baseline makes every mutation look killed.")
        return 2
    print(f"  {len(baseline)} suite(s) green.\n")

    survivors = []
    for mutation in selected:
        path = root / mutation["path"]
        original = path.read_text(encoding="utf-8")
        occurrences = original.count(mutation["old"])
        if occurrences == 0:
            print(f"  DRIFTED  {mutation['name']}")
            print(f"           anchor no longer present in {mutation['path']}. The control")
            print(f"           may have moved or been rewritten; re-point this entry before")
            print(f"           it can claim anything.")
            survivors.append(mutation["name"])
            continue
        if occurrences != 1:
            print(f"  AMBIGUOUS {mutation['name']}: anchor appears {occurrences} times.")
            survivors.append(mutation["name"])
            continue
        path.write_text(original.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
        try:
            still_green, _, _ = run_suites(root, mutation["suites"], args.verbose)
        finally:
            path.write_text(original, encoding="utf-8")
            # Verify the restore rather than trust it. This harness edits real
            # source in the working tree; a restore that silently failed would
            # leave a mutation committed, and it would be one deliberately
            # written to keep the suite green. Recorded here and acted on after
            # the `finally` -- returning from inside one swallows any in-flight
            # exception, which would hide the reason the restore was reached.
            restored = sha256(path)
        if restored != baseline_hash[mutation["path"]]:
            print(f"\nFATAL: restore of {mutation['path']} did not reproduce the")
            print(f"       baseline ({restored[:12]} != {baseline_hash[mutation['path']][:12]}).")
            print(f"       The working tree is dirty with a mutation. Fix before committing.")
            return 3
        if still_green:
            print(f"  SURVIVED {mutation['name']}")
            print(f"           {mutation['control']}")
            print(f"           Removing it changed no test result. Nothing observes this.")
            survivors.append(mutation["name"])
        else:
            print(f"  killed   {mutation['name']}")

    for rel in touched:
        if sha256(root / rel) != baseline_hash[rel]:
            print(f"\nFATAL: {rel} is not byte-identical to the baseline after the run.")
            return 3
    print(f"\nall {len(touched)} touched file(s) byte-identical to baseline")

    if survivors:
        print(f"FAIL: {len(survivors)} of {len(selected)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    if len(selected) != len(mutations):
        print(f"PASS (filtered): {len(selected)} of {len(mutations)} mutations killed. "
              f"Re-run without --only before believing the matrix.")
        return 0
    print(f"PASS: all {len(mutations)} mutations killed.")
    return 0
