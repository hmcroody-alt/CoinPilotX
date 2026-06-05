#!/usr/bin/env python3
"""Audit crash-safe Pulse video click behavior."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")

for token in [
    'row["permalink"] = f"/pulse/videos/{row.get(\'id\')}"',
    "PULSE_VIDEO_DETAIL_CRASH trace_id=%s",
    "PULSE_VIDEO_DETAIL_NOT_FOUND trace_id=%s",
    "COALESCE(v.status,'active')='active'",
    "return pulse_video_not_found_response(trace_id, 404)",
    "state = pulse_video_state(video)",
]:
    assert token in bot, f"missing click crash guard: {token}"
    print(f"PASS: {token}")

print("pulse video click crash audit ok")
