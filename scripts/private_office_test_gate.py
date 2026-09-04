#!/usr/bin/env python3
"""The Private Office test gate: same result per-file, per-directory, any order.

Every module in ``tests/private_office/`` mutates process-global state at import
time — eight point ``DATABASE_URL`` at their own SQLite file, two install a stub
``bot`` into ``sys.modules``. That is correct for a module run as a standalone
script and hazardous for a directory run, because pytest imports all of them
during collection and the last one imported wins the globals for the whole
process. ``tests/private_office/conftest.py`` neutralises this by rebinding, per
module, whatever that module claimed. This script is how we keep that true.

It runs the suite four ways and requires the same verdict from each:

* **per file** — one pytest process per module, which is the closest thing to
  the standalone invocation the modules were written for;
* **directory** — the ordinary ``pytest tests/private_office`` run;
* **repeat** — the directory run again in a fresh process, which catches state
  a module leaves on disk rather than in memory;
* **shuffled** — several runs with the module order permuted, which is what
  actually falsifies an ordering assumption. pytest-randomly is not installed
  in this environment, so the shuffling is done here by permuting the file
  arguments; pytest honours the order it is given them.

A disagreement between any two of these is a real defect even when the
directory run is green, because it means the suite's verdict depends on
collection order, and collection order is not something anyone reviews.

    python3 scripts/private_office_test_gate.py
    python3 scripts/private_office_test_gate.py --shuffles 10 --seed 7
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE_DIR = os.path.join("tests", "private_office")


def _run(args: list[str]) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    tail = (completed.stdout or completed.stderr).strip().splitlines()
    return completed.returncode, tail[-1] if tail else "(no output)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shuffles", type=int, default=4,
                        help="how many permuted-order runs to perform")
    parser.add_argument("--seed", type=int, default=0,
                        help="base seed, so a failing permutation can be replayed")
    options = parser.parse_args()

    modules = sorted(glob.glob(os.path.join(REPO_ROOT, SUITE_DIR, "test_*.py")))
    modules = [os.path.relpath(path, REPO_ROOT) for path in modules]
    if not modules:
        print(f"FAIL — no test modules found under {SUITE_DIR}")
        return 1

    print("PRIVATE OFFICE TEST GATE")
    print(f"repo: {REPO_ROOT}")
    print(f"modules: {len(modules)}")

    failures: list[str] = []

    print("\n[per file]")
    for module in modules:
        code, summary = _run([module])
        label = os.path.basename(module)
        print(f"  {'PASS' if code == 0 else 'FAIL'}  {label} — {summary}")
        if code != 0:
            failures.append(f"per file: {label}")

    print("\n[directory]")
    for attempt in (1, 2):
        code, summary = _run([SUITE_DIR])
        print(f"  {'PASS' if code == 0 else 'FAIL'}  run {attempt} — {summary}")
        if code != 0:
            failures.append(f"directory run {attempt}")

    print("\n[shuffled order]")
    for index in range(options.shuffles):
        seed = options.seed + index
        order = list(modules)
        random.Random(seed).shuffle(order)
        code, summary = _run(order)
        print(f"  {'PASS' if code == 0 else 'FAIL'}  seed {seed} — {summary}")
        if code != 0:
            failures.append(f"shuffled seed {seed} "
                            f"(replay: --shuffles 1 --seed {seed})")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} run(s) disagreed:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("PASS — per-file, directory, repeat and shuffled runs all agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
