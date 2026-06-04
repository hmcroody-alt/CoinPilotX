#!/usr/bin/env python3
"""Audit Live and replay exposure in Pulse Videos."""

from pathlib import Path

text = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ["source_type = \"replay\" if is_replay else \"live\"", "mux_recording_playback_id", "PULSE_LIVE_REPLAY_INDEX_FAILED", '"replay"', '"live"']:
    assert token in text, f"missing {token}"
    print(f"PASS: {token}")
print("pulse live/replays audit ok")
