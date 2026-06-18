#!/usr/bin/env python3
"""Deployment gate for production-readiness checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PY_COMPILE_TARGETS = [
    "bot.py",
    "services/db.py",
    "pulse_communications_v2/service.py",
    "pulse_communications_v2/routes.py",
    "scripts/postgres_compatibility_audit.py",
    "scripts/admin_security_page_audit.py",
    "scripts/failed_login_security_audit.py",
    "scripts/production_readiness_audit.py",
]

GATE_STEPS = [
    ("py_compile", [sys.executable, "-m", "py_compile", *PY_COMPILE_TARGETS]),
    ("postgres compatibility", [sys.executable, "scripts/postgres_compatibility_audit.py"]),
    ("postgres percent placeholders", [sys.executable, "scripts/postgres_percent_placeholder_audit.py"]),
    ("admin security page", [sys.executable, "scripts/admin_security_page_audit.py"]),
    ("failed login security", [sys.executable, "scripts/failed_login_security_audit.py"]),
    ("production readiness", [sys.executable, "scripts/production_readiness_audit.py"]),
]


def run_step(label: str, command: list[str]) -> int:
    print(f"\n=== {label} ===")
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode:
        print(f"DEPLOYMENT_GATE_FAIL step={label} exit={result.returncode}")
    else:
        print(f"DEPLOYMENT_GATE_PASS step={label}")
    return int(result.returncode)


def main() -> int:
    failures = 0
    for label, command in GATE_STEPS:
        failures += 1 if run_step(label, command) else 0
    if failures:
        print(f"\ndeployment gate failed: {failures} step(s)")
        return 1
    print("\ndeployment gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
