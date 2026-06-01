#!/usr/bin/env python3
"""Audit the full-system assessment baseline and hardening contracts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import media_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    rules = {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}
    route_groups = {
        "Pulse pages": "/pulse",
        "Pulse APIs": "/api/pulse/feed",
        "Pulse search": "/api/pulse/search",
        "Pulse media upload": "/api/pulse/media/upload",
        "Pulse messaging": "/api/pulse/messages/conversations",
        "Pulse live": "/api/pulse/live/start",
        "UNDX APIs": "/api/undx/chat",
        "Admin diagnostics": "/admin/performance",
    }
    for label, route in route_groups.items():
        expect(route in rules, f"{label} route is registered", route)

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
    table_count = int(cur.fetchone()[0])
    conn.close()
    expect(table_count >= 100, "database exposes the expected platform schema footprint", str(table_count))

    report = ROOT / "reports" / "full-system-assessment-2026-05-31.md"
    expect(report.exists(), "full system assessment report exists")
    report_text = report.read_text(encoding="utf-8")
    for token in [
        "Root Causes Found",
        "Frontend Issues Fixed",
        "Backend/API Issues Fixed",
        "Media, Live, Reels, Messenger, Search",
        "Production Validation Checklist",
    ]:
        expect(token in report_text, f"assessment report includes {token}")

    expect(not media_service._media_header_ok("mp4", b"not a video"), "video MIME spoofing is blocked")
    expect(not media_service._media_header_ok("pdf", b"<html>not a pdf</html>"), "document MIME spoofing is blocked")
    expect(media_service._media_header_ok("mp4", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"), "valid MP4 signature is accepted")
    expect(media_service._media_header_ok("pdf", b"%PDF-1.7\n"), "valid PDF signature is accepted")
    print("system assessment audit ok")


if __name__ == "__main__":
    main()
