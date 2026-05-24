#!/usr/bin/env python3
"""Audit Pulse Camera capture routes, media integration, and publish targets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def table_exists(cur, table):
    try:
        cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def main():
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    for table in ["pulse_camera_captures", "pulse_camera_effects", "pulse_media_assets", "pulse_status", "pulse_reels"]:
        expect(table_exists(cur, table), f"{table} exists")
    conn.close()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for token in ["/pulse/camera", "navigator.mediaDevices.getUserMedia", "target}'==='status", "pulse_camera_captures", "pulse_media_assets", "/api/pulse/media/upload"]:
        expect(token in source, f"camera integration token present: {token}")
    client = bot.webhook_app.test_client()
    response = client.get("/pulse/camera?target=status")
    expect(response.status_code in {200, 302}, "camera route loads or redirects to login")
    print("pulse camera audit ok")


if __name__ == "__main__":
    main()
