#!/usr/bin/env python3
"""Lightweight Pulse-specific performance budget audit."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (940003,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (940003, "pulse_perf_audit", "Pulse Perf Audit", "pulse-perf-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit(); conn.close()
    return 940003


def timed(client, path):
    start = time.perf_counter()
    response = client.get(path)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return response, elapsed_ms


def main():
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    for path, budget in [("/api/pulse/feed?limit=5", 1800), ("/pulse", 2500), ("/pulse/reels", 2500), ("/api/pulse/status/rail", 1500)]:
        response, elapsed = timed(client, path)
        expect(response.status_code < 500, f"{path} avoids server error", response.get_data(as_text=True)[:300])
        expect(elapsed <= budget, f"{path} stays inside local budget ({elapsed:.0f}ms <= {budget}ms)")
    css_total = sum((ROOT / "static/css" / name).stat().st_size for name in ["pulse_desktop_feed.css", "pulse_status_system.css", "pulse_reels_experience.css"])
    expect(css_total < 75000, "new Pulse CSS remains lightweight", str(css_total))
    print("pulse performance audit ok")


if __name__ == "__main__":
    main()

