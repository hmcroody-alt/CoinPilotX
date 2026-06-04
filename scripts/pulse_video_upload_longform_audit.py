#!/usr/bin/env python3
"""Verify large videos use the existing direct Mux upload path."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
manager = (root / "static/js/pulse_upload_manager.js").read_text(encoding="utf-8")
bot = (root / "bot.py").read_text(encoding="utf-8")
for token in ["/api/pulse/media/mux/direct-upload", "/api/pulse/media/mux/direct-upload/complete", "MEDIA_DIRECT_UPLOAD_MAX_VIDEO_GB", "25"]:
    assert token in manager or token in bot, f"missing long-form token {token}"
    print(f"PASS: {token}")
assert "XMLHttpRequest" in manager, "direct upload must support progress"
print("pulse long-form video upload audit ok")
