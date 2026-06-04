#!/usr/bin/env python3
"""Verify Mux playback and no-challenge source configuration remain canonical."""

from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")
media = (root / "services/media_service.py").read_text(encoding="utf-8")
assert "https://stream.mux.com/{mux_playback_id}.m3u8" in bot
assert "MUX_SOURCE_BASE_URL" in media or "R2_MUX_SOURCE_BASE_URL" in media
assert "cdn.coinpilotx.app" not in media[media.find("MUX_SOURCE_BASE_URL"):media.find("MUX_SOURCE_BASE_URL") + 900]
print("pulse video Mux source audit ok")
