#!/usr/bin/env python3
"""Audit the UNDX sovereign governance foundation.

This audit intentionally checks source structure and standalone executable tests. It
does not execute any Marketplace action or provider call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = [
    "services/pulse_ai_service.py",
    "services/undx_policy.py",
    "services/pulse_ai_provider_router.py",
    "services/business_os/marketplace/assistant.py",
    "services/business_os/undx_actions/schema.py",
    "services/business_os/undx_actions/engine.py",
    "services/business_os/undx_actions/api.py",
    "reports/undx_agent_stage0_inventory_and_architecture.md",
]

REQUIRED_SCHEMA_TOKENS = [
    "business_os_undx_tool_registry",
    "business_os_undx_permissions",
    "business_os_undx_confirmations",
    "business_os_undx_action_receipts",
    "business_os_undx_emergency_stops",
]

REQUIRED_ENGINE_TOKENS = [
    "register_tool",
    "grant_permission",
    "record_confirmation",
    "record_receipt",
    "activate_emergency_stop",
    "action_center",
]


def _fail(message: str) -> None:
    print(f"FAIL {message}")
    raise SystemExit(1)


def _read(path: str) -> str:
    full = ROOT / path
    if not full.exists():
        _fail(f"missing {path}")
    return full.read_text(encoding="utf-8")


def _run(cmd: list[str]) -> None:
    env = dict(os.environ)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(proc.stdout)
    if proc.returncode != 0:
        _fail(f"command failed: {' '.join(cmd)}")


def main() -> int:
    for path in REQUIRED_FILES:
        if not (ROOT / path).exists():
            _fail(f"missing required file {path}")

    schema = _read("services/business_os/undx_actions/schema.py")
    for token in REQUIRED_SCHEMA_TOKENS:
        if token not in schema:
            _fail(f"schema missing {token}")

    engine = _read("services/business_os/undx_actions/engine.py")
    for token in REQUIRED_ENGINE_TOKENS:
        if token not in engine:
            _fail(f"engine missing {token}")
    if "emergency stop" not in engine:
        _fail("engine missing emergency stop decision reason")

    marketplace = _read("services/business_os/marketplace/assistant.py")
    for token in ("plan(", "execute(", "confirmation_token", "verified"):
        if token not in marketplace:
            _fail(f"marketplace assistant missing {token}")

    _run([sys.executable, "tests/business_os/test_undx_schema.py"])
    _run([sys.executable, "tests/business_os/test_undx_engine.py"])
    _run([sys.executable, "tests/business_os/test_undx_api.py"])

    print("PASS UNDX sovereign governance audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
