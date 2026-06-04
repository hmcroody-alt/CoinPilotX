#!/usr/bin/env python3
"""Fail if Reels default back into the regular Pulse feed."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")
feed = (root / "services/pulse_feed_engine.py").read_text(encoding="utf-8")
assert 'else "reel_only"' in bot
assert 'visibility in {"public", "followers", "private", "reel_only"}' in feed
assert "COALESCE(p.visibility,'public') IN ('public','reel_only')" in feed
assert "share_to_feed = bool(payload.get(\"share_to_feed\"))" in bot
assert "where.append(\"v.source_type!='reel'\")" in bot
print("pulse Reels/feed separation audit ok")
