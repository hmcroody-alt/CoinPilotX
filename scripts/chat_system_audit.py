#!/usr/bin/env python3
"""Pulse chat system audit: private, group, room, and self-healing checks."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import chat_health_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db(); bot.init_db()
    result = subprocess.run([sys.executable, str(ROOT / "scripts/messenger_core_audit.py")], cwd=str(ROOT), text=True, capture_output=True)
    expect(result.returncode == 0, "canonical messenger core audit", result.stdout + result.stderr)
    conn = bot.db(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    summary = chat_health_service.health_summary(cur)
    repair = chat_health_service.repair_stale_sessions(cur)
    conn.commit(); conn.close()
    expect("tables" in summary and summary["tables"].get("conversations", 0) >= 0, "chat health summary available", str(summary))
    expect(repair.get("ok") is True, "stale session repair runs", str(repair))
    print("chat system audit ok")


if __name__ == "__main__":
    main()
