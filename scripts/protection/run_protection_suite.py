#!/usr/bin/env python3
"""Run every PulseSoc production protection check.

These checks are intentionally static and secret-safe. They protect contracts
that must not regress before any deployment: livestream and real-time audio
safety, media playback, navigation, auth/payment route presence, camera quality
fallbacks, admin action accountability, metric truthfulness, and the environment
contract.

Two design decisions here are load-bearing, and both exist because of the same
failure:

1. DISCOVERY, NOT A LIST. This script used to name three files explicitly. Eight
   other suites existed in tests/protection/ and were run by nothing. A suite
   nobody runs is documentation with a misleading filename. Files are now
   discovered, so adding a protection test is enough to have it enforced.

2. A NON-ZERO TEST COUNT IS ASSERTED. The audio CI job invoked
   `python3 -m unittest tests.protection.test_livestream_audio_token_grants`.
   That module defines module-level functions and no TestCase, so unittest
   collected nothing and printed "Ran 0 tests ... OK". The job protecting
   LiveKit publish grants was green while measuring nothing. Every file here
   must report how many checks it ran, and reporting zero is a failure.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITE_DIR = ROOT / "tests/protection"

# `PROTECTION_TESTS_RUN=n` from tests/protection/_runner.py, or unittest's own
# "Ran n tests" summary for the suites built on TestCase.
COUNT_PATTERNS = (
    re.compile(r"PROTECTION_TESTS_RUN=(\d+)"),
    re.compile(r"^Ran (\d+) tests?", re.M),
)


def _reported_count(output: str) -> int:
    total = 0
    for pattern in COUNT_PATTERNS:
        for match in pattern.finditer(output):
            total += int(match.group(1))
    return total


def main() -> int:
    files = sorted(path for path in SUITE_DIR.glob("test_*.py"))
    if not files:
        print(f"No protection suites found under {SUITE_DIR.relative_to(ROOT)}")
        return 1

    failures: list[str] = []
    silent: list[str] = []
    executed = 0

    for path in files:
        relative = path.relative_to(ROOT)
        print(f"\n=== {relative}")
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        count = _reported_count(result.stdout + result.stderr)
        executed += count
        if result.returncode != 0:
            failures.append(str(relative))
        elif count == 0:
            # Exit code 0 with nothing run is the failure mode this runner was
            # rebuilt to make impossible. Treat it as a failure, not a pass.
            silent.append(str(relative))

    print()
    if failures or silent:
        print("PulseSoc protection suite failed:")
        for failure in failures:
            print(f" - failed: {failure}")
        for quiet in silent:
            print(
                f" - ran zero checks: {quiet} (add the _runner.py __main__ guard, "
                "or unittest.main(), so the file actually executes)"
            )
        return 1
    print(f"PulseSoc protection suite passed: {executed} checks across {len(files)} suites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
