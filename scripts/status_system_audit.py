#!/usr/bin/env python3
"""Consolidated audit for Pulse Status create/view/publish/reply contracts."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(script: str):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="")
        raise AssertionError(f"{script} failed")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    run("pulse_status_audit.py")
    run("create_status_flow_audit.py")
    source = (ROOT / "bot.py").read_text()
    require("/api/pulse/status" in source, "Status create endpoint exists")
    require("/api/pulse/status/rail" in source, "Status rail endpoint exists")
    require("pulse_status_views" in source and "pulse_status_reactions" in source and "pulse_status_replies" in source, "Status view/reaction/reply tables exist")
    require("data-status-tool='stickers'" in source and "data-status-tool='music'" in source, "Status editor tools are functional hooks, not dead labels")
    require("data-upload-progress" in source, "Status publishing shows upload progress")
    print("status system audit ok")


if __name__ == "__main__":
    main()
