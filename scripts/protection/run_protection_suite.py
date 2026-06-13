#!/usr/bin/env python3
"""Run PulseSoc production protection checks.

These checks are intentionally static and secret-safe. They protect contracts
that must not regress before any deployment: livestream safety, media playback,
navigation, auth/payment route presence, and camera quality fallbacks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TESTS = [
    ROOT / "tests/protection/test_livestream_contract.py",
    ROOT / "tests/protection/test_media_playback_contract.py",
    ROOT / "tests/protection/test_core_platform_contract.py",
]


def main() -> int:
    failures: list[str] = []
    for test in TESTS:
        if not test.exists():
            failures.append(f"missing protection test: {test.relative_to(ROOT)}")
            continue
        result = subprocess.run([sys.executable, str(test)], cwd=ROOT)
        if result.returncode != 0:
            failures.append(str(test.relative_to(ROOT)))
    if failures:
        print("PulseSoc protection suite failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc protection suite passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
